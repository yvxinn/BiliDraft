"""Benchmark cached video illustrations locally, without downloads or LLM calls.

Each run writes a new directory under notes/_benchmarks. Existing notes and
acceptance evidence are never overwritten. --text-dir may point to a previously
validated note directory to preview the new illustrations with cached text.
"""

# ruff: noqa: E402
import argparse
import platform
import time
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bilidraft.config import load_config, validate_config
from bilidraft.media import find_video
from bilidraft.storage import read_json, write_json, validate_bvid, file_signature
from bilidraft.slides import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bvid")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Private task workspace")
    parser.add_argument("--config", type=Path, help="YAML path relative to workspace")
    parser.add_argument("--workers", type=int, default=0, help="0=auto; 1=serial control")
    parser.add_argument("--mode", choices=("representative", "detailed"), default="representative")
    parser.add_argument("--ocr", action="store_true", help="OCR on selected representative frames")
    parser.add_argument(
        "--text-dir", type=Path, help="Existing note directory with .task/clean.json and outline.json"
    )
    args = parser.parse_args()
    validate_bvid(args.bvid)
    cfg = load_config(args.config, workspace=args.workspace)
    cfg.slides.mode, cfg.slides.max_workers = args.mode, args.workers
    cfg.slides.representative_ocr = args.ocr
    validate_config(cfg)
    source = cfg.path(cfg.output_dir) / args.bvid / ".task"
    video = find_video(source)
    if video is None:
        parser.error("需要已经缓存的视频；本工具不下载媒体")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    note = cfg.path(cfg.output_dir) / "_benchmarks" / args.bvid / f"{stamp}-{args.mode}-w{args.workers}"
    note.mkdir(parents=True, exist_ok=False)
    task = note / ".task"
    meta = read_json(source / "meta.json")
    write_json(task / "meta.json", meta)
    started = time.monotonic()
    result = run(video, task, note / "frames", cfg)
    elapsed = time.monotonic() - started
    summary = {
        "bvid": args.bvid,
        "duration_sec": meta["duration"],
        "wall_sec": round(elapsed, 3),
        "video_signature": file_signature(video),
        "slides_config": asdict(cfg.slides),
        "algorithm": result["algo"],
        "frames": len(result["pages"]),
        "performance": result.get("performance", {}),
        "cloud_calls": 0,
        "machine": {
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "output": str(note),
    }
    if args.text_dir:
        from bilidraft.align import run_align
        from bilidraft.exporter import run_export
        from bilidraft.quality import build_report

        text_task = args.text_dir / ".task"
        clean, outline = [read_json(text_task / f"{name}.json") for name in ("clean", "outline")]
        write_json(task / "clean.json", clean)
        write_json(task / "outline.json", outline)
        if (text_task / "subtitle.json").is_file():
            write_json(task / "subtitle.json", read_json(text_task / "subtitle.json"))
        layout = run_align(clean, result, outline, cfg)
        write_json(task / "layout.json", layout)
        run_export(note, clean, layout, result, meta, cfg)
        report = build_report(note, cfg)
        write_json(note / "report.json", report)
        summary.update(
            body=len(layout["body"]),
            appendix=len(layout["appendix"]),
            text_evidence="reused cached clean/outline; no model calls in this benchmark",
        )
    write_json(note / "benchmark.json", summary)
    print(f"BENCHMARK {note / 'benchmark.json'}", flush=True)
    print(f"{elapsed:.3f}s, {len(result['pages'])} frames", flush=True)


if __name__ == "__main__":
    main()
