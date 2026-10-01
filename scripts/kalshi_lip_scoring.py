#!/usr/bin/env python3
"""P2 CORRECTION: q1 reward bounds under Kalshi's ACTUAL published LIP scoring.

Supersedes the min(pool/(1+touch), pool/target_size) bound in 98f4c955, which
is NOT a valid upper bound. See analysis/l5/kalshi/LIP_SCORING_CORRECTION.md.

Documented rules (help.kalshi.com/en/articles/13823851-liquidity-incentive-program):
  snapshot      once per second, at a random moment within the second
  Reference     walking DOWN from the best bid, the first price level at which
    Price       cumulative resting size reaches ONE FIFTH of Target Size
  multiplier    1.0 at or better than Reference Price; else DiscountFactor^ticks
  raw score     order size x distance multiplier
  share         order raw score / total raw score of qualifying orders THAT SIDE
  snapshot      share(yes) + share(no); at most 2.0
  exclusion     market not open, OR resting size fails Target Size on either side
  reward        TimePeriodScore x TimePeriodReward x (non-excluded / total),
                rounded down to the cent
  minimum       $1.00; below that nothing is paid

The decisive structural consequence, which the superseded bound missed: the
Reference Price sits only Target/5 deep, and EVERY order at or better than it
scores 1.0. So the smallest the denominator can be is ~Target/5 = 200, not
Target = 1000. A q1 order's ceiling is therefore ~5x what was reported.

TimePeriodScore normalization is UNKNOWN from the public article. If the pool
is conserved it must be the mean snapshot score divided by 2 (a participant
owning both sides entirely scores 1.0). We report BOTH readings; the /2 one is
conservative, the undivided one optimistic.
"""
import json, sys, os, urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D, getcontext

getcontext().prec = 28
B = "https://api.elections.kalshi.com/trade-api/v2"
SRC = "data/kalshi/census/incentive_programs.json"
OUT = "analysis/l5/kalshi/LIP_SCORING_BOUNDS.json"
CENTICENTS = D(10000)   # UNVERIFIED unit hypothesis
TICK = D("0.01")
MIN_PAYOUT = D("1.00")


def get(u):
    r = urllib.request.urlopen(
        urllib.request.Request(u, headers={"Accept": "application/json"}), timeout=30)
    return json.load(r)


def ts(d):
    return datetime.fromisoformat(d.replace("Z", "+00:00")) if d else None


def side_score(levels, target, df):
    """Total raw score for one side, plus the reference price.

    `levels` is [(price, size)] in that side's own price terms. Walking DOWN
    from the best bid means descending price. Returns (total_score, ref_price,
    size_at_or_above_ref, raw_size).
    """
    lv = sorted(levels, key=lambda x: -x[0])
    need = target / D(5)
    cum = D(0)
    ref = None
    for px, sz in lv:
        cum += sz
        if ref is None and cum >= need:
            ref = px
    raw = sum(sz for _, sz in lv)
    if ref is None:
        return None, None, D(0), raw          # book thinner than Target/5
    total = D(0)
    at_or_above = D(0)
    for px, sz in lv:
        if px >= ref:
            total += sz
            at_or_above += sz
        else:
            ticks = int((ref - px) / TICK)
            total += sz * (df ** ticks)
    return total, ref, at_or_above, raw


