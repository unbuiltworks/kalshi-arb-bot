"""Replacement eligibility across all 284 recorded live samples.

Answers, per window: how many samples would produce an ELIGIBLE
replacement, how many replacements the policy would actually ATTEMPT (it
only acts when the quote has drifted, so eligibility is not attempts), what
the replacement costs in cash obligation, and what it does to the worst-case
residual loss.

NO FILL IS CLAIMED ANYWHERE. A replacement is an offer at a price that CAN
trade; the venue publishes no per-order queue position.
"""
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_two_sided_probe as TS            # noqa: E402

TAPE = "analysis/l5/completion_requote/tape_20261028.jsonl"
TICK = D("0.01")

#: realised basis per window, from the venue's settlement records, and the
#: order the engine actually rested while the residual was unmatched
W = {
    "cpc-btc-updown-15m-2026-10-28-1215z": dict(
        held="UP", hq="4.21", hc="1.5547", dq="1.92", dc="0.7488",
        cq="2.29", rest="0.47", t0=1793189700.0),
    "cpc-btc-updown-15m-2026-10-28-1230z": dict(
        held="UP", hq="4.79", hc="1.1972", dq="2.38", dc="1.4518",
        cq="2.41", rest="0.37", t0=1793190600.0),
    "cpc-btc-updown-15m-2026-10-28-1245z": dict(
        held="UP", hq="2.27", hc="0.7264", dq="0", dc="0",
        cq="2.27", rest="0.63", t0=1793191500.0),
}


def main():
    rows = [json.loads(l) for l in open(TAPE)]
    tot_elig = tot_att = tot_n = 0
    print("%-14s %7s %9s %9s %8s %10s %12s"
          % ("window", "samples", "eligible", "attempts", "maker", "cash d$",
             "worst d$"))
    for slug, c in W.items():
        side = "DOWN" if c["held"] == "UP" else "UP"
        samples = [r for r in rows if r["slug"] == slug]
        px = D(c["rest"])
        n = elig = att = 0
        maker_all = True
        cash0 = cashN = None
        for r in samples:
            if not r["bids"] or not r["asks"]:
                continue
            n += 1
            d = TS.completion_requote(
                side=side, resting_own_px=str(px), resting_qty=c["cq"],
                resting_filled_qty="0",
                bid=r["bids"][0][0], ask=r["asks"][0][0],
                tick="0.01", min_qty="0.01",
                held_qty=c["hq"], held_cost_usd=c["hc"],
                deficient_qty=c["dq"], deficient_cost_usd=c["dc"],
                residual_budget_usd="3.00",
                now_s=r["t"], window_start_s=c["t0"],
                window_end_s=c["t0"] + 900.0, book_age_s="0.05")
            # ELIGIBLE: a permissible dearer price exists at this book,
            # whether or not our current quote needs moving
            probe = TS.completion_requote(
                side=side, resting_own_px="0.05", resting_qty=c["cq"],
                resting_filled_qty="0",
                bid=r["bids"][0][0], ask=r["asks"][0][0],
                tick="0.01", min_qty="0.01",
                held_qty=c["hq"], held_cost_usd=c["hc"],
                deficient_qty=c["dq"], deficient_cost_usd=c["dc"],
                residual_budget_usd="3.00",
                now_s=r["t"], window_start_s=c["t0"],
                window_end_s=c["t0"] + 900.0, book_age_s="0.05")
            if probe["act"]:
                elig += 1
            if d["act"]:
                att += 1
                new = D(d["to_price"])
                # maker-only: the target must never cross the live spread
                if TS.leg_crosses(side, new, bid=D(r["bids"][0][0]),
                                  ask=D(r["asks"][0][0])):
                    maker_all = False
                if cash0 is None:
                    cash0 = px * D(c["cq"])
                px = new
                cashN = px * D(c["cq"])
        # cash obligation: the replacement is dearer, so it commits more
        dcash = (cashN - cash0) if (cash0 is not None) else D(0)
        # worst case if the replacement FILLS vs if the residual strands
        sunk = D(c["hc"]) + D(c["dc"])
        unmatched = D(c["hq"]) - D(c["dq"])
        strand = min(D(c["hq"]), D(c["dq"])) - sunk
        if cashN is not None:
            cost = sunk + px * unmatched + TS.fee(TS.TAKER_THETA, unmatched, px)
            after = min(D(c["hq"]), D(c["dq"]) + unmatched) - cost
            dworst = after - strand
        else:
            dworst = D(0)
        print("%-14s %7d %9d %9d %8s %10s %12s"
              % (slug[-5:], n, elig, att, ("yes" if maker_all else "NO"),
                 dcash.quantize(D("0.0001")), dworst.quantize(D("0.0001"))))
        tot_n += n
        tot_elig += elig
        tot_att += att
    print("-" * 78)
    print("TOTAL samples %d   eligible %d   attempts %d"
          % (tot_n, tot_elig, tot_att))


if __name__ == "__main__":
    main()
