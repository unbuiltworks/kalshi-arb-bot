"""Restart-safe daily budget for automatic Kalshi sports MVE creation.

The generator must fail closed if this ledger is unavailable: without a durable
counter a Railway restart could reset the process-local creation cap.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os

import psycopg

_TABLE = "sports_mve_creation_daily"


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _seed_today() -> int:
    try:
        return max(0, int(os.getenv("MVE_CREATION_LEDGER_SEED_TODAY", "0")))
    except Exception:
        return 0


async def _ensure(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            day date PRIMARY KEY,
            created_count integer NOT NULL DEFAULT 0 CHECK (created_count >= 0),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


async def get_or_seed_today() -> int:
    """Return today's durable count, seeding only when today's row is absent."""
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL missing for persistent MVE creation budget")
    day = datetime.now(timezone.utc).date()
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await _ensure(conn)
        seed = _seed_today()
        await conn.execute(
            f"INSERT INTO {_TABLE} (day, created_count) VALUES (%s, %s) ON CONFLICT (day) DO NOTHING",
            (day, seed),
        )
        cur = await conn.execute(f"SELECT created_count FROM {_TABLE} WHERE day = %s", (day,))
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("persistent MVE creation budget row unavailable")
        return int(row[0])


async def reserve_today(cap: int) -> int | None:
    """Atomically reserve one creation attempt. Returns new count or None at cap."""
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL missing for persistent MVE creation budget")
    day = datetime.now(timezone.utc).date()
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await _ensure(conn)
        seed = _seed_today()
        await conn.execute(
            f"INSERT INTO {_TABLE} (day, created_count) VALUES (%s, %s) ON CONFLICT (day) DO NOTHING",
            (day, seed),
        )
        cur = await conn.execute(
            f"""
            UPDATE {_TABLE}
               SET created_count = created_count + 1,
                   updated_at = now()
             WHERE day = %s
               AND created_count < %s
         RETURNING created_count
            """,
            (day, int(cap)),
        )
        row = await cur.fetchone()
        return None if row is None else int(row[0])
