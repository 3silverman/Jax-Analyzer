"""
scoring/ranker.py

Ranks a list of scored deals and partitions them into UI buckets.

Buckets:
  alerts   — Deal Score ≥ 85 AND is_high_priority=True
  inbox    — Deal Score 60–84
  review   — Deal Score 40–59
  rejected — Deal Score < 40 OR failed hard gates

Each bucket is sorted by Deal Score descending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoring.deal_scorer import DealScore


@dataclass
class RankedDeals:
    """Partitioned and sorted deal lists for UI routing."""
    alerts:   list[tuple[Any, DealScore]]  # (PropertyRecord or id, DealScore)
    inbox:    list[tuple[Any, DealScore]]
    review:   list[tuple[Any, DealScore]]
    rejected: list[tuple[Any, DealScore]]

    @property
    def total(self) -> int:
        return len(self.alerts) + len(self.inbox) + len(self.review) + len(self.rejected)

    @property
    def alert_count(self) -> int:
        return len(self.alerts)


def rank_deals(
    scored_deals: list[tuple[Any, DealScore]],
    failed_gate_ids: set | None = None,
) -> RankedDeals:
    """
    Partition and sort scored deals into UI buckets.

    Args:
        scored_deals:    List of (property_record_or_id, DealScore) tuples.
        failed_gate_ids: Set of property ids (canonical_id) that failed hard gates.
                         These go directly to rejected regardless of score.

    Returns:
        RankedDeals with four sorted buckets.
    """
    failed_gate_ids = failed_gate_ids or set()

    alerts:   list[tuple[Any, DealScore]] = []
    inbox:    list[tuple[Any, DealScore]] = []
    review:   list[tuple[Any, DealScore]] = []
    rejected: list[tuple[Any, DealScore]] = []

    for item, ds in scored_deals:
        # Determine property id (supports both PropertyRecord objects and plain ids)
        prop_id = getattr(item, "canonical_id", item)

        if prop_id in failed_gate_ids or ds.return_score.disqualified:
            rejected.append((item, ds))
        elif ds.deal_score >= 85 and ds.is_high_priority:
            alerts.append((item, ds))
        elif ds.deal_score >= 60:
            inbox.append((item, ds))
        elif ds.deal_score >= 40:
            review.append((item, ds))
        else:
            rejected.append((item, ds))

    def by_score(pair: tuple) -> int:
        return pair[1].deal_score

    alerts.sort(key=by_score, reverse=True)
    inbox.sort(key=by_score, reverse=True)
    review.sort(key=by_score, reverse=True)
    rejected.sort(key=by_score, reverse=True)

    return RankedDeals(alerts=alerts, inbox=inbox, review=review, rejected=rejected)
