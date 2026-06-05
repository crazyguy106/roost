"""Reader for ``roost-config/settings.yaml`` — operator-facing tunables.

The settings file is optional. Every value has a hard-coded default so
a missing file just means "run with defaults". A typed accessor per
setting keeps the call sites simple and the YAML structure free to
evolve.

Hot-reload: ``config_watcher.py`` calls ``reload()`` on mtime change.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from roost.config import PROJECT_ROOT

logger = logging.getLogger("roost.settings")

SETTINGS_FILE = PROJECT_ROOT / "roost-config" / "settings.yaml"

# Defaults. Mirror the keys we expose so the schema is documented in code.
_DEFAULTS: dict[str, Any] = {
    "fragmented_messages": {
        "enabled": True,
        "debounce_seconds": 6,
        "early_release_seconds": 2,
        "max_wait_seconds": 40,
        "terminal_punctuation": [".", "?", "!"],
    },
    "default_vertical": "financial_advisor",
    "debug": False,
}

_cache: dict[str, Any] = {}
_lock = threading.Lock()


def _deep_merge(defaults: dict, override: dict) -> dict:
    """Recursive merge: override wins, missing keys fall back to defaults."""
    out = dict(defaults)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def reload() -> dict:
    """Re-read settings.yaml. Returns the resolved (merged) dict."""
    if not SETTINGS_FILE.exists():
        with _lock:
            _cache.clear()
            _cache.update(_DEFAULTS)
        return dict(_DEFAULTS)
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        if not isinstance(user_cfg, dict):
            logger.warning("settings.yaml: top-level must be a mapping; ignoring")
            user_cfg = {}
    except Exception:  # noqa: BLE001
        logger.exception("settings.yaml parse failed — using defaults")
        user_cfg = {}

    merged = _deep_merge(_DEFAULTS, user_cfg)
    with _lock:
        _cache.clear()
        _cache.update(merged)
    return dict(merged)


def get(key: str, default: Any = None) -> Any:
    """Look up a top-level key from the merged settings."""
    with _lock:
        if not _cache:
            _cache.update(_DEFAULTS)
        return _cache.get(key, default)


def get_fragmented_messages() -> dict:
    """Typed accessor for the debouncer config. Always returns a dict."""
    v = get("fragmented_messages") or {}
    return v if isinstance(v, dict) else {}


# Prime the cache at import.
reload()
