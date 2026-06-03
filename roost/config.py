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

# Browserless / Chromium sidecar
# Internal URL: how Roost reaches the sidecar inside the docker network.
# Public URL:   how a HUMAN reaches it (used to render live debug URLs in
# Telegram prompts when an RPA flow pauses for Singpass / 2FA / captcha).
# Laptop default = loopback; cloud deploys must set SIDECAR_PUBLIC_URL to
# whatever they tunnel / reverse-proxy to.
SIDECAR_INTERNAL_HTTP_URL: str = os.getenv("SIDECAR_INTERNAL_HTTP_URL", "http://chromium:3000")
SIDECAR_PUBLIC_URL: str = os.getenv("SIDECAR_PUBLIC_URL", "http://localhost:3000")

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

# Operator email — used for high-priority alerts (hot-lead notifications).
# Falls back silently if unset or Gmail not configured.
OPERATOR_EMAIL: str = os.getenv("OPERATOR_EMAIL", "")

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

# Daily summary — prepend a short AI narrative + flags above the deterministic counts.
# Fails closed: if Gemini call errors or no API key, deterministic summary still goes out.
AI_SUMMARY_ENABLED: bool = os.getenv("AI_SUMMARY_ENABLED", "true").lower() == "true"

# Curriculum scanner (seeds programme data from YAML files)
CURRICULUM_ENABLED: bool = os.getenv("CURRICULUM_ENABLED", "true").lower() == "true"

# Modular deployment feature flags
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in ("true", "1", "yes")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() in ("true", "1", "yes")
NOTION_ENABLED = os.getenv("NOTION_ENABLED", "false").lower() in ("true", "1", "yes")
INFRA_ENABLED = os.getenv("INFRA_ENABLED", "true").lower() in ("true", "1", "yes")
SSH_ENABLED = os.getenv("SSH_ENABLED", "true").lower() in ("true", "1", "yes")
CHARTS_ENABLED = False  # Charts not included in open-source release

# Guardian AI — pre-flight safety checks on tool calls
GUARDIAN_ENABLED = os.getenv("GUARDIAN_ENABLED", "true").lower() in ("true", "1", "yes")

# Cost tracking — per-run and daily token cost limits
MAX_COST_PER_RUN: float = float(os.getenv("MAX_COST_PER_RUN", "0.50"))
MAX_DAILY_COST: float = float(os.getenv("MAX_DAILY_COST", "5.00"))

# Proactive monitoring — push alerts for risks and events
PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "true").lower() in ("true", "1", "yes")
PROACTIVE_RISK_INTERVAL: int = int(os.getenv("PROACTIVE_RISK_INTERVAL", "300"))  # seconds
PROACTIVE_CALENDAR_PREP: int = int(os.getenv("PROACTIVE_CALENDAR_PREP", "30"))   # minutes before event

# Autonomy level — supervised | assisted | autonomous
# supervised: confirm every external action
# assisted: confirm destructive only (default, current behaviour)
# autonomous: no confirmation (max speed, max risk)
AUTONOMY_LEVEL: str = os.getenv("AUTONOMY_LEVEL", "assisted")

# Agent — natural language via Telegram (agentic with tool use)
AGENT_ENABLED: bool = os.getenv("AGENT_ENABLED", "true").lower() == "true"
AGENT_PROVIDER: str = os.getenv("AGENT_PROVIDER", "gemini")  # gemini | claude | claude_cli | gemini_cli | codex_cli | openai | ollama
AGENT_TIMEOUT: int = int(os.getenv("AGENT_TIMEOUT", "120"))

# Agentic Workflow surface (Phase 1 — plan + per-tool event streaming on /agentic).
# See docs/agentic-workflow-phase1.md.
AGENTIC_WORKFLOW_ENABLED: bool = os.getenv("AGENTIC_WORKFLOW_ENABLED", "false").lower() == "true"
# Planner model — empty string means "use the executor model from AGENT_PROVIDER".
AGENTIC_PLANNER_MODEL: str = os.getenv("AGENTIC_PLANNER_MODEL", "")

# Background agent limits
MAX_BACKGROUND_RUNS: int = int(os.getenv("MAX_BACKGROUND_RUNS", "3"))
MAX_BACKGROUND_DURATION: int = int(os.getenv("MAX_BACKGROUND_DURATION", "300"))  # seconds

