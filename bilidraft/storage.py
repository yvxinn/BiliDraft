"""Atomic artifacts, fingerprints and an OS-backed task lock."""

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, parse_qs


def validate_bvid(value):
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        raise ValueError("请输入完整 BV 号（例如 BV1234567890），不支持路径或多 P URL")
    return value


def parse_video_input(value):
    """Normalize a BV or canonical Bilibili URL without making HTTP requests."""
    value = value.strip()
    if value.startswith("BV"):
        return validate_bvid(value)
    url = urlsplit(value)
    if (
        url.scheme not in ("http", "https")
        or url.hostname not in ("bilibili.com", "www.bilibili.com", "m.bilibili.com")
        or url.username
        or url.password
        or url.port
    ):
        raise ValueError("请输入 BV 号或 bilibili.com/video/BV... 链接；短链请先展开")
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", url.path)
    if not match:
        raise ValueError("不是有效的 B站视频链接")
    pages = parse_qs(url.query, keep_blank_values=True).get("p", ["1"])
    if pages != ["1"]:
        raise ValueError("当前只支持第一 P，不会静默处理其他分 P")
    return validate_bvid(match[1])


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, obj):
    atomic_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_signature(path):
    path = Path(path)
    if not path.is_file():
        return None
    stat = path.stat()
    if stat.st_size <= 4 * 1024 * 1024:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


@contextmanager
def task_lock(task_dir):
    from filelock import FileLock, Timeout

    task_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(task_dir / "run.lock"), timeout=0)
    try:
        lock.acquire()
    except Timeout as e:
        raise RuntimeError("该视频已有任务在运行，请等待完成后再重试") from e
    try:
        yield
    finally:
        lock.release()
