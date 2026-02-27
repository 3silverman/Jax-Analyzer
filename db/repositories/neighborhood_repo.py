"""db/repositories/neighborhood_repo.py — neighborhood_scores CRUD."""

from __future__ import annotations
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_neighborhood(session: AsyncSession, ns: dict[str, Any]) -> None:
    stmt = text("""
        INSERT INTO neighborhood_scores (
            property_id, liveability_total,
            walk_pts, hospital_pts, crime_pts, flood_pts, visual_pts,
            walk_score, transit_score, bike_score,
            crime_grade, crime_low_confidence,
            flood_zone, flood_high_risk,
            hospital_distance_miles, closest_hospital, proximity_score,
            passed_gates, failed_gate_reasons,
            street_view_url, satellite_url, maps_link
        ) VALUES (
            :property_id, :liveability_total,
            :walk_pts, :hospital_pts, :crime_pts, :flood_pts, :visual_pts,
            :walk_score, :transit_score, :bike_score,
            :crime_grade, :crime_low_confidence,
            :flood_zone, :flood_high_risk,
            :hospital_distance_miles, :closest_hospital, :proximity_score,
            :passed_gates, :failed_gate_reasons,
            :street_view_url, :satellite_url, :maps_link
        )
        ON CONFLICT (property_id) DO UPDATE SET
            liveability_total       = EXCLUDED.liveability_total,
            walk_pts                = EXCLUDED.walk_pts,
            hospital_pts            = EXCLUDED.hospital_pts,
            crime_pts               = EXCLUDED.crime_pts,
            flood_pts               = EXCLUDED.flood_pts,
            visual_pts              = EXCLUDED.visual_pts,
            walk_score              = EXCLUDED.walk_score,
            transit_score           = EXCLUDED.transit_score,
            bike_score              = EXCLUDED.bike_score,
            crime_grade             = EXCLUDED.crime_grade,
            crime_low_confidence    = EXCLUDED.crime_low_confidence,
            flood_zone              = EXCLUDED.flood_zone,
            flood_high_risk         = EXCLUDED.flood_high_risk,
            hospital_distance_miles = EXCLUDED.hospital_distance_miles,
            closest_hospital        = EXCLUDED.closest_hospital,
            proximity_score         = EXCLUDED.proximity_score,
            passed_gates            = EXCLUDED.passed_gates,
            failed_gate_reasons     = EXCLUDED.failed_gate_reasons,
            street_view_url         = EXCLUDED.street_view_url,
            satellite_url           = EXCLUDED.satellite_url,
            maps_link               = EXCLUDED.maps_link,
            scored_at               = NOW()
    """)
    await session.execute(stmt, {
        "property_id":              ns["property_id"],
        "liveability_total":        ns.get("liveability_total", 0),
        "walk_pts":                 ns.get("walk_pts", 0),
        "hospital_pts":             ns.get("hospital_pts", 0),
        "crime_pts":                ns.get("crime_pts", 0),
        "flood_pts":                ns.get("flood_pts", 0),
        "visual_pts":               ns.get("visual_pts", 5),
        "walk_score":               ns.get("walk_score"),
        "transit_score":            ns.get("transit_score"),
        "bike_score":               ns.get("bike_score"),
        "crime_grade":              ns.get("crime_grade", "UNKNOWN"),
        "crime_low_confidence":     ns.get("crime_low_confidence", False),
        "flood_zone":               ns.get("flood_zone", "UNKNOWN"),
        "flood_high_risk":          ns.get("flood_high_risk", False),
        "hospital_distance_miles":  ns.get("hospital_distance_miles"),
        "closest_hospital":         ns.get("closest_hospital", ""),
        "proximity_score":          ns.get("proximity_score", 0),
        "passed_gates":             ns.get("passed_gates", False),
        "failed_gate_reasons":      ns.get("failed_gate_reasons", []),
        "street_view_url":          ns.get("street_view_url"),
        "satellite_url":            ns.get("satellite_url"),
        "maps_link":                ns.get("maps_link"),
    })


async def get_neighborhood(session: AsyncSession, property_id: str) -> dict | None:
    result = await session.execute(
        text("SELECT * FROM neighborhood_scores WHERE property_id = :pid"),
        {"pid": property_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_failed_gate_properties(session: AsyncSession, limit: int = 500) -> list[dict]:
    """Return all rejected properties for the Rejected tab.

    Two groups, unioned:
      - failed_gates: failed a hard neighborhood gate (crime/flood/zip) — no score
      - scored_low:   passed all gates but deal_score < 40 — includes score + reasons
    """
    result = await session.execute(
        text("""
            -- Group 1: hard-gate failures (no deal score)
            SELECT p.canonical_id, p.address, p.zip_code, p.price,
                   p.property_type, p.num_units, p.days_on_market,
                   ns.crime_grade, ns.flood_zone, ns.flood_high_risk,
                   ns.failed_gate_reasons,
                   'failed_gates'::text    AS reject_type,
                   NULL::int               AS deal_score,
                   NULL::int               AS return_score,
                   NULL::int               AS risk_score,
                   NULL::int               AS confidence_score,
                   NULL::double precision  AS conservative_cash_flow,
                   NULL::text[]            AS score_reasons
            FROM neighborhood_scores ns
            JOIN properties p ON ns.property_id = p.canonical_id
            WHERE ns.passed_gates = FALSE

            UNION ALL

            -- Group 2: passed gates, scored < 40
            SELECT p.canonical_id, p.address, p.zip_code, p.price,
                   p.property_type, p.num_units, p.days_on_market,
                   ns.crime_grade, ns.flood_zone, ns.flood_high_risk,
                   NULL                    AS failed_gate_reasons,
                   'scored_low'::text      AS reject_type,
                   ds.deal_score,
                   ds.return_score,
                   ds.risk_score,
                   ds.confidence_score,
                   ds.conservative_cash_flow,
                   ds.alert_reasons        AS score_reasons
            FROM (
                SELECT DISTINCT ON (property_id)
                    property_id, deal_score, return_score, risk_score,
                    confidence_score, conservative_cash_flow, alert_reasons
                FROM deal_scores
                ORDER BY property_id, scored_at DESC
            ) ds
            JOIN properties p ON ds.property_id = p.canonical_id
            JOIN neighborhood_scores ns ON ns.property_id = p.canonical_id
            WHERE ds.deal_score < 40

            ORDER BY deal_score DESC NULLS LAST
            LIMIT :lim
        """),
        {"lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]
