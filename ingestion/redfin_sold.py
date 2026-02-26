"""
ingestion/redfin_sold.py

Redfin sold comps via Apify actor for VA appraisal gap risk analysis.

Pulls last 6 months of multifamily + SFH sold listings in the target zip code,
computes a zip-level median sold price, and flags VA appraisal gap risk when
the listing price exceeds the median by more than 15%.

Actor: apify/redfin-scraper (or equivalent)
Auth: APIFY_TOKEN env var
"""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_APIFY_URL = "https://api.apify.com/v2/acts/apify~redfin-scraper/runs"
_SOLD_LOOKBACK_DAYS = 180  # 6 months


@dataclass
class SoldCompsResult:
    zip_code: str
    median_sold_price: float | None    # None if no comps found
    comps_count: int
    appraisal_gap_risk: bool           # True if listing > 15% above median
    gap_pct: float | None              # signed % above median (may be negative)
    raw_prices: list[float]


def get_zip_median_sold_price(
    zip_code: str,
    apify_token: str | None = None,
) -> SoldCompsResult:
    """
    Fetch recently-sold multifamily/SFH prices in a zip code via Apify Redfin actor.

    Args:
        zip_code:     5-digit zip code string.
        apify_token:  Apify API token; falls back to APIFY_TOKEN env var.

    Returns:
        SoldCompsResult. median_sold_price is None if no data available.
    """
    token = apify_token or os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        logger.warning("redfin_sold_no_apify_token")
        return SoldCompsResult(zip_code=zip_code, median_sold_price=None,
                               comps_count=0, appraisal_gap_risk=False,
                               gap_pct=None, raw_prices=[])

    try:
        import httpx
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=_SOLD_LOOKBACK_DAYS))

        input_data = {
            "search": zip_code,
            "status": "sold",
            "home_type": ["multi-family", "single_family"],
            "sort": "sold_date",
            "max_results": 100,
        }

        headers = {"Authorization": f"Bearer {token}"}
        logger.info("redfin_sold_fetch", zip_code=zip_code)
        resp = httpx.post(
            _APIFY_URL,
            json={"runInput": input_data},
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        run_id = resp.json().get("data", {}).get("id")
        if not run_id:
            raise ValueError("No run ID returned from Apify")

        # Poll for result (synchronous wait with timeout)
        result_url = f"https://api.apify.com/v2/acts/apify~redfin-scraper/runs/{run_id}/dataset/items"
        import time
        for _ in range(30):  # max 60 seconds
            time.sleep(2)
            r = httpx.get(result_url, headers=headers, timeout=15)
            if r.status_code == 200 and r.json():
                items = r.json()
                break
        else:
            logger.warning("redfin_sold_timeout", zip_code=zip_code)
            return _empty_result(zip_code)

        prices = _extract_prices(items, zip_code, cutoff)
        return _compute_result(zip_code, prices)

    except Exception as exc:
        logger.warning("redfin_sold_error", zip_code=zip_code, error=str(exc))
        return _empty_result(zip_code)


def compute_appraisal_gap(
    purchase_price: float,
    zip_median: float | None,
    threshold_pct: float = 0.15,
) -> tuple[bool, float | None]:
    """
    Check whether purchase_price exceeds zip_median by more than threshold_pct.

    Returns:
        (has_gap_risk: bool, gap_pct: float | None)
        gap_pct is None when zip_median is None.
    """
    if not zip_median or zip_median <= 0:
        return False, None
    gap = (purchase_price - zip_median) / zip_median
    return gap > threshold_pct, round(gap, 4)


def _extract_prices(
    items: list[dict[str, Any]],
    zip_code: str,
    cutoff: datetime,
) -> list[float]:
    """Pull sold prices from Apify result items, filtering by zip and date."""
    prices = []
    for item in items:
        item_zip = str(item.get("zipCode") or item.get("zip_code") or "")
        if item_zip and item_zip != zip_code:
            continue

        # Price from various possible keys
        price = (
            item.get("soldPrice")
            or item.get("sold_price")
            or item.get("price")
        )
        if not price:
            continue
        try:
            price = float(str(price).replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            continue

        if price < 10_000:  # sanity check
            continue

        # Date filter
        sold_date = item.get("soldDate") or item.get("sold_date") or ""
        if sold_date:
            try:
                dt = datetime.fromisoformat(str(sold_date)[:10])
                dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            except ValueError:
                pass  # Accept if date unparseable

        prices.append(price)

    return prices


def _compute_result(zip_code: str, prices: list[float]) -> SoldCompsResult:
    if not prices:
        return _empty_result(zip_code)
    median = statistics.median(prices)
    return SoldCompsResult(
        zip_code=zip_code,
        median_sold_price=median,
        comps_count=len(prices),
        appraisal_gap_risk=False,   # caller computes against listing price
        gap_pct=None,
        raw_prices=prices,
    )


def _empty_result(zip_code: str) -> SoldCompsResult:
    return SoldCompsResult(
        zip_code=zip_code,
        median_sold_price=None,
        comps_count=0,
        appraisal_gap_risk=False,
        gap_pct=None,
        raw_prices=[],
    )
