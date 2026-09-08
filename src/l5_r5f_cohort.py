"""R5F settled-cohort economics for the owner's profitability gate.

OWNER #167 comment 5577286614: "close the R5F economics cohort honestly:
exact decomposition pair economics / directional residual / credited
rebates / fees / settlement cashflow / venue-confirmed realized, no
theoretical P&L in realized" and, for scaling, "no size/concurrency
increase without a clean settled R5F cohort showing positive realized
expectancy with uncertainty stated".

Source of truth is the probe's own `PMUS_L5_CAMPAIGN {"kind":"settlement"}`
rows, which carry a `breakdown` produced from ledger fills plus, when the
venue supplies one, `realized_usd` with `realized_confirmed: true`. This
module reads log dumps only; it never touches the venue, the ledger or any
live path.

THE ONE ACCOUNTING IDEA THAT MAKES THIS COHORT READABLE
-------------------------------------------------------
A market that settles with `directional_residual == 0` is **arithmetically
locked** before resolution: every unit bought on one side is matched by a
unit on the other, the pair pays exactly 1.00 at settlement whichever side
wins, so the outcome is

    matched_pairs x (1.00 - matched_pair_cost)

and it does not depend on the result of the game at all. That is why this
module reports such markets as LOCKED_ZERO_RESIDUAL and states their value
even when the venue has not yet published `realizedPnl`.

It is still DERIVED, and it is stated as derived, for two reasons that are
never glossed: the venue reports `fees_usd: "venue_unreported"` on every
row, so these figures are PRE-FEE; and maker rebates are credited
separately and are not included here. A locked figure is not a realized
figure and is never summed into one.

Classification, in decreasing order of authority:
  VENUE_CONFIRMED          venue supplied realized_usd (realized_confirmed)
  LOCKED_ZERO_RESIDUAL     settled, residual == 0 -> value is determined
  INDETERMINATE            settled, residual != 0, venue gave no number
  PENDING                  not settled yet

Usage:
    python3 -m src.l5_r5f_cohort --logs 'dir/*.txt' --out analysis/l5
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import random
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

# R5F-RUN-2026-09-07 consumed its approval at this instant (probe log).
R5F_START_UTC = "2026-09-07T19:21:39Z"

VENUE_CONFIRMED = "VENUE_CONFIRMED"
LOCKED_ZERO_RESIDUAL = "LOCKED_ZERO_RESIDUAL"
INDETERMINATE = "INDETERMINATE"
PENDING = "PENDING"

LABELS = (
    "Venue-confirmed realized comes from the venue only. Locked figures are "
    "DERIVED from venue-confirmed fills and are exact ONLY because a "
    "zero-residual market pays 1.00 per matched pair regardless of the "
    "result; they are PRE-FEE (the venue reports fees_usd as "
    "venue_unreported) and exclude separately credited maker rebates. "
    "Confirmed and derived are never summed into a single 'realized'."
)


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


def iter_settlement_rows(paths: Iterable[str]):
    """Yield (timestamp, settlement object) for every settlement row that
    carries a breakdown, from Railway log dumps."""
    for path in paths:
        try:
            doc = json.loads(Path(path).read_text())
        except Exception:                              # noqa: BLE001
            continue
        for row in doc.get("deploy") or []:
            msg = row.get("message") or ""
            # cheap pre-filter, then the parsed object decides -- the live
            # logs are compact JSON but nothing here may depend on spacing
            if "settlement" not in msg or "{" not in msg:
                continue
            try:
                obj = json.loads(msg[msg.index("{"):])
            except Exception:                          # noqa: BLE001
                continue
            if obj.get("kind") != "settlement" or not obj.get("breakdown"):
                continue
            yield row.get("timestamp") or "", obj


def latest_by_slug(rows: Iterable[tuple]) -> dict:
    """A market is logged repeatedly as it moves PENDING -> SETTLED; keep
    the newest row per slug so each market is counted exactly once."""
    out: dict[str, tuple] = {}
    for ts, obj in rows:
        slug = obj.get("slug")
        if not slug:
            continue
        if slug not in out or ts > out[slug][0]:
            out[slug] = (ts, obj)
    return out


def classify(obj: Mapping[str, Any]) -> str:
    if obj.get("realized_confirmed") and obj.get("realized_usd") is not None:
        return VENUE_CONFIRMED
    if not obj.get("settled"):
        return PENDING
    resid = _d((obj.get("breakdown") or {}).get("directional_residual"))
    if resid is not None and resid == 0:
        return LOCKED_ZERO_RESIDUAL
    return INDETERMINATE


def market_row(ts: str, obj: Mapping[str, Any]) -> dict:
    b = obj.get("breakdown") or {}
    kind = classify(obj)
    venue = _d(obj.get("realized_usd"))
    matched = _d(b.get("matched_contribution_usd")) or D(0)
    resid_units = _d(b.get("directional_residual"))
    row = {
        "slug": obj.get("slug"),
        "settled_utc": ts,
        "class": kind,
        "status": obj.get("status"),
        "fills": b.get("fills"),
        "matched_pairs": b.get("matched_pairs"),
        "matched_pair_cost": (str(_d(b.get("matched_pair_cost")).quantize(D("0.000001")))
                              if _d(b.get("matched_pair_cost")) is not None else None),
        "matched_contribution_usd": str(matched.quantize(D("0.0001"))),
        "directional_residual_units": b.get("directional_residual"),
        "residual_contribution_usd": b.get("residual_contribution_usd"),
        "cost_basis_usd": b.get("cost_basis_usd"),
        "maker_fills": b.get("maker_fills"),
        "taker_fills": b.get("taker_fills"),
        "fees_usd": b.get("fees_usd"),
        "realized_usd_venue": str(venue) if venue is not None else None,
    }
    # the single number this market contributes, and where it came from
    if kind == VENUE_CONFIRMED:
        row["value_usd"] = str(venue)
        row["value_basis"] = "venue_confirmed_realized"
    elif kind == LOCKED_ZERO_RESIDUAL:
        row["value_usd"] = str(matched.quantize(D("0.0001")))
        row["value_basis"] = ("derived_locked_pre_fee: zero residual, so "
                              "matched_pairs x (1.00 - pair_cost) regardless "
                              "of the result")
    else:
        row["value_usd"] = None
        row["value_basis"] = ("none: settled with residual but the venue "
                              "published no realizedPnl"
                              if kind == INDETERMINATE else "none: not settled")
    row["paired_to_zero"] = bool(resid_units is not None and resid_units == 0)
    return row


def _bootstrap_mean_ci(values: list, n: int = 4000, seed: int = 20260908) -> dict:
    """Percentile bootstrap over per-market outcomes. Reported so the mean
    is never quoted without its uncertainty; with a handful of markets the
    interval is wide, and that is the point."""
    if not values:
        return {"n": 0, "mean": None, "ci95": None}
    rnd = random.Random(seed)
    floats = [float(v) for v in values]
    mean = sum(floats) / len(floats)
    if len(floats) == 1:
        return {"n": 1, "mean": round(mean, 4), "ci95": None,
                "note": "one market: no interval is meaningful"}
    means = []
    for _ in range(n):
        sample = [floats[rnd.randrange(len(floats))] for _ in floats]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(int(0.975 * len(means)), len(means) - 1)]
    return {"n": len(floats), "mean": round(mean, 4),
            "ci95": [round(lo, 4), round(hi, 4)]}


def build(paths: Iterable[str], since_utc: str = R5F_START_UTC) -> dict:
    latest = latest_by_slug(iter_settlement_rows(paths))
    all_rows = [market_row(ts, o) for ts, o in
                sorted(latest.values(), key=lambda x: x[0])]
    cohort = [r for r in all_rows if (r["settled_utc"] or "") >= since_utc]
    earlier = [r for r in all_rows if (r["settled_utc"] or "") < since_utc]

    def _sum(rs, kind):
        vals = [_d(r["value_usd"]) for r in rs
                if r["class"] == kind and r["value_usd"] is not None]
        return sum(vals, D(0)), len(vals)

    conf_sum, conf_n = _sum(cohort, VENUE_CONFIRMED)
    lock_sum, lock_n = _sum(cohort, LOCKED_ZERO_RESIDUAL)

    paired = [r for r in cohort if r["paired_to_zero"] and r["value_usd"] is not None]
    with_residual = [r for r in cohort
                     if not r["paired_to_zero"] and r["value_usd"] is not None]

    out = {
        "question": ("R5F settled cohort: what has faithful L5 behaviour "
                     "actually produced, split by evidence authority?"),
        "owner_ref": "#167 comment 5577286614 items 2 and 7",
        "cohort_since_utc": since_utc,
        "labels": LABELS,
        "counts": {
            "settled_markets_in_cohort": len(cohort),
            "venue_confirmed": conf_n,
            "locked_zero_residual": lock_n,
            "indeterminate": sum(1 for r in cohort if r["class"] == INDETERMINATE),
            "pending": sum(1 for r in cohort if r["class"] == PENDING),
            "markets_before_cohort": len(earlier),
        },
        "venue_confirmed_realized_usd": str(conf_sum.quantize(D("0.0001"))),
        "derived_locked_pre_fee_usd": str(lock_sum.quantize(D("0.0001"))),
        "combined_indicative_usd": {
            "value": str((conf_sum + lock_sum).quantize(D("0.0001"))),
            "warning": ("INDICATIVE ONLY -- mixes venue-confirmed realized "
                        "with derived pre-fee figures. It is not a realized "
                        "number and must never be reported as one."),
        },
        "pairing_split": {
            "paired_to_zero": {
                "markets": len(paired),
                "sum_usd": str(sum((_d(r["value_usd"]) for r in paired), D(0))
                               .quantize(D("0.0001"))),
                "negative_markets": sum(1 for r in paired
                                        if (_d(r["value_usd"]) or D(0)) < 0),
                "per_market": _bootstrap_mean_ci([_d(r["value_usd"]) for r in paired]),
            },
            "left_residual": {
                "markets": len(with_residual),
                "sum_usd": str(sum((_d(r["value_usd"]) for r in with_residual), D(0))
                               .quantize(D("0.0001"))),
                "positive_markets": sum(1 for r in with_residual
                                        if (_d(r["value_usd"]) or D(0)) > 0),
                "per_market": _bootstrap_mean_ci([_d(r["value_usd"])
                                                  for r in with_residual]),
            },
            "reading": ("the split is descriptive, not causal: markets that "
                        "pair to zero and markets that strand residual are "
                        "not randomly assigned"),
        },
        "expectancy_venue_confirmed_only": _bootstrap_mean_ci(
            [_d(r["value_usd"]) for r in cohort
             if r["class"] == VENUE_CONFIRMED and r["value_usd"] is not None]),
        "fees_and_incentives": {
            "fees_usd": ("venue_unreported on every settlement row; every "
                         "derived figure here is PRE-FEE"),
            "rebates": ("maker rebates are credited separately by the venue "
                        "and are NOT included in any figure above"),
        },
        "markets": cohort,
        "markets_before_cohort": earlier,
    }
    return out


def render_markdown(res: Mapping[str, Any]) -> str:
    c = res["counts"]
    lines = [
        "# R5F settled cohort — economics by evidence authority",
        "",
        f"_{res['owner_ref']}. Cohort = markets settled at or after "
        f"{res['cohort_since_utc']} (R5F approval consumed). "
        f"Read-only; produced from the probe's own settlement rows._",
        "",
        f"**Settled markets in cohort: {c['settled_markets_in_cohort']}** — "
        f"{c['venue_confirmed']} venue-confirmed, "
        f"{c['locked_zero_residual']} locked at zero residual, "
        f"{c['indeterminate']} indeterminate, {c['pending']} pending.",
        "",
        f"- **Venue-confirmed realized: {res['venue_confirmed_realized_usd']}**",
        f"- Derived, locked, pre-fee: {res['derived_locked_pre_fee_usd']}",
        f"- Indicative combined: {res['combined_indicative_usd']['value']} — "
        f"{res['combined_indicative_usd']['warning']}",
        "",
        "## Pairing split",
        "",
        "| outcome | markets | sum $ | mean per market | 95% CI |",
        "|---|---:|---:|---:|---|",
    ]
    for key, name in (("paired_to_zero", "paired to zero residual"),
                      ("left_residual", "left directional residual")):
        s = res["pairing_split"][key]
        pm = s["per_market"]
        lines.append(
            f"| {name} | {s['markets']} | {s['sum_usd']} | "
            f"{pm.get('mean')} | {pm.get('ci95')} |")
    lines += ["", f"_{res['pairing_split']['reading']}._", "",
              "## Per market", "",
              "| settled | market | class | matched pairs | pair cost | "
              "residual | value $ |", "|---|---|---|---:|---:|---:|---:|"]
    for r in res["markets"]:
        lines.append(
            f"| {r['settled_utc'][11:19]} | {r['slug']} | {r['class']} | "
            f"{r['matched_pairs']} | {r['matched_pair_cost']} | "
            f"{r['directional_residual_units']} | {r['value_usd']} |")
    lines += ["", "## Fees and incentives", "",
              f"- {res['fees_and_incentives']['fees_usd']}",
              f"- {res['fees_and_incentives']['rebates']}",
              "", f"_{res['labels']}_", ""]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", nargs="+", required=True,
                    help="Railway log dump files or globs")
    ap.add_argument("--since", default=R5F_START_UTC,
                    help=f"cohort start UTC (default {R5F_START_UTC})")
    ap.add_argument("--out", default=None,
                    help="directory for l5_r5f_cohort.json / .md")
    args = ap.parse_args(argv)
    paths: list[str] = []
    for pat in args.logs:
        paths.extend(sorted(_glob.glob(pat)) or [pat])
    res = build(paths, since_utc=args.since)
    md = render_markdown(res)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "l5_r5f_cohort.json").write_text(
            json.dumps(res, indent=2, default=str) + "\n")
        (out / "L5_R5F_COHORT.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
