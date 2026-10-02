#!/usr/bin/env python3
"""Replay PMUS liquidity-reward scoring over a per-second shadow tape.

Offline and read-only. Recomputes every score from the raw ladders in the
tape; nothing is trusted from the writer.

The question is NOT "is the thin-side state persistent". It is whether
conservative obtainable compensation can exceed our MEASURED cost of
producing the qualifying liquidity, with a plausible path to >= $3 average
net per eligible opportunity.

Scoring, per docs.polymarket.us/incentives/liquidity:
  score      = size x discountFactor ^ (ticks from best price on that side)
  share      = our score / total score on that side
  qualifying = that side's raw resting size >= targetSize, and (where the
               program sets maxSpread) the spread is within tolerance
  period     = every second weighted equally; non-qualifying seconds pay 0
  sides      = each independently normalized to 1.0, so one side is worth at
               most half the pool
  minimum    = rewards under $1.00 are not paid

THE AVERAGE IS TAKEN OVER SECONDS, NOT OVER SCORES. Averaging the score
first and then taking a share would discard exactly the thin-side seconds
this study exists to measure (Jensen); the per-second share is computed and
then averaged.
"""
from __future__ import annotations

import json, os, sys, collections, statistics
from decimal import Decimal as D, getcontext

getcontext().prec = 28
CENSUS = "data/pmus/incentives/programs.json"
OUT = "analysis/l5/pmus_rewards/SHADOW_RESULT.json"
MIN_PAYOUT = D("1.00")
QS = (1, 5, 10)

# ---- measured cost of producing qualifying liquidity, ACCOUNT-OBSERVED.
# analysis/l5/cross_shadow_live/ledger.jsonl, 39 distinct settled PMUS sports
# markets with realized_confirmed: -$44.58 total, -$1.1431 per market,
# 32 of 39 losing, median -$1.3368, worst -$5.6339.
# Only 1 of the 39 rows carries a fill count, so PER MARKET is the only
# honest unit; per-contract figures below are that number divided by the
# campaign's typical clip and are labelled DERIVED, not observed.
ADVERSE_PER_MARKET = D("-1.1431")
TYPICAL_CLIP = D("5")           # src/l5_incentive_breakeven.py QTYS/R4 rows
ADVERSE_PER_CONTRACT = ADVERSE_PER_MARKET / TYPICAL_CLIP
HIST_REWARD_PER_ROW = D("0.16")   # $7.04 over 44 credited rows

# ---- ACCOUNT-OBSERVED historical reward rows, for the reconciliation the
# modelled magnitudes have to survive. From the 2026-09-13 incentive_audit
# emission in analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl:
# venue by_status PAID n=37 $4.53 (33 rows visible in the truncated capture,
# $4.00). Visible PAID rows: median $0.04, mean $0.1212, MAX $1.15.
# The credited markets were tennis and soccer (aec-atp, aec-wta,
# aec-setka*, atc-ucl), whose comparable CURRENT pool is
# atp_wta_moneyline_live $300/market -- against $1,350-$10,350 for the
# NFL/CFB programs modelled here. Part of any gap is pool size, not skill.
HIST_BEST_ROW = D("1.15")
HIST_MEDIAN_ROW = D("0.04")
HIST_COMPARABLE_POOL = D("300")       # atp_wta_moneyline_live, per market
HIST_TYPICAL_CLIP = D("5")


def tick_of(bids, offers):
    pxs = sorted({p for p, _ in bids} | {p for p, _ in offers})
    gaps = [pxs[i + 1] - pxs[i] for i in range(len(pxs) - 1)]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else D("0.01")


def side_score(lv, best, tick, df):
    tot = D(0)
    raw = D(0)
    for px, sz in lv:
        tot += sz * (df ** int(abs(px - best) / tick))
        raw += sz
    return tot, raw


def params(slug):
    cen = json.load(open(CENSUS))
    for p in cen["programs"]:
        if p["marketSlug"] != slug:
            continue
        for tp in p.get("timePeriods") or []:
            if tp.get("status") == "active":
                return tp
    return None


