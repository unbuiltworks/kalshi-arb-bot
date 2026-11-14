"""Test whether Polymarket/PMUS divergence carries information -- gated.

Reads paired_venue_quotes.jsonl and runs, IN ORDER:

GATE 1, FRESHNESS. Polymarket is observed here through a TRADES feed,
so its price is a last print, not a quote. A print is usable only while
it is recent. This reports the distribution of print age and the share
of polls with a print fresher than FRESH_S. If that share is small the
predictor class is not testable on this data source at all, and no
divergence number below should be read -- the correct response is to
obtain a quote feed, not to interpret stale prints.

GATE 2, POWER. Divergence can only be tested against settled outcomes,
one observation per window. The multiple-comparisons bar used on this
branch is sqrt(2 ln N) for N hypotheses examined; a two-hour collection
yields ~24 windows and cannot clear it. This reports the window count
and refuses to print a verdict below the minimum.

ONLY THEN, DIVERGENCE. For fresh polls, divergence = poly_up - pmus_mid,
tested against whether the window settled Up, conditioned on pmus_mid so
that the test measures information ADDED by the second venue rather than
re-measuring the PMUS price we already have.

Outcomes come from PMUS after close (status/settlementPrice vs
priceToBeat), which is the venue we would actually trade.

READ-ONLY. Reads a local file and settled public markets.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "paired_venue_quotes.jsonl")
PMUS = "https://gateway.polymarket.us/v1/market/slug/"
FRESH_S = 20.0
MIN_FRESH_SHARE = 0.25
MIN_WINDOWS = 120


def num(v):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load():
    rows = []
    if not os.path.exists(STORE):
        return rows
    with open(STORE, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    return rows


def enrich(rows):
    """Attach poll time, pmus mid, poly up price, and print age."""
    out = []
    import datetime as dt
    for r in rows:
        try:
            t = dt.datetime.fromisoformat(r["utc"])
        except (KeyError, ValueError):
            continue
        bid, ask = num(r.get("pmus_bid")), num(r.get("pmus_ask"))
        if bid is None or ask is None:
            continue
        up = (r.get("poly_last") or {}).get("UP") or {}
        p, ts = num(up.get("price")), up.get("ts")
        age = None
        if ts:
            try:
                age = t.timestamp() - float(ts)
            except (TypeError, ValueError):
                age = None
        out.append({"slug": r.get("pmus_slug"), "t_in": r.get("t_in_window_s"),
                    "mid": (bid + ask) / 2.0, "poly_up": p, "age": age})
    return out


def settle(slug):
    try:
        res = subprocess.run(["curl", "-sS", PMUS + slug, "--max-time", "20"],
                             capture_output=True, text=True, timeout=30)
        d = json.loads(res.stdout)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None
    mk = d.get("market", d) if isinstance(d, dict) else {}
    terms = mk.get("assetPriceTerms") or {}
    ptb, sp = num(terms.get("priceToBeat")), num(terms.get("settlementPrice"))
    if ptb is None or sp is None:
        return None
    return 1 if sp >= ptb else 0


def main():
    rows = enrich(load())
    print("polls loaded: %d" % len(rows))
    if not rows:
        print("no data yet; let the collector run")
        return 0

    aged = [r for r in rows if r["age"] is not None]
    fresh = [r for r in aged if r["age"] <= FRESH_S]
    share = (len(fresh) / len(aged)) if aged else 0.0
    print("\n--- GATE 1, FRESHNESS -----------------------------------")
    if aged:
        ages = sorted(r["age"] for r in aged)
        print("  print age s: median %.0f  p25 %.0f  p75 %.0f  max %.0f"
              % (st.median(ages), ages[len(ages) // 4],
                 ages[3 * len(ages) // 4], ages[-1]))
    print("  polls with a print fresher than %.0fs: %d/%d = %.1f%%"
          % (FRESH_S, len(fresh), len(aged), 100.0 * share))
    if share < MIN_FRESH_SHARE:
        print("  GATE 1 FAILS. Polymarket's print is usually stale at the")
        print("  moment we would act, so divergence measured here would be")
        print("  mostly staleness. This predictor class needs a QUOTE feed")
        print("  (clob.polymarket.com/book). Not testable on trades alone.")
        return 0
    print("  gate 1 passes")

    wins = sorted({r["slug"] for r in fresh if r["slug"]})
    print("\n--- GATE 2, POWER ---------------------------------------")
    print("  settled-window observations available: %d (minimum %d)"
          % (len(wins), MIN_WINDOWS))
    if len(wins) < MIN_WINDOWS:
        print("  GATE 2 FAILS. Too few windows to distinguish a real effect")
        print("  from the winner's curse at the sqrt(2 ln N) bar this branch")
        print("  uses. Keep collecting; print no verdict.")
        return 0

    print("\n--- DIVERGENCE ------------------------------------------")
    obs = []
    for w in wins:
        o = settle(w)
        if o is None:
            continue
        ws = [r for r in fresh if r["slug"] == w]
        d = st.mean(r["poly_up"] - r["mid"] for r in ws)
        obs.append((d, st.mean(r["mid"] for r in ws), o))
    if len(obs) < MIN_WINDOWS:
        print("  only %d settled; keep collecting" % len(obs))
        return 0

    hi = [o for d, m, o in obs if d > 0]
    lo = [o for d, m, o in obs if d <= 0]
    print("  poly above pmus: %d windows, settled Up %.1f%%"
          % (len(hi), 100.0 * sum(hi) / max(1, len(hi))))
    print("  poly below pmus: %d windows, settled Up %.1f%%"
          % (len(lo), 100.0 * sum(lo) / max(1, len(lo))))
    resid = [(o - m) for d, m, o in obs]
    dv = [d for d, m, o in obs]
    if len(dv) > 2 and st.pstdev(dv) > 0 and st.pstdev(resid) > 0:
        mdv, mr = st.mean(dv), st.mean(resid)
        cov = sum((a - mdv) * (b - mr) for a, b in zip(dv, resid)) / len(dv)
        r = cov / (st.pstdev(dv) * st.pstdev(resid))
        t = r * math.sqrt(max(0, len(dv) - 2)) / math.sqrt(max(1e-12, 1 - r * r))
        print("  corr(divergence, outcome - pmus_mid) = %+.3f   t %+.2f  n %d"
              % (r, t, len(dv)))
        print("  this is the information ADDED by the second venue;")
        print("  a near-zero correlation means PMUS already prices it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
