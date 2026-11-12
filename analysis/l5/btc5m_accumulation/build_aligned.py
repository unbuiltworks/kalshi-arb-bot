"""Build the synchronized BTC5m decision dataset. READ-ONLY, offline.

ONE ROW PER RECORDED QUOTE, carrying everything a decision needed at the
moment it was made:

  market and window id ......... `market`, `window_start_utc`, `window_end_utc`
  UP/DOWN bid and ask .......... `up`, `down` (own-price convention)
  available depth .............. `up.depth` / `down.depth`, and WHY it is null
  BTC reference price .......... `index.a` (BRTI, 60-second average)
  strike ....................... `strike` (the venue's own priceToBeat)
  time remaining ............... `seconds_remaining`, and the model's `tau`
  independent probability ...... `p_up`, with `p_up.model_version`
  source timestamps / freshness  `quote_utc`, `index.bucket_t`,
                                 `index.age_s`, `p_up.reason`
  actual fills ................. `fills`, with `fills_reason`
  settlement ................... `settled_outcome`

STRICTLY CAUSAL. The probability at a row is computed from index buckets
that had ALREADY ENDED at that row's own quote timestamp -- the series is
truncated to `t + 1 <= quote_epoch` before `probability_up` is called, and
that function independently refuses anything older than MAX_AGE_S. No row
can see its own future, and no missing index observation is invented: a
row whose probability cannot be established carries `p_up.ok = False` and
the named reason, which is what the live caller would have had.

DEPTH IS GENUINELY ABSENT. The venue's PUBLIC market endpoint publishes
`bestBidQuote` / `bestAskQuote` and a `marketSides` list of prices. It
publishes NO SIZE at any level (verified against a live market,
2026-11-12). Depth is therefore recorded as null with a reason rather than
guessed; obtaining it needs the AUTHENTICATED book (`l5_pmus_auth_book`),
which this read-only path deliberately does not touch.

FILLS ARE GENUINELY ABSENT. No order of this strategy has ever filled, so
every row's `fills` is empty with that stated. The field exists so the
recorder can populate it once there is anything to populate it with.

Inputs (both committed beside this script):
  pmus_btc5m_quotes_20261112.jsonl    the public quote tape
  btc_index_1s_20261112.jsonl.gz      the venue's 1-second BRTI average

Usage: python3 -I analysis/l5/btc5m_accumulation/build_aligned.py [OUT.jsonl.gz]
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt
import gzip
import json
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())

from src import l5_btc_reference as REF        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
QUOTES = os.path.join(HERE, "pmus_btc5m_quotes_20261112.jsonl")
INDEX = os.path.join(HERE, "btc_index_1s_20261112.jsonl.gz")
OUT = os.path.join(HERE, "btc5m_aligned_20261112.jsonl.gz")

WINDOW_S = 300.0
UTC = dt.timezone.utc

DEPTH_REASON = (
    "the venue's PUBLIC market endpoint publishes no size at any level "
    "(bestBidQuote/bestAskQuote and marketSides carry prices only); depth "
    "needs the authenticated book, which this read-only path does not touch")
FILLS_REASON = (
    "no order of this strategy has ever filled; this field is empty because "
    "there is nothing to put in it, not because it was not looked for")


def model_version() -> str:
    """Identify the probability model by the exact content of its module."""
    try:
        h = subprocess.run(["git", "hash-object", "src/l5_btc_reference.py"],
                           capture_output=True, text=True,
                           check=True).stdout.strip()[:12]
    except Exception:                                          # noqa: BLE001
        h = "unknown"
    return ("l5_btc_reference@%s;source=%s;averaging_s=%d;vol_inflation=%s;"
            "sigma_rel_floor=%s;max_index_age_s=%s"
            % (h, "VENUE_INDEX_BRTI_60S", REF.AVERAGING_S, REF.VOL_INFLATION,
               REF.SIGMA_REL_FLOOR, REF.MAX_AGE_S))


def load_index():
    ts, vals = [], []
    with gzip.open(INDEX, "rt") as fh:
        for line in fh:
            d = json.loads(line)
            ts.append(int(d["t"]))
            vals.append(float(d["a"]))
    return ts, vals


def load_quotes():
    w = collections.defaultdict(list)
    with open(QUOTES, encoding="utf-8") as fh:
        for line in fh:
            q = json.loads(line)
            w[q["slug"]].append(q)
    for rows in w.values():
        rows.sort(key=lambda r: r["t"])
    return w


def window_start_epoch(slug: str) -> float:
    return dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz").replace(
        tzinfo=UTC).timestamp()


def num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def terminal_outcome(rows):
    """UP / DOWN / None, from the venue's own terminal quote."""
    for q in reversed(rows):
        if q["t"] < 240.0:
            break
        bid, ask = num(q.get("bid")), num(q.get("ask"))
        if bid is not None and bid >= 0.90:
            return "UP"
        if ask is not None and ask <= 0.10:
            return "DOWN"
    return None


