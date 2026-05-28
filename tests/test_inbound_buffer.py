"""Phase D — fragment debouncer.

The buffer holds rapid-fire WhatsApp/WeChat fragments from the same
sender and runs the processor once on the concatenated text. Tests
shrink ``debounce_seconds`` so the suite runs in under a second.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.fixture
def fast_buffer(monkeypatch):
    """Configure the debouncer with short windows for testing, then
    reset state. Restores settings after the test."""
    from roost.extras.lead_nurture.services import settings
    from roost.extras.messaging_external.services import inbound_buffer

    orig = settings.get_fragmented_messages()
    settings._cache["fragmented_messages"] = {
        "enabled": True,
        "debounce_seconds": 0.4,
        "early_release_seconds": 0.15,
        "max_wait_seconds": 2.0,
        "terminal_punctuation": [".", "?", "!"],
    }
    inbound_buffer.reset_for_tests()
    yield inbound_buffer
    inbound_buffer.reset_for_tests()
    settings._cache["fragmented_messages"] = orig


@pytest.mark.asyncio
async def test_fragments_combine_into_one_call(fast_buffer):
    received: list[dict] = []

    async def processor(msg: dict) -> None:
        received.append(msg)

    for text in ["hi", "looking for", "a financial advisor"]:
        await fast_buffer.submit(
            "whatsapp", "+65111",
            {"sender": "+65111", "text": text, "message_id": text[:3]},
            processor,
        )
        await asyncio.sleep(0.05)

    assert received == []  # no premature flush
    await asyncio.sleep(0.6)  # past debounce window
    assert len(received) == 1
    assert received[0]["text"] == "hi looking for a financial advisor"
    assert received[0]["_fragment_count"] == 3


@pytest.mark.asyncio
async def test_terminal_punctuation_triggers_early_release(fast_buffer):
    received: list[dict] = []

    async def processor(msg: dict) -> None:
        received.append(msg)

    await fast_buffer.submit(
        "whatsapp", "+65222",
        {"sender": "+65222", "text": "Are you free this week?",
         "message_id": "z"},
        processor,
    )
    # Early window is 0.15s; full debounce is 0.4s. Sleep for 0.25s —
    # should have flushed already.
    await asyncio.sleep(0.25)
    assert len(received) == 1
    assert received[0]["text"] == "Are you free this week?"


@pytest.mark.asyncio
async def test_disabled_passthrough_no_delay(fast_buffer):
    from roost.extras.lead_nurture.services import settings
    settings._cache["fragmented_messages"]["enabled"] = False

    received: list[dict] = []

    async def processor(msg: dict) -> None:
        received.append(msg)

    await fast_buffer.submit(
        "whatsapp", "+65333",
        {"sender": "+65333", "text": "instant", "message_id": "i"},
        processor,
    )
    # No sleep — processor should have fired synchronously.
    assert len(received) == 1
    assert received[0]["text"] == "instant"


@pytest.mark.asyncio
async def test_different_senders_isolated(fast_buffer):
    received: list[dict] = []

    async def processor(msg: dict) -> None:
        received.append(msg)

    await fast_buffer.submit("whatsapp", "+65A",
        {"sender": "+65A", "text": "alpha", "message_id": "a"}, processor)
    await fast_buffer.submit("whatsapp", "+65B",
        {"sender": "+65B", "text": "beta",  "message_id": "b"}, processor)
    await asyncio.sleep(0.6)

    assert len(received) == 2
    assert {m["sender"] for m in received} == {"+65A", "+65B"}


@pytest.mark.asyncio
async def test_max_wait_cap_releases_chatty_sender(fast_buffer):
    """A user who never pauses still triggers a flush at max_wait."""
    received: list[dict] = []

    async def processor(msg: dict) -> None:
        received.append(msg)

    # Submit a fragment every 0.15s (always faster than 0.4s debounce
    # but eventually crossing the 2.0s cap).
    for i in range(20):
        await fast_buffer.submit(
            "whatsapp", "+65chat",
            {"sender": "+65chat", "text": f"part{i}", "message_id": str(i)},
            processor,
        )
        await asyncio.sleep(0.15)
        if received:
            break

    assert len(received) == 1, \
        f"max_wait cap didn't fire; received={len(received)}"
    assert received[0]["_fragment_count"] >= 5
