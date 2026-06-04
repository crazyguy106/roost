"""Chatwoot inbound starts the qualification questionnaire.

`start_qualification_if_needed` previously rejected `channel="chatwoot"`
with `reason="no_addressable_channel"`, so a WhatsApp lead fronted by
Chatwoot never received the first qualifying question. Chatwoot is now an
addressable channel (it sends via the WhatsApp adapter, which routes through
Chatwoot when CHATWOOT_ENABLED). These tests lock the channel guard.
"""

from __future__ import annotations

from roost.extras.lead_nurture.services import qualification as q


def test_chatwoot_is_addressable(monkeypatch):
    # Hermetic: stub the question pack + send so we test only the guard.
    monkeypatch.setattr(q, "_get_pack", lambda slug: [{"key": "timeline",
                                                       "question": "When?"}])
    monkeypatch.setattr(q, "_wants_human", lambda text: False)
    calls = []
    monkeypatch.setattr(
        q, "_send_question",
        lambda channel, identifier, text: calls.append((channel, identifier))
        or {"ok": True},
    )

    res = q.start_qualification_if_needed(
        enrollment_id=999_999,            # nonexistent → no_enrollment AFTER send
        cadence_slug="property_buyer_intro",
        channel="chatwoot",
        identifier="+6591234567",
        contact_name="Test Lead",
        trigger_text="hi looking for a condo",
    )

    # Got past the channel guard (was 'no_addressable_channel' before the fix)…
    assert res.get("reason") != "no_addressable_channel"
    # …and actually attempted to send Q1 over the chatwoot channel.
    assert calls and calls[0][0] == "chatwoot"


def test_unknown_channel_still_rejected(monkeypatch):
    monkeypatch.setattr(q, "_get_pack", lambda slug: [{"key": "t",
                                                      "question": "When?"}])
    res = q.start_qualification_if_needed(
        enrollment_id=1,
        cadence_slug="x",
        channel="email",
        identifier="a@b.com",
        trigger_text="hi",
    )
    assert res == {"started": False, "reason": "no_addressable_channel"}
