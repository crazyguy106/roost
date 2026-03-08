"""Public forms — no authentication required.

Self-contained pages for external stakeholders to submit decisions,
feedback, or information. Responses saved to data/forms/ as JSON.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/forms")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_FORMS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "forms",
)


def _save_response(form_id: str, data: dict):
    """Save a form response as timestamped JSON."""
    os.makedirs(_FORMS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(_FORMS_DIR, f"{form_id}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


@router.get("/iapcf-decisions", response_class=HTMLResponse)
def iapcf_decisions_form(request: Request):
    return templates.TemplateResponse("forms/iapcf-decisions.html", {
        "request": request,
    })


@router.post("/iapcf-decisions", response_class=HTMLResponse)
async def iapcf_decisions_submit(request: Request):
    form = await request.form()
    data = {
        "form": "iapcf-decisions",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "responses": {
            "q1_contact_hours": form.get("q1_contact_hours"),
            "q1_custom": form.get("q1_custom"),
            "q1_per_credential": form.get("q1_per_credential"),
            "q2_fellows": form.get("q2_fellows"),
            "q3_calm": form.get("q3_calm"),
            "q3_calm_details": form.get("q3_calm_details"),
            "q4_mzm_session": form.get("q4_mzm_session"),
            "q4_mzm_custom": form.get("q4_mzm_custom"),
            "q5_mt_instrument": form.get("q5_mt_instrument"),
            "q5_mt_custom": form.get("q5_mt_custom"),
            "q5_mw_instrument": form.get("q5_mw_instrument"),
            "q5_mw_custom": form.get("q5_mw_custom"),
            "q5_validated": form.get("q5_validated"),
            "q5_ip_owner": form.get("q5_ip_owner"),
            "additional_notes": form.get("additional_notes"),
        },
    }
    path = _save_response("iapcf-decisions", data)
    return templates.TemplateResponse("forms/iapcf-decisions-thanks.html", {
        "request": request,
        "save_path": path,
    })
