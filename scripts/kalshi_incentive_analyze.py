#!/usr/bin/env python3
"""P2/P3: derive a committable candidate census from the raw Kalshi incentive dump.

READ-ONLY over data/kalshi/census/incentive_programs.json. Emits
analysis/l5/kalshi/INCENTIVE_CENSUS.json, which is small enough to track and
carries the provenance of the dump it was derived from.

Every field is labelled by evidence class in the emitted record:
  SOURCE-OBSERVED  -- read directly from the venue response
  MODELED          -- computed by us from SOURCE-OBSERVED inputs
  UNKNOWN          -- not obtainable from the public endpoint
Account-visible facts (eligibility, credited rewards, our own queue position)
are ACCOUNT-OBSERVED and are deliberately absent: no credentials are present
in this environment, so claiming them would be fabrication.
"""
import json, sys, collections
from decimal import Decimal
from datetime import datetime, timezone

SRC = "data/kalshi/census/incentive_programs.json"
OUT = "analysis/l5/kalshi/INCENTIVE_CENSUS.json"

# UNVERIFIED unit hypothesis. The largest active period_reward observed is
# 10,000,000; Kalshi advertises a $1-$1,000 per-market-per-day liquidity
# ceiling. 10,000,000 centi-cents == $1,000.00 exactly. That is corroboration,
# not confirmation: no account-credited reward has been observed to calibrate
# it. Every dollar figure derived through it is MODELED-UNVERIFIED-UNITS.
CENTICENTS_PER_DOLLAR = Decimal(10000)


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0] if ticker else ""


def main() -> int:
    blob = json.load(open(SRC))
    now = datetime.now(timezone.utc)
    progs = blob["programs"]

    def parse(d):
        return datetime.fromisoformat(d.replace("Z", "+00:00")) if d else None

    active, upcoming = [], []
    per_series = collections.defaultdict(lambda: {"total": 0, "active": 0, "upcoming": 0})
    for p in progs:
        s = series_of(p.get("market_ticker", ""))
        per_series[s]["total"] += 1
        st, en = parse(p.get("start_date")), parse(p.get("end_date"))
        if st and en and st <= now < en:
            per_series[s]["active"] += 1
            active.append(p)
        elif st and st > now:
            per_series[s]["upcoming"] += 1
            upcoming.append(p)

    # Per-series active economics.
    rows = []
    for s, counts in per_series.items():
        if not counts["active"]:
            continue
        a = [p for p in active if series_of(p.get("market_ticker", "")) == s]
        pools = [Decimal(str(p.get("period_reward") or 0)) for p in a]
        tsz = sorted({str(p.get("target_size_fp")) for p in a})
        dfs = sorted({p.get("discount_factor_bps") for p in a})
        rows.append({
            "series": s,
            "active_programs": counts["active"],
            "upcoming_programs": counts["upcoming"],
            "total_programs_in_dump": counts["total"],
            "period_reward_max_raw": str(max(pools)),
            "period_reward_sum_raw": str(sum(pools)),
            "period_reward_max_usd_MODELED_UNVERIFIED_UNITS":
                str(max(pools) / CENTICENTS_PER_DOLLAR),
            "target_size_fp_distinct": tsz,
            "discount_factor_bps_distinct": dfs,
            "incentive_types": sorted({p.get("incentive_type") for p in a}),
            "incentive_descriptions": sorted({p.get("incentive_description") for p in a}),
            # Everything below requires the live book or our account.
            "yes_depth": "UNKNOWN",
            "no_depth": "UNKNOWN",
            "spread": "UNKNOWN",
            "competing_qualifying_liquidity": "UNKNOWN",
            "obtainable_q1_share": "UNKNOWN",
            "eligibility": "ACCOUNT-OBSERVED-UNOBTAINABLE",
            "credited_rewards": "ACCOUNT-OBSERVED-UNOBTAINABLE",
        })
    rows.sort(key=lambda r: Decimal(r["period_reward_sum_raw"]), reverse=True)

    # The L5-analogue question: does the venue pay anything at all on the
    # short-horizon crypto series that most resemble L5's PMUS operating
    # regime? Answered from the dump, not from memory.
    L5_ANALOGUE = ["KXBTC15M", "KXETH15M", "KXBTCD", "KXETHD", "KXBTC", "KXETH",
                   "KXCRYPTOLEAD15M", "KXSOL15M", "KXXRP15M"]
    analogue = {s: per_series.get(s, {"total": 0, "active": 0, "upcoming": 0})
                for s in L5_ANALOGUE}

    out = {
        "derived_from": {
            "path": SRC,
            "source_url": blob["source"],
            "fetched_utc": blob["fetched_utc"],
            "pages": blob["pages"],
            "program_count": blob["count"],
        },
        "analyzed_utc": now.isoformat(),
        "evidence_classes": {
            "program_fields": "SOURCE-OBSERVED",
            "usd_conversions": "MODELED-UNVERIFIED-UNITS",
            "book_depth_spread_share": "UNKNOWN (requires live book pull)",
            "eligibility_and_credits": "ACCOUNT-OBSERVED (no credentials in this environment)",
        },
        "unit_hypothesis": {
            "assumption": "period_reward is denominated in centi-cents (1e-4 USD)",
            "corroboration": "max active period_reward 10,000,000 -> $1,000.00/day, "
                             "matching the advertised per-market-per-day ceiling",
            "status": "UNVERIFIED - no account-credited reward observed",
        },
        "active_now": len(active),
        "upcoming": len(upcoming),
        "l5_analogue_series": analogue,
        "active_series": rows,
    }
    import os
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"active={len(active)} upcoming={len(upcoming)} active_series={len(rows)}")
    for s, c in analogue.items():
        print(f"  L5-analogue {s:18s} total={c['total']:6d} active={c['active']:4d} upcoming={c['upcoming']:4d}")
    print("top active series by summed pool:")
    for r in rows[:12]:
        print(f"  {r['series']:22s} n={r['active_programs']:4d} "
              f"max=${r['period_reward_max_usd_MODELED_UNVERIFIED_UNITS']:>9s}/period")
    return 0


if __name__ == "__main__":
    sys.exit(main())
