"""What could actually have been done about a stranded leg, instant by instant.

READ-ONLY. Parses `L5_RESIDUAL_BOOK_SAMPLE` rows out of Railway deploy-log
dumps. No socket, no credential, no order body.

THE QUESTION. For a window where a first leg filled and the complement never
arrived, the engine sampled the real book every 5 s for as long as the
inventory stayed unmatched. Those rows carry the ACTUAL filled quantity and
the ACTUAL venue book, so the counterfactual does not need a model of the
market -- only a decision rule applied to what was on the wire.

WHAT COUNTS AS AVAILABLE, AND WHAT DOES NOT.

  SELL the held side      requires a BID on the held side. Priced at the
                          displayed bid, walked across rungs for our size.
  TAKER completion        requires displayed depth on the side we must cross.
                          The engine already computed `taker_walk` for the
                          exact quantity needed; a row flagged
                          `insufficient_displayed_depth` is NOT available.
  MAKER completion        reported, NEVER counted as available. The venue
                          publishes no per-order queue position, so a touch
                          at a price is not a fill at that price, and
                          `queue_ahead_bound` says how much is in front.

That asymmetry is the whole point. A maker-inclusive counterfactual can
always "fix" a strand by assuming the fill it never got, which is how a
replication programme talks itself into a policy the venue will not honour.

THE ECONOMICS. One share pays exactly $1 on one side and $0 on the other, so
for a held leg of `held_qty` at own cost `held_px`:

    pair own cost       = held_px + complement_own_px
    settled P&L if pair = (1 - pair own cost) * matched_qty
    settled P&L if held = held_qty * (1 if held side wins else 0) - acquisition

A pair below $1 is locked regardless of outcome; a pair above $1 is a CERTAIN
loss, and completing into one converts an uncertain loss into a sure one.
Both are reported, because "completion" is only an improvement when the
arithmetic says so.

Fees are charged at the TAKER rate on the completing leg and never netted
against a maker rebate.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Optional

LABEL = "L5_RESIDUAL_BOOK_SAMPLE"

#: taker Theta, the same conservative figure the deployed gates use
FEE_THETA_TAKER = D("0.0695")


def _d(v: Any) -> Optional[D]:
    try:
        if v is None:
            return None
        return D(str(v))
    except (TypeError, ValueError, ArithmeticError):
        return None


def taker_fee(qty: D, px: D) -> D:
    """Theta * C * p * (1-p), rounded UP to the cent. No rebate netted."""
    from decimal import ROUND_CEILING
    if px < 0 or px > 1:
        px = D("1")
    raw = FEE_THETA_TAKER * qty * px * (D(1) - px)
    if raw <= 0:
        return D(0)
    return raw.quantize(D("0.01"), rounding=ROUND_CEILING)


def iter_samples(paths: Iterable[str], slug: Optional[str] = None
                 ) -> Iterable[dict]:
    seen = set()
    for p in paths:
        try:
            doc = json.load(open(p))
        except (OSError, ValueError) as exc:
            print("skip %s: %s" % (p, exc), file=sys.stderr)
            continue
        for row in (doc.get("deploy") or []):
            msg = str(row.get("message") or "")
            if LABEL not in msg:
                continue
            try:
                d = json.loads(msg.split(" ", 1)[1])
            except (IndexError, ValueError):
                continue
            if slug and str(d.get("slug") or "") != slug:
                continue
            key = (d.get("slug"), d.get("t"))
            if key in seen:
                continue
            seen.add(key)
            yield d


def _walk_bids(bids: List[List[str]], need: D) -> Dict[str, Any]:
    """Proceeds from selling `need` into the DISPLAYED bid ladder."""
    got = D(0)
    cash = D(0)
    levels = 0
    for lvl in bids or []:
        px, qty = _d(lvl[0]), _d(lvl[1])
        if px is None or qty is None:
            continue
        take = min(qty, need - got)
        if take <= 0:
            break
        cash += take * px
        got += take
        levels += 1
        if got >= need:
            break
    return {"filled_qty": got, "proceeds": cash, "levels": levels,
            "complete": got >= need,
            "vwap": (cash / got) if got > 0 else None}


def analyse(samples: Iterable[dict], *, held_px: D, held_qty: D,
            held_side_wins: bool) -> Dict[str, Any]:
    """Every instant's available actions, and the best one.

    `held_side_wins` is the SETTLED outcome, known only after the fact. It is
    used to score the do-nothing branch truthfully; no decision rule here is
    allowed to see it.
    """
    acquisition = (held_px * held_qty).quantize(D("0.0001"))
    actual = (held_qty - acquisition) if held_side_wins else -acquisition

    rows: List[Dict[str, Any]] = []
    for d in samples:
        row = (d.get("book") or {}).get("row") or {}
        reach = d.get("reachability") or {}
        tw = reach.get("taker_walk") or {}
        mk = reach.get("maker_quote") or {}
        mkb = reach.get("maker_quote_behind_touch") or {}
        resid = _d(d.get("residual")) or D(0)
        need = abs(resid) if resid else held_qty

        # --- taker completion, only when the engine says depth was there
        t_px = _d(tw.get("vwap_own_px"))
        t_ok = (not bool(tw.get("insufficient_displayed_depth"))
                and t_px is not None)
        t_pair = (held_px + t_px) if t_ok else None
        t_pnl = None
        if t_pair is not None:
            matched = min(need, held_qty)
            fee = taker_fee(matched, t_px)
            t_pnl = ((D(1) - t_pair) * matched - fee).quantize(D("0.0001"))

        # --- sell the held side into displayed bids
        sell = _walk_bids(row.get("bids") or [], need)
        s_pnl = None
        if sell["filled_qty"] > 0:
            fee = taker_fee(sell["filled_qty"], sell["vwap"] or D(0))
            # proceeds now, plus whatever the unsold remainder settles to
            unsold = held_qty - sell["filled_qty"]
            settle_rest = unsold if held_side_wins else D(0)
            s_pnl = (sell["proceeds"] + settle_rest - acquisition
                     - fee).quantize(D("0.0001"))

        rows.append({
            "t": d.get("t"),
            "residual": str(resid), "held_side": d.get("held_side"),
            "order_resting": bool(d.get("order_resting")),
            "n_bids": row.get("n_bids"), "n_asks": row.get("n_asks"),
            "taker_available": t_ok,
            "taker_complement_own_px": None if t_px is None else str(t_px),
            "taker_pair_own_cost": None if t_pair is None else str(t_pair),
            "taker_settled_pnl": None if t_pnl is None else str(t_pnl),
            "sell_available": sell["filled_qty"] > 0,
            "sell_complete": sell["complete"],
            "sell_vwap": None if sell["vwap"] is None else str(sell["vwap"]),
            "sell_settled_pnl": None if s_pnl is None else str(s_pnl),
            # reported, never counted as available
            "maker_join_own_px": mk.get("own_px"),
            "maker_queue_ahead_multiple_of_need":
                mk.get("queue_ahead_multiple_of_need"),
            "maker_behind_touch_own_px": mkb.get("own_px"),
        })

    def best(key: str):
        vals = [(D(r[key]), r["t"]) for r in rows if r.get(key) is not None]
        if not vals:
            return None, None
        v, t = max(vals, key=lambda kv: kv[0])
        return str(v), t

    bt, bt_t = best("taker_settled_pnl")
    bs, bs_t = best("sell_settled_pnl")
    sub1 = [r for r in rows if r["taker_pair_own_cost"] is not None
            and D(r["taker_pair_own_cost"]) < 1]
    return {
        "label": "L5_STRAND_COMPLETION_COUNTERFACTUAL",
        "held": {"side_qty": str(held_qty), "own_px": str(held_px),
                 "acquisition_usd": str(acquisition),
                 "held_side_won": held_side_wins},
        "actual_settled_pnl_usd": str(actual),
        "samples": len(rows),
        "instants_with_no_bid_on_held_side":
            sum(1 for r in rows if (r["n_bids"] or 0) == 0),
        "instants_taker_available": sum(1 for r in rows if r["taker_available"]),
        "instants_sell_available": sum(1 for r in rows if r["sell_available"]),
        "instants_with_sub_1_pair_by_taker": len(sub1),
        "best_taker_settled_pnl_usd": bt, "best_taker_t": bt_t,
        "best_sell_settled_pnl_usd": bs, "best_sell_t": bs_t,
        "improvement_vs_actual_usd": (
            None if bt is None and bs is None else
            str(max(D(x) for x in (bt, bs) if x is not None) - D(actual))),
        "maker_queue_ahead_multiple_min": min(
            [r["maker_queue_ahead_multiple_of_need"] for r in rows
             if r.get("maker_queue_ahead_multiple_of_need")] or [None],
            key=lambda v: D(v) if v else D("9" * 12)),
        "labels": ("MAKER completion is reported and NEVER counted as "
                   "available: the venue publishes no per-order queue "
                   "position, so a touch is not a fill. A pair own cost "
                   "above $1 is a CERTAIN loss, so completing into one "
                   "converts an uncertain loss into a sure one. Taker fees "
                   "charged at Theta, rounded up, no rebate netted."),
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) < 5:
        print(__doc__)
        print("usage: python3 -m src.l5_strand_completion_counterfactual "
              "<slug> <held_px> <held_qty> <held_side_won:0|1> <dump.json>...",
              file=sys.stderr)
        return 2
    slug, px, qty, won = a[0], D(a[1]), D(a[2]), bool(int(a[3]))
    out = analyse(iter_samples(a[4:], slug=slug),
                  held_px=px, held_qty=qty, held_side_wins=won)
    rows = out.pop("rows")
    print(json.dumps(out, indent=1, default=str))
    print("# rows: %d (suppressed; pass --rows to print)" % len(rows),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
