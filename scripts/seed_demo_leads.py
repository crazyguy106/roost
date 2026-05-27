"""Seed the /leads pipeline with realistic demo data.

Idempotent: re-running drops any prior demo-seeded enrollments (matched
by source='demo_seed') and re-creates them. Safe to run on a populated
DB — real leads (source != 'demo_seed') are untouched.

Usage (host):
    python3 scripts/seed_demo_leads.py

Usage (in container on demo VPS):
    docker exec roost-roost-1 python3 /app/scripts/seed_demo_leads.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from roost.database import get_connection
from roost.extras.lead_nurture.services.cadences import (
    enroll_lead,
    seed_library,
    update_enrollment,
)


SEED_SOURCE = "demo_seed"


def _purge_prior_seed() -> int:
    """Drop any enrollments previously created by this script."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM nurture_enrollments WHERE source = ?", (SEED_SOURCE,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _enroll(name: str, phone: str = "", channel: str = "whatsapp") -> int:
    enr = enroll_lead(
        cadence_slug="property_buyer_intro",
        contact_name=name,
        contact_phone=phone,
        channel=channel,
        source=SEED_SOURCE,
    )
    return enr["id"]


def main() -> None:
    seed_library()  # idempotent — ensures cadences exist
    purged = _purge_prior_seed()
    print(f"Purged {purged} prior demo-seed enrollments")

    # 1. HOT — finished qualification, AI-CDR flagged hot
    hot_id = _enroll("Priya Sharma", "+6591112222", channel="whatsapp")
    update_enrollment(
        hot_id,
        status="active",
        fields={
            "_qualify_status": "done",
            "_qualify_label": "hot",
            "_qualify_score": 0.92,
            "_qualify_answers": {
                "timeline": "this month",
                "budget_band": "1.8M-2.2M",
                "mortgage_status": "pre-approved",
            },
            "_lead_urgency": "hot",
            "_lead_intent": "buy_now",
            "_cdr_reasoning": "Buyer has pre-approval, walked 2 showflats this week",
        },
    )

    # 2. QUALIFYING (mid-pack, question 1/3) — paused mid-flow
    qual1_id = _enroll("Marcus Tan", "+6593334444", channel="whatsapp")
    update_enrollment(
        qual1_id,
        status="paused",
        pause_reason="qualifying",
        fields={
            "_qualify_status": "in_progress",
            "_qualify_idx": 0,
            "_qualify_channel": "whatsapp",
            "_qualify_identifier": "+6593334444",
            "_qualify_answers": {},
            "_qualify_cadence_slug": "property_buyer_intro",
        },
    )

    # 3. QUALIFYING (2/3) — different channel for variety
    qual2_id = _enroll("Wei Ling Goh", "+6595556666", channel="wechat")
    update_enrollment(
        qual2_id,
        status="paused",
        pause_reason="qualifying",
        fields={
            "_qualify_status": "in_progress",
            "_qualify_idx": 1,
            "_qualify_channel": "wechat",
            "_qualify_identifier": "+6595556666",
            "_qualify_answers": {"timeline": "next quarter"},
            "_qualify_cadence_slug": "property_buyer_intro",
        },
    )

    # 4. NURTURING — qualified warm
    nur_id = _enroll("Daniel Lim", "daniel.lim@example.sg", channel="email")
    update_enrollment(
        nur_id,
        status="active",
        fields={
            "_qualify_status": "done",
            "_qualify_label": "warm",
            "_qualify_score": 0.58,
            "_qualify_answers": {
                "timeline": "6-12 months",
                "budget_band": "1.2M-1.5M",
                "mortgage_status": "looking into",
            },
        },
    )

    # 5. NEW — just enrolled, untouched
    _enroll("Aisha Rahman", "+6597778888", channel="whatsapp")

    # 6. PAUSED (meeting booked — stage change, not qualifying)
    paused_id = _enroll("Jonathan Chua", "+6599990000", channel="telegram")
    update_enrollment(
        paused_id,
        status="paused",
        pause_reason="stage:meeting booked",
        fields={
            "_meeting_at": "2026-05-30T10:00:00+08:00",
            "_qualify_label": "warm",
            "_qualify_status": "done",
        },
    )

    # 7. CLOSED — qualified cold, exited
    closed_id = _enroll("Rachel Ng", "+6588889999", channel="whatsapp")
    update_enrollment(
        closed_id,
        status="exited",
        pause_reason="qualified_cold",
        fields={
            "_qualify_status": "done",
            "_qualify_label": "cold",
            "_qualify_score": 0.18,
            "_qualify_answers": {
                "timeline": "just browsing",
                "budget_band": "under 800k",
                "mortgage_status": "not started",
            },
        },
    )

    # Summary
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM nurture_enrollments WHERE source = ?",
            (SEED_SOURCE,),
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"Seeded {total} demo leads at {datetime.now(timezone.utc).isoformat()}")
    print("Visit /leads on the demo URL to see the pipeline.")


if __name__ == "__main__":
    main()
