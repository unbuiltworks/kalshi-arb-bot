#!/usr/bin/env python3
"""Companion REST trade collector for the Gemini BTC15m shadow.

WHY THIS EXISTS, and why it is a separate file.

The L2 recorder (scripts/gemini_btc15m_shadow_record.py) subscribes to the
v2 marketdata `l2` channel. That channel delivers book deltas but NOT
executions: over a 694-second span the venue printed 1 trade on
GEMI-BTC15M2610020230-UP and the WS tape captured 0, with a frame census of
l2_updates + heartbeat only and the `trades` array non-empty on none of them.

Since the simulator fills ONLY on defensible trade-through evidence, a tape
with no trades yields zero fills by construction, forever. That is a
measurement artifact, not a result about the strategy.

The recorder and the simulator are deliberately NOT modified. This polls
/v1/trades per live symbol and writes its own tape; scripts/gemini_tape_merge.py
then emits a single tape in the recorder's own schema, so the simulator runs
against it unchanged.

READ-ONLY. Public endpoint. Dedupes by tid, so a poll interval shorter than
the trade rate costs nothing but bandwidth.
"""
from __future__ import annotations

import json, os, sys, time, urllib.request
from datetime import datetime, timezone

EVENTS = "https://api.gemini.com/v1/prediction-markets/events?limit=200"
TRADES = "https://api.gemini.com/v1/trades/{sym}?limit_trades=500"
OUT_DIR = "data/gemini_shadow"
POLL_S = 4.0
REFRESH_S = 90


def g(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        u, headers={"Accept": "application/json"}), timeout=15))


def live_symbols(prefix):
    try:
        d = g(EVENTS)
    except Exception:
        return []
    out = []
    for e in d.get("data") or []:
        if not str(e.get("ticker", "")).startswith(prefix) or e.get("status") != "active":
            continue
        for c in e.get("contracts") or []:
            if c.get("instrumentSymbol"):
                out.append(c["instrumentSymbol"])
    return sorted(set(out))


def main(seconds, prefix):
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = datetime.now(timezone.utc)
    path = os.path.join(OUT_DIR, f"trades_{prefix}_{t0.strftime('%Y%m%dT%H%M%SZ')}.jsonl")
    seen = set()
    syms = live_symbols(prefix)
    last_refresh = time.time()
    deadline = time.time() + seconds
    n = 0
    with open(path, "w") as fh:
        fh.write(json.dumps({"kind": "header", "started_utc": t0.isoformat(),
                             "source": "GET /v1/trades/{symbol}", "prefix": prefix,
                             "read_only": True, "poll_s": POLL_S}) + "\n")
        while time.time() < deadline:
            for s in syms:
                try:
                    for t in g(TRADES.format(sym=s)):
                        tid = t.get("tid")
                        if tid in seen:
                            continue
                        seen.add(tid)
                        fh.write(json.dumps({"kind": "trade", "symbol": s,
                                             "seen_utc": datetime.now(timezone.utc).isoformat(),
                                             "t": t}) + "\n")
                        n += 1
                except Exception:
                    pass
                time.sleep(0.35)
            fh.flush()
            if time.time() - last_refresh > REFRESH_S:
                last_refresh = time.time()
                syms = live_symbols(prefix) or syms
            time.sleep(POLL_S)
    print(f"{path} trades={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3600,
                  sys.argv[2] if len(sys.argv) > 2 else "BTC15M"))
