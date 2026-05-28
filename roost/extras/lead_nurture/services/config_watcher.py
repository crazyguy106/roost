"""Mtime-poll hot reloader for ``roost-config/``.

Watches ``roost-config/cadences/``, ``roost-config/question-packs/``,
and ``roost-config/settings.yaml`` every few seconds. On any file
mtime change, re-runs the relevant seeder so the operator's edits
take effect without restarting the container.

Why polling instead of inotify/watchdog? No new dependency, works the
same on every OS, and a <50-file dir takes microseconds to stat.

The watcher logs a single one-liner per reload so the operator can see
their changes landed (``docker logs roost --tail 10``).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from roost.config import PROJECT_ROOT

logger = logging.getLogger("roost.config_watcher")

ROOST_CONFIG_DIR = PROJECT_ROOT / "roost-config"
POLL_INTERVAL_SECONDS = 3.0

# Track the highest mtime we've seen, separately per concern. Bumping
# any of these triggers exactly the relevant reload.
_last_mtimes: dict[str, float] = {
    "cadences": 0.0,
    "question_packs": 0.0,
    "settings": 0.0,
}

_task: asyncio.Task | None = None


def _max_mtime(*paths: Path) -> float:
    """Highest mtime across the given paths (and files inside if a dir).
    Returns 0.0 if nothing exists."""
    best = 0.0
    for p in paths:
        if not p.exists():
            continue
        if p.is_dir():
            for f in p.glob("*.yaml"):
                try:
                    best = max(best, f.stat().st_mtime)
                except OSError:
                    continue
        else:
            try:
                best = max(best, p.stat().st_mtime)
            except OSError:
                continue
    return best


def _check_once() -> dict:
    """Run one poll cycle. Returns ``{key: reloaded_bool}`` for telemetry."""
    triggered = {}

    # ── Cadences ────────────────────────────────────────────────────
    cadence_mtime = _max_mtime(ROOST_CONFIG_DIR / "cadences")
    if cadence_mtime > _last_mtimes["cadences"]:
        _last_mtimes["cadences"] = cadence_mtime
        try:
            from roost.extras.lead_nurture.services.cadences import (
                seed_user_config,
            )
            result = seed_user_config()
            if result.get("seeded"):
                logger.info(
                    "config_watcher: re-seeded %d cadence(s) from roost-config/",
                    result["seeded"],
                )
            triggered["cadences"] = True
        except Exception:  # noqa: BLE001
            logger.exception("config_watcher: cadence reload failed")

    # ── Question packs ──────────────────────────────────────────────
    qp_mtime = _max_mtime(ROOST_CONFIG_DIR / "question-packs")
    if qp_mtime > _last_mtimes["question_packs"]:
        _last_mtimes["question_packs"] = qp_mtime
        try:
            from roost.extras.lead_nurture.services import question_packs
            stats = question_packs.reload()
            logger.info(
                "config_watcher: re-loaded question packs (%d total slugs)",
                stats["total_slugs"],
            )
            triggered["question_packs"] = True
        except Exception:  # noqa: BLE001
            logger.exception("config_watcher: question pack reload failed")

    # ── Settings ────────────────────────────────────────────────────
    settings_mtime = _max_mtime(ROOST_CONFIG_DIR / "settings.yaml")
    if settings_mtime > _last_mtimes["settings"]:
        _last_mtimes["settings"] = settings_mtime
        try:
            from roost.extras.lead_nurture.services import settings as svc_settings
            svc_settings.reload()
            logger.info("config_watcher: re-loaded settings.yaml")
            triggered["settings"] = True
        except ImportError:
            # settings module may not exist yet (built in phase E)
            pass
        except Exception:  # noqa: BLE001
            logger.exception("config_watcher: settings reload failed")

    return triggered


async def _poll_loop() -> None:
    """Forever loop. Survives any single-poll exception."""
    logger.info(
        "config_watcher started — polling %s every %.1fs",
        ROOST_CONFIG_DIR, POLL_INTERVAL_SECONDS,
    )
    # First call: prime mtimes so we don't fire a reload on first tick.
    for key, dir_or_file in (
        ("cadences", ROOST_CONFIG_DIR / "cadences"),
        ("question_packs", ROOST_CONFIG_DIR / "question-packs"),
        ("settings", ROOST_CONFIG_DIR / "settings.yaml"),
    ):
        _last_mtimes[key] = _max_mtime(dir_or_file)

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            _check_once()
        except asyncio.CancelledError:
            logger.info("config_watcher cancelled")
            return
        except Exception:  # noqa: BLE001
            logger.exception("config_watcher tick failed (continuing)")


def start() -> None:
    """Spawn the watcher task on the current event loop. Idempotent."""
    global _task
    if _task is not None and not _task.done():
        return
    if not ROOST_CONFIG_DIR.exists():
        logger.info("config_watcher: %s missing — skipping", ROOST_CONFIG_DIR)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("config_watcher: no running loop — cannot start")
        return
    _task = loop.create_task(_poll_loop())


def stop() -> None:
    """Cancel the watcher (used in tests; production lifespan handles teardown)."""
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
