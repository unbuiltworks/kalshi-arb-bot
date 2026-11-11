"""OWNER 5588925913: the one bridge from settlement to actual realized net,
built so a rebate can never be counted twice and never be counted early.

    settlement / trading P&L
  + confirmed maker rebates
  + confirmed liquidity rewards already posted
  - fees
  = actual realized net

WHY THE DOUBLE-COUNT QUESTION HAS AN ARITHMETIC ANSWER, NOT AN OPINION.
The venue's per-market `realizedPnl` reproduces EXACTLY as
`settling_side_qty x $1.00 - cost_basis` on every settled market where the
ledger holds both sides -- 3 of 3, to the cent, diff 0.0000. There is no
rebate term and no fee term in it. So line A is pure settlement cashflow
and lines B, C and D are genuinely additive rather than overlapping.
`assert_no_double_count` re-runs that reproduction and refuses to publish
a bridge if any market stops reproducing, because the day the venue starts
folding credits into realizedPnl is the day this bridge silently
double-counts them.

WHAT COUNTS AS CREDITED, AND WHAT EMPHATICALLY DOES NOT.
  * Maker rebates: the venue's own signed `commissionNotionalCollected` on
    whichever execution is OURS. Negative on the venue means paid to us.
    OBSERVED money, already in the balance.
  * Liquidity rewards: ONLY rows whose status says the venue actually paid.
    Observed statuses so far are SKIPPED and PENDING -- neither is money.
    An accrual is not a credit, and a status we have never seen before is
    treated as not-credited until it is understood.
  * NON-TRADING credits (the $10.00 sep5_outage goodwill remediation) are
    quarantined in their own memo line. They are in the balance and they
    are not performance. Counting them would make the strategy look $10
    better than it is, which is the same error as double-counting rebates,
    pointing the other way.

READ-ONLY. This module consumes evidence that already exists; it holds no
credentials, opens no connection, and cannot reach the order path.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Iterable, List, Mapping, Optional

CENT = D("0.01")

# Statuses on a reward row that mean the venue actually paid. Anything else
# -- including a status we have never seen -- is an accrual, not money.
CREDITED_REWARD_STATUSES = ("PAID", "CREDITED", "COMPLETED", "SETTLED",
                            "DISTRIBUTED")

# Activity descriptions that are cash in the account but NOT trading income.
NON_TRADING_CREDIT_KINDS = ("remediation", "goodwill", "referral", "bonus",
                            "promotion")


def _d(v: Any) -> Optional[D]:
    if v in (None, "", "None"):
        return None
    try:
        return D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


def reproduce_realized(market: Mapping[str, Any]) -> dict:
    """Does the venue's realizedPnl equal pure settlement cashflow?

    This is the double-count test. `payout - cost_basis` with no credit
    term must equal the venue's own number; a non-zero difference the size
    of a plausible rebate is the signature of the venue having folded
    credits in, and the bridge must not be published on that day."""
    econ = market.get("economics") or {}
    if not econ.get("realized_confirmed"):
        return {"reproduces": None, "why": "not venue-confirmed"}
    dv = econ.get("derived") or {}
    side = econ.get("paying_side")
    qty = _d(dv.get("bought_no") if side == "NO" else dv.get("bought_yes"))
    cost = _d(dv.get("cost_basis_usd"))
    venue = _d(econ.get("realized_usd_venue"))
    if qty is None or cost is None or venue is None:
        return {"reproduces": None, "why": "ledger lacks both sides"}
    calc = qty * D(1) - cost
    diff = (venue - calc).quantize(D("0.0001"))
    return {"reproduces": diff == 0, "venue_usd": str(venue),
            "cashflow_usd": str(calc.quantize(D("0.0001"))),
            "difference_usd": str(diff), "paying_side": side}


def assert_no_double_count(markets: Mapping[str, Any]) -> dict:
    tested, ok, failures = 0, 0, []
    for slug, m in markets.items():
        r = reproduce_realized(m)
        if r["reproduces"] is None:
            continue
        tested += 1
        if r["reproduces"]:
            ok += 1
        else:
            failures.append({"slug": slug, **r})
    return {
        "markets_tested": tested, "markets_reproducing_exactly": ok,
        "failures": failures,
        "safe_to_add_credits": bool(tested and not failures),
        "meaning": (
            "The venue's realizedPnl is pure settlement cashflow "
            "(payout - cost_basis), carrying no rebate and no fee, so "
            "credits are additive to it rather than already inside it. "
            "A failure here means the venue changed that and the bridge "
            "would double-count; it refuses instead."),
    }


def rewards_by_status(rows: Iterable[Mapping[str, Any]]) -> dict:
    """Reward rows split by whether the venue actually paid them.

    The earlier audit summed every row into one `total_usd` of $0.93 and
    that number was quoted as though it were income. It is not: every row
    observed so far is SKIPPED or PENDING."""
    credited, accrued = D("0"), D("0")
    by_status: dict = {}
    for row in rows:
        amt = _d(row.get("reward")) or _d(row.get("amount")) or D("0")
        st = str(row.get("status") or "STATUS_ABSENT").upper()
        b = by_status.setdefault(st, {"n": 0, "usd": D("0")})
        b["n"] += 1
        b["usd"] += amt
        if st in CREDITED_REWARD_STATUSES:
            credited += amt
        else:
            accrued += amt
    return {
        "credited_usd": str(credited),
        "accrued_but_not_credited_usd": str(accrued),
        "by_status": {k: {"n": v["n"], "usd": str(v["usd"])}
                      for k, v in sorted(by_status.items())},
        "credited_statuses": list(CREDITED_REWARD_STATUSES),
        "note": ("Only a status the venue uses for money paid counts. An "
                 "unrecognised status is an accrual until it is understood, "
                 "never income."),
    }


def split_non_trading_credits(credit_rows: Iterable[Mapping[str, Any]]) -> dict:
    """Cash in the account that is not performance.

    LIVE: a single $10.00 ACTIVITY_TYPE_TRANSFER described as
    {"kind":"remediation","event":"sep5_outage","action":"goodwill"}. It
    makes the balance look $10 better than the strategy earned."""
    non_trading, other = D("0"), D("0")
    rows_out: List[dict] = []
    for row in credit_rows:
        chg = row.get("accountBalanceChange") or {}
        amt = _d((chg.get("amount") or {}).get("value")) or D("0")
        desc = str(chg.get("description") or "")
        kind = next((k for k in NON_TRADING_CREDIT_KINDS
                     if k in desc.lower()), None)
        if kind:
            non_trading += amt
            rows_out.append({"usd": str(amt), "kind": kind,
                             "type": row.get("type"),
                             "time": chg.get("createTime")})
        else:
            other += amt
    return {"non_trading_credit_usd": str(non_trading),
            "unclassified_credit_usd": str(other),
            "rows": rows_out,
            "note": ("Held OUT of the bridge on purpose: it is in the "
                     "balance and it is not trading income.")}


def bridge(*, settlement_usd: Any, maker_rebates_usd: Any,
           rewards: Mapping[str, Any], fees_usd: Any,
           non_trading: Mapping[str, Any],
           double_count: Mapping[str, Any]) -> dict:
    """The five lines, and a refusal if the additivity test did not pass."""
    a = _d(settlement_usd) or D("0")
    b = _d(maker_rebates_usd) or D("0")
    c = _d(rewards.get("credited_usd")) or D("0")
    dd = _d(fees_usd) or D("0")
    net = a + b + c - dd
    publishable = bool(double_count.get("safe_to_add_credits"))
    return {
        "A_settlement_or_trading_pnl_usd": str(a),
        "B_confirmed_maker_rebates_usd": str(b),
        "C_confirmed_liquidity_rewards_usd": str(c),
        "D_fees_usd": str(dd),
        "actual_realized_net_usd": str(net.quantize(D("0.0001")))
        if publishable else None,
        "refused": None if publishable else (
            "the venue's realizedPnl no longer reproduces as pure "
            "settlement cashflow, so adding credits to it would "
            "double-count them"),
        "memo_not_performance": {
            "non_trading_credits_usd": non_trading.get(
                "non_trading_credit_usd"),
            "rewards_accrued_not_credited_usd": rewards.get(
                "accrued_but_not_credited_usd"),
        },
        "double_count_test": double_count,
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    b = rep["bridge"]
    L = ["# Realized bridge: settlement -> actual realized net", "",
         "| line | usd |", "|---|---|",
         f"| A. Settlement / trading P&L (venue realizedPnl) | "
         f"{b['A_settlement_or_trading_pnl_usd']} |",
         f"| B. + confirmed maker rebates (credited at fill) | "
         f"{b['B_confirmed_maker_rebates_usd']} |",
         f"| C. + confirmed liquidity rewards posted | "
         f"{b['C_confirmed_liquidity_rewards_usd']} |",
         f"| D. - fees charged to us | {b['D_fees_usd']} |",
         f"| **= actual realized net** | "
         f"**{b['actual_realized_net_usd'] or b['refused']}** |", ""]
    m = b["memo_not_performance"]
    L += ["## Memo — in the balance, NOT performance", "",
          f"- Non-trading credits (goodwill / remediation): "
          f"**{m['non_trading_credits_usd']}**",
          f"- Liquidity rewards accrued but not credited: "
          f"**{m['rewards_accrued_not_credited_usd']}**", ""]
    d = b["double_count_test"]
    L += ["## Double-count test", "",
          f"- Markets tested: {d['markets_tested']}; reproducing exactly: "
          f"{d['markets_reproducing_exactly']}",
          f"- Safe to add credits: **{d['safe_to_add_credits']}**",
          f"- {d['meaning']}", ""]
    r = rep.get("rewards") or {}
    if r.get("by_status"):
        L += ["## Liquidity rewards by status", "",
              "| status | rows | usd | counted as income |", "|---|---|---|---|"]
        for st, v in r["by_status"].items():
            L.append(f"| {st} | {v['n']} | {v['usd']} | "
                     f"{'yes' if st in r['credited_statuses'] else 'NO'} |")
        L.append("")
        L.append(r["note"])
    return "\n".join(L) + "\n"


def build(sequence_path: str, *, settlement_usd: Any, maker_rebates_usd: Any,
          fees_usd: Any, reward_rows: Iterable[Mapping[str, Any]] = (),
          credit_rows: Iterable[Mapping[str, Any]] = ()) -> dict:
    with open(sequence_path, "rt", encoding="utf-8") as fh:
        seq = json.load(fh)
    dc = assert_no_double_count(seq.get("per_market") or {})
    rw = rewards_by_status(reward_rows)
    nt = split_non_trading_credits(credit_rows)
    return {"bridge": bridge(settlement_usd=settlement_usd,
                             maker_rebates_usd=maker_rebates_usd,
                             rewards=rw, fees_usd=fees_usd,
                             non_trading=nt, double_count=dc),
            "rewards": rw, "non_trading": nt}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sequence", default="analysis/l5/r5f_day1_sequence.json")
    ap.add_argument("--settlement-usd", required=True)
    ap.add_argument("--maker-rebates-usd", required=True)
    ap.add_argument("--fees-usd", required=True)
    ap.add_argument("--audit-json", default=None,
                    help="incentive_audit row, for reward and credit rows")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rewards, credits = [], []
    if a.audit_json and os.path.exists(a.audit_json):
        with open(a.audit_json, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
        rewards = (doc.get("earnings") or {}).get("rows_detail") or []
        credits = (doc.get("activities") or {}).get("credit_rows") or []
    rep = build(a.sequence, settlement_usd=a.settlement_usd,
                maker_rebates_usd=a.maker_rebates_usd, fees_usd=a.fees_usd,
                reward_rows=rewards, credit_rows=credits)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "REALIZED_BRIDGE.md"), "w") as fh:
            fh.write(md)
        with open(os.path.join(a.out, "realized_bridge.json"), "w") as fh:
            json.dump(rep, fh, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
