#!/usr/bin/env python3
"""Four actions for unmatched inventory, scored against HOLDING.

THE BASELINE WAS WRONG, AND THAT IS THE POINT OF THIS SCRIPT.

`l5_book_completion.decide` refuses a completion whose pair cost exceeds
0.9532, the settled break-even. That number is a HISTORICAL AVERAGE of
completion versus stranding across 17 windows. It is not the value of
holding THIS leg in THIS book, and comparing against it is comparing
against the wrong thing.

THE CORRECT BASELINE, DERIVED. Hold a quantity q of side H, cost already
paid and therefore SUNK. Let p be the probability H settles at $1.00.

    E[hold]      = q * p
    E[complete]  = q * 1.00 - q * x        (x = complement's own price)
    incremental  = E[complete] - E[hold] = q * (1 - x - p)

So completing beats holding **iff x + p < 1**. The sunk first-leg cost
CANCELS -- which is why this comparison can be made on a tape that does not
carry it, and why no inventory cost is invented here.

WHERE p COMES FROM. The market's own mid price for the held side. That is
the standard estimate and the only one this programme is entitled to: the
durable operating model says not to assume residual directional exposure has
predictive skill, so substituting a view of our own would be exactly the
thing it forbids. p is observed, never modelled.

WHAT THE IDENTITY THEN SAYS. The complement's own price and the held side's
probability are complements: x_mid ~= 1 - p. So at the mid, completing and
holding are a WASH, and the entire incremental result is the side of the
book you transact on plus fees:

  * MAKER completion rests and is filled at BETTER than mid -> x < 1 - p
    -> incremental POSITIVE, by roughly the half-spread it captured.
  * MARKETABLE completion crosses and pays WORSE than mid -> x > 1 - p
    -> incremental NEGATIVE, by the half-spread plus the taker fee.
  * REDUCING sells the held side into the bid, also crossing, so it is
    negative in expectation versus holding by the same half-spread plus
    fee. It buys VARIANCE reduction, never expectancy.

FEES ARE THE VENUE'S OWN. `TAKER_THETA = 0.0695` per `l5_two_sided_probe`,
charged as theta * price * (1 - price) per contract. The maker rebate is
`MAKER_THETA = -0.0125` on paper and was credited $0.0000 across 55 live
fill events, so it is counted as ZERO here, not as a credit.

CONSERVATIVE THROUGHOUT.
  * A marketable completion walks the DISPLAYED ladder and is scored at its
    VWAP, and is refused outright when displayed depth is short of the
    quantity needed. Displayed depth is displayed, not guaranteed.
  * A maker completion is scored only as an OPPORTUNITY. Its queue-ahead
    multiple is reported beside every row and nothing here calls it a fill.
  * No settlement outcome is used anywhere. The comparison is in
    expectation under the market's own p, which is what is observable.
"""
from __future__ import annotations

import glob
import json
import pathlib
import sys
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_two_sided_probe as TS        # noqa: E402

TAPES = "analysis/l5/residual_exit/preserved/btc15m_20261026_*.ndjson"
VARIANT = "RESIDUAL_URGENCY"
QUEUE_MULT = D("50")
BREAKEVEN = D("0.9532")


def taker_fee(px: D) -> D:
    return TS.TAKER_THETA * px * (D(1) - px)


def walk(levels, need: D):
    """VWAP over DISPLAYED depth, or None when the ladder is short."""
    got, cost = D(0), D(0)
    for lvl in levels:
        px, qty = D(str(lvl[0])), D(str(lvl[1]))
        take = min(qty, need - got)
        if take <= 0:
            break
        got += take
        cost += take * px
        if got >= need:
            break
    if got < need:
        return None
    return cost / got


def samples():
    for f in sorted(glob.glob(str(ROOT / TAPES))):
        win = pathlib.Path(f).stem
        rows = []
        for line in open(f):
            r = json.loads(line)
            if not str(r.get("label", "")).endswith("RESIDUAL_BOOK_SAMPLE"):
                continue
            b = json.loads(r["body"])
            if b.get("shadow_variant") != VARIANT:
                continue
            resid = D(str(b.get("residual") or 0))
            if resid == 0:
                continue
            bk = ((b.get("book") or {}).get("row") or {})
            bids, asks = bk.get("bids") or [], bk.get("asks") or []
            if not bids or not asks:
                continue
            reach = (b.get("reachability") or {})
            mq = reach.get("maker_quote") or {}
            behind = reach.get("maker_quote_behind_touch") or {}
            rows.append({
                "t": float(b["t"]), "held": str(b.get("held_side") or ""),
                "need": abs(resid),
                "bids": bids, "asks": asks,
                "bid": D(str(bids[0][0])), "ask": D(str(asks[0][0])),
                "queue": D(str(mq.get("queue_ahead_bound") or 0)),
                "behind_own": (D(str(behind["own_px"]))
                               if behind.get("own_px") else None),
            })
        if rows:
            rows.sort(key=lambda r: r["t"])
            yield win, rows


