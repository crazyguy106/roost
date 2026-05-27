"""YAML <-> DB bridge for RPA flow configs.

- Library files (shipped, reviewable in PRs) live under
  `roost/services/rpa_flows/library/`.
- User-authored files (gitignored, mountable) live under `data/rpa_flows/`.
- DB rows in `rpa_flow_configs` are the live mutable copy.

Boot seeding is idempotent and never clobbers an existing config — running
upgrades is safe even after a user has customised the global default.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from roost.config import PROJECT_ROOT
from roost.extras.rpa.services.rpa_flows import configs as configs_mod
from roost.extras.rpa.services.rpa_flows.schema import assert_valid, validate_flow

logger = logging.getLogger("roost.rpa.loader")

LIBRARY_DIR = Path(__file__).parent / "library"
USER_DIR = PROJECT_ROOT / "data" / "rpa_flows"


def _safe_resolve(path: Path | str, *, allowed_roots: list[Path]) -> Path:
    """Resolve `path` and reject anything outside the allowed roots."""
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


def load_yaml(path: Path | str) -> dict[str, Any]:
    """Read and validate a flow YAML file."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{p}: top-level must be a mapping")
    assert_valid(cfg)
    return cfg


def dump_yaml(cfg: dict, path: Path | str) -> Path:
    """Write a flow config to a YAML file (validating first)."""
    assert_valid(cfg)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    return p


def import_to_db(path: Path | str, *, user_id: str = "") -> dict:
    """Load a YAML file and write it to the DB, overwriting any existing row."""
    safe = _safe_resolve(path, allowed_roots=[LIBRARY_DIR, USER_DIR])
    cfg = load_yaml(safe)
    portal = cfg.get("portal") or cfg["portal_slug"]
    return configs_mod.set_config(
        portal,
        user_id=user_id,
        name=cfg.get("name", ""),
        login_url=cfg.get("login_url", ""),
        steps=cfg.get("steps") or [],
        otp_config=cfg.get("otp_config") or {},
        enabled=bool(cfg.get("enabled", True)),
    )


def export_from_db(portal: str, path: Path | str, *, user_id: str = "") -> Path:
    """Read a DB row and write it to a YAML file under the user dir."""
    cfg = configs_mod.get_config(portal, user_id=user_id)
    if cfg is None:
        raise ValueError(f"no flow config for portal '{portal}' (user_id={user_id!r})")
    safe = _safe_resolve(path, allowed_roots=[USER_DIR, LIBRARY_DIR])
    out = {
        "portal": cfg["portal_slug"],
        "name": cfg.get("name", ""),
        "login_url": cfg.get("login_url", ""),
        "enabled": bool(cfg.get("enabled", True)),
        "otp_config": cfg.get("otp_config") or {},
        "steps": cfg.get("steps") or [],
    }
    return dump_yaml(out, safe)


def list_library() -> list[dict]:
    """Inventory of shipped library files (no DB read)."""
    out: list[dict] = []
    for p in sorted(LIBRARY_DIR.glob("*.yaml")):
        try:
            with p.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            errs = validate_flow(cfg)
            out.append({
                "file": p.name,
                "portal": cfg.get("portal") or cfg.get("portal_slug") or "",
                "name": cfg.get("name", ""),
                "enabled": bool(cfg.get("enabled", True)),
                "valid": not errs,
                "errors": errs,
            })
        except Exception as e:
            out.append({"file": p.name, "valid": False, "errors": [str(e)]})
    return out


def seed_library() -> dict:
    """On boot: import every library YAML as a global default — but only if
    no config currently exists for that portal at user_id="".

    Idempotent. Never overwrites a user-customised global default.
    """
    if not LIBRARY_DIR.exists():
        return {"seeded": 0, "skipped": 0}

    seeded = 0
    skipped = 0
    errors: list[str] = []
    for p in sorted(LIBRARY_DIR.glob("*.yaml")):
        try:
            cfg = load_yaml(p)
            portal = cfg.get("portal") or cfg["portal_slug"]
            if configs_mod.get_config(portal, user_id="") is not None:
                skipped += 1
                continue
            configs_mod.set_config(
                portal,
                user_id="",
                name=cfg.get("name", ""),
                login_url=cfg.get("login_url", ""),
                steps=cfg.get("steps") or [],
                otp_config=cfg.get("otp_config") or {},
                enabled=bool(cfg.get("enabled", True)),
            )
            seeded += 1
            logger.info("Seeded RPA flow library: %s", portal)
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            logger.exception("Failed seeding RPA flow %s", p.name)
    return {"seeded": seeded, "skipped": skipped, "errors": errors}
