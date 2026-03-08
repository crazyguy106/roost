"""File handling, Google Drive, and Git command handlers."""

import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.executor import run_command
from roost.config import UPLOADS_DIR

# Directories allowed for /send and /ls commands
_ALLOWED_DIRS = [
    UPLOADS_DIR,
    "/home/dev/projects/",
    "/home/dev/downloads/",
    "/home/dev/documents/",
]


def _path_allowed(path: str) -> bool:
    """Check if a path is within allowed directories."""
    resolved = os.path.realpath(os.path.expanduser(path))
    return any(resolved.startswith(os.path.realpath(d)) for d in _ALLOWED_DIRS)


# ── File handling ────────────────────────────────────────────────────

@authorized
async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a VPS file to the user on Telegram."""
    if not context.args:
        await update.message.reply_text("Usage: /send /path/to/file")
        return

    file_path = " ".join(context.args)

    if not _path_allowed(file_path):
        await update.message.reply_text("Access denied: path outside allowed directories.")
        return

    if not os.path.isfile(file_path):
        await update.message.reply_text(f"File not found: {file_path}")
        return

    size = os.path.getsize(file_path)
    if size > 50 * 1024 * 1024:  # 50MB Telegram limit
        await update.message.reply_text(f"File too large ({size // 1024 // 1024}MB). Telegram limit is 50MB.")
        return

    await update.message.reply_document(
        document=open(file_path, "rb"),
        filename=os.path.basename(file_path),
    )


@authorized
async def cmd_ls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List files in a directory."""
    dir_path = " ".join(context.args) if context.args else UPLOADS_DIR

    if not _path_allowed(dir_path):
        await update.message.reply_text("Access denied: path outside allowed directories.")
        return

    if not os.path.isdir(dir_path):
        await update.message.reply_text(f"Not a directory: {dir_path}")
        return

    entries = sorted(os.listdir(dir_path))
    if not entries:
        await update.message.reply_text("(empty)")
        return

    lines = []
    for e in entries[:50]:
        full = os.path.join(dir_path, e)
        if os.path.isdir(full):
            lines.append(f"📁 {e}/")
        else:
            size = os.path.getsize(full)
            if size > 1024 * 1024:
                size_str = f"{size // 1024 // 1024}MB"
            elif size > 1024:
                size_str = f"{size // 1024}KB"
            else:
                size_str = f"{size}B"
            lines.append(f"📄 {e} ({size_str})")

    text = f"`{dir_path}`\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown")


@authorized
async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle files sent to the bot — save to uploads/."""
    doc = update.message.document
    if not doc:
        return

    Path(UPLOADS_DIR).mkdir(parents=True, exist_ok=True)
    file = await doc.get_file()
    safe_name = os.path.basename(doc.file_name)
    dest = os.path.join(UPLOADS_DIR, safe_name)
    await file.download_to_drive(dest)
    await update.message.reply_text(f"Saved: {dest}")


@authorized
async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos sent to the bot — save highest-resolution version to uploads/."""
    if not update.message.photo:
        return

    Path(UPLOADS_DIR).mkdir(parents=True, exist_ok=True)

    # Telegram sends multiple sizes — last is highest resolution
    photo = update.message.photo[-1]
    file = await photo.get_file()

    # Use file_unique_id for filename since photos don't have names
    ext = ".jpg"
    if file.file_path and "." in file.file_path:
        ext = os.path.splitext(file.file_path)[1]
    safe_name = f"photo_{photo.file_unique_id}{ext}"
    dest = os.path.join(UPLOADS_DIR, safe_name)

    await file.download_to_drive(dest)

    caption = update.message.caption or ""
    caption_info = f"\nCaption: {caption}" if caption else ""
    await update.message.reply_text(f"Saved: {dest}{caption_info}")


# ── Google Drive ─────────────────────────────────────────────────────

