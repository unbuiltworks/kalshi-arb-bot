"""Best-effort persistent telemetry / fill-learning for Gemini combo market making.

This module is intentionally off the execution critical path. Database failures
never authorize a trade and never change economics. A dedicated table stores
candidate snapshots, quote decisions, maker cancels/fills, and hedge outcomes so
fillability can be calibrated from actual attempts rather than intuition.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json, os, time
from typing import Any, Iterator, Mapping

try:
    import psycopg
except Exception:  # pragma: no cover - optional runtime dependency
    psycopg = None

TABLE = "gemini_combo_mm_events"


def _dsn() -> str:
    return os.getenv("DATABASE_URL", "").strip()


@contextmanager
def connection() -> Iterator[Any]:
    dsn = _dsn()
    if not dsn or psycopg is None:
        yield None
        return
    conn = None
    try:
        conn = psycopg.connect(dsn, connect_timeout=4, autocommit=True)
        yield conn
    except Exception:
        yield None
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass


def ensure_schema() -> bool:
    sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
      id BIGSERIAL PRIMARY KEY,
      ts_ms BIGINT NOT NULL,
      run_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      combo_key TEXT NOT NULL,
      signature TEXT NOT NULL,
      leg_count INTEGER NOT NULL,
      quantity INTEGER NOT NULL,
      safe_price NUMERIC,
      fair_proxy NUMERIC,
      fair_gap NUMERIC,
      locked_per_contract NUMERIC,
      fillability DOUBLE PRECISION,
      expected_dollars_per_hour NUMERIC,
      capital_reserve NUMERIC,
      details JSONB NOT NULL DEFAULT '{{}}'::jsonb
    );
    CREATE INDEX IF NOT EXISTS {TABLE}_signature_idx ON {TABLE}(signature,event_type,ts_ms DESC);
    CREATE INDEX IF NOT EXISTS {TABLE}_run_idx ON {TABLE}(run_id,ts_ms DESC);
    """
    with connection() as conn:
        if conn is None: return False
        try:
            with conn.cursor() as cur: cur.execute(sql)
            return True
        except Exception:
            return False


def signature(*, leg_count: int, sports: list[str] | None = None, families: list[str] | None = None) -> str:
    return json.dumps({"leg_count":int(leg_count),"sports":sorted(set(sports or [])),"families":sorted(set(families or []))},separators=(",",":"),sort_keys=True)


def emit(*, run_id: str, event_type: str, combo_key: str, signature_value: str, leg_count: int, quantity: int,
         safe_price: Decimal | None = None, fair_proxy: Decimal | None = None, fair_gap: Decimal | None = None,
         locked_per_contract: Decimal | None = None, fillability: float | None = None,
         expected_dollars_per_hour: Decimal | None = None, capital_reserve: Decimal | None = None,
         details: Mapping[str, Any] | None = None) -> bool:
    if not _dsn() or psycopg is None: return False
    row = (
        int(time.time()*1000), str(run_id), str(event_type), str(combo_key), str(signature_value), int(leg_count), int(quantity),
        str(safe_price) if safe_price is not None else None,
        str(fair_proxy) if fair_proxy is not None else None,
        str(fair_gap) if fair_gap is not None else None,
        str(locked_per_contract) if locked_per_contract is not None else None,
        float(fillability) if fillability is not None else None,
        str(expected_dollars_per_hour) if expected_dollars_per_hour is not None else None,
        str(capital_reserve) if capital_reserve is not None else None,
        json.dumps(dict(details or {}),separators=(",",":"),default=str),
    )
    sql=f"""INSERT INTO {TABLE}
      (ts_ms,run_id,event_type,combo_key,signature,leg_count,quantity,safe_price,fair_proxy,fair_gap,
       locked_per_contract,fillability,expected_dollars_per_hour,capital_reserve,details)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"""
    with connection() as conn:
        if conn is None: return False
        try:
            with conn.cursor() as cur: cur.execute(sql,row)
            return True
        except Exception:
            return False


def historical_fill_rate(signature_value: str, *, lookback_days: int = 30) -> tuple[float | None,int,int]:
    """Return fill/(fill+cancel) from actual maker outcomes, if sample exists."""
    cutoff=int((time.time()-max(1,lookback_days)*86400)*1000)
    sql=f"""SELECT event_type, count(*) FROM {TABLE}
             WHERE signature=%s AND ts_ms>=%s AND event_type IN ('maker_fill','maker_cancel')
             GROUP BY event_type"""
    with connection() as conn:
        if conn is None: return None,0,0
        try:
            with conn.cursor() as cur:
                cur.execute(sql,(signature_value,cutoff)); rows=cur.fetchall()
        except Exception:
            return None,0,0
    counts={str(k):int(v) for k,v in rows}
    fills=counts.get("maker_fill",0); cancels=counts.get("maker_cancel",0); n=fills+cancels
    if n==0:return None,0,0
    # Beta(1,4) prior keeps sparse one-off fills from dominating ranking.
    rate=(fills+1)/(n+5)
    return rate,fills,cancels
