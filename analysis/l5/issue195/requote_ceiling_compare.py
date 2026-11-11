"""Original vs corrected requote controller, on recorded live order books.

WHAT IS COMPARED. One line of production code: the default of
`completion_max_pair()`, which bounds the PAIR a requote may buy.

    ORIGINAL   L5_TS_COMPLETION_MAX_PAIR unset -> 1.00 (the redemption line)
    CORRECTED  L5_TS_COMPLETION_MAX_PAIR unset -> max_pair_cost() (0.96)

Everything else is the deployed `l5_two_sided_probe.completion_requote`,
driven over `analysis/l5/completion_requote/tape_20261028.jsonl` -- real
authenticated ladders from two settled windows -- plus the venue-confirmed
2026-11-07 0100z state, where the defect cost $0.49.

WHAT IS NOT CLAIMED. Nothing here is a fill. Reaching a price level is a
NECESSARY condition for a maker fill and not a sufficient one: the venue
publishes no per-order queue position. Every figure below is a CONTROLLER
DECISION or an arithmetic pair cost. Counterfactual fills are not simulated
and shadow profitability is not asserted.
"""
import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_two_sided_probe as TS            # noqa: E402

TAPE = "analysis/l5/completion_requote/tape_20261028.jsonl"
TICK = D("0.01")
sys.path.insert(0, "analysis/l5/completion_requote")
from compare import CASES                           # noqa: E402


def _qa_fn(side, bids, asks):
    """Displayed size that must clear ahead of an own price `px`."""
    def qa(px):
        p = D(str(px))
        if side == "UP":                       # rests on the bid at wire p
            return sum((D(s) for pr, s in bids if D(pr) >= p), D(0))
        w = D(1) - p                           # DOWN rests on the ask
        return sum((D(s) for pr, s in asks if D(pr) <= w), D(0))
    return qa


def run(side, rested, held_qty, held_cost, deficient_qty, deficient_cost,
        budget, samples, ws, label):
    paid = D(held_cost) / D(held_qty)
    acts, targets, pairs = 0, [], []
    for r in samples:
        bids, asks = r.get("bids") or [], r.get("asks") or []
        if not bids or not asks:
            continue
        out = TS.completion_requote(
            side=side, resting_own_px=rested, resting_qty=D(1),
            resting_filled_qty=D(0),
            bid=D(bids[0][0]), ask=D(asks[0][0]),
            tick=TICK, min_qty=D("0.01"),
            held_qty=D(held_qty), held_cost_usd=D(held_cost),
            deficient_qty=D(deficient_qty),
            deficient_cost_usd=D(deficient_cost),
            residual_budget_usd=D(budget),
            now_s=float(r["t"]), window_start_s=float(ws),
            window_end_s=TS.window_end(float(ws)),
            book_age_s=0.05, gap_suspected=False, orders_uncertain=None,
            queue_ahead_fn=_qa_fn(side, bids, asks),
            require_queue_evidence=True, cancel_unresolved=None)
        if out.get("act"):
            acts += 1
            tgt = D(out["to_price"])
            targets.append(tgt)
            pairs.append((tgt + paid).quantize(D("0.0001")))
    return {"label": label, "samples": len(samples), "cancels": acts,
            "worst_pair": (max(pairs) if pairs else None),
            "best_pair": (min(pairs) if pairs else None),
            "mean_pair": ((sum(pairs) / len(pairs)).quantize(D("0.0001"))
                          if pairs else None),
            "zero_edge_cancels": sum(1 for p in pairs if p >= D("1.0000")),
            "over_opening_cap": sum(1 for p in pairs
                                    if p > TS.max_pair_cost())}


def both(**kw):
    os.environ["L5_TS_COMPLETION_MAX_PAIR"] = "1.00"
    orig = run(label="ORIGINAL  (pair bound 1.00)", **kw)
    os.environ.pop("L5_TS_COMPLETION_MAX_PAIR", None)
    fixed = run(label="CORRECTED (pair bound 0.96)", **kw)
    return orig, fixed


def show(orig, fixed, title):
    print("=" * 74)
    print(title)
    print("  recorded samples with a residual: %d" % orig["samples"])
    for r in (orig, fixed):
        print("  %-28s cancels %3d   pair: worst %s  mean %s"
              % (r["label"], r["cancels"], r["worst_pair"], r["mean_pair"]))
        print("  %-28s   of those, pair >= 1.0000 (zero edge): %d"
              % ("", r["zero_edge_cancels"]))
        print("  %-28s   of those, pair above the 0.96 opening cap: %d"
              % ("", r["over_opening_cap"]))
    d = orig["cancels"] - fixed["cancels"]
    print("  --> cancellations avoided: %d   (%.0f%% of the original)"
          % (d, (100.0 * d / orig["cancels"]) if orig["cancels"] else 0.0))


def main():
    rows = [json.loads(l) for l in open(TAPE)]
    for slug, c in CASES.items():
        samples = [r for r in rows if r["slug"] == slug
                   and r["t"] >= c["residual_from"]]
        ws = min(r["t"] for r in samples) - 60.0
        side = "DOWN" if c["held_side"] == "UP" else "UP"
        o, f = both(side=side, rested=D(c["rested_own_px"]),
                    held_qty=c["held_qty"], held_cost=c["held_cost_usd"],
                    deficient_qty=c["deficient_qty"],
                    deficient_cost=c["deficient_cost_usd"],
                    budget=c["residual_budget_usd"], samples=samples, ws=ws)
        show(o, f, "%s   held %s, rested own %s"
             % (slug, c["held_side"], c["rested_own_px"]))

    # ---------------- the venue-confirmed 0100z state, as a single sample
    ws = 1794013200.0
    syn = [{"t": 1794013240.1147778,
            "bids": [["0.51", "2923.02"]], "asks": [["0.53", "100"]]}]
    o, f = both(side="UP", rested=D("0.4600"), held_qty="1",
                held_cost="0.4900", deficient_qty="0", deficient_cost="0",
                budget="5", samples=syn, ws=ws)
    show(o, f, "cpc-btc-updown-15m-2026-11-07-0100z   held DOWN own 0.4900, "
               "rested UP own 0.4600  (VENUE-CONFIRMED)")
    print()
    print("REALIZED, for reference: that window completed no pair, settled a")
    print("residual 1 DOWN against a winning UP, and realized -$0.4900 net.")
    print("Whether the preserved 0.46 quote would have FILLED is UNKNOWN.")


if __name__ == "__main__":
    main()
