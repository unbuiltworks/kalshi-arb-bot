"""Current vs proposed completion quoting, on the recorded live books.

OWN-PRICE SEMANTICS. A dearer own price is closer to the touch. For a DOWN
completion the dearest non-crossing own price is 1 - best_ask; for an UP
completion it is best_bid. Resting `depth_ticks` behind means resting
CHEAPER in own terms, which is further from the market.

WHEN A RESTING QUOTE AT OWN p CAN FILL. Our order sits at wire 1-p (DOWN)
or p (UP), behind the touch by (own_touch - p). It becomes the front of its
side only when the touch comes DOWN to us, i.e. own_touch(t) <= p at some
later sample. That is a NECESSARY condition and not a sufficient one --
the venue publishes no per-order queue position, so reaching the level is
not a fill. Reported as "level reached", never as "would have filled".
"""
import json
import sys
from decimal import Decimal as D

TICK = D("0.01")
TAPE = "analysis/l5/completion_requote/tape_20261028.jsonl"

sys.path.insert(0, ".")
from src import l5_two_sided_probe as TS            # noqa: E402

#: the realised basis of the held side at the moment the residual existed,
#: and the order the engine actually rested, from the venue's own records
CASES = {
    "cpc-btc-updown-15m-2026-10-28-1215z": dict(
        held_side="UP", held_qty="4.21", held_cost_usd="1.5547",
        deficient_qty="1.92", deficient_cost_usd="0.7488",
        complete_qty="2.29", residual_budget_usd="3.00",
        rested_own_px="0.47", rested_from=1793190233.0, rested_to=1793190289.0,
        residual_from=1793190166.0),
    "cpc-btc-updown-15m-2026-10-28-1245z": dict(
        held_side="UP", held_qty="2.27", held_cost_usd="0.7264",
        deficient_qty="0", deficient_cost_usd="0",
        complete_qty="2.27", residual_budget_usd="3.00",
        rested_own_px="0.63", rested_from=1793191948.0, rested_to=1793191970.0,
        residual_from=1793191659.0),
}


def own_touch(completing, bids, asks):
    if not bids or not asks:
        return None
    best_bid, best_ask = D(bids[0][0]), D(asks[0][0])
    return (best_bid if completing == "UP" else D(1) - best_ask)


