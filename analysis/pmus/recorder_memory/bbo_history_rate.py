#!/usr/bin/env python3
"""The pre-registered measurement: how long does 2000 bbo_history entries LAST?

OWNER: "run the proposed read-only bbo_history measurement and pre-register
the 3900-second retention decision."

READ-ONLY. Consumes a Railway log dump the MCP tool already wrote to disk.
No network call, no environment read, no production contact, no deployment.

WHY THIS SOURCE. The recorder's per-market `ws_book_updates` counter is served
in the status document, but the copy in the log stream truncates at 81,920
bytes so its tail markets are unreadable. `PMUS_WS_XCHECK` rows carry the SAME
counter for a handful of markets at a time and do not truncate, so differencing
the counter for one market across two rows gives its book-update RATE directly.

WHAT THE RATE DECIDES. Retention is COUNT-based (`del s["bbo_history"][:-2000]`)
while every consumer is TIME-based, the widest window being
`window_s + SLOT_S = 3600 + 300 = 3900 s`. Adding an age bound alongside the
count bound only frees memory for markets where 2000 entries span MORE than
3900 s. Where 2000 entries span LESS, the count bound already binds and the
age bound is a no-op.

    span_s = 2000 / rate      age bound helps  <=>  span_s > 3900 s
                                              <=>  rate < 0.513 updates/s

SAMPLING BIAS, STATED UP FRONT BECAUSE IT DECIDES THE VERDICT. XCHECK
cross-checks the keyless gateway against the authenticated socket for markets
the recorder is SUBSCRIBED to. It therefore samples the 160 SEATED markets and
never the ~1,340 unseated COLD entries that are the bulk of `mstate`. This
measurement is authoritative for seated markets and establishes NOTHING about
cold ones.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent / "bbo_history_rate.json"

BBO_COUNT_BOUND = 2000
WIDEST_CONSUMER_WINDOW_S = 3900.0        # ws_gate / summarize_state: 3600+300
MIN_SEPARATION_S = 60.0                  # below this the counter delta is noise


def pctl(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


def parse(paths):
    obs = defaultdict(list)
    rows = 0
    for path in paths:
        d = json.loads(Path(path).read_text())
        for r in d.get("deploy") or []:
            msg = r.get("message") or ""
            i = msg.find("{")
            if i < 0:
                continue
            try:
                j = json.loads(msg[i:])
            except Exception:                            # noqa: BLE001
                continue
            t = j.get("t")
            if not t or not j.get("rows"):
                continue
            try:
                ts = datetime.fromisoformat(
                    str(t).replace("Z", "+00:00")).timestamp()
            except Exception:                            # noqa: BLE001
                continue
            rows += 1
            for m in j["rows"]:
                if m.get("ws_book_updates") is None:
                    continue
                obs[m["slug"]].append((ts, int(m["ws_book_updates"]),
                                       m.get("ws_trades_total")))
    return obs, rows


def main(argv=None) -> int:
    paths = list(argv or sys.argv[1:])
    if not paths:
        print("usage: bbo_history_rate.py <railway-log-dump.json> ...")
        return 2
    obs, xrows = parse(paths)

    measured = []
    for slug, v in obs.items():
        v.sort()
        if len(v) < 2:
            continue
        dt = v[-1][0] - v[0][0]
        db = v[-1][1] - v[0][1]
        if dt < MIN_SEPARATION_S or db < 0:
            continue
        rate = db / dt
        span = (BBO_COUNT_BOUND / rate) if rate > 0 else float("inf")
        measured.append({
            "market": slug, "observations": len(v), "separation_s": round(dt, 1),
            "book_updates_delta": db,
            "updates_per_s": round(rate, 4),
            "seconds_2000_entries_span": round(span, 1),
            "lifetime_book_updates": v[-1][1],
            "history_saturated_at_2000": v[-1][1] >= BBO_COUNT_BOUND,
            "age_bound_would_free_memory": span > WIDEST_CONSUMER_WINDOW_S})

    rates = [m["updates_per_s"] for m in measured]
    spans = [m["seconds_2000_entries_span"] for m in measured]
    helped = [m for m in measured if m["age_bound_would_free_memory"]]

    out = {
        "read_only": True,
        "source": {"log_dumps": [str(p) for p in paths],
                   "xcheck_rows_parsed": xrows,
                   "distinct_markets_seen": len(obs),
                   "markets_with_usable_separation": len(measured)},
        "bounds": {"bbo_history_count_bound": BBO_COUNT_BOUND,
                   "widest_consumer_window_s": WIDEST_CONSUMER_WINDOW_S,
                   "rate_below_which_an_age_bound_helps":
                       round(BBO_COUNT_BOUND / WIDEST_CONSUMER_WINDOW_S, 4)},
        "book_updates_per_second": {
            "p10": pctl(rates, .10), "p25": pctl(rates, .25),
            "p50": pctl(rates, .50), "p75": pctl(rates, .75),
            "p90": pctl(rates, .90), "max": max(rates) if rates else None},
        "seconds_2000_entries_span": {
            "p10": pctl(spans, .10), "p25": pctl(spans, .25),
            "p50": pctl(spans, .50), "p75": pctl(spans, .75),
            "p90": pctl(spans, .90), "max": max(spans) if spans else None},
        "age_bound_would_free_memory": {
            "markets": len(helped), "of": len(measured),
            "share": round(len(helped) / len(measured), 4) if measured else None},
        "per_market": measured,
        "SAMPLING_BIAS": ("XCHECK samples SUBSCRIBED markets only. This "
                          "measures the ~160 seated markets and establishes "
                          "NOTHING about the ~1,340 unseated COLD entries "
                          "that hold most of mstate."),
        "cold_market_rate": "UNKNOWN -- no read-only path to their counters",
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")

    print(f"xcheck rows {xrows}, distinct markets {len(obs)}, "
          f"measurable {len(measured)}")
    print(f"updates/s   p25={pctl(rates,.25)} p50={pctl(rates,.5)} "
          f"p75={pctl(rates,.75)}")
    print(f"2000 entries span (s)  p25={pctl(spans,.25)} p50={pctl(spans,.5)} "
          f"p75={pctl(spans,.75)}")
    print(f"age bound would free memory in {len(helped)}/{len(measured)} "
          f"markets")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
