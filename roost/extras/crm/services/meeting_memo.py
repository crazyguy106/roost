"""Voice/meeting memo → CRM write-back.

Turns a (transcribed) post-meeting voice memo into a structured note on the
client's CRM record: a short summary plus the next-actions the adviser
dictated. This is the operator-side "voice memo from a meeting →
next-actions → CRM updated" flow (FA-edition deck slide 30). Transcription
itself lives in `roost.meeting_notes_service` / `roost.voice`; this module
takes the transcript and does the extract + write-back.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("roost.meeting_memo")


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*", "", text.strip())
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        t = m.group(0)
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        return None


def extract_memo(transcript: str) -> dict:
    """Gemini extracts ``{client_name, summary, next_actions[]}`` from a memo.

    Falls back to a safe default (no client, transcript as summary) when no
    API key is set or extraction fails. Never raises.
    """
    default = {
        "client_name": "",
        "summary": (transcript or "").strip()[:500],
        "next_actions": [],
    }
    from roost.config import GEMINI_API_KEY, GEMINI_MODEL
    if not GEMINI_API_KEY or not (transcript or "").strip():
        return default

    prompt = (
        "A financial adviser dictated this post-meeting voice memo. Extract a "
        "JSON object with exactly these keys:\n"
        '  "client_name": the client they met — name only, "" if unclear,\n'
        '  "summary": a 2-3 sentence summary of the meeting,\n'
        '  "next_actions": a list of the concrete follow-up actions the '
        "adviser needs to do (each a short string).\n\n"
        f'Memo: "{transcript}"\n\nReturn ONLY the JSON object.'
    )
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        resp = _run(client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                tools=None, temperature=0.2, max_output_tokens=1024,
            ),
        ))
        data = _parse_json(resp.text or "")
        if not isinstance(data, dict):
            return default
        return {
            "client_name": str(data.get("client_name") or "").strip(),
            "summary": (str(data.get("summary") or "").strip() or default["summary"]),
            "next_actions": [
                str(a).strip() for a in (data.get("next_actions") or [])
                if str(a).strip()
            ],
        }
    except Exception:
        logger.exception("extract_memo failed")
        return default


def _run(coro):
    """Run a coroutine to completion from sync context (the bot voice handler
    is async, but the MCP/CLI path may be sync)."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in a loop — run in a fresh one on a thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def log_to_crm(transcript: str, *, client_hint: str = "") -> dict:
    """Extract a memo and write it to the matching CRM contact as a note.

    Returns ``{"ok": bool, ...}`` — on success: ``client``, ``person_id``,
    ``summary``, ``actions``. On failure: ``reason`` + the extracted ``memo``.
    Never raises.
    """
    memo = extract_memo(transcript)
    name = (client_hint or memo["client_name"]).strip()
    if not name:
        return {"ok": False, "reason": "no_client_identified", "memo": memo}

    from roost.extras.crm.services import CrmError, get_provider
    try:
        provider = get_provider()
        person = None
        try:
            hits = provider.search_people(name, limit=1)
            person = hits[0] if hits else None
        except (CrmError, NotImplementedError, Exception):
            person = None
        if not person:
            return {"ok": False, "reason": f"client_not_found: {name}", "memo": memo}

        lines = [memo["summary"]]
        if memo["next_actions"]:
            lines += ["", "Next actions:"]
            lines += [f"- [ ] {a}" for a in memo["next_actions"]]
        body = "\n".join(lines)
        provider.append_note(person_id=person.id, content=body,
                             title="Meeting note (voice memo)")
        return {
            "ok": True,
            "client": person.name,
            "person_id": person.id,
            "summary": memo["summary"],
            "actions": memo["next_actions"],
        }
    except Exception as e:
        logger.exception("log_to_crm failed")
        return {"ok": False, "reason": str(e)[:140], "memo": memo}