def score(r):
    """The four actions, each as incremental expected net versus HOLDING."""
    held, need = r["held"].upper(), r["need"]
    bid, ask = r["bid"], r["ask"]
    mid = (bid + ask) / D(2)
    # wire is YES/UP. own(UP) = wire, own(DOWN) = 1 - wire.
    p_held = mid if held == "UP" else (D(1) - mid)
    want = "DOWN" if held == "UP" else "UP"

    # 1. MAKER completion, at the price the engine actually quotes (behind
    #    the touch) when the tape carries it, else at the join.
    x_maker = r["behind_own"]
    if x_maker is None:
        x_maker = bid if want == "UP" else (D(1) - ask)
    inc_maker = need * (D(1) - x_maker - p_held)          # no maker rebate

    # 2. MARKETABLE completion: buying UP lifts the ASK ladder; buying DOWN
    #    sells the YES token INTO the BID ladder, so own cost is 1 - vwap.
    if want == "UP":
        v = walk(r["asks"], need)
        x_take = None if v is None else v
    else:
        v = walk(r["bids"], need)
        x_take = None if v is None else (D(1) - v)
    inc_take = (None if x_take is None
                else need * (D(1) - x_take - p_held) - need * taker_fee(
                    x_take if want == "UP" else (D(1) - x_take)))

    # 3. REDUCE the held leg: sell what we hold, which also crosses. Selling
    #    UP hits the BID; selling DOWN buys back the YES token at the ASK.
    if held == "UP":
        v2 = walk(r["bids"], need)
        exit_own = None if v2 is None else v2
    else:
        v2 = walk(r["asks"], need)
        exit_own = None if v2 is None else (D(1) - v2)
    inc_reduce = (None if exit_own is None
                  else need * (exit_own - p_held) - need * taker_fee(
                      exit_own if held == "UP" else (D(1) - exit_own)))

    # 4. HOLD is the baseline: incremental zero by construction.
    pair_maker = None if x_maker is None else (x_maker + p_held)
    return {
        "p_held": str(round(p_held, 4)),
        "spread": str(ask - bid),
        "x_maker_own": str(x_maker),
        "inc_maker_usd": str(round(inc_maker, 5)),
        "x_take_own": None if x_take is None else str(round(x_take, 4)),
        "inc_marketable_usd": (None if inc_take is None
                               else str(round(inc_take, 5))),
        "inc_reduce_usd": (None if inc_reduce is None
                           else str(round(inc_reduce, 5))),
        "queue_multiple": str(round(r["queue"] / need, 1)) if need else None,
        "queue_reachable": bool(r["queue"] <= QUEUE_MULT * need),
        # what the DEPLOYED threshold would say, for the comparison
        "pair_vs_p": None if pair_maker is None else str(round(pair_maker, 4)),
        "refused_by_fixed_threshold": (
            None if pair_maker is None else bool(pair_maker > BREAKEVEN)),
    }


def main() -> int:
    out = {}
    for win, rows in samples():
        scored = [dict(score(r), t=round(r["t"] - rows[0]["t"], 1)) for r in rows]
        def s(k):
            return [D(x[k]) for x in scored if x.get(k) is not None]
        maker_pos = [x for x in scored if D(x["inc_maker_usd"]) > 0]
        maker_pos_reach = [x for x in maker_pos if x["queue_reachable"]]
        take_pos = [x for x in scored
                    if x["inc_marketable_usd"] is not None
                    and D(x["inc_marketable_usd"]) > 0]
        red_pos = [x for x in scored
                   if x["inc_reduce_usd"] is not None
                   and D(x["inc_reduce_usd"]) > 0]
        # the misclassification the fixed threshold causes
        wrongly_refused = [x for x in scored
                           if x.get("refused_by_fixed_threshold")
                           and D(x["inc_maker_usd"]) > 0]
        out[win] = {
            "samples": len(scored),
            "maker_beats_hold": len(maker_pos),
            "maker_beats_hold_and_queue_reachable": len(maker_pos_reach),
            "sum_inc_maker_when_positive_usd": str(
                round(sum(D(x["inc_maker_usd"]) for x in maker_pos), 4)),
            "sum_inc_maker_positive_and_reachable_usd": str(
                round(sum(D(x["inc_maker_usd"]) for x in maker_pos_reach), 4)),
            "marketable_beats_hold": len(take_pos),
            "marketable_median_inc_usd": (
                str(sorted(s("inc_marketable_usd"))[len(s("inc_marketable_usd")) // 2])
                if s("inc_marketable_usd") else None),
            "reduce_beats_hold": len(red_pos),
            "reduce_median_inc_usd": (
                str(sorted(s("inc_reduce_usd"))[len(s("inc_reduce_usd")) // 2])
                if s("inc_reduce_usd") else None),
            "refused_by_the_0_9532_threshold": sum(
                1 for x in scored if x.get("refused_by_fixed_threshold")),
            "refused_by_threshold_but_BEATS_HOLDING": len(wrongly_refused),
            "of_those_queue_reachable": sum(
                1 for x in wrongly_refused if x["queue_reachable"]),
            "examples": scored[:3],
        }
    print(json.dumps({
        "windows": out,
        "baseline": ("HOLDING. incremental = q * (1 - x - p). The sunk "
                     "first-leg cost cancels, which is why no inventory "
                     "cost is invented."),
        "labels": {
            "p": "observed mid of the held side; never modelled",
            "maker": ("an OPPORTUNITY, not a fill. queue_multiple is "
                      "reported on every row and no row here is called a "
                      "fill."),
            "marketable": ("VWAP over DISPLAYED ladder depth, refused when "
                           "the ladder is short of the need; displayed "
                           "depth is displayed, not guaranteed"),
            "fees": ("TAKER_THETA 0.0695 * px * (1-px) per contract on any "
                     "crossing leg. The maker rebate was credited $0.0000 "
                     "across 55 live fills and is counted as zero."),
            "inventory": "modelled shadow inventory, not venue positions",
        },
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
