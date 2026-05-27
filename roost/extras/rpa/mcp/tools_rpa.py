"""MCP tools for RPA / browser-automation flows.

Two layers:
  1. Run management — start, list, cancel, answer prompts.
  2. Config management — define a portal's flow as a list of steps.

A flow is a list of step dicts. See `roost/services/rpa_flows/_interpreter.py`
for the supported step types (goto, fill, click, wait_for, get_otp,
download_one, download_each, upload_drive, log).
"""

from __future__ import annotations

import asyncio
import logging

from roost.mcp.server import mcp

logger = logging.getLogger("roost.extras.rpa.mcp.tools_rpa")


def _user_id() -> str:
    try:
        from roost.user_context import get_user_context
        ctx = get_user_context()
        return str(ctx.user_id)
    except Exception:
        return ""


# ── Run management ────────────────────────────────────────────────


@mcp.tool()
def rpa_run(portal: str, params: dict | None = None) -> dict:
    """Start an RPA flow against a configured portal.

    Returns immediately with the run id. The flow continues in the
    background; if it needs an OTP or password it will Telegram the user
    and pause until they reply. Poll status with `rpa_get_run`.

    Args:
        portal: portal slug (e.g. 'aia'). Must have a stored config.
        params: portal-specific runtime params, accessible inside steps
            via `$param:KEY` placeholders.
    """
    try:
        from roost.extras.rpa.services import rpa_flows, rpa_runs

        user_id = _user_id()
        cfg = rpa_flows.configs.get_config(portal, user_id=user_id) \
            or rpa_flows.configs.get_config(portal, user_id="")
        if cfg is None:
            return {
                "error": f"No flow config for '{portal}'. "
                         f"Use rpa_set_flow_config to create one.",
                "known": rpa_flows.list_known_portals(user_id=user_id),
            }

        run_id = rpa_runs.create_run(
            user_id=user_id, portal_slug=portal, state=params or {}
        )

        async def _go():
            try:
                await rpa_flows.dispatch(portal, run_id, user_id, params or {})
            except Exception as e:
                logger.exception("RPA flow %s crashed", portal)
                rpa_runs.fail(run_id, str(e))

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_go())
        except RuntimeError:
            import threading
            threading.Thread(target=lambda: asyncio.run(_go()), daemon=True).start()

        return {"ok": True, "run_id": run_id, "portal": portal, "status": "running"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_list_runs(status: str = "", limit: int = 20) -> dict:
    """List recent RPA runs for the current user."""
    try:
        from roost.extras.rpa.services import rpa_runs
        return {
            "runs": rpa_runs.list_runs(
                user_id=_user_id(), status=status or None, limit=limit
            )
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_get_run(run_id: int) -> dict:
    """Get the current state of an RPA run, including any pending prompt."""
    try:
        from roost.extras.rpa.services import rpa_runs
        run = rpa_runs.get_run(run_id)
        return run or {"error": f"run {run_id} not found"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_cancel(run_id: int) -> dict:
    """Cancel a running or awaiting_input RPA run."""
    try:
        from roost.extras.rpa.services import rpa_runs
        ok = rpa_runs.cancel(run_id)
        return {"ok": ok, "run_id": run_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_submit_input(run_id: int, value: str) -> dict:
    """Provide the answer for an awaiting_input RPA run (e.g. an OTP)."""
    try:
        from roost.extras.rpa.services import rpa_runs
        ok = rpa_runs.submit_input(run_id, value)
        return {"ok": ok, "run_id": run_id}
    except Exception as e:
        return {"error": str(e)}


# ── Credential / password helpers (encrypted at rest) ─────────────


@mcp.tool()
def rpa_set_credential(portal: str, field: str, value: str) -> dict:
    """Store a per-portal credential. Reference inside step `value` as
    `$cred:<portal>_<field>`.

    Args:
        portal: e.g. 'aia', 'great_eastern'.
        field: free-form (e.g. 'user', 'password').
        value: secret value.
    """
    try:
        from roost.services.credentials import store_credential
        uid = _user_id()
        uid_int = int(uid) if uid.isdigit() else 1
        store_credential(f"{portal}_{field}", value, user_id=uid_int)
        return {"ok": True, "portal": portal, "field": field}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_set_zip_password(portal: str, policy_number: str, password: str) -> dict:
    """Store a zip password keyed by `(portal, policy_number)`.

    Reference inside `download_each` via
    `password_cred_pattern="zip_password_<portal>_{policy}"`.
    """
    try:
        from roost.services.credentials import store_credential
        uid = _user_id()
        uid_int = int(uid) if uid.isdigit() else 1
        store_credential(
            f"zip_password_{portal}_{policy_number}", password, user_id=uid_int
        )
        return {"ok": True, "portal": portal, "policy_number": policy_number}
    except Exception as e:
        return {"error": str(e)}


# ── Flow config CRUD ──────────────────────────────────────────────


@mcp.tool()
def rpa_set_flow_config(
    portal: str,
    steps: list[dict],
    name: str = "",
    login_url: str = "",
    otp_config: dict | None = None,
    enabled: bool = True,
    global_default: bool = False,
) -> dict:
    """Create or update a flow config for a portal.

    Args:
        portal: portal slug (e.g. 'aia').
        steps: ordered list of step dicts. See docs/rpa.md for step types.
        name: human-readable name.
        login_url: convenience field, often referenced in the first `goto` step.
        otp_config: default OTP settings (override-able per get_otp step).
        enabled: set False to disable without deleting.
        global_default: store under user_id="" (visible to all users) instead
            of the current user. Use only on the dev VPS for shared portals.
    """
    try:
        from roost.extras.rpa.services.rpa_flows import configs
        uid = "" if global_default else _user_id()
        return configs.set_config(
            portal,
            user_id=uid,
            name=name,
            login_url=login_url,
            steps=steps,
            otp_config=otp_config or {},
            enabled=enabled,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_get_flow_config(portal: str, global_default: bool = False) -> dict:
    """Return the stored flow config for a portal."""
    try:
        from roost.extras.rpa.services.rpa_flows import configs
        uid = "" if global_default else _user_id()
        cfg = configs.get_config(portal, user_id=uid)
        return cfg or {"error": f"No config for {portal}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_list_flow_configs(include_global: bool = True) -> dict:
    """List all flow configs visible to the current user."""
    try:
        from roost.extras.rpa.services.rpa_flows import configs
        uid = _user_id()
        own = configs.list_configs(user_id=uid)
        if include_global and uid != "":
            own_slugs = {c["portal_slug"] for c in own}
            globals_ = [
                c for c in configs.list_configs(user_id="")
                if c["portal_slug"] not in own_slugs
            ]
            own += globals_
        return {"configs": own}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_delete_flow_config(portal: str, global_default: bool = False) -> dict:
    """Delete a stored flow config."""
    try:
        from roost.extras.rpa.services.rpa_flows import configs
        uid = "" if global_default else _user_id()
        ok = configs.delete_config(portal, user_id=uid)
        return {"ok": ok, "portal": portal}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_test_step(portal: str, step_index: int, params: dict | None = None) -> dict:
    """Run a single step of a stored flow against the live portal — useful
    for debugging selectors. The browser context is fresh per call (no login
    state carry-over) so this is best for `goto`, `wait_for`, `click`-style
    smoke tests on logged-in pages where storage state already covers auth.
    """
    try:
        from roost.extras.rpa.services import rpa_flows, rpa_runs
        user_id = _user_id()
        cfg = rpa_flows.configs.get_config(portal, user_id=user_id) \
            or rpa_flows.configs.get_config(portal, user_id="")
        if cfg is None:
            return {"error": f"No config for {portal}"}

        run_id = rpa_runs.create_run(
            user_id=user_id, portal_slug=portal, state={"test_step": step_index}
        )

        async def _go():
            return await rpa_flows.run_single_step(
                cfg, step_index, run_id, user_id, params or {}
            )

        result = asyncio.run(_go())
        rpa_runs.cancel(run_id)
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_list_flows() -> dict:
    """List the portals with a stored flow config (per-user + global)."""
    try:
        from roost.extras.rpa.services import rpa_flows
        return {"portals": rpa_flows.list_known_portals(user_id=_user_id())}
    except Exception as e:
        return {"error": str(e)}


# ── AI-assisted authoring ─────────────────────────────────────────


@mcp.tool()
def rpa_inspect_page(url: str, wait_for: str = "", timeout_ms: int = 30000) -> dict:
    """Load a URL via the chromium sidecar and return a structured summary
    of interactive elements (form inputs with their labels/ids/names,
    buttons, links, tables) plus stable selectors.

    Use this to draft a flow YAML for a non-technical user: ask for the
    portal URL, call `rpa_inspect_page`, then propose a flow built from
    the returned `selector` fields. After the user logs in once via the
    Roost browser, storage state is persisted, so post-login pages can
    also be inspected.

    Args:
        url: page to load.
        wait_for: optional selector to wait for after navigation.
        timeout_ms: navigation/wait timeout (default 30s).

    Returns:
        {title, url, inputs[], buttons[], links[], tables[], has_otp_field}
    """
    try:
        import asyncio
        from roost.extras.rpa.services.browser_service import inspect_page
        return asyncio.run(inspect_page(
            url,
            user_id=_user_id() or "",
            wait_for=wait_for or None,
            timeout_ms=timeout_ms,
        ))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Library / file import-export ──────────────────────────────────


@mcp.tool()
def rpa_list_library() -> dict:
    """Inventory the shipped flow YAML files in the library directory.

    Library files are templates committed to the repo. They are seeded as
    global-default DB configs on boot (only when no config already exists
    for the portal).
    """
    try:
        from roost.extras.rpa.services import rpa_flows
        return {"library": rpa_flows.list_library()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rpa_validate_flow(path: str) -> dict:
    """Parse a YAML flow file and return validation errors (empty = valid).

    Useful in CI / pre-commit to catch malformed contributions before they
    are merged.
    """
    try:
        from roost.extras.rpa.services.rpa_flows import loader, schema
        cfg = loader.load_yaml(path)
        return {"ok": True, "portal": cfg.get("portal") or cfg.get("portal_slug"),
                "errors": schema.validate_flow(cfg)}
    except schema.FlowValidationError as e:
        return {"ok": False, "errors": [str(e)]}
    except Exception as e:
        return {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}


@mcp.tool()
def rpa_import_flow(path: str, global_default: bool = False) -> dict:
    """Read a YAML flow file and write it to the DB, overwriting any existing
    row for that portal.

    Args:
        path: Absolute path under the library or `data/rpa_flows/` dir.
        global_default: If True, store under user_id="" (visible to all).
            Otherwise store under the current user.
    """
    try:
        from roost.extras.rpa.services import rpa_flows
        uid = "" if global_default else _user_id()
        result = rpa_flows.import_to_db(path, user_id=uid)
        return {"ok": True, "config": result}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def rpa_export_flow(portal: str, path: str, global_default: bool = False) -> dict:
    """Write the DB flow config for `portal` to a YAML file.

    Args:
        portal: portal slug.
        path: Absolute path under `data/rpa_flows/` or the library dir.
        global_default: If True, read the global config; otherwise the
            current user's config.
    """
    try:
        from roost.extras.rpa.services import rpa_flows
        uid = "" if global_default else _user_id()
        out = rpa_flows.export_from_db(portal, path, user_id=uid)
        return {"ok": True, "path": str(out)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
