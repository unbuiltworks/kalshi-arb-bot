#!/usr/bin/env python3
"""Two-sided hypothetical maker on a PMUS reward tape: reward economics and
execution economics, computed from the same seconds and reported SEPARATELY.

    estimated reward  -  maker adverse selection  +  spread capture
                      =  estimated net economics

Offline, read-only. Every number is recomputed from the raw ladders.

RULES THIS OBEYS, because they are what make the answer trustworthy:
  * NO FILL IS ASSUMED. A resting quote is not a fill. Markouts are reported
    PER HYPOTHETICAL FILL and are never multiplied by an invented fill rate.
  * NO MAKER REBATE IS COUNTED WITHOUT A MODELLED FILL. PMUS credits the
    rebate at the time of the fill, so a resting quote that never trades
    earns reward only -- rebate is reported on the fill line, not the
    reward line.
  * REWARD AND EXECUTION ARE NEVER NETTED INTO ONE HEADLINE. They have
    different evidence classes: reward is modelled from published rules on
    observed books; adverse selection is a markout on observed prices and
    is conditional on a fill we did not observe.

Markout convention: for a hypothetical maker BUY resting at the bid, the
markout at horizon h is mid(t+h) - bid(t) per contract -- positive means the
market moved our way after we would have bought. For a maker SELL at the ask
it is ask(t) - mid(t+h). Adverse selection is the NEGATIVE part: a fill
happens when the market comes to us, which is disproportionately when it is
about to keep going.
"""
from __future__ import annotations

import json, os, sys, statistics
from decimal import Decimal as D, getcontext

getcontext().prec = 28
CENSUS = "data/pmus/incentives/programs.json"
OUT = "analysis/l5/pmus_rewards/TWO_SIDED_RESULT.json"
HORIZONS = (1, 5, 15, 30)
QS = (1, 5, 10)
MIN_PAYOUT = D("1.00")
MAKER_REBATE_RATE = D("0.0125")        # docs.polymarket.us/fees


def tick_of(bids, offers):
    pxs = sorted({p for p, _ in bids} | {p for p, _ in offers})
    gaps = [pxs[i + 1] - pxs[i] for i in range(len(pxs) - 1)]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else D("0.01")


def score_and_ahead(lv, best, tick, df):
    """(total side score, size resting AT the touch = queue ahead of us)."""
    tot = D(0); ahead = D(0); raw = D(0)
    for px, sz in lv:
        tot += sz * (df ** int(abs(px - best) / tick))
        raw += sz
        if px == best:
            ahead += sz
    return tot, ahead, raw


def programs_for(slug):
    cen = json.load(open(CENSUS))
    out = []
    for p in cen["programs"]:
        if p["marketSlug"] != slug:
            continue
        for tp in p.get("timePeriods") or []:
            if tp.get("status") == "active":
                out.append(tp)
    return out


