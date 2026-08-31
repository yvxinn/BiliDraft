"""Portable media tools; never invoke a shell or use implicit video-only formats."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_ffmpeg(configured=""):
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            path /= "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        found = str(path) if path.is_file() else shutil.which(configured)
        if not found:
            raise RuntimeError("配置的 ffmpeg 不存在")
        return found
    found = shutil.which("ffmpeg")
    if found:
        return found
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        try:
            hits = sorted((root / "Microsoft/WinGet/Packages").glob("Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"))
        except OSError:
            hits = []
        if hits:
            return str(hits[-1])
    raise RuntimeError("未找到 ffmpeg，请安装并加入 PATH，或配置 ffmpeg 的完整路径")


def find_video(task_dir):
    for name in ("video.mp4", "video.mkv", "video.webm", "video_1080p.mp4", "video_720p.mp4"):
        p = task_dir / name
        if p.is_file() and p.stat().st_size:
            return p
    return None


def download_video(bvid, task_dir, cfg):
    ffmpeg = find_ffmpeg(cfg.ffmpeg)
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "30",
        "--ffmpeg-location",
        str(Path(ffmpeg).parent),
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "-f",
        f"bv[height<={cfg.bilibili.max_quality}]+ba/b[height<={cfg.bilibili.max_quality}]",
        "-o",
        str(task_dir / "video.%(ext)s"),
    ]
    cookies = cfg.path(cfg.bilibili.cookies_file)
    if cookies.is_file():
        cmd += ["--cookies", str(cookies)]
    cmd += [f"https://www.bilibili.com/video/{bvid}?p=1"]
    try:
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=3600)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("视频下载超过 1 小时，已停止；可用 --resume 续跑") from e
    if result.returncode:
        # yt-dlp error text can contain signed URLs/cookies; keep it out of reports.
        raise RuntimeError("yt-dlp 下载失败，请检查登录状态、网络和视频权限；可运行 bilidraft doctor")
    video = find_video(task_dir)
    if video is None:
        raise RuntimeError("下载器未产生可用视频文件")
    return video


def extract_audio(video, target, cfg):
    target = Path(target)
    temporary = target.with_name(target.stem + ".partial.wav")
    cmd = [
        find_ffmpeg(cfg.ffmpeg),
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(temporary),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode or not temporary.is_file() or temporary.stat().st_size <= 44:
            raise RuntimeError("无法从视频提取音频；请确认视频包含音轨")
        os.replace(temporary, target)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("音频提取超时") from e
    finally:
        temporary.unlink(missing_ok=True)
    return target


def read_frame(video, second, cfg, *, threads=2):
    """Decode one full-size frame with the installed FFmpeg (including AV1)."""
    import cv2
    import numpy as np

    command = [
        find_ffmpeg(cfg.ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        str(threads),
        "-ss",
        str(max(0, second)),
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-an",
        "-filter_threads",
        "1",
        "-threads",
        "1",
        "-q:v",
        "2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"读取 {second:.1f}s 视频帧超时") from e
    if result.returncode or not result.stdout:
        raise RuntimeError(f"无法解码 {second:.1f}s 视频帧")
    frame = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("视频帧图像解码失败")
    return frame


def read_preview(video, second, width, height, cfg):
    """Seek to a sparse candidate and decode two small frames 0.5s apart.

    Accurate input seeking decodes from the nearest keyframe, not from the
    beginning of the video. Decoder, filter and output threads are all bounded;
    independent candidates are parallelized by the caller.
    """
    import numpy as np

    command = [
        find_ffmpeg(cfg.ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-ss",
        str(max(0, second)),
        "-i",
        str(video),
        "-an",
        "-filter_threads",
        "1",
        "-vf",
        f"fps=2:round=up:start_time=0,scale={width}:{height}:flags=area,format=gray",
        "-frames:v",
        "2",
        "-threads",
        "1",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"读取 {second:.1f}s 预览帧超时") from e
    size = width * height
    if result.returncode or len(result.stdout) not in (size, size * 2):
        raise RuntimeError(f"无法解码 {second:.1f}s 视频预览")
    frames = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, height, width)
    return frames[0], frames[-1], 0.5 if len(frames) == 2 else 0.0


def sample_frames(video, width, height, cfg):
    """Stream 1fps grayscale frames; memory is bounded to one analysis frame."""
    import numpy as np

    command = [
        find_ffmpeg(cfg.ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "2",
        "-i",
        str(video),
        "-an",
        "-vf",
        f"fps=1:round=up:start_time=0,scale={width}:{height}:flags=area,format=gray",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
        try:
            second, size = 0, width * height
            while True:
                data = process.stdout.read(size)
                if not data:
                    break
                if len(data) != size:
                    raise RuntimeError("FFmpeg 返回不完整的分析帧")
                yield second, np.frombuffer(data, dtype=np.uint8).reshape(height, width)
                second += 1
            if process.wait(timeout=30) != 0:
                raise RuntimeError("FFmpeg 视频扫描失败，请检查文件完整性")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
