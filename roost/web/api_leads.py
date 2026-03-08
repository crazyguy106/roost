"""Webhook endpoint for framework assessment leads."""

import logging
import os
import secrets

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from roost.config import LEAD_WEBHOOK_SECRET

router = APIRouter(prefix="/api/leads", tags=["leads"])
_logger = logging.getLogger("roost.web.leads")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.getenv("LEAD_CORS_ORIGIN", "*"),
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class LeadCaptureRequest(BaseModel):
    """Payload from framework assessment report."""

    name: str = ""
    email: str
    orgName: str = ""
    industries: list[str] = []
    revenue: str = ""
    aiProfile: str = ""
    riskScore: int = 0
    riskLevel: str = ""
    riskFactors: list[str] = []
    sectors: list[str] = ["base"]
    timestamp: str = ""


@router.post("/capture")
def capture_lead(body: LeadCaptureRequest, token: str = Query("")):
    """Receive a lead from the framework assessment and process it.

    Creates a contact, pipeline task, and schedules follow-up emails.
    Secured with a webhook token (passed as ?token= query param).
    """
    # Auth: validate webhook secret
    if not LEAD_WEBHOOK_SECRET or not secrets.compare_digest(token, LEAD_WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid or missing token")

    _logger.info("Lead capture: %s <%s> from %s (risk: %d)",
                 body.name, body.email, body.orgName, body.riskScore)

    from roost.services.lead_pipeline import ingest_lead

    lead_data = {
        "name": body.name,
        "email": body.email,
        "org_name": body.orgName,
        "industries": body.industries,
        "revenue": body.revenue,
        "ai_profile": body.aiProfile,
        "risk_score": body.riskScore,
        "risk_level": body.riskLevel,
        "risk_factors": body.riskFactors,
        "sectors": body.sectors,
        "timestamp": body.timestamp,
    }

    result = ingest_lead(lead_data)

    if "error" in result:
        return JSONResponse(status_code=400, content=result, headers=_CORS_HEADERS)

    return JSONResponse(content={"ok": True, **result}, headers=_CORS_HEADERS)


@router.options("/capture")
def capture_lead_options():
    """Handle CORS preflight for the lead capture endpoint."""
    return JSONResponse(content={}, headers=_CORS_HEADERS)
