"""Markdown export with safe local image paths and visible fallback markers."""

import re
from pathlib import Path
from .storage import atomic_text


def _fmt(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _inline(text):
    text = re.sub(r"\s+", " ", str(text))
    return re.sub(r"([\\`*_{}\[\]<>#])", r"\\\1", text)


def run_export(note_dir, clean, layout, slides, meta, cfg):
    note_dir = Path(note_dir)
    rep, paras = clean["report"], layout["paragraphs"]
    seg2para = {sid: i for i, p in enumerate(paras) for sid in p["seg_ids"]}
    segments = {s["id"]: s for s in clean["segments"]}
    numeric = [r for r in rep.get("chunks_detail", []) if r.get("num_warning")]
    number_reviews = {}
    for review in numeric:
        first, last = segments.get(review.get("first_id")), segments.get(review.get("last_id"))
        if first and last and first["id"] in seg2para:
            number_reviews.setdefault(seg2para[first["id"]], []).append((first, last, review))
    heads = {}
    for h in layout["structure"]["headings"]:
        pi = seg2para.get(h["after_id"])
        if pi is not None and pi not in heads:
            heads[pi] = h
    src = {"upowner": "B站字幕", "bilibili_ai": "B站 AI 字幕", "sensevoice": "SenseVoice（VAD 时间戳）"}.get(
        rep.get("source"), rep.get("source", "未知")
    )
    lines = [
        f"# {_inline(meta['title'])}",
        "",
        f"> **UP 主**：{_inline(meta['owner'])} · **BV**：{meta['bvid']} · **时长**：{_fmt(meta['duration'])}",
        f"> **字幕来源**：{_inline(src)} · **文本长度保留率**：{rep['retention'] * 100:.1f}% · **存疑段**：{rep['suspect_ratio'] * 100:.1f}%",
        f"> **配图**：正文 {len(layout['body'])} 张 / 附录 {len(layout['appendix'])} 张",
        f"> **数字写法待核对**：{len(numeric)} 个润色块（可能为数字改写、术语纠错或重复消除）",
        "> 自动校验不等于事实核验；存疑段保留原文，请核对数字与术语。",
        "",
    ]
    if slides.get("mode") == "representative":
        lines.insert(-1, "> 配图为按时间分布选取的参考画面，不保证保留每次翻页；正文仍保留完整讲解。")
    if rep.get("processing_mode") == "raw":
        lines.insert(-1, "> **原始字幕模式**：未调用语言模型，未做 AI 润色或语义检查；标题按时间分段。")
    by_para = {}
    for item in layout["body"]:
        by_para.setdefault(item["para_index"], []).append(item["page"])

    def add_image(page):
        relative = Path(page["file"])
        full = (note_dir / relative).resolve()
        if (
            relative.is_absolute()
            or not full.is_relative_to((note_dir / "frames").resolve())
            or not full.is_file()
        ):
            raise ValueError("配图路径无效或文件缺失")
        lines.extend(["", f"![]({relative.as_posix()})", f"*▲ {_fmt(page['t_capture'])}*", ""])

    for i, p in enumerate(paras):
        h = heads.get(i)
        if h:
            prefix = f"{_fmt(p['start'])} · " if h.get("mode") else ""
            lines.extend([f"{'#' * h['level']} {prefix}{_inline(h['title'])}", ""])
        if p.get("suspect"):
            lines.extend(
                [f"> ⚠ 本段 {_fmt(p['start'])}–{_fmt(p['end'])} 含未通过润色校验的原文，请核对。", ""]
            )
        for first, last, review in number_reviews.get(i, []):
            values = _inline("、".join(dict.fromkeys(review["num_warning"])))
            lines.extend(
                [
                    f"> ⚠ {_fmt(first['start'])}–{_fmt(last['end'])} 的润色块有数字写法变化：{values}。"
                    "请对照原始字幕与画面核对；原文保留在 .task/subtitle.json。",
                    "",
                ]
            )
        if p["text"].strip():
            # Escape HTML to prevent transcript content becoming active markup.
            text = p["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Transcript text is not a source of active image/link directives.
            text = text.replace("[", "\\[").replace("]", "\\]")
            lines.append(text)
        for page in by_para.get(i, []):
            add_image(page)
        lines.append("")
    if layout["appendix"]:
        title = "其余参考画面" if slides.get("mode") == "representative" else "其余页面（含口播帧）"
        lines.extend(["---", "", f"## 附录：{title}", ""])
        for item in layout["appendix"]:
            add_image(item["page"])
    out = note_dir / "note.md"
    atomic_text(out, "\n".join(lines) + "\n")
    return out
