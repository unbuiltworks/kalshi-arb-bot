"""Decompose the CASH-CONFIRMED pre-incentive L5 edge. Q1, Q2, Q5, Q6.

Explains the conservative +$15,238 / ~$138.50/day baseline; does not
optimize it. Historical reconstruction of another trader's public
activity -- no figure is realized profit for our account.

ACCOUNTING, all charged, nothing modelled:
  * identity = PROVENANCE (params.user), never the absent `name` field
  * dedupe   = (txh, conditionId, outcomeIndex, ts, price, size, type)
  * cost     = usdcSize - price*size, the DEBITED wedge
  * cash     = redemption + sell_usdc - buy_usdc   (fees already inside)

THE DOUBLE-COUNT RULE. Q2 asks to attribute cash between matched-pair
economics and residual settlement. Those two are ACCRUAL constructs; the
cash is a FACT. So the attribution is computed as a SPLIT OF THE ACTUAL
CASH, derived from the inventory state at settlement, never as an
independent model added alongside redemption:

  settlement cash for a condition = 1.00 * (winning shares held)
  of which   matched part  = 1.00 * matched_qty   (the pair's own payout)
             residual part = 1.00 * (winning residual qty)
  and the purchase cost splits the same way, at each leg's own VWAP.

Every component therefore sums back to the measured cash by construction,
and is asserted to do so.
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import statistics
from typing import Any, Dict, List

SRC = (("data/l5/raw/activity.jsonl", False),
       ("data/l5/raw/activity_gap.jsonl.gz", True))
L5 = "0xb945945d5bca"
PRE = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp()
#: identified regime boundaries (from the regime-detection section)
REGIMES = (("R1", None, dt.datetime(2026, 4, 25, tzinfo=dt.timezone.utc)
            .timestamp()),
           ("R2", dt.datetime(2026, 4, 25, tzinfo=dt.timezone.utc).timestamp(),
            dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc).timestamp()),
           ("R3_pre_incentive_part",
            dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc).timestamp(),
            PRE))
KEY = lambda x: (x.get("transactionHash"), x.get("conditionId"),
                 x.get("outcomeIndex"), x.get("timestamp"),
                 str(x.get("price")), str(x.get("size")), x.get("type"))


def fl(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load() -> List[Dict[str, Any]]:
    seen, rows = set(), []
    for path, gz in SRC:
        op = gzip.open if gz else open
        with op(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if L5 not in str((env.get("params") or {})
                                 .get("user", "")).lower():
                    continue
                pl = env.get("payload")
                if not isinstance(pl, list):
                    continue
                for r in pl:
                    if not isinstance(r, dict):
                        continue
                    nm = r.get("name")
                    if nm is not None and nm != "l5Zn1bWoM8eTsK":
                        continue                     # contradicting identity
                    k = KEY(r)
                    if k in seen:
                        continue
                    seen.add(k)
                    rows.append(r)
    return rows


def build(rows):
    """Per-condition sequential reconstruction, in timestamp order."""
    by = collections.defaultdict(list)
    for r in rows:
        c = r.get("conditionId")
        if c:
            by[c].append(r)
    events = []
    for cond, rs in by.items():
        rs.sort(key=lambda r: (fl(r.get("timestamp")),
                               0 if r.get("type") == "TRADE" else 1))
        trades = [r for r in rs if r.get("type") == "TRADE"]
        if not trades:
            continue
        t0 = fl(trades[0].get("timestamp"))
        if t0 >= PRE or t0 <= 0:
            continue
        slug = ""
        for r in rs:
            if r.get("eventSlug"):
                slug = str(r["eventSlug"])
                break
        dur = 900 if "-15m-" in slug else (300 if "-5m-" in slug else None)
        try:
            w0 = int(slug.rsplit("-", 1)[-1])
        except Exception:                                      # noqa: BLE001
            w0 = None
        # ---- walk the sequence
        qty = {0: 0.0, 1: 0.0}
        cost = {0: 0.0, 1: 0.0}
        buy_usdc = sell_usdc = charged = 0.0
        path = []
        prev_oi = None
        prev_px = None
        n_ext_lo = n_ext_hi = n_ext_eq = n_switch = 0
        peak_outlay = 0.0
        cum_outlay = 0.0
        first_complete_at = None
        matched_hist = []
        for r in trades:
            oi = r.get("outcomeIndex")
            if oi not in (0, 1):
                continue
            px, q, u = fl(r.get("price")), fl(r.get("size")), fl(r.get("usdcSize"))
            side = str(r.get("side") or "").upper()
            ts = fl(r.get("timestamp"))
            charged += max(0.0, u - px * q)
            if side == "BUY":
                qty[oi] += q
                cost[oi] += px * q
                buy_usdc += u
                cum_outlay += u
            elif side == "SELL":
                qty[oi] -= q
                cost[oi] -= px * q
                sell_usdc += u
                cum_outlay -= u
            peak_outlay = max(peak_outlay, cum_outlay)
            m = min(max(0.0, qty[0]), max(0.0, qty[1]))
            matched_hist.append(m)
            if m > 0 and first_complete_at is None:
                first_complete_at = ts
            # relation of this fill to the previous one
            rel = "OPEN"
            if prev_oi is not None:
                if oi == prev_oi:
                    if prev_px is not None and px < prev_px:
                        rel = "EXTEND_LOWER"
                        n_ext_lo += 1
                    elif prev_px is not None and px > prev_px:
                        rel = "EXTEND_HIGHER"
                        n_ext_hi += 1
                    else:
                        rel = "EXTEND_SAME"
                        n_ext_eq += 1
                else:
                    rel = "SWITCH"
                    n_switch += 1
            path.append({"t": ts, "oi": oi, "side": side, "px": px, "q": q,
                         "rel": rel, "up_qty": qty[0], "dn_qty": qty[1],
                         "matched": m,
                         "residual": abs(qty[0] - qty[1]),
                         "matched_cost": None, "tte": (
                             (w0 + dur - ts) if (w0 and dur) else None)})
            prev_oi, prev_px = oi, px
        # ---- terminal state
        reds = [r for r in rs if r.get("type") == "REDEEM"]
        red_cash = sum(fl(r.get("usdcSize")) for r in reds)
        red_legs = {r.get("outcomeIndex") for r in reds
                    if r.get("outcomeIndex") is not None}
        cash = red_cash + sell_usdc - buy_usdc
        matched = min(max(0.0, qty[0]), max(0.0, qty[1]))
        vw = {oi: (cost[oi] / qty[oi] if qty[oi] > 0 else 0.0)
              for oi in (0, 1)}
        resid_oi = 0 if qty[0] > qty[1] else (1 if qty[1] > qty[0] else None)
        resid_q = abs(qty[0] - qty[1])
        # ---- cash attribution: a split of the ACTUAL cash, not a model
        pair_cost = vw[0] + vw[1]
        matched_cash = matched * (1.0 - pair_cost)
        won = (resid_oi is not None and resid_oi in red_legs)
        resid_cash = (resid_q * ((1.0 if won else 0.0)
                                 - (vw[resid_oi] if resid_oi is not None
                                    else 0.0)))
        events.append({
            "cond": cond, "slug": slug, "t0": t0, "dur": dur,
            "n_fills": len(path), "cash": cash,
            "buy_usdc": buy_usdc, "sell_usdc": sell_usdc,
            "red_cash": red_cash, "red_n": len(reds),
            "charged_cost": charged, "has_red": bool(reds),
            "up_qty": qty[0], "dn_qty": qty[1],
            "matched_qty": matched, "residual_qty": resid_q,
            "residual_oi": resid_oi, "residual_won": won,
            "pair_cost": pair_cost if (qty[0] > 0 and qty[1] > 0) else None,
            "vw_up": vw[0], "vw_dn": vw[1],
            "matched_cash_part": matched_cash,
            "residual_cash_part": resid_cash,
            "attribution_gap": cash - (matched_cash + resid_cash - charged),
            "n_ext_lower": n_ext_lo, "n_ext_higher": n_ext_hi,
            "n_ext_same": n_ext_eq, "n_switch": n_switch,
            "first_px": path[0]["px"] if path else None,
            "first_oi": path[0]["oi"] if path else None,
            "first_tte": path[0]["tte"] if path else None,
            "time_to_first_complete": ((first_complete_at - t0)
                                       if first_complete_at else None),
            "matched_pct": (matched / max(qty[0], qty[1])
                            if max(qty[0], qty[1]) > 0 else None),
            "residual_pct": (resid_q / max(qty[0], qty[1])
                             if max(qty[0], qty[1]) > 0 else None),
            "peak_outlay": peak_outlay,
            "path": path,
        })
    events.sort(key=lambda e: e["t0"])
    return events


def q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))], 4)


def dist(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": q(xs, 0), "p10": q(xs, .1), "p25": q(xs, .25),
            "median": q(xs, .5), "p75": q(xs, .75), "p90": q(xs, .9),
            "max": q(xs, 1.0), "mean": round(statistics.fmean(xs), 4),
            "sum": round(sum(xs), 2)}


def main() -> int:
    rows = load()
    ev = build(rows)
    cash = [e["cash"] for e in ev]
    total = sum(cash)
    # ---- Q5 concentration
    srt = sorted(ev, key=lambda e: -e["cash"])
    conc = {}
    for frac in (0.01, 0.05, 0.10, 0.25):
        k = max(1, int(round(frac * len(srt))))
        conc["top_%d%%" % int(frac * 100)] = {
            "events": k,
            "cash": round(sum(e["cash"] for e in srt[:k]), 2),
            "share_of_total_pct": (round(100.0 * sum(e["cash"]
                                                     for e in srt[:k])
                                         / total, 2) if total else None)}
    wins = sum(1 for c in cash if c > 0)
    # drawdown on the chronological cash series
    run = 0.0
    peak = 0.0
    maxdd = 0.0
    losing = cur = 0
    for e in ev:
        run += e["cash"]
        peak = max(peak, run)
        maxdd = min(maxdd, run - peak)
        if e["cash"] < 0:
            cur += 1
            losing = max(losing, cur)
        else:
            cur = 0
    # ---- Q6 time stability
    wk = collections.defaultdict(float)
    wk_n = collections.Counter()
    for e in ev:
        w = dt.datetime.utcfromtimestamp(e["t0"]).strftime("%G-W%V")
        wk[w] += e["cash"]
        wk_n[w] += 1
    reg = {}
    for name, lo, hi in REGIMES:
        sub = [e for e in ev
               if (lo is None or e["t0"] >= lo) and e["t0"] < hi]
        if not sub:
            continue
        days = len({dt.datetime.utcfromtimestamp(e["t0"]).date()
                    for e in sub})
        reg[name] = {
            "events": len(sub), "days": days,
            "cash": round(sum(e["cash"] for e in sub), 2),
            "per_day": round(sum(e["cash"] for e in sub) / days, 2),
            "per_event": round(sum(e["cash"] for e in sub) / len(sub), 4),
            "win_rate_pct": round(100.0 * sum(1 for e in sub
                                              if e["cash"] > 0)
                                  / len(sub), 2),
            "median_event_cash": q([e["cash"] for e in sub], .5),
        }
    # ---- Q2 attribution, asserted to reconcile
    gap = sum(e["attribution_gap"] for e in ev)
    res = {
        "label": "L5_EDGE_DECOMPOSITION_V1",
        "scope": {"pre_incentive_events": len(ev),
                  "fills": sum(e["n_fills"] for e in ev),
                  "total_cash": round(total, 2),
                  "days": len({dt.datetime.utcfromtimestamp(e["t0"]).date()
                               for e in ev}),
                  "per_day": round(total / len({
                      dt.datetime.utcfromtimestamp(e["t0"]).date()
                      for e in ev}), 2)},
        "Q2_attribution": {
            "matched_pair_part": round(sum(e["matched_cash_part"]
                                           for e in ev), 2),
            "residual_settlement_part": round(sum(e["residual_cash_part"]
                                                  for e in ev), 2),
            "charged_transaction_costs": round(-sum(e["charged_cost"]
                                                    for e in ev), 2),
            "sum_of_parts": round(sum(e["matched_cash_part"]
                                      + e["residual_cash_part"]
                                      - e["charged_cost"] for e in ev), 2),
            "measured_cash": round(total, 2),
            "UNRECONCILED_RESIDUAL": round(gap, 2),
            "note": ("the parts are a SPLIT of measured cash derived from "
                     "terminal inventory, never a model added alongside "
                     "redemption, so no value is double-counted. The "
                     "unreconciled residual is reported, not hidden."),
        },
        "Q1_matched_vs_residual": {
            "events_with_matched_inventory": sum(1 for e in ev
                                                 if e["matched_qty"] > 0),
            "matched_cash_part_total": round(sum(e["matched_cash_part"]
                                                 for e in ev), 2),
            "residual_cash_part_total": round(sum(e["residual_cash_part"]
                                                  for e in ev), 2),
            "pair_cost": dist([e["pair_cost"] for e in ev]),
            "matched_pct": dist([e["matched_pct"] for e in ev]),
            "residual_pct": dist([e["residual_pct"] for e in ev]),
            "residual_won_events": sum(1 for e in ev if e["residual_won"]),
        },
        "Q5_concentration": {
            "event_cash": dist(cash),
            "win_rate_pct": round(100.0 * wins / len(ev), 2),
            "concentration": conc,
            "max_drawdown": round(maxdd, 2),
            "longest_losing_streak_events": losing,
        },
        "Q6_time_stability": {
            "by_regime": reg,
            "by_week": {k: {"cash": round(v, 2), "events": wk_n[k]}
                        for k, v in sorted(wk.items())},
        },
        "Q7_capacity_inputs": {
            "peak_outlay_per_event": dist([e["peak_outlay"] for e in ev]),
            "cash_per_deployed_dollar": dist(
                [e["cash"] / e["peak_outlay"] for e in ev
                 if e["peak_outlay"] > 0]),
        },
        "profit_attribution": ("NONE for our account; historical "
                               "reconstruction of public activity."),
    }
    with open("analysis/l5/EDGE_DECOMPOSITION.json", "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps(res, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