def main():
    rows = [json.loads(l) for l in open(TAPE)]
    for slug, c in CASES.items():
        samples = [r for r in rows
                   if r["slug"] == slug and r["t"] >= c["residual_from"]]
        completing = "DOWN" if c["held_side"] == "UP" else "UP"
        ceil = TS.completion_ceiling(
            **{k: c[k] for k in ("held_side", "held_qty", "held_cost_usd",
                                 "deficient_qty", "deficient_cost_usd",
                                 "complete_qty", "residual_budget_usd")})
        cap = TS.max_pair_cost()
        paid = D(c["held_cost_usd"]) / D(c["held_qty"])
        rested = D(c["rested_own_px"])

        touches, behind, cur_ok, prop_ok, touch_ok_cap = [], [], 0, 0, 0
        for r in samples:
            t = own_touch(completing, r["bids"], r["asks"])
            if t is None:
                continue
            touches.append((r["t"], t))
            cur = t - 2 * TICK                      # today's quote, repriced
            behind.append(int((t - rested) / TICK))
            if paid + cur <= cap:
                cur_ok += 1
            if t <= (ceil or D(0)):
                prop_ok += 1
            if paid + t <= cap:
                touch_ok_cap += 1

        life = [(ts, t) for ts, t in touches
                if c["rested_from"] <= ts <= c["rested_to"]]
        after = [(ts, t) for ts, t in touches if ts >= c["rested_from"]]
        reached_life = any(t <= rested for _, t in life)
        reached_after = any(t <= rested for _, t in after)
        n = len(touches)
        print("=" * 72)
        print(slug)
        print("  samples with a residual        %d" % n)
        print("  realised basis of held leg     %s" % paid.quantize(D('0.0001')))
        print("  completion ceiling             %s   (opening cap %s)"
              % (ceil, cap))
        print("  own touch range                %s .. %s"
              % (min(t for _, t in touches), max(t for _, t in touches)))
        print("  -- the order the engine RESTED at own %s" % rested)
        print("     ticks behind the touch       min %d  median %d  max %d"
              % (min(behind), sorted(behind)[len(behind) // 2], max(behind)))
        print("     level reached during its life          %s (%d samples)"
              % (reached_life, len(life)))
        print("     level reached any time after placement %s" % reached_after)
        print("  -- quote ELIGIBILITY over the residual's life")
        print("     two-ticks-behind permitted by opening cap  %d/%d" %
              (cur_ok, n))
        print("     AT THE TOUCH permitted by opening cap      %d/%d" %
              (touch_ok_cap, n))
        print("     AT THE TOUCH permitted by completion ceil  %d/%d" %
              (prop_ok, n))


if __name__ == "__main__":
    main()


# ===================== policy simulation over the recorded books
#
# NO FILL IS EVER CLAIMED. The venue publishes no per-order queue position,
# so the only honest execution measure available from a book tape is whether
# our quote was AT the touch -- a price that can trade -- or behind it, where
# it cannot trade until the market comes back. 1215z proves the distinction
# matters: its level was reached and it still did not fill.

def simulate():
    rows = [json.loads(l) for l in open(TAPE)]
    print()
    print("#" * 72)
    print("POLICY SIMULATION -- marketable time, not fills")
    print("#" * 72)
    for slug, c in CASES.items():
        samples = [r for r in rows
                   if r["slug"] == slug and r["t"] >= c["residual_from"]]
        completing = "DOWN" if c["held_side"] == "UP" else "UP"
        cap, rested = TS.max_pair_cost(), D(c["rested_own_px"])
        paid = D(c["held_cost_usd"]) / D(c["held_qty"])
        t1 = c["residual_from"] - (c["residual_from"] % 900) + 900

        cur_px, prop_px = rested, rested
        cur_mkt = prop_mkt = requotes = blocked_cap = 0
        n = 0
        for r in samples:
            t = own_touch(completing, r["bids"], r["asks"])
            if t is None:
                continue
            n += 1
            # current policy: the order is placed once and FROZEN
            if cur_px >= t:
                cur_mkt += 1
            # proposed: evaluate a requote on every sample
            d = TS.completion_requote(
                side=completing, resting_own_px=str(prop_px),
                resting_qty=c["complete_qty"], resting_filled_qty="0",
                bid=r["bids"][0][0], ask=r["asks"][0][0],
                tick="0.01", min_qty="0.01",
                held_qty=c["held_qty"], held_cost_usd=c["held_cost_usd"],
                deficient_qty=c["deficient_qty"],
                deficient_cost_usd=c["deficient_cost_usd"],
                residual_budget_usd=c["residual_budget_usd"],
                now_s=r["t"], window_start_s=t1 - 900, window_end_s=t1,
                book_age_s="0.05")
            if d["act"]:
                prop_px = D(d["to_price"])
                requotes += 1
            if prop_px >= t:
                prop_mkt += 1
            if paid + t > cap:
                blocked_cap += 1
        print("-" * 72)
        print(slug)
        print("  samples                                   %d" % n)
        print("  CURRENT  quote frozen at own %s" % rested)
        print("           samples AT the touch (marketable)  %d/%d  (%.0f%%)"
              % (cur_mkt, n, 100.0 * cur_mkt / n))
        print("  PROPOSED requote toward the touch")
        print("           samples AT the touch (marketable)  %d/%d  (%.0f%%)"
              % (prop_mkt, n, 100.0 * prop_mkt / n))
        print("           requotes issued                    %d" % requotes)
        print("           final quote own px                 %s" % prop_px)
        print("  touch above the OPENING cap in             %d/%d samples"
              % (blocked_cap, n))


if __name__ == "__main__":
    simulate()
