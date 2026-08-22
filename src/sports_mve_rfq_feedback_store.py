"""Durable manufacturing-only RFQ feedback for sports MVE candidate ranking.

This store is not an execution gate. If it is unavailable, callers should keep the
existing process-local feedback and continue scanning; every live candidate remains
subject to independent rule, depth, freshness, fee, capital, and order-entry gates.
"""
from __future__ import annotations

from decimal import Decimal
import os
from typing import Any, Mapping

import psycopg

D = Decimal
_TABLE = "sports_mve_rfq_feedback"


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


async def _ensure(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            signature text PRIMARY KEY,
            observations bigint NOT NULL DEFAULT 0 CHECK (observations >= 0),
            quoted bigint NOT NULL DEFAULT 0 CHECK (quoted >= 0),
            best_lock numeric NOT NULL DEFAULT -9,
            last_lock numeric NOT NULL DEFAULT -9,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


async def load_feedback() -> dict[str, dict[str, Any]]:
    """Load the durable signature-level feedback snapshot."""
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL missing for persistent sports MVE RFQ feedback")
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await _ensure(conn)
        cur = await conn.execute(
            f"SELECT signature, observations, quoted, best_lock, last_lock FROM {_TABLE}"
        )
        rows = await cur.fetchall()
    return {
        str(signature): {
            "observations": int(observations),
            "quoted": int(quoted),
            "best_lock": D(str(best_lock)),
            "last_lock": D(str(last_lock)),
        }
        for signature, observations, quoted, best_lock, last_lock in rows
    }


def snapshot(feedback: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Copy feedback into normalized scalar values for delta accounting."""
    return {
        str(signature): {
            "observations": int(row.get("observations", 0)),
            "quoted": int(row.get("quoted", 0)),
            "best_lock": D(str(row.get("best_lock", "-9"))),
            "last_lock": D(str(row.get("last_lock", "-9"))),
        }
        for signature, row in feedback.items()
    }


async def merge_feedback_delta(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> int:
    """Atomically add this process cycle's observation deltas to durable feedback.

    Counts are incremented rather than replaced, making this restart-safe and safe
    if more than one process contributes observations. best_lock is monotonic; the
    cycle's latest last_lock is retained for diagnostics.
    """
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL missing for persistent sports MVE RFQ feedback")

    deltas: list[tuple[str, int, int, D, D]] = []
    for signature, row in after.items():
        prev = before.get(signature) or {}
        observations_delta = int(row.get("observations", 0)) - int(prev.get("observations", 0))
        quoted_delta = int(row.get("quoted", 0)) - int(prev.get("quoted", 0))
        if observations_delta <= 0 and quoted_delta <= 0:
            continue
        deltas.append((
            str(signature),
            max(0, observations_delta),
            max(0, quoted_delta),
            D(str(row.get("best_lock", "-9"))),
            D(str(row.get("last_lock", "-9"))),
        ))

    if not deltas:
        return 0

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await _ensure(conn)
        for signature, observations, quoted, best_lock, last_lock in deltas:
            await conn.execute(
                f"""
                INSERT INTO {_TABLE}
                    (signature, observations, quoted, best_lock, last_lock)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (signature) DO UPDATE SET
                    observations = {_TABLE}.observations + EXCLUDED.observations,
                    quoted = {_TABLE}.quoted + EXCLUDED.quoted,
                    best_lock = GREATEST({_TABLE}.best_lock, EXCLUDED.best_lock),
                    last_lock = EXCLUDED.last_lock,
                    updated_at = now()
                """,
                (signature, observations, quoted, best_lock, last_lock),
            )
    return len(deltas)
