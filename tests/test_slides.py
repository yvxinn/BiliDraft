import cv2
import numpy as np
import pytest
from PIL import Image
from bilidraft import slides
from bilidraft.storage import write_json


@pytest.fixture
def three_pages(tmp_path):
    video = tmp_path / "pages.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (320, 200))
    if not writer.isOpened():
        pytest.fail("OpenCV MJPG encoder unavailable")
    rng = np.random.default_rng(123)
    for _ in range(3):
        cells = rng.integers(0, 2, (5, 8), dtype=np.uint8) * 230
        frame = cv2.resize(cells, (320, 200), interpolation=cv2.INTER_NEAREST)
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        for _ in range(25):
            writer.write(frame)
    writer.release()
    return video


def test_stable_page_timeline_retains_real_cuts(cfg, three_pages):
    periods, forced = slides.scan_pages(three_pages, (0, 0, 320, 200), cfg, progress=lambda *a: None)
    assert len(periods) == 3
    assert all(end > start for start, end in periods)
    assert periods[0][1] < 5 and 5 <= periods[1][0] < 10 and periods[2][0] >= 10
    assert forced[0] == 0


def test_exported_frames_are_full_size_and_deduplicated(cfg, tmp_path, three_pages, monkeypatch):
    cfg.slides.mode = "detailed"
    monkeypatch.setattr(slides, "create_ocr", lambda *a: lambda frame: (None, None))
    task, frames = tmp_path / ".task", tmp_path / "截图"
    write_json(task / "meta.json", {"duration": 15})
    result = slides.run(three_pages, task, frames, cfg, progress=lambda *a: None)
    assert len(result["pages"]) == 3
    assert all(p["width"] == 320 and p["height"] == 200 for p in result["pages"])
    for file in frames.glob("*.jpg"):
        with Image.open(file) as im:
            assert im.size == (320, 200)
    assert len(list(frames.glob("*.jpg"))) == 3


def test_corrupt_video_is_an_actionable_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError, match="无法解码"):
        slides._capture(bad)


def test_phash_collision_does_not_drop_different_slide_content():
    from imagehash import phash

    first = np.full((128, 192), 255, dtype=np.uint8)
    first[:12] = 50
    second = first.copy()
    second[35:65, 15:130] = 30
    same_hash = phash(Image.fromarray(first))
    previous = [(same_hash, first)]
    assert not slides._duplicate(same_hash, second, previous, 10)
    # A small cursor movement or mild codec noise should not create another page.
    noise = first.copy()
    noise[80:82, 150:152] = 0
    assert slides._duplicate(same_hash, noise, previous, 10)


def test_small_content_changes_on_white_template_create_separate_pages(cfg, tmp_path):
    video = tmp_path / "same-template.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (320, 200))
    assert writer.isOpened()
    for left in (15, 115, 215):
        frame = np.full((200, 320, 3), 255, dtype=np.uint8)
        frame[110:150, left : left + 80] = 30
        for _ in range(25):
            writer.write(frame)
    writer.release()
    periods, _ = slides.scan_pages(video, (0, 0, 320, 200), cfg, progress=lambda *a: None)
    assert len(periods) == 3
    assert periods[0][1] < 5 and periods[1][1] < 10


def test_representative_mode_is_bounded_full_frame_and_never_loads_ocr(
    cfg, tmp_path, three_pages, monkeypatch
):
    monkeypatch.setattr(slides, "create_ocr", lambda *a: pytest.fail("default must not run OCR"))
    # More slides than the configured image budget must not become more OCR work.
    cfg.slides.max_frames = 2
    task = tmp_path / ".task"
    write_json(task / "meta.json", {"duration": 15})
    serial = slides.run(three_pages, task, tmp_path / "serial", cfg, progress=lambda *a: None)
    assert 1 <= len(serial["pages"]) <= 2
    assert not serial["ocr_enabled"]
    assert serial["performance"]["worker_seconds"]["ocr"] == 0
    assert serial["pages"][0]["t_capture"] < 15
    assert serial["pages"][0]["width"] == 320
    with Image.open(tmp_path / "serial" / serial["pages"][0]["file"].split("/")[-1]) as im:
        assert im.size == (320, 200)


