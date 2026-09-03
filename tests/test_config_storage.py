import json
from pathlib import Path
import pytest
from bilidraft.config import Config, load_config
from bilidraft.defaults import EXAMPLE
from bilidraft.storage import atomic_text, read_json, task_lock, validate_bvid


@pytest.mark.parametrize(
    "text",
    [
        "unknown: 2",
        "llm: []",
        "llm:\n  max_workers: 0",
        "llm:\n  timeout: true",
        "llm:\n  reasoning_effort: endless",
        "align:\n  text_align: nope",
        "polish:\n  level: summary",
        "structure:\n  max_batch_chars: 999",
        "structure:\n  min_heading_sec: 200\n  target_heading_sec: 100",
        "quality:\n  min_retention: .nan",
        "llm:\n  base_url: https://user:secret@example.com/v1",
        "output_dir: ''",
        "- list",
        "llm: [invalid",
        "slides:\n  gap_force: 0",
        "slides:\n  mode: fastish",
        "slides:\n  max_workers: 9",
        "slides:\n  max_frames: 0",
        "slides:\n  representative_per_10min: 0",
        "slides:\n  representative_ocr: yesplease",
    ],
)
def test_bad_config_is_not_silently_ignored(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path, workspace=tmp_path)


def test_config_paths_and_env(tmp_path, monkeypatch):
    p = tmp_path / "cfg.yaml"
    p.write_text("output_dir: my-notes\nllm:\n  api_key_env: TEST_KEY", encoding="utf-8")
    monkeypatch.setenv("TEST_KEY", "secret")
    c = load_config(p, tmp_path)
    assert c.path(c.output_dir) == tmp_path / "my-notes"
    assert c.llm.api_key == "secret"
    assert "secret" not in repr(c)


def test_optimized_text_defaults_match_packaged_example():
    cfg = Config()
    assert (cfg.polish.chunk_chars, cfg.polish.split_retries) == (2400, 2)
    assert cfg.structure.max_batch_chars == 100000
    example = Path("config/config.example.yaml").read_text(encoding="utf-8")
    assert EXAMPLE == example


@pytest.mark.parametrize(
    "value", ["../secret", "BV1234567890/..", "https://x", "BVabc", "", r"BV1234567890\.."]
)
def test_reject_path_as_bvid(value):
    with pytest.raises(ValueError):
        validate_bvid(value)


def test_atomic_replace_does_not_leave_temp(tmp_path):
    p = tmp_path / "nested/result.json"
    atomic_text(p, json.dumps({"中文": "测试"}))
    assert read_json(p) == {"中文": "测试"}
    atomic_text(p, '{"new":true}')
    assert read_json(p) == {"new": True}
    assert not list(p.parent.glob("*.tmp"))


def test_task_lock_prevents_concurrent_writer(tmp_path):
    with task_lock(tmp_path):
        with pytest.raises(RuntimeError, match="已有任务"):
            with task_lock(tmp_path):
                pass
    with task_lock(tmp_path):
        assert Path(tmp_path).exists()
