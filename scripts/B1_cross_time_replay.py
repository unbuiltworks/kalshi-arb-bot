"""TRACK B -- cross-time inventory compression on Kalshi and Gemini.

Chronological replay, NO LOOKAHEAD. Tests whether buying the two opposite
outcomes AT DIFFERENT TIMES can reach positive net expectancy on a venue
we can actually reach.

THREE QUANTITIES, KEPT SEPARATE -- this is the whole point of the design:

  QUOTED OPPORTUNITY     the ask was at or below theta at some moment.
                         Says nothing about whether we could have traded.
  DEFENSIBLE SIMULATED   a passive order at theta whose QUEUE actually
  FILL                   cleared: aggressive volume arriving at or through
                         our price exceeded the depth resting ahead of us
                         when we posted. A touch is NEVER a fill.
  ACTUAL HISTORICAL FILL a print that really happened in the tape.

VENUE MECHANICS
  Kalshi  bbo carries yes_bids and no_bids. So
            yes_ask = 1 - best_no_bid      no_ask = 1 - best_yes_bid
          prints carry taker_side and yes_price:
            taker_side='no'  -> taker sold YES, hitting a YES bid
            taker_side='yes' -> taker bought YES, lifting the YES ask
          A resting YES bid at p is therefore filled only by taker_side='no'
          prints at yes_price <= p, and only after the depth ahead clears.
  Gemini  bbo carries bids/asks directly per leg market (…-UP / …-DOWN).

STRATEGIES
  AGG(theta)   buy one unit of a leg the first time its ask <= theta,
               paying the ask, taker fees. Both legs independently.
  PAS(theta)   post a bid at theta on each leg; fill only when the queue
               clears as defined above, maker fees.

SETTLEMENT is a PROXY: the last print in the window decides the winner
(yes_price > 0.5 -> YES). The tapes carry no settlement record. Every
result is labelled with this and markets without a usable final print are
dropped rather than guessed.

EVERY market is settled, including those where only one leg filled -- a
single unpaired leg is a directional bet paying 1 or 0, and dropping those
is the survivorship error that invalidated an earlier version of this
analysis.

Read-only. No network. Nothing is armed.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import random
import statistics
import sys

THETAS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.48, 0.50]
SEED = 20261009


def kalshi_fee(contracts, price, maker):
    """CLAUDE.md schedule: ceil(rate * C * P * (1-P)) in CENTS, rounded UP."""
    rate = 0.0175 if maker else 0.07
    return math.ceil(rate * contracts * price * (1 - price) * 100) / 100.0


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def ci(vals, rng, reps=2000, cap=20000):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), cap)
    out = sorted(statistics.fmean([v[rng.randrange(k)] for _ in range(m)])
                 for _ in range(reps))
    return out[int(reps * .025)], out[int(reps * .975)]


def load(paths):
    """market -> chronological list of events, per venue."""
    mk = collections.defaultdict(list)
    meta = {}
    for p in paths:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                v, k, m = r.get("venue"), r.get("kind"), r.get("market")
                if v not in ("kalshi", "gemini") or not m:
                    continue
                if k == "window":
                    meta[m] = r
                elif k in ("bbo", "print"):
                    mk[(v, m)].append(r)
    for key in mk:
        mk[key].sort(key=lambda r: r.get("t") or 0)
    return mk, meta


def kalshi_asks(ev):
    yb = ev.get("yes_bids") or []
    nb = ev.get("no_bids") or []
    byb = max((f(p) for p, _ in yb), default=None)
    bnb = max((f(p) for p, _ in nb), default=None)
    return ((1 - bnb) if bnb is not None else None,      # yes_ask
            (1 - byb) if byb is not None else None,      # no_ask
            byb, bnb, yb, nb)


def replay_kalshi(events, theta):
    """Returns dict of per-strategy state for one market, one theta."""
    quoted = {"YES": False, "NO": False}
    agg = {}
    pas_order = {}          # leg -> depth_ahead remaining
    pas_fill = {}
    last_yes_price = None
    n_prints = 0

    for ev in events:
        k = ev.get("kind")
        if k == "bbo":
            ya, na, byb, bnb, yb, nb = kalshi_asks(ev)
            for leg, ask in (("YES", ya), ("NO", na)):
                if ask is not None and ask <= theta:
                    quoted[leg] = True
                    if leg not in agg:
                        agg[leg] = ask            # AGG: pay the ask now
            # PAS: post at theta once, record the depth resting ahead
            if byb is not None and "YES" not in pas_order \
                    and "YES" not in pas_fill:
                ahead = sum(f(s, 0.0) for p, s in yb if f(p, 0) >= theta)
                pas_order["YES"] = ahead
            if bnb is not None and "NO" not in pas_order \
                    and "NO" not in pas_fill:
                ahead = sum(f(s, 0.0) for p, s in nb if f(p, 0) >= theta)
                pas_order["NO"] = ahead
        elif k == "print":
            n_prints += 1
            yp = f(ev.get("yes_price"))
            cnt = f(ev.get("count"), 0.0) or 0.0
            ts = ev.get("taker_side")
            if yp is not None:
                last_yes_price = yp
            # a resting YES bid at theta is hit by taker_side='no' at <= theta
            if ts == "no" and yp is not None and yp <= theta \
                    and "YES" in pas_order:
                pas_order["YES"] -= cnt
                if pas_order["YES"] < 0:
                    pas_fill["YES"] = theta
                    del pas_order["YES"]
            # a resting NO bid at theta is hit by taker_side='yes' when the
            # NO price (1-yes_price) is <= theta
            if ts == "yes" and yp is not None and (1 - yp) <= theta \
                    and "NO" in pas_order:
                pas_order["NO"] -= cnt
                if pas_order["NO"] < 0:
                    pas_fill["NO"] = theta
                    del pas_order["NO"]
    return quoted, agg, pas_fill, last_yes_price, n_prints


def settle(fills, winner, maker):
    """fills: leg->price. winner: 'YES'|'NO'. Returns net P&L for 1 unit."""
    if not fills:
        return None
    cost = 0.0
    for leg, px in fills.items():
        cost += px + kalshi_fee(1, px, maker)
    proceeds = 1.0 if len(fills) == 2 else (
        1.0 if next(iter(fills)) == winner else 0.0)
    return proceeds - cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)

    paths = []
    for g in a.tapes:
        paths.extend(sorted(glob.glob(g)))
    mk, meta = load(paths)

    kal = [(v, m) for (v, m) in mk if v == "kalshi"]
    gem = [(v, m) for (v, m) in mk if v == "gemini"]

    # --- Gemini feasibility: are both legs even quoted, and are there prints?
    gem_events = sum(len(mk[k]) for k in gem)
    gem_prints = sum(1 for k in gem for e in mk[k] if e.get("kind") == "print")
    gem_up = {m for (v, m) in gem if m.endswith("-UP")}
    gem_dn = {m for (v, m) in gem if m.endswith("-DOWN")}
    gem_pairs = {m[:-3] for m in gem_up} & {m[:-5] for m in gem_dn}

    R = {
        "tape_files": len(paths),
        "venues": {
            "kalshi": {"markets": len(kal),
                       "events": sum(len(mk[k]) for k in kal)},
            "gemini": {"markets": len(gem), "events": gem_events,
                       "prints": gem_prints,
                       "up_leg_markets": len(gem_up),
                       "down_leg_markets": len(gem_dn),
                       "markets_with_BOTH_legs": len(gem_pairs)},
        },
        "settlement_method":
            "PROXY: last print in the window decides the winner "
            "(yes_price > 0.5 -> YES). The tapes carry no settlement "
            "record. Markets without a usable final print are DROPPED.",
        "fees": "Kalshi schedule from CLAUDE.md: taker ceil(0.07*C*P*(1-P)), "
                "maker ceil(0.0175*C*P*(1-P)), rounded UP to the cent.",
        "kalshi": {},
    }

    if gem_prints < 100 or len(gem_pairs) == 0:
        R["venues"]["gemini"]["VERDICT"] = (
            f"NOT TESTABLE: {gem_prints} prints across {len(gem)} leg "
            f"markets, and {len(gem_pairs)} markets quote BOTH legs. A "
            "passive strategy cannot be evaluated without aggressive flow "
            "to fill against, and a cross-time pair cannot be assembled "
            "where the complement leg is not quoted. This is a data "
            "limitation, not a profitability finding.")

    for th in THETAS:
        agg_pnl, pas_pnl = [], []
        q_both = q_any = 0
        pas_both = pas_one = pas_none = 0
        agg_both = agg_one = 0
        n_mkt = 0
        dropped_no_winner = 0
        for key in kal:
            ev = mk[key]
            quoted, agg, pas, last_yp, npr = replay_kalshi(ev, th)
            if last_yp is None:
                dropped_no_winner += 1
                continue
            n_mkt += 1
            winner = "YES" if last_yp > 0.5 else "NO"
            if quoted["YES"] and quoted["NO"]:
                q_both += 1
            if quoted["YES"] or quoted["NO"]:
                q_any += 1
            pa = settle(agg, winner, maker=False)
            pp = settle(pas, winner, maker=True)
            agg_pnl.append(pa if pa is not None else 0.0)
            pas_pnl.append(pp if pp is not None else 0.0)
            if len(agg) == 2:
                agg_both += 1
            elif len(agg) == 1:
                agg_one += 1
            if len(pas) == 2:
                pas_both += 1
            elif len(pas) == 1:
                pas_one += 1
            else:
                pas_none += 1
        if not n_mkt:
            continue
        alo, ahi = ci(agg_pnl, rng)
        plo, phi = ci(pas_pnl, rng)
        R["kalshi"][f"{th:.2f}"] = {
            "markets_settled": n_mkt,
            "dropped_no_final_print": dropped_no_winner,
            "QUOTED_opportunity": {
                "both_legs_quoted_at_or_below_theta": round(q_both / n_mkt, 4),
                "either_leg": round(q_any / n_mkt, 4),
            },
            "AGGRESSIVE_taker": {
                "both_legs_filled": round(agg_both / n_mkt, 4),
                "one_leg_only": round(agg_one / n_mkt, 4),
                "mean_pnl_per_market": round(statistics.fmean(agg_pnl), 5),
                "ci95": [None if alo is None else round(alo, 5),
                         None if ahi is None else round(ahi, 5)],
                "share_profitable": round(
                    sum(1 for x in agg_pnl if x > 0) / n_mkt, 4),
            },
            "PASSIVE_queue_cleared": {
                "both_legs_filled": round(pas_both / n_mkt, 4),
                "one_leg_only": round(pas_one / n_mkt, 4),
                "never_filled": round(pas_none / n_mkt, 4),
                "mean_pnl_per_market": round(statistics.fmean(pas_pnl), 5),
                "ci95": [None if plo is None else round(plo, 5),
                         None if phi is None else round(phi, 5)],
                "share_profitable": round(
                    sum(1 for x in pas_pnl if x > 0) / n_mkt, 4),
            },
        }

    best = None
    for th, row in R["kalshi"].items():
        for mode in ("AGGRESSIVE_taker", "PASSIVE_queue_cleared"):
            lo = row[mode]["ci95"][0]
            v = row[mode]["mean_pnl_per_market"]
            if lo is not None and (best is None or v > best[2]):
                best = (th, mode, v, row[mode]["ci95"])
    if best:
        th, mode, v, c = best
        R["VERDICT"] = {
            "best": {"theta": th, "mode": mode, "mean_pnl_per_market": v,
                     "ci95": c},
            "gate": ("POSITIVE net expectancy: the best configuration's whole "
                     "95% CI is above zero."
                     if c[0] is not None and c[0] > 0 else
                     ("NEGATIVE: the best configuration's whole CI is below "
                      "zero." if c[1] is not None and c[1] < 0 else
                      "INDETERMINATE: the best configuration's CI spans "
                      "zero.")),
            "_caveats": [
                "settlement is a last-print proxy, not a settlement record",
                "one unit per leg; no capacity or size scaling modelled",
                "queue model assumes we join BEHIND all depth at or better "
                "than our price and never re-price or cancel",
                "no latency, no rejects, no partial fills",
            ],
        }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
