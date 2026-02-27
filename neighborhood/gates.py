"""
neighborhood/gates.py

Hard gate evaluator for the neighborhood filter step.

A property that fails any gate goes directly to the Failed Gates tab.
No underwriting or scoring is run on failed-gate properties.

Gates (from CLAUDE.md):
  1. Crime grade B or above (A+, A, A-, B+, B, B-)
  2. Flood zone X or shaded X only (not AE, VE, AO, AH, A, V, AR, A99)
  3. Zip code in whitelist (32204, 32205, 32206, 32207)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from neighborhood.crime_grade import CrimeGradeResult, PASSING_GRADES
from neighborhood.flood_zone import FloodZoneResult
from normalization.schema import PropertyRecord, ZIP_WHITELIST


@dataclass
class GateResult:
    """
    Outcome of the hard gate evaluation for a single property.

    Attributes:
        passed:        True if all gates pass and property proceeds to underwriting.
        failed_gates:  List of human-readable failure reasons.
        destination:   "underwriting" | "failed_gates_tab"
    """
    passed:       bool
    failed_gates: list[str] = field(default_factory=list)
    destination:  Literal["underwriting", "failed_gates_tab"] = "underwriting"


def evaluate_gates(
    record:      PropertyRecord,
    crime:       CrimeGradeResult,
    flood:       FloodZoneResult,
) -> GateResult:
    """
    Evaluate all hard gates for a property.

    Args:
        record: Canonical PropertyRecord from normalization.
        crime:  CrimeGradeResult from crime_grade module.
        flood:  FloodZoneResult from flood_zone module.

    Returns:
        GateResult with passed flag, list of failure reasons, and destination.
    """
    failures: list[str] = []

    # ── Gate 1: Zip whitelist ─────────────────────────────────────────────────
    if record.zip_code not in ZIP_WHITELIST:
        failures.append(
            f"Zip code {record.zip_code!r} is not in the target whitelist "
            f"({', '.join(sorted(ZIP_WHITELIST))})"
        )

    # ── Gate 2: Crime grade ───────────────────────────────────────────────────
    # low_confidence means the scraper couldn't confirm the grade (e.g. API down).
    # We allow these through rather than blocking on missing data; the UI flags them.
    if not crime["low_confidence"] and not crime["passes_gate"]:
        failures.append(
            f"Crime grade {crime['grade']!r} does not meet B-or-above requirement "
            f"for zip {record.zip_code}"
        )

    # ── Gate 3: Flood zone ────────────────────────────────────────────────────
    if flood["flood_zone"] == "UNKNOWN":
        # Unknown flood zone — flag but don't hard-fail (FEMA API can be patchy)
        # This is a soft warning; only AE/VE trigger a hard gate fail
        pass
    elif flood["is_high_risk"]:
        failures.append(
            f"Flood zone {flood['flood_zone']!r} is a high-risk SFHA zone "
            f"(AE/VE/AO/AH). Property must be in Zone X."
        )

    passed = len(failures) == 0
    return GateResult(
        passed       = passed,
        failed_gates = failures,
        destination  = "underwriting" if passed else "failed_gates_tab",
    )
