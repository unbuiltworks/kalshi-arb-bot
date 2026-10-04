"""The DEVELOPMENT state table for STATE_CONDITIONAL_PAIR_V1.

Frozen by PREREGISTRATION_STATE_CONDITIONAL_V1.md. One row per (window,
second), carrying only information published at or before that second.

CAUSALITY IS ENFORCED, NOT INTENDED. The four rules in the preregistration are
asserted here:

  1. a 1-second BRTI bucket starting at s is used only when s <= t-1, because a
     bucket is not complete -- and so not published -- until its second ends;
  2. priceToBeat is withheld before ws+15s;
  3. book state is the latest update with timestamp <= t;
  4. the settlement label is attached AFTER every feature is computed, and the
     feature list is asserted not to contain it.

Rule 1 is the one that would be easiest to break silently and the most
damaging: the settlement statistic averages the final minute, so reading one
bucket early near the close leaks the answer directly.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D

from scripts.pmus_favorite_dca import load, eligible, window_start  # noqa
from src import pmus_brti as BR  # noqa
from src import pmus_fees as FEES  # noqa

ONE = D(1)
#: trailing window for realized vol, and for the flow/momentum variables
VOL_LOOKBACK_S = 300
MOM_LOOKBACK_S = 60
#: priceToBeat is withheld until this many seconds into the window
PTB_DELAY_S = 15
STAT_N = BR.STAT_N

#: the feature list the model is allowed to see, frozen here
FEATURES = ("z", "locked_frac", "mom_60s")
LABEL = "outcome_up"


def brti_cached(slug: str, a: int, b: int, cache: str) -> dict:
    os.makedirs(cache, exist_ok=True)
    p = os.path.join(cache, "%s.json" % slug)
    if os.path.exists(p):
        with open(p) as fh:
            return {int(k): {kk: D(vv) if kk != "ticks" else int(vv)
                             for kk, vv in v.items()}
                    for k, v in json.load(fh).items()}
    c = BR.index_1s(a, b)
    with open(p, "w") as fh:
        json.dump({str(k): {kk: str(vv) for kk, vv in v.items()}
                   for k, v in c.items()}, fh)
    time.sleep(0.15)
    return c


def sigma_1s(closes, t: int) -> float:
    """Realized 1-second vol in PRICE units over the trailing lookback.

    Log returns, then rescaled by the current level, so the quantity is
    directly comparable with a dollar distance to strike. Only buckets with
    start <= t-1 enter.
    """
    xs = [(s, closes[s]) for s in range(t - VOL_LOOKBACK_S, t)
          if s in closes]
    if len(xs) < 30:
        return 0.0
    r = []
    for i in range(1, len(xs)):
        p0, p1 = float(xs[i - 1][1]), float(xs[i][1])
        if p0 > 0 and p1 > 0:
            r.append(math.log(p1 / p0))
    if len(r) < 30:
        return 0.0
    m = sum(r) / len(r)
    v = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(v) * float(xs[-1][1])


def sigma_remaining_avg(s1: float, n_rem: int, d: int) -> float:
    """Std of the mean of the remaining settlement observations.

    Driftless random walk in 1-second steps: the mean of n future steps that
    begin d seconds ahead has variance sigma^2 * (d + (n+1)(2n+1)/(6n)). The
    first term is the drift of the level to the start of the averaging window;
    the second is the dispersion of the average itself.
    """
    if n_rem <= 0 or s1 <= 0:
        return 0.0
    return s1 * math.sqrt(d + (n_rem + 1) * (2 * n_rem + 1) / (6.0 * n_rem))


def build_window(slug: str, w, cache: str) -> list:
    tm = BR.market_terms(slug)
    assert tm["index_symbol"] == "BRTI", tm["index_symbol"]
    ws, we = tm["window_start_unix"], tm["window_end_unix"]
    ptb = tm["price_to_beat"]
    closes_all = brti_cached(slug, ws - VOL_LOOKBACK_S - 120, we + 30, cache)
    closes = {s: v["close"] for s, v in closes_all.items()}
    ticks = {s: v["ticks"] for s, v in closes_all.items()}

    books = sorted(w["book"], key=lambda b: b["t"])
    trades = sorted(w["trade"], key=lambda x: x["t"])
    bi = 0
    ti = 0
    rows = []
    for t in range(ws + PTB_DELAY_S, we):          # we-1 inclusive
        # --- rule 3: latest book at or before t
        while bi + 1 < len(books) and books[bi + 1]["t"] <= t:
            bi += 1
        b = books[bi] if books and books[bi]["t"] <= t else None
        while ti < len(trades) and trades[ti]["t"] <= t:
            ti += 1
        # --- rule 1: only buckets with start <= t-1
        brti = None
        for k in range(t - 1, t - 121, -1):
            if k in closes:
                brti = closes[k]
                break
        if brti is None or b is None:
            continue
        sec_rem = we - t
        s1 = sigma_1s(closes, t)
        # settlement-minute structure
        lo = we + BR.STAT_LO                       # we-60
        pub = [s for s in range(lo, we + BR.STAT_HI + 1)
               if s <= t - 1 and s in closes]
        n_pub = len(pub)
        cum = sum((closes[s] for s in pub), D(0))
        n_rem = STAT_N - n_pub
        if n_rem <= 0:
            continue                               # outcome fully determined
        req = (STAT_N * ptb - cum) / n_rem
        gap = brti - req
        d_ahead = max(0, lo - t)
        sra = sigma_remaining_avg(s1, n_rem, d_ahead)
        if sra <= 0:
            continue
        # momentum, trailing 60 s, normalized
        p0 = None
        for k in range(t - 1 - MOM_LOOKBACK_S, t):
            if k in closes:
                p0 = closes[k]
                break
        mom = (float(brti - p0) / s1) if (p0 is not None and s1 > 0) else 0.0
        # executable PMUS prices. price.value is ALWAYS the long-side price.
        bid, ask = D(b["bid"]), D(b["ask"])
        bq, aq = D(b["bid_qty"]), D(b["ask_qty"])
        up_px, up_q = ask, aq
        dn_px, dn_q = ONE - bid, bq
        # flow over the trailing 60 s, from trades already printed by t
        j = ti - 1
        n_tr = 0
        sv = D(0)
        while j >= 0 and trades[j]["t"] > t - MOM_LOOKBACK_S:
            n_tr += 1
            q = D(trades[j]["qty"])
            sv += q if trades[j].get("dir") == "buy" else -q
            j -= 1
        rows.append({
            "slug": slug, "t": t, "seconds_remaining": sec_rem,
            "price_to_beat": str(ptb), "brti_now": str(brti),
            "raw_distance": str(brti - ptb),
            "sigma_1s": round(s1, 6),
            "n_published": n_pub, "settle_cum_sum": str(cum),
            "settle_avg_published": (str(cum / n_pub) if n_pub else None),
            "n_remaining": n_rem,
            "required_remaining_avg": str(req),
            "gap_to_required": str(gap),
            "sigma_remaining_avg": round(sra, 6),
            "zero_tick_in_published": sum(1 for s in pub
                                          if ticks.get(s, 0) == 0),
            # ---- the three frozen features
            "z": round(float(gap) / sra, 6),
            "locked_frac": round(n_pub / float(STAT_N), 6),
            "mom_60s": round(mom, 6),
            # ---- executable PMUS state
            "up_ask": str(up_px), "up_ask_qty": str(up_q),
            "down_px": str(dn_px), "down_qty": str(dn_q),
            "spread": str(ask - bid), "book_age_s": round(t - b["t"], 3),
            "fee_up_cent": str(FEES.taker_fee(up_px, ONE, cents=True)),
            "fee_down_cent": str(FEES.taker_fee(dn_px, ONE, cents=True)),
            "fee_up_4dp": str(FEES.taker_fee(up_px, ONE)),
            "fee_down_4dp": str(FEES.taker_fee(dn_px, ONE)),
            "trades_60s": n_tr, "signed_vol_60s": str(sv),
        })
    # --- rule 4: the label is attached only now, after every feature exists
    lab = 1 if tm["outcome"] == "UP" else 0
    for r in rows:
        r[LABEL] = lab
    return rows, tm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default="data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl")
    ap.add_argument("--out", default="analysis/l5/state_conditional")
    ap.add_argument("--cache", default="data/brti_1s")
    ap.add_argument("--label", default="DEVELOPMENT")
    a = ap.parse_args()
    by = load(a.tape)
    slugs = sorted(s for s in by if eligible(s, by[s])[0])
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "state_table_%s.jsonl" % a.label.lower())
    n = 0
    terms = {}
    with open(p, "w") as fh:
        for sl in slugs:
            rows, tm = build_window(sl, by[sl], a.cache)
            # the label must not be reachable as a feature
            assert LABEL not in FEATURES
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            n += len(rows)
            terms[sl] = {k: str(v) for k, v in tm.items()}
            print("%s  rows=%-4d outcome=%s ptb=%s settle=%s"
                  % (sl, len(rows), tm["outcome"], tm["price_to_beat"],
                     tm["settlement_price"]))
    with open(os.path.join(a.out, "market_terms_%s.json" % a.label.lower()),
              "w") as fh:
        json.dump(terms, fh, indent=1)
    print("\nwindows=%d rows=%d -> %s" % (len(slugs), n, p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
