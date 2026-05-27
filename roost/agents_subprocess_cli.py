"""Shared base for subprocess-driven CLI agent providers.

The platform-CLI providers (Claude Code, Gemini CLI, Codex CLI) all share
the same plumbing — spawn a binary per turn, pipe the user prompt over
stdin, parse newline-delimited JSON events on stdout, surface tool
calls/results to Roost via ``on_tool_event``, and persist a Roost
session_id → vendor session_id map across turns.

The differences are pure data:

- Argv layout (each CLI has its own flag names for output-format,
  session resume, MCP config, permission mode, model)
- Stream-json event schema (Claude wraps content blocks under
  ``message.content``; Gemini emits flat events with ``tool_id`` and
  ``parameters``; Codex differs again)
- Auth-state directory (~/.claude, ~/.gemini, ~/.codex)
- MCP config delivery (Claude takes ``--mcp-config <file>``; Gemini
  reads ``~/.gemini/settings.json``; Codex reads
  ``~/.codex/config.toml``)

So this module defines a ``BaseSubprocessCliAgent`` with the I/O loop
and a ``BaseStreamEventParser`` protocol for vendor-specific event
translation. Each provider lives in its own module
(``agents_claude_cli.py``, ``agents_gemini_cli.py``,
``agents_codex_cli.py``) and only writes the parts that actually
differ.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable

logger = logging.getLogger("roost.agents_subprocess_cli")

# Process-wide lock — vendor auth dirs (~/.claude/, ~/.gemini/, ~/.codex/)
# aren't designed for concurrent CLI invocations. One lock across all
# providers is overkill in theory but free in practice and removes a
# whole class of cross-provider footguns.
_CLI_LOCK = asyncio.Lock()


# ── Session-map helpers (Roost session_id → vendor session_id) ──────

def load_sessions(path: str) -> dict[str, str]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sessions(path: str, sessions: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(sessions, f)
    os.replace(tmp, path)


def get_session_id(path: str, roost_session_id: str | None) -> str | None:
    if not roost_session_id:
        return None
    return load_sessions(path).get(roost_session_id)


def set_session_id(path: str, roost_session_id: str | None,
                   vendor_session_id: str) -> None:
    if not roost_session_id or not vendor_session_id:
        return
    sessions = load_sessions(path)
    sessions[roost_session_id] = vendor_session_id
    save_sessions(path, sessions)


# ── Truncation helpers shared across parsers ────────────────────────

def truncate(value: Any, limit: int = 200) -> Any:
    try:
        s = json.dumps(value, default=str)
    except Exception:
        s = str(value)
    if len(s) <= limit:
        return value
    return s[:limit] + "..."


def truncate_text(value: Any, limit: int = 500) -> str:
    s = "" if value is None else str(value)
    return s if len(s) <= limit else s[:limit] + "..."


# ── Parser protocol ─────────────────────────────────────────────────

class BaseStreamEventParser:
    """Subclasses translate vendor stream-json events into Roost's
    ``on_progress`` text and ``on_tool_event`` payloads.

    Public attributes (set during parsing):
      - ``final_text``: the assistant's final text output.
      - ``captured_session_id``: vendor session id from an init/result
        event (used to update the session map after the process exits).
      - ``tool_starts``: dict of tool-call-id → perf-counter start time,
        used to compute ``duration_ms`` on the matching result event.
    """

    def __init__(self) -> None:
        self.final_text: str = ""
        self.captured_session_id: str | None = None
        self.tool_starts: dict[str, float] = {}

    async def handle(
        self,
        event: dict,
        on_progress: Callable | None = None,
        on_tool_event: Callable | None = None,
    ) -> None:
        raise NotImplementedError


# ── Base agent ──────────────────────────────────────────────────────

class BaseSubprocessCliAgent:
    """Subprocess-per-turn agent base. Subclasses define vendor specifics
    via class attributes and the two abstract methods below.

    Conforms to the same ``run()`` signature as ``ClaudeAgent`` /
    ``OpenAIAgent`` / ``GeminiAgent`` so it can plug into ``create_agent``
    without callers caring which CLI is behind it.
    """

    # Subclass overrides ────────────────────────────────────────────
    provider_id: str = "subprocess_cli"
    default_bin: str = ""
    default_auth_dir: str = ""           # e.g. "/home/dev/.claude"
    default_timeout_s: int = 300

    def __init__(
        self,
        system_prompt: str = "",
        session_id: str | None = None,
        include_agent_tools: bool = False,  # parity, unused
        api_key: str = "",                  # parity, unused
        model: str = "",
        tool_scope: str = "",
    ):
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.model = model
        self.tool_scope = tool_scope

        self.bin = self._env("BIN") or self.default_bin
        self.mcp_config_path = self._env("MCP_CONFIG") or ""
        self.permission_mode = self._env("PERMISSION_MODE") or "yolo"
        self.timeout_s = int(self._env("TIMEOUT") or self.default_timeout_s)

    # Helpers ───────────────────────────────────────────────────────

    @classmethod
    def _env_prefix(cls) -> str:
        return cls.provider_id.upper()

    @classmethod
    def _env(cls, suffix: str) -> str | None:
        return os.environ.get(f"{cls._env_prefix()}_{suffix}")

    @classmethod
    def session_file_path(cls) -> str:
        override = cls._env("SESSION_FILE")
        if override:
            return override
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", f"{cls.provider_id}_sessions.json",
        )

    # Vendor specifics ──────────────────────────────────────────────

    def _build_cmd(self, existing_session: str | None) -> tuple[list[str], str | None]:
        """Return (argv, provisional_session_id).

        ``provisional_session_id`` is non-None when the agent assigns a
        session id at process-start time (e.g. Claude --session-id) and
        should be persisted even if the vendor never echoes one back.
        Return None when the vendor assigns ids server-side (Gemini,
        Codex).
        """
        raise NotImplementedError

    def _make_parser(self) -> BaseStreamEventParser:
        raise NotImplementedError

    def _missing_binary_message(self) -> str:
        return (
            f"{self.provider_id} binary '{self.bin}' not found. "
            f"Ensure the CLI is installed and on $PATH."
        )

    def _error_exit_message(self, rc: int, stderr: str) -> str:
        return f"{self.provider_id} exited {rc}: {stderr[:300]}"

    def _timeout_message(self) -> str:
        return f"{self.provider_id} timed out after {self.timeout_s}s."

    # Public entrypoint ─────────────────────────────────────────────

    async def run(
        self,
        user_prompt: str,
        user_id: str = "",
        on_progress: Callable | None = None,
        confirmation_callback: Callable | None = None,
        on_tool_event: Callable | None = None,
    ) -> str:
        async with _CLI_LOCK:
            return await self._run_locked(user_prompt, on_progress, on_tool_event)

    async def _run_locked(
        self,
        user_prompt: str,
        on_progress: Callable | None,
        on_tool_event: Callable | None,
    ) -> str:
        existing = get_session_id(self.session_file_path(), self.session_id)
        cmd, provisional_sid = self._build_cmd(existing)
        logger.info("%s CLI: %s", self.provider_id,
                    " ".join(cmd[:10]) + (" ..." if len(cmd) > 10 else ""))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=10 * 1024 * 1024,
            )
        except FileNotFoundError:
            return self._missing_binary_message()

        assert proc.stdin is not None
        proc.stdin.write(user_prompt.encode("utf-8"))
        proc.stdin.close()

        parser = self._make_parser()

        try:
            assert proc.stdout is not None
            while True:
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=self.timeout_s,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    try:
                        await proc.wait()
                    except Exception:
                        pass
                    return self._timeout_message()

                if not raw:
                    break

                try:
                    event = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.debug("Non-JSON %s line: %r",
                                 self.provider_id, raw[:200])
                    continue

                await parser.handle(event, on_progress, on_tool_event)

            rc = await proc.wait()
            if rc != 0:
                assert proc.stderr is not None
                stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
                logger.error("%s CLI exited %d: %s",
                             self.provider_id, rc, stderr[:500])
                if parser.final_text:
                    return parser.final_text
                return self._error_exit_message(rc, stderr)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass

        sid_to_save = parser.captured_session_id or provisional_sid
        if sid_to_save:
            try:
                set_session_id(self.session_file_path(),
                               self.session_id, sid_to_save)
            except Exception:
                logger.debug("Session save failed", exc_info=True)

        return parser.final_text or "(no response)"
