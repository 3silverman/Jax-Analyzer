"""
neighborhood/crime_grade.py

CrimeGrade.org crime grade lookup for a neighborhood.

Caching strategy (priority order):
  1. In-memory process cache (lasts for the process lifetime; cleared between daily runs)
  2. DB cache via crime_grade_repo (7-day TTL; checked via get_cached_crime_grade())
  3. Live scrape from CrimeGrade.org (falls back to UNKNOWN/low_confidence on failure)

Hard gate: property fails if crime grade is C, D, or F.
Pass grades: A+, A, A-, B+, B, B-
"""

from __future__ import annotations

import re
import time
from typing import TypedDict

import httpx
import structlog

logger = structlog.get_logger(__name__)

_BASE_URL = "https://crimegrade.org/safest-places-in-"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Grades that pass the hard gate (B or above)
PASSING_GRADES: frozenset[str] = frozenset({"A+", "A", "A-", "B+", "B", "B-"})

# Process-level in-memory cache: {zip_code: (result_dict, timestamp)}
_mem_cache: dict[str, tuple[dict, float]] = {}
_MEM_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class CrimeGradeResult(TypedDict):
    grade:           str     # e.g. "B+", "C", "UNKNOWN"
    passes_gate:     bool    # True = B or above
    low_confidence:  bool    # True = scraped data unreliable


def _letter_grade_from_html(html: str) -> str | None:
    """Extract a letter grade from CrimeGrade HTML. Returns None if not found."""
    patterns = [
        r'class="[^"]*grade[^"]*"[^>]*>\s*([A-F][+-]?)\s*<',
        r'Overall Crime Grade.*?([A-F][+-]?)',
        r'"grade"\s*:\s*"([A-F][+-]?)"',
    ]
    for pat in patterns:
        match = re.search(pat, html, re.IGNORECASE | re.DOTALL)
        if match:
            g = match.group(1).strip().upper()
            if re.match(r'^[A-F][+-]?$', g):
                return g
    return None


def _make_result(grade: str, low_confidence: bool = False) -> CrimeGradeResult:
    passes = grade in PASSING_GRADES
    return CrimeGradeResult(grade=grade, passes_gate=passes, low_confidence=low_confidence)


def _unknown() -> CrimeGradeResult:
    return CrimeGradeResult(grade="UNKNOWN", passes_gate=False, low_confidence=True)


def _check_mem_cache(zip_code: str) -> CrimeGradeResult | None:
    entry = _mem_cache.get(zip_code)
    if entry:
        result, ts = entry
        if time.monotonic() - ts < _MEM_CACHE_TTL_SECONDS:
            return CrimeGradeResult(**result)
    return None


def _put_mem_cache(zip_code: str, result: CrimeGradeResult) -> None:
    _mem_cache[zip_code] = (dict(result), time.monotonic())


def clear_mem_cache() -> None:
    """Clear the process-level cache (for tests and between daily runs)."""
    _mem_cache.clear()


def _scrape(zip_code: str) -> CrimeGradeResult:
    """Live scrape from CrimeGrade.org. Returns UNKNOWN on any error."""
    url = f"{_BASE_URL}{zip_code}-fl/"
    try:
        logger.info("crime_grade_request", zip_code=zip_code)
        response = httpx.get(url, headers=_HEADERS, timeout=20, follow_redirects=True)
        response.raise_for_status()
        html = response.text
    except Exception as exc:
        logger.warning("crime_grade_scrape_error", zip_code=zip_code, error=str(exc))
        return _unknown()

    grade = _letter_grade_from_html(html)
    if grade is None:
        logger.warning("crime_grade_parse_failed", zip_code=zip_code)
        return _unknown()

    result = _make_result(grade)
    logger.info("crime_grade_result", zip_code=zip_code, grade=grade, passes=result["passes_gate"])
    return result


def get_crime_grade(zip_code: str) -> CrimeGradeResult:
    """
    Synchronous crime grade lookup with in-memory process cache.

    Use get_cached_crime_grade() (async, DB-backed) from the pipeline for
    full DB caching with 7-day TTL and manual overrides.

    Args:
        zip_code: Five-digit US zip code string.

    Returns:
        CrimeGradeResult. Returns UNKNOWN/low_confidence=True on any scraping failure.
    """
    cached = _check_mem_cache(zip_code)
    if cached is not None:
        return cached

    result = _scrape(zip_code)
    _put_mem_cache(zip_code, result)
    return result


async def get_cached_crime_grade(zip_code: str) -> CrimeGradeResult:
    """
    Async DB-backed crime grade lookup with 7-day TTL and manual override support.

    Priority:
      1. In-memory process cache
      2. DB manual override (takes precedence, never expires)
      3. DB scrape cache (7-day TTL)
      4. Live scrape → stored in DB cache for next call

    Gracefully falls back to synchronous get_crime_grade() if DB unavailable.
    """
    # 1. In-memory
    cached = _check_mem_cache(zip_code)
    if cached is not None:
        return cached

    # 2 & 3. DB lookup
    try:
        from db.connection import get_session
        from db.repositories.crime_grade_repo import get_cached_grade, store_grade

        async with get_session() as session:
            db_result = await get_cached_grade(session, zip_code)

        if db_result:
            result = CrimeGradeResult(
                grade=db_result["grade"],
                passes_gate=db_result["passes_gate"],
                low_confidence=db_result.get("low_confidence", False),
            )
            _put_mem_cache(zip_code, result)
            return result

        # 4. Live scrape → store in DB
        result = _scrape(zip_code)
        try:
            async with get_session() as session:
                await store_grade(
                    session, zip_code,
                    result["grade"], result["passes_gate"], result["low_confidence"]
                )
        except Exception as exc:
            logger.warning("crime_grade_db_store_failed", zip_code=zip_code, error=str(exc))

        _put_mem_cache(zip_code, result)
        return result

    except Exception as exc:
        logger.warning("crime_grade_db_unavailable", zip_code=zip_code, error=str(exc))
        return get_crime_grade(zip_code)
