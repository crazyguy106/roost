"""Web content extraction service."""

import httpx
import trafilatura
from markdownify import markdownify as md

_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (compatible; Roost/1.0)"
_CDP_ENDPOINT = "http://127.0.0.1:9222"


def fetch_article(url: str) -> dict:
    """Fetch URL and extract main article content as markdown.

    Uses trafilatura for content extraction (strips nav, ads, boilerplate).
    Falls back to markdownify on the raw HTML if trafilatura returns nothing.
    """
    try:
        resp = httpx.get(url, headers={"User-Agent": _USER_AGENT},
                         timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        # Try trafilatura first (best at article extraction)
        content = trafilatura.extract(html, include_links=True,
                                       include_tables=True,
                                       output_format="markdown",
                                       url=url)
        if content:
            return {"url": url, "content": content, "method": "trafilatura",
                    "content_length": len(content)}

        # Fallback: markdownify the full HTML
        content = md(html, strip=["script", "style", "nav", "footer", "header"])
        content = "\n".join(line for line in content.splitlines() if line.strip())
        return {"url": url, "content": content, "method": "markdownify_fallback",
                "content_length": len(content)}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}


def fetch_js_page(url: str, js_expression: str | None = None,
                  wait_seconds: float = 2) -> dict:
    """Fetch a JS-rendered page via Playwright CDP, optionally run JS.

    Connects to existing Chrome instance on CDP endpoint.
    If js_expression is provided, evaluates it and returns the result.
    Otherwise returns the page HTML converted to markdown.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(_CDP_ENDPOINT)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()

            try:
                page.goto(url, wait_until="networkidle", timeout=30000)

                if wait_seconds > 0:
                    page.wait_for_timeout(int(wait_seconds * 1000))

                if js_expression:
                    result = page.evaluate(js_expression)
                    return {"url": url, "result": result, "method": "js_eval"}

                html = page.content()
                content = trafilatura.extract(html, include_links=True,
                                               include_tables=True,
                                               output_format="markdown",
                                               url=url)
                if not content:
                    content = md(html, strip=["script", "style", "nav", "footer"])
                    content = "\n".join(l for l in content.splitlines() if l.strip())

                return {"url": url, "content": content, "method": "playwright+trafilatura",
                        "content_length": len(content)}
            finally:
                page.close()
    except Exception as e:
        return {"error": str(e), "url": url}
