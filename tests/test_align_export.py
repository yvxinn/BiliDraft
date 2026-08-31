import pytest
from bilidraft import align, exporter


def cleaned(rows):
    return {
        "segments": [
            {"id": i, "start": i * 50, "end": i * 50 + 40, "raw": text, "clean": text, "flags": []}
            for i, text in enumerate(rows)
        ],
        "report": {"source": "test", "segments": len(rows), "retention": 1.0, "suspect_ratio": 0.0},
    }


def test_zero_image_budget_and_talking_heads_go_to_appendix(cfg):
    cfg.slides.body_per_10min = 0
    slides = {
        "duration": 600,
        "pages": [
            {"t_capture": 3, "file": "frames/a.jpg", "ocr_chars": 100},
            {"t_capture": 4, "file": "frames/b.jpg", "ocr_chars": 0, "talking_head": True},
        ],
    }
    layout = align.run_align(cleaned(["hello"]), slides, {"headings": []}, cfg)
    assert layout["body"] == [] and len(layout["appendix"]) == 2


def test_short_blog_clip_still_gets_a_body_illustration(cfg):
    slides = {
        "mode": "representative",
        "duration": 15,
        "pages": [{"t_capture": 3, "file": "frames/a.jpg", "ocr_chars": 0}],
    }
    result = align.run_align(cleaned(["a short complete explanation"]), slides, {"headings": []}, cfg)
    assert len(result["body"]) == 1
    assert result["body"][0]["alignment"] == "time"


def test_early_frame_is_not_attached_to_final_paragraph(cfg):
    c = cleaned(["first", "second", "third"])
    c["segments"][0]["start"] = 10
    slides = {"duration": 600, "pages": [{"t_capture": 0, "file": "frames/a.jpg", "ocr_chars": 5}]}
    result = align.run_align(c, slides, {"headings": []}, cfg)
    assert result["body"][0]["para_index"] == 0


def test_ocr_can_refine_time_but_keeps_order(cfg):
    slides = {
        "duration": 600,
        "pages": [
            {
                "t_capture": 55,
                "t_start": 0,
                "file": "frames/a.jpg",
                "ocr_chars": 100,
                "ocr_text": "attention encoder decoder",
            },
            {
                "t_capture": 60,
                "t_start": 0,
                "file": "frames/b.jpg",
                "ocr_chars": 99,
                "ocr_text": "unrelated text",
            },
        ],
    }
    result = align.run_align(
        cleaned(["attention encoder decoder", "something else"]), slides, {"headings": []}, cfg
    )
    assert result["body"][0]["alignment"] == "ocr"
    assert result["body"][0]["para_index"] == 0
    assert result["body"][1]["para_index"] >= result["body"][0]["para_index"]


def test_merged_ids_stay_with_their_visible_sentence():
    segments = [
        {"id": 0, "start": 0, "end": 20, "clean": "前后合并完整句", "flags": []},
        {"id": 1, "start": 20, "end": 100, "clean": "", "flags": []},
        {"id": 2, "start": 100, "end": 110, "clean": "下一句", "flags": []},
    ]
    paras = align._aggregate_paragraphs(segments, 45)
    assert paras[0]["seg_ids"] == [0, 1]
    assert paras[1]["seg_ids"] == [2]


def test_heading_inside_paragraph_and_fallback_warning(cfg, tmp_path):
    c = cleaned(["第一句", "第二句"])
    c["segments"][1].update(start=10, end=20, flags=["polish_suspect", "raw_fallback"])
    layout = align.run_align(
        c, {"duration": 600, "pages": []}, {"headings": [{"after_id": 1, "level": 2, "title": "标题"}]}, cfg
    )
    result = exporter.run_export(
        tmp_path,
        c,
        layout,
        {"pages": []},
        {"title": "<script>alert</script>", "owner": "tester", "bvid": "BV1234567890", "duration": 600},
        cfg,
    )
    text = result.read_text(encoding="utf-8")
    assert "## 标题" in text and "含未通过" in text and "<script>" not in text


def test_nearby_headings_split_paragraphs_without_dropping_titles(cfg, tmp_path):
    c = cleaned(["先讲原理。", "再讲例子。"])
    c["segments"][0].update(start=0, end=5)
    c["segments"][1].update(start=5, end=10)
    structure = {
        "headings": [
            {"after_id": 0, "level": 2, "title": "原理"},
            {"after_id": 1, "level": 2, "title": "例子"},
        ]
    }
    slides = {"duration": 10, "pages": []}
    layout = align.run_align(c, slides, structure, cfg)
    note = exporter.run_export(
        tmp_path,
        c,
        layout,
        slides,
        {"title": "test", "owner": "test", "bvid": "BV1234567890", "duration": 10},
        cfg,
    ).read_text(encoding="utf-8")
    assert len(layout["paragraphs"]) == 2
    assert note.index("## 原理") < note.index("先讲原理。") < note.index("## 例子") < note.index("再讲例子。")


def test_numeric_review_is_visible_even_without_raw_fallback(cfg, tmp_path):
    c = cleaned(["效率是100倍。", "继续讲解。"])
    c["report"]["chunks_detail"] = [{"ok": True, "first_id": 0, "last_id": 0, "num_warning": ["100", "100"]}]
    slides = {"duration": 100, "pages": []}
    layout = align.run_align(c, slides, {"headings": []}, cfg)
    note = exporter.run_export(
        tmp_path,
        c,
        layout,
        slides,
        {"title": "测试", "owner": "test", "bvid": "BV1234567890", "duration": 100},
        cfg,
    ).read_text(encoding="utf-8")
    assert "数字写法待核对**：1" in note
    assert "00:00–00:40 的润色块有数字写法变化：100" in note
    assert "含未通过润色校验的原文" not in note
    assert "效率是100倍。" in note and "继续讲解。" in note


@pytest.mark.parametrize("file", ["../private.jpg", "/etc/passwd", "frames/missing.jpg"])
def test_export_rejects_unsafe_or_missing_image(cfg, tmp_path, file):
    c = cleaned(["hello"])
    page = {"file": file, "t_capture": 0}
    layout = {
        "paragraphs": [{"start": 0, "end": 1, "text": "hi", "seg_ids": [0]}],
        "structure": {"headings": []},
        "body": [{"para_index": 0, "page": page}],
        "appendix": [],
    }
    with pytest.raises(ValueError):
        exporter.run_export(
            tmp_path,
            c,
            layout,
            {"pages": [page]},
            {"title": "test", "owner": "test", "bvid": "BV1234567890", "duration": 600},
            cfg,
        )
