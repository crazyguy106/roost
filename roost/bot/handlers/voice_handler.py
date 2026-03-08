"""Voice note handler — transcribe + create note/task/journal + Otter upload."""

import logging
import os
import re
import threading

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.models import TaskCreate, NoteCreate
from roost import task_service

logger = logging.getLogger(__name__)

# --- Voice command prefix detection ---
# Two layers: fast regex for known patterns, then Levenshtein fallback
# for garbles we haven't seen yet.

# Detect whether whisper is available — fall back to Gemini if not
try:
    from roost.voice import transcribe_telegram_voice  # noqa: F401
    _USE_GEMINI_TRANSCRIPTION = False
except ImportError:
    _USE_GEMINI_TRANSCRIPTION = True

# Layer 1: Regex for known speech-to-text garbles
_PREFIX_TASK = re.compile(
    r"^(?:\[task\]|task\s*[:.,;]|tasks?\s*[:,.]|tas\s+kohlen\s*[,.]?)\s*",
    re.IGNORECASE,
)
_PREFIX_JOURNAL = re.compile(
    r"^(?:\[journal\]|journal\s*[:.,;])\s*",
    re.IGNORECASE,
)
_PREFIX_NOTE = re.compile(
    r"^(?:\[note\]|note\s*[:.,;])\s*",
    re.IGNORECASE,
)
_PREFIX_DECK = re.compile(
    r"^(?:\[deck\]|deck\s*[:.,;])\s*",
    re.IGNORECASE,
)
_PREFIX_MEETING = re.compile(
    r"^(?:\[meeting\]|meeting\s*[:.,;]|meeting\s+notes?\s*[:.,;]?)\s*",
    re.IGNORECASE,
)

# Layer 2: Levenshtein distance for unknown garbles
_COMMAND_WORDS = {"task": "task", "journal": "journal", "note": "note", "deck": "deck", "meeting": "meeting"}
_MAX_EDIT_DISTANCE = {"task": 2, "journal": 3, "note": 2, "deck": 2, "meeting": 3}


def _levenshtein(s: str, t: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s) < len(t):
        return _levenshtein(t, s)
    if not t:
        return len(s)
    prev = list(range(len(t) + 1))
    for i, sc in enumerate(s):
        curr = [i + 1]
        for j, tc in enumerate(t):
            curr.append(min(
                prev[j + 1] + 1,       # deletion
                curr[j] + 1,            # insertion
                prev[j] + (sc != tc),   # substitution
            ))
        prev = curr
    return prev[-1]


def _detect_voice_command(text: str) -> tuple[str | None, str]:
    """Detect command prefix from transcribed text.

    Returns (command, content) where command is 'task'/'journal'/'note'/'deck'/'meeting'/None
    and content is the text after the prefix.

    Uses regex first (fast path), then Levenshtein fallback on the first
    word(s) for garbles we haven't seen before.
    """
    # Fast path: regex
    for cmd, pattern in [("task", _PREFIX_TASK), ("journal", _PREFIX_JOURNAL), ("note", _PREFIX_NOTE), ("deck", _PREFIX_DECK), ("meeting", _PREFIX_MEETING)]:
        m = pattern.match(text)
        if m:
            content = text[m.end():].strip() or text
            return cmd, content

    # Slow path: fuzzy match on first 1-2 words
    # Strip brackets if present: "[something] rest" → "something", "rest"
    stripped = text.strip()
    if stripped.startswith("["):
        bracket_end = stripped.find("]")
        if bracket_end > 0:
            candidate = stripped[1:bracket_end].strip().lower()
            rest = stripped[bracket_end + 1:].strip()
            for cmd, max_dist in _MAX_EDIT_DISTANCE.items():
                if _levenshtein(candidate, cmd) <= max_dist:
                    return cmd, rest or text

    # Check first word — only match if followed by punctuation separator
    # (prevents "Take the dog" matching "task")
    words = stripped.split(None, 1)
    if words:
        raw_first = words[0]
        has_separator = bool(re.search(r"[,:;.!?]+$", raw_first))
        first = re.sub(r"[,:;.!?]+$", "", raw_first).lower()
        rest = words[1] if len(words) > 1 else text
        if has_separator:
            for cmd, max_dist in _MAX_EDIT_DISTANCE.items():
                if _levenshtein(first, cmd) <= max_dist:
                    return cmd, rest.strip()

        # Check first two words joined (e.g. "Tas Kohlen" → "taskohlen")
        if len(words) > 1:
            second_words = rest.split(None, 1)
            if second_words:
                two_word = first + re.sub(r"[,:;.!?]+$", "", second_words[0]).lower()
                remainder = second_words[1] if len(second_words) > 1 else text
                # "taskohlen" vs "task" won't match, but check compound against
                # command + "colon" (common Whisper garble for ":")
                for cmd in _COMMAND_WORDS:
                    # e.g. "tas kohlen" → "taskohlen" vs "taskcolon"
                    target = cmd + "colon"
                    if _levenshtein(two_word, target) <= 3:
                        return cmd, remainder.strip()

    return None, text


