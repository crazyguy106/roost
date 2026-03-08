"""FastMCP server — stdio transport for Claude Code integration.

Entry point: roost-mcp (registered in setup.py console_scripts).

Multi-tenant: Each process serves a single user, identified by
ROOST_USER env var. See docs/multi-tenancy.md.
"""

import logging

from fastmcp import FastMCP

from roost.config import (
    GOOGLE_ENABLED,
    MS_ENABLED,
    AI_ENABLED,
    TELEGRAM_ENABLED,
    NOTION_ENABLED,
    INFRA_ENABLED,
)

logger = logging.getLogger("roost.mcp.server")

mcp = FastMCP(
    name="roost",
    instructions=(
        "Roost manages tasks, projects, contacts, entities, calendar events, "
        "email, file storage, and more for a personal productivity system. "
        "Use these tools to query and modify the user's data."
    ),
)

# ── Core tools (always loaded) ──────────────────────────────────────
from roost.mcp import tools_tasks              # noqa: E402, F401
from roost.mcp import tools_tasks_extended     # noqa: E402, F401
from roost.mcp import tools_activity           # noqa: E402, F401
from roost.mcp import tools_wellbeing          # noqa: E402, F401
from roost.mcp import tools_contacts           # noqa: E402, F401
from roost.mcp import tools_comms              # noqa: E402, F401
from roost.mcp import tools_projects           # noqa: E402, F401
from roost.mcp import tools_entities           # noqa: E402, F401
from roost.mcp import tools_notes              # noqa: E402, F401
from roost.mcp import tools_okr               # noqa: E402, F401
from roost.mcp import tools_bundles           # noqa: E402, F401
from roost.mcp import tools_time              # noqa: E402, F401
from roost.mcp import tools_stats             # noqa: E402, F401
from roost.mcp import tools_scheduled_emails  # noqa: E402, F401
from roost.mcp import tools_scrape            # noqa: E402, F401
from roost.mcp import tools_text_cleanup      # noqa: E402, F401

# ── Google tools (GOOGLE_ENABLED) ───────────────────────────────────
if GOOGLE_ENABLED:
    from roost.mcp import tools_gmail           # noqa: E402, F401
    from roost.mcp import tools_calendar        # noqa: E402, F401
    from roost.mcp import tools_calendar_write  # noqa: E402, F401
    from roost.mcp import tools_drive           # noqa: E402, F401
    from roost.mcp import tools_slides          # noqa: E402, F401
    from roost.mcp import tools_sheets          # noqa: E402, F401
    from roost.mcp import tools_docs            # noqa: E402, F401

# ── Microsoft tools (MS_ENABLED) ────────────────────────────────────
if MS_ENABLED:
    from roost.mcp import tools_ms_email       # noqa: E402, F401
    from roost.mcp import tools_ms_calendar    # noqa: E402, F401
    from roost.mcp import tools_ms_onedrive    # noqa: E402, F401
    from roost.mcp import tools_ms_excel       # noqa: E402, F401
    from roost.mcp import tools_ms_teams       # noqa: E402, F401
    from roost.mcp import tools_ms_sharepoint  # noqa: E402, F401

# ── AI tools (AI_ENABLED) ───────────────────────────────────────────
if AI_ENABLED:
    from roost.mcp import tools_gemini         # noqa: E402, F401
    from roost.mcp import tools_meeting_notes  # noqa: E402, F401
    from roost.mcp import tools_docgen         # noqa: E402, F401

# ── Telegram tools (TELEGRAM_ENABLED) ───────────────────────────────
if TELEGRAM_ENABLED:
    from roost.mcp import tools_telegram       # noqa: E402, F401

# ── Notion tools (NOTION_ENABLED) ───────────────────────────────────
if NOTION_ENABLED:
    from roost.mcp import tools_notion         # noqa: E402, F401

# ── Infra tools (INFRA_ENABLED) ─────────────────────────────────────
if INFRA_ENABLED:
    from roost.mcp import tools_ssh            # noqa: E402, F401
    from roost.mcp import tools_docker         # noqa: E402, F401
    from roost.mcp import tools_k8s            # noqa: E402, F401


def main():
    """Run the MCP server over stdio."""
    from roost.user_context import init_user_context

    ctx = init_user_context()
    logger.info("MCP server starting for user: %s (id=%d)", ctx.email, ctx.user_id)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
