#!/usr/bin/env python3
"""End-to-end smoke for the FA-edition Chatwoot adapter.

Builds a real Chatwoot `message_created`/`incoming` envelope, signs it with
the `CHATWOOT_WEBHOOK_SECRET` from .env (so the signature check runs end-to-end
just like a real Chatwoot delivery), and POSTs it at a running Roost. Then
polls `/api/leads` until the new lead appears.

The default text and phone target a financial-adviser scenario (FA edition).

Usage:
    python3 scripts/smoke_chatwoot.py
    python3 scripts/smoke_chatwoot.py --phone +6598765432
    python3 scripts/smoke_chatwoot.py --message "Hi, can you advise on endowment policies?"
    ROOST_URL=https://roost.ethanseow.com python3 scripts/smoke_chatwoot.py

What it does NOT do:
- Send anything outbound (the inbound just lands as a lead — no Chatwoot REST
  calls are made by the smoke).
- Touch a real Chatwoot install. The webhook target is Roost's webhook URL;
  no Chatwoot account needs to be online.

Exit codes:
  0 — webhook accepted, lead landed.
  1 — config missing or webhook rejected.
  2 — webhook accepted but lead didn't appear within --timeout seconds.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx


def _read_secret_from_env_file(env_path: Path) -> str:
    """Pull CHATWOOT_WEBHOOK_SECRET out of .env without printing the value.
    Returns empty string if the key is missing or holds a CHANGE_ME sentinel."""
    if not env_path.exists():
        return ""
    pat = re.compile(r"^CHATWOOT_WEBHOOK_SECRET=(.+)$")
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = pat.match(line)
            if m:
                val = m.group(1).strip().strip('"').strip("'")
                if val.startswith("CHANGE_ME"):
                    return ""
                return val
    except OSError:
        pass
    return ""


def _build_payload(phone: str, message: str, inbox_id: int, account_id: int) -> dict:
    """A minimal-but-real Chatwoot 4.14.1-shaped `message_created`/`incoming`
    envelope. Matches the captured sample in docs/chatwoot-webhook-samples/."""
    contact = {
        "id": 99,
        "name": "FA Smoke Contact",
        "phone_number": phone,
        "email": "",
        "type": "contact",
    }
    return {
        "event": "message_created",
        "message_type": "incoming",
        "content": message,
        "content_type": "text",
        "account": {"id": account_id, "name": "FA Smoke"},
        "inbox": {"id": inbox_id, "name": "WhatsApp (smoke)"},
        "conversation": {
            "id": 9001,
            "inbox_id": inbox_id,
            "channel": "Channel::Whatsapp",
            "status": "open",
            "contact_inbox": {
                "contact_id": 99,
                "inbox_id": inbox_id,
                "source_id": phone,
            },
            "messages": [],
            "meta": {"sender": contact},
        },
        "sender": contact,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--url",
        default=os.environ.get("ROOST_URL", "http://127.0.0.1:8080"),
        help="Roost base URL. Default: http://127.0.0.1:8080 "
             "(or $ROOST_URL).",
    )
    p.add_argument("--phone", default="+6591234567",
                   help="Sender phone in E.164. Default: +6591234567.")
    p.add_argument(
        "--message",
        default="Hi, I'd like to learn more about retirement planning options.",
        help="Inbound message body — defaults to a financial-adviser ask.",
    )
    p.add_argument("--inbox-id", type=int, default=2,
                   help="Chatwoot inbox id in the envelope. Default: 2.")
    p.add_argument("--account-id", type=int, default=1,
                   help="Chatwoot account id in the envelope. Default: 1.")
    p.add_argument("--timeout", type=float, default=15.0,
                   help="Seconds to poll /api/leads after the webhook. Default 15.")
    args = p.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    secret = _read_secret_from_env_file(repo_root / ".env")
    if not secret:
        print("✗ CHATWOOT_WEBHOOK_SECRET missing or still CHANGE_ME in .env.",
              file=sys.stderr)
        print("  Set it to whatever Chatwoot generated when you created the",
              file=sys.stderr)
        print("  webhook (Settings → Integrations → Webhooks → Edit).",
              file=sys.stderr)
        return 1

    payload = _build_payload(args.phone, args.message,
                             args.inbox_id, args.account_id)
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    digest = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(),
                      hashlib.sha256).hexdigest()
    sig = f"sha256={digest}"

    webhook_url = args.url.rstrip("/") + "/api/chatwoot/webhook"
    print(f"→ POST {webhook_url}")
    print(f"  phone:   {args.phone}")
    print(f"  message: {args.message!r}")

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                webhook_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Chatwoot-Timestamp": ts,
                    "X-Chatwoot-Signature": sig,
                    "X-Chatwoot-Delivery": str(uuid.uuid4()),
                    "User-Agent": "Ruby",
                },
            )
    except httpx.HTTPError as e:
        print(f"✗ HTTP error talking to {webhook_url}: {e}", file=sys.stderr)
        return 1

    if resp.status_code == 401:
        print("✗ 401 — the CHATWOOT_WEBHOOK_SECRET in .env doesn't match what",
              file=sys.stderr)
        print("  Roost is checking with. Re-copy from the Chatwoot UI.",
              file=sys.stderr)
        return 1
    if resp.status_code == 403:
        print("✗ 403 — CHATWOOT_ENABLED is off on the server. Flip it on in",
              file=sys.stderr)
        print("  Settings → Feature Flags (or .env) and restart Roost.",
              file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"✗ {resp.status_code} — webhook rejected.", file=sys.stderr)
        try:
            print(f"  body: {resp.json()}", file=sys.stderr)
        except Exception:
            print(f"  body: {resp.text[:200]!r}", file=sys.stderr)
        return 1

    print(f"✓ 200 — webhook accepted ({resp.json()})")

    # Poll /api/leads for the new entry. Free-text search by phone keeps the
    # query small; the FA edition's local provider stores phone exactly as sent.
    leads_url = args.url.rstrip("/") + "/api/leads"
    deadline = time.monotonic() + args.timeout
    print(f"→ Polling {leads_url} for phone={args.phone} (up to {args.timeout}s)…")
    found = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(leads_url, params={"q": args.phone})
            if r.status_code == 200:
                rows = r.json()
                rows = rows.get("leads", rows) if isinstance(rows, dict) else rows
                for row in rows or []:
                    if (row.get("contact_phone") or row.get("phone") or "") == args.phone:
                        found = row
                        break
            if found:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1.0)

    if not found:
        print(f"✗ No lead with phone={args.phone} appeared within {args.timeout}s.",
              file=sys.stderr)
        print("  Check roost logs:", file=sys.stderr)
        print("    docker compose -f docker-compose.yml -f docker-compose.fa.yml logs roost",
              file=sys.stderr)
        return 2

    print(f"✓ Lead landed: id={found.get('id')} "
          f"channel={found.get('channel')} stage={found.get('stage')}")
    print("")
    print("  Open the Roost UI to inspect:")
    print(f"    {args.url.rstrip('/')}/leads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
