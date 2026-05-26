#!/usr/bin/env python3
"""End-to-end smoke for the WhatsApp inbound pipeline.

Skips the HTTP / HMAC layer (already covered by tests/test_whatsapp_inbound.py)
and calls the inbound processor directly with a Meta-shaped message dict, so
you can watch the full pipeline run locally:

    parsed message → leads.ingest_lead → enrollment → tick → Telegram prompt

Usage:
    python3 scripts/smoke_whatsapp_inbound.py
    python3 scripts/smoke_whatsapp_inbound.py --phone +6591234567 --name "Alice"
    python3 scripts/smoke_whatsapp_inbound.py --text "Hi, I want a 3BR in D9" --cleanup

Side effects (against live Attio): one test person + one deal + one note,
and one nurture enrollment in the property cadence. The first cadence step
is held for approval — nothing is sent until you tap ✅ on Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _run(args) -> int:
    from roost.extras.crm.services import get_provider
    from roost.extras.lead_nurture.services.cadences import (
        get_enrollment, list_enrollments, update_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import tick
    from roost.extras.messaging_external.web.api_whatsapp import _process_inbound

    active = get_provider().__class__.__name__
    print(f"  CRM provider: {active}")

    msg = {
        "sender": args.phone,
        "sender_name": args.name,
        "message_id": f"smoke.wamid.{int(datetime.now().timestamp())}",
        "text": args.text,
    }
    print(f"→ Simulating inbound WhatsApp from {args.phone} ({args.name})…")
    print(f"  text: {args.text!r}")

    # _process_inbound calls leads.ingest_lead internally and then runs the
    # recipe pipeline (or just classifies + notifies if no recipe).
    await _process_inbound(msg)
    print("  _process_inbound returned.")

    # Find the enrollment that was just created for this phone.
    paused = list_enrollments(status="paused", limit=20)
    matching = [
        e for e in paused if (e.get("contact_phone") or "") == args.phone
    ]
    if not matching:
        # Maybe it auto-sent (preapproval) and is already 'active'.
        active_enr = list_enrollments(status="active", limit=20)
        matching = [
            e for e in active_enr if (e.get("contact_phone") or "") == args.phone
        ]
    if not matching:
        print("✗ No enrollment found for that phone. Either ingest_lead "
              "skipped (existing person already enrolled in this cadence?) "
              "or the cadence pick failed.")
        return 1

    enr = matching[0]
    enrollment_id = enr["id"]
    print(f"  Enrollment #{enrollment_id}: cadence={enr['cadence_slug']} "
          f"channel={enr.get('channel')} status={enr['status']} "
          f"next_run_at={enr['next_run_at']}")

    print("→ Accelerating next_run_at to now and ticking…")
    update_enrollment(enrollment_id, next_run_at=_utc_now_iso())
    out = tick()
    print("  tick →", json.dumps(out, default=str)[:300])

    enr = get_enrollment(enrollment_id)
    print(f"  Post-tick: status={enr['status']} step={enr['current_step']}")

    if enr["status"] == "paused":
        print("\n✓ Step held for approval. Telegram should show a prompt with")
        print(f"  ✅/⏭ buttons for enrollment #{enrollment_id}.")
        print(f"  Or run: /napprove {enrollment_id}  |  /nskip {enrollment_id}")
    elif enr["status"] == "active":
        print("\n✓ Step auto-sent (preapproval matched).")
    elif enr["status"] == "completed":
        print("\n✓ Cadence completed.")

    if args.cleanup:
        update_enrollment(
            enrollment_id, status="exited",
            pause_reason="smoke_whatsapp_inbound cleanup",
        )
        print(f"→ Cleaned up enrollment #{enrollment_id}.")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phone", default="+6591234567",
                   help="Sender phone in E.164. Default: +6591234567 "
                        "(must be a libphonenumber-valid number for the "
                        "country, not just well-formed E.164).")
    p.add_argument("--name", default="WA Smoke Test",
                   help="Sender display name (Meta's profile name).")
    p.add_argument("--text",
                   default="Hi, I'm looking for a 3-bedroom condo in D9 "
                           "under 3M. Available this weekend?",
                   help="Inbound message body.")
    p.add_argument("--cleanup", action="store_true",
                   help="Mark the enrollment as 'exited' after the tick.")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
