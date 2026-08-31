from types import SimpleNamespace
import pytest
from bilidraft import pipeline, polish, structure, slides, bilibili
from bilidraft.storage import write_json, read_json

BVID = "BV1234567890"


@pytest.fixture
def fake_run(cfg, transcript, monkeypatch):
    note, task = pipeline.task_paths(BVID, cfg)
    task.mkdir(parents=True)
    (note / "frames").mkdir()
    meta = {"bvid": BVID, "cid": 1, "duration": 600, "title": "测试视频", "owner": "test"}
    write_json(task / "meta.json", meta)
    write_json(task / "subtitle.json", transcript)
    (task / "video.mp4").write_bytes(b"cached-video")
    calls = {"clean": 0, "outline": 0, "slides": 0}

    def fake_polish(tr, meta, cfg, **kwargs):
        calls["clean"] += 1
        return {
            "segments": [
                {
                    "id": s["id"],
                    "start": s["start"],
                    "end": s["end"],
                    "raw": s["text"],
                    "clean": s["text"],
                    "flags": [],
                }
                for s in tr["segments"]
            ],
            "report": {
                "retention": 1.0,
                "suspect_ratio": 0.0,
                "segments": len(tr["segments"]),
                "source": tr["source"],
                "chunks_detail": [{"ok": True}],
            },
        }

    def fake_structure(*args):
        calls["outline"] += 1
        return {"headings": [{"after_id": 0, "title": "标题", "level": 2}], "fallback_reason": ""}

    def fake_slides(video, task, frames, cfg, **kwargs):
        calls["slides"] += 1
        (frames / "p.jpg").write_bytes(b"test-image")
        write_json(
            task / "slides.json",
            {"duration": 600, "pages": [{"t_capture": 1, "file": "frames/p.jpg", "ocr_chars": 100}]},
        )

    monkeypatch.setattr(polish, "run_polish", fake_polish)
    monkeypatch.setattr(structure, "run_structure", fake_structure)
    monkeypatch.setattr(slides, "run", fake_slides)
    monkeypatch.setattr(
        bilibili,
        "BilibiliClient",
        lambda *a: SimpleNamespace(fetch_meta=lambda *a: meta, fetch_subtitle=lambda *a: transcript),
    )
    return note, task, calls


def test_resume_checks_actual_note_location(cfg, fake_run):
    note, task, calls = fake_run
    result = pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert result["quality"]["passed"]
    before = dict(calls)
    mtime = (note / "note.md").stat().st_mtime_ns
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert before == calls and (note / "note.md").stat().st_mtime_ns == mtime


def test_parameter_change_invalidates_downstream(cfg, fake_run):
    note, task, calls = fake_run
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    cfg.polish.chunk_chars += 10
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert calls == {"clean": 2, "outline": 2, "slides": 1}


def test_missing_frame_is_rebuilt(cfg, fake_run):
    note, task, calls = fake_run
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    (note / "frames/p.jpg").unlink()
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert (note / "frames/p.jpg").exists() and calls["slides"] == 2


def test_force_outline_does_not_rerun_clean_or_slides(cfg, fake_run):
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    pipeline.run(BVID, from_stage="outline", cfg=cfg, progress=lambda *a: None)
    assert fake_run[2] == {"clean": 1, "outline": 2, "slides": 1}


def test_stage_failure_is_recorded_and_resume_recovers(cfg, fake_run, monkeypatch):
    old = structure.run_structure
    monkeypatch.setattr(
        structure, "run_structure", lambda *a: (_ for _ in ()).throw(RuntimeError("simulated"))
    )
    with pytest.raises(RuntimeError):
        pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert read_json(fake_run[1] / "stages.json")["outline"]["status"] == "failed"
    monkeypatch.setattr(structure, "run_structure", old)
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert fake_run[2]["clean"] == 1


def test_skip_download_does_not_hide_missing_video(cfg, fake_run):
    (fake_run[1] / "video.mp4").unlink()
    with pytest.raises(RuntimeError, match="skip-download"):
        pipeline.run(BVID, resume=True, skip_download=True, cfg=cfg, progress=lambda *a: None)


