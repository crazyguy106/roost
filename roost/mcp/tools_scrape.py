"""MCP tools for web content extraction."""

from roost.mcp.server import mcp


@mcp.tool()
def scrape_url(url: str, output_file: str = "") -> dict:
    """Fetch a URL and extract its main content as clean markdown.

    Uses trafilatura to strip navigation, ads, and boilerplate — returning
    only the article/page content. Works for ~90% of web pages (articles,
    blogs, company profiles). For JS-heavy pages (SPAs, Lark), use scrape_js.

    Args:
        url: The URL to fetch and extract content from.
        output_file: Optional path to save the extracted markdown to disk.
    """
    try:
        from roost.services.scrape_service import fetch_article
        import os

        result = fetch_article(url)
        if "error" in result:
            return result

        if output_file:
            os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
            with open(output_file, "w") as f:
                f.write(f"# Scraped: {url}\n\n{result['content']}")
            result["saved_to"] = output_file

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def scrape_js(url: str, js_expression: str = "", wait_seconds: float = 2,
              output_file: str = "") -> dict:
    """Fetch a JS-rendered page via headless browser, optionally run JavaScript.

    Connects to the existing Chrome instance (CDP on port 9222). Use this for:
    - Single-page applications (SPAs) that render content via JavaScript
    - Lark Sheets (pass JS expression to extract cell data)
    - Pages behind authentication (shares browser session/cookies)

    If js_expression is provided, evaluates it and returns the result.
    Otherwise extracts the rendered page content as markdown.

    Args:
        url: The URL to navigate to.
        js_expression: Optional JavaScript to evaluate on the page. The return
            value becomes the result. Example for Lark:
            "document.querySelector('.sheet-container').innerText"
        wait_seconds: Seconds to wait after page load for JS rendering (default 2).
        output_file: Optional path to save the result to disk.
    """
    try:
        from roost.services.scrape_service import fetch_js_page
        import os

        result = fetch_js_page(url, js_expression or None, wait_seconds)
        if "error" in result:
            return result

        if output_file:
            content = result.get("content") or str(result.get("result", ""))
            os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
            with open(output_file, "w") as f:
                f.write(f"# Scraped: {url}\n\n{content}")
            result["saved_to"] = output_file

        return result
    except Exception as e:
        return {"error": str(e)}
