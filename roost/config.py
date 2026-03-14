"""Load configuration from .env file."""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Telegram
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USERS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
]

# Google OAuth
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_ALLOWED_EMAIL: str = os.getenv("GOOGLE_ALLOWED_EMAIL", "")
GOOGLE_ALLOWED_EMAILS: set[str] = {
    e.strip().lower() for e in GOOGLE_ALLOWED_EMAIL.split(",") if e.strip()
}
SESSION_SECRET: str = os.getenv("SESSION_SECRET", "change-me-to-random-hex")

# Master toggle for all Google services (Calendar, Gmail, Drive, Workspace)
GOOGLE_ENABLED: bool = os.getenv("GOOGLE_ENABLED", "true").lower() == "true"

# Legacy Basic Auth (fallback if OAuth not configured)
WEB_USERNAME: str = os.getenv("WEB_USERNAME", "admin")
WEB_PASSWORD: str = os.getenv("WEB_PASSWORD", "changeme")

# Database
DATABASE_PATH: str = os.getenv(
    "DATABASE_PATH",
    str(PROJECT_ROOT / "data" / "roost.db"),
)

# Rate limiting (seconds between AI commands per user)
AI_RATE_LIMIT: int = int(os.getenv("AI_RATE_LIMIT", "30"))

# Web server
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.getenv("WEB_PORT", "8080"))

# File paths
UPLOADS_DIR: str = os.getenv("UPLOADS_DIR", str(PROJECT_ROOT / "uploads"))
DOCS_DIR: str = os.getenv("DOCS_DIR", str(PROJECT_ROOT / "generated"))

# Calendar & Reminders
GOOGLE_CALENDAR_ICS_URL: str = os.getenv("GOOGLE_CALENDAR_ICS_URL", "")
MORNING_DIGEST_HOUR: int = int(os.getenv("MORNING_DIGEST_HOUR", "8"))
MORNING_DIGEST_MINUTE: int = int(os.getenv("MORNING_DIGEST_MINUTE", "0"))
REMINDER_TIMEZONE: str = os.getenv("REMINDER_TIMEZONE", "Asia/Singapore")

# Notion Mirror
NOTION_API_TOKEN: str = os.getenv("NOTION_API_TOKEN", "")
NOTION_SYNC_ENABLED: bool = os.getenv("NOTION_SYNC_ENABLED", "false").lower() == "true"
NOTION_PARENT_PAGE_ID: str = os.getenv("NOTION_PARENT_PAGE_ID", "")
NOTION_POLL_INTERVAL: int = int(os.getenv("NOTION_POLL_INTERVAL", "300"))
NOTION_JOURNAL_PAGE_ID: str = os.getenv("NOTION_JOURNAL_PAGE_ID", "")

# Gmail + Calendar Write (OAuth — reuses GOOGLE_CLIENT_ID/SECRET)
GMAIL_ENABLED: bool = os.getenv("GMAIL_ENABLED", "false").lower() == "true"
# Multi-account Google: default account email (empty = use first available)
DEFAULT_GOOGLE_ACCOUNT: str = os.getenv("DEFAULT_GOOGLE_ACCOUNT", "")
GMAIL_SEND_FROM: str = os.getenv("GMAIL_SEND_FROM", "")
GMAIL_POLL_INTERVAL: int = int(os.getenv("GMAIL_POLL_INTERVAL", "300"))

# Microsoft Graph OAuth (Azure AD)
MS_CLIENT_ID: str = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET: str = os.getenv("MS_CLIENT_SECRET", "")
MS_TENANT_ID: str = os.getenv("MS_TENANT_ID", "common")
MS_ENABLED: bool = os.getenv("MS_ENABLED", "false").lower() == "true"

# Shared MSAL token cache file — when set, Roost reads/writes the MSAL
# SerializableTokenCache as JSON to this file instead of the SQLite DB.
# Use to share auth tokens between Roost and DeptTools on the same host.
MSAL_CACHE_PATH: str = os.getenv("MSAL_CACHE_PATH", "")

# Gemini Agentic (google-genai SDK)
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_AGENTIC: bool = os.getenv("GEMINI_AGENTIC", "true").lower() == "true"

# Curriculum scanner (seeds programme data from YAML files)
CURRICULUM_ENABLED: bool = os.getenv("CURRICULUM_ENABLED", "true").lower() == "true"

# Modular deployment feature flags
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in ("true", "1", "yes")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() in ("true", "1", "yes")
NOTION_ENABLED = os.getenv("NOTION_ENABLED", "false").lower() in ("true", "1", "yes")
INFRA_ENABLED = os.getenv("INFRA_ENABLED", "true").lower() in ("true", "1", "yes")
SSH_ENABLED = os.getenv("SSH_ENABLED", "true").lower() in ("true", "1", "yes")
CHARTS_ENABLED = False  # Charts not included in open-source release

# Agent — natural language via Telegram (agentic with tool use)
AGENT_ENABLED: bool = os.getenv("AGENT_ENABLED", "true").lower() == "true"
AGENT_PROVIDER: str = os.getenv("AGENT_PROVIDER", "gemini")  # gemini | claude | openai | ollama
AGENT_TIMEOUT: int = int(os.getenv("AGENT_TIMEOUT", "120"))

