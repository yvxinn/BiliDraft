"""Validated configuration; paths are relative to the selected workspace."""

import json
import math
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
MIXIN_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]


@dataclass
class BilibiliCfg:
    max_quality: int = 1080
    credential_file: str = "config/credential.json"
    cookies_file: str = "config/cookies.txt"


@dataclass
class LlmCfg:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = field(default="ollama", repr=False)
    api_key_env: str = "BILIDRAFT_API_KEY"
    model: str = ""
    reasoning_effort: str = ""
    temperature: float = 0.2
    timeout: int = 300
    retries: int = 2
    max_workers: int = 2


@dataclass
class PolishCfg:
    level: str = "light_polish"
    min_length_ratio_lossless: float = 0.85
    min_length_ratio_light: float = 0.75
    sim_floor: float = 0.55
    chunk_chars: int = 600
    overlap_chars: int = 70
    split_retries: int = 1


@dataclass
class AsrCfg:
    engine: str = "sensevoice"
    model: str = "iic/SenseVoiceSmall"
    vad_model: str = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
    device: str = "cpu"
    max_segment_sec: int = 15


@dataclass
class SlidesCfg:
    mode: str = "representative"
    representative_per_10min: int = 6
    max_frames: int = 60
    max_workers: int = 0
    representative_ocr: bool = False
    ocr_max_side: int = 960
    parallel_with_text: bool = True
    ocr_threads: int = 2
    block_t: float = 14.0
    stable_frac: float = 0.70
    drift_frac: float = 0.25
    stable_need: float = 2.0
    gap_force: float = 90.0
    phash_hamming: int = 10
    face_ocr_chars: int = 20
    body_per_10min: int = 8


@dataclass
class AlignCfg:
    para_sec: float = 45.0
    para_chars: int = 450
    ref_window_before: float = 15.0
    ref_window_after: float = 60.0
    text_align: bool = True
    similarity_threshold: float = 0.5


@dataclass
class QualityCfg:
    min_retention: float = 0.90
    max_suspect_ratio: float = 0.02


@dataclass
class Config:
    bilibili: BilibiliCfg = field(default_factory=BilibiliCfg)
    llm: LlmCfg = field(default_factory=LlmCfg)
    polish: PolishCfg = field(default_factory=PolishCfg)
    asr: AsrCfg = field(default_factory=AsrCfg)
    slides: SlidesCfg = field(default_factory=SlidesCfg)
    align: AlignCfg = field(default_factory=AlignCfg)
    quality: QualityCfg = field(default_factory=QualityCfg)
    text_mode: str = "polish"
    output_dir: str = "notes"
    ffmpeg: str = ""
    workspace: Path = field(default_factory=Path.cwd, repr=False)

    def path(self, value):
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.workspace / p


def _assign(obj, data, prefix=""):
    if not isinstance(data, dict):
        raise ValueError(f"配置 {prefix or '根节点'} 必须为映射")
    allowed = {f.name for f in fields(obj)} - {"workspace"}
    for k, v in data.items():
        label = f"{prefix}{k}"
        if k not in allowed:
            raise ValueError(f"未知配置项：{label}")
        current = getattr(obj, k)
        if hasattr(current, "__dataclass_fields__"):
            _assign(current, v, label + ".")
        else:
            typ = type(current)
            valid = type(v) in (int, float) if typ is float else type(v) is typ
            if not valid or (isinstance(v, float) and not math.isfinite(v)):
                raise ValueError(f"配置类型错误：{label}")
            setattr(obj, k, v)


def load_config(path=None, workspace=None):
    cfg = Config(workspace=Path(workspace or Path.cwd()).resolve())
    yml = cfg.path(path) if path else cfg.workspace / "config/config.yaml"
    if path and not yml.is_file():
        raise ValueError(f"配置文件不存在：{yml}")
    if yml.exists():
        import yaml

        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8-sig"))
        except (yaml.YAMLError, OSError) as e:
            raise ValueError("无法读取配置 YAML，请检查语法与文件权限") from e
        _assign(cfg, {} if data is None else data)
    key = os.environ.get(cfg.llm.api_key_env) if cfg.llm.api_key_env else None
    if key:
        cfg.llm.api_key = key
    if os.environ.get("BILIDRAFT_MODEL"):
        cfg.llm.model = os.environ["BILIDRAFT_MODEL"]
    if os.environ.get("BILIDRAFT_BASE_URL"):
        cfg.llm.base_url = os.environ["BILIDRAFT_BASE_URL"]
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    url = urlsplit(cfg.llm.base_url)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("llm.base_url 必须为不含凭据、查询参数的 HTTP(S) URL")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程 LLM 必须使用 HTTPS；HTTP 仅用于本机回环地址")
    if not cfg.output_dir.strip():
        raise ValueError("输出目录不能为空")
    if cfg.text_mode not in ("polish", "raw"):
        raise ValueError("text_mode 只能为 polish 或 raw")
    if cfg.llm.reasoning_effort not in ("", "none", "low", "medium", "high"):
        raise ValueError("llm.reasoning_effort 只能为空字符串、none、low、medium 或 high")
    if cfg.polish.level not in ("light_polish", "lossless"):
        raise ValueError("polish.level 只能为 light_polish 或 lossless")
    if cfg.asr.engine not in ("sensevoice", "disabled"):
        raise ValueError("asr.engine 只能为 sensevoice 或 disabled")
    if cfg.slides.mode not in ("representative", "detailed", "none"):
        raise ValueError("slides.mode 只能为 representative、detailed 或 none")
    ranges = {
        "llm.timeout": (1, 3600),
        "llm.retries": (0, 5),
        "llm.max_workers": (1, 8),
        "llm.temperature": (0, 2),
        "polish.chunk_chars": (50, 8000),
        "polish.overlap_chars": (0, 1000),
        "polish.split_retries": (0, 2),
        "polish.min_length_ratio_lossless": (0.5, 1),
        "polish.min_length_ratio_light": (0.5, 1),
        "polish.sim_floor": (0, 1),
        "asr.max_segment_sec": (1, 30),
        "slides.block_t": (0.01, 255),
        "slides.representative_per_10min": (1, 30),
        "slides.max_frames": (1, 300),
        "slides.max_workers": (0, 8),
        "slides.ocr_max_side": (320, 1920),
        "slides.ocr_threads": (1, 16),
        "slides.stable_frac": (0.01, 1),
        "slides.drift_frac": (0.01, 1),
        "slides.stable_need": (0.1, 60),
        "slides.gap_force": (1, 600),
        "slides.phash_hamming": (0, 64),
        "slides.face_ocr_chars": (0, 10000),
        "slides.body_per_10min": (0, 60),
        "align.para_sec": (1, 600),
        "align.para_chars": (50, 4000),
        "align.ref_window_before": (0, 120),
        "align.ref_window_after": (0, 300),
        "align.similarity_threshold": (0, 1),
        "quality.min_retention": (0, 1),
        "quality.max_suspect_ratio": (0, 1),
        "bilibili.max_quality": (144, 4320),
    }
    for name, (low, high) in ranges.items():
        section, key = name.split(".")
        if not low <= getattr(getattr(cfg, section), key) <= high:
            raise ValueError(f"配置 {name} 必须在 {low} 到 {high} 之间")


def load_credential(cfg=None):
    cfg = cfg or load_config()
    f = cfg.path(cfg.bilibili.credential_file)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise ValueError()
        return data
    except (ValueError, OSError) as e:
        raise ValueError("登录凭据格式无效，请重新运行 bilidraft login") from e
