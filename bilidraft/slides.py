"""Stable-page detection with bounded frame memory and full-frame exports."""

import time
import os
import math
from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event, local
import cv2
import numpy as np
from imagehash import phash
from PIL import Image
from .storage import read_json, write_json
from .media import read_frame, read_preview, sample_frames

ALGO = "representative_v8_sparse_parallel"
DETAILED_ALGO = "anchor_drift_v7_content_guard"


def create_ocr(threads=2):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        raise RuntimeError("OCR 尚未安装：运行 python scripts/setup.py --ocr 或安装 bilidraft[ocr]") from e

    threads = max(1, min(threads, os.cpu_count() or 1))
    return RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)


def _blk(frame, roi):
    x, y, w, h = roi
    gray = frame[y : y + h, x : x + w]
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float32)


def _frac_stable(a, b, threshold):
    return float((np.abs(a - b) < threshold).mean())


def _duplicate(hh, thumbnail, previous, hamming):
    # pHash alone collapses different text on slides with the same template.
    # Keep a tiny grayscale thumbnail to confirm that the content is also close.
    for other_hash, other_thumbnail in previous:
        if abs(hh - other_hash) < hamming:
            delta = np.abs(thumbnail.astype(np.int16) - other_thumbnail.astype(np.int16))
            if float(delta.mean()) <= 2 and float((delta > 18).mean()) <= 0.01:
                return True
    return False


def _capture(video):
    # FFmpeg's automatic decoder thread count can overwhelm laptop CPUs,
    # especially when an OCR session is alive in the same process.
    cap = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    if not cap.isOpened() or cap.get(cv2.CAP_PROP_FRAME_WIDTH) < 1:
        cap.release()
        raise RuntimeError("视频无法解码，请检查文件是否完整")
    return cap


def detect_roi(video_path, duration, probe=10, ocr=None, cfg=None):
    if ocr is None:
        ocr = create_ocr()
    cap = _capture(video_path)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    density = np.zeros(width)
    try:
        for i in range(probe):
            second = duration * (i + 0.5) / probe
            if cfg is not None:
                frame = read_frame(video_path, second, cfg)
            else:
                cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
                ok, frame = cap.read()
                if not ok:
                    continue
            for box, _, confidence in ocr(frame)[0] or []:
                a = max(0, int(min(p[0] for p in box)))
                b = min(width - 1, int(max(p[0] for p in box)))
                density[a : b + 1] += confidence * (b - a + 1) / width
    finally:
        cap.release()
    kernel = min(41, width)
    sm = np.convolve(density, np.ones(kernel) / kernel, mode="same")
    if sm.max() <= 0:
        return 0, 0, width, height
    mask = np.r_[False, sm >= sm.max() * 0.25, False]
    changes = np.diff(mask.astype(int))
    starts, ends = np.where(changes == 1)[0], np.where(changes == -1)[0]
    k = int(np.argmax(ends - starts))
    if ends[k] - starts[k] < width * 0.2:
        return 0, 0, width, height
    a, b = max(int(starts[k] * 0.96), 0), min(int(ends[k] * 1.04), width)
    return a, 0, b - a, height


