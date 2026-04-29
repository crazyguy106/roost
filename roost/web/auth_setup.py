"""One-time setup wizard routes (password + deployment shape).

Registered unconditionally — the wizard must work on first boot before
any OAuth or password is configured.
"""

import logging
import secrets
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from roost.config import PROJECT_ROOT

logger = logging.getLogger("roost.auth.setup")

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

SETUP_TOKEN_FILE = PROJECT_ROOT / "data" / ".setup_token"


@router.get("/setup")
async def setup_password_form(request: Request):
    token = request.query_params.get("token", "")
    if not SETUP_TOKEN_FILE.exists():
        return RedirectResponse("/auth/login-page")
    stored_token = SETUP_TOKEN_FILE.read_text().strip()
    if not stored_token or not secrets.compare_digest(token, stored_token):
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse("setup_password.html", {
        "request": request, "token": token, "error": None,
    })


@router.post("/setup")
async def setup_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    if not SETUP_TOKEN_FILE.exists():
        return RedirectResponse("/auth/login-page")
    stored_token = SETUP_TOKEN_FILE.read_text().strip()
    if not stored_token or not secrets.compare_digest(token, stored_token):
        return RedirectResponse("/auth/login-page")

    if password != password_confirm:
        return templates.TemplateResponse("setup_password.html", {
            "request": request, "token": token,
            "error": "Passwords do not match.",
        })
    if len(password) < 8:
        return templates.TemplateResponse("setup_password.html", {
            "request": request, "token": token,
            "error": "Password must be at least 8 characters.",
        })

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("WEB_PASSWORD="):
                new_lines.append(f"WEB_PASSWORD={password}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"WEB_PASSWORD={password}")
        env_file.write_text("\n".join(new_lines) + "\n")
    else:
        env_file.write_text(f"WEB_PASSWORD={password}\n")

    new_token = secrets.token_urlsafe(32)
    SETUP_TOKEN_FILE.write_text(new_token)

    import roost.config as cfg
    cfg.WEB_PASSWORD = password

    logger.info("Password set via setup token — proceeding to deployment step")
    return RedirectResponse(f"/auth/setup/deployment?token={new_token}", status_code=303)


@router.get("/setup/deployment")
async def setup_deployment_form(request: Request):
    token = request.query_params.get("token", "")
    if not SETUP_TOKEN_FILE.exists():
        return RedirectResponse("/auth/login-page")
    stored_token = SETUP_TOKEN_FILE.read_text().strip()
    if not stored_token or not secrets.compare_digest(token, stored_token):
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse("setup_deployment.html", {
        "request": request, "token": token, "error": None,
    })


@router.post("/setup/deployment")
async def setup_deployment_submit(
    request: Request,
    token: str = Form(...),
    shape: str = Form(...),
    roost_domain: str = Form(""),
    roost_admin_email: str = Form(""),
    sidecar_public_url: str = Form(""),
):
    if not SETUP_TOKEN_FILE.exists():
        return RedirectResponse("/auth/login-page")
    stored_token = SETUP_TOKEN_FILE.read_text().strip()
    if not stored_token or not secrets.compare_digest(token, stored_token):
        return RedirectResponse("/auth/login-page")

    if shape not in {"laptop", "hosted", "vps_domain"}:
        return templates.TemplateResponse("setup_deployment.html", {
            "request": request, "token": token,
            "error": "Pick one of: Laptop / Hosted-by-you / VPS+domain.",
        })

    if shape == "vps_domain":
        if not roost_domain or "." not in roost_domain:
            return templates.TemplateResponse("setup_deployment.html", {
                "request": request, "token": token,
                "error": "VPS+domain shape requires a domain (e.g. roost.example.com).",
            })
        if not roost_admin_email or "@" not in roost_admin_email:
            return templates.TemplateResponse("setup_deployment.html", {
                "request": request, "token": token,
                "error": "VPS+domain shape requires an admin email for Let's Encrypt.",
            })
    if shape == "hosted":
        if not sidecar_public_url or not sidecar_public_url.startswith("http"):
            return templates.TemplateResponse("setup_deployment.html", {
                "request": request, "token": token,
                "error": "Hosted shape requires the public sidecar URL (https://your-host/sidecar).",
            })

    env_updates: dict[str, str | None] = {}
    if shape == "laptop":
        env_updates["ROOST_DOMAIN"] = None
        env_updates["ROOST_ADMIN_EMAIL"] = None
        env_updates["SIDECAR_PUBLIC_URL"] = None
    elif shape == "hosted":
        env_updates["SIDECAR_PUBLIC_URL"] = sidecar_public_url
    elif shape == "vps_domain":
        env_updates["ROOST_DOMAIN"] = roost_domain
        env_updates["ROOST_ADMIN_EMAIL"] = roost_admin_email

    env_file = PROJECT_ROOT / ".env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        keep = True
        for k, v in env_updates.items():
            if line.startswith(f"{k}="):
                if v is None:
                    keep = False
                else:
                    line = f"{k}={v}"
                seen.add(k)
                break
        if keep:
            new_lines.append(line)
    for k, v in env_updates.items():
        if v is not None and k not in seen:
            new_lines.append(f"{k}={v}")
    env_file.write_text("\n".join(new_lines) + "\n")

    SETUP_TOKEN_FILE.unlink()
    logger.info("Setup wizard finished; deployment shape=%s", shape)

    return templates.TemplateResponse("setup_deployment.html", {
        "request": request, "token": "", "error": None,
        "success": True, "shape": shape, "roost_domain": roost_domain,
    })