# Agent API keys (for agentic mode — separate from CLI subscriptions)
CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Claude Code CLI provider (AGENT_PROVIDER=claude_cli)
# Runs `claude -p` as a subprocess. Auth via Claude subscription, not API key.
# Roost's MCP tools exposed via --mcp-config when CLAUDE_CLI_MCP_CONFIG is set.
CLAUDE_CLI_BIN: str = os.getenv("CLAUDE_CLI_BIN", "claude")
CLAUDE_CLI_MODEL: str = os.getenv("CLAUDE_CLI_MODEL", "")  # empty = CLI default
CLAUDE_CLI_MCP_CONFIG: str = os.getenv("CLAUDE_CLI_MCP_CONFIG", "")
CLAUDE_CLI_PERMISSION_MODE: str = os.getenv("CLAUDE_CLI_PERMISSION_MODE", "bypassPermissions")
CLAUDE_CLI_TIMEOUT: int = int(os.getenv("CLAUDE_CLI_TIMEOUT", "300"))
CLAUDE_CLI_SESSION_FILE: str = os.getenv("CLAUDE_CLI_SESSION_FILE", "")

# Gemini CLI provider (AGENT_PROVIDER=gemini_cli)
# Runs `gemini -p` as a subprocess. Auth via ~/.gemini/ state (Google OAuth via
# `gemini /auth`) or GEMINI_API_KEY env. MCP servers read from ~/.gemini/settings.json.
GEMINI_CLI_BIN: str = os.getenv("GEMINI_CLI_BIN", "gemini")
GEMINI_CLI_MODEL: str = os.getenv("GEMINI_CLI_MODEL", "")
GEMINI_CLI_MCP_CONFIG: str = os.getenv("GEMINI_CLI_MCP_CONFIG", "")
GEMINI_CLI_PERMISSION_MODE: str = os.getenv("GEMINI_CLI_PERMISSION_MODE", "yolo")
GEMINI_CLI_TIMEOUT: int = int(os.getenv("GEMINI_CLI_TIMEOUT", "300"))
GEMINI_CLI_SESSION_FILE: str = os.getenv("GEMINI_CLI_SESSION_FILE", "")

# Codex CLI provider (AGENT_PROVIDER=codex_cli) — SCAFFOLD, untested
# Runs `codex exec --json` as a subprocess. Auth via ~/.codex/ (ChatGPT Plus/Pro
# subscription via `codex login`) or OPENAI_API_KEY env. MCP via ~/.codex/config.toml.
CODEX_CLI_BIN: str = os.getenv("CODEX_CLI_BIN", "codex")
CODEX_CLI_MODEL: str = os.getenv("CODEX_CLI_MODEL", "")
CODEX_CLI_MCP_CONFIG: str = os.getenv("CODEX_CLI_MCP_CONFIG", "")
CODEX_CLI_PERMISSION_MODE: str = os.getenv("CODEX_CLI_PERMISSION_MODE", "full-auto")
CODEX_CLI_TIMEOUT: int = int(os.getenv("CODEX_CLI_TIMEOUT", "300"))
CODEX_CLI_SESSION_FILE: str = os.getenv("CODEX_CLI_SESSION_FILE", "")

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

# Demo magic-link token. When set, ?demo=<token> on any URL mints a session
# cookie as the owner and redirects to a clean URL. Treat like a password.
DEMO_ACCESS_TOKEN: str = os.getenv("DEMO_ACCESS_TOKEN", "")


# Dropbox + Otter.ai
DROPBOX_APP_KEY: str = os.getenv("DROPBOX_APP_KEY", "")
DROPBOX_APP_SECRET: str = os.getenv("DROPBOX_APP_SECRET", "")
DROPBOX_OTTER_FOLDER: str = os.getenv("DROPBOX_OTTER_FOLDER", "/Apps/Otter")
OTTER_POLL_INTERVAL: int = int(os.getenv("OTTER_POLL_INTERVAL", "120"))
OTTER_WEBHOOK_SECRET: str = os.getenv("OTTER_WEBHOOK_SECRET", "")
OTTER_SUMMARY_FOLDER: str = os.getenv("OTTER_SUMMARY_FOLDER", "/Otter")

