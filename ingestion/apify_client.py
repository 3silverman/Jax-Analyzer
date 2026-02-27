"""
ingestion/apify_client.py

Thin HTTP wrapper around the Apify API for running actors and fetching results.

Does NOT perform any normalization — returns raw actor output dicts.
Normalization happens in normalization/normalizer.py.
"""

from __future__ import annotations

import os
import time
from typing import Any, Generator

import httpx
import structlog

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.apify.com/v2"
_DEFAULT_TIMEOUT = 60          # seconds per HTTP request
_POLL_INTERVAL   = 10          # seconds between run-status polls
_MAX_POLL_SECS   = 600         # 10 minutes max wait per actor run


class ApifyError(Exception):
    """Raised when the Apify API returns an error or the run times out."""


class ApifyClient:
    """
    Minimal Apify REST client.

    Usage::

        client = ApifyClient()  # reads APIFY_TOKEN from env
        items = client.run_actor("lukaskrivka/zillow-scraper", input_payload={...})
        for item in items:
            ...
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("APIFY_TOKEN", "")
        if not self._token:
            raise ApifyError("APIFY_TOKEN is not set in the environment.")
        self._http = httpx.Client(
            timeout=_DEFAULT_TIMEOUT,
            headers={"Authorization": f"Bearer {self._token}"},
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _post(self, path: str, json: dict) -> dict[str, Any]:
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        response = self._http.post(url, json=json)
        response.raise_for_status()
        return response.json()

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{_BASE_URL}/{path.lstrip('/')}"
        response = self._http.get(url, params=params or {})
        response.raise_for_status()
        return response.json()

    def _wait_for_run(self, run_id: str) -> str:
        """
        Poll until the actor run is SUCCEEDED or FAILED/TIMED-OUT.
        Returns the default dataset ID on success.
        Raises ApifyError on failure.
        """
        deadline = time.monotonic() + _MAX_POLL_SECS
        while time.monotonic() < deadline:
            data = self._get(f"actor-runs/{run_id}")
            status = data.get("data", {}).get("status", "")
            logger.debug("apify_run_status", run_id=run_id, status=status)

            if status == "SUCCEEDED":
                return data["data"]["defaultDatasetId"]
            if status in ("FAILED", "TIMED-OUT", "ABORTED"):
                raise ApifyError(f"Apify run {run_id} ended with status: {status}")

            time.sleep(_POLL_INTERVAL)

        raise ApifyError(f"Apify run {run_id} timed out after {_MAX_POLL_SECS}s")

    def _iter_dataset(self, dataset_id: str, batch: int = 1000) -> Generator[dict[str, Any], None, None]:
        """Yield items from a dataset in batches."""
        offset = 0
        while True:
            data = self._get(
                f"datasets/{dataset_id}/items",
                params={"offset": offset, "limit": batch, "clean": "true"},
            )
            items: list[dict] = data.get("data", {}).get("items", [])
            if not items:
                break
            yield from items
            offset += len(items)
            if len(items) < batch:
                break

    # ── Public API ────────────────────────────────────────────────────────────

    def run_actor(
        self,
        actor_id: str,
        input_payload: dict[str, Any],
        memory_mbytes: int = 512,
    ) -> list[dict[str, Any]]:
        """
        Run an Apify actor synchronously and return all output items.

        Args:
            actor_id:        Actor slug, e.g. "lukaskrivka/zillow-scraper".
            input_payload:   JSON body sent to the actor as its input.
            memory_mbytes:   Memory limit for the actor run.

        Returns:
            List of raw item dicts from the actor's default dataset.
        """
        logger.info("apify_actor_start", actor_id=actor_id)

        # Apify REST API: input is the raw body; memory is a query param
        url = f"{_BASE_URL}/acts/{actor_id}/runs"
        response = self._http.post(url, json=input_payload, params={"memory": memory_mbytes})
        response.raise_for_status()
        run_id = response.json()["data"]["id"]
        logger.info("apify_run_created", run_id=run_id)

        dataset_id = self._wait_for_run(run_id)
        items = list(self._iter_dataset(dataset_id))
        logger.info("apify_actor_done", actor_id=actor_id, item_count=len(items))
        return items

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ApifyClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
