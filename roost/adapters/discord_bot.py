"""Discord bot adapter for Roost.

Runs as a standalone process: python -m roost.adapters.discord_bot

Requires: pip install discord.py
Config: DISCORD_BOT_TOKEN, DISCORD_ALLOWED_USERS (comma-separated user IDs)
"""

import asyncio
import logging

try:
    import discord
    from discord import Intents, Message
except ImportError:
    raise ImportError("discord.py is required: pip install discord.py")

from roost.config import DISCORD_BOT_TOKEN, DISCORD_ALLOWED_USERS
from roost.adapters import run_agent, truncate
from roost.events import subscribe, TASK_CREATED, TASK_UPDATED, TASK_COMPLETED

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("roost.discord")


class RoostDiscordBot(discord.Client):
    """Discord client that routes DMs and mentions to the Roost AI agent."""

    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        super().__init__(intents=intents)

    async def on_ready(self):
        logger.info("Discord bot ready: %s (ID: %s)", self.user.name, self.user.id)
        # Subscribe to event bus for notifications
        subscribe(TASK_CREATED, self._on_task_event)
        subscribe(TASK_UPDATED, self._on_task_event)
        subscribe(TASK_COMPLETED, self._on_task_event)
        logger.info("Event notifier subscribed")

    async def on_message(self, message: Message):
        # Ignore own messages
        if message.author == self.user:
            return

        # Check if this is a DM or a mention in a channel
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = self.user in message.mentions if not is_dm else False

        if not is_dm and not is_mention:
            return

        # Auth check
        if DISCORD_ALLOWED_USERS and message.author.id not in DISCORD_ALLOWED_USERS:
            await message.reply(
                f"You're not authorized. Your Discord ID: {message.author.id}\n"
                "Ask your admin to add you to DISCORD_ALLOWED_USERS."
            )
            return

        # Strip the mention from the message text
        prompt = message.content
        if is_mention:
            prompt = prompt.replace(f"<@{self.user.id}>", "").strip()

        if not prompt:
            return

        # Show typing indicator while processing
        async with message.channel.typing():
            response = await run_agent(
                prompt=prompt,
                user_id=str(message.author.id),
                platform="discord",
            )

        # Send response (split if too long)
        for chunk in _split_message(response):
            await message.reply(chunk)

    async def _on_task_event(self, data: dict) -> None:
        """Forward task events to authorized Discord users via DM."""
        if data.get("source") == "discord":
            return

        task = data.get("task")
        if not task:
            return

        source = data.get("source", "unknown")
        event_type = data.get("event_type", "updated")

        icons = {"task.created": "New task", "task.completed": "Task completed", "task.updated": "Task updated"}
        label = icons.get(event_type, "Task event")

        text = f"**{label}** (from {source}):\n#{task.id} {task.title}"

        for user_id in DISCORD_ALLOWED_USERS:
            try:
                user = await self.fetch_user(user_id)
                if user:
                    dm = await user.create_dm()
                    await dm.send(text)
            except Exception:
                logger.debug("Failed to DM Discord user %s", user_id, exc_info=True)


def _split_message(text: str, limit: int = 2000) -> list[str]:
    """Split a message into chunks that fit Discord's character limit."""
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at a newline
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks


def main():
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set in .env")
        return

    bot = RoostDiscordBot()
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
