"""Launch the bundled skill runtime without depending on the caller's cwd."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = str(candidate) if candidate.is_file() else sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    try:
        return subprocess.call([python, "-m", "bilidraft", *sys.argv[1:]], env=env)
    except KeyboardInterrupt:
        return 130
    except OSError:
        print("Cannot launch runtime. Run: python <skill-dir>/scripts/setup.py", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
