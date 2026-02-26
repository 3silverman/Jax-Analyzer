"""
api/routes/web.py

FastAPI route handlers for all HTML UI views.
Data is fetched from a mock in-memory store for now; wired to DB in Session 7.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from api.state import get_store

router = APIRouter()
templates = Jinja2Templates(directory="ui/templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tmpl(request: Request, name: str, ctx: dict) -> HTMLResponse:
    ctx["request"] = request
    return templates.TemplateResponse(name, ctx)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/inbox")


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request):
    store = get_store()
    deals = store.get("inbox", []) + store.get("alerts", [])
    deals.sort(key=lambda d: d.get("deal_score", 0), reverse=True)
    return _tmpl(request, "inbox.html", {"deals": deals, "active_tab": "inbox"})


@router.get("/rejected", response_class=HTMLResponse)
async def rejected(request: Request):
    store = get_store()
    return _tmpl(request, "rejected.html", {
        "properties": store.get("rejected", []),
        "active_tab": "rejected",
    })


@router.get("/shortlist", response_class=HTMLResponse)
async def shortlist(request: Request):
    store = get_store()
    return _tmpl(request, "shortlist.html", {
        "deals": store.get("shortlist", []),
        "active_tab": "shortlist",
    })


@router.get("/deal/{deal_id}", response_class=HTMLResponse)
async def deal_detail(request: Request, deal_id: str):
    store  = get_store()
    all_ds = store.get("inbox", []) + store.get("alerts", []) + store.get("shortlist", [])
    deal   = next((d for d in all_ds if d.get("canonical_id") == deal_id), None)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return _tmpl(request, "deal_detail.html", {"deal": deal, "active_tab": "inbox"})


@router.post("/deal/{deal_id}/outcome")
async def update_outcome(deal_id: str, outcome: str = Form(...)):
    store  = get_store()
    all_ds = store.get("inbox", []) + store.get("alerts", []) + store.get("shortlist", [])
    deal   = next((d for d in all_ds if d.get("canonical_id") == deal_id), None)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    deal["outcome"] = outcome
    # Move to shortlist if pursued
    if outcome in ("pursued", "offer_made", "closed"):
        sl = store.setdefault("shortlist", [])
        if not any(d.get("canonical_id") == deal_id for d in sl):
            sl.append(deal)
    return RedirectResponse(url=f"/deal/{deal_id}", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    store = get_store()
    return _tmpl(request, "settings.html", {
        "settings": store.get("settings", _default_settings()),
        "active_tab": "settings",
    })


@router.post("/settings")
async def update_settings(
    request: Request,
    zip_whitelist: str = Form(...),
    alert_threshold: int = Form(85),
    digest_email: str = Form(""),
):
    store = get_store()
    store["settings"] = {
        "zip_whitelist":  [z.strip() for z in zip_whitelist.split(",") if z.strip()],
        "alert_threshold": alert_threshold,
        "digest_email":    digest_email,
    }
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/assumptions", response_class=HTMLResponse)
async def assumptions_page(request: Request):
    store = get_store()
    from ingestion.va_rates import fetch_va_rate
    va_rate = fetch_va_rate()
    va_rate_ctx = {
        "rate":            va_rate.rate,
        "source":          va_rate.source,
        "is_fallback":     va_rate.is_fallback,
        "is_stale":        va_rate.is_stale,
        "fetched_at_display": (
            va_rate.fetched_at.strftime("%b %d %H:%M UTC")
            if va_rate.fetched_at else "never"
        ),
    }
    return _tmpl(request, "assumptions.html", {
        "assumptions": store.get("assumptions", _default_assumptions()),
        "va_rate":     va_rate_ctx,
        "active_tab":  "assumptions",
    })


@router.get("/assumptions/refresh-rate")
async def refresh_va_rate():
    """Force a fresh VA rate fetch from FRED and redirect back to assumptions."""
    from ingestion.va_rates import fetch_va_rate
    fetch_va_rate(force_refresh=True)
    return RedirectResponse(url="/assumptions", status_code=303)


@router.post("/assumptions")
async def update_assumptions(
    request: Request,
    interest_rate: float = Form(7.5),
    insurance_pct: float = Form(0.5),
    ltr_vacancy:   float = Form(8.0),
    mtr_vacancy:   float = Form(10.0),
    str_vacancy:   float = Form(25.0),
    mgmt_rate:     float = Form(8.0),
    capex_rate:    float = Form(0.5),
    maintenance_rate: float = Form(1.0),
):
    store = get_store()
    store["assumptions"] = {
        "interest_rate":     interest_rate,
        "insurance_pct":     insurance_pct,
        "ltr_vacancy":       ltr_vacancy,
        "mtr_vacancy":       mtr_vacancy,
        "str_vacancy":       str_vacancy,
        "mgmt_rate":         mgmt_rate,
        "capex_rate":        capex_rate,
        "maintenance_rate":  maintenance_rate,
    }
    return RedirectResponse(url="/assumptions", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request):
    store = get_store()
    return _tmpl(request, "audit_log.html", {
        "logs": store.get("scan_logs", []),
        "active_tab": "audit",
    })


@router.post("/scan/run")
async def trigger_scan():
    """Manual scan trigger — runs in background."""
    import asyncio
    from ingestion.run_scan import run_scan
    asyncio.create_task(asyncio.to_thread(run_scan))
    return {"status": "scan_started"}


# ── Default data helpers ──────────────────────────────────────────────────────

def _default_settings() -> dict:
    return {
        "zip_whitelist":   ["32204", "32205", "32206", "32207", "32210", "32211", "32217"],
        "alert_threshold": 85,
        "digest_email":    "",
    }


def _default_assumptions() -> dict:
    return {
        "interest_rate":    7.5,
        "insurance_pct":    0.5,
        "ltr_vacancy":      8.0,
        "mtr_vacancy":     10.0,
        "str_vacancy":     25.0,
        "mgmt_rate":        8.0,
        "capex_rate":       0.5,
        "maintenance_rate": 1.0,
    }