def main(out_path=OUT):
    idx_t, idx_v = load_index()
    quotes = load_quotes()
    mv = model_version()
    n_rows = n_ok = 0
    reasons = collections.Counter()
    per_window = {}

    with gzip.open(out_path, "wt") as out:
        for slug in sorted(quotes):
            rows = quotes[slug]
            ws = window_start_epoch(slug)
            we = ws + WINDOW_S
            outcome = terminal_outcome(rows)
            ok_here = 0
            for q in rows:
                try:
                    now = dt.datetime.fromisoformat(
                        q["utc"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    now = ws + float(q["t"])
                # STRICTLY CAUSAL: only buckets that had ended by `now`
                hi = bisect.bisect_right(idx_t, now - 1.0)
                lo = max(0, hi - (REF.KEEP_S + 1))
                series = list(zip(idx_t[lo:hi], idx_v[lo:hi]))
                strike = num(q.get("to_beat"))
                p = REF.probability_up(
                    series=series, strike=(strike if strike else float("nan")),
                    window_end=we, now=now, received_t=now)
                bid, ask = num(q.get("bid")), num(q.get("ask"))
                row = {
                    "schema": "btc5m_aligned/1",
                    "market": slug,
                    "window_start_utc": dt.datetime.fromtimestamp(
                        ws, UTC).isoformat(),
                    "window_end_utc": dt.datetime.fromtimestamp(
                        we, UTC).isoformat(),
                    "t_in_window_s": q["t"],
                    "seconds_remaining": round(WINDOW_S - float(q["t"]), 2),
                    "quote_utc": q.get("utc"),
                    "up": {"bid": bid, "ask": ask, "depth": None},
                    "down": {"bid": (None if ask is None else round(1 - ask, 4)),
                             "ask": (None if bid is None else round(1 - bid, 4)),
                             "depth": None},
                    "depth_unavailable_reason": DEPTH_REASON,
                    "strike": strike,
                    "index": {
                        "a": p.get("index"),
                        "bucket_t": p.get("bucket_t"),
                        "age_s": (None if p.get("index_age_s") is None
                                  else round(p["index_age_s"], 3)),
                        "symbol": REF.INDEX_SYMBOL,
                        "averaging_s": REF.AVERAGING_S,
                    },
                    "p_up": {
                        "ok": bool(p.get("ok")),
                        "value": p.get("p_up"),
                        "reason": p.get("reason"),
                        "model_version": mv,
                        "tau_s": p.get("seconds_left"),
                        "expected_settlement": p.get("expected_settlement"),
                        "sd": p.get("sd"),
                        "z": p.get("z"),
                        "sigma_per_second": p.get("sigma_per_second"),
                        "distance_to_strike": p.get("distance"),
                    },
                    "closed": q.get("closed"),
                    "settled_outcome": outcome,
                    "fills": [],
                    "fills_reason": FILLS_REASON,
                }
                out.write(json.dumps(row, sort_keys=True) + "\n")
                n_rows += 1
                if p.get("ok"):
                    n_ok += 1
                    ok_here += 1
                else:
                    reasons[p.get("reason")] += 1
            per_window[slug] = {"rows": len(rows), "p_ok": ok_here,
                                "outcome": outcome}

    windows = len(per_window)
    resolved = sum(1 for v in per_window.values() if v["outcome"])
    usable = sum(1 for v in per_window.values()
                 if v["outcome"] and v["p_ok"] > 0)
    print("ALIGNED DATASET  %s" % out_path)
    print("  size              %.2f MB" % (os.path.getsize(out_path) / 1e6))
    print("  rows              %d" % n_rows)
    print("  windows           %d" % windows)
    print("  windows resolved  %d" % resolved)
    print("  windows with p_up %d" % usable)
    print("  rows with p_up    %d (%.1f%%)"
          % (n_ok, 100.0 * n_ok / max(1, n_rows)))
    print("  model             %s" % mv)
    if reasons:
        print("  rows WITHOUT p_up, by reason:")
        for r, n in reasons.most_common():
            print("     %-34s %d" % (r, n))
    print("  depth             null on every row -- %s" % DEPTH_REASON[:60])
    print("  fills             empty on every row -- never filled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
