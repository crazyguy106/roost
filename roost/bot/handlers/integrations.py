"""Integration command handlers: Gmail, Notion, Otter, Voice + base class."""

import logging
from abc import ABC, abstractmethod

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized

logger = logging.getLogger(__name__)


# ── IntegrationCommand base class ────────────────────────────────────

class IntegrationCommand(ABC):
    """Base class for integration commands (gmail, notion, otter, voice).

    Eliminates duplicated try/except + availability + subcommand dispatch.
    """
    name: str
    module_name: str

    @abstractmethod
    async def check_available(self) -> tuple[bool, str | None]:
        """Return (is_available, reason_if_not)."""
        ...

    @abstractmethod
    def setup_instructions(self, reason: str | None) -> str:
        """Markdown setup instructions when not available."""
        ...

    @abstractmethod
    async def handle_subcommand(self, subcmd: str, args: list, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handle a subcommand. Return True if handled, False to fall through to status."""
        ...

    @abstractmethod
    async def status_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
        """Return the status text (Markdown) for the default (no-subcommand) case."""
        ...

    def as_handler(self):
        """Return an @authorized async function suitable for CommandHandler."""
        integration = self

        @authorized
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                available, reason = await integration.check_available()
                if not available:
                    await update.message.reply_text(
                        integration.setup_instructions(reason), parse_mode="Markdown")
                    return
                if context.args:
                    if await integration.handle_subcommand(
                            context.args[0].lower(), context.args[1:], update, context):
                        return
                text = await integration.status_text(update, context)
                await update.message.reply_text(text, parse_mode="Markdown")
            except ImportError:
                await update.message.reply_text(f"{integration.module_name} not available.")
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")

        return handler


# ── Gmail ────────────────────────────────────────────────────────────

class GmailCmd(IntegrationCommand):
    name = "gmail"
    module_name = "Gmail module"

    async def check_available(self):
        from roost.gmail import is_gmail_available
        from roost.gmail.client import get_stored_refresh_token

        has_token = bool(get_stored_refresh_token())
        if not has_token:
            return False, "no_token"
        available = is_gmail_available()
        if not available:
            return False, "disabled"
        return True, None

    def setup_instructions(self, reason):
        if reason == "no_token":
            return (
                "*Gmail Integration*\n\n"
                "Status: No OAuth token\n\n"
                "To set up:\n"
                "1. Visit your web UI → `/auth/gmail`\n"
                "2. Authorize with Google\n"
                "3. Set `GMAIL_ENABLED=true` in .env\n"
                "4. Restart"
            )
        return (
            "*Gmail Integration*\n\n"
            "Status: Token stored but disabled\n\n"
            "Set `GMAIL_ENABLED=true` in .env and restart."
        )

    async def handle_subcommand(self, subcmd, args, update, context):
        if subcmd == "digest":
            from roost.gmail.service import send_digest
            from roost.config import GMAIL_SEND_FROM
            ok = send_digest(GMAIL_SEND_FROM)
            if ok:
                await update.message.reply_text(f"Digest sent to {GMAIL_SEND_FROM}")
            else:
                await update.message.reply_text("Failed to send digest.")
            return True

        if subcmd == "sync":
            from roost.gmail.calendar_write import sync_task_deadlines
            stats = sync_task_deadlines()
            await update.message.reply_text(
                f"Calendar sync:\n"
                f"  Created: {stats['created']}\n"
                f"  Skipped: {stats['skipped']}\n"
                f"  Errors: {stats['errors']}"
            )
            return True

        if subcmd == "poll":
            from roost.gmail.poller import poll_inbox
            created = poll_inbox()
            await update.message.reply_text(f"Inbox poll: {created} items created.")
            return True

        return False

    async def status_text(self, update, context):
        from roost.config import GMAIL_SEND_FROM
        lines = ["*Gmail Integration*\n"]
        lines.append("Status: ✅ Active")
        lines.append(f"Send from: {GMAIL_SEND_FROM}")
        lines.append("\n*Commands:*")
        lines.append("/gmail digest — Send daily digest email")
        lines.append("/gmail sync — Sync deadlines to Google Calendar")
        lines.append("/gmail poll — Check inbox for [task]/[note] emails")
        return "\n".join(lines)


# ── Notion ───────────────────────────────────────────────────────────

class NotionCmd(IntegrationCommand):
    name = "notion"
    module_name = "Notion sync module"

    async def check_available(self):
        from roost.notion import is_notion_available
        available = is_notion_available()
        return available, None if available else "disabled"

    def setup_instructions(self, reason):
        return (
            "*Notion Sync*\n\n"
            "Status: Disabled\n\n"
            "To enable, set in .env:\n"
            "```\nNOTION_API_TOKEN=your_token\n"
            "NOTION_SYNC_ENABLED=true\n```"
        )

    async def handle_subcommand(self, subcmd, args, update, context):
        if subcmd == "export":
            await update.message.reply_text("Running bulk export to Notion...")
            try:
                from roost.notion.sync import bulk_export_to_notion
                stats = bulk_export_to_notion()
                await update.message.reply_text(
                    f"*Bulk Export Complete*\n\n"
                    f"Items pushed: {stats.get('pushed', 0)}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await update.message.reply_text(f"Bulk export failed: {e}")
            return True

        if subcmd == "sync-programmes":
            await update.message.reply_text("Syncing all programmes to Notion...")
            try:
                from roost.notion.sync import bulk_export_curricula_to_notion
                stats = bulk_export_curricula_to_notion()
                await update.message.reply_text(
                    f"*Programme Sync Complete*\n\n"
                    f"Programmes pushed: {stats['programmes']}\n"
                    f"Modules pushed: {stats['modules']}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await update.message.reply_text(f"Programme sync failed: {e}")
            return True

        return False

    async def status_text(self, update, context):
        from roost.database import get_connection
        conn = get_connection()
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM notion_sync_log WHERE status = 'pending'"
        ).fetchone()["cnt"]
        failed = conn.execute(
            "SELECT COUNT(*) as cnt FROM notion_sync_log WHERE status = 'failed'"
        ).fetchone()["cnt"]
        states = conn.execute(
            "SELECT table_name, last_synced_at FROM notion_sync_state"
        ).fetchall()

        # Programme sync status
        programme_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM curricula WHERE notion_page_id IS NOT NULL"
        ).fetchone()["cnt"]
        total_programmes = conn.execute(
            "SELECT COUNT(*) as cnt FROM curricula WHERE is_active = 1"
        ).fetchone()["cnt"]
        module_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM curriculum_modules WHERE notion_page_id IS NOT NULL"
        ).fetchone()["cnt"]
        total_modules = conn.execute(
            "SELECT COUNT(*) as cnt FROM curriculum_modules"
        ).fetchone()["cnt"]
        conn.close()

        lines = ["*Notion Sync*\n"]
        lines.append("Connection: ✅ Active")
        lines.append(f"Pending pushes: {pending}")
        if failed:
            lines.append(f"Failed: {failed}")

        lines.append(f"\n*Programmes:* {programme_count}/{total_programmes} synced")
        lines.append(f"*Modules:* {module_count}/{total_modules} synced")

        if states:
            lines.append("\n*Last sync:*")
            for s in states:
                lines.append(f"  {s['table_name']}: {s['last_synced_at'] or 'never'}")
        else:
            lines.append("\nNo sync history yet.")

        lines.append("\n*Commands:*")
        lines.append("/notion export — Bulk export all data to Notion")
        lines.append("/notion sync-programmes — Sync curricula to Notion")
        return "\n".join(lines)


# ── Otter ────────────────────────────────────────────────────────────

class OtterCmd(IntegrationCommand):
    name = "otter"
    module_name = "Dropbox module"

    async def check_available(self):
        from roost.dropbox_client import is_dropbox_available
        available = is_dropbox_available()
        return available, None if available else "disabled"

    def setup_instructions(self, reason):
        return (
            "*Dropbox + Otter.ai*\n\n"
            "Status: Not configured\n\n"
            "To enable, set in .env:\n"
            "```\nDROPBOX_APP_KEY=your_key\n"
            "DROPBOX_APP_SECRET=your_secret\n```\n"
            "Then run: `python -m roost.dropbox_client`"
        )

    async def handle_subcommand(self, subcmd, args, update, context):
        return False  # No subcommands for otter

    async def status_text(self, update, context):
        from roost.config import DROPBOX_OTTER_FOLDER, OTTER_POLL_INTERVAL
        from roost.database import get_connection
        conn = get_connection()
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM otter_pending WHERE status = 'pending'"
        ).fetchone()["cnt"]
        done = conn.execute(
            "SELECT COUNT(*) as cnt FROM otter_pending WHERE status = 'done'"
        ).fetchone()["cnt"]
        failed = conn.execute(
            "SELECT COUNT(*) as cnt FROM otter_pending WHERE status = 'failed'"
        ).fetchone()["cnt"]
        conn.close()

        lines = ["*Dropbox + Otter.ai*\n"]
        lines.append("Connection: ✅ Active")
        lines.append(f"Folder: `{DROPBOX_OTTER_FOLDER}`")
        lines.append(f"Poll interval: {OTTER_POLL_INTERVAL}s")
        lines.append(f"\nPending transcripts: {pending}")
        if done:
            lines.append(f"Completed: {done}")
        if failed:
            lines.append(f"Failed: {failed}")
        lines.append("\n_Voice notes auto-upload to Otter via Dropbox._")
        return "\n".join(lines)


# ── Voice status ─────────────────────────────────────────────────────

class VoiceStatusCmd(IntegrationCommand):
    name = "voice"
    module_name = "faster-whisper"

    async def check_available(self):
        # Voice is always "available" if the module imports
        try:
            import roost.voice  # noqa: F401
            return True, None
        except ImportError:
            return False, "not_installed"

    def setup_instructions(self, reason):
        return (
            "faster-whisper not installed.\n"
            "Install: pip install faster-whisper"
        )

    async def handle_subcommand(self, subcmd, args, update, context):
        return False  # No subcommands for voice status

    async def status_text(self, update, context):
        import roost.voice as v

        if v._model is not None:
            status = "✅ Loaded (in memory)"
        else:
            status = "💤 Standby (model not loaded)"

        lines = ["*Voice Transcription*\n"]
        lines.append(f"Status: {status}")
        lines.append("Model: base (INT8, CPU)")
        lines.append(f"Auto-unload: {v._UNLOAD_SECONDS}s idle")
        lines.append("\n_Send a voice message to transcribe._")
        lines.append("_Prefix with \"task:\" to create a task._")
        return "\n".join(lines)


# Instantiate handlers at module level
cmd_gmail = GmailCmd().as_handler()
cmd_notion = NotionCmd().as_handler()
cmd_otter = OtterCmd().as_handler()
cmd_voice = VoiceStatusCmd().as_handler()
