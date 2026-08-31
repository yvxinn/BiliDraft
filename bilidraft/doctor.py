"""Actionable diagnostics; offline checks never contact a provider."""

import importlib
import importlib.util
from importlib.metadata import version, PackageNotFoundError
from .config import load_credential
from .media import find_ffmpeg


def diagnose(cfg, offline=False):
    checks = []

    def add(name, ok, message, required=True):
        checks.append({"name": name, "ok": bool(ok), "required": required, "message": message})

    for name in ("requests", "yaml", "yt_dlp", "cv2", "imagehash", "PIL", "filelock"):
        add(
            name,
            importlib.util.find_spec(name) is not None,
            "已安装" if importlib.util.find_spec(name) else "请安装项目依赖",
        )
    needs_ocr = cfg.slides.mode == "detailed" or (
        cfg.slides.mode == "representative" and cfg.slides.representative_ocr
    )
    present_ocr = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    add(
        "ocr",
        present_ocr,
        "已安装" if present_ocr else "可选 OCR：安装 bilidraft[ocr] 或运行 scripts/setup.py --ocr",
        required=needs_ocr,
    )
    add(
        "model_config",
        bool(cfg.llm.model.strip()),
        "已配置模型" if cfg.llm.model.strip() else "尚未配置 llm.model；原始字幕模式无需模型",
        required=not offline and cfg.text_mode == "polish",
    )
    variants = []
    for name in (
        "opencv-python",
        "opencv-python-headless",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
    ):
        try:
            variants.append(f"{name}=={version(name)}")
        except PackageNotFoundError:
            pass
    add(
        "opencv_distribution",
        len(variants) == 1,
        ", ".join(variants)
        if len(variants) == 1
        else "OpenCV 安装缺失或多个包共享 cv2；请使用新虚拟环境重新安装项目",
    )
    try:
        cv2 = importlib.import_module("cv2")
        usable = all(hasattr(cv2, name) for name in ("CascadeClassifier", "VideoCapture", "imdecode"))
        add(
            "opencv_runtime",
            usable,
            "关键接口可用" if usable else "OpenCV 关键接口缺失；请检查版本和重复安装",
        )
    except (ImportError, OSError):
        add("opencv_runtime", False, "OpenCV 无法载入；检查重复安装和系统动态库（Linux 的 libGL）")
    try:
        add("ffmpeg", True, find_ffmpeg(cfg.ffmpeg))
    except RuntimeError as e:
        add("ffmpeg", False, str(e))
    try:
        present = bool(load_credential(cfg).get("sessdata"))
        add(
            "login",
            present,
            "本地凭据存在（在线自检才验证有效性）" if present else "尚未登录，可运行 bilidraft login",
            required=False,
        )
    except ValueError as e:
        add("login", False, str(e), required=False)
    if cfg.asr.engine != "disabled":
        installed = importlib.util.find_spec("funasr") is not None
        add(
            "asr",
            installed,
            "无字幕兜底可用；模型首次使用需下载" if installed else '无字幕视频需要 pip install -e ".[asr]"',
            required=False,
        )
    if not offline:
        try:
            from .bilibili import BilibiliClient

            data = BilibiliClient(cfg).get(
                "https://api.bilibili.com/x/web-interface/nav", allow_logged_out=True
            )
            add("bilibili", True, "API 连通")
            add(
                "login_online",
                bool(data.get("data", {}).get("isLogin")),
                "已登录" if data.get("data", {}).get("isLogin") else "未登录或凭据已失效",
                required=False,
            )
        except Exception as e:
            add("bilibili", False, str(e))
        if cfg.text_mode == "polish" and cfg.llm.model.strip():
            try:
                from .llm import chat, parse_json

                response = parse_json(chat(cfg.llm, '输出JSON对象 {"ok":true}', "连通性测试"))
                add(
                    "llm",
                    response.get("ok") is True,
                    "模型响应正常" if response.get("ok") is True else "模型响应格式不符",
                )
            except Exception as e:
                add("llm", False, str(e))
    return {"ok": all(c["ok"] for c in checks if c["required"]), "offline": offline, "checks": checks}