# Lead Pipeline (external assessment framework webhook)
LEAD_WEBHOOK_SECRET: str = os.getenv("LEAD_WEBHOOK_SECRET", "")

# WhatsApp Cloud API (Meta Business Platform)
WHATSAPP_ENABLED: bool = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")

# Chatwoot (self-hosted helpdesk — FA edition fronts WhatsApp via a Chatwoot
# inbox). Inbound: Chatwoot fires HMAC-signed webhooks to /api/chatwoot/webhook.
# Outbound: REST API with api_access_token header. CHATWOOT_WEBHOOK_SECRET is
# the per-webhook secret from Chatwoot UI (rotates if the webhook row is
# recreated); CHATWOOT_API_KEY is the user-level access token.
# Web tty (FA-edition operator surface). The sweeper kills tmux windows
# whose `chat_windows.last_active_at` is older than TTY_IDLE_TTL_MINUTES
# (default 6 hours — full work-day stays live, overnight gets cleaned up).
# TTY_MEMORY_PRESSURE_RATIO is the cgroup memory.current/.max threshold
# above which the sweeper starts evicting coldest windows.
TTY_IDLE_TTL_MINUTES: int = int(os.getenv("TTY_IDLE_TTL_MINUTES", "360"))
TTY_MEMORY_PRESSURE_RATIO: float = float(os.getenv("TTY_MEMORY_PRESSURE_RATIO", "0.85"))

CHATWOOT_ENABLED: bool = os.getenv("CHATWOOT_ENABLED", "false").lower() == "true"
CHATWOOT_URL: str = os.getenv("CHATWOOT_URL", "").rstrip("/")
CHATWOOT_API_KEY: str = os.getenv("CHATWOOT_API_KEY", "")
CHATWOOT_ACCOUNT_ID: str = os.getenv("CHATWOOT_ACCOUNT_ID", "")
CHATWOOT_INBOX_ID: str = os.getenv("CHATWOOT_INBOX_ID", "")
CHATWOOT_WEBHOOK_SECRET: str = os.getenv("CHATWOOT_WEBHOOK_SECRET", "")

# SMS (outbound) — vendor-agnostic dispatch via SMS_PROVIDER.
# Only Twilio is implemented today; flag-gated so adapter fails closed when off.
SMS_ENABLED: bool = os.getenv("SMS_ENABLED", "false").lower() == "true"
SMS_PROVIDER: str = os.getenv("SMS_PROVIDER", "twilio")
TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")

# Property-Agent Toolkit (Singapore) — master flag for the sidebar group + pages.
# Stamp-duty calculator is pure-Python and gated only by this flag.
# DNC and CDD have their own sub-flags below for the external API integrations.
PROPERTY_AGENT_ENABLED: bool = os.getenv("PROPERTY_AGENT_ENABLED", "true").lower() == "true"

# CRM adapter — vendor-agnostic dispatch. "local" wraps Roost's own contacts table.
# Other supported: attio | hubspot | zoho | salesforce | pipedrive
CRM_PROVIDER: str = os.getenv("CRM_PROVIDER", "local")

# Attio
ATTIO_API_KEY: str = os.getenv("ATTIO_API_KEY", "")
ATTIO_BASE_URL: str = os.getenv("ATTIO_BASE_URL", "https://api.attio.com/v2")
ATTIO_OBJECT_PEOPLE: str = os.getenv("ATTIO_OBJECT_PEOPLE", "people")
ATTIO_OBJECT_COMPANIES: str = os.getenv("ATTIO_OBJECT_COMPANIES", "companies")
ATTIO_OBJECT_DEALS: str = os.getenv("ATTIO_OBJECT_DEALS", "deals")
ATTIO_WEBHOOK_SECRET: str = os.getenv("ATTIO_WEBHOOK_SECRET", "")

# HubSpot
HUBSPOT_ACCESS_TOKEN: str = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
HUBSPOT_BASE_URL: str = os.getenv("HUBSPOT_BASE_URL", "https://api.hubapi.com")

