"""Gemini CLI agent provider.

Spawns ``gemini -p <prompt> --output-format stream-json --yolo`` per turn.
Auth uses ``~/.gemini/`` state (Google account OAuth via ``gemini /auth``
or ``GEMINI_API_KEY``); MCP servers are read from
``~/.gemini/settings.json`` (or wherever the user's `mcpServers` config
lives — Gemini CLI doesn't take a CLI-flag override, so we mount the
config file into the bind-mounted auth dir at boot time).

Stream-json schema (from @google/gemini-cli-core's stream-json-formatter):

  - ``{type: "init", timestamp, session_id, model}``
  - ``{type: "message", timestamp, role: "user"|"assistant", content, delta?}``
  - ``{type: "tool_use", timestamp, tool_name, tool_id, parameters}``
  - ``{type: "tool_result", timestamp, tool_id, status: "success"|"error",
        output?, error?: {type, message}}``
  - ``{type: "error", timestamp, severity: "warning"|"error", message}``
  - ``{type: "result", timestamp, status, error?, stats?}``

Session continuity: Gemini CLI's ``--resume`` takes a positional index
(``--resume 1``, ``--resume 2``) rather than a UUID, so we cannot
assign IDs server-side as we do for Claude. We persist whatever
``session_id`` the init event reports, then resume by ``--resume latest``
when re-entering a tracked session — the safest single-user pattern.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from roost.agents_subprocess_cli import (
    BaseStreamEventParser,
    BaseSubprocessCliAgent,
    truncate,
    truncate_text,
)

logger = logging.getLogger("roost.agents_gemini_cli")


class _GeminiStreamEventParser(BaseStreamEventParser):
    """Translate Gemini CLI stream-json into Roost on_tool_event payloads."""

    async def handle(
        self,
        event: dict,
        on_progress: Callable | None = None,
        on_tool_event: Callable | None = None,
    ) -> None:
        etype = event.get("type")

        if etype == "init":
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid
            return

        if etype == "message":
            if event.get("role") != "assistant":
                return
            text = event.get("content", "") or ""
            if not text:
                return
            # Gemini emits both deltas (delta=True) and the final
            # consolidated message (delta=False/absent). Keep the most
            # recent non-delta as final_text; pipe everything to
            # on_progress so live UIs see the stream.
            if not event.get("delta"):
                self.final_text = text
            if on_progress:
                try:
                    await on_progress(text)
                except Exception:
                    logger.debug("on_progress failed", exc_info=True)
            return

        if etype == "tool_use":
            tu_id = event.get("tool_id", "") or ""
            tu_name = event.get("tool_name", "unknown")
            tu_input = event.get("parameters") or {}
            if tu_id:
                self.tool_starts[tu_id] = time.perf_counter()
            if on_tool_event:
                try:
                    await on_tool_event({
                        "type": "tool_called",
                        "call_id": tu_id,
                        "tool": tu_name,
                        "args_preview": truncate(tu_input),
                    })
                except Exception:
                    logger.debug("on_tool_event(tool_called) failed", exc_info=True)
            return

        if etype == "tool_result":
            tu_id = event.get("tool_id", "") or ""
            status = event.get("status", "success")
            is_err = status == "error"
            output = event.get("output")
            error = event.get("error") or {}

            dur_ms = 0
            if tu_id and tu_id in self.tool_starts:
                dur_ms = int((time.perf_counter() - self.tool_starts.pop(tu_id)) * 1000)

            if on_tool_event:
                payload: dict[str, Any] = {
                    "type": "tool_failed" if is_err else "tool_returned",
                    "call_id": tu_id,
                    "tool": "",
                    "duration_ms": dur_ms,
                }
                if is_err:
                    err_msg = error.get("message") if isinstance(error, dict) else str(error)
                    payload["error"] = truncate_text(err_msg or output or "tool failed")
                else:
                    payload["result_preview"] = truncate_text(output)
                try:
                    await on_tool_event(payload)
                except Exception:
                    logger.debug("on_tool_event(result) failed", exc_info=True)
            return

        if etype == "error":
            # Surface as final_text if we have nothing else; let
            # on_progress see it for UIs that stream errors.
            msg = event.get("message", "") or ""
            if msg and not self.final_text:
                self.final_text = f"Gemini error ({event.get('severity', 'error')}): {msg}"
            if on_progress and msg:
                try:
                    await on_progress(self.final_text)
                except Exception:
                    logger.debug("on_progress(error) failed", exc_info=True)
            return

        if etype == "result":
            # On success Gemini doesn't repeat the final text here, but
            # on failure the error.message carries the final message.
            err = event.get("error")
            if event.get("status") == "error" and isinstance(err, dict):
                msg = err.get("message", "") or ""
                if msg and not self.final_text:
                    self.final_text = f"Gemini error: {msg}"


class GeminiCliAgent(BaseSubprocessCliAgent):
    """Agent that runs Gemini CLI as a subprocess per turn.

    Conforms to the same interface as the other agent providers. Bills
    against the user's Gemini/Google account (free or paid tier per
    their auth setup), not the AI Studio API key — unless GEMINI_API_KEY
    is set in the container, in which case Gemini CLI uses that.
    """

    provider_id = "gemini_cli"
    default_bin = "gemini"
    default_auth_dir = "/home/dev/.gemini"
    default_timeout_s = 300

    @classmethod
    def _env_prefix(cls) -> str:
        return "GEMINI_CLI"

    def _build_cmd(self, existing_session: str | None) -> tuple[list[str], str | None]:
        cmd: list[str] = [
            self.bin,
            "-p", "",                       # consume stdin
            "--output-format", "stream-json",
        ]

        # Permission handling: yolo == auto-approve all tools.
        if self.permission_mode == "yolo":
            cmd.append("--yolo")
        else:
            cmd.extend(["--approval-mode", self.permission_mode])

        if existing_session:
            # Gemini's --resume takes an index ("latest" or 1..N), not a
            # UUID. We tracked the session_id from a prior init event,
            # but the CLI's resume index is process-local. "latest" is
            # the safe single-user choice; multi-tenant would need a
            # richer session map (CLI exposes --list-sessions for that).
            cmd.extend(["--resume", "latest"])

        if self.model:
            cmd.extend(["--model", self.model])

        # MCP server config: Gemini reads ~/.gemini/settings.json. There
        # is no --mcp-config flag. The Docker entrypoint copies
        # /etc/roost/gemini-settings.json into the bind-mounted
        # ~/.gemini/ on first boot. We don't manage that path here.

        # Vendor assigns its own session_id, so no provisional id.
        return cmd, None

    def _make_parser(self) -> BaseStreamEventParser:
        return _GeminiStreamEventParser()

    def _missing_binary_message(self) -> str:
        return (
            f"Gemini CLI not found at '{self.bin}'. Install with "
            "`npm install -g @google/gemini-cli` or set GEMINI_CLI_BIN."
        )

    def _error_exit_message(self, rc: int, stderr: str) -> str:
        return f"Gemini CLI error (exit {rc}): {stderr[:300]}"

    def _timeout_message(self) -> str:
        return f"Gemini CLI timed out after {self.timeout_s}s."