@authorized
async def cmd_gdrive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from roost.config import GOOGLE_ENABLED
    if not GOOGLE_ENABLED:
        await update.message.reply_text("Google Drive is not enabled on this instance.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/gdrive ls PATH\n/gdrive get PATH\n"
            "/gdrive put PATH\n/gdrive send PATH"
        )
        return

    user_id = update.effective_user.id
    subcmd = context.args[0].lower()
    path = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    if subcmd == "ls":
        remote_path = f"gdrive:{path}" if path else "gdrive:"
        output = await run_command(
            ["rclone", "lsf", remote_path],
            timeout=60, source="telegram:gdrive", user_id=str(user_id),
        )
        await update.message.reply_text(output or "(empty)")

    elif subcmd == "get":
        if not path:
            await update.message.reply_text("Usage: /gdrive get PATH")
            return
        await update.message.reply_text(f"Downloading {path}...")
        output = await run_command(
            ["rclone", "copy", f"gdrive:{path}", "/home/dev/downloads/"],
            timeout=300, source="telegram:gdrive", user_id=str(user_id),
        )
        msg = output if output != "(no output)" else f"Downloaded to /home/dev/downloads/"
        await update.message.reply_text(msg)

    elif subcmd == "put":
        if not path:
            await update.message.reply_text("Usage: /gdrive put /local/path")
            return
        if not os.path.exists(path):
            await update.message.reply_text(f"File not found: {path}")
            return
        await update.message.reply_text(f"Uploading {path}...")
        output = await run_command(
            ["rclone", "copy", path, "gdrive:"],
            timeout=300, source="telegram:gdrive", user_id=str(user_id),
        )
        msg = output if output != "(no output)" else f"Uploaded {os.path.basename(path)} to Drive root"
        await update.message.reply_text(msg)

    elif subcmd == "send":
        if not path:
            await update.message.reply_text("Usage: /gdrive send PATH")
            return
        await update.message.reply_text(f"Fetching {path} from Drive...")
        filename = os.path.basename(path)
        local_dest = f"/tmp/{filename}"
        await run_command(
            ["rclone", "copy", f"gdrive:{path}", "/tmp/"],
            timeout=300, source="telegram:gdrive", user_id=str(user_id),
        )
        if os.path.isfile(local_dest):
            await update.message.reply_document(
                document=open(local_dest, "rb"),
                filename=filename,
            )
            os.remove(local_dest)
        else:
            await update.message.reply_text(f"Failed to download {path}")

    else:
        await update.message.reply_text("Unknown subcommand. Use: ls, get, put, send")


# ── Git commands ─────────────────────────────────────────────────────

@authorized
async def cmd_git(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage:\n/git status\n/git log\n/git diff")
        return

    user_id = update.effective_user.id
    subcmd = context.args[0].lower()
    repo_path = "/app"
    if len(context.args) > 1 and context.args[-1].startswith("/"):
        repo_path = context.args[-1]

    if subcmd == "status":
        cmd = ["git", "-C", repo_path, "status", "--short"]
    elif subcmd == "log":
        cmd = ["git", "-C", repo_path, "log", "--oneline", "-20"]
    elif subcmd == "diff":
        cmd = ["git", "-C", repo_path, "diff", "--stat"]
    else:
        await update.message.reply_text("Unknown git command. Use: status, log, diff")
        return

    output = await run_command(cmd, timeout=30, source="telegram:git", user_id=str(user_id))
    await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")


@authorized
async def cmd_commit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /commit Your commit message here")
        return

    user_id = update.effective_user.id
    message = " ".join(context.args)
    repo_path = "/app"

    await run_command(
        ["git", "-C", repo_path, "add", "-A"],
        timeout=15, source="telegram:git", user_id=str(user_id),
    )
    output = await run_command(
        ["git", "-C", repo_path, "commit", "-m", message],
        timeout=30, source="telegram:git", user_id=str(user_id),
    )
    await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")