def scan_pages(video_path, roi, cfg, progress=print):
    """Keep timestamps and tiny block hashes, never a full image per candidate."""
    cap = _capture(video_path)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    scaled_w = min(width, 640)
    scaled_h = max(2, round(height * scaled_w / width / 2) * 2)
    x, y, w, h = roi
    scaled_roi = (
        int(x * scaled_w / width),
        int(y * scaled_h / height),
        max(1, int(w * scaled_w / width)),
        max(1, int(h * scaled_h / height)),
    )
    sc = cfg.slides
    periods, force = [], []
    current, last = None, None
    force_t, force_previous = -sc.gap_force, []
    index, last_t = 0, 0.0
    started = time.monotonic()
    stream = sample_frames(video_path, scaled_w, scaled_h, cfg)
    try:
        for t, frame in stream:
            last_t = t
            block = _blk(frame, scaled_roi)
            x, y, w, h = scaled_roi
            thumbnail = cv2.resize(frame[y : y + h, x : x + w], (192, 128), interpolation=cv2.INTER_AREA)
            if last is not None:
                if _frac_stable(block, last, sc.block_t) >= sc.stable_frac:
                    if current is None:
                        current = [max(0, t - 1), t, block.copy(), thumbnail.copy()]
                    elif (
                        _frac_stable(block, current[2], sc.block_t) < 1 - sc.drift_frac
                        or float((np.abs(thumbnail.astype(np.int16) - current[3]) > 18).mean()) > 0.025
                    ):
                        if current is not None and current[1] - current[0] >= sc.stable_need:
                            periods.append((current[0], current[1]))
                        current = [t, t, block.copy(), thumbnail.copy()]
                    else:
                        current[1] = t
                else:
                    if current is not None and current[1] - current[0] >= sc.stable_need:
                        periods.append((current[0], current[1]))
                    current = None
            if t - force_t >= sc.gap_force:
                hh = phash(Image.fromarray(frame[y : y + h, x : x + w]))
                if not _duplicate(hh, thumbnail, force_previous, 12):
                    force.append(t)
                    force_previous = [(hh, thumbnail.copy())]
                    force_t = t
            last = block
            if int(t) % 300 == 0:
                progress(f"slides 扫描 {int(t) // 60} 分钟")
            index += 1
    finally:
        stream.close()
    if current is not None and current[1] - current[0] >= sc.stable_need:
        periods.append((current[0], current[1]))
    if index == 0:
        raise RuntimeError("视频没有可读帧")
    progress(
        f"slides 扫描 {last_t:.0f}s，耗时 {time.monotonic() - started:.0f}s，候选 {len(periods) + len(force)}"
    )
    return periods, force