def test_body_image_budget_only_rebuilds_layout(cfg, fake_run):
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    cfg.slides.body_per_10min = 0
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert fake_run[2] == {"clean": 1, "outline": 1, "slides": 1}
    assert read_json(fake_run[1] / "layout.json")["body"] == []


def test_force_clean_does_not_silently_reuse_polish_chunks(cfg, fake_run, monkeypatch):
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    old = polish.run_polish
    reuse = []

    def observed(*args, **kwargs):
        reuse.append(kwargs["reuse"])
        return old(*args, **kwargs)

    monkeypatch.setattr(polish, "run_polish", observed)
    pipeline.run(BVID, from_stage="clean", cfg=cfg, progress=lambda *a: None)
    assert reuse == [False]
    assert fake_run[2] == {"clean": 2, "outline": 2, "slides": 1}


def test_interrupted_stage_is_recorded_and_resumable(cfg, fake_run, monkeypatch):
    old = polish.run_polish
    monkeypatch.setattr(polish, "run_polish", lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert read_json(fake_run[1] / "stages.json")["clean"]["status"] == "interrupted"
    monkeypatch.setattr(polish, "run_polish", old)
    assert pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)["quality"]["passed"]


def test_first_run_without_subtitles_downloads_before_local_asr(cfg, fake_run, transcript, monkeypatch):
    from bilidraft import media, transcribe

    _, task, _ = fake_run
    meta = read_json(task / "meta.json")
    for name in ("meta.json", "subtitle.json", "video.mp4"):
        (task / name).unlink()
    events = []

    def download(bvid, folder, config):
        events.append("download")
        path = folder / "video.mp4"
        path.write_bytes(b"complete synthetic source")
        return path

    def audio(video, target, config):
        assert video.read_bytes() == b"complete synthetic source"
        events.append("audio")
        target.write_bytes(b"synthetic audio")
        return target

    def asr(audio, folder, config):
        assert audio.exists()
        events.append("asr")
        return transcript

    monkeypatch.setattr(
        bilibili,
        "BilibiliClient",
        lambda *a: SimpleNamespace(fetch_meta=lambda *a: meta, fetch_subtitle=lambda *a: None),
    )
    monkeypatch.setattr(media, "download_video", download)
    monkeypatch.setattr(media, "extract_audio", audio)
    monkeypatch.setattr(transcribe, "_sensevoice", asr)
    report = pipeline.run(BVID, cfg=cfg, progress=lambda *a: None)
    assert report["quality"]["passed"]
    assert events == ["download", "audio", "asr"]
    assert all(row["status"] == "done" for row in read_json(task / "stages.json").values())


def test_slides_and_cloud_text_overlap_but_layout_waits_for_both(cfg, fake_run, monkeypatch):
    from threading import Event

    slide_started, text_started = Event(), Event()
    original_slides, original_polish = slides.run, polish.run_polish

    def concurrent_slides(*args, **kwargs):
        slide_started.set()
        assert text_started.wait(5), "text branch never ran concurrently"
        return original_slides(*args, **kwargs)

    def concurrent_polish(*args, **kwargs):
        text_started.set()
        assert slide_started.wait(5), "slides branch never ran concurrently"
        return original_polish(*args, **kwargs)

    monkeypatch.setattr(slides, "run", concurrent_slides)
    monkeypatch.setattr(polish, "run_polish", concurrent_polish)
    assert pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)["quality"]["passed"]
    records = read_json(fake_run[1] / "stages.json")
    assert all(r["status"] == "done" for r in records.values())
    cfg.slides.max_workers = 1
    cfg.slides.parallel_with_text = False
    before = dict(fake_run[2])
    pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert fake_run[2] == before, "scheduling changes must not invalidate semantic output"


def test_failed_parallel_slides_preserve_text_and_do_not_export(cfg, fake_run, monkeypatch):
    original = slides.run
    monkeypatch.setattr(slides, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bad frame")))
    with pytest.raises(RuntimeError, match="bad frame"):
        pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    note, task, calls = fake_run
    assert not (note / "note.md").exists()
    records = read_json(task / "stages.json")
    assert records["slides"]["status"] == "failed"
    assert records["clean"]["status"] == "done"
    monkeypatch.setattr(slides, "run", original)
    assert pipeline.run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)["quality"]["passed"]
    assert calls["clean"] == 1
