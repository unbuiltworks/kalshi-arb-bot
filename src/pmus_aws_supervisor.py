"""Always-on PMUS L5 transfer supervisor for the AWS hot host.

The supervisor removes GitHub/SSM from the opportunity -> order hot path.
It is deliberately DISARMED by default and is safe to run 24/7 in watch
mode. Live mode requires a separate dual-key supervisor arm; even then it
can run only one q=1 campaign at a time and delegates all order/economic
rules to :mod:`src.pmus_q1_executor_v4`.

Input is the authenticated read-only recorder's local stdout log. The AWS
systemd unit writes that output to a persistent file, so the supervisor
never needs Railway or GitHub to discover a gate clear.

Hard operating rules:
* no market when recorder evidence is stale
* no market when ANY open order or ANY non-zero account position exists
* only Q1_GATE_CLEARED, two-sided, 20-80c candidates
* one campaign at a time, q=1 inherited from executor v4
* a fill/residual blocks all new first legs until account reconciliation
* no automatic size/concurrency increase
* realized P&L is never inferred here
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_q1_executor_v4 as q1

ROOT_ENV = "PMUS_SUPERVISOR_ROOT"
DEFAULT_ROOT = "/state/pmus-supervisor"
RECORDER_LOG_ENV = "PMUS_SUPERVISOR_RECORDER_LOG"
DEFAULT_RECORDER_LOG = "/state/pmus-ws/recorder.log"
LOOP_ENV = "PMUS_SUPERVISOR_LOOP_SECONDS"
STALE_ENV = "PMUS_SUPERVISOR_MAX_STATUS_AGE_SECONDS"

LIVE_ENV = "PMUS_SUPERVISOR_LIVE"
SID_ENV = "PMUS_SUPERVISOR_SESSION_ID"
ARM_ENV = "PMUS_SUPERVISOR_ARM"

DEFAULT_LOOP_S = 2.0
DEFAULT_MAX_AGE_S = 90.0
COOLDOWN_NO_FILL_S = 20.0


def root_dir() -> Path:
    return Path(os.getenv(ROOT_ENV, DEFAULT_ROOT))


def _atomic_json(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (os.getenv(LIVE_ENV, "").strip().upper() == "YES" and bool(sid)
            and os.getenv(ARM_ENV, "").strip() == sid)


def _tail(path: Path, max_bytes: int = 256 * 1024) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        return f.read().decode("utf-8", errors="replace")


def latest_recorder_status(path: Optional[Path] = None) -> Optional[dict]:
    path = path or Path(os.getenv(RECORDER_LOG_ENV, DEFAULT_RECORDER_LOG))
    for line in reversed(_tail(path).splitlines()):
        marker = "PMUS_WS_STATUS "
        pos = line.find(marker)
        if pos < 0:
            continue
        try:
            obj = json.loads(line[pos + len(marker):])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def status_age_seconds(status: Mapping[str, Any]) -> Optional[float]:
    dt = _parse_time(status.get("t"))
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _dec(value: Any) -> Optional[D]:
    try:
        return D(str(value)) if value is not None else None
    except Exception:
        return None


def eligible_candidates(status: Mapping[str, Any]) -> list[dict]:
    # Prefer the recorder's full-slate cleared list; a valid candidate must
    # never be invisible just because it sits outside the five display rows.
    # Older status lines without the field fall back to top_markets.
    rows = status.get("eligible_markets")
    if not isinstance(rows, list):
        rows = status.get("top_markets")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("gate_status") != "Q1_GATE_CLEARED":
            continue
        if not row.get("both_sides_served"):
            continue
        bid = _dec(row.get("bid")); ask = _dec(row.get("ask"))
        if bid is None or ask is None or not q1.entry_ok(bid) or ask < bid:
            continue
        counts = row.get("slot_counts_theoretical") or {}
        matched = int(counts.get("MATCHED") or 0) if isinstance(counts, Mapping) else 0
        if matched < 2:
            continue
        bid_qty = _dec(row.get("bid_qty")) or D("0")
        ask_qty = _dec(row.get("ask_qty")) or D("0")
        spread = ask - bid
        trades = int(row.get("trades_1h") or 0)
        # Ranking only chooses among already-cleared markets. Tight spread,
        # repeated completion evidence, flow and q1 depth are preferred.
        score = (D(matched) * D("100") + D(min(trades, 500)) / D("10")
                 + min(bid_qty, ask_qty, D("100")) / D("100")
                 - spread * D("1000"))
        out.append({**dict(row), "score": str(score)})
    out.sort(key=lambda r: D(r["score"]), reverse=True)
    return out


def account_block_reason(t: q1.Transport) -> Optional[str]:
    """Authoritative pre-campaign reconciliation.

    These are read-only REST calls and can transiently return 404/429/5xx.
    Reuse the audited bounded GET retry path; mutations are never replayed.
    This function is intentionally called only when LIVE and immediately
    before a campaign, not on every 2-second DISARMED watch tick.
    """
    opened = q1.base._get_with_retry(t, "/v1/orders/open")
    orders = opened.get("orders") if isinstance(opened, Mapping) else None
    if isinstance(orders, list) and orders:
        return f"open_orders:{len(orders)}"

    resp = q1.base._get_with_retry(t, "/v1/portfolio/positions")
    rows = resp.get("positions") if isinstance(resp, Mapping) else None
    nonzero = []
    if isinstance(rows, Mapping):
        iterable = rows.items()
    elif isinstance(rows, list):
        iterable = ((str(r.get("marketSlug") or ""), r)
                    for r in rows if isinstance(r, Mapping))
    else:
        iterable = []
    for slug, row in iterable:
        if not isinstance(row, Mapping):
            continue
        raw = row.get("netPositionDecimal")
        if raw is None:
            raw = row.get("netPosition")
        if raw is None:
            raw = row.get("quantity")
        try:
            value = D(str(raw or 0))
        except Exception:
            return "position_unparseable"
        if value != 0:
            nonzero.append((slug, str(value)))
    if nonzero:
        return "nonzero_positions:" + ",".join(f"{s}={v}" for s, v in nonzero[:5])
    return None


def _load_state() -> dict:
    p = root_dir() / "state.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text())
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_state(state: Mapping[str, Any]) -> None:
    _atomic_json(root_dir() / "state.json", state)


def _heartbeat(**fields: Any) -> None:
    _atomic_json(root_dir() / "heartbeat.json", {
        "t": _now_iso(),
        "mode": "LIVE_Q1" if armed() else "DISARMED_WATCH",
        **fields,
    })


def run_once(t: Optional[q1.Transport] = None) -> dict:
    t = t or q1.Transport()
    status = latest_recorder_status()
    if status is None:
        return {"action": "WAIT", "reason": "no_recorder_status"}
    age = status_age_seconds(status)
    max_age = float(os.getenv(STALE_ENV, str(DEFAULT_MAX_AGE_S)))
    if age is None or age > max_age:
        return {"action": "WAIT", "reason": "stale_recorder_status",
                "age_s": age}
    if status.get("conn") != "connected" or status.get("auth") != "ok":
        return {"action": "WAIT", "reason": "recorder_not_healthy"}

    candidates = eligible_candidates(status)
    if not candidates:
        return {"action": "WAIT", "reason": "no_q1_gate_clear",
                "status_age_s": age}

    chosen = candidates[0]
    # DISARMED watch is market-data-only. Do not burn authenticated REST
    # limits every two seconds just to say WOULD_TRADE. LIVE mode performs
    # the authoritative account reconciliation immediately before campaign.
    if not armed():
        return {"action": "WOULD_TRADE", "reason": "supervisor_disarmed",
                "candidate": chosen.get("slug"), "bid": chosen.get("bid"),
                "ask": chosen.get("ask"), "score": chosen.get("score")}

    try:
        block = account_block_reason(t)
    except Exception as exc:
        return {"action": "WAIT", "reason":
                f"account_reconcile_error:{type(exc).__name__}"}
    if block:
        return {"action": "BLOCK", "reason": block,
                "candidate": chosen.get("slug")}

    slug = str(chosen["slug"])
    campaign_sid = f"aws24-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    # Supervisor arm authorizes the loop; each campaign still gets a fresh
    # executor dual key and an exact live-gate assertion.
    prior = {k: os.environ.get(k) for k in (
        q1.LIVE_ENV, q1.SID_ENV, q1.ARM_ENV, q1.MARKET_ENV, q1.GATE_ENV)}
    try:
        os.environ[q1.LIVE_ENV] = "YES"
        os.environ[q1.SID_ENV] = campaign_sid
        os.environ[q1.ARM_ENV] = campaign_sid
        os.environ[q1.MARKET_ENV] = slug
        os.environ[q1.GATE_ENV] = slug
        result = q1.run_campaign(t)
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {"action": "CAMPAIGN_COMPLETE", "campaign_id": campaign_sid,
            "candidate": slug, "result": result}


def run_forever() -> int:
    root_dir().mkdir(parents=True, exist_ok=True)
    state = _load_state()
    loop_s = float(os.getenv(LOOP_ENV, str(DEFAULT_LOOP_S)))
    next_allowed = float(state.get("next_allowed_monotonic") or 0.0)
    while True:
        if time.monotonic() < next_allowed:
            _heartbeat(action="COOLDOWN")
            time.sleep(loop_s)
            continue
        try:
            result = run_once()
        except Exception as exc:
            result = {"action": "WAIT",
                      "reason": f"supervisor_exception:{type(exc).__name__}"}
        _heartbeat(**result)
        if result.get("action") == "CAMPAIGN_COMPLETE":
            r = result.get("result") or {}
            state["last_campaign"] = result
            state["last_campaign_t"] = _now_iso()
            _save_state(state)
            # No-fill can recycle after a short operational cooldown. Any
            # fill/residual will be caught by account reconciliation and block.
            if r.get("status") in ("NO_FILL", "REJECTED_NO_FILL",
                                    "NO_FILL_SAFE_REFUSAL"):
                next_allowed = time.monotonic() + COOLDOWN_NO_FILL_S
        time.sleep(loop_s)


def main() -> int:
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
