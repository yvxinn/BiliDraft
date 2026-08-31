"""Subtitle-first transcription with real VAD segment timestamps."""

import math
import re
from .storage import write_json


def validate_transcript(tr):
    if not isinstance(tr, dict) or not isinstance(tr.get("segments"), list) or not tr["segments"]:
        raise ValueError("字幕为空或格式无效")
    ids, last = set(), -1.0
    for s in tr["segments"]:
        if not isinstance(s, dict) or type(s.get("id")) is not int or s["id"] in ids:
            raise ValueError("字幕 id 重复或格式无效")
        ids.add(s["id"])
        start, end = s.get("start"), s.get("end")
        if type(start) not in (int, float) or type(end) not in (int, float):
            raise ValueError("字幕缺少时间戳")
        if not math.isfinite(start) or not math.isfinite(end) or start < last or start < 0 or end <= start:
            raise ValueError("字幕时间戳无效或顺序错误")
        if not isinstance(s.get("text"), str):
            raise ValueError("字幕正文必须为字符串")
        last = start
    if not any(s["text"].strip() for s in tr["segments"]):
        raise ValueError("字幕没有可读文本")
    return tr


def get_transcript(client, bvid, cid, task_dir, cfg=None, video=None):
    from .config import load_config

    cfg = cfg or load_config()
    sub = client.fetch_subtitle(bvid, cid)
    if sub:
        return validate_transcript(sub)
    if cfg.asr.engine == "disabled":
        raise RuntimeError("该视频无可用字幕，且 ASR 已禁用")
    if video is None:
        raise RuntimeError("ASR 需要已下载的视频")
    from .media import extract_audio

    # Always extract from the selected video; never use an old 10-minute experiment clip.
    audio = extract_audio(video, task_dir / "audio.wav", cfg)
    return _sensevoice(audio, task_dir, cfg)


def sensevoice_result(res):
    if not isinstance(res, list) or not res:
        raise RuntimeError("ASR 没有返回结果")
    rows = [s for r in res for s in r.get("sentence_info", [])]
    if not rows:
        raise RuntimeError("ASR 未返回 VAD 时间戳；需要 FunASR >=1.4.7，不能使用整段零时间戳代替")
    segments = []
    for row in rows:
        text = re.sub(r"<\|[^|]*\|>", "", row.get("text", row.get("sentence", ""))).strip()
        if text:
            segments.append(
                {
                    "id": len(segments),
                    "start": float(row["start"]) / 1000,
                    "end": float(row["end"]) / 1000,
                    "text": text,
                }
            )
    return validate_transcript(
        {"source": "sensevoice", "timestamp_kind": "vad_segment", "segments": segments}
    )


def _sensevoice(audio_path, task_dir, cfg=None):
    from .config import load_config

    cfg = cfg or load_config()
    try:
        from funasr import AutoModel
    except ImportError as e:
        raise RuntimeError('ASR 依赖未安装，请运行 python -m pip install -e ".[asr]"') from e
    model = AutoModel(
        model=cfg.asr.model,
        vad_model=cfg.asr.vad_model,
        device=cfg.asr.device,
        disable_update=True,
        vad_kwargs={"max_single_segment_time": cfg.asr.max_segment_sec * 1000},
    )
    res = model.generate(
        input=str(audio_path),
        batch_size_s=60,
        merge_vad=True,
        merge_length_s=cfg.asr.max_segment_sec,
        sentence_timestamp=True,
    )
    transcript = sensevoice_result(res)
    write_json(task_dir / "asr.json", transcript)
    return transcript