@authorized
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe a voice note and create a note/task from it.

    1. Instant: faster-whisper transcribes locally → creates note/task
    2. Background: uploads audio to Dropbox /Apps/Otter for high-quality
       Otter.ai transcript (poller will update the note later)
    """
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    duration = voice.duration or 0
    if duration > 300:  # 5 min max
        await update.message.reply_text("Voice note too long (max 5 min).")
        return

    status_msg = await update.message.reply_text(
        f"Transcribing {duration}s voice note..."
    )

    try:
        import tempfile

        tg_file = await voice.get_file()

        # Save a copy for Dropbox upload and/or Gemini transcription
        audio_copy_path = None
        try:
            from roost.dropbox_client import is_dropbox_available
            if is_dropbox_available() or _USE_GEMINI_TRANSCRIPTION:
                tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
                audio_copy_path = tmp.name
                tmp.close()
                await tg_file.download_to_drive(audio_copy_path)
        except ImportError:
            if _USE_GEMINI_TRANSCRIPTION:
                tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
                audio_copy_path = tmp.name
                tmp.close()
                await tg_file.download_to_drive(audio_copy_path)
        except Exception:
            if _USE_GEMINI_TRANSCRIPTION and not audio_copy_path:
                # Must have a local file for Gemini — download directly
                tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
                audio_copy_path = tmp.name
                tmp.close()
                tg_file = await voice.get_file()
                await tg_file.download_to_drive(audio_copy_path)

        # Transcribe — Gemini fallback when whisper is not available
        if _USE_GEMINI_TRANSCRIPTION:
            if not audio_copy_path:
                await status_msg.edit_text("Failed to download voice note.")
                return
            from roost.meeting_notes_service import transcribe_audio_gemini
            text = transcribe_audio_gemini(audio_copy_path)
        else:
            from roost.voice import transcribe_telegram_voice
            # Re-fetch file object since download_to_drive consumed the URL
            tg_file = await voice.get_file()
            text = await transcribe_telegram_voice(tg_file)

        if not text:
            # Transcription failed — save as placeholder note
            note = task_service.create_note(
                NoteCreate(content="[transcription failed]", tag="voice")
            )
            await status_msg.edit_text(
                f"Noted #{note.id} [voice]: transcription failed."
            )
            if audio_copy_path and not _USE_GEMINI_TRANSCRIPTION:
                threading.Thread(
                    target=_upload_to_otter,
                    args=(audio_copy_path, note.id),
                    daemon=True,
                ).start()
            elif audio_copy_path:
                os.remove(audio_copy_path)
            return

        # Detect command prefix (regex fast path + Levenshtein fallback)
        note_id = None
        command, content = _detect_voice_command(text)

        if command == "deck":
            # Voice-to-presentation: generate .pptx from spoken content
            await status_msg.edit_text("Generating presentation from voice note...")
            try:
                from roost.meeting_notes_service import generate_presentation_content
                from roost.presentation_builder import build_presentation_from_content

                pres_content = generate_presentation_content(prompt=content)
                title_slug = pres_content.get("title", "deck")[:40].replace(" ", "_")
                output_path = os.path.join(tempfile.gettempdir(), f"{title_slug}.pptx")
                build_presentation_from_content(pres_content, output_path=output_path)

                size_kb = os.path.getsize(output_path) / 1024
                caption = (
                    f"*{pres_content.get('title', 'Presentation')}*\n"
                    f"{len(pres_content.get('slides', []))} slides | {size_kb:.0f} KB\n\n"
                    f"_From voice: {text[:200]}_"
                )
                with open(output_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(output_path),
                        caption=caption,
                        parse_mode="Markdown",
                    )
                await status_msg.delete()
                os.remove(output_path)
            except Exception as deck_err:
                logger.exception("Voice-to-deck failed")
                await status_msg.edit_text(f"Deck generation failed: {deck_err}")
            # Save transcript as note regardless
            note = task_service.create_note(NoteCreate(content=f"[deck] {text}", tag="voice"))
            note_id = note.id

        elif command == "meeting":
            # Voice-to-structured-notes: Gemini structures the transcript
            await status_msg.edit_text("Structuring meeting notes from voice...")
            try:
                from roost.meeting_notes_service import structure_meeting_notes
                result = structure_meeting_notes(content)

                lines = []
                if result.get("summary"):
                    lines.append(f"*Summary:* {result['summary']}\n")
                if result.get("key_points"):
                    lines.append("*Key Points:*")
                    for p in result["key_points"]:
                        lines.append(f"  \u2022 {p}")
                    lines.append("")
                if result.get("action_items"):
                    lines.append("*Action Items:*")
                    for item in result["action_items"]:
                        owner = item.get("owner", "?")
                        action = item.get("action", "")
                        deadline = f" (by {item['deadline']})" if item.get("deadline") else ""
                        lines.append(f"  \u2610 *{owner}*: {action}{deadline}")

                await status_msg.edit_text("\n".join(lines) or "No structure extracted.", parse_mode="Markdown")
            except Exception as mn_err:
                logger.exception("Voice-to-meeting-notes failed")
                await status_msg.edit_text(f"Meeting notes structuring failed: {mn_err}")
            note = task_service.create_note(NoteCreate(content=f"[meeting] {text}", tag="voice"))
            note_id = note.id

        elif command == "task":
            task = task_service.create_task(
                TaskCreate(title=content[:200]), source="voice"
            )
            note = task_service.create_note(
                NoteCreate(content=f"[task #{task.id}] {text}", tag="voice")
            )
            note_id = note.id
            reply = (
                f"Created task #{task.id}: {task.title}\n\n"
                f"_Transcript: {text[:500]}_"
            )
            notion_url = _sync_voice_to_notion(text, note.id, label="Task Voice Note")
            if notion_url:
                reply += f"\n\n[Notion]({notion_url})"
            await status_msg.edit_text(reply, parse_mode="Markdown")
        elif command == "journal":
            note = task_service.create_note(
                NoteCreate(content=content, tag="journal")
            )
            note_id = note.id
            reply = f"Journal #{note.id}:\n{content[:500]}"
            notion_url = _sync_voice_to_notion(content, note.id, label="Journal")
            if notion_url:
                reply += f"\n\n[Notion]({notion_url})"
            await status_msg.edit_text(reply, parse_mode="Markdown")
        elif command == "note":
            note = task_service.create_note(NoteCreate(content=content, tag="voice"))
            note_id = note.id
            reply = f"Noted #{note.id} [voice]: {content[:300]}"
            notion_url = _sync_voice_to_notion(content, note.id, label="Voice Note")
            if notion_url:
                reply += f"\n\n[Notion]({notion_url})"
            await status_msg.edit_text(reply, parse_mode="Markdown")
        else:
            # Default: save as a note tagged "voice"
            note = task_service.create_note(NoteCreate(content=text, tag="voice"))
            note_id = note.id
            reply = f"Noted #{note.id} [voice]:\n{text[:500]}"
            notion_url = _sync_voice_to_notion(text, note.id, label="Voice Note")
            if notion_url:
                reply += f"\n\n[Notion]({notion_url})"
            else:
                reply += "\n\n_Tip: Start with \"journal:\", \"task:\", \"deck:\", or \"meeting:\" to route._"
            await status_msg.edit_text(reply, parse_mode="Markdown")

        # Upload to Dropbox for Otter.ai (background thread) — only when whisper is primary
        if audio_copy_path and note_id and not _USE_GEMINI_TRANSCRIPTION:
            threading.Thread(
                target=_upload_to_otter,
                args=(audio_copy_path, note_id),
                daemon=True,
            ).start()
        elif audio_copy_path:
            os.remove(audio_copy_path)

    except Exception as e:
        logger.exception("Voice handler error")
        await status_msg.edit_text(f"Voice transcription failed: {e}")


def _sync_voice_to_notion(content: str, note_id: int, label: str = "Journal") -> str | None:
    """Create a Notion page for a voice note. Returns URL or None.

    Args:
        content: The transcription text.
        note_id: Local note ID (notion_page_id stored back to DB).
        label: Title prefix — "Journal", "Voice Note", "Task Voice Note", etc.
    """
    try:
        from datetime import datetime
        from roost.config import NOTION_API_TOKEN, NOTION_JOURNAL_PAGE_ID
        from roost.database import get_connection

        if not NOTION_API_TOKEN or not NOTION_JOURNAL_PAGE_ID:
            return None

        from roost.notion.client import get_client, rate_limited_call

        client = get_client()
        if client is None:
            return None

        today = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H:%M")
        title = f"{label} — {today} {time_str}"

        # Split content into paragraphs for Notion blocks
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": p[:2000]}}]
                },
            }
            for p in paragraphs
        ]
        if not children:
            children = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": content[:2000]}}]
                    },
                }
            ]

        result = rate_limited_call(
            client.pages.create,
            parent={"page_id": NOTION_JOURNAL_PAGE_ID},
            properties={
                "title": [{"text": {"content": title}}],
            },
            children=children,
        )
        page_url = result.get("url", "")
        notion_page_id = result.get("id", "")

        # Store Notion page ID on the note for later updates (e.g. Otter poller)
        if notion_page_id:
            conn = get_connection()
            conn.execute(
                "UPDATE notes SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, note_id),
            )
            conn.commit()
            conn.close()

        logger.info("%s #%d synced to Notion: %s", label, note_id, page_url)
        return page_url
    except Exception:
        logger.exception("Notion sync failed for note #%d (%s)", note_id, label)
        return None


@authorized
async def cmd_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recent journal entries. Usage: /journal [count]"""
    count = 10
    if context.args:
        try:
            count = min(int(context.args[0]), 50)
        except ValueError:
            pass

    entries = task_service.list_notes(tag="journal", limit=count)
    if not entries:
        await update.message.reply_text(
            "No journal entries yet.\n"
            "Send a voice note starting with \"journal:\" to create one."
        )
        return

    lines = [f"*Journal* ({len(entries)} recent)\n"]
    for e in entries:
        preview = e.content[:80].replace("\n", " ")
        if len(e.content) > 80:
            preview += "..."
        date = e.created_at[:16] if e.created_at else "?"
        lines.append(f"#{e.id} [{date}]\n{preview}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _upload_to_otter(audio_path: str, note_id: int) -> None:
    """Upload audio to Dropbox Otter folder and register for polling."""
    try:
        from datetime import datetime
        from roost.config import DROPBOX_OTTER_FOLDER
        from roost.dropbox_client import upload_file
        from roost.otter_poll import register_pending

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"voice_{note_id}_{timestamp}.ogg"
        dropbox_path = f"{DROPBOX_OTTER_FOLDER}/{filename}"

        if upload_file(audio_path, dropbox_path):
            register_pending(note_id, dropbox_path)
            logger.info("Uploaded voice note #%d to Otter: %s", note_id, dropbox_path)
        else:
            logger.warning("Failed to upload voice note #%d to Dropbox", note_id)
    except Exception:
        logger.exception("Otter upload failed for note #%d", note_id)
    finally:
        import os as _os
        if _os.path.exists(audio_path):
            _os.remove(audio_path)
