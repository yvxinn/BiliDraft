import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bilidraft.cli import app
from bilidraft.config import Config
from bilidraft.pipeline import run, task_paths
from bilidraft.storage import parse_video_input, read_json, write_json

BVID = "BV1234567890"


@pytest.mark.parametrize(
    "value",
    [
        BVID,
        f"https://www.bilibili.com/video/{BVID}/?p=1&spm_id_from=test",
        f"https://m.bilibili.com/video/{BVID}",
    ],
)
def test_video_inputs_normalize_without_network(value):
    assert parse_video_input(value) == BVID


@pytest.mark.parametrize(
    "value",
    [
        f"https://bilibili.com.evil.invalid/video/{BVID}",
        f"https://user:pass@bilibili.com/video/{BVID}",
        f"https://www.bilibili.com/video/{BVID}?p=2",
        f"https://www.bilibili.com/video/{BVID}?p=1&p=2",
        "https://b23.tv/example",
        "../private",
    ],
)
def test_unsupported_sources_are_not_silently_followed(value):
    with pytest.raises(ValueError):
        parse_video_input(value)


def test_dry_run_uses_explicit_workspace_without_files_or_key_leaks(tmp_path, monkeypatch):
    workspace = tmp_path / "work space"
    monkeypatch.setenv("BILIDRAFT_API_KEY", "PRIVATE_DRY_RUN_SECRET")
    monkeypatch.setenv("BILIDRAFT_MODEL", "chosen-model")
    result = CliRunner().invoke(app, ["--workspace", str(workspace), "run", BVID, "--dry-run"])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    assert plan["workspace"] == str(workspace.resolve())
    assert plan["model"] == "chosen-model"
    assert "PRIVATE_DRY_RUN_SECRET" not in result.output
    assert not workspace.exists()


def test_raw_pipeline_with_disabled_images_never_calls_model(tmp_path, transcript, monkeypatch):
    import requests

    cfg = Config(workspace=tmp_path, text_mode="raw")
    cfg.slides.mode = "none"
    note, task = task_paths(BVID, cfg)
    write_json(
        task / "meta.json",
        {"bvid": BVID, "cid": 1, "title": "Synthetic lecture", "owner": "test", "duration": 600},
    )
    write_json(task / "subtitle.json", transcript)
    (task / "video.mp4").write_bytes(b"known cached input; images disabled")
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        lambda *a, **kw: pytest.fail("raw cached mode made HTTP request"),
    )
    report = run(BVID, resume=True, skip_download=True, cfg=cfg, progress=lambda *a: None)
    assert report["quality"]["passed"] and report["text_mode"] == "raw"
    assert report["images"]["total"] == 0
    clean = read_json(task / "clean.json")
    assert [s["clean"] for s in clean["segments"]] == [s["text"] for s in transcript["segments"]]
    assert clean["report"]["model_calls"] == 0
    assert "原始字幕模式" in (note / "note.md").read_text(encoding="utf-8")
    before = (note / "note.md").stat().st_mtime_ns
    cfg.llm.model = "unused-model-change"
    run(BVID, resume=True, cfg=cfg, progress=lambda *a: None)
    assert (note / "note.md").stat().st_mtime_ns == before


def test_model_is_required_before_creating_work_or_downloading(tmp_path):
    cfg = Config(workspace=tmp_path)
    with pytest.raises(ValueError, match="llm.model"):
        run(BVID, cfg=cfg)
    assert not (tmp_path / "notes").exists()


def test_skill_runner_works_outside_install_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("BILIDRAFT_MODEL", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run.py"),
            "--workspace",
            str(tmp_path / "isolated"),
            "run",
            BVID,
            "--text-mode",
            "raw",
            "--dry-run",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["text_mode"] == "raw" and not data["sends_transcript_to_model"]
    assert not (tmp_path / "isolated").exists()


def test_init_config_respects_workspace(tmp_path):
    result = CliRunner().invoke(app, ["--workspace", str(tmp_path), "init-config"])
    assert result.exit_code == 0
    assert (tmp_path / "config/config.yaml").is_file()


def test_cli_version_without_subcommand():
    from bilidraft import __version__

    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_skill_contains_valid_metadata_and_local_references():
    import re
    import yaml

    root = Path(__file__).resolve().parents[1]
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(skill.split("---", 2)[1])
    assert meta["name"] == "bilidraft" and meta["description"]
    ui = yaml.safe_load((root / "agents/openai.yaml").read_text(encoding="utf-8"))["interface"]
    assert "$bilidraft" in ui["default_prompt"]
    for path in re.findall(r"\]\(([^)]+)\)", skill):
        assert (root / path).is_file(), path
