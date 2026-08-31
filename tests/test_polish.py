import pytest
from bilidraft import polish


@pytest.mark.parametrize(
    "groups",
    [
        [{"ids": [0, 0], "text": "重复"}],
        [{"ids": [99], "text": "未知"}],
        [{"ids": [1, 0], "text": "乱序"}],
        [{"ids": [0], "text": ""}],
        [{"ids": [True], "text": "bool"}],
        [{"ids": [0], "text": 10}],
        [{"ids": [0], "text": "a"}, {"ids": [0], "text": "b"}],
    ],
)
def test_invalid_groups_cannot_overwrite_information(transcript, groups):
    with pytest.raises(ValueError):
        polish._parse_groups(transcript["segments"], groups)


@pytest.mark.parametrize("text", ["不能删除", "不是", "3元", "答案是零", "需停止"])
def test_short_semantic_sentences_are_not_fillers(text):
    chunk = [{"id": 0, "text": text}]
    assert polish._acceptable_misses(chunk, [0], {}) == ([], [0])


def test_fillers_can_be_explicitly_absorbed():
    assert polish._acceptable_misses([{"id": 0, "text": "嗯，呃"}], [0], {}) == ([0], [])


def test_numeric_matching_does_not_accept_substrings(cfg):
    _, lost, _, _ = polish._validate([{"id": 0, "text": "12 12"}], [([0], "112 12")], cfg.polish)
    assert lost == ["12"]


def test_numeric_check_preserves_subtitle_boundaries_and_counts(cfg):
    chunk = [{"id": 0, "text": "235"}, {"id": 1, "text": "235"}]
    assert polish._validate(chunk, [([0, 1], "235、235。")], cfg.polish)[1] == []
    assert polish._validate(chunk, [([0, 1], "235。")], cfg.polish)[1] == ["235"]


@pytest.mark.parametrize(
    "raw,edited,missing", [("1篇", "一篇", ["1"]), ("100倍", "一百倍", ["100"]), ("3.5", "三点五", ["3.5"])]
)
def test_equal_chinese_number_spelling_does_not_trigger_hard_failure(raw, edited, missing):
    assert polish._unsafe_numbers([{"text": raw}], [([0], edited)], missing) == []


def test_number_in_model_name_cannot_disappear_even_if_selfcheck_would_approve(cfg, monkeypatch):
    import json

    chunk = [{"id": 0, "start": 0, "end": 1, "text": "理解专家基于chain3VL构建。"}]
    calls = []

    def reply(*args):
        calls.append(1)
        return json.dumps({"lines": [{"ids": [0], "text": "理解专家基于Chameleon VL构建。"}]})

    monkeypatch.setattr(polish, "_chat", reply)
    result = polish._process(cfg.llm, chunk, "", cfg.polish, "")[0]
    assert result["report"]["unsafe_numbers"] == ["3"]
    assert result["segments"][0]["clean"] == chunk[0]["text"]
    assert "raw_fallback" in result["segments"][0]["flags"]
    assert len(calls) == 1  # The unsafe candidate never reaches semantic self-check.


def test_unpunctuated_groups_are_retried_before_export(cfg, monkeypatch):
    import json

    cfg.llm.retries = 1
    chunk = [{"id": 0, "text": "从互联网到移动互联网"}, {"id": 1, "text": "生活方式发生了改变"}]
    replies = iter(
        [
            {"lines": [{"ids": [s["id"]], "text": s["text"]} for s in chunk]},
            {
                "lines": [
                    {"ids": [0], "text": chunk[0]["text"] + "，"},
                    {"ids": [1], "text": chunk[1]["text"] + "。"},
                ]
            },
            {"dropped": []},
        ]
    )
    monkeypatch.setattr(polish, "_chat", lambda *a: json.dumps(next(replies), ensure_ascii=False))
    mapped, report = polish.polish_chunk(cfg.llm, chunk, "", cfg.polish)
    assert report["ok"] and report["attempts"] == 2
    assert "".join(mapped.values()) == "从互联网到移动互联网，生活方式发生了改变。"


@pytest.mark.parametrize("text", ["他说：“保留原文。”", 'He said, "Keep this!"', "下一步，", "例如："])
def test_clause_punctuation_with_closing_quotes_is_valid(text):
    assert polish._parse_groups([{"id": 0, "text": text}], [{"ids": [0], "text": text}]) == [([0], text)]


def test_failed_gate_never_exports_model_text(cfg, transcript, monkeypatch):
    monkeypatch.setattr(polish, "polish_chunk", lambda *a: ({0: "删减内容", 1: ""}, {"ok": False}))
    leaves = polish._process(cfg.llm, transcript["segments"], "", cfg.polish, "")
    assert [s["clean"] for s in leaves[0]["segments"]] == [s["text"] for s in transcript["segments"]]
    assert all("raw_fallback" in s["flags"] for s in leaves[0]["segments"])


def test_missing_selfcheck_field_is_not_success(cfg, monkeypatch):
    monkeypatch.setattr(polish, "_chat", lambda *a: "{}")
    with pytest.raises(ValueError):
        polish._verify_dropped(cfg.llm, "a", "b")


