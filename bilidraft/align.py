"""Time alignment with conservative OCR/reference refinement."""

import re
import math
from bisect import bisect_right

REF_RE = re.compile(
    r"(如图所示|看这|看一下这|这张图|这一页|这个公式|大家看|左边|右边|as you can see|look at this)", re.I
)


def _visible(s):
    text = s["clean"]
    if any(flag in s.get("flags", []) for flag in ("raw_fallback", "unpolished")) and text.strip():
        # Add only a visual separator; raw and clean evidence remain untouched.
        return text + (" " if text[-1:] in "。！？.!?；;，," else "。")
    return text


def _aggregate_paragraphs(segments, target_sec, max_chars=450, break_before=()):
    paragraphs, current, start = [], [], None

    def flush():
        paragraphs.append(
            {
                "start": current[0]["start"],
                "end": max(s["end"] for s in current),
                "seg_ids": [s["id"] for s in current],
                "text": "".join(_visible(s) for s in current),
                "suspect": any("polish_suspect" in s.get("flags", []) for s in current),
            }
        )

    for s in segments:
        if start is None:
            start = s["start"]
        # Keep merged IDs with the preceding visible text, even across a time boundary.
        if (
            current
            and s["clean"]
            and (
                s["id"] in break_before
                or s["start"] - start >= target_sec
                or sum(len(x["clean"]) for x in current) >= max_chars
            )
        ):
            flush()
            current, start = [], s["start"]
        current.append(s)
    if current:
        flush()
    return paragraphs


def text_similarity(a, b):
    # Require at least two shared English terms, or Chinese 3-grams.
    han_a = "".join(re.findall(r"[\u4e00-\u9fff]", a))
    han_b = "".join(re.findall(r"[\u4e00-\u9fff]", b))
    ga = {han_a[i : i + 3] for i in range(max(0, len(han_a) - 2))}
    gb = {han_b[i : i + 3] for i in range(max(0, len(han_b) - 2))}
    zh = 2 * len(ga & gb) / max(len(ga) + len(gb), 1)
    ea = set(re.findall(r"[a-z][a-z0-9_+-]{2,}", a.lower()))
    eb = set(re.findall(r"[a-z][a-z0-9_+-]{2,}", b.lower()))
    en = 2 * len(ea & eb) / max(len(ea) + len(eb), 1) if len(ea & eb) >= 2 else 0
    return max(zh, en)


def n_body(slides, cfg):
    budget = cfg.slides.body_per_10min * slides["duration"] / 600
    return max(math.ceil(budget) if slides.get("mode") == "representative" else int(budget), 0)


def run_align(clean, slides, structure, cfg):
    anchors = {h["after_id"] for h in structure["headings"]}
    paras = _aggregate_paragraphs(clean["segments"], cfg.align.para_sec, cfg.align.para_chars, anchors)
    if not paras:
        raise ValueError("没有可排版的正文")
    starts = [p["start"] for p in paras]
    refs = {i for i, p in enumerate(paras) if REF_RE.search(p["text"])}
    items, last = [], 0
    for page in sorted(slides["pages"], key=lambda p: p["t_capture"]):
        t = page["t_capture"]
        baseline = max(0, min(bisect_right(starts, t) - 1, len(paras) - 1))
        choice, reason, score = baseline, "time", 0.0
        if cfg.align.text_align and page.get("ocr_text"):
            low = page.get("t_start", t) - cfg.align.ref_window_before
            high = t + cfg.align.ref_window_after
            candidates = [
                (text_similarity(page["ocr_text"], p["text"]), i)
                for i, p in enumerate(paras)
                if i >= last and p["end"] >= low and p["start"] <= high
            ]
            if candidates:
                score, idx = max(candidates, key=lambda item: (item[0], -abs(starts[item[1]] - t)))
                if score >= cfg.align.similarity_threshold:
                    choice, reason = idx, "ocr"
                else:
                    anchored = [
                        (s, i)
                        for s, i in candidates
                        if i in refs
                        and s >= cfg.align.similarity_threshold * 0.7
                        and page.get("t_start", t) <= paras[i]["end"]
                        and paras[i]["start"] <= t
                    ]
                    if anchored:
                        score, choice = max(anchored)
                        reason = "reference+ocr"
        choice = max(choice, last)
        last = choice
        items.append({"page": page, "para_index": choice, "alignment": reason, "score": round(score, 4)})
    eligible = sorted(
        [x for x in items if not x["page"].get("talking_head")],
        key=lambda x: (-x["page"].get("ocr_chars", 0), x["page"]["t_capture"]),
    )
    selected, bins = set(), {}
    for item in eligible:
        if len(selected) >= n_body(slides, cfg):
            break
        bin_id = int(item["page"]["t_capture"] // 600)
        if bins.get(bin_id, 0) < cfg.slides.body_per_10min:
            selected.add(item["page"]["file"])
            bins[bin_id] = bins.get(bin_id, 0) + 1
    return {
        "paragraphs": paras,
        "ref_paras": sorted(refs),
        "structure": structure,
        "body": [x for x in items if x["page"]["file"] in selected],
        "appendix": [x for x in items if x["page"]["file"] not in selected],
    }
