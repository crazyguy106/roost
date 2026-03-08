"""Telegram bot scheduler — morning digest, deadline reminders, urgency recalc.

Uses python-telegram-bot's built-in JobQueue (backed by APScheduler).
"""

import logging
from datetime import datetime, time, timedelta, timezone
from telegram.ext import Application
from roost.config import (
    TELEGRAM_ALLOWED_USERS,
    MORNING_DIGEST_HOUR,
    MORNING_DIGEST_MINUTE,
    REMINDER_TIMEZONE,
)

logger = logging.getLogger("roost.scheduler")


def init_scheduler(app: Application) -> None:
    """Set up recurring jobs on the bot's JobQueue."""
    try:
        import pytz
        tz = pytz.timezone(REMINDER_TIMEZONE)
    except Exception:
        tz = None

    jq = app.job_queue
    if not jq:
        logger.warning("JobQueue not available — scheduler disabled")
        return

    # 1. Morning digest — daily at configured time
    digest_time = time(
        hour=MORNING_DIGEST_HOUR,
        minute=MORNING_DIGEST_MINUTE,
        tzinfo=tz,
    )
    jq.run_daily(_morning_digest, time=digest_time, name="morning_digest")
    logger.info(
        "Scheduled morning digest at %02d:%02d %s",
        MORNING_DIGEST_HOUR, MORNING_DIGEST_MINUTE, REMINDER_TIMEZONE,
    )

    # 2. Deadline reminders — every 30 minutes
    jq.run_repeating(_deadline_reminders, interval=1800, first=60, name="deadline_reminders")
    logger.info("Scheduled deadline reminders (every 30 min)")

    # 3. Urgency recalculation — every 6 hours
    jq.run_repeating(_urgency_recalc, interval=21600, first=300, name="urgency_recalc")
    logger.info("Scheduled urgency recalculation (every 6h)")

    # 4. Curriculum auto-scan — every 24 hours (if enabled)
    try:
        from roost.config import CURRICULUM_ENABLED
        if CURRICULUM_ENABLED:
            jq.run_repeating(_curriculum_scan, interval=86400, first=60, name="curriculum_scan")
            logger.info("Scheduled curriculum scan (every 24h)")
    except (ImportError, AttributeError):
        pass

    # 8. Check-in prompts — every hour, checks if enabled per user
    jq.run_repeating(_checkin_prompt, interval=3600, first=600, name="checkin_prompt")
    logger.info("Scheduled check-in prompt (every 1h, user-configurable)")

    # 5. Notion poller — if enabled (every NOTION_POLL_INTERVAL seconds)
    try:
        from roost.config import NOTION_SYNC_ENABLED, NOTION_POLL_INTERVAL
        if NOTION_SYNC_ENABLED:
            jq.run_repeating(
                _notion_poll, interval=NOTION_POLL_INTERVAL,
                first=120, name="notion_poll",
            )
            logger.info("Scheduled Notion poller (every %ds)", NOTION_POLL_INTERVAL)
    except (ImportError, AttributeError):
        pass

    # 6. Gmail inbox poller — if enabled (every GMAIL_POLL_INTERVAL seconds)
    try:
        from roost.config import GMAIL_ENABLED, GMAIL_POLL_INTERVAL
        if GMAIL_ENABLED:
            jq.run_repeating(
                _gmail_inbox_poll, interval=GMAIL_POLL_INTERVAL,
                first=180, name="gmail_inbox_poll",
            )
            logger.info("Scheduled Gmail inbox poller (every %ds)", GMAIL_POLL_INTERVAL)
    except (ImportError, AttributeError):
        pass

    # 7. Otter transcript poller — if Dropbox configured
    try:
        from roost.dropbox_client import is_dropbox_available
        from roost.config import OTTER_POLL_INTERVAL
        if is_dropbox_available():
            jq.run_repeating(
                _otter_poll, interval=OTTER_POLL_INTERVAL,
                first=90, name="otter_poll",
            )
            logger.info("Scheduled Otter transcript poller (every %ds)", OTTER_POLL_INTERVAL)
    except (ImportError, AttributeError):
        pass

    # 9. Scheduled email sender — every 60 seconds
    jq.run_repeating(_send_scheduled_emails, interval=60, first=30, name="scheduled_emails")
    logger.info("Scheduled email sender (every 60s)")

    logger.info("Scheduler initialized")


async def _send_to_all(context, text: str) -> None:
    """Send a message to all authorized Telegram users."""
    for user_id in TELEGRAM_ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception:
            logger.exception("Failed to send to user %s", user_id)


