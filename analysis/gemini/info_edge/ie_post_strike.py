"""POST-STRIKE executable-opportunity investigation. FROZEN DEV ONLY.

THE QUESTION, exactly as posed:

  Given that BTC state favours a side AND that favoured side is actually
  offered, what PRE-TRADE observable variables distinguish offers whose
  settlement probability exceeds their executable price?

WHY POST-STRIKE ONLY. A BTC5m window's strike is published at the window's
OPEN, i.e. expiry - 300 s. Before that there is no strike, so "distance from
strike" does not exist and a price of 0.50 is a coin flip by construction.
SEMANTICS-1 filled at TTE ~720 s, pre-strike, which is why its win/loss
carries no directional information and is excluded here. Every checkpoint in
this file has TTE <= 300 s, and the strike's own availableAt is CHECKED
against the observation time rather than assumed.

DISTANCE IS ONE FACTOR, NOT THREE. Raw distance, normalised distance and the
sign of distance are the same underlying quantity on three scales. They are
reported as one factor and never combined as if independent.

NO THRESHOLD SWEEP. Five economically motivated candidates, stated before the
numbers were seen, each with a reason it could work:

  R0  every favoured-side executable offer           (the baseline to beat)
  R1  price <= 0.50                 -- a side the state favours should not
                                       cost more than a coin flip
  R2  |z| >= 1.0                    -- the cushion is at least one standard
                                       deviation of the REMAINING move, so
                                       the state is decisively favoured
  R3  R1 and R2                     -- cheap AND decisive
  R4  R3 and size >= 25             -- and large enough to be worth an order

z = distance / (sigma_1s * sqrt(TTE) * price), with sigma_1s the realised
per-second Coinbase return volatility over the preceding 300 s. It is a
normalisation, not a fitted parameter.

FEES ARE NOT OPTIONAL. Taker fee = ceil(0.07 * C * P * (1-P), cent), the
venue's own credited formula. EV/contract = q - p - fee(p). q is estimated
IN-SAMPLE on DEV, which makes every EV below a CANDIDATE-GENERATION figure and
not a validated edge: the circularity guard in PREREG_POWERED_COHORT.md
requires q to be applied out-of-sample before any edge is claimed.

No live trades. No pre-strike trades. No EXTEND. Skipping a window is
expected and is not a failure.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")

#: post-strike checkpoints only. 300 s is the window open, so every one of
#: these is inside the window and has a published strike.
TTES = (240, 180, 120, 90, 60, 45, 30, 20, 15, 10, 5)

#: horizons for the BTC path variables. Fixed, not searched.
HORIZONS = (5, 15, 60)

#: volatility estimation window, in seconds, for the normalisation only.
VOL_LOOKBACK = 300.0


def taker_fee(price: D, contracts: D = D("1")) -> D:
    """The venue's own credited taker formula, ceil to the cent."""
    raw = D("0.07") * contracts * price * (D("1") - price)
    return raw.quantize(D("0.01"), rounding=ROUND_CEILING)


def ap(book, rows, seen, t):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
            seen.pop(p, None)
        else:
            if p not in book:
                seen[p] = t
            book[p] = s


