"""Codex CLI agent provider (UNTESTED scaffold).

Wraps OpenAI's official ``@openai/codex`` CLI. Marked SCAFFOLD because
the parser was written against the public docs/issue tracker and has
not been validated end-to-end against a logged-in Codex install — the
event shape may need adjustment after a live run. Set
``AGENT_PROVIDER=codex_cli`` and exercise it; if events come through
malformed, file an issue and tweak ``_CodexStreamEventParser.handle``.

Working assumptions for the parser (best-effort):

  - ``{type: "session.created" | "session_started", id|session_id}``
  - ``{type: "agent_message" | "message", role: "assistant", content: <str
        |list-of-content-blocks>}``
  - ``{type: "tool_call" | "function_call", id|call_id, name, arguments}``
  - ``{type: "tool_call_output" | "function_call_output", call_id,
        output, status}``
  - ``{type: "error", message}``
  - ``{type: "task_complete" | "result", final_output?}``

If Codex doesn't emit JSONL at all (some versions stream plain text on
``codex exec``), the base class falls through to capturing the process
exit code and stderr, which yields a sensible error message rather than
silent failure.
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

logger = logging.getLogger("roost.agents_codex_cli")


def _coerce_text(content: Any) -> str:
    """Codex sometimes ships ``content`` as a plain string and sometimes
    as a list of ``{type: 'output_text', text: ...}`` blocks (mirroring
    the OpenAI Responses API schema). Normalise both shapes."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            text = b.get("text") or b.get("content") or ""
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts)
    return ""


class _CodexStreamEventParser(BaseStreamEventParser):
    """Parser for the @openai/codex CLI's JSONL output. Best-effort."""

    _SESSION_TYPES = {"session.created", "session_started", "session_create"}
    _MESSAGE_TYPES = {"agent_message", "message", "assistant_message"}
    _TOOL_CALL_TYPES = {"tool_call", "function_call", "agent_tool_call"}
    _TOOL_RESULT_TYPES = {
        "tool_call_output", "function_call_output", "tool_result",
    }
    _ERROR_TYPES = {"error", "agent_error"}
    _FINAL_TYPES = {"task_complete", "result", "agent_task_complete"}

    async def handle(
        self,
        event: dict,
        on_progress: Callable | None = None,
        on_tool_event: Callable | None = None,
    ) -> None:
        etype = event.get("type", "")

        if etype in self._SESSION_TYPES:
            sid = event.get("session_id") or event.get("id")
            if sid:
                self.captured_session_id = sid
            return

        if etype in self._MESSAGE_TYPES:
            role = event.get("role", "assistant")
            if role != "assistant":
                return
            text = _coerce_text(event.get("content") or event.get("text"))
            if not text:
                return
            self.final_text = text
            if on_progress:
                try:
                    await on_progress(text)
                except Exception:
                    logger.debug("on_progress failed", exc_info=True)
            return

        if etype in self._TOOL_CALL_TYPES:
            call_id = event.get("call_id") or event.get("id") or ""
            name = event.get("name") or event.get("tool_name") or "unknown"
            args = event.get("arguments") or event.get("parameters") or {}
            if call_id:
                self.tool_starts[call_id] = time.perf_counter()
            if on_tool_event:
                try:
                    await on_tool_event({
                        "type": "tool_called",
                        "call_id": call_id,
                        "tool": name,
                        "args_preview": truncate(args),
                    })
                except Exception:
                    logger.debug("on_tool_event(tool_called) failed", exc_info=True)
            return

        if etype in self._TOOL_RESULT_TYPES:
            call_id = event.get("call_id") or event.get("id") or ""
            status = (event.get("status") or "success").lower()
            is_err = status in ("error", "failed", "failure")
            output = event.get("output") or event.get("result")

            dur_ms = 0
            if call_id and call_id in self.tool_starts:
                dur_ms = int((time.perf_counter() - self.tool_starts.pop(call_id)) * 1000)

            if on_tool_event:
                payload: dict[str, Any] = {
                    "type": "tool_failed" if is_err else "tool_returned",
                    "call_id": call_id,
                    "tool": "",
                    "duration_ms": dur_ms,
                }
                if is_err:
                    payload["error"] = truncate_text(output)
                else:
                    payload["result_preview"] = truncate_text(output)
                try:
                    await on_tool_event(payload)
                except Exception:
                    logger.debug("on_tool_event(result) failed", exc_info=True)
            return

        if etype in self._ERROR_TYPES:
            msg = event.get("message") or event.get("error") or ""
            if isinstance(msg, dict):
                msg = msg.get("message", str(msg))
            if msg and not self.final_text:
                self.final_text = f"Codex error: {msg}"
            return

        if etype in self._FINAL_TYPES:
            final = event.get("final_output") or event.get("output")
            text = _coerce_text(final)
            if text and not self.final_text:
                self.final_text = text


class CodexCliAgent(BaseSubprocessCliAgent):
    """Agent that runs OpenAI's ``codex`` CLI as a subprocess per turn.

    SCAFFOLD ONLY — set ``AGENT_PROVIDER=codex_cli`` once you've
    ``codex login``'d in the container and verified the event schema
    matches the parser above. Roost surfaces errors verbatim, so a
    schema mismatch shows up as a clear ``"Codex error: ..."`` message
    rather than silent breakage.
    """

    provider_id = "codex_cli"
    default_bin = "codex"
    default_auth_dir = "/home/dev/.codex"
    default_timeout_s = 300

    @classmethod
    def _env_prefix(cls) -> str:
        return "CODEX_CLI"

    def _build_cmd(self, existing_session: str | None) -> tuple[list[str], str | None]:
        # `codex exec` is the documented non-interactive entrypoint.
        # `--json` selects newline-delimited JSON event output. `-` (or
        # `--prompt-stdin`) makes the CLI read the prompt from stdin.
        # `--skip-git-repo-check` bypasses the trusted-directory guard
        # (Roost's CWD inside the container is not a git repo).
        cmd: list[str] = [self.bin, "exec", "--json", "--skip-git-repo-check"]

        # Codex's permission concept maps roughly: yolo/auto = no
        # confirmations. The CLI flag has shifted across versions — older
        # codex used `--full-auto`, current expects `--sandbox <mode>`
        # (workspace-write is the closest equivalent: writes inside the
        # workspace, no network unless explicitly granted).
        if self.permission_mode in ("yolo", "full-auto", "auto"):
            cmd.extend(["--sandbox", "workspace-write"])
        elif self.permission_mode and self.permission_mode != "default":
            cmd.extend(["--ask-for-approval", self.permission_mode])

        if existing_session:
            cmd.extend(["--resume", existing_session])

        if self.model:
            cmd.extend(["--model", self.model])

        if self.mcp_config_path:
            # Recent codex versions accept --config FILE to pin a TOML
            # config file. Older versions only read ~/.codex/config.toml.
            cmd.extend(["--config", self.mcp_config_path])

        # Prompt comes via stdin (BaseSubprocessCliAgent pipes it).
        cmd.append("-")

        return cmd, None

    def _make_parser(self) -> BaseStreamEventParser:
        return _CodexStreamEventParser()

    def _missing_binary_message(self) -> str:
        return (
            f"Codex CLI not found at '{self.bin}'. Install with "
            "`npm install -g @openai/codex` or set CODEX_CLI_BIN."
        )

    def _error_exit_message(self, rc: int, stderr: str) -> str:
        return f"Codex CLI error (exit {rc}): {stderr[:300]}"

    def _timeout_message(self) -> str:
        return f"Codex CLI timed out after {self.timeout_s}s."
