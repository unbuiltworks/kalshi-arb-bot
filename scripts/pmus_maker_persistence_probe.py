"""P3. Which maker behaviour does the type-3 trade tape actually support?

The public L5 fill cache has 1-second timestamps and no maker/taker flag, so
it could not separate a maker ladder from a taker sweep.
`SUBSCRIPTION_TYPE_TRADE` on /v1/ws/markets carries `maker.side`,
`taker.side` and MICROSECOND `tradeTime`, which is the right instrument.

WHAT IT CAN AND CANNOT SHOW. This observes the WHOLE market, not L5. We
cannot attribute a trade to a specific participant from this feed. So the
question it answers is narrower and honest: at microsecond resolution, does
the resting side of this book behave like one persistent level, a
replenished level, a repriced level, or several simultaneous levels? A
finding here is about BTC-15m maker behaviour in general, and is evidence
ABOUT the hypothesis rather than proof about L5.

Read-only: subscribe/unsubscribe only. No private stream, no money path.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_ws_evidence_recorder as R   # noqa: E402

ALLOWED_KEYS = {"subscribe", "unsubscribe"}
#: a burst of trades at ONE price within this window is treated as one sweep
SWEEP_S = 0.050


def guard(frame):
    bad = set(frame) - ALLOWED_KEYS
    if bad:
        raise RuntimeError("blocked frame keys: %s" % (sorted(bad),))
    return frame


def classify(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group trades into maker-side events and name what each implies."""
    by_maker = collections.defaultdict(list)
    for t in trades:
        by_maker[t["maker_side"]].append(t)
    out: Dict[str, Any] = {}
    for mside, rows in by_maker.items():
        rows.sort(key=lambda r: r["t"])
        groups, cur = [], []
        for r in rows:
            if cur and (r["t"] - cur[-1]["t"] <= SWEEP_S):
                cur.append(r)
            else:
                if cur:
                    groups.append(cur)
                cur = [r]
        if cur:
            groups.append(cur)
        one_level = multi_level = 0
        for g in groups:
            if len({r["px"] for r in g}) > 1:
                multi_level += 1
            else:
                one_level += 1
        # consecutive GROUPS at the same price = the level was replenished;
        # at a different price = it was repriced
        repl = repr_ = 0
        for a, b in zip(groups, groups[1:]):
            pa = {r["px"] for r in a}
            pb = {r["px"] for r in b}
            if pa & pb:
                repl += 1
            else:
                repr_ += 1
        out[mside] = {
            "trades": len(rows), "events": len(groups),
            "single_price_events": one_level,
            "MULTI_PRICE_events_same_instant": multi_level,
            "multi_level_share": (round(multi_level / len(groups), 4)
                                  if groups else None),
            "consecutive_same_price_REPLENISH": repl,
            "consecutive_diff_price_REPRICE": repr_,
            "replenish_share": (round(repl / (repl + repr_), 4)
                                if (repl + repr_) else None),
        }
    return out


async def run(slug: str, seconds: float) -> Dict[str, Any]:
    import websockets
    headers = R.auth_headers(os.environ[R.KEY_ENV], os.environ[R.SECRET_ENV],
                             path=R.WS_PATH)
    trades: List[Dict[str, Any]] = []
    async with websockets.connect(R.WS_URL, additional_headers=headers,
                                  max_size=8 * 1024 * 1024,
                                  open_timeout=15) as ws:
        await ws.send(json.dumps(guard({"subscribe": {
            "request_id": "tr-" + uuid.uuid4().hex[:8],
            "subscription_type": R.SUB_TRADE, "market_slugs": [slug]}})))
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except Exception:                                  # noqa: BLE001
                break
            try:
                m = json.loads(raw)
            except Exception:                                  # noqa: BLE001
                continue
            tr = m.get("trade") if isinstance(m, dict) else None
            if not isinstance(tr, dict):
                continue
            px = tr.get("price")
            px = px.get("value") if isinstance(px, dict) else px
            qty = tr.get("quantity")
            qty = qty.get("value") if isinstance(qty, dict) else qty
            tt = str(tr.get("tradeTime") or "")
            # microsecond stamp -> float seconds
            try:
                import datetime as dt
                ts = dt.datetime.fromisoformat(
                    tt.replace("Z", "+00:00")).timestamp()
            except Exception:                                  # noqa: BLE001
                ts = time.time()
            trades.append({
                "t": ts, "px": str(px), "qty": str(qty),
                "maker_side": str((tr.get("maker") or {}).get("side")),
                "taker_side": str((tr.get("taker") or {}).get("side")),
                "id": tr.get("id")})
    return {"LABEL": "PMUS_MAKER_PERSISTENCE_PROBE", "slug": slug,
            "read_only": True, "seconds": seconds,
            "trades": len(trades),
            "distinct_microsecond_stamps": len({t["t"] for t in trades}),
            "by_maker_side": classify(trades),
            "labels": ("market-wide, NOT attributed to any participant. "
                       "Evidence ABOUT maker behaviour on BTC-15m, not proof "
                       "about L5. A multi-price event at one instant means "
                       "the resting side held several levels simultaneously.")}


async def main() -> int:
    slug = os.getenv("PMUS_TAPE_SLUG", "").strip()
    secs = float(os.getenv("PMUS_TAPE_SECONDS", "120") or 120)
    if not R.has_credentials() or not slug:
        print("PMUS_TAPE " + json.dumps(
            {"error": "NO_CREDENTIALS" if not R.has_credentials()
             else "NO_SLUG"}), flush=True)
        return 2
    rep = await run(slug, secs)
    print("PMUS_TAPE " + json.dumps(rep, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
