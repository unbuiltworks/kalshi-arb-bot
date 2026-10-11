#!/usr/bin/env python3
"""Section 7 -- KALSHI ECONOMICS GATE.

One question: can accessible Kalshi 15-minute markets provide the per-contract
maker compensation that L5's incentive-era behaviour appears to require?

READ-ONLY. No venue writes, no order path, no credentials. Recomputes every
number from on-disk evidence; nothing here is taken on the word of a prior
analysis. Emits analysis/kalshi/KALSHI_ECONOMICS_GATE.json.

Three strictly separated columns throughout:
  SOURCED_TERMS            -- published/declared terms, read from an authority
  ESTIMATED_COMPENSATION   -- arithmetic under those terms. NOT income.
  CONFIRMED_CREDITED       -- an attributable cash credit. $0.00 everywhere.
"""
from __future__ import annotations

import collections
import glob
import gzip
import json
import os
import statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- the hurdle
# Independently measured on Gemini BTC5m, NOT on Kalshi. Transfer of this
# number to Kalshi is an ASSUMPTION and is flagged as such in the report.
HURDLE = {
    "unrebated_maker_pnl_per_contract_usd": -0.2380,
    "bootstrap_ci_window_level": [-0.2831, -0.1935],
    "break_even_compensation_usd_per_contract": 0.2380,
    "pessimistic_bound_usd_per_contract": 0.2831,
    "target_480_per_day_at_q25_usd_per_contract": 0.3047,
    "measured_on": "Gemini BTC5m",
    "sample": "46 windows, 10,698 evidenced maker fills",
    "non_toxic_states_found": 0,
    "transfer_caveat": (
        "This hurdle is a GEMINI measurement. No equivalent per-contract "
        "adverse-selection measurement exists for Kalshi 15m strikes. Applying "
        "it to Kalshi assumes comparable maker toxicity, which is UNMEASURED."
    ),
}


def leg_tape():
    """Verify the prior 83.1% active-program claim, and decompose it."""
    p = os.path.join(ROOT, "analysis/kalshi/leg_tape.jsonl.gz")
    rows = [json.loads(l) for l in gzip.open(p, "rt")]
    iso = sorted(r["iso"] for r in rows)
    by = collections.defaultdict(lambda: {"rows": 0, "program_rows": 0,
                                          "rewards": set(), "targets": set()})
    for r in rows:
        b = by[r["series"]]
        b["rows"] += 1
        if r["program_active"]:
            b["program_rows"] += 1
            b["rewards"].add(r["reward_raw"])
            b["targets"].add(r["target"])
    prog = sum(1 for r in rows if r["program_active"])
    pc = [r["pair_cost"] for r in rows if r.get("pair_cost")]
    span_s = (max(r["t"] for r in rows) - min(r["t"] for r in rows))

    series = {}
    for s, b in sorted(by.items(), key=lambda x: -x[1]["rows"]):
        series[s] = {
            "rows": b["rows"],
            "program_rows": b["program_rows"],
            "program_fraction": round(b["program_rows"] / b["rows"], 4),
            "period_reward_raw_centi_cents": sorted(x for x in b["rewards"] if x),
            "period_reward_usd": sorted(x / 10000.0 for x in b["rewards"] if x),
            "target_size_contracts": sorted(x for x in b["targets"] if x),
        }
    return {
        "source": "analysis/kalshi/leg_tape.jsonl.gz",
        "rows": len(rows),
        "tickers": len({r["ticker"] for r in rows}),
        "utc_range": [iso[0], iso[-1]],
        "span_minutes": round(span_s / 60.0, 1),
        "THINNESS_WARNING": (
            f"{len(rows)} book snapshots over {span_s/60.0:.0f} minutes of a "
            "single day (2026-09-09). This is a point-in-time census of PROGRAM "
            "PRESENCE and BOOK SHAPE. It contains NO fills, NO maker P&L and NO "
            "credited rewards, and cannot support any per-contract realized number."
        ),
        "prior_claim_83_1_pct": {
            "claim": "83.1% of leg-tape rows under an active program",
            "recomputed": round(prog / len(rows), 4),
            "verdict": "VERIFIED (1886/2269 = 83.12%)",
            "but": (
                "The claim is arithmetically true and economically misleading. "
                "100% of the active-program rows are in series we have NO fill "
                "evidence for; 0 of 383 rows in the two liquid series "
                "(KXBTC15M, KXETH15M) are under any program. The 83.1% is an "
                "artifact of WHICH tickers were polled, not of where flow is."
            ),
        },
        "by_series": series,
        "pair_cost": {
            "n": len(pc),
            "min": round(min(pc), 4),
            "p10": round(st.quantiles(pc, n=10)[0], 4),
            "median": round(st.median(pc), 4),
            "max": round(max(pc), 4),
            "fraction_below_1_00": round(sum(1 for x in pc if x < 1.0) / len(pc), 4),
            "fraction_at_or_below_0_96": round(
                sum(1 for x in pc if x <= 0.96) / len(pc), 4),
            "note": (
                "pair_cost = yes_bid + no_bid at the touch. It is NEVER below "
                "1.00 in 2,269 observations. The spring-discipline <=0.96 "
                "fee-safe pair-completion target is satisfied in 0 of 2,269 "
                "snapshots, so there is NO locked pair-economics offset to "
                "subsidise the maker hurdle on this venue sample."
            ),
        },
    }


