"""Tests for the Guardian draft-and-approve queue used by money-moving
SME-Ops writes."""

from __future__ import annotations

import importlib
import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_drafts():
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM guardian_drafts")
        conn.commit()
    finally:
        conn.close()
    yield


# ── Decision rule ─────────────────────────────────────────────────────


def test_money_movement_check_drafts_refund():
    from roost.services.guardian import guardian_check, NEEDS_APPROVAL
    out = guardian_check("stripe_create_refund",
                         {"charge_id": "ch_1", "amount": 10.0}, "")
    assert out["decision"] == NEEDS_APPROVAL
    assert out["rule"] == "money_movement_draft"


def test_money_movement_check_drafts_cancel():
    from roost.services.guardian import guardian_check, NEEDS_APPROVAL
    out = guardian_check("shopify_cancel_order",
                         {"order_id": 1, "refund": True}, "")
    assert out["decision"] == NEEDS_APPROVAL


def test_xero_draft_invoice_passes_through():
    from roost.services.guardian import guardian_check, ALLOW
    out = guardian_check("xero_create_invoice",
                         {"contact_id": "c1", "line_items": [{}],
                          "status": "DRAFT"}, "")
    assert out["decision"] == ALLOW


def test_xero_authorised_invoice_drafts():
    from roost.services.guardian import guardian_check, NEEDS_APPROVAL
    out = guardian_check("xero_create_invoice",
                         {"contact_id": "c1", "line_items": [{}],
                          "status": "AUTHORISED"}, "")
    assert out["decision"] == NEEDS_APPROVAL


# ── Gate / draft persistence ──────────────────────────────────────────


def test_guardian_gate_creates_draft():
    from roost.services.guardian import guardian_gate, list_pending_drafts
    out = guardian_gate("stripe_create_refund",
                        {"charge_id": "ch_x", "amount": 5})
    assert out["status"] == "pending_approval"
    assert out["draft_id"] > 0
    drafts = list_pending_drafts()
    assert len(drafts) == 1
    assert drafts[0]["tool_name"] == "stripe_create_refund"
    assert drafts[0]["args"]["charge_id"] == "ch_x"


def test_guardian_gate_returns_none_for_safe_tool():
    from roost.services.guardian import guardian_gate
    assert guardian_gate("get_task", {"task_id": 1}) is None


# ── Approval dispatch ─────────────────────────────────────────────────


@respx.mock
def test_approve_draft_executes_stripe_refund(monkeypatch):
    # Patch the values the stripe module bound at import time.
    import roost.extras.sme_ops.services.stripe as sm
    monkeypatch.setattr(sm, "STRIPE_ENABLED", True)
    monkeypatch.setattr(sm, "STRIPE_API_KEY", "sk_test_x")

    from roost.services.guardian import (
        approve_draft, create_draft, get_draft,
    )
    respx.post("https://api.stripe.com/v1/refunds").mock(
        return_value=httpx.Response(200, json={
            "id": "re_1", "charge": "ch_1", "amount": 1000,
            "currency": "usd", "status": "succeeded",
            "reason": None, "created": 1700000000,
        }),
    )

    draft_id = create_draft(
        "stripe_create_refund",
        {"charge_id": "ch_1", "amount": 10.0, "reason": None},
    )
    out = approve_draft(draft_id)
    assert out["status"] == "executed", out
    assert out["result"]["status"] == "succeeded"
    assert out["result"]["amount"] == 10.00

    final = get_draft(draft_id)
    assert final["status"] == "executed"
    assert final["executed_at"] is not None


def test_approve_marks_failed_when_executor_returns_error():
    from roost.services.guardian import approve_draft, create_draft, get_draft
    # Stripe disabled by default → executor will return error.
    draft_id = create_draft(
        "stripe_create_refund",
        {"charge_id": "ch_x", "amount": 1.0},
    )
    out = approve_draft(draft_id)
    assert out["status"] == "failed"
    assert get_draft(draft_id)["status"] == "failed"


def test_reject_draft():
    from roost.services.guardian import create_draft, get_draft, reject_draft
    draft_id = create_draft("stripe_create_refund", {"charge_id": "ch_x"})
    out = reject_draft(draft_id, reason="not authorised")
    assert out["status"] == "rejected"
    final = get_draft(draft_id)
    assert final["status"] == "rejected"
    assert "not authorised" in final["result_json"]


def test_double_approve_is_idempotent():
    from roost.services.guardian import approve_draft, create_draft
    draft_id = create_draft("stripe_create_refund", {"charge_id": "ch_x"})
    approve_draft(draft_id)  # first call → failed (stripe disabled)
    out = approve_draft(draft_id)
    assert out["status"] == "not_pending"


# ── HTTP endpoints ────────────────────────────────────────────────────


def _build_client(monkeypatch, *, sme_enabled=True):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", sme_enabled)
    import roost.extras.sme_ops.web.api_sme_drafts as api
    importlib.reload(api)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_drafts_endpoint_404_when_disabled(monkeypatch):
    client = _build_client(monkeypatch, sme_enabled=False)
    assert client.get("/api/sme/drafts").status_code == 404


def test_drafts_endpoint_lists_pending(monkeypatch):
    from roost.services.guardian import create_draft
    create_draft("stripe_create_refund", {"charge_id": "ch_a"})
    create_draft("shopify_cancel_order", {"order_id": 1})
    client = _build_client(monkeypatch)
    r = client.get("/api/sme/drafts")
    assert r.status_code == 200
    drafts = r.json()["drafts"]
    assert len(drafts) == 2
    assert {d["tool_name"] for d in drafts} == {
        "stripe_create_refund", "shopify_cancel_order"
    }


def test_reject_endpoint(monkeypatch):
    from roost.services.guardian import create_draft, get_draft
    draft_id = create_draft("stripe_create_refund", {"charge_id": "ch_a"})
    client = _build_client(monkeypatch)
    r = client.post(f"/api/sme/drafts/{draft_id}/reject",
                    json={"reason": "no"})
    assert r.status_code == 200
    assert get_draft(draft_id)["status"] == "rejected"


def test_approve_endpoint_404_for_unknown_draft(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.post("/api/sme/drafts/9999999/approve")
    assert r.status_code == 404


# ── MCP wrapper integration ───────────────────────────────────────────


def test_stripe_refund_mcp_tool_returns_pending(monkeypatch):
    from roost.extras.sme_ops.mcp import tools_stripe
    out = tools_stripe.stripe_create_refund.fn("ch_x", amount=5.0)
    assert out["status"] == "pending_approval"
    assert out["draft_id"] > 0


def test_shopify_cancel_mcp_tool_returns_pending(monkeypatch):
    from roost.extras.sme_ops.mcp import tools_shopify
    out = tools_shopify.shopify_cancel_order.fn(123, refund=True)
    assert out["status"] == "pending_approval"


def test_xero_invoice_draft_status_executes_directly(monkeypatch):
    """A DRAFT-status invoice should not go through the gate."""
    from roost.extras.sme_ops.mcp import tools_xero
    # Xero disabled — service returns xero_disabled, but importantly
    # the call goes through to the executor (no draft).
    out = tools_xero.xero_create_invoice.fn(
        "c1", [{"description": "x", "quantity": 1, "unit_amount": 1}],
        status="DRAFT",
    )
    assert out.get("status") != "pending_approval"
