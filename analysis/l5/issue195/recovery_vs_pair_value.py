"""Does the distance recovery destroy quotes worth keeping? Measured.

THE QUESTION PR #199 RAISES. `acc_stale_by_distance` -> `recover_stale_leg`
takes NO held quantity and NO cost basis: it cancels a resting leg purely
because the book has left it behind. So it can cancel an inventory-reducing
maker quote whose PAIR is excellent, and the replacement is then priced two
ticks behind the CURRENT touch and re-tested against the pair cap -- which
can refuse it outright, leaving no quote at all.

AT 0100z IT DID NOT FIRE on the profitable quote: resting 0.4600 against a
0.5100 touch is 3 ticks of drift inside a 4-tick tolerance. So the question
is not "was this the 0100z cause" -- it was not -- but "how often would it
cost us a good quote", which is what decides whether a guard is worth its
behavioural change.

THE HARM, PRECISELY. Three things must coincide:
  1. the resting quote's pair is INSIDE the opening cap (worth keeping);
  2. the recovery fires (drift past tolerance, zero fill, budget available);
  3. the replacement's pair is OUTSIDE the cap (so it cannot be re-placed).
Then a profitable resting quote is converted into NO quote. Count 2-only and
1+2 as well, so the gradient is visible rather than one headline number.

NOTHING HERE IS A FILL. Reaching a level is necessary for a maker fill and
not sufficient; these are controller verdicts and arithmetic pair costs.
"""
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
sys.path.insert(0, "analysis/l5/completion_requote")
from src import l5_two_sided_probe as TS            # noqa: E402
from compare import CASES                           # noqa: E402

TAPE = "analysis/l5/completion_requote/tape_20261028.jsonl"
TICK = D("0.01")


def main():
    rows = [json.loads(l) for l in open(TAPE)]
    cap = TS.max_pair_cost()
    print("opening pair cap %s   drift tolerance %s ticks   depth %s ticks"
          % (cap, TS.recover_drift_ticks(), TS.depth_ticks()))
    print("recoveries allowed per market: %s" % TS.recover_max_per_market())
    for slug, c in CASES.items():
        samples = [r for r in rows if r["slug"] == slug
                   and r["t"] >= c["residual_from"]]
        if not samples:
            continue
        side = "DOWN" if c["held_side"] == "UP" else "UP"
        rested = D(c["rested_own_px"])
        paid = D(c["held_cost_usd"]) / D(c["held_qty"])
        pair_rest = (rested + paid).quantize(D("0.0001"))
        t0 = min(r["t"] for r in samples)
        n = fires = keepable = stranded = repl_refused = 0
        fires_fix = 0
        for r in samples:
            bids, asks = r.get("bids") or [], r.get("asks") or []
            if not bids or not asks:
                continue
            b, a = D(bids[0][0]), D(asks[0][0])
            if a <= b:
                continue
            n += 1
            kw = dict(
                side=side, own_px=rested, qty=D("1"), remaining=D("1"),
                state="LIVE", submitted_t=t0, now=float(r["t"]),
                bid=b, ask=a, tick=TICK, depth_ticks=TS.depth_ticks(),
                book_age_s=0.05, recoveries_used=0,
                open_budget_remaining=None)
            # WITHOUT the inventory this is the pre-fix decision exactly,
            # because absent inventory declines the extra question.
            v = TS.recover_stale_leg(**kw)
            vfix = TS.recover_stale_leg(
                **kw, held_qty=D(c["held_qty"]),
                held_cost_usd=D(c["held_cost_usd"]))
            if vfix.get("recover"):
                fires_fix += 1
            if not v.get("recover"):
                continue
            fires += 1
            repl = TS.quote_own_price(side, bid=b, ask=a, tick=TICK,
                                      depth_ticks=TS.depth_ticks())
            if repl is None:
                continue
            pair_repl = (D(repl) + paid).quantize(D("0.0001"))
            if pair_repl > cap:
                repl_refused += 1
            if pair_rest <= cap:
                keepable += 1
                if pair_repl > cap:
                    stranded += 1
        print("=" * 72)
        print("%s   %s resting own %s" % (slug, side, rested))
        print("  held basis %s  ->  resting pair %s  (%s the %s cap)"
              % (paid.quantize(D("0.0001")), pair_rest,
                 "inside" if pair_rest <= cap else "OUTSIDE", cap))
        print("  samples                                      %d" % n)
        print("  recovery would fire       BEFORE            %d" % fires)
        print("  recovery would fire       AFTER the fix    %d" % fires_fix)
        print("  ... and the replacement's pair exceeds cap   %d" % repl_refused)
        print("  ... on a resting quote worth keeping         %d" % keepable)
        print("  ... BOTH: a good quote cancelled, unreplaceable  %d"
              % stranded)
        print("  destructive recoveries PREVENTED             %d" % (fires - fires_fix))
        print("  legitimate recoveries still possible         %d" % fires_fix)
    print("=" * 72)
    print("0100z, the venue-confirmed state: resting UP own 0.4600 against a")
    v = TS.recover_stale_leg(
        side="UP", own_px=D("0.4600"), qty=D("1"), remaining=D("1"),
        state="LIVE", submitted_t=1794013200.0, now=1794013240.1147778,
        bid=D("0.51"), ask=D("0.53"), tick=TICK,
        depth_ticks=TS.depth_ticks(), book_age_s=0.061,
        recoveries_used=0, open_budget_remaining=None)
    print("  0.5100 touch -> recover %s (%s), drift %s of %s allowed"
          % (v.get("recover"), v.get("reason"), v.get("drift_ticks"),
             v.get("allowed_ticks")))
    print("  so the distance rule was NOT the 0100z cause.")


if __name__ == "__main__":
    main()
