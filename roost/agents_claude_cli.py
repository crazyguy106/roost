"""Claude Code CLI agent provider.

Spawns `claude -p --output-format=stream-json` as a subprocess per turn.
Auth is via the Claude subscription (the `~/.claude/` state the user logged
in with), not an Anthropic API key. Roost's MCP tools are exposed to the
CLI via the `--mcp-config` flag pointing at a generated config file, so
the agent can still call create_task, search_emails, attio_*, etc.

Session continuity: a UUID is assigned on first turn via --session-id and
persisted in a Roost-side JSON map (Roost session_id -> Claude UUID).
Subsequent turns resume that session via --resume.

Process plumbing (asyncio.Lock, subprocess + JSON-line loop, session-map
persistence) lives in ``agents_subprocess_cli.BaseSubprocessCliAgent``;
this module is the Claude-specific argv builder + stream-json parser.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable

from roost.agents_subprocess_cli import (
    BaseStreamEventParser,
    BaseSubprocessCliAgent,
    get_session_id,
    load_sessions,
    save_sessions,
    set_session_id,
    truncate,
    truncate_text,
)

logger = logging.getLogger("roost.agents_claude_cli")


# ── Backwards-compat re-exports ─────────────────────────────────────
# Earlier callers and tests imported these names from this module
# directly. Keep the surface stable across the refactor.

def _session_file_path() -> str:
    return ClaudeCliAgent.session_file_path()


def _load_sessions() -> dict[str, str]:
    return load_sessions(_session_file_path())


def _save_sessions(sessions: dict[str, str]) -> None:
    save_sessions(_session_file_path(), sessions)


def _get_claude_session_id(roost_session_id: str | None) -> str | None:
    return get_session_id(_session_file_path(), roost_session_id)


def _set_claude_session_id(roost_session_id: str | None,
                           claude_session_id: str) -> None:
    set_session_id(_session_file_path(), roost_session_id, claude_session_id)


def _truncate(value: Any, limit: int = 200) -> Any:
    return truncate(value, limit)


def _truncate_text(value: Any, limit: int = 500) -> str:
    return truncate_text(value, limit)


# ── Parser: Claude stream-json events → Roost on_tool_event ─────────

class _StreamEventParser(BaseStreamEventParser):
    """Parses Claude CLI stream-json NDJSON events into Roost on_tool_event
    callbacks. Stateful: tracks tool_call_id → start-time for duration and
    accumulates the final assistant text.

    Claude event shape:
      - ``{type: "system", subtype: "init", session_id}``
      - ``{type: "assistant", message: {content: [{type:text,text}|
            {type:tool_use,id,name,input}]}}``
      - ``{type: "user", message: {content: [{type:tool_result,
            tool_use_id,is_error,content}]}}``
      - ``{type: "result", result, session_id}``
    """

    async def handle(
        self,
        event: dict,
        on_progress: Callable | None = None,
        on_tool_event: Callable | None = None,
    ) -> None:
        etype = event.get("type")

        if etype == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid
            return

        if etype == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "")
                    if text:
                        self.final_text = text
                        if on_progress:
                            try:
                                await on_progress(text)
                            except Exception:
                                logger.debug("on_progress failed", exc_info=True)
                elif btype == "tool_use":
                    tu_id = block.get("id", "")
                    tu_name = block.get("name", "unknown")
                    tu_input = block.get("input") or {}
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

        if etype == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tu_id = block.get("tool_use_id", "")
                is_err = bool(block.get("is_error"))
                content = block.get("content")
                if isinstance(content, list):
                    content = "\n".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
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
                        payload["error"] = truncate_text(content)
                    else:
                        payload["result_preview"] = truncate_text(content)
                    try:
                        await on_tool_event(payload)
                    except Exception:
                        logger.debug("on_tool_event(result) failed", exc_info=True)
            return

        if etype == "result":
            text = event.get("result")
            if text and not self.final_text:
                self.final_text = text
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid


# Backwards-compat alias used by other modules.
_ClaudeStreamEventParser = _StreamEventParser


class ClaudeCliAgent(BaseSubprocessCliAgent):
    """Agent that runs Claude Code CLI as a subprocess per turn.

    Conforms to the same interface as ClaudeAgent / OpenAIAgent /
    GeminiAgent. Bills against the Claude subscription, not an API key.
    """

    provider_id = "claude_cli"
    default_bin = "claude"
    default_auth_dir = "/home/dev/.claude"
    default_timeout_s = 300

    @classmethod
    def _env_prefix(cls) -> str:
        # Existing envs use CLAUDE_CLI_* (not CLAUDE_CLI_CLI_*).
        return "CLAUDE_CLI"

    def __init__(
        self,
        system_prompt: str = "",
        session_id: str | None = None,
        include_agent_tools: bool = False,
        api_key: str = "",
        model: str = "",
        tool_scope: str = "",
    ):
        super().__init__(
            system_prompt=system_prompt,
            session_id=session_id,
            include_agent_tools=include_agent_tools,
            api_key=api_key,
            model=model,
            tool_scope=tool_scope,
        )
        # Claude uses --permission-mode bypassPermissions, not "yolo".
        self.permission_mode = os.environ.get(
            "CLAUDE_CLI_PERMISSION_MODE", "bypassPermissions",
        )

    def _build_cmd(self, existing_session: str | None) -> tuple[list[str], str | None]:
        cmd: list[str] = [
            self.bin, "-p",
            "--output-format", "stream-json",
            "--input-format", "text",
            "--verbose",
            "--permission-mode", self.permission_mode,
        ]

        provisional_sid: str | None = None
        if existing_session:
            cmd.extend(["--resume", existing_session])
        else:
            provisional_sid = str(uuid.uuid4())
            cmd.extend(["--session-id", provisional_sid])
            if self.system_prompt:
                cmd.extend(["--append-system-prompt", self.system_prompt])

        if self.model:
            cmd.extend(["--model", self.model])

        if self.mcp_config_path:
            cmd.extend(["--mcp-config", self.mcp_config_path, "--strict-mcp-config"])

        return cmd, provisional_sid

    def _make_parser(self) -> BaseStreamEventParser:
        return _StreamEventParser()

    def _missing_binary_message(self) -> str:
        return (
            f"Claude CLI not found at '{self.bin}'. Install with "
            "`npm install -g @anthropic-ai/claude-code` or set CLAUDE_CLI_BIN."
        )

    def _error_exit_message(self, rc: int, stderr: str) -> str:
        return f"Claude CLI error (exit {rc}): {stderr[:300]}"

    def _timeout_message(self) -> str:
        return f"Claude CLI timed out after {self.timeout_s}s."