def test_successful_merge_is_not_duplicated(cfg, transcript, monkeypatch):
    raw = "".join(s["text"] for s in transcript["segments"])
    replies = iter(
        ['{"lines":[{"ids":[0,1],"text":' + __import__("json").dumps(raw) + "}]}", '{"dropped":[]}']
    )
    monkeypatch.setattr(polish, "_chat", lambda *a: next(replies))
    result = polish.run_polish(transcript, {}, cfg, progress=lambda x: None)
    assert result["segments"][0]["clean"] == raw
    assert result["segments"][1]["clean"] == ""
    assert result["report"]["suspect_ratio"] == 0


def test_checkpoints_reuse_success_and_retry_failure(cfg, tmp_path, monkeypatch):
    cfg.polish.chunk_chars = 50
    tr = {
        "source": "test",
        "segments": [{"id": i, "start": i, "end": i + 1, "text": "完整句子" * 20} for i in range(2)],
    }
    calls = []

    def fake(llm, chunk, *args):
        calls.append(chunk[0]["id"])
        ok = chunk[0]["id"] == 0 or calls.count(1) > 1
        return [
            {
                "segments": [
                    {
                        "id": s["id"],
                        "raw": s["text"],
                        "clean": s["text"],
                        "start": s["start"],
                        "end": s["end"],
                        "flags": [] if ok else ["polish_suspect", "raw_fallback"],
                    }
                    for s in chunk
                ],
                "report": {"ok": ok},
            }
        ]

    monkeypatch.setattr(polish, "_process", fake)
    first = polish.run_polish(tr, {}, cfg, tmp_path, progress=lambda x: None)
    assert len(first["report"]["chunks_detail"]) == 2
    polish.run_polish(tr, {}, cfg, tmp_path, reuse=True, progress=lambda x: None)
    assert sorted(calls) == [0, 1]
    final = polish.run_polish(tr, {}, cfg, tmp_path, reuse=True, retry_failed=True, progress=lambda x: None)
    assert calls.count(0) == 1 and calls.count(1) == 2
    assert final["report"]["suspect_ratio"] == 0


def test_chunk_cache_rechecks_integrity_and_code_but_keeps_runtime_changes(
    cfg, transcript, tmp_path, monkeypatch
):
    from bilidraft.storage import read_json, write_json

    calls = []

    def reply(llm, prompt, payload):
        import json

        calls.append(prompt)
        if "lines" in json.loads(payload):
            return json.dumps(
                {"lines": [{"ids": [s["id"]], "text": s["text"]} for s in transcript["segments"]]}
            )
        return '{"dropped":[]}'

    monkeypatch.setattr(polish, "_chat", reply)
    polish.run_polish(transcript, {}, cfg, tmp_path, progress=lambda _: None)
    assert len(calls) == 2
    cfg.llm.timeout += 30
    cfg.llm.max_workers += 1
    polish.run_polish(transcript, {}, cfg, tmp_path, reuse=True, progress=lambda _: None)
    assert len(calls) == 2
    path = tmp_path / "polish_chunks" / "0000.json"
    corrupt = read_json(path)
    corrupt["results"][0]["segments"][0]["clean"] = "被修改的缓存"
    write_json(path, corrupt)
    result = polish.run_polish(transcript, {}, cfg, tmp_path, reuse=True, progress=lambda _: None)
    assert len(calls) == 4 and result["segments"][0]["clean"] == transcript["segments"][0]["text"]
    monkeypatch.setattr(polish, "file_signature", lambda _: {"sha256": "new implementation"})
    polish.run_polish(transcript, {}, cfg, tmp_path, reuse=True, progress=lambda _: None)
    assert len(calls) == 6


def test_interrupt_does_not_send_queued_chunks_or_selfcheck(cfg, monkeypatch):
    from threading import Event
    from concurrent.futures import CancelledError, ThreadPoolExecutor

    entered, release = Event(), Event()
    calls = []
    cfg.llm.max_workers = 1
    cfg.polish.chunk_chars = 50
    tr = {
        "source": "test",
        "segments": [{"id": i, "start": i, "end": i + 1, "text": "保留信息" * 20} for i in range(20)],
    }

    def request(*args):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return '{"lines":[{"ids":[0],"text":"保留信息"}]}'

    def interrupt(futures):
        assert entered.wait(5)
        raise KeyboardInterrupt()

    class ControlledExecutor(ThreadPoolExecutor):
        def shutdown(self, *args, **kwargs):
            release.set()
            return super().shutdown(*args, **kwargs)

    monkeypatch.setattr(polish, "_chat", request)
    monkeypatch.setattr(polish, "as_completed", interrupt)
    monkeypatch.setattr(polish, "ThreadPoolExecutor", ControlledExecutor)
    with pytest.raises(KeyboardInterrupt):
        polish.run_polish(tr, {}, cfg, progress=lambda _: None)
    assert len(calls) == 1
    cancelled = Event()
    cancelled.set()
    with pytest.raises(CancelledError):
        polish._process(cfg.llm, tr["segments"][:1], "", cfg.polish, "", cancelled=cancelled)
    assert len(calls) == 1
