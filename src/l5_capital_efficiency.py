"""What does the bankroll actually buy, per dollar and per hour?

OWNER item 3: "Measure capital efficiency: dollars locked, time locked,
completed pairs per bankroll-dollar-hour, idle time, turnover/day and
opportunity cost."

The unit is the DOLLAR-HOUR: cost basis committed to a market multiplied by
how long it stayed committed. It is the only unit in which a $19 position
held for eight minutes and a $2 position held for twenty are comparable, and
it is the denominator the owner asked every profitability number to be
expressed in.

THE ANCHORING PROBLEM, AND THE FIX. A market that pairs to zero releases its
capital at the moment the residual closes, and the campaign log records that
moment. A market that STRANDS residual does not release anything until the
venue settles it -- but the log only runs to the last row we happened to
capture, so reading lock time from the log understates a stranded position
by however long the tape happens to end early. Every stranded lock here is
therefore anchored to the venue's own settled_utc instead. The two families
use different end anchors on purpose, and each is the correct one for what
it measures:

    paired   first fill -> residual reaches zero        (campaign log)
    stranded first fill -> venue settlement             (venue)

Using the log for both would have made stranded capital look 25% cheaper
than it is.

WHAT IT SHOWS. Four stranded markets out of sixteen -- a quarter of the
cohort -- consume 44% of every capital-hour the campaign spent, and they are
also where all of the measured losses are. Stranded residual is expensive
twice: it loses money, and while it is losing money it is holding the only
concurrency slot the lane has.

THAT IS NOT AN ARGUMENT TO ABANDON RESIDUAL. L5 carries residual to
settlement and so do we; the HOLD policy is not in question here. It is an
argument that completion rate is the same lever for capital efficiency as it
is for P&L -- the two are not competing improvements, they are one.

READ-ONLY.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.l5_sports_clock_study import iso_epoch

# Confirmed credits, same source as the realized bridge. Never estimates.
CONFIRMED_MAKER_REBATES_USD = D("2.1300")
REBATE_FILLS = 293


def _f(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def first_fill_and_close(per_market: Mapping[str, Any]) -> Dict[str, dict]:
    """When each market took its first unit, and when it went flat again."""
    out: Dict[str, dict] = {}
    for slug, m in (per_market or {}).items():
        rows = sorted(m.get("rows") or [], key=lambda r: r.get("idx") or 0)
        first = last = closed = None
        for r in rows:
            t = iso_epoch(r.get("t_utc"))
            if t is None:
                continue
            last = t
            inv = r.get("inventory_after") or {}
            if first is None and ((_f(inv.get("up")) or 0) > 0
                                  or (_f(inv.get("dn")) or 0) > 0):
                first = t
            resid = _f(inv.get("residual"))
            if (first is not None and resid is not None
                    and abs(resid) < 1e-9 and closed is None):
                closed = t
        if first is not None:
            out[slug] = {"first_fill": first, "closed": closed, "last": last}
    return out


def lock_rows(cohort: Mapping[str, Any],
              fills: Mapping[str, dict]) -> List[dict]:
    """One row per settled market: dollars, seconds, dollar-hours.

    A market counts as PAIRED only if it both went flat in the log and shows
    no directional residual in the venue's own decomposition. Either signal
    alone has been wrong before -- one market's ledger reported zero residual
    units while the venue traded 4.34 more than we saw."""
    rows: List[dict] = []
    for m in cohort.get("markets") or []:
        slug = m.get("slug")
        f = fills.get(slug)
        if not f:
            continue
        cb = _f(m.get("cost_basis_usd"))
        settled = iso_epoch(m.get("settled_utc"))
        resid_units = _f(m.get("directional_residual_units"))
        paired = f["closed"] is not None and resid_units == 0
        log_lock = (f["closed"] if f["closed"] is not None
                    else f["last"]) - f["first_fill"]
        true_lock = (log_lock if paired
                     else ((settled - f["first_fill"]) if settled is not None
                           else log_lock))
        rows.append({
            "slug": slug,
            "paired": paired,
            "cost_basis_usd": cb,
            "log_lock_s": round(log_lock, 1),
            "lock_s": round(true_lock, 1),
            "lock_anchor": "residual reached zero (log)" if paired
                           else "venue settlement",
            "dollar_hours": (round(cb * true_lock / 3600.0, 6)
                             if cb is not None else None),
            "reconciles": bool((m.get("qty_reconciliation") or {}
                                ).get("reconciles")),
            "matched_pairs": _f(m.get("matched_pairs")),
            "matched_contribution_usd": _f(m.get("matched_contribution_usd")),
            "residual_contribution_usd": _f(m.get("residual_contribution_usd")),
        })
    return rows


def _sum(rows: Iterable[Mapping[str, Any]], key: str) -> float:
    return round(sum((r.get(key) or 0.0) for r in rows), 6)


def efficiency(rows: List[dict], *, span_hours: Optional[float] = None,
               fills_in_cohort: int = 0) -> dict:
    paired = [r for r in rows if r["paired"]]
    stranded = [r for r in rows if not r["paired"]]
    dh_all = _sum(rows, "dollar_hours")
    dh_p, dh_s = _sum(paired, "dollar_hours"), _sum(stranded, "dollar_hours")

    # money rates use only markets whose quantities reconcile
    ok = [r for r in rows if r["reconciles"]]
    dh_ok = _sum(ok, "dollar_hours")
    realized = round(_sum(ok, "matched_contribution_usd")
                     + _sum(ok, "residual_contribution_usd"), 6)
    pairs = _sum(ok, "matched_pairs")
    rebate = (float(CONFIRMED_MAKER_REBATES_USD) * fills_in_cohort
              / REBATE_FILLS) if fills_in_cohort else 0.0

    out: Dict[str, Any] = {
        "settled_markets": len(rows),
        "capital": {
            "total_dollar_hours": dh_all,
            "paired_markets": len(paired),
            "paired_dollar_hours": dh_p,
            "stranded_markets": len(stranded),
            "stranded_dollar_hours": dh_s,
            "stranded_share_of_markets": (round(len(stranded) / len(rows), 4)
                                          if rows else None),
            "stranded_share_of_dollar_hours": (round(dh_s / dh_all, 4)
                                               if dh_all else None),
            "median_lock_s_paired": _median([r["lock_s"] for r in paired]),
            "median_lock_s_stranded": _median([r["lock_s"] for r in stranded]),
            "max_lock_s": max((r["lock_s"] for r in rows), default=None),
        },
        "per_dollar_hour": {
            "basis": "reconciling settled markets only",
            "markets": len(ok),
            "dollar_hours": dh_ok,
            "completed_pairs": pairs,
            "completed_pairs_per_dollar_hour": (round(pairs / dh_ok, 4)
                                                if dh_ok else None),
            "realized_usd_pre_credit": realized,
            "realized_usd_per_dollar_hour_pre_credit": (
                round(realized / dh_ok, 6) if dh_ok else None),
            "confirmed_rebate_usd_attributable": round(rebate, 4),
            "realized_usd_per_dollar_hour_with_confirmed_credits": (
                round((realized + rebate) / dh_ok, 6) if dh_ok else None),
            "credits_note": (
                "maker rebates only, mapped per fill and therefore "
                "approximate; liquidity rewards are $0.00 credited and enter "
                "as zero, never at accrued face value"),
        },
    }
    if span_hours:
        out["turnover"] = {
            "cohort_span_hours": round(span_hours, 2),
            "settled_markets_per_day": round(len(rows) * 24.0 / span_hours, 2),
            "dollar_hours_consumed_per_day": round(dh_all * 24.0 / span_hours,
                                                   4),
            "mean_capital_in_use_usd": round(dh_all / span_hours, 4),
        }
    out["opportunity_cost"] = {
        "statement": (
            f"{len(stranded)} of {len(rows)} settled markets stranded "
            f"residual and consumed "
            f"{out['capital']['stranded_share_of_dollar_hours']:.1%} of every "
            "capital-hour the campaign spent. With concurrency 1 those hours "
            "are not merely idle capital -- they are the whole lane, because "
            "selection does not run while a position is held."
            if rows else "no settled markets"),
        "not_an_argument_to_abandon_residual": (
            "L5 carries residual to settlement and so do we; the HOLD policy "
            "is not in question. This says completion rate is the same lever "
            "for capital efficiency as for P&L, not that stranded inventory "
            "should be dumped."),
    }
    return out


def _median(xs: List[float]) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    n = len(v)
    return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 1)


def report(cohort_path: str = "analysis/l5/l5_r5f_cohort.json",
           logs: Optional[List[str]] = None) -> dict:
    from src import l5_r5f_sequence_ledger as sl
    with open(cohort_path, "rt", encoding="utf-8") as fh:
        cohort = json.load(fh)
    files = sl.expand_logs(logs or ["../r5f_logs"])
    led = sl.campaign_report(sl.harvest(files, sl.SHELL_PREFIX))
    rows = lock_rows(cohort, first_fill_and_close(led.get("per_market") or {}))
    ts = [iso_epoch(m.get("settled_utc")) for m in cohort.get("markets") or []]
    ts = [t for t in ts if t is not None]
    span = ((max(ts) - min(ts)) / 3600.0) if len(ts) > 1 else None
    fills = sum(int(m.get("fills") or 0) for m in cohort.get("markets") or [])
    rep = efficiency(rows, span_hours=span, fills_in_cohort=fills)
    rep["markets"] = sorted(rows, key=lambda r: -(r["dollar_hours"] or 0))
    rep["anchoring"] = (
        "Paired locks end when the residual reaches zero in the campaign log; "
        "stranded locks end at the venue's settled_utc. Reading both from the "
        "log would understate stranded capital by however long the tape ends "
        "early -- here about 25%.")
    return rep


def render_markdown(rep: Mapping[str, Any]) -> str:
    c, p = rep["capital"], rep["per_dollar_hour"]
    L = ["# Capital efficiency", "",
         f"{rep['settled_markets']} settled markets, "
         f"**{c['total_dollar_hours']} dollar-hours** of capital consumed.",
         "", "| | paired | stranded |", "|---|---|---|",
         f"| markets | {c['paired_markets']} | {c['stranded_markets']} |",
         f"| dollar-hours | {c['paired_dollar_hours']} | "
         f"{c['stranded_dollar_hours']} |",
         f"| median lock (s) | {c['median_lock_s_paired']} | "
         f"{c['median_lock_s_stranded']} |", "",
         f"**{c['stranded_share_of_markets']:.1%} of markets consume "
         f"{c['stranded_share_of_dollar_hours']:.1%} of the capital-hours.**",
         "", "## Per bankroll-dollar-hour", "",
         f"Basis: {p['basis']} ({p['markets']} markets, "
         f"{p['dollar_hours']} dollar-hours)", "",
         "| quantity | value |", "|---|---|",
         f"| completed pairs | {p['completed_pairs']} |",
         f"| completed pairs per dollar-hour | "
         f"**{p['completed_pairs_per_dollar_hour']}** |",
         f"| realized $ per dollar-hour, pre-credit | "
         f"**${p['realized_usd_per_dollar_hour_pre_credit']}** |",
         f"| confirmed rebate attributable | "
         f"${p['confirmed_rebate_usd_attributable']} |",
         f"| realized $ per dollar-hour, with confirmed credits | "
         f"**${p['realized_usd_per_dollar_hour_with_confirmed_credits']}** |",
         "", p["credits_note"], ""]
    if rep.get("turnover"):
        t = rep["turnover"]
        L += ["## Turnover", "",
              f"- span **{t['cohort_span_hours']} h**",
              f"- **{t['settled_markets_per_day']}** settled markets/day",
              f"- **{t['dollar_hours_consumed_per_day']}** dollar-hours/day",
              f"- mean capital in use **${t['mean_capital_in_use_usd']}**", ""]
    o = rep["opportunity_cost"]
    L += ["## Opportunity cost", "", o["statement"], "",
          "> " + o["not_an_argument_to_abandon_residual"], "",
          "## Anchoring", "", rep["anchoring"], "",
          "## Per market", "",
          "| market | paired | $ basis | lock s | anchor | $-hours |",
          "|---|---|---|---|---|---|"]
    for r in rep["markets"]:
        L.append(f"| `{r['slug']}` | {'yes' if r['paired'] else 'no'} | "
                 f"{r['cost_basis_usd']} | {r['lock_s']} | "
                 f"{r['lock_anchor']} | {r['dollar_hours']} |")
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", default="analysis/l5/l5_r5f_cohort.json")
    ap.add_argument("--logs", nargs="+", default=["../r5f_logs"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = report(a.cohort, a.logs)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "CAPITAL_EFFICIENCY.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "capital_efficiency.json"), "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
