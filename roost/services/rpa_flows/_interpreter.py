"""Generic step interpreter for data-driven RPA flows.

A flow config is a list of step dicts. The interpreter walks them in order,
maintaining a small `vars` dict for cross-step values (notably `$otp` after
a `get_otp` step and `$last_download` after a `download_one` step).

Step types
----------
goto           {url, wait_until?, timeout_ms?}
fill           {selector, value}        # value supports $cred:KEY, $param:KEY,
                                        #   $var:KEY, $otp, or a literal string
click          {selector, timeout_ms?}
wait_for       {selector, state?, timeout_ms?}
wait_ms        {ms}
press          {selector, key}
get_otp        {source: "email"|"telegram", query?, regex?, prompt?, timeout?}
                # stores the captured code as $otp
download_one   {trigger_selector, save_dir_param?, password_cred?, extract?}
                # saves to vars.last_download (Path); if extract=true and
                # password_cred is given, extracts and stores last_extracted
download_each  {item_selector, trigger_selector_within, save_dir_param?,
                policy_attr?, password_cred_pattern?, extract?, max?}
                # iterates each match of item_selector; for each, clicks the
                # trigger inside it, saves the download, and (optionally)
                # extracts using a per-item password resolved from
                # password_cred_pattern (e.g. "zip_password_aia_{policy}").
upload_drive   {remote_path, source: "last_download"|"last_extracted"}
screenshot     {path?, name?, selector?, full_page?}
                # captures the full page (or just an element if selector is
                # given) into today's run dir under screenshots/.
                # stores the file path as $var:last_screenshot.
whatsapp_send  {to, body?, document?, image?, caption?, filename?, source?}
                # send a WhatsApp message via the Meta Cloud API.
                #   - body            → text-only
                #   - document/image  → explicit local path or $var:last_*
                #   - source: "last_download" | "last_screenshot" | "last_extracted"
                #     resolves to the most recent artefact of that kind.
                # to/body/caption/filename support placeholders.
log            {message}                # info-log a message (debugging)

Value placeholders
------------------
$cred:KEY     →  credentials.get_credential(KEY)
$param:KEY    →  params.get(KEY) supplied at run time
$var:KEY      →  interpreter vars[KEY] (e.g. $var:otp, $var:policy_number)
$otp          →  shorthand for $var:otp
$state:KEY    →  rpa_runs.get_run(run_id)['state_json'].get(KEY)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from roost.config import UPLOADS_DIR
from roost.services import archive_service, browser_service, rpa_runs
from roost.services.credentials import get_credential
from roost.services.otp_source import EmailOtpSource, TelegramOtpSource

logger = logging.getLogger("roost.rpa.interpreter")


class StepError(Exception):
    pass


def _resolve_value(raw: Any, ctx: dict) -> str:
    """Resolve a placeholder string against the runtime context."""
    if not isinstance(raw, str):
        return raw
    if not raw.startswith("$"):
        return raw
    if raw == "$otp":
        return ctx["vars"].get("otp", "")
    if raw.startswith("$var:"):
        return ctx["vars"].get(raw[5:], "")
    if raw.startswith("$param:"):
        return ctx["params"].get(raw[7:], "")
    if raw.startswith("$cred:"):
        uid = ctx["uid_int"]
        return get_credential(raw[6:], user_id=uid) or ""
    if raw.startswith("$state:"):
        return ctx["params"].get(raw[7:], "")
    return raw


def _today_dir(user_id: str, portal: str, sub: str = "") -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    out = Path(UPLOADS_DIR) / "rpa" / portal / str(user_id) / today
    if sub:
        out = out / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


# ── Step handlers ──────────────────────────────────────────────────


async def _step_goto(page, step, ctx):
    url = _resolve_value(step["url"], ctx)
    await page.goto(
        url,
        wait_until=step.get("wait_until", "domcontentloaded"),
        timeout=step.get("timeout_ms", 60_000),
    )


async def _step_fill(page, step, ctx):
    await page.fill(step["selector"], _resolve_value(step["value"], ctx))


async def _step_click(page, step, ctx):
    await page.click(step["selector"], timeout=step.get("timeout_ms", 30_000))


async def _step_wait_for(page, step, ctx):
    await page.wait_for_selector(
        step["selector"],
        state=step.get("state", "visible"),
        timeout=step.get("timeout_ms", 30_000),
    )


async def _step_wait_ms(page, step, ctx):
    await asyncio.sleep(step["ms"] / 1000.0)


async def _step_press(page, step, ctx):
    await page.press(step["selector"], step["key"])


async def _step_select_option(page, step, ctx):
    """Pick a value from a native <select> element.

    `value` may be a string (matches by `value=` attribute) or a label —
    pass `by="label"` to match the visible option text instead.
    """
    selector = step["selector"]
    value = _resolve_value(step["value"], ctx)
    by = step.get("by", "value")
    if by == "label":
        await page.select_option(selector, label=value)
    else:
        await page.select_option(selector, value=value)


async def _step_get_otp(page, step, ctx):
    source = step.get("source", "telegram")
    if source == "email":
        src = EmailOtpSource(
            query=_resolve_value(step.get("query", ""), ctx),
            regex=step.get("regex", r"\b(\d{6})\b"),
            timeout=step.get("timeout", 120),
        )
    else:
        src = TelegramOtpSource(
            prompt=_resolve_value(step.get("prompt", "Enter the OTP"), ctx),
            timeout=step.get("timeout", 300),
        )
    code = await src.get(ctx["run_id"])
    ctx["vars"]["otp"] = code


async def _step_await_user_session(page, step, ctx):
    """Pause until the user completes a manual step (e.g. Singpass login).

    Sends a one-way Telegram message telling the user what to do, then
    polls the page until `selector` appears. The user interacts with the
    same Chromium session via the sidecar's noVNC / debug URL — when
    they finish (login, payment, captcha), the post-action selector
    materialises and the flow resumes. No user reply needed.

    Args:
        selector: CSS selector that signals "user is done".
        prompt: optional message to send via Telegram. Default is generic.
        timeout_ms: max wait, default 600_000 (10 minutes).
    """
    selector = step["selector"]
    timeout_ms = int(step.get("timeout_ms", 600_000))
    prompt = _resolve_value(
        step.get("prompt", "Please complete the required action in the browser. I'll resume automatically."),
        ctx,
    )
    live_url = None
    try:
        from roost.services.browser_service import live_debug_url
        live_url = await live_debug_url(page)
    except Exception:
        logger.warning("await_user_session: failed to resolve live debug URL", exc_info=True)

    # Persist live URL on the run row so the /rpa web viewer can render
    # a "Take over browser" button without re-querying CDP.
    try:
        from roost.services.rpa_runs import set_state_field
        set_state_field(ctx["run_id"], "live_url", live_url)
        set_state_field(ctx["run_id"], "manual_prompt", prompt)
    except Exception:
        logger.debug("await_user_session: failed to persist live_url", exc_info=True)

    try:
        from roost.services.rpa_runs import _notify_telegram
        await _notify_telegram(ctx["run_id"], prompt, "manual_intervention", live_url=live_url)
    except Exception:
        logger.warning("await_user_session: Telegram notify failed; continuing to poll", exc_info=True)
    await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
    logger.info("[run #%d] await_user_session: target selector appeared", ctx["run_id"])
    # Clear live_url once we've resumed — the URL will be stale.
    try:
        from roost.services.rpa_runs import set_state_field
        set_state_field(ctx["run_id"], "live_url", None)
    except Exception:
        pass


async def _step_download_one(page, step, ctx):
    save_dir = _today_dir(ctx["user_id"], ctx["portal"])
    trigger = step["trigger_selector"]

    async def click(pg):
        await pg.click(trigger)

    target = await browser_service.download_via_action(page, click, save_dir)
    ctx["vars"]["last_download"] = str(target)
    ctx["downloaded"].append(str(target))

    if step.get("extract") and step.get("password_cred"):
        password = get_credential(step["password_cred"], user_id=ctx["uid_int"])
        files = archive_service.extract_zip(target, password=password)
        ctx["vars"]["last_extracted"] = [str(f) for f in files]
        ctx["extracted"].extend(str(f) for f in files)


async def _step_download_each(page, step, ctx):
    item_selector = step["item_selector"]
    trigger_within = step["trigger_selector_within"]
    policy_attr = step.get("policy_attr", "data-policy")
    password_pattern = step.get("password_cred_pattern", "")
    max_items = step.get("max", 999)
    save_dir = _today_dir(ctx["user_id"], ctx["portal"])

    items = await page.locator(item_selector).all()
    for item in items[:max_items]:
        policy = await item.get_attribute(policy_attr) or ""
        ctx["vars"]["policy"] = policy

        async def click(pg, it=item):
            await it.locator(trigger_within).click()

        target = await browser_service.download_via_action(page, click, save_dir)
        ctx["vars"]["last_download"] = str(target)
        ctx["downloaded"].append(str(target))

        if step.get("extract"):
            cred_key = password_pattern.format(policy=policy) if password_pattern else ""
            password = (
                get_credential(cred_key, user_id=ctx["uid_int"]) if cred_key else None
            )
            if not password and step.get("prompt_if_missing", True):
                password = await rpa_runs.request_input(
                    ctx["run_id"],
                    f"Zip password for {policy}",
                    kind="text",
                )
                if cred_key and password:
                    from roost.services.credentials import store_credential
                    store_credential(cred_key, password, user_id=ctx["uid_int"])
            try:
                files = archive_service.extract_zip(target, password=password)
                ctx["extracted"].extend(str(f) for f in files)
            except archive_service.BadZipPassword:
                logger.warning("Bad password for %s, skipping extraction", policy)


async def _step_upload_drive(page, step, ctx):
    remote = _resolve_value(step["remote_path"], ctx)
    src_var = step.get("source", "last_download")
    paths = ctx["vars"].get(src_var)
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        return
    try:
        from roost.mcp.tools_drive import drive_upload
    except Exception:
        logger.warning("drive_upload unavailable; skipping")
        return
    for p in paths:
        try:
            drive_upload(p, remote)
        except Exception:
            logger.exception("Drive upload failed for %s", p)


async def _step_screenshot(page, step, ctx):
    """Capture a screenshot. Saves under today's run dir as `screenshots/`.

    Args:
        path:      explicit filename (resolved relative to run dir).
        selector:  if given, captures the bounding box of that element.
        full_page: if true, captures the full scrollable page (default false).
        name:      optional logical label, used as the filename stem when no
                   `path` is given. Default: "screenshot_<index>".
    """
    save_dir = _today_dir(ctx["user_id"], ctx["portal"], sub="screenshots")
    raw_path = step.get("path")
    if raw_path:
        path_str = _resolve_value(raw_path, ctx)
        target = Path(path_str)
        if not target.is_absolute():
            target = save_dir / target
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        stem = _resolve_value(step.get("name", ""), ctx) or f"screenshot_{ctx['vars'].get('_step_index', 0)}"
        target = save_dir / f"{stem}.png"

    selector = step.get("selector")
    full_page = bool(step.get("full_page", False))

    if selector:
        loc = page.locator(selector)
        await loc.screenshot(path=str(target))
    else:
        await page.screenshot(path=str(target), full_page=full_page)

    ctx["vars"]["last_screenshot"] = str(target)
    ctx["downloaded"].append(str(target))


async def _step_whatsapp_send(page, step, ctx):
    """Send a WhatsApp message via the Meta Cloud API.

    The op is fire-and-forget from the interpreter's perspective: a Meta API
    error is logged and recorded in `ctx.vars.last_whatsapp_error` but does
    not abort the flow. Successful sends record `last_whatsapp_message_id`.

    Resolution order for media:
        explicit `document` / `image` path  >  `source` shortcut  >  text-only.
    """
    from roost.services import whatsapp as wa

    to = _resolve_value(step["to"], ctx)
    caption_raw = step.get("caption")
    caption = _resolve_value(caption_raw, ctx) if caption_raw else None
    filename_raw = step.get("filename")
    filename = _resolve_value(filename_raw, ctx) if filename_raw else None

    media_path: str | None = None
    media_kind: str | None = None
    for key in ("document", "image"):
        raw = step.get(key)
        if raw:
            media_path = _resolve_value(raw, ctx)
            media_kind = key
            break

    if not media_path and step.get("source"):
        src = step["source"]
        val = ctx["vars"].get(src)
        if isinstance(val, list):
            val = val[0] if val else None
        if val:
            media_path = str(val)
            ext = Path(media_path).suffix.lower()
            media_kind = "image" if ext in {".jpg", ".jpeg", ".png", ".webp"} else "document"

    if media_kind == "document":
        result = wa.send_document(to, path=media_path, caption=caption, filename=filename)
    elif media_kind == "image":
        result = wa.send_image(to, path=media_path, caption=caption)
    else:
        body = _resolve_value(step.get("body", ""), ctx)
        if not body:
            raise StepError("whatsapp_send: need one of body / document / image / source")
        result = wa.send_text_message(to, body)

    if result.get("error"):
        ctx["vars"]["last_whatsapp_error"] = result["error"]
        logger.warning("[run #%d] whatsapp_send failed: %s", ctx["run_id"], result)
    else:
        ctx["vars"]["last_whatsapp_message_id"] = result.get("message_id", "")


async def _step_log(page, step, ctx):
    logger.info("[run #%d] %s", ctx["run_id"], _resolve_value(step.get("message", ""), ctx))


HANDLERS = {
    "goto": _step_goto,
    "fill": _step_fill,
    "click": _step_click,
    "wait_for": _step_wait_for,
    "wait_ms": _step_wait_ms,
    "press": _step_press,
    "select_option": _step_select_option,
    "get_otp": _step_get_otp,
    "await_user_session": _step_await_user_session,
    "download_one": _step_download_one,
    "download_each": _step_download_each,
    "upload_drive": _step_upload_drive,
    "screenshot": _step_screenshot,
    "whatsapp_send": _step_whatsapp_send,
    "log": _step_log,
}

# Fail fast if schema's declared op list ever drifts from the actual handlers.
from roost.services.rpa_flows.schema import KNOWN_OPS as _SCHEMA_KNOWN_OPS
assert set(HANDLERS) == set(_SCHEMA_KNOWN_OPS), (
    f"interpreter HANDLERS and schema.KNOWN_OPS drifted: "
    f"{set(HANDLERS) ^ set(_SCHEMA_KNOWN_OPS)}"
)


# ── Public entry points ────────────────────────────────────────────


async def run_config(
    config: dict,
    run_id: int,
    user_id: str,
    params: dict | None = None,
) -> dict:
    """Execute a stored flow config end-to-end."""
    portal = config["portal_slug"]
    steps = config.get("steps") or []
    if not steps:
        rpa_runs.fail(run_id, f"flow config for {portal} has no steps")
        return {"error": "empty_flow"}

    ctx = {
        "run_id": run_id,
        "user_id": user_id,
        "portal": portal,
        "uid_int": int(user_id) if user_id.isdigit() else 1,
        "params": params or {},
        "vars": {},
        "downloaded": [],
        "extracted": [],
    }

    try:
        async with browser_service.portal_context(user_id, portal) as bctx:
            page = await bctx.new_page()
            for i, step in enumerate(steps):
                await _run_step(page, step, ctx, index=i)
    except Exception as e:
        logger.exception("Flow %s failed at runtime", portal)
        rpa_runs.fail(run_id, f"{type(e).__name__}: {e}")
        return {"error": str(e), "downloaded": ctx["downloaded"], "extracted": ctx["extracted"]}

    result = {
        "portal": portal,
        "downloaded": ctx["downloaded"],
        "extracted": ctx["extracted"],
        "vars": {k: v for k, v in ctx["vars"].items() if k != "otp"},
    }
    rpa_runs.complete(run_id, result)
    return result


async def run_single_step(
    config: dict,
    step_index: int,
    run_id: int,
    user_id: str,
    params: dict | None = None,
) -> dict:
    """Run one step in isolation against the live portal — for selector debugging.

    The caller supplies a transient run_id so OTP prompts still work; the
    run is left as-is afterwards (caller decides whether to complete it).
    """
    portal = config["portal_slug"]
    steps = config.get("steps") or []
    if step_index < 0 or step_index >= len(steps):
        return {"error": f"step_index out of range (0..{len(steps)-1})"}

    ctx = {
        "run_id": run_id,
        "user_id": user_id,
        "portal": portal,
        "uid_int": int(user_id) if user_id.isdigit() else 1,
        "params": params or {},
        "vars": {},
        "downloaded": [],
        "extracted": [],
    }
    step = steps[step_index]

    async with browser_service.portal_context(user_id, portal) as bctx:
        page = await bctx.new_page()
        try:
            await _run_step(page, step, ctx, index=step_index)
            return {"ok": True, "step": step, "vars": ctx["vars"], "downloaded": ctx["downloaded"]}
        except Exception as e:
            return {"ok": False, "step": step, "error": f"{type(e).__name__}: {e}"}


async def _run_step(page, step, ctx, *, index: int):
    op = step.get("op") or step.get("type")
    if not op:
        raise StepError(f"step #{index} missing 'op'")
    handler = HANDLERS.get(op)
    if not handler:
        raise StepError(f"step #{index}: unknown op '{op}'")
    logger.info("[run #%d] step %d: %s", ctx["run_id"], index, op)
    ctx["vars"]["_step_index"] = index
    await handler(page, step, ctx)
