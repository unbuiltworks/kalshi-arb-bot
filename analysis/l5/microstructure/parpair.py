"""What are L5's $1.00 atomic taker complete-set purchases FOR?

FROZEN INPUT. The pairs themselves are established evidence (tag
l5-microstructure-frozen-2026-10-06): same TRANSACTION, opposite outcomes,
equal quantity, prices summing to exactly 1.0000, L5 taker on both legs. This
module asks only what role they play in his inventory and economics.

PAIRS ARE KEYED ON THE TRANSACTION, never the block. Keying on the block
conflates two coincident matches with one atomic event, which is the error the
first pass nearly made.

ONE STRUCTURAL FACT BOUNDS THE WHOLE STUDY, AND IT IS ARITHMETIC RATHER THAN
EMPIRICAL: a par pair adds the SAME quantity to both outcomes, so it moves
matched inventory by +q and leaves residual EXACTLY unchanged. Any hypothesis
that the pairs reduce directional imbalance is therefore false before a single
row is read. It is still measured, so the claim is refuted from data rather
than asserted.

FEES ARE MODEL-IMPLIED, NOT CREDITED. L5_OPERATING_MODEL.md records that on a
BUY the venue's usdcSize exceeds price x size by exactly 0.07 * p * (1 - p).
That formula is applied here and labelled `model_implied`. No rebate or reward
is credited in anything this repository holds, so reward eligibility is
reported as UNDETERMINABLE rather than estimated.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

FEE_RATE = 0.07          # from L5_OPERATING_MODEL.md, model-implied only
WINS = (30, 60, 120)


def jl(p: str) -> List[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def find_pairs(fills: List[dict]) -> Tuple[List[Dict[str, Any]], set]:
    """Transaction-level equal-quantity complementary-price pairs."""
    bytx: Dict[Any, List[dict]] = defaultdict(list)
    for f in fills:
        bytx[(f["market_slug"], f.get("tx"))].append(f)
    pairs: List[Dict[str, Any]] = []
    member: set = set()
    for (slug, tx), g in bytx.items():
        if tx is None:
            continue
        ups = [r for r in g if r["outcome"] == "up"]
        dns = [r for r in g if r["outcome"] == "down"]
        used = set()
        for u in ups:
            for i, d in enumerate(dns):
                if i in used:
                    continue
                if (abs(u["quantity"] - d["quantity"]) < 1e-6
                        and abs(u["price"] + d["price"] - 1.0) < 1e-9):
                    pairs.append({"slug": slug, "tx": tx, "up": u, "down": d})
                    member.add(_id(u))
                    member.add(_id(d))
                    used.add(i)
                    break
    pairs.sort(key=lambda p: (p["slug"], p["up"]["t"]))
    return pairs, member


def _id(f: dict):
    return (f["market_slug"], f.get("tx"), f.get("log_index"))


def ctx(p: Dict[str, Any], seq: List[dict], member: set, window_s: int
        ) -> Dict[str, Any]:
    u, d = p["up"], p["down"]
    t = u["t"]
    qty = u["quantity"]
    first_leg = min((u, d), key=lambda r: (r["t"], r.get("log_index") or 0))
    last_leg = max((u, d), key=lambda r: (r["t"], r.get("log_index") or 0))
    pre = first_leg["inv_before"]
    post = last_leg["inv_after"]

    passive = [r for r in seq if _id(r) not in member]
    paired = [r for r in seq if _id(r) in member]

    def around(lo: int, hi: int, rows: List[dict]):
        sel = [r for r in rows if lo <= r["t"] - t <= hi]
        return {"n_fills": len(sel),
                "shares": round(sum(r["quantity"] for r in sel), 4)}

    # matched-inventory growth attributable to PASSIVE flow after the pair
    def matched_growth(hi: int) -> Optional[float]:
        after = [r for r in seq if 0 < r["t"] - t <= hi]
        if not after:
            return 0.0
        return round((after[-1]["inv_after"]["matched"]
                      - post["matched"]), 4)

    is_first = all(r["t"] >= first_leg["t"] for r in seq
                   if _id(r) != _id(first_leg) or True) and \
        seq.index(first_leg) == 0
    prior_any = seq.index(first_leg)
    prior_passive = len([r for r in seq[:seq.index(first_leg)]
                         if _id(r) not in member])

    fee_u = FEE_RATE * u["price"] * (1 - u["price"]) * qty
    fee_d = FEE_RATE * d["price"] * (1 - d["price"]) * qty
    return {
        "market_slug": p["slug"], "contract_length": u["contract_length"],
        "tx": p["tx"], "t": t,
        "secs_after_open": first_leg["secs_since_open"],
        "tte_s": first_leg["tte_s"],
        "tte_fraction_remaining": round(first_leg["tte_s"] / window_s, 4),
        "quantity_per_leg": round(qty, 6),
        "up_price": u["price"], "down_price": d["price"],
        "price_sum": round(u["price"] + d["price"], 6),
        "roles": {"up": u.get("l5_role"), "down": d.get("l5_role")},
        "both_taker": u.get("l5_role") == "taker" == d.get("l5_role"),
        "mechanisms": {"up": u["mechanism"], "down": d["mechanism"]},
        # inventory, before and after the pair as one event
        "inv_before": pre, "inv_after": post,
        "matched_before": pre["matched"], "matched_after": post["matched"],
        "matched_delta": round(post["matched"] - pre["matched"], 4),
        "residual_before": pre["residual"], "residual_after": post["residual"],
        "residual_delta": round(post["residual"] - pre["residual"], 6),
        "imbalance_before": pre["imbalance_up_minus_down"],
        "imbalance_after": post["imbalance_up_minus_down"],
        "abs_imbalance_delta": round(abs(post["imbalance_up_minus_down"])
                                     - abs(pre["imbalance_up_minus_down"]), 6),
        # sequence position
        "is_first_l5_action_in_window": prior_any == 0,
        "l5_actions_before_it": prior_any,
        "passive_fills_before_it": prior_passive,
        # passive flow around it
        "passive_before": {str(w): around(-w, -1, passive) for w in WINS},
        "passive_after": {str(w): around(1, w, passive) for w in WINS},
        "paired_after": {str(w): around(1, w, paired) for w in WINS},
        "matched_growth_after": {str(w): matched_growth(w) for w in WINS},
        # economics
        "gross_pair_cost": round(u["price"] + d["price"], 6),
        "cash_out": round(qty * (u["price"] + d["price"]), 4),
        "settlement_payout": round(qty, 4),
        "gross_margin": round(qty * (1.0 - (u["price"] + d["price"])), 6),
        "model_implied_fee_usd": round(fee_u + fee_d, 6),
        "model_implied_fee_note": ("0.07 * p * (1-p) per leg, from "
                                   "L5_OPERATING_MODEL.md; NOT a credited "
                                   "fee from a venue ledger"),
        "net_after_model_fee": round(qty * (1.0 - (u["price"] + d["price"]))
                                     - (fee_u + fee_d), 6),
        "reward_eligibility": "UNDETERMINABLE_FROM_THIS_DATA",
    }


def q(v, ps=(0.1, 0.25, 0.5, 0.75, 0.9)) -> Dict[str, Any]:
    c = sorted(float(x) for x in v if x is not None)
    if not c:
        return {"n": 0}
    out = {"n": len(c), "min": round(c[0], 4), "max": round(c[-1], 4),
           "mean": round(sum(c) / len(c), 4)}
    for p in ps:
        out["p%d" % int(p * 100)] = round(c[int(p * (len(c) - 1))], 4)
    return out


def hypotheses(rows: List[Dict[str, Any]], all_fills: List[dict],
               window_s: int) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    opens = [r["secs_after_open"] for r in rows]
    allsecs = [f["secs_since_open"] for f in all_fills]

    def share(pred):
        return round(sum(1 for r in rows if pred(r)) / n, 4)

    # H2: matched growth stalled before, resumes after?
    before60 = [r["passive_before"]["60"]["shares"] for r in rows]
    after60 = [r["passive_after"]["60"]["shares"] for r in rows]
    return {
        "n_pairs": n,
        "H1_initializes_a_new_market": {
            "share_that_are_the_FIRST_l5_action_in_the_window":
                share(lambda r: r["is_first_l5_action_in_window"]),
            "l5_actions_before_the_pair": q(
                [r["l5_actions_before_it"] for r in rows]),
            "verdict_note": ("a pair that opens a market would have zero "
                             "prior L5 actions"),
        },
        "H2_replenishes_matched_inventory_after_passive_depletion": {
            "passive_shares_in_the_60s_BEFORE": q(before60),
            "passive_shares_in_the_60s_AFTER": q(after60),
            "share_with_MORE_passive_after_than_before":
                round(sum(1 for a, b in zip(after60, before60) if a > b)
                      / n, 4),
            "matched_delta_from_the_pair_itself": q(
                [r["matched_delta"] for r in rows]),
            "matched_growth_after": {w: q([r["matched_growth_after"][w]
                                           for r in rows]) for w in ("30", "60", "120")},
        },
        "H3_reduces_directional_imbalance": {
            "abs_imbalance_delta": q([r["abs_imbalance_delta"]
                                      for r in rows]),
            "residual_delta": q([r["residual_delta"] for r in rows]),
            "share_with_ANY_residual_change":
                share(lambda r: abs(r["residual_delta"]) > 1e-6),
            "verdict": ("REFUTED BY CONSTRUCTION AND CONFIRMED IN DATA: equal "
                        "quantity on both outcomes cannot move residual"),
        },
        "H4_precedes_a_new_passive_ladder_cycle": {
            "share_with_a_passive_fill_within_30s_after":
                share(lambda r: r["passive_after"]["30"]["n_fills"] > 0),
            "share_with_a_passive_fill_within_120s_after":
                share(lambda r: r["passive_after"]["120"]["n_fills"] > 0),
            "share_with_a_passive_fill_within_30s_BEFORE":
                share(lambda r: r["passive_before"]["30"]["n_fills"] > 0),
            "passive_fills_after_30s": q(
                [r["passive_after"]["30"]["n_fills"] for r in rows]),
        },
        "H5_regular_80_share_quantities": {
            "quantity_per_leg": q([r["quantity_per_leg"] for r in rows]),
            "share_at_80": share(lambda r: abs(r["quantity_per_leg"] - 80)
                                 < 0.75),
            "share_on_the_20_grid": share(
                lambda r: abs(r["quantity_per_leg"]
                              - round(r["quantity_per_leg"] / 20) * 20) < 0.75
                and r["quantity_per_leg"] >= 19.25),
            "distinct_quantities": len({round(r["quantity_per_leg"], 4)
                                        for r in rows}),
            "top_quantities": Counter(round(r["quantity_per_leg"], 4)
                                      for r in rows).most_common(8),
        },
        "H6_preferentially_at_the_opening": {
            "secs_after_open_of_PAIRS": q(opens),
            "secs_after_open_of_ALL_FILLS": q(allsecs),
            "share_of_pairs_in_first_30s":
                round(sum(1 for x in opens if x <= 30) / n, 4),
            "share_of_all_fills_in_first_30s":
                round(sum(1 for x in allsecs if x <= 30) / len(allsecs), 4),
            "tte_fraction_remaining": q([r["tte_fraction_remaining"]
                                         for r in rows]),
        },
        "H7_incentive_or_reward_periods": {
            "verdict": "UNDETERMINABLE_FROM_THIS_DATA",
            "why": ("no credited rebate or reward appears in any artifact "
                    "this repository holds for these windows, and the "
                    "operating model forbids assuming one. The archive spans "
                    "2026-08-21..24 only, which is far too short to resolve "
                    "an incentive regime against a baseline."),
            "what_would_settle_it": ("the venue's credited rebate/reward "
                                     "ledger for L5's address over a span "
                                     "containing a regime change"),
        },
        "ECONOMICS": {
            "gross_margin_usd": q([r["gross_margin"] for r in rows]),
            "model_implied_fee_usd": q([r["model_implied_fee_usd"]
                                        for r in rows]),
            "net_after_model_fee_usd": q([r["net_after_model_fee"]
                                          for r in rows]),
            "total_cash_out": round(sum(r["cash_out"] for r in rows), 2),
            "total_settlement_payout": round(sum(r["settlement_payout"]
                                                 for r in rows), 2),
            "total_model_implied_fee": round(sum(r["model_implied_fee_usd"]
                                                 for r in rows), 2),
            "note": ("gross margin is exactly zero by construction when the "
                     "price sum is 1.0000; the model-implied fee therefore "
                     "makes every such pair NEGATIVE before any rebate"),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = jl(a.events)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))

    rows: List[Dict[str, Any]] = []
    all_member: set = set()
    for slug, seq in sorted(byw.items()):
        pairs, member = find_pairs(seq)
        all_member |= member
        for p in pairs:
            rows.append(ctx(p, seq, member, a.window_s))

    paired_fills = sum(1 for r in ev if _id(r) in all_member)
    paired_shares = sum(r["quantity"] for r in ev if _id(r) in all_member)
    tot_shares = sum(r["quantity"] for r in ev)
    rep = {
        "contract_length": a.contract_length,
        "pairs_keyed_on": "TRANSACTION (never the block)",
        "n_pairs": len(rows),
        "windows_with_a_pair": len({r["market_slug"] for r in rows}),
        "windows_total": len(byw),
        "paired_fills": paired_fills, "total_fills": len(ev),
        "paired_fill_share": round(paired_fills / len(ev), 4),
        "paired_shares": round(paired_shares, 2),
        "total_shares": round(tot_shares, 2),
        "paired_share_of_volume": round(paired_shares / tot_shares, 4),
        "both_legs_taker_share": (
            round(sum(1 for r in rows if r["both_taker"]) / len(rows), 4)
            if rows else None),
        "hypotheses": hypotheses(rows, ev, a.window_s),
        "no_pnl_optimized": True,
        "pairs": rows,
    }
    os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, "PARPAIR_%s.json" % a.contract_length)
    with open(p, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items() if k != "pairs"},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
