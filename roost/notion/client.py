"""Singleton Notion client with rate limiting and retry logic.

Rate limit: 3 requests/second (Notion API limit).
Retries on 429, 502, 503 with exponential backoff.
"""

import logging
import threading
import time

logger = logging.getLogger("roost.notion.client")

_client = None
_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 0.34  # ~3 req/sec


def get_client():
    """Get or create the singleton Notion client.

    Returns None if notion-client is not installed or token is missing.
    """
    global _client

    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        try:
            from notion_client import Client
            from roost.config import NOTION_API_TOKEN

            if not NOTION_API_TOKEN:
                logger.warning("NOTION_API_TOKEN not set")
                return None

            _client = Client(auth=NOTION_API_TOKEN)
            # Verify connection
            _client.users.me()
            logger.info("Notion client initialized successfully")
            return _client
        except ImportError:
            logger.warning("notion-client package not installed")
            return None
        except Exception:
            logger.exception("Failed to initialize Notion client")
            _client = None
            return None


def rate_limited_call(func, *args, **kwargs):
    """Execute a Notion API call with rate limiting and retry.

    Enforces minimum interval between requests and retries on
    429 (rate limit) and 5xx errors.
    """
    global _last_request_time

    max_retries = 3
    for attempt in range(max_retries):
        # Rate limiting
        with _lock:
            now = time.time()
            elapsed = now - _last_request_time
            if elapsed < _MIN_INTERVAL:
                time.sleep(_MIN_INTERVAL - elapsed)
            _last_request_time = time.time()

        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_str = str(e)
            status = getattr(e, "status", 0) or 0

            # Retry on rate limit or server errors
            if status in (429, 502, 503) or "rate" in error_str.lower():
                wait = (2 ** attempt) * 1.0
                if status == 429:
                    # Try to extract retry-after
                    retry_after = getattr(e, "headers", {}).get("Retry-After")
                    if retry_after:
                        wait = max(float(retry_after), wait)
                logger.warning(
                    "Notion API %d, retrying in %.1fs (attempt %d/%d)",
                    status, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                continue

            # Non-retryable error
            raise

    # All retries exhausted
    raise RuntimeError(f"Notion API call failed after {max_retries} retries")
