#!/usr/bin/env python3
"""P3: rank active Kalshi incentive candidates by the MOST GENEROUS q1 payout
the public book can support, and compare it to the cost of carrying q1.

The bound is deliberately generous, so a candidate that fails it cannot pass a
realistic model:
  * the ENTIRE period pool is assumed payable to the single side we rest on
    (in reality it is shared across both sides and across price levels);
  * the only competitors counted are those already resting AT that touch
    (deeper qualifying levels and arriving competitors are ignored);
  * no decay for the fraction of the period we are actually resting.

TWO CEILINGS, NOT ONE. An earlier revision of this script applied only the
pro-rata ceiling, and therefore reported $447/period for a market whose
thinner touch held 0.34 contracts -- it read an EMPTY book as a free pool.
It is not: the program scales payout by how much of target_size_fp is
actually supplied, so one contract against a 1000-contract target earns at
most a thousandth of the pool no matter how little competition there is.
The bound is the MINIMUM of:
    pro-rata   = pool * q / (q + competitors at touch)
    supply-cap = pool * q / target_size_fp
Where the book is thin the supply cap binds; where it is deep the pro-rata
term binds. Reporting either alone overstates the opportunity.
Kalshi's published allocation is spread- and depth-weighted; we do not have
its exact coefficients, so UPPER BOUND is the honest label, not an estimate.

Evidence classes: pool/target/discount/book = SOURCE-OBSERVED.
q1 bound = MODELED (from SOURCE-OBSERVED inputs, UNVERIFIED reward units).
Our eligibility and any credited reward = ACCOUNT-OBSERVED, unobtainable here.
"""
import json, sys, urllib.request, os
from datetime import datetime, timezone
from decimal import Decimal as D

B = "https://api.elections.kalshi.com/trade-api/v2"
SRC = "data/kalshi/census/incentive_programs.json"
OUT = "analysis/l5/kalshi/CANDIDATE_RANKING.json"
# UNVERIFIED: see INCENTIVE_CENSUS.json unit_hypothesis.
CENTICENTS = D(10000)


def get(u):
    r = urllib.request.urlopen(
        urllib.request.Request(u, headers={"Accept": "application/json"}), timeout=30)
    return json.load(r)


def ts(d):
    return datetime.fromisoformat(d.replace("Z", "+00:00")) if d else None


def main(limit=120):
    blob = json.load(open(SRC))
    now = datetime.now(timezone.utc)
    act = [x for x in blob["programs"]
           if ts(x.get("start_date")) and ts(x.get("end_date"))
           and ts(x["start_date"]) <= now < ts(x["end_date"])]
    act.sort(key=lambda x: -int(x.get("period_reward") or 0))

    rows, errs = [], 0
    for prog in act[:limit]:
        t = prog["market_ticker"]
        try:
            ob = get(f"{B}/markets/{t}/orderbook?depth=10")["orderbook_fp"]
        except Exception as e:
            errs += 1
            continue
        yes = [(D(a), D(s)) for a, s in (ob.get("yes_dollars") or [])]
        no = [(D(a), D(s)) for a, s in (ob.get("no_dollars") or [])]
        if not yes or not no:
            continue
        yb = max(a for a, _ in yes)
        nb = max(a for a, _ in no)
        y_touch = sum(s for a, s in yes if a == yb)
        n_touch = sum(s for a, s in no if a == nb)
        # Generous: pick the THINNER touch -- the side where one contract is
        # the largest fraction of the competition.
        thin = min(y_touch, n_touch)
        pool = D(prog["period_reward"]) / CENTICENTS
        days = D(str((ts(prog["end_date"]) - ts(prog["start_date"])).total_seconds() / 86400))
        tgt = D(prog["target_size_fp"])
        pro_rata = pool / (thin + 1)
        supply_cap = pool / tgt if tgt else pool
        bound = min(pro_rata, supply_cap)
        binding = "supply_cap" if supply_cap < pro_rata else "pro_rata"
        rows.append({
            "market_ticker": t,
            "series": t.split("-", 1)[0],
            "pool_usd_period_MODELED": str(pool),
            "period_days_SOURCE": str(round(days, 3)),
            "target_size_fp_SOURCE": prog["target_size_fp"],
            "discount_factor_bps_SOURCE": prog["discount_factor_bps"],
            "yes_bid_SOURCE": str(yb),
            "yes_ask_SOURCE": str(1 - nb),
            "spread_SOURCE": str((1 - nb) - yb),
            "touch_size_yes_SOURCE": str(y_touch),
            "touch_size_no_SOURCE": str(n_touch),
            "q1_pro_rata_ceiling_usd_MODELED": str(pro_rata),
            "q1_supply_cap_ceiling_usd_MODELED": str(supply_cap),
            "binding_ceiling": binding,
            "q1_payout_UPPER_BOUND_usd_per_period_MODELED": str(bound),
            "eligibility": "ACCOUNT-OBSERVED-UNOBTAINABLE",
            "credited_reward": "ACCOUNT-OBSERVED-UNOBTAINABLE",
        })
    rows.sort(key=lambda r: D(r["q1_payout_UPPER_BOUND_usd_per_period_MODELED"]), reverse=True)
    out = {
        "derived_from": {"path": SRC, "source_url": blob["source"],
                         "fetched_utc": blob["fetched_utc"]},
        "books_fetched_utc": now.isoformat(),
        "active_programs_total": len(act),
        "programs_examined": limit,
        "orderbook_fetch_errors": errs,
        "bound_definition": "min(pro-rata ceiling, supply-cap ceiling). Entire period "
                            "pool assumed payable to one side; competitors = resting size "
                            "at the thinner touch only; no time decay. UPPER BOUND.",
        "candidates": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"examined={limit} ranked={len(rows)} errors={errs}")
    for r in rows[:15]:
        print(f"  {r['market_ticker'][:52]:52s} pool=${r['pool_usd_period_MODELED']:>6s} "
              f"thin_touch={min(D(r['touch_size_yes_SOURCE']),D(r['touch_size_no_SOURCE'])):>10} "
              f"q1<=${D(r['q1_payout_UPPER_BOUND_usd_per_period_MODELED']):.4f} "
              f"({r['binding_ceiling']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 120))
