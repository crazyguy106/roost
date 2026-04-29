"""Persistent async-Playwright browser contexts for RPA flows.

Connects to the browserless/chromium sidecar over CDP (same endpoint as
`scrape_service`) and manages one storage-state file per (user, portal)
so that login cookies survive across runs.

Why not extend `scrape_service.py`:
  - `scrape_service` is sync-only, one-shot, stateless.
  - RPA flows are I/O-bound and live inside the async Telegram bot;
    using sync Playwright in async code blocks the event loop.
  - RPA needs persistent storage state per portal; `scrape_service`
    intentionally throws contexts away.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from roost.config import PROJECT_ROOT

logger = logging.getLogger("roost.services.browser_service")

# Direct ws:// URL bypasses browserless' /json/version discovery, which
# defaults to returning `ws://0.0.0.0:3000/` (its bind address, not the
# routable hostname). Override via env if your sidecar uses a different host.
CDP_ENDPOINT = os.getenv("CDP_ENDPOINT", "ws://chromium:3000")

# Where per-portal storage state files (cookies, localStorage) live.
STATE_DIR = Path(os.getenv("BROWSER_STATE_DIR", str(PROJECT_ROOT / "data" / "browser_state")))


def _state_path(user_id: str | int, portal_slug: str) -> Path:
    safe_user = str(user_id).replace("/", "_") or "default"
    safe_portal = portal_slug.replace("/", "_")
    return STATE_DIR / safe_user / f"{safe_portal}.json"


@asynccontextmanager
async def portal_context(
    user_id: str | int,
    portal_slug: str,
    *,
    viewport: dict | None = None,
    locale: str = "en-SG",
    timezone_id: str = "Asia/Singapore",
) -> AsyncIterator:
    """Async context manager yielding a Playwright BrowserContext.

    Loads storage state from disk on entry (if present) and saves it back
    on clean exit. The browser is kept alive on the browserless sidecar;
    only the context (cookies, pages) is created and torn down here.

    Usage:
        async with portal_context(user_id, "aia") as ctx:
            page = await ctx.new_page()
            await page.goto("https://...")
    """
    from playwright.async_api import async_playwright

    state_file = _state_path(user_id, portal_slug)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    storage_state = None
    if state_file.exists():
        try:
            storage_state = json.loads(state_file.read_text())
        except Exception:
            logger.warning("Corrupt storage state for %s, ignoring", state_file)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_ENDPOINT)
        try:
            context = await browser.new_context(
                storage_state=storage_state,
                viewport=viewport or {"width": 1280, "height": 900},
                locale=locale,
                timezone_id=timezone_id,
                accept_downloads=True,
            )
            try:
                yield context
            finally:
                # Persist cookies / localStorage for next run
                try:
                    state = await context.storage_state()
                    state_file.write_text(json.dumps(state))
                except Exception:
                    logger.exception("Failed to save storage state for %s", portal_slug)
                await context.close()
        finally:
            # CDP browser is owned by the sidecar; just disconnect.
            try:
                await browser.close()
            except Exception:
                pass


async def download_via_action(page, click_action, dest_dir: str | Path) -> Path:
    """Run `click_action(page)` while expecting a download; save it to `dest_dir`.

    `click_action` is an async callable that triggers the download (e.g.
    `lambda pg: pg.click("text=Download policy")`). Returns the saved path.

    When connected over CDP to a remote browserless sidecar, Playwright's
    `download.save_as()` performs a server-side copy inside the sidecar and
    returns a 0-byte file on the client side. To work around this we re-fetch
    the resource via the browser context's APIRequestContext (which carries
    the authenticated session cookies), and fall back to `save_as` only when
    the URL is a `blob:` URL that can't be re-fetched.
    """
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with page.expect_download() as dl_info:
        await click_action(page)
    download = await dl_info.value
    target = out_dir / download.suggested_filename
    url = download.url

    if url and not url.startswith("blob:"):
        resp = await page.context.request.get(url)
        body = await resp.body()
        target.write_bytes(body)
    else:
        await download.save_as(target)

    logger.info("Downloaded %s → %s (%d bytes)",
                download.suggested_filename, target, target.stat().st_size)
    return target


_INSPECT_JS = r"""
() => {
  function trim(s, n=80){ return (s||'').toString().trim().slice(0,n); }
  function unique(sel){ try { return document.querySelectorAll(sel).length === 1; } catch { return false; } }
  function bestSelector(el){
    if (!el) return null;
    if (el.id) {
      const s = '#' + CSS.escape(el.id);
      if (unique(s)) return s;
    }
    if (el.getAttribute('name')) {
      const s = el.tagName.toLowerCase() + "[name='" + el.getAttribute('name').replace(/'/g, "\\'") + "']";
      if (unique(s)) return s;
    }
    for (const a of ['data-testid','data-test','data-cy','data-policy','data-policy-no']) {
      const v = el.getAttribute(a);
      if (v) {
        const s = '[' + a + "='" + v.replace(/'/g, "\\'") + "']";
        if (unique(s)) return s;
      }
    }
    if (el.getAttribute('aria-label')) {
      const s = el.tagName.toLowerCase() + "[aria-label='" + el.getAttribute('aria-label').replace(/'/g, "\\'") + "']";
      if (unique(s)) return s;
    }
    return null;
  }
  function labelFor(el){
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.id) {
      const lbl = document.querySelector("label[for='" + CSS.escape(el.id) + "']");
      if (lbl) return trim(lbl.textContent);
    }
    const wrap = el.closest('label');
    if (wrap) return trim(wrap.textContent);
    return el.placeholder || el.getAttribute('title') || '';
  }
  const inputs = [...document.querySelectorAll('input,select,textarea')]
    .filter(el => el.type !== 'hidden')
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      label: trim(labelFor(el)),
      placeholder: el.placeholder || '',
      selector: bestSelector(el),
    }));
  const buttons = [...document.querySelectorAll('button, input[type=submit], input[type=button], [role=button]')]
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      text: trim(el.innerText || el.value),
      label: trim(el.getAttribute('aria-label') || ''),
      selector: bestSelector(el),
    }))
    .filter(b => b.text || b.label);
  const links = [...document.querySelectorAll('a[href]')]
    .map(el => ({
      text: trim(el.innerText),
      href: trim(el.getAttribute('href'), 200),
      selector: bestSelector(el),
    }))
    .filter(l => l.text)
    .slice(0, 40);
  const tables = [...document.querySelectorAll('table')]
    .map(el => ({
      caption: trim(el.querySelector('caption')?.textContent || ''),
      headers: [...el.querySelectorAll('th')].slice(0,12).map(h => trim(h.innerText, 40)),
      row_count: el.querySelectorAll('tr').length,
      first_row_selector: bestSelector(el.querySelector('tbody tr')),
    }))
    .slice(0, 6);
  return {
    title: document.title,
    url: location.href,
    inputs, buttons, links, tables,
    has_otp_field: inputs.some(i => /otp|2fa|verification|verify|code/i.test(
      i.name + ' ' + i.id + ' ' + i.label + ' ' + i.placeholder)),
  };
}
"""


async def live_debug_url(page) -> str | None:
    """Return a public devtools URL the user can open to drive `page`.

    Queries the sidecar's CDP discovery endpoint (`/json/list`) and matches
    the page's current URL to a target. The returned URL is rewritten so it
    points at SIDECAR_PUBLIC_URL — i.e. the host/port the human can reach
    (loopback on laptop deploys, reverse-proxied domain on cloud deploys).

    Returns None if discovery fails or no match. Callers should treat this
    as best-effort — RPA flows still work without a live URL, the user just
    has to fall back to noVNC / SSH tunnelling.
    """
    from urllib.parse import urlsplit
    try:
        import httpx
        from roost.config import SIDECAR_INTERNAL_HTTP_URL, SIDECAR_PUBLIC_URL
    except Exception:
        return None

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{SIDECAR_INTERNAL_HTTP_URL.rstrip('/')}/json/list")
            r.raise_for_status()
            targets = r.json()
    except Exception:
        logger.warning("live_debug_url: CDP /json/list failed", exc_info=True)
        return None

    page_url = page.url
    target = None
    for t in targets:
        if t.get("type") == "page" and t.get("url") == page_url:
            target = t
            break
    if target is None:
        # Fall back to first non-extension page target.
        for t in targets:
            if t.get("type") == "page" and not (t.get("url") or "").startswith("chrome-extension://"):
                target = t
                break
    if target is None or not target.get("id"):
        return None

    pub = SIDECAR_PUBLIC_URL.rstrip("/")
    parts = urlsplit(pub)
    # The devtools front-end constructs ws://|wss:// from page scheme,
    # so `ws=` must be host[:port] + path, NO scheme. When deployed
    # behind Roost's /sidecar proxy this includes the proxy path.
    ws_authority = (parts.netloc or pub) + (parts.path or "")
    return f"{pub}/devtools/inspector.html?ws={ws_authority}/devtools/page/{target['id']}"


async def inspect_page(url: str, *, user_id: str | int = "", portal_slug: str = "_inspect",
                       wait_for: str | None = None, timeout_ms: int = 30000) -> dict:
    """Load `url` and return a structured summary of interactive elements.

    Used by the AI flow-authoring assistant to draft selectors without the
    user touching DevTools. Reuses the per-portal storage state so a page
    that requires login can be inspected after a manual login session.
    """
    async with portal_context(user_id, portal_slug) as ctx:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_for:
            try:
                await page.wait_for_selector(wait_for, timeout=timeout_ms)
            except Exception:
                pass
        try:
            return await page.evaluate(_INSPECT_JS)
        finally:
            await page.close()
