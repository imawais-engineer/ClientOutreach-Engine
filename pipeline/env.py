"""Minimal .env loader (stdlib only, no python-dotenv).

Values already present in the real environment win, so `APIFY_TOKEN=... python -m
pipeline.run` still overrides the file.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env"


def load_env(path: str | Path | None = None) -> dict[str, str]:
    """Load KEY=VALUE lines from .env into os.environ; returns what was loaded."""
    target = Path(path) if path else DEFAULT_ENV
    loaded: dict[str, str] = {}
    if not target.exists():
        return loaded
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def require(name: str) -> str:
    """Return an env var, loading .env first, or raise a clear error."""
    value = os.environ.get(name) or load_env().get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to {DEFAULT_ENV} (see .env.example)."
        )
    return value