# Agent API keys (for agentic mode — separate from CLI subscriptions)
CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Ollama (local LLM — OpenAI-compatible API, free)
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1")

# Default email domain for username→email resolution (e.g. "example.com")
# When set, usernames without @ are tried as username@DEFAULT_EMAIL_DOMAIN
DEFAULT_EMAIL_DOMAIN: str = os.getenv("DEFAULT_EMAIL_DOMAIN", "")

# Multi-tenancy (per-user data isolation)
# When false: all data visible to all users (single-user mode, default for dev VPS)
# When true: tasks, notes, routines, etc. scoped by user_id
MULTI_TENANT: bool = os.getenv("MULTI_TENANT", "false").lower() == "true"

# Access control — who can log in via OAuth (Google or Microsoft)
# ALLOWED_EMAILS: comma-separated list of exact emails (case-insensitive)
# ALLOWED_DOMAINS: comma-separated list of email domains (e.g. "example.com")
# If both are empty, anyone can log in (open access — not recommended for DeptTools).
# GOOGLE_ALLOWED_EMAIL (legacy) is merged into ALLOWED_EMAILS automatically.
ALLOWED_EMAILS: set[str] = {
    e.strip().lower()
    for e in os.getenv("ALLOWED_EMAILS", "").split(",")
    if e.strip()
} | GOOGLE_ALLOWED_EMAILS
ALLOWED_DOMAINS: set[str] = {
    d.strip().lower()
    for d in os.getenv("ALLOWED_DOMAINS", "").split(",")
    if d.strip()
}

# Auto-provisioning — when true, new users are created automatically on first OAuth login
# When false, user must be pre-created via CLI or admin before they can log in
AUTO_PROVISION: bool = os.getenv("AUTO_PROVISION", "true").lower() == "true"

# Dev access token (bypasses web auth for local tools like Playwright)
DEV_TOKEN: str = os.getenv("DEV_TOKEN", "")


# Dropbox + Otter.ai
DROPBOX_APP_KEY: str = os.getenv("DROPBOX_APP_KEY", "")
DROPBOX_APP_SECRET: str = os.getenv("DROPBOX_APP_SECRET", "")
DROPBOX_OTTER_FOLDER: str = os.getenv("DROPBOX_OTTER_FOLDER", "/Apps/Otter")
OTTER_POLL_INTERVAL: int = int(os.getenv("OTTER_POLL_INTERVAL", "120"))
OTTER_WEBHOOK_SECRET: str = os.getenv("OTTER_WEBHOOK_SECRET", "")
OTTER_SUMMARY_FOLDER: str = os.getenv("OTTER_SUMMARY_FOLDER", "/Otter")

# Lead Pipeline (external assessment framework webhook)
LEAD_WEBHOOK_SECRET: str = os.getenv("LEAD_WEBHOOK_SECRET", "")


# ImprovMX (email forwarding management)
IMPROVMX_API_KEY: str = os.getenv("IMPROVMX_API_KEY", "")
IMPROVMX_ENABLED: bool = bool(IMPROVMX_API_KEY)

# Namecheap (domain registrar API)
NAMECHEAP_API_USER: str = os.getenv("NAMECHEAP_API_USER", "")
NAMECHEAP_API_KEY: str = os.getenv("NAMECHEAP_API_KEY", "")
NAMECHEAP_USERNAME: str = os.getenv("NAMECHEAP_USERNAME", "") or NAMECHEAP_API_USER
NAMECHEAP_CLIENT_IP: str = os.getenv("NAMECHEAP_CLIENT_IP", "")
NAMECHEAP_SANDBOX: bool = os.getenv("NAMECHEAP_SANDBOX", "false").lower() == "true"
NAMECHEAP_ENABLED: bool = bool(NAMECHEAP_API_KEY and NAMECHEAP_API_USER)

# Cloudflare (DNS management via API v4)
CLOUDFLARE_API_TOKEN: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_API_TOKEN_SECONDARY: str = os.getenv("CLOUDFLARE_API_TOKEN_SECONDARY", "")
CLOUDFLARE_ENABLED: bool = bool(CLOUDFLARE_API_TOKEN)

# Feature flags for MCP tool groups
AI_ENABLED: bool = os.getenv("AI_ENABLED", "true").lower() == "true"
TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
NOTION_ENABLED: bool = os.getenv("NOTION_ENABLED", "true").lower() == "true"
INFRA_ENABLED: bool = os.getenv("INFRA_ENABLED", "true").lower() == "true"


def validate_config() -> None:
    """Log warnings for insecure default configuration values."""
    _log = logging.getLogger("roost.config")
    if SESSION_SECRET == "change-me-to-random-hex":
        _log.warning("SESSION_SECRET is still the default — set a random hex value in .env")
    if WEB_PASSWORD == "changeme":
        _log.warning("WEB_PASSWORD is still the default — set a strong password in .env")
    if DEV_TOKEN and len(DEV_TOKEN) < 32:
        _log.warning("DEV_TOKEN is shorter than 32 characters — use a longer random token")