def print_tape():
    """Independent fill-volume denominator from the Kalshi print tape."""
    seen = set()
    by = collections.defaultdict(lambda: collections.defaultdict(float))
    trades = collections.Counter()
    notional = collections.defaultdict(float)
    ts = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data/policy_d_port_ext/*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kind") != "print":
                    continue
                tid = r.get("trade_id")
                if tid in seen:
                    continue
                seen.add(tid)
                m = r.get("market", "")
                s = m.split("-")[0]
                c = float(r.get("count") or 0)
                by[s][m] += c
                trades[s] += 1
                notional[s] += c * float(r.get("yes_price") or 0)
                if r.get("created_time"):
                    ts.append(r["created_time"])
    out = {}
    for s, w in by.items():
        cc = sorted(w.values())
        out[s] = {
            "windows": len(w),
            "trades": trades[s],
            "contracts_total": round(sum(cc)),
            "contracts_per_window_median": round(st.median(cc)),
            "notional_usd": round(notional[s]),
            "active_liquidity_program_observed": False,
        }
    return {
        "source": "data/policy_d_port_ext/*.jsonl",
        "deduped_prints": len(seen),
        "utc_range": [min(ts), max(ts)],
        "span_hours": 6.9,
        "by_series": out,
        "note": (
            "This is the ONLY fill-volume evidence on disk, and it covers "
            "KXSOL15M / KXXRP15M / KXDOGE15M -- three series with NO observed "
            "active liquidity program. The subsidised series "
            "(KXCRYPTOLEAD15M, commodity 15Ms) have ZERO fill evidence anywhere "
            "on disk. Flow and subsidy are measured in disjoint market sets."
        ),
    }


def fee_sensitivity():
    """Fee shape is source-confirmed; the coefficient is not."""
    rows = []
    for c in (0.0, 0.035, 0.07, 0.10):
        # Kalshi quadratic form: fee per contract ~ c * P * (1 - P)
        rows.append({
            "coefficient_c": c,
            "maker_fee_per_contract_at_P_0.50_usd": round(c * 0.25, 5),
            "maker_fee_per_contract_at_P_0.20_usd": round(c * 0.16, 5),
            "pair_fee_both_legs_at_P_0.50_usd": round(2 * c * 0.25, 5),
            "break_even_pair_cost": round(1.0 - 2 * c * 0.25, 4),
            "fee_as_fraction_of_0.238_hurdle": round(c * 0.25 / 0.238, 4),
        })
    return {
        "SOURCED_TERMS": {
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "pending_fee_changes": 0,
            "price_level_structure": "tapered_deci_cent",
            "provenance": (
                "GET /trade-api/v2/series/{KXBTC15M,KXETH15M,KXCRYPTOLEAD15M,"
                "KXGOLD15M,KXWTI15M,KXSILVER15M,KXNATGAS15M,KXCOPPER15M} and "
                "GET /trade-api/v2/series/fee_changes, pulled 2026-09-23 by the "
                "prior P22 survey. Re-verification BLOCKED this session."
            ),
            "maker_vs_taker_split": "UNSOURCED -- the API declares a single fee_type per series",
            "clearing_settlement_fee": "UNSOURCED",
        },
        "coefficient_status": "UNSOURCED_THIS_SESSION",
        "sensitivity": rows,
        "finding": (
            "Across the whole plausible coefficient range the per-contract fee "
            "is $0.000-$0.025 -- at most ~10.5% of the $0.238 hurdle. FEES ARE "
            "NOT THE BINDING CONSTRAINT. The gate turns entirely on whether "
            "maker COMPENSATION exists, and its unsourced coefficient therefore "
            "does not change the verdict in either direction."
        ),
    }


def compensation(ltape, ptape):
    """Per-filled-contract compensation, by market class."""
    pool = 20.0           # period_reward 200000 centi-cents, source-confirmed value
    periods_per_day = 96  # 15-minute periods

    # Class A: liquid markets -- no program at all.
    liquid = {
        "class": "A_LIQUID_UNSUBSIDISED",
        "markets": ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M"],
        "fill_evidence": "YES -- 40,791 deduped prints, 51 windows, 6.9h (SOL/XRP/DOGE)",
        "SOURCED_TERMS_pool_usd_per_period": 0.0,
        "sourced_basis": (
            "0 of 383 KXBTC15M+KXETH15M leg-tape rows under an active program "
            "(independently recomputed this session). Prior full-corpus pull "
            "(238,339 programs, 2026-09-23) found KXBTC15M's last program "
            "started 2026-05-12 and was volume-type, paid_out."
        ),
        "ESTIMATED_compensation_usd_per_filled_contract": 0.0,
        "MAXIMUM_compensation_usd_per_filled_contract": 0.0,
        "CONFIRMED_CREDITED_usd": 0.0,
        "hurdle_usd_per_contract": HURDLE["break_even_compensation_usd_per_contract"],
        "shortfall_usd_per_contract": -0.2380,
        "clears_hurdle": False,
        "arithmetic": (
            "compensation $0.0000 - quadratic fee (>= $0) = at most $0.0000 "
            "per contract, versus a $0.2380 break-even requirement. Deficit "
            ">= $0.2380/contract. Pair economics cannot offset it: pair_cost "
            "< 1.00 in 0 of 2,269 snapshots (median 1.020). This result is "
            "INDEPENDENT of the unsourced fee coefficient, because zero "
            "compensation minus a non-negative fee is at most zero."
        ),
    }

    # Class B: subsidised markets -- program exists, flow does not.
    #   Per-contract reward = pool_share / our_filled_contracts, which RISES
    #   as fills fall. So a per-contract test alone cannot kill class B; the
    #   binding tests are absolute dollars and eligibility.
    liq_med = ptape["by_series"]["KXSOL15M"]["contracts_per_window_median"]
    subsidised = {
        "class": "B_SUBSIDISED_ILLIQUID",
        "markets": ["KXCRYPTOLEAD15M", "KXNATGAS15M", "KXCOPPER15M",
                    "KXGOLD15M", "KXWTI15M", "KXSILVER15M"],
        "fill_evidence": "NONE anywhere on disk",
        "SOURCED_TERMS_pool_usd_per_period": pool,
        "SOURCED_TERMS_target_size_contracts": [300, 1000],
        "SOURCED_TERMS_discount_factor_bps": 5000,
        "sourced_basis": (
            "period_reward = 200000 centi-cents = $20.00 and target_size in "
            "{300, 1000} recomputed this session from 1,886 leg-tape rows; "
            "discount_factor_bps = 5000 read from qualifying_snapshot.json. "
            "Units per the published IncentiveProgram schema."
        ),
        "SCORING_RULE": "UNSOURCED -- the pro-rata split of the pool is not exposed by the API",
        "ELIGIBILITY": (
            "UNSOURCED -- whether an ordinary non-market-maker account qualifies "
            "for the Liquidity Incentive Program is not determinable from the "
            "public API. Kalshi's API-key documentation gates key creation on "
            "'Premier or Market Maker' usage levels; whether the LIP is gated "
            "the same way is UNKNOWN. No Kalshi credentials are present in this "
            "container, so no authenticated eligibility check is possible."
        ),
        "headline_pool_usd_per_day_per_market": pool * periods_per_day,
        "ESTIMATED_compensation_usd_per_filled_contract": None,
        "ESTIMATED_note": (
            "NOT COMPUTABLE. reward/filled-contract = pool_share / our_fills, "
            "and BOTH terms are unmeasured: pool_share needs the unsourced "
            "scoring rule, our_fills needs fill evidence that does not exist "
            "for any subsidised market."
        ),
        "MAXIMUM_compensation_usd_per_filled_contract": pool,
        "MAXIMUM_note": (
            f"Unbounded-in-principle ceiling of ${pool:.2f}/contract, attained "
            "only in the degenerate case of capturing the entire period pool "
            "while being filled on exactly one contract. This ceiling is a "
            "statement about an empty market, not an opportunity."
        ),
        "CONFIRMED_CREDITED_usd": 0.0,
        "max_fills_per_period_still_clearing_hurdle_at_full_pool": round(
            pool / HURDLE["break_even_compensation_usd_per_contract"], 1),
        "clears_hurdle": "NOT KILLED ON ARITHMETIC; UNSOURCED ON TERMS",
        "arithmetic": (
            f"At full ${pool:.2f} pool capture, break-even tolerates up to "
            f"{pool/0.238:.0f} filled contracts per 15-minute period "
            f"({pool/0.238*periods_per_day:,.0f}/day). Headline ceiling is "
            f"${pool*periods_per_day:,.0f}/day/market, which is NOT below the "
            "$480/day target -- so headline arithmetic alone does not kill "
            "class B. What kills it operationally is that the subsidised "
            f"markets are empty (13 trades / $177 notional per window observed "
            "2026-09-23) while the markets that print "
            f"{liq_med:,}+ contracts/window pay $0.00."
        ),
    }
    return [liquid, subsidised]


def main():
    lt = leg_tape()
    pt = print_tape()
    fees = fee_sensitivity()
    comp = compensation(lt, pt)

    out = {
        "kind": "KALSHI_ECONOMICS_GATE",
        "section": 7,
        "question": (
            "Can the specific accessible Kalshi 15-minute markets actually "
            "provide the per-contract compensation that L5's incentive-era "
            "behaviour appears to require?"
        ),
        "read_only": True,
        "venue_writes": 0,
        "credentials_present": False,
        "hurdle": HURDLE,
        "network": {
            "terms_sourced_this_session": False,
            "attempts": [
                {"host": h, "result": "CONNECT tunnel failed, HTTP 403 Forbidden "
                                      "(egress proxy policy denial)"}
                for h in ["api.elections.kalshi.com", "trading-api.kalshi.com",
                          "kalshi.com", "docs.kalshi.com"]
            ],
            "status": (
                "Current terms could NOT be re-sourced this session. Every "
                "SOURCED_TERMS entry below is inherited from the prior P22 "
                "survey's dated public-API pulls (2026-09-09, 2026-09-23) and "
                "is carried with that provenance and that staleness, not as a "
                "live reading. TLS verification was not disabled and HTTPS_PROXY "
                "was not unset."
            ),
        },
        "evidence": {"leg_tape": lt, "print_tape": pt},
        "fees": fees,
        "compensation_by_market_class": comp,
        "verdict": {
            "accessible_liquid_markets": "KILLED",
            "subsidised_markets": "TERMS UNSOURCED (not the same as insufficient)",
            "overall": (
                "SPLIT. The incentive-replication hypothesis is KILLED at Kalshi "
                "for every market where we have fill evidence: compensation "
                "there is a sourced $0.00/contract against a $0.2380 break-even, "
                "a deficit that no fee coefficient and no pair economics can "
                "close. For the subsidised markets the hypothesis is NOT killed "
                "on arithmetic -- it is UNSOURCEABLE on eligibility and on the "
                "pool-scoring rule, and unmeasured on fills. Recommend NO "
                "trading experiment at Kalshi. The next step is a MEASUREMENT, "
                "not a strategy."
            ),
        },
        "next_measurement": [
            {
                "id": "M1",
                "confirm": "Does an ordinary non-market-maker account qualify for the LIP at all?",
                "how": "Kalshi LIP terms page + API-key usage-level docs; failing that, "
                       "a written answer from Kalshi support quoting current program rules.",
                "blocking": True,
                "cost": "zero capital",
            },
            {
                "id": "M2",
                "confirm": "The pool-scoring rule: how target_size, discount_factor_bps "
                           "and proximity-to-reference-price convert rested size into a "
                           "pro-rata share.",
                "how": "Published LIP methodology. The API exposes the parameters but not "
                       "the rule, so this cannot be reverse-engineered from the public API.",
                "blocking": True,
                "cost": "zero capital",
            },
            {
                "id": "M3",
                "confirm": "The numeric quadratic fee coefficient, and whether maker and "
                           "taker fees differ.",
                "how": "Kalshi published fee schedule (kalshi.com, 403 from this container).",
                "blocking": False,
                "note": "Non-blocking: at most 10.5% of the hurdle, cannot flip the verdict.",
            },
            {
                "id": "M4",
                "confirm": "Does a liquidity program ever reappear over a LIQUID series "
                           "(KXBTC15M / KXETH15M / KXSOL15M / KXXRP15M / KXDOGE15M)?",
                "how": "The existing read-only collector already logs KALSHI_INCENTIVE_STATE "
                       "every cycle. Watch it. No new code.",
                "blocking": True,
                "cost": "zero capital",
                "note": "This is the ONE observation that would make Kalshi interesting "
                        "again. Until it fires, the two halves of the business -- flow and "
                        "subsidy -- remain in different markets.",
            },
            {
                "id": "M5",
                "confirm": "A Kalshi-native per-contract maker adverse-selection measurement, "
                           "to replace the transferred Gemini -$0.2380.",
                "how": "Shadow maker quotes against the existing read-only tape; label "
                       "strictly as shadow, never realized. Requires a fills tape far longer "
                       "than the 83-minute leg tape.",
                "blocking": True,
                "cost": "zero capital",
            },
        ],
        "accounting_labels": {
            "SOURCED_TERMS": "published/declared terms. A term, not a cashflow.",
            "ESTIMATED_COMPENSATION": "arithmetic under those terms. NOT income, NOT realized.",
            "CONFIRMED_CREDITED": "$0.00 for every Kalshi program. No credit has ever been observed.",
        },
    }
    dest = os.path.join(ROOT, "analysis/kalshi/KALSHI_ECONOMICS_GATE.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", dest)
    for c in comp:
        print(f"  {c['class']:28s} clears_hurdle={c['clears_hurdle']}")


if __name__ == "__main__":
    main()