def test_preview_decode_tracks_cut_and_stays_small(cfg, three_pages):
    from bilidraft.media import read_preview

    first, last, offset = read_preview(three_pages, 4.5, 160, 100, cfg)
    assert first.shape == last.shape == (100, 160)
    assert offset == 0.5
    assert np.abs(first.astype(np.int16) - last.astype(np.int16)).mean() > 10


def test_selection_covers_timeline_not_only_high_detail_first_window(cfg):
    from imagehash import phash

    candidates = []
    rng = np.random.default_rng(73)
    for bin_id in range(6):
        thumbnail = rng.integers(0, 255, (128, 192), dtype=np.uint8)
        candidates.append(
            {
                "bin": bin_id,
                "t": bin_id * 100 + 20,
                "score": 1.0 - bin_id / 10,
                "blank": False,
                "hash": phash(Image.fromarray(thumbnail)),
                "thumbnail": thumbnail,
            }
        )
    expected = slides._choose_representatives(candidates, cfg)
    assert [p["bin"] for p in expected] == list(range(6))
    assert [p["t"] for p in slides._choose_representatives(candidates[::-1], cfg)] == [
        p["t"] for p in expected
    ]
    # Repeated pages can use an alternative in the same time window.
    duplicate = dict(candidates[0], bin=1, t=110, score=2)
    assert len(slides._choose_representatives(candidates + [duplicate], cfg)) == 6


def test_parallel_and_serial_representative_results_are_identical(cfg, tmp_path, three_pages):
    task = tmp_path / ".task"
    write_json(task / "meta.json", {"duration": 15})
    cfg.slides.max_workers = 1
    a = slides.run(three_pages, task, tmp_path / "a", cfg, progress=lambda *a: None)
    cfg.slides.max_workers = 4
    b = slides.run(three_pages, task, tmp_path / "b", cfg, progress=lambda *a: None)
    assert a["pages"] == b["pages"]
    for page in a["pages"]:
        name = page["file"].split("/")[-1]
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_tiny_clip_preview_does_not_require_two_frames(cfg, tmp_path):
    from bilidraft.media import read_preview

    path = tmp_path / "tiny.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (320, 200))
    writer.write(np.full((200, 320, 3), 100, dtype=np.uint8))
    writer.release()
    first, last, offset = read_preview(path, 0, 160, 100, cfg)
    assert offset == 0 and np.array_equal(first, last)


def test_worker_count_is_bounded_on_small_cpu(monkeypatch):
    monkeypatch.setattr(slides.os, "cpu_count", lambda: 2)
    assert slides.worker_count(8) == 1
    monkeypatch.setattr(slides.os, "cpu_count", lambda: 20)
    assert slides.worker_count(0) == 4


def test_cancelled_selection_does_not_commit_partial_slides(cfg, tmp_path, three_pages, monkeypatch):
    from concurrent.futures import CancelledError
    from threading import Event

    stop = Event()
    stop.set()
    task = tmp_path / ".task"
    write_json(task / "meta.json", {"duration": 15})
    monkeypatch.setattr(slides, "read_preview", lambda *a: pytest.fail("cancelled work must not decode"))
    with pytest.raises(CancelledError):
        slides.run(three_pages, task, tmp_path / "frames", cfg, cancel_event=stop)
    assert not (task / "slides.json").exists()


def test_failed_preview_does_not_leave_a_success_manifest(cfg, tmp_path, three_pages, monkeypatch):
    task = tmp_path / ".task"
    write_json(task / "meta.json", {"duration": 15})
    monkeypatch.setattr(
        slides, "read_preview", lambda *a: (_ for _ in ()).throw(RuntimeError("decode failed"))
    )
    with pytest.raises(RuntimeError, match="decode failed"):
        slides.run(three_pages, task, tmp_path / "frames", cfg, progress=lambda *a: None)
    assert not (task / "slides.json").exists()