def _run_detailed(video_path, task_dir, frames_dir, cfg, progress=print):
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = float(read_json(task_dir / "meta.json")["duration"])
    progress("slides 初始化 OCR 与 ROI 探测")
    ocr = create_ocr(cfg.slides.ocr_threads)
    roi = detect_roi(video_path, duration, ocr=ocr, cfg=cfg)
    periods, force = scan_pages(video_path, roi, cfg, progress)
    items = [(end, start, end, "stable") for start, end in periods]
    items += [(t, t, t, "gap_fill") for t in force]
    # Prefer complete stable-page captures over an earlier temporary gap-fill frame.
    items.sort(key=lambda x: (x[3] != "stable", x[0]))
    cap = _capture(video_path)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        cap.release()
        raise RuntimeError("OpenCV 人脸检测模型缺失")
    x, y, w, h = roi
    kept, hashes = [], []
    try:
        for t, start, end, kind in items:
            frame = read_frame(video_path, t, cfg)
            crop = frame[y : y + h, x : x + w]
            hh = phash(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            thumbnail = cv2.resize(gray, (192, 128), interpolation=cv2.INTER_AREA)
            if _duplicate(hh, thumbnail, hashes, cfg.slides.phash_hamming):
                continue
            hashes.append((hh, thumbnail))
            result, _ = ocr(crop)
            text = "\n".join(r[1] for r in result or [])
            faces = cascade.detectMultiScale(gray, 1.1, 4, minSize=(max(20, int(h * 0.12)),) * 2)
            # Timestamp-based names are stable and ASCII; OpenCV imencode handles Unicode parent paths.
            name = f"p_{int(round(t * 1000)):09d}.jpg"
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                raise RuntimeError("截图编码失败")
            (frames_dir / name).write_bytes(encoded.tobytes())
            kept.append(
                {
                    "t_capture": round(t, 3),
                    "t_start": round(start, 3),
                    "t_end": round(end, 3),
                    "file": f"frames/{name}",
                    "kind": kind,
                    "ocr_text": text,
                    "ocr_chars": len(text.replace("\n", "")),
                    "has_faces": len(faces),
                    "talking_head": len(faces) > 0 and len(text) < cfg.slides.face_ocr_chars,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                }
            )
            if len(kept) % 10 == 0:
                progress(f"slides 已保存 {len(kept)} 张完整画面")
    finally:
        cap.release()
    kept.sort(key=lambda p: p["t_capture"])
    out = {
        "roi": list(map(int, roi)),
        "duration": duration,
        "algo": DETAILED_ALGO,
        "mode": "detailed",
        "pages": kept,
    }
    write_json(task_dir / "slides.json", out)
    return out


def worker_count(requested=0):
    # Reserve room for native libraries and the OS. More sessions are not always
    # faster: each OCR instance has its own ONNX Runtime pools.
    cpus = os.cpu_count() or 1
    return max(1, min(requested or 4, max(1, cpus // 2)))


def _preview_times(duration, budget):
    """Three alternatives per evenly spread time window; never sample past EOF."""
    width = duration / budget
    for index in range(budget):
        for fraction in (0.2, 0.5, 0.8):
            yield index, max(0.0, min((index + fraction) * width, duration - 1.0))


def _preview_candidate(video, item, width, height, cfg, stop):
    if stop.is_set():
        raise CancelledError()
    index, t = item
    started = time.monotonic()
    first, last, offset = read_preview(video, t, width, height, cfg)
    decode = time.monotonic() - started
    thumbnail = cv2.resize(last, (192, 128), interpolation=cv2.INTER_AREA)
    # Motion between nearby frames penalizes transitions and scrolling. Edge
    # density rewards diagrams/text without the cost of reading every word.
    motion = float(np.abs(last.astype(np.int16) - first.astype(np.int16)).mean())
    edges = float((np.abs(np.diff(last.astype(np.int16), axis=1)) > 20).mean())
    blank = float(last.std()) < 5 or edges < 0.0005
    score = 0.65 * (1 - min(motion / 25, 1)) + 0.35 * min(edges / 0.08, 1)
    return {
        "bin": index,
        "t": t + offset,
        "score": score,
        "blank": blank,
        "hash": phash(Image.fromarray(thumbnail)),
        "thumbnail": thumbnail,
        "decode_sec": decode,
    }


def _choose_representatives(candidates, cfg):
    chosen, previous = [], []
    grouped = {}
    for item in candidates:
        grouped.setdefault(item["bin"], []).append(item)
    for index in sorted(grouped):
        # Tie-break by timestamp so thread completion order cannot change output.
        for item in sorted(grouped[index], key=lambda p: (-p["score"], p["t"])):
            if item["blank"] or _duplicate(
                item["hash"], item["thumbnail"], previous, cfg.slides.phash_hamming
            ):
                continue
            chosen.append(item)
            previous.append((item["hash"], item["thumbnail"]))
            break
    return chosen


def _render_representative(video, candidate, frames_dir, cfg, state, stop, ocr_threads):
    if stop.is_set():
        raise CancelledError()
    t = candidate["t"]
    started = time.monotonic()
    frame = read_frame(video, t, cfg, threads=1)
    captured = time.monotonic() - started
    h, w = frame.shape[:2]
    text, ocr_elapsed, init_elapsed = "", 0.0, 0.0
    if cfg.slides.representative_ocr:
        started = time.monotonic()
        if not hasattr(state, "ocr"):
            state.ocr = create_ocr(ocr_threads)
        init_elapsed = time.monotonic() - started
        ratio = min(1, cfg.slides.ocr_max_side / max(w, h))
        analysis = cv2.resize(frame, (max(1, round(w * ratio)), max(1, round(h * ratio))))
        started = time.monotonic()
        result, _ = state.ocr(analysis)
        ocr_elapsed = time.monotonic() - started
        text = "\n".join(r[1] for r in result or [])
    started = time.monotonic()
    if not hasattr(state, "faces"):
        state.faces = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if state.faces.empty():
            raise RuntimeError("OpenCV 人脸检测模型缺失")
    small_w = min(w, 480)
    gray = cv2.cvtColor(cv2.resize(frame, (small_w, max(1, round(h * small_w / w)))), cv2.COLOR_BGR2GRAY)
    faces = state.faces.detectMultiScale(gray, 1.1, 4, minSize=(max(20, int(gray.shape[0] * 0.12)),) * 2)
    # Without OCR, only a large central face is excluded from the body. Small
    # lecturer webcams must not suppress useful presentation screenshots.
    large_face = any(fw * fh >= gray.size * 0.06 for _, _, fw, fh in faces)
    talking_head = bool(large_face and len(text) < cfg.slides.face_ocr_chars)
    name = f"p_{int(round(t * 1000)):09d}.jpg"
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("截图编码失败")
    (frames_dir / name).write_bytes(encoded.tobytes())
    return {
        "t_capture": round(t, 3),
        "t_start": round(t, 3),
        "t_end": round(t, 3),
        "file": f"frames/{name}",
        "kind": "representative",
        "ocr_text": text,
        "ocr_chars": len(text.replace("\n", "")),
        "has_faces": len(faces),
        "talking_head": talking_head,
        "width": w,
        "height": h,
        "visual_score": round(candidate["score"], 4),
    }, {
        "capture": captured,
        "ocr_init": init_elapsed,
        "ocr": ocr_elapsed,
        "classify_encode": time.monotonic() - started,
    }


def run(video_path, task_dir, frames_dir, cfg, progress=print, cancel_event=None):
    """Blog illustrations by default; detailed mode preserves dense page analysis."""
    stop = cancel_event if cancel_event is not None else Event()

    def log(message):
        if stop.is_set():
            raise CancelledError()
        progress(message)

    if cfg.slides.mode == "none":
        out = {
            "duration": read_json(task_dir / "meta.json")["duration"],
            "mode": "none",
            "algo": "disabled",
            "pages": [],
            "ocr_enabled": False,
        }
        write_json(task_dir / "slides.json", out)
        return out
    cv2.setNumThreads(1)
    if cfg.slides.mode == "detailed":
        return _run_detailed(video_path, task_dir, frames_dir, cfg, log)
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = float(read_json(task_dir / "meta.json")["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("视频时长必须为正数")
    budget = min(
        cfg.slides.max_frames, max(1, math.ceil(duration * cfg.slides.representative_per_10min / 600))
    )
    cap = _capture(video_path)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    scaled_w = min(width, 480)
    scaled_h = max(2, round(height * scaled_w / width / 2) * 2)
    workers = min(worker_count(cfg.slides.max_workers), budget * 3)
    ocr_threads = min(cfg.slides.ocr_threads, max(1, (os.cpu_count() or 1) // workers // 2))
    started = time.monotonic()
    log(
        f"slides 代表配图：最多 {budget} 张，{workers} 个并行任务，OCR {'开启' if cfg.slides.representative_ocr else '关闭'}"
    )
    candidates, kept, timings = [], [], {}
    state = local()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="slides") as pool:
        try:
            selection_start = time.monotonic()
            jobs = [
                pool.submit(_preview_candidate, video_path, item, scaled_w, scaled_h, cfg, stop)
                for item in _preview_times(duration, budget)
            ]
            for i, job in enumerate(jobs, 1):
                candidates.append(job.result())
                if i % 30 == 0 or i == len(jobs):
                    log(f"slides 预览 {i}/{len(jobs)}")
            chosen = _choose_representatives(candidates, cfg)
            selection_wall = time.monotonic() - selection_start
            render_start = time.monotonic()
            jobs = [
                pool.submit(
                    _render_representative, video_path, item, frames_dir, cfg, state, stop, ocr_threads
                )
                for item in chosen
            ]
            for i, job in enumerate(jobs, 1):
                page, elapsed = job.result()
                kept.append(page)
                for key, value in elapsed.items():
                    timings[key] = timings.get(key, 0.0) + value
                if i % 10 == 0 or i == len(jobs):
                    log(f"slides 完整配图 {i}/{len(jobs)}")
            render_wall = time.monotonic() - render_start
        except BaseException:
            stop.set()
            for job in jobs:
                job.cancel()
            raise
    if stop.is_set():
        raise CancelledError()
    performance = {
        "wall_sec": round(time.monotonic() - started, 3),
        "selection_wall_sec": round(selection_wall, 3),
        "render_wall_sec": round(render_wall, 3),
        "worker_seconds": {
            "preview_decode": round(sum(p["decode_sec"] for p in candidates), 3),
            **{k: round(v, 3) for k, v in timings.items()},
        },
        "workers": workers,
        "ocr_threads_per_worker": ocr_threads if cfg.slides.representative_ocr else 0,
        "preview_count": len(candidates),
        "frame_budget": budget,
        "selected_count": len(kept),
        "note": "wall 为实际等待时间；worker_seconds 为并行工作之和，不能相加当作总耗时。",
    }
    out = {
        "roi": [0, 0, width, height],
        "duration": duration,
        "algo": ALGO,
        "mode": "representative",
        "ocr_enabled": cfg.slides.representative_ocr,
        "performance": performance,
        "pages": sorted(kept, key=lambda p: p["t_capture"]),
    }
    write_json(task_dir / "slides.json", out)
    log(f"slides 完成：{len(kept)} 张，选图 {selection_wall:.1f}s，取图/分析 {render_wall:.1f}s")
    return out
