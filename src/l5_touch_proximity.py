"""Where L5 actually transacts, measured — and what cannot be measured.

OWNER 2026-09-11: "reconstruct, wherever public data permits, L5's trade price
versus contemporaneous bid/ask. Determine whether L5 disproportionately trades
at/near the touch... Do not assume self-trading. Test it only if public
transaction/counterparty evidence supports it."

WHAT THIS CANNOT DO, STATED FIRST
---------------------------------
**There is no contemporaneous order book for L5's trades.** Nothing on disk
carries bid/ask time series for the crypto windows he traded, and the venue
does not serve historical depth. So:

  * L5's distance from the touch is NOT COMPUTABLE.
  * L5's queue position is NOT COMPUTABLE.
  * Neither will be estimated, inferred from his prices, or reported with a
    confidence interval that would imply they were measured.

That is the honest boundary of this analysis, and it is stated before the
findings rather than buried under them.

WHAT IS COMPUTABLE, AND WHY IT ANSWERS THE QUESTION ANYWAY
-----------------------------------------------------------
Two things on disk settle the substance without a book:

1. **maker/taker per fill**, labelled by a full-era takerOnly walk of the
   venue's own /trades endpoint. A taker fill means L5 crossed and paid the
   touch outright. A maker fill means someone traded against his resting
   order.

2. **matched_pair_unit_cost per fill** -- what his two legs actually cost
   together. This is where "where you quote" ends up economically: a trader
   who rests far behind the touch collects a cheap pair rarely; one who rests
   at the front collects a thin pair often. The distribution shows which.

SELF-TRADING IS NOT TESTED HERE. The owner asked that it be tested only on
public transaction/counterparty evidence. `l5_all_trades.csv` carries a
transaction_hash but no counterparty identity, so the question is not
answerable from this data and no claim about it appears in the output.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics as _st
from typing import Any, Iterable, Mapping, Optional

CANONICAL = "analysis/l5/l5_canonical_sequence.csv.gz"

# OUR live gates, for the comparison the owner asked for. Values from
# CLAUDE.md and the deployed spring config, not invented here.
OUR_GATES = (("a <=0.96 spring preference", 0.96),
             ("b 0.96-0.98 observation cap", 0.98),
             ("c 0.98-1.00 hard ceiling", 1.00))

NOT_COMPUTABLE = (
    "L5's distance from touch and queue position are NOT COMPUTABLE: no "
    "contemporaneous order book exists for these windows, on disk or from the "
    "venue. They are not estimated from his prices."
)
NO_SELF_TRADE_CLAIM = (
    "self-trading is not tested: the trade rows carry a transaction hash but "
    "no counterparty identity, so the question is unanswerable from this data "
    "and no claim either way is made."
)


def rows(path: str = CANONICAL) -> Iterable[dict]:
    op = gzip.open if str(path).endswith(".gz") else open
    with op(path, "rt") as fh:
        for r in csv.DictReader(fh):
            yield r


def _f(v: Any) -> Optional[float]:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x


def quantile(a: list, p: float) -> Optional[float]:
    if not a:
        return None
    a = sorted(a)
    i = (len(a) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(a) - 1)
    return a[lo] + (a[hi] - a[lo]) * (i - lo)


def liquidity_role(rs: Iterable[Mapping[str, Any]]) -> dict:
    """How often L5 PAID the touch rather than resting.

    `unknown` is reported, never folded into either side: the takerOnly walk
    covered one era, and treating an unlabelled early-era fill as maker would
    manufacture the answer."""
    n, counts = 0, {}
    for r in rs:
        n += 1
        k = (r.get("maker_taker") or "").strip() or "unknown"
        counts[k] = counts.get(k, 0) + 1
    labelled = sum(v for k, v in counts.items() if k in ("maker", "taker"))
    return {
        "fills": n, "counts": counts, "labelled": labelled,
        "unlabelled": counts.get("unknown", 0),
        "maker_share_of_labelled": (round(counts.get("maker", 0) / labelled, 4)
                                    if labelled else None),
        "coverage_note": ("maker/taker comes from a full-era takerOnly walk of "
                          "the venue's /trades; fills outside that era are "
                          "`unknown` and are never assumed to be either."),
    }


def pair_cost_distribution(rs: Iterable[Mapping[str, Any]],
                           by: Optional[str] = None) -> dict:
    """What L5's matched pairs actually cost, and how they fall against OUR
    gates. This is the measurement that stands in for distance-from-touch."""
    vals, groups = [], {}
    for r in rs:
        x = _f(r.get("matched_pair_unit_cost"))
        if x is None or x <= 0:
            continue
        vals.append(x)
        if by:
            groups.setdefault(str(r.get(by)), []).append(x)
    out = {"n": len(vals), "not_computable": NOT_COMPUTABLE}
    if not vals:
        return out
    out.update({
        "mean": round(_st.mean(vals), 4),
        "p05": round(quantile(vals, .05), 4),
        "p25": round(quantile(vals, .25), 4),
        "median": round(quantile(vals, .50), 4),
        "p75": round(quantile(vals, .75), 4),
        "p95": round(quantile(vals, .95), 4),
        "against_our_gates": gate_split(vals),
    })
    if by:
        out["by_" + by] = {k: {"n": len(v), "median": round(quantile(v, .5), 4),
                               "share_above_par": round(
                                   sum(1 for x in v if x > 1.0) / len(v), 4)}
                           for k, v in sorted(groups.items(),
                                              key=lambda kv: -len(kv[1]))
                           if len(v) >= 200}
    return out


def gate_split(vals: list) -> dict:
    """How much of L5's own trading our live gates would have refused."""
    n = len(vals)
    if not n:
        return {}
    out, prev = {}, 0.0
    for name, hi in OUR_GATES:
        out[name] = round(sum(1 for x in vals if prev < x <= hi) / n, 4)
        prev = hi
    out["d >1.00 we REFUSE"] = round(sum(1 for x in vals if x > 1.0) / n, 4)
    out["would_clear_our_0_96_preference"] = round(
        sum(1 for x in vals if x <= 0.96) / n, 4)
    out["would_clear_our_1_00_ceiling"] = round(
        sum(1 for x in vals if x <= 1.0) / n, 4)
    return out


def study(path: str = CANONICAL) -> dict:
    rs = list(rows(path))
    return {
        "question": ("does L5 transact at/near the touch, and are we "
                     "optimising pair price harder than he does?"),
        "liquidity_role": liquidity_role(rs),
        "pair_cost": pair_cost_distribution(rs, by="duration_minutes"),
        "maker_pair_cost": pair_cost_distribution(
            [r for r in rs if (r.get("maker_taker") or "") == "maker"]),
        "taker_pair_cost": pair_cost_distribution(
            [r for r in rs if (r.get("maker_taker") or "") == "taker"]),
        "limits": [NOT_COMPUTABLE, NO_SELF_TRADE_CLAIM],
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--canonical", default=CANONICAL)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = study(a.canonical)
    print(json.dumps(res, indent=2, default=str))
    if a.out:
        from pathlib import Path
        Path(a.out).write_text(json.dumps(res, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