async def _morning_digest(context) -> None:
    """Daily morning briefing with triage + calendar."""
    try:
        from roost.triage import get_today_tasks
        from roost.calendar_service import get_today_events
        from roost.task_service import get_shutdown_summary

        # If shutdown was active yesterday, prompt to resume
        shutdown = get_shutdown_summary()
        if shutdown:
            lines = [
                "Good morning! Your day was paused yesterday.\n",
                f"{shutdown['paused_count']} task(s) still paused.",
                "Use /resumeday to restore them.\n",
            ]
            await _send_to_all(context, "\n".join(lines))
            return

        triage = get_today_tasks()
        events = get_today_events()

        lines = [
            "Good morning! Here's your daily briefing:\n",
            "_Life provides. You respond. The wake forms behind you._ (以悟归空)\n",
        ]

        # Overdue
        overdue = triage.get("overdue", [])
        if overdue:
            lines.append(f"OVERDUE ({len(overdue)}):")
            for t in overdue[:5]:
                lines.append(f"  #{t['id']} {t['title']}")

        # Due today
        due = triage.get("due_today", [])
        if due:
            lines.append(f"\nDue today ({len(due)}):")
            for t in due[:5]:
                lines.append(f"  #{t['id']} {t['title']}")

        # In progress
        wip = triage.get("in_progress", [])
        if wip:
            lines.append(f"\nIn progress ({len(wip)}):")
            for t in wip[:3]:
                ctx = f" — {t.get('context_note', '')}" if t.get("context_note") else ""
                lines.append(f"  #{t['id']} {t['title']}{ctx}")

        # Calendar events
        if events:
            lines.append(f"\nCalendar ({len(events)}):")
            for e in events[:5]:
                start = e["start"].strftime("%H:%M") if e.get("start") else "?"
                lines.append(f"  {start} {e['summary']}")

        # Suggested focus
        top = triage.get("top_urgent", [])
        if top:
            lines.append(f"\nSuggested focus:")
            t = top[0]
            lines.append(f"  #{t['id']} {t['title']}")

        # Streak info
        try:
            from roost.task_service import get_streak, get_spoon_status
            streak = get_streak()
            if streak["current"] > 1:
                lines.append(f"\nStreak: Day {streak['current']} (best: {streak['best']})")
            spoon = get_spoon_status()
            lines.append(f"Spoon budget: {spoon['budget']}/{spoon['budget']}")
        except Exception:
            logger.debug("Failed to fetch streak/spoon info for morning digest", exc_info=True)

        if len(lines) == 1:
            lines.append("Nothing urgent today! Enjoy your day.")

        await _send_to_all(context, "\n".join(lines))
        logger.info("Sent morning digest")

    except Exception:
        logger.exception("Morning digest failed")


