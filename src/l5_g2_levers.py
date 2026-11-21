"""Rank the levers by their effect on completion, using only real venue money.

OWNER GO (#167): "rank the 19% gap, residual-cap behavior, spread-market
filtering, and any other measured L5/executor discrepancy specifically by
their likely effect on P(economically acceptable complement completion |
first leg filled) and ultimately realized trading drag... For each one I want
L5 evidence vs our evidence, causal mechanism, counterfactual against our
real historical orders without invented fills, expected upside/downside,
whether it's actually L5 fidelity or just one of our safety shells, and the
smallest experiment capable of disproving it."

THE EX-ANTE RULE, WHICH DECIDES WHAT MAY BE COUNTERFACTUALLED
--------------------------------------------------------------
A filter can only be applied to history if it could have been evaluated
BEFORE the trade. "Drop the markets that failed to complete" is not a lever,
it is hindsight, and it would report a spectacular fake improvement. So every
lever here is tagged EX_ANTE (shape, price band, category -- knowable at
selection) or EX_POST (completion, pair count, residual -- knowable only
afterwards), and `counterfactual` REFUSES to score an EX_POST filter. This is
the same discipline the flow roster enforces in time: rank on what preceded
the window, never on the window itself.

WHAT THE MONEY SAYS, BEFORE ANY LEVER
--------------------------------------
Across both settled cohorts the relationship is close to deterministic:

    matched pairs > 0   ->  market made money        (every single one)
    matched pairs = 0   ->  market lost money        (every single one)

R5F states it most cleanly: 12 markets paired to zero summed +$6.3639 with
11 of 12 positive; 4 markets left residual summed -$9.2384 with ZERO
positive. Not one stranded market in the entire record came out ahead.

So completion is not one variable among several. It is the variable, and the
owner's metric -- P(acceptable completion | first leg filled) -- is the right
thing to have named.

A CORRECTION THIS MODULE EXISTS PARTLY TO MAKE
-----------------------------------------------
An earlier reading of the by-shape table (spread: 2 markets, -$5.033, -23.1%
of filled dollars) was reported as evidence for a spread-market filter. That
was wrong. The two spread markets are wisc-nd at -$6.2255 and lou-miss at
+$1.1925 -- one of our best. The cell is one disaster and one success, and
its mean is the disaster. Shape was a proxy for "the market that stranded",
which is EX_POST. `by_group` therefore reports n, the worst single market's
share of the group's loss, and refuses to rank any group whose loss is
concentrated in one market.
"""

import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCOREBOARD = "analysis/l5/l5_economic_scoreboard.json"
R5F = "analysis/l5/l5_r5f_cohort.json"

EX_ANTE = "ex_ante"       # knowable at selection time; may be counterfactualled
EX_POST = "ex_post"       # knowable only after the fact; may NOT be

# a group whose loss is this concentrated in one market is not a finding
CONCENTRATION_REFUSAL = 0.60
MIN_MARKETS_TO_RANK = 5

LABELS = {
    "money": "every dollar here is a venue-confirmed or venue-derived "
             "settlement figure from our own fills; no fill is invented",
    "ex_ante": "a filter is only counterfactualled if it could have been "
               "evaluated before the order was placed",
    "not_a_rule": "this ranks candidate levers; it changes no gate and "
                  "authorizes nothing",
}


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_markets(root: str = ".") -> List[Dict[str, Any]]:
    """Both settled cohorts, normalised to one row shape."""
    out: List[Dict[str, Any]] = []
    p = os.path.join(root, SCOREBOARD)
    if os.path.exists(p):
        for m in json.load(open(p, encoding="utf-8"))["markets"]:
            out.append({
                "slug": m["slug"], "cohort": m.get("cohort"),
                "source": "scoreboard",
                "pairs": _f(m.get("matched_pairs")) or 0.0,
                "pair_cost": _f(m.get("pair_cost")),
                "pair_usd": _f(m.get("pair_contribution_usd")) or 0.0,
                "residual_usd": _f(m.get("residual_contribution_usd")) or 0.0,
                "net_usd": _f(m.get("net_usd")) or 0.0,
                "turnover_usd": _f(m.get("filled_usd_turnover")) or 0.0,
                "shape": m.get("shape"), "price_band": m.get("price_band"),
                "category": m.get("category"),
            })
    p = os.path.join(root, R5F)
    if os.path.exists(p):
        for m in json.load(open(p, encoding="utf-8"))["markets"]:
            net = _f(m.get("realized_usd_venue"))
            if net is None:
                net = _f(m.get("matched_contribution_usd")) or 0.0
            out.append({
                "slug": m["slug"], "cohort": "R5F", "source": "r5f",
                "pairs": _f(m.get("matched_pairs")) or 0.0,
                "pair_cost": _f(m.get("matched_pair_cost")),
                "pair_usd": _f(m.get("matched_contribution_usd")) or 0.0,
                "residual_usd": _f(m.get("residual_contribution_usd")) or 0.0,
                "net_usd": net, "turnover_usd": _f(m.get("value_usd")) or 0.0,
                "shape": None, "price_band": None, "category": None,
            })
    return out


