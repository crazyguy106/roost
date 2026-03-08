"""Timezone utilities for the Telegram bot.

Uses REMINDER_TIMEZONE from config (default: Asia/Singapore) so that
datetime.now() always returns the user's wall-clock time, regardless of
the server's system timezone.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from roost.config import REMINDER_TIMEZONE

_LOCAL_TZ = ZoneInfo(REMINDER_TIMEZONE)


def get_local_now() -> datetime:
    """Return the current datetime in the configured local timezone."""
    return datetime.now(_LOCAL_TZ)


def to_local_dt(dt: datetime) -> datetime:
    """Convert a datetime to the configured local timezone.

    - If *dt* is timezone-aware, converts directly.
    - If *dt* is naive, assumes UTC first then converts.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_LOCAL_TZ)