async def _deadline_reminders(context) -> None:
    """Send escalating reminders for upcoming deadlines."""
    try:
        # Skip if shutdown is active
        from roost.task_service import is_shutdown_active
        if is_shutdown_active():
            return

        from roost.database import get_connection

        conn = get_connection()
        now = datetime.now()

        # Find tasks with deadlines within the next 24 hours that aren't done
        upcoming = conn.execute(
            """SELECT id, title, deadline, priority FROM tasks
               WHERE status != 'done' AND deadline IS NOT NULL
               AND deadline > ? AND deadline <= ?""",
            (now.strftime("%Y-%m-%d %H:%M:%S"),
             (now + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        conn.close()

        for row in upcoming:
            task = dict(row)
            try:
                dl = datetime.strptime(task["deadline"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                try:
                    dl = datetime.strptime(task["deadline"][:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

            delta = dl - now
            hours = delta.total_seconds() / 3600

            # Only send at specific thresholds (within the 30-min check window)
            if hours <= 0.25:  # 15 min
                urgency = "URGENT"
            elif hours <= 3:
                urgency = "Soon"
            elif hours <= 24:
                urgency = "Today"
            else:
                continue

            # Avoid spamming: only alert at threshold crossings
            # 15min: alert if 0 < hours <= 0.5
            # 3h: alert if 2.5 < hours <= 3.5
            # 24h: alert if 23.5 < hours <= 24.5
            should_alert = (
                (hours <= 0.5) or
                (2.5 < hours <= 3.5) or
                (23.5 < hours <= 24.5)
            )

            if should_alert:
                time_str = dl.strftime("%H:%M")
                text = f"Deadline {urgency}: #{task['id']} {task['title']} (due {time_str})"
                await _send_to_all(context, text)

    except Exception:
        logger.exception("Deadline reminder check failed")


async def _urgency_recalc(context) -> None:
    """Batch recompute urgency scores."""
    try:
        from roost.triage import recalculate_all_urgency_scores
        count = recalculate_all_urgency_scores()
        logger.info("Recalculated urgency for %d tasks", count)
    except Exception:
        logger.exception("Urgency recalculation failed")


async def _curriculum_scan(context) -> None:
    """Periodic scan for new curricula in ~/projects/."""
    try:
        from roost import task_service
        results = task_service.scan_and_register_curricula()
        logger.info("Curriculum scan complete: %d registered", len(results))
    except Exception:
        logger.exception("Curriculum scan failed")


async def _gmail_inbox_poll(context) -> None:
    """Poll Gmail inbox for [task]/[note] emails."""
    try:
        from roost.gmail.poller import poll_inbox
        created = poll_inbox()
        if created:
            logger.info("Gmail poll: %d items created from email", created)
    except ImportError:
        pass
    except Exception:
        logger.exception("Gmail inbox poll failed")


async def _otter_poll(context) -> None:
    """Poll Dropbox for Otter.ai transcripts and meeting summaries."""
    # A. Voice note transcript upgrades
    try:
        from roost.otter_poll import poll_otter_transcripts
        updated = poll_otter_transcripts()
        if updated:
            logger.info("Otter poll: updated %d notes", updated)
            await _send_to_all(
                context,
                f"Otter.ai updated {updated} voice note(s) with high-quality transcripts."
            )
    except ImportError:
        pass
    except Exception:
        logger.exception("Otter transcript poll failed")

    # B. Meeting summary ingest (Zapier → Dropbox)
    try:
        from roost.otter_poll import poll_otter_summaries
        created = poll_otter_summaries()
        if created:
            logger.info("Otter summary poll: created %d notes", created)
            await _send_to_all(
                context,
                f"Otter.ai captured {created} new meeting summary/summaries as notes."
            )
    except ImportError:
        pass
    except Exception:
        logger.exception("Otter summary poll failed")


async def _notion_poll(context) -> None:
    """Poll Notion for changes and process retry queue."""
    try:
        from roost.notion.poller import poll_notion_changes
        poll_notion_changes()
        logger.info("Notion poll complete")
    except ImportError:
        pass
    except Exception:
        logger.exception("Notion poll failed")


async def _checkin_prompt(context) -> None:
    """Periodic check-in prompt — asks user what they're working on."""
    try:
        from roost.task_service import (
            get_setting, is_shutdown_active, list_tasks,
        )

        # Skip if shutdown is active
        if is_shutdown_active():
            return

        # Check if user has enabled check-ins
        enabled = get_setting("checkin_enabled")
        if enabled != "1":
            return

        # Check work hours (default 9-18)
        now = datetime.now()
        start_hour = int(get_setting("checkin_start_hour") or "9")
        end_hour = int(get_setting("checkin_end_hour") or "18")
        if not (start_hour <= now.hour < end_hour):
            return

        # Check interval (default 2h)
        interval_hours = int(get_setting("checkin_interval_hours") or "2")
        last_checkin = get_setting("checkin_last_sent")
        if last_checkin:
            try:
                last_dt = datetime.fromisoformat(last_checkin)
                if (now - last_dt).total_seconds() < interval_hours * 3600:
                    return
            except ValueError:
                pass

        # Build check-in message
        from roost.task_service import set_setting
        set_setting("checkin_last_sent", now.isoformat(timespec="seconds"))

        wip = list_tasks(status="in_progress")

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        if wip:
            task = wip[0]
            msg = f'Quick check-in: still working on "{task.title}"?'
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Yes", callback_data="checkin:yes"),
                    InlineKeyboardButton("Switch task", callback_data="checkin:switch"),
                ],
                [
                    InlineKeyboardButton("Taking a break", callback_data="checkin:break"),
                    InlineKeyboardButton("Snooze 1h", callback_data="checkin:snooze"),
                ],
            ])
        else:
            msg = "What are you working on right now?"
            top = list_tasks(status="todo", order_by="urgency", limit=3, exclude_paused_projects=True)
            buttons = [
                [InlineKeyboardButton(
                    f"#{t.id} {t.title[:30]}",
                    callback_data=f"next:start:{t.id}",
                )]
                for t in top
            ]
            if not buttons:
                return  # No tasks to suggest
            keyboard = InlineKeyboardMarkup(buttons)

        for user_id in TELEGRAM_ALLOWED_USERS:
            try:
                await context.bot.send_message(
                    chat_id=user_id, text=msg, reply_markup=keyboard,
                )
            except Exception:
                logger.exception("Failed to send check-in to user %s", user_id)

    except Exception:
        logger.exception("Check-in prompt failed")


async def _send_scheduled_emails(context) -> None:
    """Process due scheduled emails and notify user."""
    try:
        from roost.services.scheduled_emails import process_due_emails

        sent = process_due_emails()
        if sent:
            await _send_to_all(context, f"Sent {sent} scheduled email(s).")
            logger.info("Scheduled email sender: sent %d emails", sent)
    except Exception:
        logger.exception("Scheduled email sender failed")