# ---------------------------------------------------------------- outcomes

def outcome(m: Mapping[str, Any], par: float = 1.0) -> str:
    """The owner's three cases, decided on the finished book.

    `acceptable` requires BOTH that the market completed and that the pairs
    it formed pay: a book paired to zero at an average cost above par has
    removed the directional risk and locked a loss, which is not a success
    and must not be counted as one. tomvoj-ticjir did exactly that at 1.1107.
    """
    pairs = m.get("pairs") or 0.0
    if pairs <= 0:
        return "no_completion"
    cost = m.get("pair_cost")
    if cost is not None and cost > par:
        return "completed_above_par"
    if (m.get("residual_usd") or 0.0) < 0:
        return "completed_with_residual"
    return "acceptable_completion"


def completion_rate(markets: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """P(acceptable completion | first leg filled), and what the rest cost.

    Denominator is every market where a first leg filled -- which is every
    settled market in the record, since a market with no fill never settles
    into this ledger.
    """
    by: Dict[str, List[Mapping[str, Any]]] = {}
    for m in markets:
        by.setdefault(outcome(m), []).append(m)
    n = len(markets)
    good = len(by.get("acceptable_completion", []))
    rows = {}
    for k, v in sorted(by.items()):
        rows[k] = {"markets": len(v),
                   "net_usd": round(sum(x["net_usd"] for x in v), 4),
                   "positive_markets": sum(1 for x in v if x["net_usd"] > 0),
                   "worst": min((x["net_usd"] for x in v), default=None)}
    return {
        "markets_with_a_first_leg_fill": n,
        "acceptable_completions": good,
        "p_acceptable_completion_given_first_leg": (
            round(good / n, 4) if n else None),
        "by_outcome": rows,
        "labels": LABELS,
    }


def loss_attribution(markets: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Where the losses actually come from, by mechanism rather than by tag."""
    losers = [m for m in markets if m["net_usd"] < 0]
    total = sum(m["net_usd"] for m in losers)
    by_out: Dict[str, float] = {}
    for m in losers:
        by_out[outcome(m)] = by_out.get(outcome(m), 0.0) + m["net_usd"]
    worst = min(losers, key=lambda m: m["net_usd"]) if losers else None
    return {
        "losing_markets": len(losers),
        "total_loss_usd": round(total, 4),
        "loss_by_outcome_usd": {k: round(v, 4) for k, v in sorted(by_out.items())},
        "share_from_no_completion": (
            round(by_out.get("no_completion", 0.0) / total, 4) if total else None),
        "worst_market": worst["slug"] if worst else None,
        "worst_market_usd": round(worst["net_usd"], 4) if worst else None,
        "worst_market_share_of_loss": (
            round(worst["net_usd"] / total, 4) if (worst and total) else None),
    }


# ------------------------------------------------------------ group levers

def by_group(markets: Sequence[Mapping[str, Any]], field: str) -> Dict[str, Any]:
    """Group the record by an EX_ANTE tag, and refuse concentrated cells.

    The spread-shape cell is why this refuses: 2 markets, -$5.033, of which
    -$6.2255 is one market and +$1.1925 the other. Reporting -23.1% of filled
    dollars for "spread" describes one disaster, not a shape.
    """
    groups: Dict[str, List[Mapping[str, Any]]] = {}
    for m in markets:
        v = m.get(field)
        if v is not None:
            groups.setdefault(str(v), []).append(m)
    out = {}
    for k, v in sorted(groups.items()):
        net = sum(x["net_usd"] for x in v)
        losers = [x for x in v if x["net_usd"] < 0]
        loss = sum(x["net_usd"] for x in losers)
        worst = min((x["net_usd"] for x in v), default=0.0)
        conc = (worst / loss) if loss else 0.0
        rankable = len(v) >= MIN_MARKETS_TO_RANK and conc < CONCENTRATION_REFUSAL
        out[k] = {
            "markets": len(v), "net_usd": round(net, 4),
            "worst_market_usd": round(worst, 4),
            "worst_share_of_group_loss": round(conc, 4),
            "rankable": rankable,
            "why_not": (None if rankable else
                        ("only %d markets" % len(v)
                         if len(v) < MIN_MARKETS_TO_RANK else
                         "%.0f%% of this group's loss is one market"
                         % (100 * conc))),
        }
    return out


def counterfactual(markets: Sequence[Mapping[str, Any]], field: str,
                   drop_values: Sequence[str], timing: str) -> Dict[str, Any]:
    """Exact leave-out arithmetic on real venue numbers -- EX_ANTE only.

    No fill is invented and no trajectory is re-simulated: a dropped market
    contributes nothing, a kept market contributes exactly what the venue
    paid. That is the only counterfactual this record can support honestly.
    """
    if timing != EX_ANTE:
        return {"refused": True,
                "why": "an %s filter cannot be applied to history: it uses "
                       "information that did not exist when the order was "
                       "placed, and scoring it would manufacture a result"
                       % timing}
    drop = {str(v) for v in drop_values}
    kept = [m for m in markets if str(m.get(field)) not in drop]
    removed = [m for m in markets if str(m.get(field)) in drop]
    base = sum(m["net_usd"] for m in markets)
    after = sum(m["net_usd"] for m in kept)
    return {
        "refused": False, "filter": "%s not in %s" % (field, sorted(drop)),
        "markets_before": len(markets), "markets_after": len(kept),
        "markets_removed": len(removed),
        "net_before_usd": round(base, 4), "net_after_usd": round(after, 4),
        "delta_usd": round(after - base, 4),
        "removed_slugs": [m["slug"] for m in removed],
        "also_removed_winners": [m["slug"] for m in removed if m["net_usd"] > 0],
    }


def break_even_completion(markets: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """What P(acceptable completion | first leg filled) has to reach.

    The record splits cleanly: an acceptable completion pays a mean amount,
    every other outcome costs a mean amount, and no market in the entire
    settled history straddles the two. So the break-even completion rate is
    just where those means balance:

        p * mean_win = (1 - p) * mean_loss
        p = mean_loss / (mean_win + mean_loss)

    This is the honest target. It is NOT a promise that raising the rate is
    achievable, and it assumes the two means survive a larger sample -- the
    win mean rests on 27 markets, the loss mean on 15 with one market at a
    third of the total. Both are stated so the reader can discount them.
    """
    wins = [m for m in markets if outcome(m) == "acceptable_completion"]
    losses = [m for m in markets if outcome(m) != "acceptable_completion"]
    if not wins or not losses:
        return {"estimable": False, "why": "need both outcomes present"}
    mw = sum(m["net_usd"] for m in wins) / len(wins)
    ml = -sum(m["net_usd"] for m in losses) / len(losses)
    p_now = len(wins) / (len(wins) + len(losses))
    if mw + ml <= 0:
        return {"estimable": False, "why": "means do not straddle zero"}
    p_be = ml / (mw + ml)
    worst = min(m["net_usd"] for m in losses)
    return {
        "estimable": True,
        "p_now": round(p_now, 4),
        "p_break_even": round(p_be, 4),
        "gap_points": round(p_be - p_now, 4),
        "gap_relative": round((p_be - p_now) / p_now, 4) if p_now else None,
        "mean_acceptable_completion_usd": round(mw, 4),
        "mean_other_outcome_usd": round(-ml, 4),
        "n_wins": len(wins), "n_losses": len(losses),
        "worst_single_loss_usd": round(worst, 4),
        "worst_share_of_all_losses": round(
            worst / sum(m["net_usd"] for m in losses), 4),
        "caveat": "the loss mean is dominated by one market; excluding it "
                  "the bar falls, and a rule that only avoids that one "
                  "market is fitted to it rather than to the mechanism",
    }


def build(root: str = ".") -> Dict[str, Any]:
    ms = load_markets(root)
    return {
        "markets": len(ms),
        "completion": completion_rate(ms),
        "break_even": break_even_completion(ms),
        "loss_attribution": loss_attribution(ms),
        "by_shape": by_group(ms, "shape"),
        "by_price_band": by_group(ms, "price_band"),
        "labels": LABELS,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    print(json.dumps(build("."), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
