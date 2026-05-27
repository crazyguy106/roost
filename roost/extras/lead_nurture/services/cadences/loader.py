"""YAML <-> DB bridge for nurture cadences.

Each library YAML defines one cadence and (optionally) the response_templates
it references, so a cadence ships its message library in one place. Seeding
upserts the templates into `response_templates` and the cadence into
`nurture_cadences`. Idempotent — never overwrites a user-customised default.

Mirrors `roost.services.rpa_flows.loader`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from roost.config import PROJECT_ROOT
from roost.extras.lead_nurture.services.cadences import store
from roost.services import response_templates as templates_svc

logger = logging.getLogger("roost.cadences.loader")

LIBRARY_DIR = Path(__file__).parent / "library"
USER_DIR = PROJECT_ROOT / "data" / "cadences"

REQUIRED_TOP_KEYS = {"slug", "name", "steps"}
ALLOWED_CHANNELS = {"email", "whatsapp", "telegram"}


def _safe_resolve(path: Path | str, *, allowed_roots: list[Path]) -> Path:
    p = Path(path).expanduser().resolve()
    for root in allowed_roots:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    raise ValueError(
        f"path {p} is outside allowed roots: {[str(r) for r in allowed_roots]}"
    )


def validate_cadence(cfg: dict) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errs: list[str] = []
    if not isinstance(cfg, dict):
        return ["top-level must be a mapping"]
    missing = REQUIRED_TOP_KEYS - set(cfg)
    if missing:
        errs.append(f"missing required keys: {sorted(missing)}")
    steps = cfg.get("steps")
    if not isinstance(steps, list) or not steps:
        errs.append("steps must be a non-empty list")
        return errs
    template_names = {t.get("name") for t in (cfg.get("templates") or []) if isinstance(t, dict)}
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errs.append(f"steps[{i}]: must be a mapping")
            continue
        if "template" not in step:
            errs.append(f"steps[{i}]: missing 'template'")
        if "day_offset" not in step:
            errs.append(f"steps[{i}]: missing 'day_offset'")
        ch = step.get("channel", "email")
        if ch not in ALLOWED_CHANNELS:
            errs.append(f"steps[{i}]: channel '{ch}' not in {sorted(ALLOWED_CHANNELS)}")
        # If templates are inlined, every step.template must resolve.
        if template_names and step.get("template") not in template_names:
            errs.append(
                f"steps[{i}]: template '{step.get('template')}' not defined "
                f"in this cadence's templates block"
            )
    return errs


def assert_valid(cfg: dict) -> None:
    errs = validate_cadence(cfg)
    if errs:
        raise ValueError("invalid cadence: " + "; ".join(errs))


def load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{p}: top-level must be a mapping")
    assert_valid(cfg)
    return cfg


def dump_yaml(cfg: dict, path: Path | str) -> Path:
    assert_valid(cfg)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    return p


def _seed_inline_templates(cfg: dict, *, user_id: str = "") -> int:
    """Upsert templates inlined under cfg['templates']. Returns count seeded."""
    count = 0
    for t in cfg.get("templates") or []:
        name = t.get("name")
        if not name:
            continue
        existing = templates_svc.get_template_by_name(name)
        if "error" in existing:
            templates_svc.create_template(
                name=name,
                body=t.get("body", ""),
                subject=t.get("subject", ""),
                category=t.get("category", "nurture"),
                channel=t.get("channel", "any"),
                intent_tags=t.get("intent_tags") or [],
                sequence_group=cfg["slug"],
                sequence_day=int(t.get("sequence_day", 0)),
                user_id=user_id,
            )
            count += 1
    return count


def import_to_db(path: Path | str, *, user_id: str = "", source: str = "user") -> dict:
    """Load a YAML file and write the cadence + inline templates to the DB."""
    safe = _safe_resolve(path, allowed_roots=[LIBRARY_DIR, USER_DIR])
    cfg = load_yaml(safe)
    seeded_templates = _seed_inline_templates(cfg, user_id=user_id)
    cadence = store.set_cadence(
        slug=cfg["slug"],
        name=cfg["name"],
        description=cfg.get("description", ""),
        vertical=cfg.get("vertical", "generic"),
        steps=cfg["steps"],
        enabled=bool(cfg.get("enabled", True)),
        source=source,
        user_id=user_id,
    )
    cadence["templates_seeded"] = seeded_templates
    return cadence


def export_from_db(slug: str, path: Path | str, *, user_id: str = "") -> Path:
    cadence = store.get_cadence_by_slug(slug, user_id=user_id)
    if cadence is None:
        raise ValueError(f"no cadence for slug '{slug}' (user_id={user_id!r})")
    out = {
        "slug": cadence["slug"],
        "name": cadence["name"],
        "description": cadence.get("description", ""),
        "vertical": cadence.get("vertical", "generic"),
        "enabled": bool(cadence.get("enabled", True)),
        "steps": cadence.get("steps", []),
    }
    safe = _safe_resolve(path, allowed_roots=[USER_DIR, LIBRARY_DIR])
    return dump_yaml(out, safe)


def list_library() -> list[dict]:
    out: list[dict] = []
    if not LIBRARY_DIR.exists():
        return out
    for p in sorted(LIBRARY_DIR.glob("*.yaml")):
        try:
            with p.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            errs = validate_cadence(cfg)
            out.append({
                "file": p.name,
                "slug": cfg.get("slug", ""),
                "name": cfg.get("name", ""),
                "vertical": cfg.get("vertical", "generic"),
                "enabled": bool(cfg.get("enabled", True)),
                "steps": len(cfg.get("steps") or []),
                "templates": len(cfg.get("templates") or []),
                "valid": not errs,
                "errors": errs,
            })
        except Exception as e:  # noqa: BLE001 — surface any parse error per file
            out.append({"file": p.name, "valid": False, "errors": [str(e)]})
    return out


def seed_library() -> dict:
    """On boot: import every library YAML as a global default — but only if
    no cadence currently exists for that slug at user_id="".

    Idempotent. Never overwrites a user-customised global default.
    """
    if not LIBRARY_DIR.exists():
        return {"seeded": 0, "skipped": 0, "errors": []}

    seeded = 0
    skipped = 0
    errors: list[str] = []
    for p in sorted(LIBRARY_DIR.glob("*.yaml")):
        try:
            cfg = load_yaml(p)
            slug = cfg["slug"]
            if store.get_cadence_by_slug(slug, user_id="") is not None:
                skipped += 1
                # Templates may still be missing if a previous boot failed
                # midway — try to backfill them too. _seed_inline_templates
                # is itself idempotent (skips any name that exists).
                _seed_inline_templates(cfg, user_id="")
                continue
            _seed_inline_templates(cfg, user_id="")
            store.set_cadence(
                slug=slug,
                name=cfg["name"],
                description=cfg.get("description", ""),
                vertical=cfg.get("vertical", "generic"),
                steps=cfg["steps"],
                enabled=bool(cfg.get("enabled", True)),
                source="library",
                user_id="",
            )
            seeded += 1
            logger.info("Seeded cadence library: %s", slug)
        except Exception as e:  # noqa: BLE001 — keep seeding remaining files
            errors.append(f"{p.name}: {e}")
            logger.exception("Failed seeding cadence %s", p.name)
    return {"seeded": seeded, "skipped": skipped, "errors": errors}