def main(limit=150):
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
            ob = get(f"{B}/markets/{t}/orderbook?depth=100")["orderbook_fp"]
        except Exception:
            errs += 1
            continue
        yes = [(D(a), D(s)) for a, s in (ob.get("yes_dollars") or [])]
        no = [(D(a), D(s)) for a, s in (ob.get("no_dollars") or [])]
        if not yes or not no:
            continue
        target = D(prog["target_size_fp"])
        df = D(prog["discount_factor_bps"]) / D(10000)
        pool = D(prog["period_reward"]) / CENTICENTS
        st, en = ts(prog["start_date"]), ts(prog["end_date"])
        days = D(str((en - st).total_seconds() / 86400))
        remaining_days = D(str(max(0.0, (en - now).total_seconds() / 86400)))

        ys, yref, yabove, yraw = side_score(yes, target, df)
        ns, nref, nabove, nraw = side_score(no, target, df)
        # Exclusion: BOTH sides must meet Target Size in raw resting size.
        two_sided = (yraw >= target) and (nraw >= target)

        out = {
            "market_ticker": t,
            "series": t.split("-", 1)[0],
            "pool_usd_period_MODELED_UNVERIFIED_UNITS": str(pool),
            "period_days_SOURCE": str(round(days, 3)),
            "remaining_days_SOURCE": str(round(remaining_days, 3)),
            "target_size_SOURCE": str(target),
            "discount_factor_SOURCE": str(df),
            "raw_resting_yes_SOURCE": str(yraw),
            "raw_resting_no_SOURCE": str(nraw),
            "two_sided_target_met_now_SOURCE": two_sided,
            "yes_reference_price_MODELED": str(yref) if yref is not None else "UNKNOWN",
            "no_reference_price_MODELED": str(nref) if nref is not None else "UNKNOWN",
            "yes_total_raw_score_MODELED": str(ys) if ys is not None else "UNKNOWN",
            "no_total_raw_score_MODELED": str(ns) if ns is not None else "UNKNOWN",
        }
        # q1 at or better than the reference price scores exactly 1.0.
        for name, sc in (("yes", ys), ("no", ns)):
            if sc is None:
                out[f"q1_share_{name}_MODELED"] = "UNKNOWN"
                continue
            # our own contract joins the denominator
            out[f"q1_share_{name}_MODELED"] = str(D(1) / (sc + D(1)))
        shares = [D(out[f"q1_share_{s}_MODELED"]) for s in ("yes", "no")
                  if out[f"q1_share_{s}_MODELED"] != "UNKNOWN"]
        if not shares:
            rows.append(out)
            continue
        best = max(shares)
        # Optimistic: best side, every snapshot non-excluded, resting the whole
        # period, TimePeriodScore NOT halved.
        opt = best * pool
        # Conservative: same, but TimePeriodScore halved (pool-conserving
        # reading) -- still assumes 100% uptime and zero exclusion.
        cons = best * pool / D(2)
        out["q1_reward_OPTIMISTIC_usd_per_period_MODELED"] = str(opt)
        out["q1_reward_CONSERVATIVE_usd_per_period_MODELED"] = str(cons)
        out["paid_at_optimistic_MIN_1USD"] = bool(opt >= MIN_PAYOUT)
        out["paid_at_conservative_MIN_1USD"] = bool(cons >= MIN_PAYOUT)
        # The structural floor on the denominator: Target/5 all at multiplier 1.
        floor_denom = target / D(5)
        out["q1_reward_STRUCTURAL_CEILING_usd_MODELED"] = str(pool / (floor_denom + 1))
        rows.append(out)

    rows.sort(key=lambda r: D(r.get("q1_reward_OPTIMISTIC_usd_per_period_MODELED", "0")),
              reverse=True)
    res = {
        "derived_from": {"path": SRC, "source_url": blob["source"],
                         "fetched_utc": blob["fetched_utc"]},
        "scoring_source": "https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program",
        "books_fetched_utc": now.isoformat(),
        "supersedes": "analysis/l5/kalshi/CANDIDATE_RANKING.json (invalid bound)",
        "unknowns": {
            "TimePeriodScore_normalization": "UNKNOWN - both readings reported",
            "non_excluded_snapshot_fraction": "UNKNOWN - requires per-second history",
            "our_uptime_fraction": "UNKNOWN - assumed 1.0 (generous)",
            "competitor_arrival": "UNKNOWN - books are a single instant",
            "eligibility": "ACCOUNT-OBSERVED - unobtainable, no credentials",
        },
        "programs_examined": limit,
        "orderbook_fetch_errors": errs,
        "candidates": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"examined={limit} scored={len(rows)} errors={errs}\n")
    print(f"{'market':50s} {'pool':>7s} {'denomYES':>11s} {'opt$':>8s} {'cons$':>8s} {'2sided':>7s}")
    for r in rows[:15]:
        print(f"{r['market_ticker'][:50]:50s} "
              f"{r['pool_usd_period_MODELED_UNVERIFIED_UNITS']:>7s} "
              f"{r['yes_total_raw_score_MODELED'][:11]:>11s} "
              f"{D(r.get('q1_reward_OPTIMISTIC_usd_per_period_MODELED','0')):>8.4f} "
              f"{D(r.get('q1_reward_CONSERVATIVE_usd_per_period_MODELED','0')):>8.4f} "
              f"{str(r['two_sided_target_met_now_SOURCE']):>7s}")
    paid = [r for r in rows if r.get("paid_at_optimistic_MIN_1USD")]
    print(f"\nclearing the $1.00 minimum payout even OPTIMISTICALLY: {len(paid)}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 150))
