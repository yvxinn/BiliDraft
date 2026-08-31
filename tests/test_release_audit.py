"""Prevent private task data and unsafe paths from entering release archives."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "release_audit", Path(__file__).resolve().parents[1] / "scripts/audit_release.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize(
    "path",
    [
        "config/credential.json",
        "notes/course/note.md",
        ".claude/settings.json",
        "../README.md",
        "bilidraft/private.pyc",
    ],
)
def test_private_or_unsafe_files_are_rejected(path):
    errors = []
    audit.inspect(path, b"synthetic", errors)
    assert errors


def test_archive_detects_credentials_paths_and_incomplete_skill(tmp_path):
    artifact = tmp_path / "skill.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("bilidraft/config/credential.json", "synthetic")
        archive.writestr("../README.md", "unsafe")
    errors = []
    audit.audit_archive(artifact, errors)
    reasons = {e["reason"] for e in errors}
    assert "not in public file allowlist" in reasons
    assert "unsafe archive member" in reasons
    assert "required release file missing" in reasons


def test_scanner_reports_rule_without_echoing_secret_value():
    secret = "ghp_" + "A" * 36
    errors = []
    audit.inspect("README.md", secret.encode(), errors)
    assert errors == [{"path": "README.md", "reason": "token-format"}]
    assert secret not in str(errors)
