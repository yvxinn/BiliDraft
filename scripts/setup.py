"""Bootstrap the skill's isolated runtime; never write task data or credentials."""

import argparse
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only environment check")
    parser.add_argument("--asr", action="store_true", help="Install optional local speech recognition")
    parser.add_argument("--ocr", action="store_true", help="Install optional screenshot OCR")
    parser.add_argument("--offline", action="store_true", help="Do not contact the package index")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10+ is required")
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if args.check:
        if not python.is_file():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "reason": "skill virtual environment is missing",
                        "setup": str(Path(__file__).resolve()),
                    }
                )
            )
            return 1
        modules = [
            "typer",
            "requests",
            "yaml",
            "cv2",
            "yt_dlp",
            "numpy",
            "imagehash",
            "PIL",
            "filelock",
            "qrcode",
        ]
        modules += ["funasr", "torch", "torchaudio", "soundfile"] if args.asr else []
        modules += ["rapidocr_onnxruntime"] if args.ocr else []
        probe = "import importlib.util,json,sys; missing=[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]; ok=not missing and sys.version_info>=(3,10); print(json.dumps({'ok':ok,'python':sys.version.split()[0],'missing':missing})); sys.exit(not ok)"
        return subprocess.call([str(python), "-c", probe, *modules])
    if environment.exists() and not (environment / "pyvenv.cfg").is_file():
        parser.error("Existing .venv is not a recognized virtual environment; it will not be replaced")
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    extras = [name for name in ("asr", "ocr") if getattr(args, name)]
    package = str(ROOT) + ("[" + ",".join(extras) + "]" if extras else "")
    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    if args.offline:
        command += ["--no-index", "--no-build-isolation"]
    return subprocess.call([*command, package], cwd=ROOT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"Runtime setup failed ({type(error).__name__}). Check Python, permissions and package availability.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
