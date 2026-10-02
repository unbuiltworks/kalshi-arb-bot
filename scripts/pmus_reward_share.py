#!/usr/bin/env python3
"""P5: obtainable PMUS liquidity-reward share from the ACTUAL scoring rules
and the ACTUAL competing books. No advertised pool is treated as obtainable.

Scoring (docs.polymarket.us/incentives/liquidity, quoted in the census report):
  "Score = Discount Factor ^ (ticks from best price) x Order Size"
  "Each snapshot is normalized; the bid side and ask side are each
   independently normalized to 1.0 per snapshot, provided Target Size is met
   on that side."
  "Every second, a random snapshot of the order book is taken" and every
   second is weighted equally.
  Target Size: "minimum aggregate size of resting orders (across all
   participants) needed on a side of the book for that side to qualify. The
   exchange accumulates orders from best price outward until this threshold
   is met."
  maxSpread programs: both sides must reach Target Size within the tolerance.
  "Rewards under $1.00 are not paid out."

Structural contrast with Kalshi, and it is the whole story: Kalshi measures
distance from a REFERENCE PRICE sitting Target/5 deep, so a maker can rest
well behind the touch at a full 1.0 multiplier. PMUS measures distance from
the BEST PRICE with a discount factor of 0.15-0.35 on most programs, so one
tick back already scores 15-35% and two ticks 2-12%. PMUS pays only for
liquidity AT the touch -- which is exactly the liquidity that gets adversely
selected.

Two readings of the qualifying set are reported because the docs are
ambiguous and the choice moves the denominator:
  ALL    every resting order on the side scores.
  WINDOW only orders inside the cumulative-Target-Size accumulation score.
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D, getcontext

getcontext().prec = 28
GW = "https://gateway.polymarket.us/v1"
CENSUS = "data/pmus/incentives/programs.json"
OUT = "analysis/l5/pmus_rewards/REWARD_SHARE.json"
MIN_PAYOUT = D("1.00")


def get(path):
    for a in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                GW + path, headers={"Accept": "application/json"}), timeout=30)
            return json.load(r)
        except Exception:
            if a == 3:
                raise
            time.sleep(1.5 ** a)


def levels(side):
    out = []
    for lv in side or []:
        try:
            out.append((D(lv["px"]["value"]), D(lv["qty"])))
        except Exception:
            continue
    return out


def infer_tick(bids, offers):
    """Smallest observed gap between adjacent price levels, floored at 0.005."""
    pxs = sorted({p for p, _ in bids} | {p for p, _ in offers})
    gaps = [pxs[i + 1] - pxs[i] for i in range(len(pxs) - 1)]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else D("0.01")


def score_side(lv, best, tick, df, target, window):
    """(total_score, qualifying_raw_size). lv sorted best-first."""
    tot, cum = D(0), D(0)
    for px, sz in lv:
        if window and cum >= target:
            break
        ticks = int(abs(px - best) / tick)
        tot += sz * (df ** ticks)
        cum += sz
    return tot, cum


def main(per_program=2):
    cen = json.load(open(CENSUS))
    now = datetime.now(timezone.utc)
    # group active rows by programId, keep the largest pools
    by_prog = {}
    for p in cen["programs"]:
        for tp in p.get("timePeriods") or []:
            if tp.get("status") != "active":
                continue
            by_prog.setdefault(tp["programId"], []).append((p, tp))
    ranked = sorted(by_prog.items(),
                    key=lambda kv: -max(t.get("rewardPool") or 0 for _, t in kv[1]))

    rows = []
    for pid, members in ranked[:22]:
        members.sort(key=lambda m: -(m[1].get("rewardPool") or 0))
        for p, tp in members[:per_program]:
            slug = p["marketSlug"]
            try:
                md = get(f"/markets/{slug}/book")["marketData"]
            except Exception as e:
                rows.append({"programId": pid, "marketSlug": slug,
                             "error": f"book fetch failed: {e}"})
                continue
            bids, offers = levels(md.get("bids")), levels(md.get("offers"))
            if not bids or not offers:
                continue
            bids.sort(key=lambda x: -x[0])
            offers.sort(key=lambda x: x[0])
            bb, ba = bids[0][0], offers[0][0]
            tick = infer_tick(bids, offers)
            df = D(str(tp["discountFactor"]))
            target = D(str(tp["targetSize"]))
            pool = D(str(tp.get("rewardPool") or 0))
            ms = tp.get("maxSpread")
            spread = ba - bb

            rec = {
                "programId": pid, "marketSlug": slug,
                "category_SOURCE": p.get("category"),
                "subcategory_SOURCE": p.get("subcategory"),
                "period_SOURCE": tp.get("period"),
                "start_SOURCE": tp.get("start"),
                "rewardPool_SOURCE": str(pool),
                "targetSize_SOURCE": str(target),
                "discountFactor_SOURCE": str(df),
                "maxSpread_SOURCE": str(ms) if ms is not None else "none",
                "tick_INFERRED": str(tick),
                "best_bid_SOURCE": str(bb), "best_ask_SOURCE": str(ba),
                "spread_SOURCE": str(spread),
                "max_spread_ok_SOURCE": (ms is None) or (float(spread) <= float(ms)),
            }
            for window in (False, True):
                tag = "WINDOW" if window else "ALL"
                bs, braw = score_side(bids, bb, tick, df, target, window)
                os_, oraw = score_side(offers, ba, tick, df, target, window)
                rec[f"bid_score_{tag}_MODELED"] = str(bs)
                rec[f"ask_score_{tag}_MODELED"] = str(os_)
                rec[f"bid_qualifies_{tag}_SOURCE"] = bool(braw >= target)
                rec[f"ask_qualifies_{tag}_SOURCE"] = bool(oraw >= target)
                # QUALIFICATION GATE. A side that does not reach Target Size
                # earns NOTHING -- it is not "uncontested", it is excluded.
                # An earlier revision omitted this and reported $422 for a q1
                # order against a near-empty offer side, which is the same
                # mistake the Kalshi pass made: reading an empty book as a
                # free pool. maxSpread programs additionally require the
                # spread tolerance to hold.
                spread_ok = (ms is None) or (float(spread) <= float(ms))
                bid_ok = (braw + D(1) >= target) and spread_ok
                ask_ok = (oraw + D(1) >= target) and spread_ok
                # q contracts resting AT the touch score 1.0 x q.
                for q in (1, 10, 100):
                    qd = D(q)
                    cands = []
                    if bid_ok:
                        cands.append(qd / (bs + qd))
                    if ask_ok:
                        cands.append(qd / (os_ + qd))
                    share = max(cands) if cands else D(0)
                    # pool is split across the two independently-normalized
                    # sides, so one side is worth at most half the pool.
                    rec[f"q{q}_reward_{tag}_MODELED"] = str(share * pool / 2)
                rec[f"bid_eligible_{tag}_SOURCE"] = bool(bid_ok)
                rec[f"ask_eligible_{tag}_SOURCE"] = bool(ask_ok)
            rows.append(rec)
            time.sleep(0.22)

    res = {
        "census": {"path": CENSUS, "source": cen["source"],
                   "fetched_utc": cen["fetched_utc"]},
        "scoring_source": "https://docs.polymarket.us/incentives/liquidity",
        "books_fetched_utc": now.isoformat(),
        "assumptions": {
            "side_split": "pool/2 per side (each side independently normalized to 1.0)",
            "uptime": "1.0 of the period -- GENEROUS",
            "qualifying_seconds_fraction": "UNKNOWN, assumed 1.0 -- GENEROUS",
            "our_placement": "resting AT the touch, multiplier 1.0 -- best case",
            "competitor_response": "none -- books are a single instant",
            "min_payout": "rewards under $1.00 are not paid",
        },
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2, sort_keys=True)

    ok = [r for r in rows if "error" not in r and "q1_reward_ALL_MODELED" in r]
    ok.sort(key=lambda r: -D(r["q10_reward_ALL_MODELED"]))
    print(f"{'programId':34s} {'cat':4s} {'pool':>7s} {'tgt':>7s} {'df':>5s} "
          f"{'bidScore':>10s} {'q1$':>7s} {'q10$':>7s} {'q100$':>8s}")
    for r in ok[:20]:
        print(f"{r['programId'][:34]:34s} {str(r['category_SOURCE'])[:4]:4s} "
              f"{D(r['rewardPool_SOURCE']):>7.0f} {D(r['targetSize_SOURCE']):>7.0f} "
              f"{D(r['discountFactor_SOURCE']):>5} {D(r['bid_score_ALL_MODELED']):>10.1f} "
              f"{D(r['q1_reward_ALL_MODELED']):>7.4f} {D(r['q10_reward_ALL_MODELED']):>7.3f} "
              f"{D(r['q100_reward_ALL_MODELED']):>8.3f}")
    print(f"\nrows={len(rows)} ok={len(ok)} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
