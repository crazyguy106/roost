"""RPA run viewer JSON API.

Read-only listing of RPA runs + a cancel endpoint. The takeover URL is
read straight off `state_json.live_url` which `await_user_session`
populates when it pauses.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from roost.extras.rpa.services import rpa_runs

logger = logging.getLogger("roost.extras.rpa.web.api_rpa")

router = APIRouter(prefix="/api/rpa", tags=["rpa"])


def _trim(run: dict) -> dict:
    """Public-shape projection — drop large/raw fields."""
    state = run.get("state_json") if isinstance(run.get("state_json"), dict) else {}
    # Surface live_url whenever it's present. `await_user_session` keeps the
    # run in `running` while the user drives the browser, so gating on
    # status="awaiting_input" would hide the takeover URL exactly when it's
    # most needed. The interpreter clears live_url once the wait resolves.
    live_url = state.get("live_url")
    return {
        "id": run.get("id"),
        "user_id": run.get("user_id"),
        "portal_slug": run.get("portal_slug"),
        "recipe_id": run.get("recipe_id"),
        "status": run.get("status"),
        "prompt_text": run.get("prompt_text") or state.get("manual_prompt"),
        "prompt_kind": run.get("prompt_kind"),
        "live_url": live_url,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "result_json": run.get("result_json"),
    }


@router.get("/runs")
def list_rpa_runs(request: Request, status: str | None = None, limit: int = 30):
    """List recent RPA runs. Optional `status` filter."""
    runs = rpa_runs.list_runs(status=status, limit=limit)
    return JSONResponse({"runs": [_trim(r) for r in runs]})


@router.post("/runs/{run_id}/cancel")
def cancel_run(request: Request, run_id: int):
    """Cancel a running or awaiting-input run."""
    try:
        rpa_runs.cancel(run_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.exception("cancel run %d failed", run_id)
        return JSONResponse({"ok": False, "detail": str(e)}, status_code=500)