def main(tape):
    seq = {}
    hdr = None
    errors = 0
    for line in open(tape):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] == "header":
            hdr = r
        elif r["kind"] == "error":
            errors += 1
        elif r["kind"] == "book":
            seq.setdefault(r["slug"], []).append(r)

    results = []
    for slug, rs in seq.items():
        # index by second so a 429 gap is a HOLE, not a shifted sample
        import datetime as dt
        t0 = dt.datetime.fromisoformat(rs[0]["t"])
        bysec = {}
        for r in rs:
            s = int((dt.datetime.fromisoformat(r["t"]) - t0).total_seconds())
            bysec.setdefault(s, r)
        span = max(bysec) + 1

        parsed = {}
        for s, r in bysec.items():
            bids = [(D(a), D(b)) for a, b in r["bids"]]
            offers = [(D(a), D(b)) for a, b in r["offers"]]
            if not bids or not offers:
                continue
            bids.sort(key=lambda x: -x[0]); offers.sort(key=lambda x: x[0])
            parsed[s] = (bids, offers, bids[0][0], offers[0][0])

        for tp in programs_for(slug):
            df = D(str(tp["discountFactor"]))
            target = D(str(tp["targetSize"]))
            pool = D(str(tp.get("rewardPool") or 0))
            ms = tp.get("maxSpread")

            shares = {q: {"bid": [], "ask": []} for q in QS}
            qual_bid = qual_ask = 0
            spreads, ahead_b, ahead_a, scores_b, scores_a = [], [], [], [], []
            markout = {h: {"buy": [], "sell": []} for h in HORIZONS}

            for s, (bids, offers, bb, ba) in parsed.items():
                tick = tick_of(bids, offers)
                spread = ba - bb
                mid = (bb + ba) / 2
                spreads.append(spread)
                spread_ok = (ms is None) or (float(spread) <= float(ms))
                bs, b_ahead, b_raw = score_and_ahead(bids, bb, tick, df)
                as_, a_ahead, a_raw = score_and_ahead(offers, ba, tick, df)
                scores_b.append(bs); scores_a.append(as_)
                ahead_b.append(b_ahead); ahead_a.append(a_ahead)
                bq = spread_ok and (b_raw >= target)
                aq = spread_ok and (a_raw >= target)
                qual_bid += bq; qual_ask += aq
                for q in QS:
                    qd = D(q)
                    shares[q]["bid"].append(qd / (bs + qd) if bq else D(0))
                    shares[q]["ask"].append(qd / (as_ + qd) if aq else D(0))
                # markouts against the future mid, only where that second exists
                for h in HORIZONS:
                    f = parsed.get(s + h)
                    if not f:
                        continue
                    fmid = (f[2] + f[3]) / 2
                    markout[h]["buy"].append(fmid - bb)    # we bought at bb
                    markout[h]["sell"].append(ba - fmid)   # we sold at ba

            n = len(parsed)
            if not n:
                continue
            med = lambda v: sorted(v)[len(v) // 2] if v else D(0)
            mean = lambda v: (sum(v) / len(v)) if v else D(0)

            rec = {
                "marketSlug": slug, "programId": tp["programId"],
                "period_SOURCE": tp.get("period"),
                "rewardPool_SOURCE": str(pool), "targetSize_SOURCE": str(target),
                "discountFactor_SOURCE": str(df),
                "maxSpread_SOURCE": str(ms) if ms is not None else "none",
                "span_seconds": span, "seconds_sampled": n,
                "coverage": str(D(n) / span),
                "fetch_errors_429": errors,
                "distinct_book_states_OBSERVED": len(
                    {json.dumps([r["bids"], r["offers"]]) for r in rs}),
                "qualifying_fraction_bid_OBSERVED": str(D(qual_bid) / n),
                "qualifying_fraction_ask_OBSERVED": str(D(qual_ask) / n),
                "median_spread_OBSERVED": str(med(spreads)),
                "median_queue_ahead_bid_OBSERVED": str(med(ahead_b)),
                "median_queue_ahead_ask_OBSERVED": str(med(ahead_a)),
                "median_side_score_bid_OBSERVED": str(med(scores_b)),
                "median_side_score_ask_OBSERVED": str(med(scores_a)),
            }
            # ---- EXECUTION economics, per hypothetical fill. No fill assumed.
            for h in HORIZONS:
                for side in ("buy", "sell"):
                    v = markout[h][side]
                    if not v:
                        continue
                    rec[f"markout_{h}s_{side}_mean_per_contract_OBSERVED"] = str(mean(v))
                    rec[f"markout_{h}s_{side}_median_per_contract_OBSERVED"] = str(med(v))
                    neg = [x for x in v if x < 0]
                    rec[f"markout_{h}s_{side}_adverse_frac_OBSERVED"] = str(D(len(neg)) / len(v))
            # two-sided round trip at 30s, per contract per side
            if markout[30]["buy"] and markout[30]["sell"]:
                rec["two_sided_markout_30s_sum_per_contract_OBSERVED"] = str(
                    mean(markout[30]["buy"]) + mean(markout[30]["sell"]))
            rec["spread_capture_per_round_trip_OBSERVED"] = str(med(spreads))

            # ---- REWARD economics, separate line.
            for q in QS:
                qd = D(q)
                r_bid = mean(shares[q]["bid"]) * pool / 2
                r_ask = mean(shares[q]["ask"]) * pool / 2
                rec[f"q{q}_reward_bid_MODELED"] = str(r_bid)
                rec[f"q{q}_reward_ask_MODELED"] = str(r_ask)
                tot = r_bid + r_ask
                rec[f"q{q}_reward_two_sided_MODELED"] = str(tot)
                rec[f"q{q}_reward_paid_after_min_MODELED"] = str(
                    tot if tot >= MIN_PAYOUT else D(0))
                # rebate ONLY on a modelled fill, reported per fill, never added in
                bb_med = med([p for p in (x[2] for x in parsed.values())])
                rec[f"q{q}_maker_rebate_PER_MODELLED_FILL"] = str(
                    MAKER_REBATE_RATE * qd * bb_med * (1 - bb_med))
            results.append(rec)

    out = {
        "tape": tape, "header": hdr,
        "scoring_source": "https://docs.polymarket.us/incentives/liquidity",
        "separation": "reward economics and execution economics are reported "
                      "separately and are NEVER summed into one headline",
        "no_fill_assumed": True,
        "rebate_rule": "credited at fill; reported per modelled fill only",
        "rows": results,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    for r in results:
        print(f"\n== {r['marketSlug']} / {r['programId']} ({r['period_SOURCE']})")
        print(f"   seconds {r['seconds_sampled']}/{r['span_seconds']} "
              f"coverage {float(r['coverage']):.2f}  distinct states "
              f"{r['distinct_book_states_OBSERVED']}  429s {r['fetch_errors_429']}")
        print(f"   qualify bid {float(r['qualifying_fraction_bid_OBSERVED']):.3f} "
              f"ask {float(r['qualifying_fraction_ask_OBSERVED']):.3f}  "
              f"median spread {r['median_spread_OBSERVED']}  "
              f"queue ahead {r['median_queue_ahead_bid_OBSERVED']}/{r['median_queue_ahead_ask_OBSERVED']}")
        print(f"   REWARD   q1 ${float(r['q1_reward_two_sided_MODELED']):.4f}  "
              f"q5 ${float(r['q5_reward_two_sided_MODELED']):.4f}  "
              f"q10 ${float(r['q10_reward_two_sided_MODELED']):.4f} (two-sided)")
        for h in HORIZONS:
            kb=f"markout_{h}s_buy_mean_per_contract_OBSERVED"
            ks=f"markout_{h}s_sell_mean_per_contract_OBSERVED"
            if kb in r:
                print(f"   MARKOUT {h:>2}s  buy {float(r[kb]):+.5f}  sell {float(r[ks]):+.5f}"
                      f"  adverse frac {float(r[f'markout_{h}s_buy_adverse_frac_OBSERVED']):.2f}"
                      f"/{float(r[f'markout_{h}s_sell_adverse_frac_OBSERVED']):.2f}")
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
