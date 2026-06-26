"""
db.py — PostgreSQL persistence for the AI Cloud Cost Detective (AWS version).

Covers step ⑥ of the request flow: storing analysis results (and users).

The Azure reference used "Azure Managed PostgreSQL". That's just PostgreSQL —
nothing in this code is Azure-specific. Point DATABASE_URL at any Postgres
(AWS RDS/Aurora, Azure, or local) and it works the same. We use asyncpg because
FastAPI is async and we have a WebSocket; a blocking driver would stall the
event loop.

Schema (created on startup):
  users     — id, email, password_hash, created_at
  analyses  — id, user_id, services_scanned, resources_scanned, issues_found,
              estimated_savings, analysis_result (jsonb), status, created_at

NOTE on the schema vs the Azure prompt: the prompt's `analyses.resource_group`
column becomes `services_scanned` here, because in AWS we scan a set of services,
not a resource group. Everything else matches the prompt 1:1.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import asyncpg


# A module-level pool, created on startup and reused across requests.
_pool: Optional[asyncpg.Pool] = None


class DBNotConfigured(Exception):
    """Raised when DATABASE_URL is missing — lets the app run without a DB in dev."""


def _dsn() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise DBNotConfigured(
            "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
            "and set DATABASE_URL to your PostgreSQL connection string."
        )
    return url


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analyses (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    services_scanned  TEXT,
    resources_scanned INTEGER NOT NULL DEFAULT 0,
    issues_found      INTEGER NOT NULL DEFAULT 0,
    estimated_savings TEXT,
    analysis_result   JSONB,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def init_db() -> bool:
    """
    Create the connection pool and tables on startup.

    Returns True if the DB is connected, False if DATABASE_URL is unset (so the
    app can still boot and serve non-DB endpoints in local dev). Any *other*
    failure (bad URL, server down) is raised so it's not silently swallowed.
    """
    global _pool
    try:
        dsn = _dsn()
    except DBNotConfigured:
        return False

    _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
    return True


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def is_connected() -> bool:
    return _pool is not None


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise DBNotConfigured("Database is not connected (DATABASE_URL not set).")
    return _pool


# --------------------------------------------------------------------------- #
# analyses queries                                                             #
# --------------------------------------------------------------------------- #
async def create_analysis(user_id: Optional[int], services_scanned: str) -> int:
    """
    Insert a 'pending' analysis row up front and return its id.

    We create the row BEFORE scanning so we have an analysis_id to use as the
    WebSocket channel (ws/progress/{analysis_id}) and so a crash mid-scan still
    leaves a 'pending'/'failed' record rather than nothing.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO analyses (user_id, services_scanned, status)
            VALUES ($1, $2, 'pending')
            RETURNING id
            """,
            user_id,
            services_scanned,
        )
    return row["id"]


async def complete_analysis(
    analysis_id: int,
    *,
    resources_scanned: int,
    issues_found: int,
    estimated_savings: str,
    analysis_result: dict[str, Any],
    status: str = "complete",
) -> None:
    """Fill in the results after the scan + AI analysis finish (step ⑥)."""
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analyses
               SET resources_scanned = $2,
                   issues_found      = $3,
                   estimated_savings = $4,
                   analysis_result   = $5::jsonb,
                   status            = $6
             WHERE id = $1
            """,
            analysis_id,
            resources_scanned,
            issues_found,
            estimated_savings,
            json.dumps(analysis_result, default=str),
            status,
        )


async def fail_analysis(analysis_id: int, error: str) -> None:
    """Mark an analysis 'failed' (e.g. AI error) so history shows what happened."""
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE analyses
               SET status = 'failed',
                   analysis_result = $2::jsonb
             WHERE id = $1
            """,
            analysis_id,
            json.dumps({"error": error}),
        )


async def get_history(user_id: Optional[int], limit: int = 50) -> list[dict]:
    """
    Return past analyses for a user (newest first).

    If user_id is None we return recent analyses across all users — useful in
    dev before auth exists. Once JWT auth lands, the route always passes the
    authenticated user's id.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            rows = await conn.fetch(
                """
                SELECT id, user_id, services_scanned, resources_scanned,
                       issues_found, estimated_savings, analysis_result,
                       status, created_at
                  FROM analyses
                 ORDER BY created_at DESC
                 LIMIT $1
                """,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, user_id, services_scanned, resources_scanned,
                       issues_found, estimated_savings, analysis_result,
                       status, created_at
                  FROM analyses
                 WHERE user_id = $1
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                user_id,
                limit,
            )

    history = []
    for r in rows:
        d = dict(r)
        # asyncpg returns jsonb as a string; decode for a clean JSON response.
        if isinstance(d.get("analysis_result"), str):
            d["analysis_result"] = json.loads(d["analysis_result"])
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        history.append(d)
    return history
