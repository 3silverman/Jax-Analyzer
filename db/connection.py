"""
db/connection.py

SQLAlchemy async engine and session factory for Supabase (Postgres).

Usage (in FastAPI lifespan or repository):
    from db.connection import get_session
    async with get_session() as session:
        result = await session.execute(...)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

_engine = None
_Session = None


def _build_engine():
    global _engine, _Session
    url = os.environ.get("SUPABASE_URL", "")
    if not url:
        raise RuntimeError("SUPABASE_URL environment variable is not set.")

    # Supabase connection string format:
    # postgresql+asyncpg://postgres:<password>@<host>:<port>/postgres
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif not url.startswith("postgresql+asyncpg://"):
        raise ValueError(f"Unsupported SUPABASE_URL scheme: {url[:30]!r}")

    _engine = create_async_engine(
        url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    _Session = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding a database session."""
    global _Session
    if _Session is None:
        _build_engine()
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class Base(DeclarativeBase):
    """Declarative base for all ORM models (optional — repositories use raw SQL)."""
    pass


async def close_engine() -> None:
    """Gracefully close the connection pool (call in app shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
