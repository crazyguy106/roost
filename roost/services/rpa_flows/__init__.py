"""RPA flows — data-driven, configured per (user, portal) in the DB.

A flow is a list of step dicts stored in the `rpa_flow_configs` table and
edited via the MCP tools (`rpa_set_flow_config` etc.). The interpreter at
`_interpreter.run_config` walks the steps using the existing primitives:
`browser_service` for Playwright, `otp_source` for OTP capture,
`archive_service` for password-protected zip extraction.

There is intentionally no per-portal Python module — adding a new portal
is a configuration change, not a code change.
"""

from __future__ import annotations

from roost.services.rpa_flows import configs
from roost.services.rpa_flows.loader import (
    LIBRARY_DIR,
    USER_DIR,
    dump_yaml,
    export_from_db,
    import_to_db,
    list_library,
    load_yaml,
    seed_library,
)
from roost.services.rpa_flows.schema import FlowValidationError, validate_flow


def __getattr__(name):
    """Lazy-load interpreter symbols so importing the package doesn't pull
    in playwright/pyzipper for callers that only need configs/loader."""
    if name in ("run_config", "run_single_step"):
        from roost.services.rpa_flows._interpreter import run_config, run_single_step
        return {"run_config": run_config, "run_single_step": run_single_step}[name]
    raise AttributeError(f"module 'roost.services.rpa_flows' has no attribute {name!r}")

__all__ = [
    "dispatch",
    "configs",
    "run_config",
    "run_single_step",
    "LIBRARY_DIR",
    "USER_DIR",
    "load_yaml",
    "dump_yaml",
    "import_to_db",
    "export_from_db",
    "list_library",
    "seed_library",
    "validate_flow",
    "FlowValidationError",
]


async def dispatch(portal: str, run_id: int, user_id: str, params: dict) -> dict:
    """Look up the stored config for `(portal, user_id)` and run it."""
    cfg = configs.get_config(portal, user_id=user_id)
    if cfg is None and user_id:
        # Fall back to a global config (user_id="") if no per-user override
        cfg = configs.get_config(portal, user_id="")
    if cfg is None:
        return {
            "error": f"No flow config for portal '{portal}'. "
                     f"Use rpa_set_flow_config to create one."
        }
    if not cfg.get("enabled", True):
        return {"error": f"Flow config for '{portal}' is disabled"}
    from roost.services.rpa_flows._interpreter import run_config as _run_config
    return await _run_config(cfg, run_id, user_id, params)


def list_known_portals(user_id: str = "") -> list[str]:
    """List portals that have a config (per-user, then global fallback)."""
    seen: set[str] = set()
    for cfg in configs.list_configs(user_id=user_id):
        seen.add(cfg["portal_slug"])
    if user_id:
        for cfg in configs.list_configs(user_id=""):
            seen.add(cfg["portal_slug"])
    return sorted(seen)
