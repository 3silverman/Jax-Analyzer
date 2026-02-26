"""
api/routes/web.py

FastAPI route handlers for all HTML UI views.
Data is read from the DB (Supabase/Postgres) with graceful fallback to the
in-memory store when the DB is unavailable (no DATABASE_URL set, network error, etc.).
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from api.state import get_store

logger = structlog.get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="ui/templates")


# ── DB helpers (try DB, fallback to in-memory) ────────────────────────────────

async def _try_db_inbox() -> list[dict] | None:
    try:
        from db.connection import get_session
        from db.repositories.score_repo import get_inbox_deals
        async with get_session() as session:
            rows = await get_inbox_deals(session)
        # Merge deal_card_json fields into the row dict for template compatibility
        deals = []
        for row in rows:
            card = row.get("deal_card_json") or {}
            if isinstance(card, str):
                card = json.loads(card)
            merged = {**card, **row}
            merged.pop("deal_card_json", None)
            # Ensure confidence_score_pts key exists
            if "confidence_score_pts" not in merged:
                merged["confidence_score_pts"] = merged.get("confidence_score", 0)
            deals.append(merged)
        return deals
    except Exception as exc:
        logger.warning("db_inbox_fallback", error=str(exc))
        return None


async def _try_db_rejected() -> list[dict] | None:
    try:
        from db.connection import get_session
        from db.repositories.neighborhood_repo import get_failed_gate_properties
        async with get_session() as session:
            return await get_failed_gate_properties(session)
    except Exception as exc:
        logger.warning("db_rejected_fallback", error=str(exc))
        return None


async def _try_db_shortlist() -> list[dict] | None:
    try:
        from db.connection import get_session
        from db.repositories.outcome_repo import get_shortlisted
        async with get_session() as session:
            rows = await get_shortlisted(session)
        deals = []
        for row in rows:
            card = row.get("deal_card_json") or {}
            if isinstance(card, str):
                card = json.loads(card)
            merged = {**card, **{k: v for k, v in row.items() if k != "deal_card_json"}}
            deals.append(merged)
        return deals
    except Exception as exc:
        logger.warning("db_shortlist_fallback", error=str(exc))
        return None


async def _try_db_deal(deal_id: str) -> dict | None:
    try:
        from db.connection import get_session
        from db.repositories.score_repo import get_latest_score
        from db.repositories.property_repo import get_property
        from db.repositories.neighborhood_repo import get_neighborhood
        async with get_session() as session:
            prop = await get_property(session, deal_id)
            if not prop:
                return None
            score = await get_latest_score(session, deal_id)
            ns = await get_neighborhood(session, deal_id)
        if score:
            card = score.get("deal_card_json") or {}
            if isinstance(card, str):
                card = json.loads(card)
            merged = {**card}
            merged.update({k: v for k, v in prop.items()})
            if ns:
                for k in ("crime_grade", "walk_score", "flood_zone", "flood_high_risk",
                          "liveability_total", "street_view_url", "satellite_url", "maps_link"):
                    if k in ns:
                        merged.setdefault(k, ns[k])
            merged.update({k: v for k, v in score.items() if k != "deal_card_json"})
            if "confidence_score_pts" not in merged:
                merged["confidence_score_pts"] = merged.get("confidence_score", 0)
            return merged
        return None
    except Exception as exc:
        logger.warning("db_deal_fallback", deal_id=deal_id, error=str(exc))
        return None


async def _try_db_assumptions() -> dict | None:
    try:
        from db.connection import get_session
        from db.repositories.assumptions_repo import get_assumptions
        async with get_session() as session:
            row = await get_assumptions(session)
        # Convert DB decimal fractions to UI-display percentages
        return {
            "interest_rate":    round((row.get("interest_rate") or 0.075) * 100, 3),
            "insurance_pct":    round((row.get("insurance_pct") or 0.005) * 100, 2),
            "ltr_vacancy":      round((row.get("ltr_vacancy") or 0.08) * 100, 1),
            "mtr_vacancy":      round((row.get("mtr_vacancy") or 0.10) * 100, 1),
            "str_vacancy":      round((row.get("str_vacancy") or 0.25) * 100, 1),
            "mgmt_rate":        round((row.get("mgmt_rate") or 0.08) * 100, 1),
            "capex_rate":       round((row.get("capex_rate") or 0.005) * 100, 2),
            "maintenance_rate": round((row.get("maintenance_rate") or 0.01) * 100, 2),
            "active_preset":    row.get("active_preset", "custom"),
        }
    except Exception as exc:
        logger.warning("db_assumptions_fallback", error=str(exc))
        return None


async def _try_db_save_assumptions(updates: dict) -> None:
    try:
        from db.connection import get_session
        from db.repositories.assumptions_repo import update_assumptions
        async with get_session() as session:
            await update_assumptions(session, updates)
    except Exception as exc:
        logger.warning("db_save_assumptions_failed", error=str(exc))


async def _try_db_save_preset(preset: str) -> bool:
    """Returns True if the preset was applied, False if not found (e.g. empty my_settings)."""
    try:
        from db.connection import get_session
        from db.repositories.assumptions_repo import apply_preset
        async with get_session() as session:
            return await apply_preset(session, preset)
    except Exception as exc:
        logger.warning("db_apply_preset_failed", error=str(exc))
        return False


async def _try_db_save_my_settings(values: dict) -> None:
    try:
        from db.connection import get_session
        from db.repositories.assumptions_repo import save_my_settings
        async with get_session() as session:
            await save_my_settings(session, values)
    except Exception as exc:
        logger.warning("db_save_my_settings_failed", error=str(exc))


async def _try_db_get_my_settings_exists() -> bool:
    try:
        from db.connection import get_session
        from db.repositories.assumptions_repo import get_my_settings_snapshot
        async with get_session() as session:
            snap = await get_my_settings_snapshot(session)
        return snap is not None
    except Exception:
        return False


async def _try_db_audit_log() -> list[dict] | None:
    try:
        from db.connection import get_session
        from db.repositories.scan_log_repo import get_scan_history
        async with get_session() as session:
            return await get_scan_history(session, limit=50)
    except Exception as exc:
        logger.warning("db_audit_fallback", error=str(exc))
        return None


async def _try_db_record_outcome(
    deal_id: str,
    outcome: str,
    notes: str = "",
    offer_price: float | None = None,
) -> None:
    try:
        from db.connection import get_session
        from db.repositories.outcome_repo import record_outcome
        from db.repositories.property_repo import update_property_status
        async with get_session() as session:
            await record_outcome(session, deal_id, outcome, notes=notes,
                                 offer_price=offer_price)
            if outcome in ("pursued", "offer_made", "closed"):
                await update_property_status(session, deal_id, "shortlisted")
    except Exception as exc:
        logger.warning("db_outcome_fallback", deal_id=deal_id, error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tmpl(request: Request, name: str, ctx: dict) -> HTMLResponse:
    ctx["request"] = request
    return templates.TemplateResponse(name, ctx)


def _deals_from_store() -> list[dict]:
    store = get_store()
    return store.get("inbox", []) + store.get("alerts", [])


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/inbox")


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, filter: str = "all"):
    deals = await _try_db_inbox()
    if deals is None:
        store = get_store()
        deals = _deals_from_store()
        deals.sort(key=lambda d: d.get("deal_score", 0), reverse=True)

    # Inbox filter
    if filter == "multifamily":
        deals = [d for d in deals if d.get("property_type", "") in
                 ("duplex", "triplex", "quadplex")]
    elif filter == "sfh":
        deals = [d for d in deals if d.get("property_type", "") == "sfr"]
    elif filter == "sfh_adu":
        deals = [d for d in deals if d.get("property_type", "") == "sfr_adu"]

    # Active preset for banner
    assump = await _try_db_assumptions() or _default_assumptions()
    active_preset = assump.get("active_preset", "custom")

    return _tmpl(request, "inbox.html", {
        "deals": deals,
        "active_tab": "inbox",
        "active_preset": active_preset,
        "filter": filter,
    })


@router.get("/rejected", response_class=HTMLResponse)
async def rejected(request: Request):
    properties = await _try_db_rejected()
    if properties is None:
        store = get_store()
        properties = store.get("rejected", [])
    return _tmpl(request, "rejected.html", {
        "properties": properties,
        "active_tab": "rejected",
    })


@router.get("/shortlist", response_class=HTMLResponse)
async def shortlist(request: Request):
    deals = await _try_db_shortlist()
    if deals is None:
        store = get_store()
        deals = store.get("shortlist", [])
    return _tmpl(request, "shortlist.html", {
        "deals": deals,
        "active_tab": "shortlist",
    })


async def _try_db_mark_viewed(deal_id: str) -> None:
    try:
        from db.connection import get_session
        from db.repositories.outcome_repo import mark_viewed
        async with get_session() as session:
            await mark_viewed(session, deal_id)
    except Exception:
        pass  # non-critical; never surface to user


@router.get("/deal/{deal_id}", response_class=HTMLResponse)
async def deal_detail(request: Request, deal_id: str):
    deal = await _try_db_deal(deal_id)
    if deal is None:
        # Fallback to in-memory
        store = get_store()
        all_ds = _deals_from_store() + store.get("shortlist", [])
        deal = next((d for d in all_ds if d.get("canonical_id") == deal_id), None)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    # Stamp viewed_at on first open (mark_viewed is idempotent — skips if already set)
    await _try_db_mark_viewed(deal_id)
    return _tmpl(request, "deal_detail.html", {"deal": deal, "active_tab": "inbox"})


@router.post("/deal/{deal_id}/outcome")
async def update_outcome(
    deal_id: str,
    outcome: str = Form(...),
    notes: str = Form(""),
    offer_price: str = Form(""),
):
    offer = float(offer_price) if offer_price.strip() else None
    await _try_db_record_outcome(deal_id, outcome, notes=notes, offer_price=offer)

    # Mirror to in-memory store for session consistency
    store = get_store()
    all_ds = _deals_from_store() + store.get("shortlist", [])
    deal = next((d for d in all_ds if d.get("canonical_id") == deal_id), None)
    if deal:
        deal["outcome"] = outcome
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
    # Persist threshold to DB
    await _try_db_save_assumptions({"alert_threshold": alert_threshold})
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/assumptions", response_class=HTMLResponse)
async def assumptions_page(request: Request):
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
    assump = await _try_db_assumptions() or _default_assumptions()
    my_settings_exists = await _try_db_get_my_settings_exists()
    return _tmpl(request, "assumptions.html", {
        "assumptions":        assump,
        "va_rate":            va_rate_ctx,
        "active_tab":         "assumptions",
        "my_settings_exists": my_settings_exists,
    })


@router.get("/assumptions/refresh-rate")
async def refresh_va_rate():
    """Force a fresh VA rate fetch from FRED and redirect back to assumptions."""
    from ingestion.va_rates import fetch_va_rate
    va = fetch_va_rate(force_refresh=True)
    # Persist to DB
    try:
        from db.connection import get_session
        from db.repositories.assumptions_repo import update_va_rate
        async with get_session() as session:
            await update_va_rate(session, va.rate, va.fetched_at, va.source, va.is_stale)
    except Exception:
        pass
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
    active_preset: str = Form("custom"),
):
    display_vals = {
        "interest_rate":    interest_rate,
        "insurance_pct":    insurance_pct,
        "ltr_vacancy":      ltr_vacancy,
        "mtr_vacancy":      mtr_vacancy,
        "str_vacancy":      str_vacancy,
        "mgmt_rate":        mgmt_rate,
        "capex_rate":       capex_rate,
        "maintenance_rate": maintenance_rate,
        "active_preset":    active_preset,
    }
    # In-memory store keeps display values (percentages as-entered)
    store = get_store()
    store["assumptions"] = display_vals

    # DB stores decimal fractions
    await _try_db_save_assumptions({
        "interest_rate":    interest_rate / 100,
        "insurance_pct":    insurance_pct / 100,
        "ltr_vacancy":      ltr_vacancy / 100,
        "mtr_vacancy":      mtr_vacancy / 100,
        "str_vacancy":      str_vacancy / 100,
        "mgmt_rate":        mgmt_rate / 100,
        "capex_rate":       capex_rate / 100,
        "maintenance_rate": maintenance_rate / 100,
        "active_preset":    active_preset,
    })
    return RedirectResponse(url="/assumptions", status_code=303)


@router.post("/assumptions/preset/{preset_name}")
async def apply_preset(preset_name: str):
    """Apply a named preset and redirect to assumptions page."""
    if preset_name not in ("conservative", "base", "optimistic", "my_settings"):
        raise HTTPException(status_code=400, detail="Unknown preset")
    applied = await _try_db_save_preset(preset_name)
    if not applied and preset_name == "my_settings":
        # No snapshot saved yet — redirect back without applying
        return RedirectResponse(url="/assumptions?error=no_my_settings", status_code=303)
    return RedirectResponse(url="/assumptions", status_code=303)


@router.post("/assumptions/save-my-settings")
async def save_my_settings_route(
    interest_rate:    float = Form(7.5),
    insurance_pct:    float = Form(0.5),
    ltr_vacancy:      float = Form(8.0),
    mtr_vacancy:      float = Form(10.0),
    str_vacancy:      float = Form(25.0),
    mgmt_rate:        float = Form(8.0),
    capex_rate:       float = Form(0.5),
    maintenance_rate: float = Form(1.0),
):
    """Snapshot the current form values as My Settings and activate that preset."""
    # Values arrive as display percentages; convert to decimals for storage
    decimal_vals = {
        "interest_rate":    interest_rate / 100,
        "insurance_pct":    insurance_pct / 100,
        "ltr_vacancy":      ltr_vacancy / 100,
        "mtr_vacancy":      mtr_vacancy / 100,
        "str_vacancy":      str_vacancy / 100,
        "mgmt_rate":        mgmt_rate / 100,
        "capex_rate":       capex_rate / 100,
        "maintenance_rate": maintenance_rate / 100,
    }
    await _try_db_save_my_settings(decimal_vals)
    # Also persist the live values to the assumptions row so they take effect immediately
    await _try_db_save_assumptions({**decimal_vals, "active_preset": "my_settings"})
    return RedirectResponse(url="/assumptions", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request):
    logs = await _try_db_audit_log()
    if logs is None:
        store = get_store()
        logs = store.get("scan_logs", [])
    return _tmpl(request, "audit_log.html", {
        "logs": logs,
        "active_tab": "audit",
    })


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    analytics: dict[str, Any] = {}
    try:
        from db.connection import get_session
        from db.repositories.score_repo import get_analytics_summary
        from db.repositories.outcome_repo import get_funnel_metrics
        async with get_session() as session:
            summary = await get_analytics_summary(session)
            funnel  = await get_funnel_metrics(session)
        analytics = {**summary, **funnel}
    except Exception as exc:
        logger.warning("db_analytics_fallback", error=str(exc))
        # Build rough metrics from in-memory store
        store = get_store()
        deals = _deals_from_store()
        analytics = {
            "total_active":  len(deals),
            "passed_gates":  len(deals),
            "scored_60_plus": sum(1 for d in deals if d.get("deal_score", 0) >= 60),
            "high_priority": sum(1 for d in deals if d.get("is_high_priority")),
            "pursued": 0, "offer_made": 0, "closed": 0, "rejected": 0,
        }
    return _tmpl(request, "analytics.html", {
        "analytics": analytics,
        "active_tab": "analytics",
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
        "active_preset":   "custom",
    }