def pq(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    return v[min(int(q * (len(v) - 1)), len(v) - 1)]


def extract():
    dev = json.loads(
        Path("analysis/gemini/info_edge/DEV_COHORT_FROZEN.json").read_text())
    keep = set(dev["tickers"])
    settled, expiry, cb = {}, {}, []
    strike_at = {}
    ev = defaultdict(list)
    for f in sorted(Path("data/gemini_infoedge").glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                if r.get("ticker") in keep:
                    settled[r["ticker"]] = r
                continue
            if k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = SYM.match(r.get("market") or "")
            if not m or m.group(1) not in keep:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll"):
                if r.get("expiry"):
                    expiry[tk] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).timestamp()
                sa = (r.get("strike") or {}) if isinstance(r.get("strike"), dict) else {}
                if sa.get("availableAt"):
                    strike_at[tk] = datetime.fromisoformat(
                        sa["availableAt"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_depth", "p_snapshot", "p_trade"):
                ev[tk].append(r)
    cb.sort()
    cbt = [x[0] for x in cb]

    def cb_at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    def sigma_1s(t):
        """Realised per-second return volatility over the preceding window."""
        lo = bisect.bisect_left(cbt, t - VOL_LOOKBACK)
        hi = bisect.bisect_right(cbt, t)
        pts = cb[lo:hi]
        if len(pts) < 30:
            return None
        # one price per second, then log returns
        per_s = {}
        for ts, px in pts:
            per_s[int(ts)] = px
        ks = sorted(per_s)
        rets = []
        for a, b in zip(ks, ks[1:]):
            if b - a != 1:
                continue
            pa, pb = per_s[a], per_s[b]
            if pa > 0 and pb > 0:
                rets.append(math.log(pb / pa))
        if len(rets) < 20:
            return None
        mu = sum(rets) / len(rets)
        var = sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)
        return math.sqrt(var) if var > 0 else None

    inst = []
    skipped_pre_strike = 0
    for tk, s in settled.items():
        E = expiry.get(tk)
        if E is None:
            continue
        try:
            strike = float(s["strike"])
        except Exception:
            continue
        up_won = s["resolution_side"] == "yes"
        evs = sorted(ev.get(tk, []), key=lambda r: r["t_ns"])
        bids, asks, bseen, aseen = {}, {}, {}, {}
        seeded, last_u = False, None
        want = sorted(TTES, reverse=True)
        wi = 0
        snaps = []
        for r in evs:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_trade":
                continue
            if r["kind"] == "p_snapshot":
                nb, na = {}, {}
                ap(nb, r.get("bids"), {}, t)
                ap(na, r.get("asks"), {}, t)
                for p in nb:
                    bseen.setdefault(p, t)
                for p in na:
                    aseen.setdefault(p, t)
                for p in list(bseen):
                    if p not in nb:
                        bseen.pop(p, None)
                for p in list(aseen):
                    if p not in na:
                        aseen.pop(p, None)
                bids, asks = nb, na
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                ap(bids, r.get("b"), bseen, t)
                ap(asks, r.get("a"), aseen, t)
                last_u = u
            snaps.append((t, dict(bids), dict(asks)))
            while wi < len(want) and (E - t) <= want[wi]:
                tte = want[wi]
                wi += 1
                # THE STRIKE MUST ALREADY EXIST. Checked, never assumed.
                sa = strike_at.get(tk)
                if sa is not None and t < sa:
                    skipped_pre_strike += 1
                    continue
                c = cb_at(t)
                if c is None:
                    continue
                dist = c - strike
                side = "UP" if dist > 0 else "DOWN"
                if side == "UP":
                    lvl = min(asks) if asks else None
                    px = lvl
                    sz = asks.get(lvl) if lvl is not None else None
                    first = aseen.get(lvl) if lvl is not None else None
                    opp = max(bids) if bids else None
                else:
                    lvl = max(bids) if bids else None
                    px = (1 - lvl) if lvl is not None else None
                    sz = bids.get(lvl) if lvl is not None else None
                    first = bseen.get(lvl) if lvl is not None else None
                    opp = min(asks) if asks else None
                if px is None or px <= 0 or px >= 1:
                    continue
                sig = sigma_1s(t)
                z = None
                if sig:
                    sd = sig * math.sqrt(tte) * c
                    if sd > 0:
                        z = abs(dist) / sd
                # depth takeable at or better than the touch, favoured side
                if side == "UP":
                    depth = sum(q for p, q in asks.items() if p <= lvl)
                else:
                    depth = sum(q for p, q in bids.items() if p >= lvl)
                spread = None
                if opp is not None and lvl is not None:
                    spread = abs(lvl - opp)
                moves = {}
                for h in HORIZONS:
                    a0 = cb_at(t - h)
                    moves[h] = (c - a0) if a0 is not None else None
                inst.append({
                    "ticker": tk, "tte": tte, "t": t, "side": side,
                    "price": px, "size": sz, "level": lvl, "depth": depth,
                    "spread": spread,
                    "level_age": (t - first) if first else None,
                    "dist": round(dist, 2),
                    "abs_dist": round(abs(dist), 2),
                    "z": (round(z, 3) if z is not None else None),
                    **{f"mv{h}": (round(moves[h], 2)
                                  if moves[h] is not None else None)
                       for h in HORIZONS},
                    # does the recent path AGREE with the favoured side?
                    "path_agrees": (None if moves[15] is None else
                                    ((moves[15] > 0) == (side == "UP"))),
                    "correct": (up_won if side == "UP" else (not up_won)),
                })
        for r in inst:
            if r["ticker"] != tk or "persist_s" in r:
                continue
            lvl, t0, side = r["level"], r["t"], r["side"]
            gone = None
            for (ts, b, a) in snaps:
                if ts <= t0:
                    continue
                if lvl not in (a if side == "UP" else b):
                    gone = ts
                    break
            r["persist_s"] = (gone - t0) if gone else None
    return inst, skipped_pre_strike


# --------------------------------------------------------------------------
# the five predeclared candidates
# --------------------------------------------------------------------------
CANDIDATES = [
    ("R0  every favoured-side executable offer", lambda r: True),
    ("R1  price <= 0.50", lambda r: r["price"] <= 0.50),
    ("R2  |z| >= 1.0", lambda r: r["z"] is not None and r["z"] >= 1.0),
    ("R3  R1 and R2",
     lambda r: r["price"] <= 0.50 and r["z"] is not None and r["z"] >= 1.0),
    ("R4  R3 and size >= 25",
     lambda r: (r["price"] <= 0.50 and r["z"] is not None and r["z"] >= 1.0
                and (r["size"] or 0) >= 25)),
]


def evaluate(rows, name):
    n = len(rows)
    if n == 0:
        return {"rule": name, "n": 0}
    wins = [r for r in rows if r["correct"]]
    q = len(wins) / n
    prices = [D(str(round(r["price"], 2))) for r in rows]
    fees = [taker_fee(p) for p in prices]
    mean_p = sum(prices) / len(prices)
    mean_fee = sum(fees) / len(fees)
    ev_gross = D(str(round(q, 6))) - mean_p
    ev_net = ev_gross - mean_fee
    worst = max(p + f for p, f in zip(prices, fees))
    return {
        "rule": name, "n": n,
        "windows": len({r["ticker"] for r in rows}),
        "price_p50": pq([r["price"] for r in rows], .5),
        "price_mean": float(mean_p),
        "correct": q,
        "fee_mean": float(mean_fee),
        "ev_gross": float(ev_gross),
        "ev_net": float(ev_net),
        "size_p50": pq([r["size"] for r in rows], .5),
        "size_p10": pq([r["size"] for r in rows], .1),
        "depth_p50": pq([r["depth"] for r in rows], .5),
        "worst_loss": float(worst),
    }


def main():
    inst, pre = extract()
    print("=" * 94)
    print("POST-STRIKE EXECUTABLE OPPORTUNITY -- FROZEN DEV ONLY (48 windows)")
    print("=" * 94)
    print("Only TTE <= 300 s, with the strike's availableAt checked against")
    print("the observation time. SEMANTICS-1 was PRE-strike and is excluded.")
    print(f"checkpoints skipped as pre-strike: {pre}")
    print(f"favoured-side EXECUTABLE instances: {len(inst)} "
          f"across {len({r['ticker'] for r in inst})} independent windows\n")
    if not inst:
        print("nothing to analyse")
        return

    print("--- the distance factor (ONE factor on three scales) ---")
    print("  raw distance, |distance| and z are the same quantity. Reported")
    print("  together so they are never counted as independent signals.")
    for nm, key in (("abs_dist ($)", "abs_dist"), ("z (sd of remaining move)", "z")):
        v = [r[key] for r in inst if r[key] is not None]
        print("  %-26s n=%3d  p10 %8.2f  p50 %8.2f  p90 %8.2f"
              % (nm, len(v), pq(v, .1), pq(v, .5), pq(v, .9)))
    print()

    print("--- other pre-trade observables, winners vs losers ---")
    W = [r for r in inst if r["correct"]]
    L = [r for r in inst if not r["correct"]]
    print(f"  winners n={len(W)}   losers n={len(L)}")
    hdr = ("price", "size", "depth", "spread", "level_age", "persist_s",
           "mv15", "z", "abs_dist")
    for k in hdr:
        w = pq([r.get(k) for r in W], .5)
        l = pq([r.get(k) for r in L], .5)
        f = lambda x: "    -" if x is None else f"{x:8.2f}"
        print("  %-12s  win p50 %s   lose p50 %s" % (k, f(w), f(l)))
    pa = [r for r in inst if r["path_agrees"] is not None]
    if pa:
        ag = [r for r in pa if r["path_agrees"]]
        dg = [r for r in pa if not r["path_agrees"]]
        wr = lambda s: (sum(1 for r in s if r["correct"]) / len(s) * 100
                        if s else None)
        print("  path agrees with favoured side: n=%d correct %.1f%%  |  "
              "disagrees: n=%d correct %.1f%%"
              % (len(ag), wr(ag) or 0, len(dg), wr(dg) or 0))
    print()

    print("--- THE FIVE PREDECLARED CANDIDATES ---")
    print("  EV/contract = correct_rate - mean_price - mean_fee.")
    print("  correct_rate is IN-SAMPLE on DEV, so EV_net is a CANDIDATE")
    print("  figure, not a validated edge.\n")
    print("  %-38s %4s %4s %7s %8s %8s %8s %7s %7s %7s"
          % ("rule", "n", "win", "price", "correct", "EVgross", "EVnet",
             "sz_p50", "sz_p10", "worst"))
    out = []
    for name, fn in CANDIDATES:
        rows = [r for r in inst if fn(r)]
        res = evaluate(rows, name)
        out.append(res)
        if res["n"] == 0:
            print("  %-38s %4d   (no instances)" % (name, 0))
            continue
        print("  %-38s %4d %4d %7.3f %7.1f%% %+8.4f %+8.4f %7.1f %7.1f %7.3f"
              % (name, res["n"], res["windows"], res["price_mean"],
                 res["correct"] * 100, res["ev_gross"], res["ev_net"],
                 res["size_p50"] or 0, res["size_p10"] or 0,
                 res["worst_loss"]))
    print()
    print("--- per-TTE availability of the best-reduced candidate (R3) ---")
    r3 = [r for r in inst if CANDIDATES[3][1](r)]
    by = defaultdict(list)
    for r in r3:
        by[r["tte"]].append(r)
    for tte in sorted(by, reverse=True):
        g = by[tte]
        print("  TTE %4d  n=%2d  windows=%2d  correct %5.1f%%  price p50 %.2f"
              % (tte, len(g), len({x['ticker'] for x in g}),
                 sum(1 for x in g if x["correct"]) / len(g) * 100,
                 pq([x["price"] for x in g], .5)))

    Path("analysis/gemini/info_edge/ie_post_strike.json").write_text(
        json.dumps({"instances": inst, "candidates": out,
                    "skipped_pre_strike": pre}, indent=2, default=str))
    print("\nwrote analysis/gemini/info_edge/ie_post_strike.json")


if __name__ == "__main__":
    main()
