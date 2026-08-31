"""Machine-readable quality evidence; metrics do not prove semantic equivalence."""

from collections import Counter
from .storage import read_json


def build_report(note, cfg, records=None):
    task = note / ".task"
    clean, layout, slides, outline = [
        read_json(task / (name + ".json")) for name in ("clean", "layout", "slides", "outline")
    ]
    metrics = clean["report"]
    warnings = []
    if metrics["retention"] < cfg.quality.min_retention:
        warnings.append("文本长度保留率低于阈值")
    if metrics["suspect_ratio"] > cfg.quality.max_suspect_ratio:
        warnings.append("存疑段比例高于阈值，可用 --retry-failed 重试")
    if outline.get("fallback_reason"):
        warnings.append("AI 小标题生成失败，已使用时间分段标题")
    numeric = sum(bool(x.get("num_warning")) for x in metrics.get("chunks_detail", []))
    if numeric:
        warnings.append(f"{numeric} 个块有数字写法告警，需要人工核对")
    referenced = [x["page"]["file"] for x in layout["body"] + layout["appendix"]]
    missing = [f for f in referenced if not (note / f).is_file()]
    if missing:
        warnings.append("存在缺失配图")
    all_frames = {p["file"] for p in slides["pages"]}
    if set(referenced) != all_frames or len(referenced) != len(set(referenced)):
        warnings.append("配图未完整分流或发生重复")
    return {
        "schema_version": 1,
        "text_mode": metrics.get("processing_mode", "polish"),
        "quality": {
            "passed": not warnings,
            "warnings": warnings,
            "thresholds": {
                "min_retention": cfg.quality.min_retention,
                "max_suspect_ratio": cfg.quality.max_suspect_ratio,
            },
            "note": "长度保留率和模型自检不等于事实核验，请抽查数字、术语和存疑段。",
        },
        "polish": metrics,
        "images": {
            "mode": slides.get("mode", "detailed"),
            "ocr_enabled": slides.get("ocr_enabled", True),
            "performance": slides.get("performance", {}),
            "total": len(slides["pages"]),
            "body": len(layout["body"]),
            "appendix": len(layout["appendix"]),
            "missing": missing,
            "alignment": dict(Counter(x.get("alignment", "time") for x in layout["body"])),
        },
        "headings": len(outline["headings"]),
        "stages": {
            s: {"status": r.get("status"), "elapsed_sec": r.get("elapsed_sec")}
            for s, r in (records or {}).items()
            if s != "report"
        },
        "cost": {"currency": None, "amount": None, "note": "未采集供应商计费数据，不推算或宣称零成本。"},
    }
