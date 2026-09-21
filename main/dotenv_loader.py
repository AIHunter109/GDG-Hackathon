"""Minimal .env loader -- no external dependency, matching this project's
existing habit of hand-rolling small parsers instead of adding a library
for something this simple (see extractor.py's xlsx/docx readers).

Precedence: a real environment variable (set via $env:, setx, Docker/Cloud
Run env vars, etc.) always wins over a value in .env. .env only fills in
whatever isn't already set, so a terminal override still works exactly as
before without editing any file.
"""
import os
from pathlib import Path


def load_dotenv(path=None):
    """Read KEY=VALUE lines from `path` (default: repo root .env) into
    os.environ, skipping blank lines, comments, and keys already set.
    Silently does nothing if the file doesn't exist -- an absent .env is
    not an error, it just means "use real environment variables only"."""
    if path is None:
        path = Path(__file__).resolve().parent.parent / ".env"
    else:
        path = Path(path)

    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
