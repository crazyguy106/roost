"""OTP delivery sources for RPA flows.

Two strategies:
  - EmailOtpSource: poll Gmail for a recent message matching `query`,
    extract the code with `regex` (must capture group 1).
  - TelegramOtpSource: ask the user via the running rpa_run.

A flow picks one per portal — e.g. AIA mails OTPs (EmailOtpSource), AIA
SMS-only would use TelegramOtpSource.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from roost.services import rpa_runs

logger = logging.getLogger("roost.services.otp_source")


class OtpSource(Protocol):
    async def get(self, run_id: int) -> str: ...


@dataclass
class EmailOtpSource:
    """Poll Gmail for an OTP message and extract the code.

    Args:
        query: Gmail search query (e.g. 'from:noreply@aia.com subject:OTP').
        regex: pattern with one capturing group that yields the code.
        timeout: total seconds to wait before giving up.
        poll_interval: seconds between polls.
        google_account: Gmail account to query.
    """

    query: str
    regex: str = r"\b(\d{6})\b"
    timeout: float = 120.0
    poll_interval: float = 8.0
    google_account: str | None = None

    async def get(self, run_id: int) -> str:
        from roost.mcp.gmail_helpers import search_messages, read_thread

        deadline = time.monotonic() + self.timeout
        compiled = re.compile(self.regex)
        # Restrict to mail received within the last 5 minutes so we don't
        # pick up an old code.
        scoped_query = f"newer_than:5m {self.query}"
        last_seen: set[str] = set()

        while time.monotonic() < deadline:
            try:
                messages = await asyncio.to_thread(
                    search_messages,
                    scoped_query,
                    max_results=5,
                    account=self.google_account,
                )
            except Exception as e:
                logger.warning("Gmail OTP search failed: %s", e)
                messages = []

            for msg in messages:
                msg_id = msg.get("id") or msg.get("threadId") or ""
                if msg_id in last_seen:
                    continue
                last_seen.add(msg_id)
                tid = msg.get("threadId") or msg_id
                try:
                    thread = await asyncio.to_thread(
                        read_thread, tid, account=self.google_account
                    )
                except Exception:
                    continue
                body = self._thread_text(thread)
                m = compiled.search(body)
                if m:
                    code = m.group(1)
                    logger.info("Email OTP captured for run #%d", run_id)
                    return code

            await asyncio.sleep(self.poll_interval)

        # Fall through to Telegram prompt — degrades to user-asked.
        logger.warning("Email OTP timed out for run #%d, prompting user", run_id)
        return await TelegramOtpSource().get(run_id)

    @staticmethod
    def _thread_text(thread: dict) -> str:
        parts: list[str] = []
        for m in thread.get("messages", []) or []:
            parts.append(m.get("snippet", ""))
            parts.append(m.get("body", ""))
            parts.append(m.get("subject", ""))
        return "\n".join(p for p in parts if p)


@dataclass
class TelegramOtpSource:
    """Prompt the user to enter an OTP via Telegram and wait for the reply."""

    prompt: str = "Enter the OTP from your portal"
    timeout: float = 300.0

    async def get(self, run_id: int) -> str:
        value = await rpa_runs.request_input(
            run_id, self.prompt, kind="otp", timeout=self.timeout
        )
        return value.strip()
