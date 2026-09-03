import json

from bilidraft import structure


def _clean(count=6, chars=10):
    return {
        "segments": [
            {
                "id": index,
                "start": index * 60,
                "end": index * 60 + 50,
                "clean": f"第{index}段" + "内容" * chars,
            }
            for index in range(count)
        ]
    }


def test_normal_course_is_structured_in_one_global_request(cfg, monkeypatch):
    clean = _clean(count=12, chars=500)
    payloads = []

    def reply(llm, prompt, payload):
        value = json.loads(payload)
        payloads.append(value)
        return json.dumps(
            {
                "headings": [
                    {"after_id": 0, "level": 2, "title": "整体问题"},
                    {"after_id": 6, "level": 3, "title": "关键方法"},
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(structure, "chat", reply)
    result = structure.run_structure(clean, {"title": "课程"}, cfg)
    assert result["mode"] == "semantic_global" and result["model_calls"] == 1
    assert len(payloads) == 1 and len(payloads[0]["lines"]) == 12
    assert [heading["level"] for heading in result["headings"]] == [2, 3]


def test_very_long_course_carries_heading_context_between_batches(cfg, monkeypatch):
    cfg.structure.max_batch_chars = 1000
    cfg.structure.context_headings = 1
    clean = _clean(count=6, chars=300)
    payloads = []

    def reply(llm, prompt, payload):
        value = json.loads(payload)
        payloads.append(value)
        first_id = value["lines"][0]["id"]
        return json.dumps(
            {"headings": [{"after_id": first_id, "level": 3, "title": f"主题{first_id}"}]},
            ensure_ascii=False,
        )

    monkeypatch.setattr(structure, "chat", reply)
    result = structure.run_structure(clean, {}, cfg)
    assert result["mode"] == "semantic_batched" and result["model_calls"] > 1
    assert payloads[0]["batch"]["previous_headings"] == []
    assert payloads[1]["batch"]["previous_headings"] == [{"level": 2, "title": "主题0"}]
    assert result["headings"][0]["level"] == 2


def test_invalid_model_headings_use_time_fallback(cfg, monkeypatch):
    monkeypatch.setattr(structure, "chat", lambda *args: '{"headings":[]}')
    result = structure.run_structure(_clean(), {}, cfg)
    assert result["mode"] == "semantic_with_time_fallback"
    assert result["headings"][0]["mode"] == "time_fallback"
    assert result["fallback_reason"] == "batch 1: ValueError"


def test_raw_mode_never_calls_model(cfg, monkeypatch):
    cfg.text_mode = "raw"
    monkeypatch.setattr(structure, "chat", lambda *args: (_ for _ in ()).throw(AssertionError()))
    result = structure.run_structure(_clean(), {}, cfg)
    assert result["mode"] == "time" and result["model_calls"] == 0
