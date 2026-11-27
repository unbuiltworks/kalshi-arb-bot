"""Reward calibration: venue reward rows over the bot's own THEORETICAL
reward estimates, by competition (and programme). READ-ONLY.

Engine flag PMUS_RN1_REWARD_CALIBRATION (see src/pmus_sports_two_sided.py).
Nothing here places, cancels or blocks an order, and nothing here changes a
setting: the ratio it reports is evidence for the owner, who sets
PMUS_RN1_REWARD_CALIBRATION_TABLE. It is never applied automatically.

Inputs
  rows   venue ``/v1/incentives/earnings`` rows (REWARD_CHECK rows_detail):
         marketSlug, date, an amount field, status, optional programId.
  theo   {slug: {day: theoretical USD}}: the bot's own accrual of the
         THEORETICAL reward rate of its resting legs (``rn1_reward_est``,
         the full programme pool credited to the market under the venue's
         published scoring rule, no haircut) times resting time, keyed by
         the venue's reward day.

Output, per competition: venue USD by status, theoretical USD of the
market-days with a row, ratios actual / theoretical on the settled rows
(PAID over the theoretical of PAID + SKIPPED rows: preferred, money) and on
the PENDING rows (flagged: accrued, not credited),
sample counts, and the theoretical USD of market-days with no row at all
(not in any ratio: the venue may not have posted the day yet).
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import pmus_campaign_economics as CE

LABEL = ("actual / THEORETICAL reward by competition; PAID rows are money, "
         "PENDING rows are accrued and NOT credited; evidence for the "
         "calibration table, never applied automatically")
AMOUNT_KEYS = ("amount", "amountUsd", "usd", "value", "reward", "earned",
               "total")


def row_amount(row: Mapping[str, Any]) -> Optional[D]:
    for k in AMOUNT_KEYS:
        v = CE._d(row.get(k))
        if v is not None:
            return v
    return None


def _blank() -> Dict[str, Any]:
    return {"rows": 0, "rows_matched": 0, "paid_usd": D(0),
            "pending_usd": D(0), "skipped_usd": D(0), "other_usd": D(0),
            "theo_paid_usd": D(0), "theo_pending_usd": D(0),
            "theo_skipped_usd": D(0), "n_paid": 0, "n_pending": 0,
            "n_skipped": 0, "theo_no_row_usd": D(0), "n_no_row": 0,
            "paid_matched_usd": D(0), "pending_matched_usd": D(0)}


def _ratio(a: D, b: D) -> Optional[float]:
    return round(float(a / b), 4) if b > 0 else None


def calibrate(rows: Any, theo: Mapping[str, Mapping[str, Any]],
              program_rows: bool = True) -> Dict[str, Any]:
    """See module doc. Pure; never raises on a malformed row (skipped)."""
    by: Dict[str, Dict[str, Any]] = {}
    by_prog: Dict[str, Dict[str, Any]] = {}
    seen = set()
    for r in rows or ():
        if not isinstance(r, Mapping):
            continue
        if str(r.get("programType") or "liquidityProgram") != \
                "liquidityProgram":
            continue
        slug = str(r.get("marketSlug") or "")
        day = CE._row_day(r)
        amt = row_amount(r)
        if not slug or day is None or amt is None:
            continue
        comp = CE.sport_of(slug)
        b = CE.bucket(r.get("status"))
        th = CE._d((theo.get(slug) or {}).get(day))
        groups = [by.setdefault(comp, _blank())]
        if program_rows and r.get("programId"):
            groups.append(by_prog.setdefault(str(r["programId"]), _blank()))
        for g in groups:
            g["rows"] += 1
            g[b + "_usd"] += amt
            if th is not None and th > 0:
                g["rows_matched"] += 1
                if b in ("paid", "pending", "skipped"):
                    g["theo_%s_usd" % b] += th
                    g["n_%s" % b] += 1
                if b in ("paid", "pending"):
                    g["%s_matched_usd" % b] += amt
        seen.add((slug, day))
    for slug, days in (theo or {}).items():
        for day, v in (days or {}).items():
            th = CE._d(v)
            if th is None or th <= 0 or (slug, day) in seen:
                continue
            g = by.setdefault(CE.sport_of(slug), _blank())
            g["theo_no_row_usd"] += th
            g["n_no_row"] += 1

    def fin(g: Dict[str, Any]) -> Dict[str, Any]:
        # settled rows: PAID is money, SKIPPED was not paid (actual 0)
        rp = _ratio(g["paid_matched_usd"], g["theo_paid_usd"] +
                    g["theo_skipped_usd"]) \
            if (g["n_paid"] + g["n_skipped"]) else None
        rq = _ratio(g["pending_matched_usd"], g["theo_pending_usd"]) \
            if g["n_pending"] else None
        # every market-day the bot estimated, a day with no venue row
        # counted as zero (lower bound; the venue may not have posted it)
        th_all = g["theo_paid_usd"] + g["theo_pending_usd"] + \
            g["theo_skipped_usd"] + g["theo_no_row_usd"]
        r0 = _ratio(g["paid_matched_usd"] + g["pending_matched_usd"],
                    th_all) if th_all > 0 else None
        basis = "PAID" if rp is not None else (
            "PENDING_NOT_CREDITED" if rq is not None else None)
        out = {k: (str(v) if isinstance(v, D) else v) for k, v in g.items()}
        out.update(ratio_paid=rp, ratio_pending=rq,
                   ratio_no_row_as_zero=r0,
                   ratio=rp if rp is not None else rq, basis=basis)
        return out
    return {"by_competition": {k: fin(v) for k, v in sorted(by.items())},
            "by_program": {k: fin(v) for k, v in sorted(by_prog.items())},
            "label": LABEL}
