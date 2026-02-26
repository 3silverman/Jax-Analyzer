"""
neighborhood/crime_grade.py

CrimeGrade.org crime grade lookup for a neighborhood.

Strategy: scrape the CrimeGrade.org page for the given zip code.
If scraping fails (bot detection, structural change), returns a mock/unknown
grade with low_confidence=True so it never passes the B-or-above hard gate.

Hard gate: property fails if crime grade is C, D, or F.
Pass grades: A+, A, A-, B+, B, B-
"""

from __future__ import annotations

import re
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


class CrimeGradeResult(TypedDict):
    grade:           str     # e.g. "B+", "C", "UNKNOWN"
    passes_gate:     bool    # True = B or above
    low_confidence:  bool    # True = scraped data unreliable; never surfaces as high-priority


def _letter_grade_from_html(html: str) -> str | None:
    """
    Attempt to extract a letter grade (A+, A, B-, C, etc.) from CrimeGrade HTML.
    Returns None if no recognisable grade is found.
    """
    # CrimeGrade typically renders the grade in a large element with class "grade"
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


def get_crime_grade(zip_code: str) -> CrimeGradeResult:
    """
    Fetch the crime grade for a zip code from CrimeGrade.org.

    Args:
        zip_code: Five-digit US zip code string.

    Returns:
        CrimeGradeResult with grade, passes_gate flag, and low_confidence flag.
        On any scraping failure returns grade="UNKNOWN", passes_gate=False,
        low_confidence=True so the property cannot pass the hard gate.
    """
    url = f"{_BASE_URL}{zip_code}-fl/"
    try:
        logger.info("crime_grade_request", zip_code=zip_code)
        response = httpx.get(url, headers=_HEADERS, timeout=20, follow_redirects=True)
        response.raise_for_status()
        html = response.text
    except Exception as exc:
        logger.warning("crime_grade_scrape_error", zip_code=zip_code, error=str(exc))
        return CrimeGradeResult(grade="UNKNOWN", passes_gate=False, low_confidence=True)

    grade = _letter_grade_from_html(html)
    if grade is None:
        logger.warning("crime_grade_parse_failed", zip_code=zip_code)
        return CrimeGradeResult(grade="UNKNOWN", passes_gate=False, low_confidence=True)

    passes = grade in PASSING_GRADES
    result = CrimeGradeResult(grade=grade, passes_gate=passes, low_confidence=False)
    logger.info("crime_grade_result", zip_code=zip_code, grade=grade, passes=passes)
    return result
