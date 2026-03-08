"""Subprocess executor for CLI commands (Claude, Gemini, rclone)."""

import asyncio
import logging
import time
from telegram import Message
from roost.config import AI_RATE_LIMIT
from roost import task_service

logger = logging.getLogger(__name__)

# Per-user rate limiting: {user_id: last_command_timestamp}
_rate_limits: dict[int, float] = {}

# Streaming config
STREAM_INTERVAL = 3  # seconds between message edits
MAX_MSG_LEN = 4000   # Telegram max is 4096, leave margin


class RateLimitError(Exception):
    def __init__(self, wait_seconds: int):
        self.wait_seconds = wait_seconds
        super().__init__(f"Rate limited. Try again in {wait_seconds}s.")


def check_rate_limit(user_id: int) -> None:
    """Raise RateLimitError if user is sending AI commands too fast."""
    now = time.time()
    last = _rate_limits.get(user_id, 0)
    elapsed = now - last
    if elapsed < AI_RATE_LIMIT:
        raise RateLimitError(int(AI_RATE_LIMIT - elapsed))
    _rate_limits[user_id] = now


def _truncate_output(text: str, limit: int = MAX_MSG_LEN) -> str:
    """Keep the tail of output if it exceeds the limit."""
    if len(text) <= limit:
        return text
    return "... [truncated]\n" + text[-(limit - 20):]


async def run_command(cmd: list[str], timeout: int = 120,
                      source: str = "telegram", user_id: str | None = None) -> str:
    """Run a subprocess and return its full output (non-streaming)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1024 * 1024,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()

        if len(output) > 3900:
            output = output[:3900] + "\n\n... [truncated]"

        task_service.log_command(
            source=source,
            command=" ".join(cmd),
            output=output[:2000],
            exit_code=proc.returncode,
            user_id=user_id,
        )
        return output or "(no output)"

    except asyncio.TimeoutError:
        return f"Command timed out after {timeout}s."
    except FileNotFoundError:
        return f"Command not found: {cmd[0]}"
    except Exception as e:
        return f"Error: {e}"


async def run_command_streaming(cmd: list[str], status_msg: Message,
                                timeout: int = 180, source: str = "telegram",
                                user_id: str | None = None) -> str:
    """Run a subprocess and stream output by editing a Telegram message.

    Edits status_msg every STREAM_INTERVAL seconds with accumulated output.
    Returns the final complete output.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1024 * 1024,
        )

        output_lines: list[str] = []
        last_edit = 0.0

        async def read_and_update():
            nonlocal last_edit
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                output_lines.append(line.decode("utf-8", errors="replace"))

                now = time.time()
                if now - last_edit >= STREAM_INTERVAL:
                    last_edit = now
                    text = "".join(output_lines).strip()
                    display = _truncate_output(text)
                    try:
                        await status_msg.edit_text(f"```\n{display}\n```",
                                                   parse_mode="Markdown")
                    except Exception:
                        # Edit can fail if content unchanged or rate limited
                        logger.debug("Stream edit failed (likely unchanged or rate limited)", exc_info=True)

        await asyncio.wait_for(read_and_update(), timeout=timeout)
        await proc.wait()

        full_output = "".join(output_lines).strip()

        task_service.log_command(
            source=source,
            command=" ".join(cmd),
            output=full_output[:2000],
            exit_code=proc.returncode,
            user_id=user_id,
        )

        return full_output or "(no output)"

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            logger.debug("Failed to kill timed-out process", exc_info=True)
        partial = "".join(output_lines).strip() if output_lines else ""
        return partial + f"\n\n[timed out after {timeout}s]"
    except FileNotFoundError:
        return f"Command not found: {cmd[0]}"
    except Exception as e:
        return f"Error: {e}"
