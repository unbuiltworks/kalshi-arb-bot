"""ADVERSARIAL ACCOUNTING AUDIT of the +$20,424 pre-incentive claim.

The brief is to FALSIFY, not validate. Nothing here is realized profit for
our account; this is another trader's public activity.

THE CLAIM'S CONSTRUCTION, read out of scripts/l5_regime_economics.py:

    trading_net_ex_incentives = 1 + 2 + 3 - 4
      1 matched_pair_pnl = matched_qty * (1 - pair_cost)   <-- ACCRUAL
      2 residual_pnl     = qty*payout - cost               <-- ACCRUAL
      3 spread_capture   = round-trip (vwap_sell - vwap_buy)
      4 fees_confirmed   = entryFeesUsdc where present

and component 7, redeem_cashflow, is EXPLICITLY EXCLUDED from the net.

So the headline is an ACCRUAL figure whose largest term is derived from
pair cost -- exactly what the audit forbids. This module ignores that
construction and rebuilds the number from CASHFLOWS:

    cash_pnl = redemption_proceeds + sell_proceeds
               - buy_principal - confirmed_fees

Per condition, with every component reported separately so no term can
hide inside another. No synthetic matching anywhere: a matched pair
produces cash only if a redemption record says it did.
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
from typing import Any, Dict, List

RAW = (("data/l5/raw/activity.jsonl", False),
       ("data/l5/raw/activity_gap.jsonl.gz", True))
POS = ("data/l5/raw/positions_all.jsonl",
       "data/l5/raw/positions_closed.jsonl",
       "data/l5/raw/positions_redeemable.jsonl")
L5 = "0xb945945d5bca"
#: the claim's own boundary: pre-REWARD is everything before 2026-08
PRE_END = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp()
KEY = lambda x: (x.get("transactionHash"), x.get("conditionId"),
                 x.get("outcomeIndex"), x.get("timestamp"),
                 str(x.get("price")), str(x.get("size")), x.get("type"))


def f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load():
    seen, rows = set(), []
    stats = collections.Counter()
    for path, gz in RAW:
        op = gzip.open if gz else open
        with op(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    stats["malformed_envelope"] += 1
                    continue
                if L5 not in str((env.get("params") or {})
                                 .get("user", "")).lower():
                    stats["dropped_no_provenance"] += 1
                    continue
                pl = env.get("payload")
                if not isinstance(pl, list):
                    stats["payload_not_list"] += 1
                    continue
                for r in pl:
                    if not isinstance(r, dict):
                        continue
                    k = KEY(r)
                    if k in seen:
                        stats["dupes_dropped"] += 1
                        continue
                    seen.add(k)
                    rows.append(r)
    return rows, stats


def confirmed_fees():
    """entryFeesUsdc per (condition, asset); snapshots repeat so keep max."""
    fees: Dict[tuple, float] = {}
    legs = 0
    for path in POS:
        try:
            fh = open(path, "rt", encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if L5 not in str((env.get("params") or {})
                                 .get("user", "")).lower():
                    continue
                pl = env.get("payload")
                if not isinstance(pl, list):
                    continue
                for p in pl:
                    if not isinstance(p, dict):
                        continue
                    fee = p.get("entryFeesUsdc")
                    if fee in (None, ""):
                        continue
                    k = (p.get("conditionId"), p.get("asset"))
                    v = f(fee)
                    if k not in fees or v > fees[k]:
                        fees[k] = v
    byc: Dict[str, float] = collections.defaultdict(float)
    for (cond, _a), v in fees.items():
        byc[cond] += v
        legs += 1
    return dict(byc), legs, fees


def main() -> int:
    rows, stats = load()
    fees_by_cond, fee_legs, fee_raw = confirmed_fees()

    # ---- per-condition cashflow ledger
    C: Dict[str, Dict[str, Any]] = {}

    def slot(cond):
        if cond not in C:
            C[cond] = {"buy_principal": 0.0, "buy_qty": 0.0,
                       "sell_proceeds": 0.0, "sell_qty": 0.0,
                       "buy_qty_by_leg": collections.defaultdict(float),
                       "sell_qty_by_leg": collections.defaultdict(float),
                       "redeem_cash": 0.0, "redeem_n": 0,
                       "redeem_legs": set(), "first_ts": None,
                       "n_fills": 0, "usdc_buy": 0.0}
        return C[cond]

    unattributed = collections.Counter()
    for r in rows:
        t = r.get("type")
        cond = r.get("conditionId")
        ts = r.get("timestamp")
        if not cond:
            unattributed["no_conditionId_" + str(t)] += 1
            continue
        s = slot(cond)
        if s["first_ts"] is None or (ts and ts < s["first_ts"]):
            s["first_ts"] = ts
        if t == "TRADE":
            px, qty = f(r.get("price")), f(r.get("size"))
            usdc = f(r.get("usdcSize"))
            side = str(r.get("side") or "").upper()
            oi = r.get("outcomeIndex")
            s["n_fills"] += 1
            if side == "BUY":
                s["buy_principal"] += px * qty
                s["usdc_buy"] += usdc
                s["buy_qty"] += qty
                s["buy_qty_by_leg"][oi] += qty
            elif side == "SELL":
                s["sell_proceeds"] += px * qty
                s["sell_qty"] += qty
                s["sell_qty_by_leg"][oi] += qty
            else:
                unattributed["trade_no_side"] += 1
        elif t == "REDEEM":
            s["redeem_cash"] += f(r.get("usdcSize"))
            s["redeem_n"] += 1
            if r.get("outcomeIndex") is not None:
                s["redeem_legs"].add(r.get("outcomeIndex"))

    # ---- classify and reconcile
    out_rows: List[Dict[str, Any]] = []
    for cond, s in C.items():
        if s["first_ts"] is None:
            continue
        pre = s["first_ts"] < PRE_END
        fee = fees_by_cond.get(cond)
        # entitlement check: winning shares held = qty on the redeemed leg
        held = {k: s["buy_qty_by_leg"][k] - s["sell_qty_by_leg"].get(k, 0.0)
                for k in set(list(s["buy_qty_by_leg"]) +
                             list(s["sell_qty_by_leg"]))}
        entitled = sum(max(0.0, held.get(l, 0.0)) for l in s["redeem_legs"])
        cash = (s["redeem_cash"] + s["sell_proceeds"]
                - s["buy_principal"] - (fee or 0.0))
        out_rows.append({
            "cond": cond, "pre_incentive": pre, "n_fills": s["n_fills"],
            "buy_principal": s["buy_principal"],
            "usdc_buy_reported": s["usdc_buy"],
            "sell_proceeds": s["sell_proceeds"],
            "sell_qty": s["sell_qty"],
            "redeem_cash": s["redeem_cash"], "redeem_n": s["redeem_n"],
            "redeem_legs": sorted(s["redeem_legs"]),
            "fee_confirmed": fee,
            "entitled_qty_on_redeemed_legs": entitled,
            "redeem_minus_entitlement": s["redeem_cash"] - entitled,
            "cash_pnl": cash,
            "has_redemption": s["redeem_n"] > 0,
            "both_legs_redeemed": len(s["redeem_legs"]) > 1,
        })

    pre_rows = [r for r in out_rows if r["pre_incentive"]]
    with_red = [r for r in pre_rows if r["has_redemption"]]
    no_red = [r for r in pre_rows if not r["has_redemption"]]
    fully = [r for r in with_red if r["fee_confirmed"] is not None]

    def agg(rs, label):
        return {
            "label": label, "events": len(rs),
            "buy_principal": round(sum(r["buy_principal"] for r in rs), 2),
            "sell_proceeds": round(sum(r["sell_proceeds"] for r in rs), 2),
            "redeem_cash": round(sum(r["redeem_cash"] for r in rs), 2),
            "fees_confirmed": round(sum(r["fee_confirmed"] or 0.0
                                        for r in rs), 2),
            "events_with_confirmed_fee": sum(1 for r in rs
                                             if r["fee_confirmed"]
                                             is not None),
            "CASH_PNL": round(sum(r["cash_pnl"] for r in rs), 2),
            "fills": sum(r["n_fills"] for r in rs),
        }

    pre_fills = sum(r["n_fills"] for r in pre_rows)
    claim = 20424.0
    res = {
        "label": "L5_PREINCENTIVE_CASHFLOW_AUDIT_V1",
        "what_the_claim_is": (
            "trading_net_ex_incentives = matched*(1-pair_cost) + "
            "residual_accrual + spread_capture - confirmed_fees. Its largest "
            "term is MODELLED FROM PAIR COST and the actual redemption "
            "cashflow is excluded from it. This audit replaces it with "
            "cash_pnl = redemption + sell_proceeds - buy_principal - fees."),
        "corpus": {"rows_admitted": len(rows), **dict(stats)},
        "unattributed_records": dict(unattributed),
        "confirmed_fee_legs": fee_legs,
        "pre_incentive_boundary_utc": "2026-08-01T00:00:00Z",
        "aggregates": [
            agg(pre_rows, "ALL pre-incentive conditions"),
            agg(with_red, "pre-incentive WITH a redemption record"),
            agg(no_red, "pre-incentive WITHOUT any redemption record"),
            agg(fully, "FULLY RECONCILED (redemption AND confirmed fee)"),
        ],
        "coverage": {
            "pre_incentive_conditions": len(pre_rows),
            "with_redemption": len(with_red),
            "with_redemption_pct": (round(100.0 * len(with_red)
                                          / len(pre_rows), 2)
                                    if pre_rows else None),
            "fully_reconciled": len(fully),
            "fully_reconciled_pct": (round(100.0 * len(fully)
                                           / len(pre_rows), 2)
                                     if pre_rows else None),
        },
        "fee_stress_test": {
            "claim_usd": claim,
            "pre_incentive_fills": pre_fills,
            "breakeven_missing_cost_per_fill": (round(claim / pre_fills, 6)
                                                if pre_fills else None),
            "note": ("the average additional cost per TRADE fill that would "
                     "take the claim to exactly $0"),
        },
        "integrity_checks": {
            "conditions_with_both_legs_redeemed":
                sum(1 for r in pre_rows if r["both_legs_redeemed"]),
            "conditions_where_redeem_exceeds_entitlement_by_gt_1":
                sum(1 for r in pre_rows
                    if r["redeem_minus_entitlement"] > 1.0),
            "largest_redeem_minus_entitlement":
                round(max((r["redeem_minus_entitlement"]
                           for r in pre_rows), default=0.0), 2),
            "sum_redeem_minus_entitlement":
                round(sum(r["redeem_minus_entitlement"]
                          for r in pre_rows), 2),
            "why": ("redemption should pay $1 per winning share HELD. Cash "
                    "materially above entitlement would mean the ledger "
                    "credits shares L5 did not hold -- i.e. artificial "
                    "profit."),
        },
        "profit_attribution": ("NONE for our account. Historical "
                              "reconstruction of another trader's public "
                              "activity."),
    }
    with open("analysis/l5/PREINCENTIVE_AUDIT.json", "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
