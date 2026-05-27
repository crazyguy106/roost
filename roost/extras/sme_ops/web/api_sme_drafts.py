"""Approve / reject endpoints for the Guardian draft queue.

Mounted under `/api/sme/drafts` because today the only draft producers
are SME-Ops money-moving writes. If/when other modules use the same
queue, the prefix can move up to `/api/guardian/drafts`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException

from roost.config import SME_OPS_ENABLED
from roost.services.guardian import (
    approve_draft,
    list_pending_drafts,
    reject_draft,
)

logger = logging.getLogger("roost.web.api_sme_drafts")

router = APIRouter(prefix="/api/sme/drafts", tags=["sme-ops", "guardian"])


def _require_sme_ops() -> None:
    if not SME_OPS_ENABLED:
        raise HTTPException(status_code=404, detail="SME Ops bundle disabled")


@router.get("")
def list_drafts() -> dict:
    _require_sme_ops()
    return {"ok": True, "drafts": list_pending_drafts()}


@router.post("/{draft_id}/approve")
def approve(draft_id: int) -> dict:
    _require_sme_ops()
    result = approve_draft(draft_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return result


@router.post("/{draft_id}/reject")
def reject(draft_id: int, body: dict = Body(default_factory=dict)) -> dict:
    _require_sme_ops()
    result = reject_draft(draft_id, reason=str(body.get("reason", "")))
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return result
