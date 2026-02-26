"""
api/state.py

Simple in-memory store for the application state.
Replaced by DB repositories in production (Session 7).
"""

from __future__ import annotations

_store: dict = {}


def get_store() -> dict:
    return _store


def reset_store() -> None:
    """Test helper to clear state between tests."""
    _store.clear()