# Zoho CRM (OAuth — refresh token persists, access token refreshed hourly)
ZOHO_CLIENT_ID: str = os.getenv("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET: str = os.getenv("ZOHO_CLIENT_SECRET", "")
ZOHO_REFRESH_TOKEN: str = os.getenv("ZOHO_REFRESH_TOKEN", "")
ZOHO_ACCOUNTS_URL: str = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
ZOHO_API_DOMAIN: str = os.getenv("ZOHO_API_DOMAIN", "https://www.zohoapis.com")
ZOHO_REDIRECT_URI: str = os.getenv("ZOHO_REDIRECT_URI", "")
ZOHO_OAUTH_SCOPES: str = os.getenv(
    "ZOHO_OAUTH_SCOPES",
    "ZohoCRM.modules.ALL,ZohoCRM.users.READ,ZohoCRM.notification.ALL",
)

# Salesforce (OAuth username-password or refresh token)
SALESFORCE_INSTANCE_URL: str = os.getenv("SALESFORCE_INSTANCE_URL", "")
SALESFORCE_ACCESS_TOKEN: str = os.getenv("SALESFORCE_ACCESS_TOKEN", "")
SALESFORCE_API_VERSION: str = os.getenv("SALESFORCE_API_VERSION", "v59.0")

# Pipedrive
PIPEDRIVE_API_TOKEN: str = os.getenv("PIPEDRIVE_API_TOKEN", "")
PIPEDRIVE_BASE_URL: str = os.getenv("PIPEDRIVE_BASE_URL", "https://api.pipedrive.com/v1")

# CDD Screening (sanctions / PEP / adverse media)
# Vendor: "complyadvantage" | "acuris" | "refinitiv". Only complyadvantage
# is implemented today; the others raise NotImplementedError.
CDD_ENABLED: bool = os.getenv("CDD_ENABLED", "false").lower() == "true"
CDD_VENDOR: str = os.getenv("CDD_VENDOR", "complyadvantage")
CDD_API_KEY: str = os.getenv("CDD_API_KEY", "")
CDD_API_BASE_URL: str = os.getenv("CDD_API_BASE_URL", "")  # vendor default if empty
CDD_REFRESH_DAYS: int = int(os.getenv("CDD_REFRESH_DAYS", "30"))

# PDPC DNC Registry (Singapore Do-Not-Call)
DNC_ENABLED: bool = os.getenv("DNC_ENABLED", "false").lower() == "true"
DNC_API_BASE_URL: str = os.getenv("DNC_API_BASE_URL", "https://www.dnc.gov.sg/api/v2")
DNC_API_KEY: str = os.getenv("DNC_API_KEY", "")
DNC_ORG_ID: str = os.getenv("DNC_ORG_ID", "")

# WeChat Official Account API (Tencent)
WECHAT_ENABLED: bool = os.getenv("WECHAT_ENABLED", "false").lower() == "true"
WECHAT_APP_ID: str = os.getenv("WECHAT_APP_ID", "")
WECHAT_APP_SECRET: str = os.getenv("WECHAT_APP_SECRET", "")
WECHAT_TOKEN: str = os.getenv("WECHAT_TOKEN", "")
WECHAT_ENCODING_AES_KEY: str = os.getenv("WECHAT_ENCODING_AES_KEY", "")

# Discord Bot
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_ALLOWED_USERS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("DISCORD_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
]

# Slack Bot (Socket Mode)
SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")
SLACK_ALLOWED_USERS: list[str] = [
    uid.strip()
    for uid in os.getenv("SLACK_ALLOWED_USERS", "").split(",")
    if uid.strip()
]

# Signal Bot (via signal-cli-rest-api)
SIGNAL_API_URL: str = os.getenv("SIGNAL_API_URL", "")
SIGNAL_PHONE_NUMBER: str = os.getenv("SIGNAL_PHONE_NUMBER", "")
SIGNAL_ALLOWED_NUMBERS: list[str] = [
    n.strip()
    for n in os.getenv("SIGNAL_ALLOWED_NUMBERS", "").split(",")
    if n.strip()
]

# Matrix Bot (via matrix-nio)
MATRIX_HOMESERVER: str = os.getenv("MATRIX_HOMESERVER", "")
MATRIX_USER_ID: str = os.getenv("MATRIX_USER_ID", "")
MATRIX_ACCESS_TOKEN: str = os.getenv("MATRIX_ACCESS_TOKEN", "")
MATRIX_PASSWORD: str = os.getenv("MATRIX_PASSWORD", "")
MATRIX_ALLOWED_USERS: list[str] = [
    uid.strip()
    for uid in os.getenv("MATRIX_ALLOWED_USERS", "").split(",")
    if uid.strip()
]


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

# SME Ops vertical bundle — ERP/integration toolkit for small/medium businesses.
# The bundle ships with a universal Zapier ingress so users can wire in any of
# Zapier's 6,000+ apps before native adapters land. Per-app adapter flags
# (XERO_ENABLED, SHOPIFY_ENABLED, etc.) will be added in Phase 1+.
SME_OPS_ENABLED: bool = os.getenv("SME_OPS_ENABLED", "false").lower() == "true"
ZAPIER_ENABLED: bool = os.getenv("ZAPIER_ENABLED", "false").lower() == "true"
ZAPIER_INGRESS_TOKEN: str = os.getenv("ZAPIER_INGRESS_TOKEN", "")
ZAPIER_OUTBOUND_URL: str = os.getenv("ZAPIER_OUTBOUND_URL", "")

# SME Ops native adapters (Phase 1A — read-only)
STRIPE_ENABLED: bool = os.getenv("STRIPE_ENABLED", "false").lower() == "true"
STRIPE_API_KEY: str = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
SHOPIFY_ENABLED: bool = os.getenv("SHOPIFY_ENABLED", "false").lower() == "true"
SHOPIFY_STORE_DOMAIN: str = os.getenv("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_ACCESS_TOKEN: str = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_WEBHOOK_SECRET: str = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
XERO_ENABLED: bool = os.getenv("XERO_ENABLED", "false").lower() == "true"
XERO_PAT: str = os.getenv("XERO_PAT", "")
XERO_TENANT_ID: str = os.getenv("XERO_TENANT_ID", "")
# Phase 1B — OAuth2 + webhook
XERO_CLIENT_ID: str = os.getenv("XERO_CLIENT_ID", "")
XERO_CLIENT_SECRET: str = os.getenv("XERO_CLIENT_SECRET", "")
XERO_REDIRECT_URI: str = os.getenv("XERO_REDIRECT_URI", "")
XERO_WEBHOOK_KEY: str = os.getenv("XERO_WEBHOOK_KEY", "")
ROOST_DOMAIN: str = os.getenv("ROOST_DOMAIN", "")

# Feature flags for MCP tool groups
AI_ENABLED: bool = os.getenv("AI_ENABLED", "true").lower() == "true"
TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
NOTION_ENABLED: bool = os.getenv("NOTION_ENABLED", "true").lower() == "true"
INFRA_ENABLED: bool = os.getenv("INFRA_ENABLED", "true").lower() == "true"

# ── Bundle master flags ──────────────────────────────────────────────
# Each vertical bundle under roost/extras/ is gated by one master flag.
# When false, the bundle's services / MCP tools / web routes / templates
# / bot handlers / DB tables are all skipped at import time. Per-adapter
# sub-flags (STRIPE_ENABLED, CDD_ENABLED, etc.) only matter when the
# parent bundle is enabled.
#
# SME_OPS_ENABLED + PROPERTY_AGENT_ENABLED are defined above with the
# bundle-specific config; mirrored here for the registry.
CRM_ENABLED: bool = os.getenv("CRM_ENABLED", "true").lower() == "true"
LEAD_NURTURE_ENABLED: bool = os.getenv("LEAD_NURTURE_ENABLED", "true").lower() == "true"
RPA_ENABLED: bool = os.getenv("RPA_ENABLED", "true").lower() == "true"
MESSAGING_EXTERNAL_ENABLED: bool = os.getenv("MESSAGING_EXTERNAL_ENABLED", "true").lower() == "true"


def validate_config() -> None:
    """Log warnings for insecure default configuration values."""
    _log = logging.getLogger("roost.config")
    if SESSION_SECRET == "change-me-to-random-hex":
        _log.warning("SESSION_SECRET is still the default — set a random hex value in .env")
    if WEB_PASSWORD == "changeme":
        _log.warning("WEB_PASSWORD is still the default — set a strong password in .env")
    if DEV_TOKEN and len(DEV_TOKEN) < 32:
        _log.warning("DEV_TOKEN is shorter than 32 characters — use a longer random token")
