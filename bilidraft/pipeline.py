"""Dependency-aware pipeline with atomic records, output validation and task locking."""

import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event, Lock
from dataclasses import asdict
from pathlib import Path
from .config import load_config
from .storage import fingerprint, read_json, write_json, file_signature, task_lock, validate_bvid

STAGES = ["meta", "download", "subtitle", "clean", "outline", "slides", "layout", "note", "report"]
DEPS = {
    "meta": [],
    "download": ["meta"],
    "subtitle": ["meta", "download"],
    "clean": ["subtitle", "meta"],
    "outline": ["clean"],
    "slides": ["download", "meta"],
    "layout": ["clean", "outline", "slides"],
    "note": ["layout", "clean", "meta", "slides"],
    "report": ["note", "clean", "outline", "slides", "layout"],
}
MODULES = {
    "meta": ["bilibili"],
    "download": ["media"],
    "subtitle": ["transcribe", "bilibili", "media"],
    "clean": ["polish", "llm"],
    "outline": ["structure", "llm"],
    "slides": ["slides", "media"],
    "layout": ["align"],
    "note": ["exporter"],
    "report": ["quality"],
}


def _stage_records(task_dir):
    f = task_dir / "stages.json"
    if not f.exists():
        return {}
    try:
        data = read_json(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def task_paths(bvid, cfg):
    validate_bvid(bvid)
    note = cfg.path(cfg.output_dir) / bvid
    return note, note / ".task"


def output_paths(stage, note, task):
    if stage == "download":
        from .media import find_video

        return [find_video(task) or task / "video.mp4"]
    if stage == "note":
        return [note / "note.md"]
    if stage == "report":
        return [note / "report.json"]
    paths = [task / f"{stage}.json"]
    if stage == "slides" and paths[0].is_file():
        try:
            for page in read_json(paths[0])["pages"]:
                frame = (note / page["file"]).resolve()
                if not frame.is_relative_to((note / "frames").resolve()):
                    return []  # Never trust paths from cached artifacts.
                paths.append(frame)
        except (OSError, ValueError, KeyError, TypeError):
            return []
    return paths


def outputs_valid(stage, paths, bvid):
    if not paths or any(not p.is_file() or p.stat().st_size == 0 for p in paths):
        return False
    try:
        if stage == "meta":
            d = read_json(paths[0])
            return d["bvid"] == bvid and d["duration"] > 0 and type(d["cid"]) is int
        if stage == "subtitle":
            from .transcribe import validate_transcript

            validate_transcript(read_json(paths[0]))
        elif stage in ("clean", "outline", "slides", "layout", "report"):
            data = read_json(paths[0])
            required = {
                "clean": "segments",
                "outline": "headings",
                "slides": "pages",
                "layout": "paragraphs",
                "report": "quality",
            }[stage]
            if required not in data:
                return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def _params(stage, cfg):
    llm = {
        k: v
        for k, v in asdict(cfg.llm).items()
        if k not in ("api_key", "api_key_env", "timeout", "retries", "max_workers")
    }
    return {
        "meta": {},
        "download": {"max_quality": cfg.bilibili.max_quality},
        "subtitle": asdict(cfg.asr),
        "clean": {
            "text_mode": cfg.text_mode,
            **({"llm": llm, "polish": asdict(cfg.polish)} if cfg.text_mode == "polish" else {}),
        },
        "outline": {
            "text_mode": cfg.text_mode,
            **({"llm": llm, "structure": asdict(cfg.structure)} if cfg.text_mode == "polish" else {}),
        },
        "slides": {
            k: v
            for k, v in asdict(cfg.slides).items()
            if k not in ("body_per_10min", "max_workers", "ocr_threads", "parallel_with_text")
        },
        "layout": {"align": asdict(cfg.align), "body_per_10min": cfg.slides.body_per_10min},
        "note": {"quality": asdict(cfg.quality)},
        "report": asdict(cfg.quality),
    }[stage]


def run(
    bvid, resume=False, skip_download=False, cfg=None, retry_failed=False, from_stage=None, progress=print
):
    cfg = cfg or load_config()
    if cfg.text_mode == "polish" and not cfg.llm.model.strip():
        raise ValueError("请配置 llm.model 或 BILIDRAFT_MODEL；只保留原始字幕可用 --text-mode raw")
    note, task = task_paths(bvid, cfg)
    if from_stage is not None and from_stage not in STAGES:
        raise ValueError("未知阶段：" + from_stage)
    resume = resume or retry_failed or from_stage is not None
    note.mkdir(parents=True, exist_ok=True)
    (note / "frames").mkdir(exist_ok=True)
    with task_lock(task):
        return _run(bvid, note, task, cfg, resume, skip_download, retry_failed, from_stage, progress)


def _run(bvid, note, task, cfg, resume, skip_download, retry_failed, from_stage, log):
    records = _stage_records(task)
    changed = set()
    module_dir = Path(__file__).parent
    client = None
    state_lock, cancel = Lock(), Event()

    def save(stage, record, mark_changed=False):
        with state_lock:
            records[stage] = record
            if mark_changed:
                changed.add(stage)
            write_json(task / "stages.json", records)

    def execute(stage):
        nonlocal client
        outputs = output_paths(stage, note, task)
        signatures = [file_signature(p) for p in outputs]
        code = {name: file_signature(module_dir / (name + ".py")) for name in MODULES[stage]}
        dependencies = {
            dep: [file_signature(p) for p in output_paths(dep, note, task)] for dep in DEPS[stage]
        }
        input_hash = fingerprint(
            {"schema": 2, "params": _params(stage, cfg), "code": code, "dependencies": dependencies}
        )
        with state_lock:
            rec = records.get(stage, {})
            dependency_changed = any(d in changed for d in DEPS[stage])
        valid = outputs_valid(stage, outputs, bvid)
        forced = stage == from_stage or (stage == "clean" and retry_failed) or dependency_changed
        cached = (
            resume
            and valid
            and not forced
            and rec.get("input_hash") == input_hash
            and rec.get("outputs") == signatures
            and rec.get("status") == "done"
        )
        # One-time migration of validated source files, never derived model output.
        legacy = (
            resume
            and valid
            and not forced
            and "input_hash" not in rec
            and stage in ("meta", "download", "subtitle")
        )
        if stage == "download" and skip_download:
            if not valid:
                raise RuntimeError("--skip-download 需要已有完整 video.mp4/mkv/webm")
            legacy = True
        if cached or legacy:
            if legacy:
                save(
                    stage,
                    {
                        "status": "done",
                        "input_hash": input_hash,
                        "outputs": signatures,
                        "elapsed_sec": 0,
                        "migrated": True,
                    },
                )
            log(f"[{stage}] 缓存")
            return
        log(f"[{stage}] 开始")
        started = time.monotonic()
        save(stage, {"status": "running", "input_hash": input_hash, "started_at": time.time()})
        try:
            if stage in ("meta", "subtitle") and client is None:
                from .bilibili import BilibiliClient

                client = BilibiliClient(cfg)
            if stage == "meta":
                write_json(task / "meta.json", client.fetch_meta(bvid, task))
            elif stage == "download":
                from .media import download_video

                download_video(bvid, task, cfg)
            elif stage == "subtitle":
                from .media import find_video
                from .transcribe import get_transcript

                meta = read_json(task / "meta.json")
                write_json(
                    task / "subtitle.json",
                    get_transcript(client, bvid, meta["cid"], task, cfg, find_video(task)),
                )
            elif stage == "clean":
                from .polish import run_polish, raw_transcript

                clean = (
                    raw_transcript(read_json(task / "subtitle.json"))
                    if cfg.text_mode == "raw"
                    else run_polish(
                        read_json(task / "subtitle.json"),
                        read_json(task / "meta.json"),
                        cfg,
                        task_dir=task,
                        reuse=resume and from_stage != "clean",
                        retry_failed=retry_failed,
                        progress=log,
                    )
                )
                write_json(task / "clean.json", clean)
            elif stage == "outline":
                from .structure import run_structure

                write_json(
                    task / "outline.json",
                    run_structure(read_json(task / "clean.json"), read_json(task / "meta.json"), cfg),
                )
            elif stage == "slides":
                from .media import find_video
                from .slides import run as run_slides

                run_slides(find_video(task), task, note / "frames", cfg, progress=log, cancel_event=cancel)
            elif stage == "layout":
                from .align import run_align

                write_json(
                    task / "layout.json",
                    run_align(
                        read_json(task / "clean.json"),
                        read_json(task / "slides.json"),
                        read_json(task / "outline.json"),
                        cfg,
                    ),
                )
            elif stage == "note":
                from .exporter import run_export

                run_export(
                    note,
                    read_json(task / "clean.json"),
                    read_json(task / "layout.json"),
                    read_json(task / "slides.json"),
                    read_json(task / "meta.json"),
                    cfg,
                )
            elif stage == "report":
                from .quality import build_report

                report = build_report(note, cfg, records)
                write_json(note / "report.json", report)
            outputs = output_paths(stage, note, task)
            if not outputs_valid(stage, outputs, bvid):
                raise RuntimeError(f"{stage} 未生成有效产物")
            record = {
                "status": "done",
                "input_hash": input_hash,
                "outputs": [file_signature(p) for p in outputs],
                "elapsed_sec": round(time.monotonic() - started, 2),
            }
            save(stage, record, mark_changed=True)
        except (Exception, KeyboardInterrupt) as e:
            save(
                stage,
                {
                    "status": "interrupted"
                    if isinstance(e, (KeyboardInterrupt, CancelledError))
                    else "failed",
                    "input_hash": input_hash,
                    "error_type": type(e).__name__,
                    "elapsed_sec": round(time.monotonic() - started, 2),
                },
            )
            raise
        log(f"[{stage}] 完成，{record['elapsed_sec']:.1f}s")

    # ASR finishes before this branch to avoid competing CPU model pools. Only
    # cloud text work overlaps screenshots; layout waits for both artifacts.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="slide-stage") as pool:
        slide_job = None
        try:
            for stage in STAGES:
                if stage == "clean" and cfg.slides.parallel_with_text:
                    slide_job = pool.submit(execute, "slides")
                if stage == "slides" and slide_job is not None:
                    slide_job.result()
                else:
                    execute(stage)
        except BaseException:
            cancel.set()
            if slide_job is not None:
                slide_job.cancel()
            raise
    report = read_json(note / "report.json")
    log(f"完成 → {note / 'note.md'}")
    log(f"质量：{'通过自动阈值' if report['quality']['passed'] else '需要检查'}；详见 report.json")
    return report
