"""Question-pack loader.

Question packs are the 3-5 qualifying questions Roost asks a new lead
over the inbound channel right after they enrol in a cadence. Packs
were historically defined as a Python dict (``QUESTIONS_BY_CADENCE``)
in ``qualification.py``; this module loads them from YAML instead so an
operator can edit them without touching Python.

Lookup order (first match wins):

  1. ``roost-config/question-packs/<slug>.yaml`` — operator override
  2. ``roost/extras/lead_nurture/services/cadences/library/question-packs/<slug>.yaml`` — shipped default
  3. ``qualification.QUESTIONS_BY_CADENCE`` — last-resort in-code fallback
     (kept for safety so a missing YAML never makes a lead un-qualifiable)

``reload()`` rebuilds the cache from disk; safe to call from the mtime
poller. Validation is lenient — each question only needs ``key``,
``question``, and ``weight``; ``hot_keywords`` / ``warm_keywords``
default to empty lists.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from roost.config import PROJECT_ROOT

logger = logging.getLogger("roost.question_packs")

LIBRARY_DIR = (
    Path(__file__).parent / "cadences" / "library" / "question-packs"
)
USER_CONFIG_DIR = PROJECT_ROOT / "roost-config" / "question-packs"

_REQUIRED_KEYS = {"key", "question", "weight"}

# In-memory cache: {cadence_slug: [question_dict, ...]}.
# Protected by _lock for the rare case of a reload racing a lookup.
_CACHE: dict[str, list[dict[str, Any]]] = {}
_lock = threading.Lock()


def _load_pack(path: Path) -> tuple[str, list[dict]] | None:
    """Parse one YAML pack. Returns ``(slug, questions)`` or ``None`` on
    any failure — failures are logged, never raised."""
    try:
        with path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        logger.exception("question pack parse failed: %s", path)
        return None

    if not isinstance(cfg, dict):
        logger.warning("question pack %s: top-level must be a mapping", path)
        return None
    if not cfg.get("enabled", True):
        return None

    slug = cfg.get("cadence_slug") or ""
    questions = cfg.get("questions") or []
    if not slug or not isinstance(questions, list) or not questions:
        logger.warning(
            "question pack %s: missing cadence_slug or questions", path,
        )
        return None

    cleaned: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            logger.warning("%s: question[%d] not a mapping — skipping", path, i)
            continue
        missing = _REQUIRED_KEYS - set(q.keys())
        if missing:
            logger.warning(
                "%s: question[%d] missing keys %s — skipping",
                path, i, sorted(missing),
            )
            continue
        cleaned.append({
            "key": str(q["key"]),
            "question": str(q["question"]).rstrip(),
            "weight": float(q["weight"]),
            "hot_keywords": list(q.get("hot_keywords") or []),
            "warm_keywords": list(q.get("warm_keywords") or []),
        })

    if not cleaned:
        return None
    return slug, cleaned


def reload() -> dict:
    """Rebuild the cache from disk. Returns stats."""
    new_cache: dict[str, list[dict]] = {}
    library_count = 0
    override_count = 0

    if LIBRARY_DIR.exists():
        for p in sorted(LIBRARY_DIR.glob("*.yaml")):
            result = _load_pack(p)
            if result is None:
                continue
            slug, questions = result
            new_cache[slug] = questions
            library_count += 1

    # roost-config/ overrides win — processed second.
    if USER_CONFIG_DIR.exists():
        for p in sorted(USER_CONFIG_DIR.glob("*.yaml")):
            result = _load_pack(p)
            if result is None:
                continue
            slug, questions = result
            if slug in new_cache:
                logger.info("question pack override: %s (from roost-config/)", slug)
            new_cache[slug] = questions
            override_count += 1

    with _lock:
        _CACHE.clear()
        _CACHE.update(new_cache)

    return {
        "library": library_count,
        "overrides": override_count,
        "total_slugs": len(new_cache),
    }


def get_questions_for_cadence(slug: str) -> list[dict] | None:
    """Look up the question pack for a cadence. Returns ``None`` if no
    pack is defined for this cadence (qualification then no-ops, the
    cadence still runs)."""
    with _lock:
        pack = _CACHE.get(slug)
    if pack is not None:
        return pack
    # Last-resort fallback: the in-code dict still in qualification.py.
    # Import is lazy to avoid a circular import.
    try:
        from roost.extras.lead_nurture.services.qualification import (
            QUESTIONS_BY_CADENCE,
        )
        return QUESTIONS_BY_CADENCE.get(slug)
    except Exception:  # noqa: BLE001
        return None


# Build the cache once at import time so production callers don't pay
# the cold-start cost on the first lead. Idempotent — re-importing in
# tests is safe.
reload()
