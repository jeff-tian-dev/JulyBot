"""Plain HTML pages: pricing, success, cancel. No JS framework, no auth."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from web.app import WEB_ROOT
from web.tiers import TIERS

router = APIRouter()
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))


@router.get("/")
async def pricing(request: Request):
    return templates.TemplateResponse(
        request, "pricing.html", {"tiers": TIERS.values()}
    )


@router.get("/success")
async def success(request: Request):
    return templates.TemplateResponse(request, "success.html", {})


@router.get("/cancel")
async def cancel(request: Request):
    return templates.TemplateResponse(request, "cancel.html", {})
