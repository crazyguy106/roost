#!/usr/bin/env python3
"""End-to-end smoke test for lead nurture.

Seeds a fake lead, accelerates its first cadence step to "now", runs one
nurture tick, and prints what happened. The goal is to see the Telegram
approval prompt appear on your phone within a couple of seconds without
waiting for the 60s scheduler.

Usage:
    python3 scripts/smoke_nurture.py --vertical generic
    python3 scripts/smoke_nurture.py --vertical property --cleanup
    python3 scripts/smoke_nurture.py --email me@example.com --name "Jane Doe"

Runs against whatever CRM provider is active. The first cadence step is
always held for approval (no outbound message is sent), so the only side
effect against live Attio is one test person + deal + note that you can
delete from the workspace afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vertical", default="generic",
                   choices=["generic", "property", "financial_advisor"])
    p.add_argument("--email", default="smoke+test@example.com")
    p.add_argument("--name", default="Smoke Test")
    p.add_argument("--phone", default="")
    p.add_argument("--source", default="smoke_test")
    p.add_argument("--cleanup", action="store_true",
                   help="Mark the enrollment as 'exited' after the tick.")
    args = p.parse_args()

    # Show what's about to happen — including which CRM gets written to.
    from roost.extras.crm.services import get_provider
    from roost.extras.lead_nurture.services.cadences import list_preapprovals
    active = get_provider().__class__.__name__
    print(f"  CRM provider: {active}")
    if "local" not in active.lower():
        rules = list_preapprovals()
        print(f"  Will create: 1 person + 1 deal + 1 note in your live CRM.")
        print(f"  Preapproval rules in DB: {len(rules)} "
              "(if any match this lead, the first step will auto-send "
              "instead of holding for approval).")

    from roost.extras.lead_nurture.services.leads import ingest_lead
    from roost.extras.lead_nurture.services.cadences import (
        update_enrollment, get_enrollment, list_enrollments,
    )
    from roost.extras.lead_nurture.services.nurture import tick

    print(f"→ Ingesting lead (vertical={args.vertical}, email={args.email})…")
    res = ingest_lead(
        channel="manual_test",
        email=args.email,
        phone=args.phone,
        name=args.name,
        vertical=args.vertical,
        source=args.source,
        message_text=f"Smoke-test inbound — {_utc_now_iso()}",
    )
    print("  ingest_lead →", json.dumps(res, default=str)[:300])

    enrollment_id = res.get("enrollment_id")
    if not enrollment_id:
        print("✗ No enrollment_id returned — ingest may have skipped it.",
              file=sys.stderr)
        return 1

    enr = get_enrollment(enrollment_id)
    print(f"  Enrollment #{enrollment_id}: cadence={enr['cadence_slug']} "
          f"channel={enr.get('channel')} step={enr['current_step']} "
          f"next_run_at={enr['next_run_at']}")

    print("→ Accelerating next_run_at to now…")
    update_enrollment(enrollment_id, next_run_at=_utc_now_iso())

    print("→ Running nurture.tick()…")
    out = tick()
    print("  tick →", json.dumps(out, default=str)[:300])

    enr = get_enrollment(enrollment_id)
    print(f"  Post-tick: status={enr['status']} step={enr['current_step']} "
          f"next_run_at={enr.get('next_run_at')}")

    if enr["status"] == "paused":
        print("\n✓ Step held for approval. Check Telegram — you should see a")
        print(f"  prompt with ✅/⏭ buttons for enrollment #{enrollment_id}.")
        print(f"  Or run: /napprove {enrollment_id}  |  /nskip {enrollment_id}")
    elif enr["status"] == "active":
        print("\n✓ Step auto-sent (preapproval matched).")
    elif enr["status"] == "completed":
        print("\n✓ Cadence completed.")
    else:
        print(f"\n? Unexpected status: {enr['status']}")

    if args.cleanup:
        update_enrollment(
            enrollment_id, status="exited", pause_reason="smoke_test cleanup",
        )
        print(f"→ Cleaned up enrollment #{enrollment_id} (marked exited).")
    else:
        active = [e for e in list_enrollments(status="paused", limit=20)
                  if e.get("source") == args.source]
        print(f"\nℹ {len(active)} smoke-test enrollment(s) still paused. "
              "Re-run with --cleanup to remove.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
