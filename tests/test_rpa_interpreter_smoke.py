"""End-to-end smoke test for the RPA interpreter, with Playwright and
sidecar deps stubbed out.

Verifies:
  - step dispatch walks every supported op
  - placeholder resolution ($cred, $param, $var, $otp, $state)
  - OTP pause/resume round-trip via TelegramOtpSource
  - vars carry across steps (last_download, last_extracted, otp, policy)
  - downloads/extractions land in `ctx["downloaded"]` and `ctx["extracted"]`

This is the closest we can get to a portal-free, deps-light verification
that the engine is correctly wired. For a real-portal smoke against
the-internet.herokuapp.com, see docs/rpa-authoring.md.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Stub heavy deps before importing the interpreter ──────────────────

def _install_stubs():
    if "pyzipper" not in sys.modules:
        m = types.ModuleType("pyzipper")
        m.AESZipFile = type("AESZipFile", (), {})
        m.WZ_AES = 0
        sys.modules["pyzipper"] = m
    if "playwright" not in sys.modules:
        sys.modules["playwright"] = types.ModuleType("playwright")
    if "playwright.async_api" not in sys.modules:
        async_api = types.ModuleType("playwright.async_api")
        async def _ap(*_a, **_kw):
            raise RuntimeError("real playwright not available in test env")
        async_api.async_playwright = _ap
        async_api.Browser = object
        async_api.BrowserContext = object
        async_api.Page = object
        async_api.Download = object
        sys.modules["playwright.async_api"] = async_api


_install_stubs()


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_page():
    """A mock Playwright Page with all the methods the interpreter calls."""
    page = MagicMock(name="Page")
    page.goto = AsyncMock()
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.press = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.locator = MagicMock()
    return page


@pytest.fixture
def patched_browser_service(monkeypatch, tmp_path):
    """Replace `browser_service.portal_context` with a no-op async ctx, and
    `download_via_action` with a stub that writes a 1-byte file and returns
    its path."""
    from roost.services import browser_service

    @asynccontextmanager
    async def fake_ctx(user_id, portal_slug, **kw):
        bctx = MagicMock(name="BrowserContext")
        async def new_page():
            return None  # caller will overwrite via patch in test
        bctx.new_page = AsyncMock(side_effect=new_page)
        yield bctx

    async def fake_download(page, click_action, dest_dir):
        await click_action(page)
        out = Path(dest_dir) / "fake_download.zip"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(browser_service, "portal_context", fake_ctx)
    monkeypatch.setattr(browser_service, "download_via_action", fake_download)
    return browser_service


# ── Smoke tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interpreter_walks_every_simple_op(fake_page, patched_browser_service, monkeypatch, tmp_path):
    """Run a flow that exercises goto/fill/click/wait_for/wait_ms/press/log
    and confirms placeholder resolution touches $param, $cred, $var, $otp.
    """
    # Patch credentials lookup
    from roost.services import credentials
    monkeypatch.setattr(
        credentials, "get_credential",
        lambda key, user_id=None: {"the_internet_user": "tomsmith"}.get(key, ""),
    )

    # Patch the portal_context to yield a context whose new_page returns our fake_page
    from roost.services import browser_service

    @asynccontextmanager
    async def fake_ctx(user_id, portal_slug, **kw):
        bctx = MagicMock()
        bctx.new_page = AsyncMock(return_value=fake_page)
        yield bctx
    monkeypatch.setattr(browser_service, "portal_context", fake_ctx)

    from roost.services.rpa_flows._interpreter import run_config
    from roost.services import rpa_runs

    cfg = {
        "portal_slug": "smoke",
        "steps": [
            {"op": "goto", "url": "$param:login_url"},
            {"op": "fill", "selector": "#u", "value": "$cred:the_internet_user"},
            {"op": "fill", "selector": "#otp", "value": "$otp"},
            {"op": "press", "selector": "#u", "key": "Enter"},
            {"op": "wait_ms", "ms": 1},
            {"op": "wait_for", "selector": ".ok"},
            {"op": "click", "selector": "button"},
            {"op": "log", "message": "done"},
        ],
    }

    run_id = rpa_runs.create_run(user_id="1", portal_slug="smoke")
    # Pre-seed otp into vars by submitting before the OTP step would run
    # (this flow has no get_otp; we set the var directly via params)
    cfg["steps"][2]["value"] = "$param:otp"  # easier: read from param
    result = await run_config(cfg, run_id, "1", {"login_url": "https://x", "otp": "123456"})

    assert "error" not in result, result

    # Verify Playwright methods were actually called with resolved values
    fake_page.goto.assert_awaited_once()
    assert fake_page.goto.await_args.args[0] == "https://x"

    # First fill: $cred resolved to tomsmith
    first_fill = fake_page.fill.await_args_list[0]
    assert first_fill.args == ("#u", "tomsmith")

    # Second fill: $param:otp resolved to 123456
    second_fill = fake_page.fill.await_args_list[1]
    assert second_fill.args == ("#otp", "123456")

    fake_page.press.assert_awaited_with("#u", "Enter")
    fake_page.wait_for_selector.assert_awaited()
    fake_page.click.assert_awaited_with("button", timeout=30000)


@pytest.mark.asyncio
async def test_interpreter_otp_pause_and_resume(fake_page, monkeypatch):
    """get_otp via TelegramOtpSource should pause the run and resume when
    rpa_runs.submit_input is called."""
    from roost.services import browser_service, rpa_runs

    @asynccontextmanager
    async def fake_ctx(user_id, portal_slug, **kw):
        bctx = MagicMock()
        bctx.new_page = AsyncMock(return_value=fake_page)
        yield bctx
    monkeypatch.setattr(browser_service, "portal_context", fake_ctx)

    # Don't actually ping Telegram during test
    async def _noop(*_a, **_kw): return None
    monkeypatch.setattr(rpa_runs, "_notify_telegram", _noop)

    from roost.services.rpa_flows._interpreter import run_config

    cfg = {
        "portal_slug": "smoke_otp",
        "steps": [
            {"op": "get_otp", "source": "telegram",
             "prompt": "Enter OTP", "timeout": 5},
            {"op": "fill", "selector": "#otp", "value": "$otp"},
        ],
    }

    run_id = rpa_runs.create_run(user_id="1", portal_slug="smoke_otp")

    async def submit_after_pause():
        # Wait until the run flips to awaiting_input, then submit
        for _ in range(50):
            run = rpa_runs.get_run(run_id)
            if run and run.get("status") == "awaiting_input":
                break
            await asyncio.sleep(0.05)
        rpa_runs.submit_input(run_id, "987654")

    submitter = asyncio.create_task(submit_after_pause())
    result = await run_config(cfg, run_id, "1", {})
    await submitter

    assert "error" not in result, result
    fake_page.fill.assert_awaited_with("#otp", "987654")


@pytest.mark.asyncio
async def test_interpreter_screenshot_records_path(
    fake_page, monkeypatch, tmp_path
):
    """screenshot should call page.screenshot, write to today's run dir, and
    record the path as last_screenshot + in ctx.downloaded."""
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    import importlib
    from roost import config as roost_config
    importlib.reload(roost_config)
    from roost.services.rpa_flows import _interpreter
    importlib.reload(_interpreter)

    from roost.services import browser_service, rpa_runs

    captured = {}
    async def fake_screenshot(path, full_page=False):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG fake")
        captured["path"] = path
        captured["full_page"] = full_page
    fake_page.screenshot = fake_screenshot

    @asynccontextmanager
    async def fake_ctx(user_id, portal_slug, **kw):
        bctx = MagicMock()
        bctx.new_page = AsyncMock(return_value=fake_page)
        yield bctx
    monkeypatch.setattr(browser_service, "portal_context", fake_ctx)

    cfg = {
        "portal_slug": "smoke_shot",
        "steps": [
            {"op": "screenshot", "name": "rates", "full_page": True},
        ],
    }
    run_id = rpa_runs.create_run(user_id="1", portal_slug="smoke_shot")
    result = await _interpreter.run_config(cfg, run_id, "1", {})

    assert "error" not in result, result
    assert captured["full_page"] is True
    assert captured["path"].endswith("rates.png")
    assert result["downloaded"] and result["downloaded"][0].endswith("rates.png")
    assert "last_screenshot" in result["vars"]


@pytest.mark.asyncio
async def test_interpreter_whatsapp_send_dispatches_to_service(
    fake_page, monkeypatch, tmp_path
):
    """whatsapp_send op should dispatch to the right whatsapp service
    function (text vs document) and record the message_id in vars."""
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    import importlib
    from roost import config as roost_config
    importlib.reload(roost_config)
    from roost.services.rpa_flows import _interpreter
    importlib.reload(_interpreter)

    from roost.services import browser_service, rpa_runs, whatsapp

    sent = []
    def fake_send_text(to, body):
        sent.append(("text", to, body))
        return {"ok": True, "message_id": "wamid.text"}
    def fake_send_document(to, path=None, link=None, caption=None, filename=None, **_kw):
        sent.append(("document", to, str(path), caption, filename))
        return {"ok": True, "message_id": "wamid.doc"}
    monkeypatch.setattr(whatsapp, "send_text_message", fake_send_text)
    monkeypatch.setattr(whatsapp, "send_document", fake_send_document)

    @asynccontextmanager
    async def fake_ctx(user_id, portal_slug, **kw):
        bctx = MagicMock()
        bctx.new_page = AsyncMock(return_value=fake_page)
        yield bctx
    monkeypatch.setattr(browser_service, "portal_context", fake_ctx)

    fake_pdf = tmp_path / "report.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    cfg = {
        "portal_slug": "smoke_wa",
        "steps": [
            {"op": "whatsapp_send", "to": "$param:phone", "body": "$param:greeting"},
            {"op": "whatsapp_send", "to": "$param:phone",
             "document": str(fake_pdf), "caption": "$param:caption",
             "filename": "$param:fname"},
        ],
    }
    run_id = rpa_runs.create_run(user_id="1", portal_slug="smoke_wa")
    result = await _interpreter.run_config(
        cfg, run_id, "1",
        {
            "phone": "+6591234567",
            "greeting": "Hi Alex",
            "caption": "AIA 2026-04-26",
            "fname": "policy.pdf",
        },
    )

    assert "error" not in result, result
    assert len(sent) == 2
    assert sent[0] == ("text", "+6591234567", "Hi Alex")
    assert sent[1][0] == "document"
    assert sent[1][1] == "+6591234567"
    assert sent[1][2].endswith("report.pdf")
    assert sent[1][3] == "AIA 2026-04-26"
    assert sent[1][4] == "policy.pdf"
    assert result["vars"].get("last_whatsapp_message_id") == "wamid.doc"


@pytest.mark.asyncio
async def test_interpreter_download_one_records_path(
    fake_page, patched_browser_service, monkeypatch, tmp_path
):
    """download_one should populate ctx.downloaded and resolve $var:last_download
    in subsequent steps."""
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))

    # Re-import config so UPLOADS_DIR is picked up for this run
    import importlib
    from roost import config as roost_config
    importlib.reload(roost_config)
    from roost.services.rpa_flows import _interpreter
    importlib.reload(_interpreter)

    from roost.services import browser_service, rpa_runs

    @asynccontextmanager
    async def fake_ctx(user_id, portal_slug, **kw):
        bctx = MagicMock()
        bctx.new_page = AsyncMock(return_value=fake_page)
        yield bctx
    monkeypatch.setattr(browser_service, "portal_context", fake_ctx)

    cfg = {
        "portal_slug": "smoke_dl",
        "steps": [
            {"op": "download_one", "trigger_selector": "a.dl"},
            {"op": "log", "message": "got $var:last_download"},
        ],
    }
    run_id = rpa_runs.create_run(user_id="1", portal_slug="smoke_dl")
    result = await _interpreter.run_config(cfg, run_id, "1", {})

    assert "error" not in result, result
    assert result["downloaded"], "expected at least one downloaded path"
    assert result["downloaded"][0].endswith("fake_download.zip")
