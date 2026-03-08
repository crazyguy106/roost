"""Scheduled Notion poller: pull changes and process retry queue.

Called periodically by the scheduler (every NOTION_POLL_INTERVAL seconds).
"""

import logging

logger = logging.getLogger("roost.notion.poller")


def poll_notion_changes() -> None:
    """Poll Notion for changes and process the retry queue.

    This is the main entry point called by the scheduler.
    """
    from roost.notion import is_notion_available

    if not is_notion_available():
        return

    try:
        from roost.notion.sync import pull_changes, process_retry_queue

        # Pull changes from Notion -> SQLite
        pulled = pull_changes()
        if pulled:
            logger.info("Pulled %d changes from Notion", pulled)

        # Retry failed pushes
        retried = process_retry_queue()
        if retried:
            logger.info("Retried %d failed pushes", retried)

    except Exception:
        logger.exception("Notion poll cycle failed")