def main(tape):
    hdr = None
    samples = collections.defaultdict(list)
    errs = collections.Counter()
    for line in open(tape):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "header":
            hdr = r
        elif r["kind"] == "error":
            errs[r["slug"]] += 1
        elif r["kind"] == "book":
            samples[r["slug"]].append(r)

    cache = {}
    out_rows = []
    for slug, rs in samples.items():
        tp = cache.get(slug) or params(slug)
        cache[slug] = tp
        if not tp:
            continue
        df = D(str(tp["discountFactor"]))
        target = D(str(tp["targetSize"]))
        pool = D(str(tp.get("rewardPool") or 0))
        ms = tp.get("maxSpread")

        per_q = {q: [] for q in QS}        # per-second share, 0 when not qualifying
        qual_secs = 0
        bid_scores, ask_scores, spreads, touch_px = [], [], [], []
        for s in rs:
            bids = [(D(a), D(b)) for a, b in s["bids"]]
            offers = [(D(a), D(b)) for a, b in s["offers"]]
            if not bids or not offers:
                for q in QS:
                    per_q[q].append(D(0))
                continue
            bids.sort(key=lambda x: -x[0]); offers.sort(key=lambda x: x[0])
            bb, ba = bids[0][0], offers[0][0]
            tick = tick_of(bids, offers)
            spread = ba - bb
            spread_ok = (ms is None) or (float(spread) <= float(ms))
            bs, braw = side_score(bids, bb, tick, df)
            as_, araw = side_score(offers, ba, tick, df)
            bid_scores.append(bs); ask_scores.append(as_)
            spreads.append(spread); touch_px.append(bb)
            bq = spread_ok and (braw >= target)
            aq = spread_ok and (araw >= target)
            if bq or aq:
                qual_secs += 1
            for q in QS:
                qd = D(q)
                cands = []
                if bq:
                    cands.append(qd / (bs + qd))
                if aq:
                    cands.append(qd / (as_ + qd))
                per_q[q].append(max(cands) if cands else D(0))

        n = len(rs)
        rec = {
            "marketSlug": slug,
            "programId": tp["programId"],
            "samples": n,
            "fetch_errors": errs.get(slug, 0),
            "rewardPool_SOURCE": str(pool),
            "targetSize_SOURCE": str(target),
            "discountFactor_SOURCE": str(df),
            "maxSpread_SOURCE": str(ms) if ms is not None else "none",
            "qualifying_time_fraction_OBSERVED": str(D(qual_secs) / n) if n else "0",
        }
        if bid_scores:
            bsf = sorted(float(x) for x in bid_scores)
            asf = sorted(float(x) for x in ask_scores)
            pct = lambda v, p: v[min(len(v) - 1, int(len(v) * p))]
            rec["bid_score_p10_p50_p90_OBSERVED"] = [pct(bsf, .10), pct(bsf, .50), pct(bsf, .90)]
            rec["ask_score_p10_p50_p90_OBSERVED"] = [pct(asf, .10), pct(asf, .50), pct(asf, .90)]
            rec["median_spread_OBSERVED"] = str(sorted(spreads)[len(spreads) // 2])
            rec["median_touch_price_OBSERVED"] = str(sorted(touch_px)[len(touch_px) // 2])

        for q in QS:
            shares = per_q[q]
            mean_share = sum(shares) / len(shares) if shares else D(0)
            # period reward = mean per-second share x (pool / 2 sides)
            reward = mean_share * pool / 2
            rec[f"q{q}_mean_share_MODELED"] = str(mean_share)
            rec[f"q{q}_period_reward_MODELED"] = str(reward)
            rec[f"q{q}_paid_after_min_MODELED"] = str(reward if reward >= MIN_PAYOUT else D(0))
            # ---- the two numbers that now matter.
            #
            # TWO adverse-selection estimates, because the transferred one is
            # not valid where the reward is largest. Our -$1.14/market was
            # earned quoting TIGHT markets; the high-reward states found here
            # are markets where the near-touch is empty and the spread is
            # wide. Resting alone at the touch of a 16-cent-wide book is the
            # worst adverse selection there is, and the historical number
            # does not measure it. Reporting only the transferred figure
            # would flatter exactly the candidates that deserve the most
            # suspicion.
            #
            #   TRANSFERRED  historical -$1.1431/market / clip 5
            #   SPREAD       market-specific: a fill at our resting touch is
            #                immediately half the quoted spread below mid,
            #                per contract, once per period (conservative:
            #                a lone touch in a wide book is hit more than
            #                once).
            adverse_hist = abs(ADVERSE_PER_CONTRACT) * D(q)
            med_spread = sorted(spreads)[len(spreads) // 2] if spreads else D(0)
            adverse_spread = med_spread / 2 * D(q)
            adverse = max(adverse_hist, adverse_spread)
            rec[f"q{q}_adverse_TRANSFERRED_DERIVED"] = str(adverse_hist)
            rec[f"q{q}_adverse_SPREAD_SOURCE_DERIVED"] = str(adverse_spread)
            rec[f"q{q}_adverse_basis"] = ("spread" if adverse_spread > adverse_hist
                                          else "transferred")
            rec[f"q{q}_adverse_selection_cost_DERIVED"] = str(adverse)
            paid = reward if reward >= MIN_PAYOUT else D(0)
            rec[f"q{q}_reward_over_adverse_MODELED"] = str(paid / adverse) if adverse else "inf"
            rec[f"q{q}_expected_net_MODELED"] = str(paid - adverse)
            # capital-hours: q contracts at the touch price for the sampled span
            if touch_px:
                px = sorted(touch_px)[len(touch_px) // 2]
                hours = D(n) / D(3600)
                cap_h = px * D(q) * hours
                rec[f"q{q}_capital_usd_MODELED"] = str(px * D(q))
                rec[f"q{q}_capital_hours_sampled_MODELED"] = str(cap_h)
                if cap_h:
                    rec[f"q{q}_net_per_capital_hour_MODELED"] = str((paid - adverse) / cap_h)
        out_rows.append(rec)

    res = {
        "tape": tape,
        "header": hdr,
        "scoring_source": "https://docs.polymarket.us/incentives/liquidity",
        "cost_basis": {
            "source": "analysis/l5/cross_shadow_live/ledger.jsonl",
            "settled_markets": 39,
            "realized_total_usd": "-44.5814",
            "per_market_usd_ACCOUNT_OBSERVED": str(ADVERSE_PER_MARKET),
            "losing_markets": "32/39",
            "median_usd": "-1.3368", "worst_usd": "-5.6339",
            "per_contract_DERIVED": str(ADVERSE_PER_CONTRACT),
            "derivation": "per-market / typical clip 5; only 1 of 39 rows "
                          "carries a fill count, so per-contract is DERIVED",
        },
        "historical_reward_per_credited_row_ACCOUNT_OBSERVED": str(HIST_REWARD_PER_ROW),
        "reconciliation_against_account_history": {
            "source": "analysis/l5/live_tape_2026-09-13 incentive_audit; "
                      "analysis/l5/incentives/TRANSFER_2PT51.md",
            "credited_total_usd": "7.04 account-lifetime (44 rows, 3 transfers)",
            "visible_paid_rows": "33 of 37, $4.00 of $4.53 -- the capture "
                                 "truncates at rows[:50]; UNRESOLVED",
            "per_row_median_usd": str(HIST_MEDIAN_ROW),
            "per_row_max_usd": str(HIST_BEST_ROW),
            "credited_market_types": "tennis (atp/wta/setka) and soccer (ucl)",
            "comparable_current_pool_usd": str(HIST_COMPARABLE_POOL),
            "implied_historical_share_best": str(
                HIST_BEST_ROW / (HIST_COMPARABLE_POOL / 2)),
            "implied_historical_share_median": str(
                HIST_MEDIAN_ROW / (HIST_COMPARABLE_POOL / 2)),
            "implied_historical_share_per_contract_best": str(
                HIST_BEST_ROW / (HIST_COMPARABLE_POOL / 2) / HIST_TYPICAL_CLIP),
            "note": "the apparent ~400x gap between modelled and credited "
                    "decomposes into pool size (tennis $300 vs NFL/CFB "
                    "$1,350-$10,350) and share (historical best 0.77% of the "
                    "side pool at clip ~5). It is a large gap, not an "
                    "inexplicable one, and the share term is the part a "
                    "reward-optimising controller could actually move.",
        },
        "caveats": [
            "mean is over SECONDS, not over scores",
            "our own order is assumed to score 1.0 x q at the touch -- best case",
            "no competitor response is modelled; a real quote would attract one",
            "the sampled span may be shorter than the full program period",
        ],
        "rows": out_rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2, sort_keys=True)

    print(f"tape={tape}")
    print(f"{'market':42s} {'n':>5s} {'qualFrac':>8s} {'bidP50':>9s} "
          f"{'q1$':>8s} {'q10$':>8s} {'q10 R/AS':>9s} {'q10 net':>8s}")
    for r in sorted(out_rows, key=lambda r: -D(r["q10_period_reward_MODELED"])):
        print(f"{r['marketSlug'][:42]:42s} {r['samples']:>5d} "
              f"{float(r['qualifying_time_fraction_OBSERVED']):>8.3f} "
              f"{r.get('bid_score_p10_p50_p90_OBSERVED',[0,0,0])[1]:>9.1f} "
              f"{D(r['q1_period_reward_MODELED']):>8.4f} "
              f"{D(r['q10_period_reward_MODELED']):>8.4f} "
              f"{D(r['q10_reward_over_adverse_MODELED']):>9.3f} "
              f"{D(r['q10_expected_net_MODELED']):>8.3f}")
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
