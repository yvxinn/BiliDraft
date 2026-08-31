"""Audit public source and archives without reading or printing local credentials.

This bounded check complements manual review and history/secret scanning; it is
not a proof that arbitrary secrets or copyrighted material cannot be present.
"""

import argparse
import json
import re
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
TOP_LEVEL = {
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "README.md",
    "SKILL.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "MANIFEST.in",
    "pyproject.toml",
}
EXACT = {
    "config/config.example.yaml",
    "agents/openai.yaml",
    ".github/workflows/ci.yml",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
}
SUFFIXES = {
    "bilidraft": {".py"},
    "tests": {".py"},
    "scripts": {".py", ".md"},
    "docs": {".md"},
    "references": {".md"},
}
METADATA = {
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "entry_points.txt",
    "requires.txt",
    "top_level.txt",
    "METADATA",
    "WHEEL",
    "RECORD",
    "LICENSE",
}
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "token-format": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{32,})\b"
    ),
    "user-path": re.compile(
        r"(?:[A-Za-z]:[\\/](?:Users|Documents)[\\/][A-Za-z0-9_.-]+|/(?:Users|home)/[A-Za-z0-9_.-]+/)"
    ),
}
MAX_FILE_BYTES = 2 * 1024 * 1024


def allowed(name, archive=False):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if name in TOP_LEVEL or name in EXACT:
        return True
    if len(path.parts) == 2 and path.parts[0] in SUFFIXES:
        return path.suffix in SUFFIXES[path.parts[0]] and not path.name.startswith(".")
    if archive:
        if name in {"PKG-INFO", "setup.cfg"}:
            return True
        if path.parts[0].endswith((".egg-info", ".dist-info")):
            return path.name in METADATA and len(path.parts) in (2, 3)
    return False


def inspect(name, data, errors, archive=False):
    if not allowed(name, archive):
        errors.append({"path": name, "reason": "not in public file allowlist"})
    if len(data) > MAX_FILE_BYTES:
        errors.append({"path": name, "reason": "unexpected large file"})
        return
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        errors.append({"path": name, "reason": "unexpected binary/non-UTF8 file"})
        return
    for label, pattern in PATTERNS.items():
        if pattern.search(content):
            errors.append({"path": name, "reason": label})


def source_files():
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.as_posix()}",
            "-c",
            "core.excludesFile=",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return sorted({n for n in result.stdout.decode("utf-8").split("\0") if n and (ROOT / n).exists()})


def audit_archive(path, errors):
    count = 0
    names = set()
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                count += 1
                original = PurePosixPath(member.filename)
                if (
                    member.file_size > MAX_FILE_BYTES
                    or original.is_absolute()
                    or ".." in original.parts
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    errors.append({"path": member.filename, "reason": "unsafe archive member"})
                    continue
                # Wheels use paths directly; a skill zip has one enclosing directory.
                name = member.filename if path.suffix == ".whl" else member.filename.partition("/")[2]
                names.add(name)
                inspect(name, archive.read(member), errors, archive=True)
    else:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                if member.isdir():
                    continue
                count += 1
                original = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or member.size > MAX_FILE_BYTES
                    or original.is_absolute()
                    or ".." in original.parts
                ):
                    errors.append({"path": member.name, "reason": "unexpected archive member type/size"})
                    continue
                name = member.name.partition("/")[2]
                names.add(name)
                with archive.extractfile(member) as stream:
                    inspect(name, stream.read(), errors, archive=True)
    required = {"bilidraft/__init__.py", "bilidraft/cli.py"}
    if path.suffix != ".whl":
        required |= {
            "SKILL.md",
            "scripts/run.py",
            "scripts/setup.py",
            "references/runtime.md",
            "agents/openai.yaml",
        }
    for missing in sorted(required - names):
        errors.append({"path": missing, "reason": "required release file missing"})
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, help="Also inspect wheels, source tarballs and skill zips")
    args = parser.parse_args()
    errors = []
    names = source_files()
    for name in names:
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            errors.append({"path": name, "reason": "symlink/outside source root"})
            continue
        inspect(name, path.read_bytes(), errors)
    artifacts = {}
    if args.artifacts:
        for path in sorted(args.artifacts.iterdir()):
            if path.name.endswith((".whl", ".tar.gz", ".zip")):
                before = len(errors)
                artifacts[path.name] = audit_archive(path, errors)
                for error in errors[before:]:
                    error["artifact"] = path.name
        if not artifacts:
            errors.append({"path": str(args.artifacts), "reason": "no release archives found"})
    print(
        json.dumps(
            {
                "ok": not errors,
                "source_files": len(names),
                "artifacts": artifacts,
                "issues": errors,
                "scope": "current files and supplied archives; excludes history",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return bool(errors)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Audit failed ({type(error).__name__}); inspect the repository or archive locally.")
        raise SystemExit(1) from None
