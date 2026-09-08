"""What completion rate does this clone need to break even?

OWNER: "Calculate the clone's break-even completion rate from observed pair
profit, residual loss, confirmed rebates/rewards and fees."

THE NUMBER. Every opened leg ends one of two ways: it gets completed into a
pair, or it is stranded as directional residual and carried to settlement.
So solve for p in

    p * pair_profit_per_matched_pair
  + (1 - p) * residual_loss_per_residual_unit
  + confirmed_credit_per_leg  =  0

THE BASIS, AND WHY IT IS NOT THE OBVIOUS ONE. The obvious cut is per MARKET:
compare markets that paired to zero against markets that stranded residual.
That cut is contaminated and the contamination is definitional -- a market
that pairs to zero leaves no residual to settle, so the venue never publishes
a realizedPnl for it and it can ONLY appear on the derived side. Every
venue-confirmed market is negative and every derived market is positive
partly by construction, and no amount of arithmetic on that split removes it.

The cohort already carries the cut that does work: each market reports
matched_contribution_usd and residual_contribution_usd SEPARATELY, on the
same rows, from the same ledger. Four of five venue-confirmed markets carry
BOTH a matched contribution and a residual one. So pair economics and
residual economics can be measured WITHIN markets rather than across two
populations that were never randomly assigned. That is the primary basis
here. The per-market view is kept below it as a cross-check, carrying its
own warning, because a discarded number is easier to argue with than a
hidden one.

WHAT IS EXCLUDED AND WHY. aec-setkameua-vervas-kohboh does not reconcile:
the venue traded 13.82 units, our ledger saw 9.48, delta 4.34. Its
directional_residual_units reads 0.00, which is our ledger's blindness and
not a flat book, and its residual_contribution is therefore a plug
(realized - matched) rather than an observed quantity. Including it would
put 1.91 dollars of loss in the numerator against zero units in the
denominator. It leaves BOTH sides of every per-unit rate. Its
paired_to_zero flag is wrong for the same reason.

CREDITS ARE CONFIRMED-ONLY AND THEIR MAPPING IS APPROXIMATE. $2.1300 of
maker rebates is venue-credited across 293 fills. Rebates scale with maker
NOTIONAL, and we hold a per-fill figure, so mapping them onto opened legs is
an approximation and is labelled one. Liquidity rewards enter as $0.0000 --
every row is SKIPPED or PENDING, and accrued face value is never revenue.
Fees are $0.0000 CONFIRMED, not assumed: the venue charged none against 293
maker fills.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

# ---- confirmed money, from the realized bridge (venue numbers only) -------
CONFIRMED_MAKER_REBATES_USD = D("2.1300")      # our side, 293 fills
CONFIRMED_FEES_USD = D("0.0000")               # maker-only; venue charged none
CONFIRMED_LIQUIDITY_REWARDS_USD = D("0.0000")  # all rows SKIPPED / PENDING
REBATE_FILLS = 293
REBATE_MARKETS = 55                            # markets with any fill

EXCLUDED_NON_RECONCILING = (
    "a market whose venue and ledger quantities disagree is refused every "
    "per-unit rate: its residual units may be a missed fill rather than a "
    "flat book, which would put loss in the numerator against no units in "
    "the denominator")


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


def reconciles(m: Mapping[str, Any]) -> bool:
    return bool((m.get("qty_reconciliation") or {}).get("reconciles"))


# ------------------------------------------------------------- primary basis
def leg_economics(markets: List[Mapping[str, Any]]) -> dict:
    """Pair and residual economics measured WITHIN markets, per unit.

    matched_pairs counts PAIRS (two units); directional_residual_units counts
    single unmatched units. The identity 2*pairs + residual == ledger_qty
    holds on every reconciling row and is asserted, because a decomposition
    that does not add up to the ledger is not a decomposition."""
    used, skipped = [], []
    pairs = resid = mc = rc = D("0")
    fills = 0
    for m in markets:
        if not reconciles(m):
            skipped.append({"slug": m.get("slug"),
                            "why": "qty_reconciliation.reconciles is false",
                            "qty_delta": (m.get("qty_reconciliation")
                                          or {}).get("qty_delta")})
            continue
        p = _d(m.get("matched_pairs")) or D("0")
        r = _d(m.get("directional_residual_units")) or D("0")
        pairs += p
        resid += r
        mc += _d(m.get("matched_contribution_usd")) or D("0")
        rc += _d(m.get("residual_contribution_usd")) or D("0")
        fills += int(m.get("fills") or 0)
        used.append(m.get("slug"))
    legs = pairs + resid            # every opened leg: paired, or stranded
    return {
        "markets_used": len(used),
        "markets_excluded": skipped,
        "excluded_because": EXCLUDED_NON_RECONCILING,
        "fills": fills,
        "matched_pairs": pairs,
        "residual_units": resid,
        "opened_legs": legs,
        "matched_contribution_usd": mc,
        "residual_contribution_usd": rc,
        "pair_profit_per_matched_pair": (mc / pairs) if pairs else None,
        "residual_loss_per_residual_unit": (rc / resid) if resid else None,
        "observed_leg_completion_rate": (pairs / legs) if legs else None,
    }


def credit_per_leg(fills: int, legs: D) -> dict:
    """Confirmed rebates mapped onto opened legs. APPROXIMATE, and said so.

    We hold rebates per FILL. A fill can cover many units, so rebate-per-leg
    is rebate-per-fill scaled by this cohort's own fills-per-leg. Rebates
    actually accrue on maker notional, so this is a mapping and not a
    measurement -- which is why the break-even below is reported as a RANGE
    bracketed by the with-credit and without-credit answers."""
    per_fill = (CONFIRMED_MAKER_REBATES_USD / D(REBATE_FILLS)
                if REBATE_FILLS else D("0"))
    per_leg = ((per_fill * D(fills) / legs).quantize(D("0.000001"))
               if (legs and fills) else D("0"))
    return {
        "rebate_per_fill_usd": per_fill.quantize(D("0.000001")),
        "cohort_fills": fills,
        "cohort_attributable_rebate_usd":
            (per_fill * D(fills)).quantize(D("0.0001")),
        "rebate_per_opened_leg_usd": per_leg,
        "liquidity_rewards_usd": CONFIRMED_LIQUIDITY_REWARDS_USD,
        "fees_usd": CONFIRMED_FEES_USD,
        "mapping_is_approximate": (
            "rebates accrue on maker NOTIONAL; we hold a per-fill figure and "
            "scale it by this cohort's fills-per-leg. The break-even is "
            "therefore a RANGE, not a point."),
        "rewards_are_zero_because": (
            "every liquidity-reward row is SKIPPED or PENDING; accrued face "
            "value is never revenue"),
    }


def breakeven_p(pair_profit: D, residual_loss: D,
                credit: D = D("0")) -> Optional[float]:
    """p where expected value per opened leg is exactly zero.

    residual_loss is SIGNED (negative for a loss)."""
    denom = pair_profit - residual_loss
    if denom == 0:
        return None
    return float(round((-residual_loss - credit) / denom, 6))


# ----------------------------------------------------------- secondary basis
def split_cohort(markets: List[Mapping[str, Any]]) -> dict:
    """Settled markets by VALUE BASIS, never pooled. Cross-check only."""
    conf, lock, other = [], [], []
    for m in markets:
        basis = str(m.get("value_basis") or "")
        v = _d(m.get("realized_usd_venue")) if basis.startswith(
            "venue_confirmed") else _d(m.get("locked_usd") or m.get("value_usd"))
        row = {"slug": m.get("slug"), "usd": v, "basis": basis}
        if v is None:
            other.append(row)
        elif basis.startswith("venue_confirmed"):
            conf.append(row)
        elif basis.startswith("derived_locked"):
            lock.append(row)
        else:
            other.append(row)
    return {"venue_confirmed": conf, "derived_locked": lock,
            "unclassified": other}


def _mean(rows: List[dict]) -> Optional[D]:
    vals = [r["usd"] for r in rows if r["usd"] is not None]
    return (sum(vals) / D(len(vals))) if vals else None


def per_market_crosscheck(markets: List[Mapping[str, Any]]) -> dict:
    sp = split_cohort(markets)
    conf, lock = sp["venue_confirmed"], sp["derived_locked"]
    n = len(conf) + len(lock)
    pp, rl = _mean(lock), _mean(conf)
    obs = round(len(lock) / n, 6) if n else None
    out: Dict[str, Any] = {
        "settled_markets": n,
        "paired_to_zero_derived": len(lock),
        "stranded_residual_venue_confirmed": len(conf),
        "observed_pairing_rate_market_level": obs,
        "pair_profit_per_paired_market_usd": pp,
        "residual_loss_per_stranded_market_usd": rl,
        "warning": (
            "CONTAMINATED AND KEPT ANYWAY. Every venue-confirmed market is "
            "negative and every derived market positive partly BY "
            "CONSTRUCTION: a market that pairs to zero leaves no residual to "
            "settle, so the venue never publishes a realizedPnl for it and it "
            "can only appear on the derived side. Read the per-leg basis "
            "above; this is here so the weaker cut can be argued with rather "
            "than hidden."),
    }
    if pp is not None and rl is not None:
        credit_per_market = ((CONFIRMED_MAKER_REBATES_USD - CONFIRMED_FEES_USD)
                             / D(REBATE_MARKETS))
        out["breakeven_excluding_credits"] = breakeven_p(pp, rl)
        out["breakeven_including_credits"] = breakeven_p(pp, rl,
                                                         credit_per_market)
        out["credit_per_market_usd"] = credit_per_market
    return out


# ----------------------------------------------------------------- the report
def report(cohort_path: str = "analysis/l5/l5_r5f_cohort.json") -> dict:
    with open(cohort_path, "rt", encoding="utf-8") as fh:
        cohort = json.load(fh)
    markets = cohort.get("markets") or []
    econ = leg_economics(markets)
    cred = credit_per_leg(econ["fills"], econ["opened_legs"])

    pp = econ["pair_profit_per_matched_pair"]
    rl = econ["residual_loss_per_residual_unit"]
    obs = econ["observed_leg_completion_rate"]

    out: Dict[str, Any] = {
        "basis": "per opened leg, measured WITHIN markets, reconciling only",
        "markets_used": econ["markets_used"],
        "markets_excluded": econ["markets_excluded"],
        "excluded_because": econ["excluded_because"],
        "matched_pairs": str(econ["matched_pairs"]),
        "residual_units": str(econ["residual_units"]),
        "opened_legs": str(econ["opened_legs"]),
        "matched_contribution_usd": str(econ["matched_contribution_usd"]),
        "residual_contribution_usd": str(econ["residual_contribution_usd"]),
        "pair_profit_per_matched_pair_usd":
            str(pp.quantize(D("0.000001"))) if pp is not None else None,
        "residual_loss_per_residual_unit_usd":
            str(rl.quantize(D("0.000001"))) if rl is not None else None,
        "observed_leg_completion_rate":
            float(round(obs, 6)) if obs is not None else None,
        "confirmed_credits": {k: (str(v) if isinstance(v, D) else v)
                              for k, v in cred.items()},
        "per_market_crosscheck": {
            k: (str(v) if isinstance(v, D) else v)
            for k, v in per_market_crosscheck(markets).items()},
    }
    if pp is None or rl is None or obs is None:
        out["breakeven"] = {"available": False,
                            "why": "one side of the decomposition is empty"}
        return out

    be_ex = breakeven_p(pp, rl)
    be_in = breakeven_p(pp, rl, cred["rebate_per_opened_leg_usd"])
    spread = pp - rl                       # value of one completion point x100
    out["breakeven"] = {
        "excluding_credits": be_ex,
        "including_confirmed_credits": be_in,
        "observed": float(round(obs, 6)),
        "range_reading": (
            "the two figures BRACKET the answer: the credit mapping is "
            "approximate, so the truth sits between them"),
        "gap_percentage_points_vs_including_credits":
            round((float(obs) - be_in) * 100, 3),
        "gap_percentage_points_vs_excluding_credits":
            round((float(obs) - be_ex) * 100, 3),
        "verdict": ("BELOW BREAK-EVEN on both ends of the range"
                    if float(obs) < min(be_ex, be_in) else
                    "ABOVE BREAK-EVEN on both ends of the range"
                    if float(obs) > max(be_ex, be_in) else
                    "INSIDE THE RANGE -- the credit mapping decides the sign, "
                    "which means it cannot be called on this evidence"),
    }
    out["marginal_value"] = {
        "usd_per_opened_leg_per_completion_point":
            str((spread / D(100)).quantize(D("0.000001"))),
        "usd_over_this_cohort_per_completion_point":
            str((spread * econ["opened_legs"] / D(100)).quantize(D("0.0001"))),
        "completion_points_needed_to_reach_breakeven":
            round((be_in - float(obs)) * 100, 3),
        "reading": (
            "a residual unit costs "
            f"{float(-rl / pp):.1f}x what a matched pair earns, which is why "
            "completion rate dominates every other lever measured so far"),
    }
    return out


def render_markdown(rep: Mapping[str, Any]) -> str:
    b = rep.get("breakeven") or {}
    c = rep["confirmed_credits"]
    L = ["# Break-even completion rate", "",
         f"Basis: **{rep['basis']}** — "
         f"{rep['markets_used']} settled markets, "
         f"{rep['opened_legs']} opened legs "
         f"({rep['matched_pairs']} matched pairs, "
         f"{rep['residual_units']} residual units)", "",
         "| quantity | value |", "|---|---|",
         f"| pair profit per matched pair | "
         f"${rep['pair_profit_per_matched_pair_usd']} |",
         f"| residual loss per residual unit | "
         f"${rep['residual_loss_per_residual_unit_usd']} |",
         f"| confirmed maker rebate per opened leg | "
         f"${c['rebate_per_opened_leg_usd']} *(approximate mapping)* |",
         f"| confirmed liquidity rewards | ${c['liquidity_rewards_usd']} |",
         f"| confirmed fees | ${c['fees_usd']} |", ""]
    if b.get("available") is False:
        return "\n".join(L + [f"**Unavailable** — {b.get('why')}"]) + "\n"
    L += ["| leg completion rate | value |", "|---|---|",
          f"| break-even, excluding credits | **{b['excluding_credits']:.2%}** |",
          f"| break-even, including confirmed credits | "
          f"**{b['including_confirmed_credits']:.2%}** |",
          f"| **observed** | **{b['observed']:.2%}** |",
          f"| gap vs the credited end | "
          f"**{b['gap_percentage_points_vs_including_credits']:+.2f} pp** |", "",
          f"**{b['verdict']}.** {b['range_reading']}.", "",
          "## What a completion point is worth", ""]
    mv = rep["marginal_value"]
    L += [f"- **${mv['usd_per_opened_leg_per_completion_point']} per opened "
          f"leg** for each percentage point of completion rate",
          f"- **${mv['usd_over_this_cohort_per_completion_point']}** per point "
          f"across this cohort",
          f"- **{mv['completion_points_needed_to_reach_breakeven']:+.2f} pp** "
          f"still needed to reach break-even",
          f"- {mv['reading']}", "",
          "## Excluded", ""]
    for x in rep["markets_excluded"]:
        L.append(f"- `{x['slug']}` — {x['why']} (qty_delta {x['qty_delta']})")
    L += ["", rep["excluded_because"], "",
          "## Credits", "", c["mapping_is_approximate"], "",
          c["rewards_are_zero_because"], "",
          "## Per-market cross-check (weaker cut, kept on purpose)", ""]
    x = rep["per_market_crosscheck"]
    L += [f"- markets paired to zero **{x['paired_to_zero_derived']}** / "
          f"stranded **{x['stranded_residual_venue_confirmed']}** "
          f"→ observed **{x['observed_pairing_rate_market_level']:.2%}**",
          f"- break-even on that cut: "
          f"**{x.get('breakeven_including_credits', float('nan')):.2%}** "
          f"including credits", "", x["warning"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", default="analysis/l5/l5_r5f_cohort.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = report(a.cohort)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "BREAKEVEN.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "breakeven.json"), "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
