"""Free-form AI reply (draft_reply) + context-pull (recent_context)."""

from __future__ import annotations

import pytest

from roost.database import get_connection
from roost.extras.lead_nurture.services import conversation as conv
from roost.extras.messaging_external.services.ai_cdr import draft_reply


@pytest.mark.asyncio
async def test_draft_reply_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr("roost.config.GEMINI_API_KEY", "", raising=False)
    assert await draft_reply("what are your fees?", sender="Marcus") == ""


def test_recent_context_formats_thread():
    phone = "+6590000077"

    def _wipe():
        c = get_connection()
        c.execute("DELETE FROM lead_messages WHERE identifier=?", (phone,))
        c.commit()
        c.close()

    _wipe()
    try:
        conv.log_message(channel="chatwoot", identifier=phone, direction="in",
                         body="Hi, interested in retirement planning")
        conv.log_message(channel="chatwoot", identifier=phone, direction="out",
                         body="Happy to help! When are you hoping to start?")
        ctx = conv.recent_context(channel="chatwoot", identifier=phone)
        assert "Them: Hi, interested in retirement planning" in ctx
        assert "Us: Happy to help" in ctx
        # oldest-first
        assert ctx.index("Them:") < ctx.index("Us:")
    finally:
        _wipe()


def test_recent_context_empty_when_no_thread():
    assert conv.recent_context(channel="chatwoot", identifier="+6590000099") == ""
