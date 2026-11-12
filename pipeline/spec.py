"""Config loader: the single read path for everything in config/.

The pipeline never hardcodes a rule that lives in config/. If a spec is missing
or malformed this module fails loudly instead of guessing.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
SOURCES_DIR = CONFIG_DIR / "sources"


class SpecError(RuntimeError):
    """Raised when config is missing, malformed or inconsistent."""


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SpecError(f"missing config file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{path}: expected a JSON object")
    return data


@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    return _load(ROOT / "config.json")


@lru_cache(maxsize=1)
def brain() -> dict[str, Any]:
    spec_path = config().get("brain", {}).get("spec", "config/brain.json")
    return _load(ROOT / spec_path)


@lru_cache(maxsize=1)
def ui() -> dict[str, Any]:
    spec_path = config().get("ui", {}).get("spec", "config/ui.json")
    return _load(ROOT / spec_path)


@lru_cache(maxsize=1)
def _source_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in sorted(SOURCES_DIR.glob("*.json")):
        data = _load(path)
        source_id = data.get("id")
        if not source_id:
            raise SpecError(f"{path}: missing 'id'")
        paths[str(source_id)] = path
    if not paths:
        raise SpecError(f"no source specs found in {SOURCES_DIR}")
    return paths


def source_ids() -> list[str]:
    return list(_source_paths())


@lru_cache(maxsize=None)
def source(source_id: str) -> dict[str, Any]:
    path = _source_paths().get(source_id)
    if path is None:
        raise SpecError(
            f"unknown source '{source_id}'; known: {', '.join(source_ids())}"
        )
    spec = _load(path)
    if "scoring" not in spec or "dimensions" not in spec.get("scoring", {}):
        raise SpecError(f"{path}: missing scoring.dimensions")
    return spec


def dimensions(source_id: str) -> dict[str, dict[str, Any]]:
    return source(source_id)["scoring"]["dimensions"]


def active_sources() -> list[str]:
    return list(config().get("sources", {}).get("active", []))


def stage_order() -> list[str]:
    return list(config().get("sources", {}).get("stage_order", []))


def classification_bands() -> dict[str, dict[str, Any]]:
    return brain()["scoring"]["classification"]


def lifecycle() -> list[str]:
    return list(brain()["lifecycle"]["states"])


def quality() -> dict[str, Any]:
    return brain()["quality"]


def capability_terms() -> list[str]:
    """Flatten the capability list into searchable phrases for scoring."""
    terms: list[str] = []
    for capability in brain().get("capabilities", []):
        service = str(capability.get("service", "")).lower().strip()
        if service:
            terms.append(service)
    return terms
