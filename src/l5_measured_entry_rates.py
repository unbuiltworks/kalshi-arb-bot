"""Entry-EV rates MEASURED from authenticated settled outcomes. Pure.

WHY THIS EXISTS. `l5_btc5m_execution.straddle_probability` reads
`p_pair_natural` and `p_complete` from environment variables. Its
docstring calls them "one of the entry-EV gate's measured rates", and
nothing in the repository measures them -- they are owner-set constants.
The gate fails closed when they are absent, which is right, but when
present it prices every rung off an assumption. The sports lane does
better: `pmus_sports_open_gate` estimates its terms per sport from
settled markets and shrinks them toward a pooled prior. The BTC lanes
had no counterpart.

WHY IT MATTERS, measured on the 168 retired BTC15m markets of
2026-10-12..11-05 (data/l5/ops/btc15m_settled_20261012_20261105.jsonl):

    including the $1 placeholders   n=168  p_pair 0.3274  EV -$0.3392/mkt
    AUTHENTICATED rows only         n=148  p_pair 0.3716  EV -$0.0215/mkt

Twenty rows priced at exactly $1.0000 a contract carried -$53.80 of the
-$66.58 residual loss -- 81% of it. The same entry is marginally
negative on measured prices and catastrophically negative on the bound.
A gate fed the wrong one of those two is wrong by 15.8x, in the
direction that decides whether this strategy is fixable or hopeless.

THREE RULES.

  1. ONLY AUTHENTICATED COST BASES COUNT. A residual leg whose unit cost
     is exactly $1.0000 is the settlement path's upper-bound placeholder,
     not a fill -- nobody buys a binary at $1, where the best case is to
     get the dollar back. Those rows are EXCLUDED from the estimate and
     counted separately, never silently averaged in.

  2. A SMALL SAMPLE CANNOT OPEN ON A STREAK. Rates are shrunk toward a
     neutral prior with `prior_n` pseudo-markets, and below `min_n`
     authenticated markets the estimate REFUSES rather than returning a
     confident number from four observations.

  3. THE GATE READS THE LOWER BOUND, NOT THE POINT ESTIMATE. `ev_lower`
     is `ev - z * se`: a mean that is positive but indistinguishable from
     zero is not an edge. Requiring `ev_lower > 0` is what "positive
     after uncertainty" means, and it is the only reading that refuses
     the case this program keeps hitting -- a plausible mean on a sample
     too small to carry it.

WHAT IT DOES NOT DO. It authorizes nothing, writes nothing, reads no
environment and holds no credential. It returns rates; whether to quote
on them is the controller's decision and the owner's permission.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

ZERO = D("0")
ONE = D("1")

#: pseudo-markets of prior weight -- the sports gate's PRIOR_N, same role
PRIOR_N = D("10")

#: fewer authenticated markets than this and we decline to estimate
MIN_N = 20

#: standard errors subtracted from the mean for `ev_lower`
Z = D("1")

PAIRED = "PAIRED"
RESIDUAL = "RESIDUAL"
NO_POSITION = "NO_POSITION"
UPPER_BOUND = "UPPER_BOUND_COST_BASIS"


def _d(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return x if x.is_finite() else None


def residual_unit_cost(row: Mapping[str, Any]) -> Optional[D]:
    """|residual_cost| / |residual_qty|, or None when there is no residual."""
    q, c = _d(row.get("residual_qty")), _d(row.get("residual_cost"))
    if q is None or c is None:
        return None
    q, c = abs(q), abs(c)
    if q == ZERO:
        return None
    return c / q


def classify(row: Mapping[str, Any]) -> str:
    """PAIRED / RESIDUAL / NO_POSITION / UPPER_BOUND_COST_BASIS.

    RULE 1: a residual priced at exactly $1.0000 a contract is the
    placeholder, and it is reported as UPPER_BOUND rather than as a
    measured residual.
    """
    rq = _d(row.get("residual_qty"))
    mq = _d(row.get("matched_qty"))
    if rq is None:
        return UPPER_BOUND if residual_unit_cost(row) == ONE else NO_POSITION
    if rq != ZERO:
        return UPPER_BOUND if residual_unit_cost(row) == ONE else RESIDUAL
    if mq is not None and mq > ZERO:
        return PAIRED
    return NO_POSITION


def _sqrt(x: D) -> D:
    return x.sqrt() if x > ZERO else ZERO


def estimate(rows: Sequence[Mapping[str, Any]], *,
             prior_n: D = PRIOR_N, min_n: int = MIN_N, z: D = Z
             ) -> Dict[str, Any]:
    """Measured entry rates, or a refusal.

    Each row is a retired settlement record carrying `matched_qty`,
    `matched_pnl`, `residual_qty`, `residual_cost` and
    `settlement_pnl`.
    """
    paired: List[D] = []
    resid: List[D] = []
    excluded = 0
    unreadable = 0
    no_position = 0
    for row in rows:
        kind = classify(row)
        if kind == UPPER_BOUND:
            excluded += 1
            continue
        if kind == NO_POSITION:
            no_position += 1
            continue
        pnl = _d(row.get("settlement_pnl"))
        if pnl is None:
            unreadable += 1
            continue
        if kind == PAIRED:
            #: the matched line is the paired economics; settlement_pnl
            #: equals it when nothing stranded, and we take the explicit
            #: one when present so a future row with both cannot drift.
            paired.append(_d(row.get("matched_pnl")) or pnl)
        else:
            resid.append(pnl)

    n = len(paired) + len(resid)
    out: Dict[str, Any] = {
        "n_authenticated": n,
        "n_paired": len(paired),
        "n_residual": len(resid),
        "n_excluded_upper_bound": excluded,
        "n_no_position": no_position,
        "n_unreadable_pnl": unreadable,
        "ok": False, "reason": None,
        "p_pair": None, "pair_margin_usd": None,
        "residual_loss_usd": None,
        "ev_per_market_usd": None, "se_usd": None,
        "ev_lower_usd": None,
        "labels": ("rates measured from AUTHENTICATED settled rows only; "
                   "rows at the $1 placeholder are excluded and counted. "
                   "ev_lower is the figure a gate should read."),
    }
    if n < int(min_n):
        out["reason"] = "TOO_FEW_AUTHENTICATED_MARKETS"
        out["min_n"] = int(min_n)
        return out

    nd = D(n)
    # RULE 2: shrink the pair rate toward a neutral 0.5 with prior_n
    # pseudo-markets. Shrinkage moves a small sample toward "no
    # information", never toward a more attractive number than observed
    # unless the observation itself was extreme.
    p_raw = D(len(paired)) / nd
    p_pair = ((D(len(paired)) + prior_n * D("0.5"))
              / (nd + prior_n))
    mean_paired = (sum(paired, ZERO) / D(len(paired))) if paired else ZERO
    mean_resid = (sum(resid, ZERO) / D(len(resid))) if resid else ZERO
    # per-market EV from the shrunk mixture of the two measured outcomes
    ev = p_pair * mean_paired + (ONE - p_pair) * mean_resid

    # dispersion of the per-market result, over every authenticated row
    all_pnl = list(paired) + list(resid)
    mean_all = sum(all_pnl, ZERO) / nd
    var = (sum(((x - mean_all) ** 2 for x in all_pnl), ZERO)
           / D(max(n - 1, 1)))
    se = _sqrt(var) / _sqrt(nd)

    out.update(
        ok=True,
        p_pair=str(p_pair), p_pair_observed=str(p_raw),
        pair_margin_usd=str(mean_paired),
        residual_loss_usd=str(mean_resid),
        ev_per_market_usd=str(ev),
        mean_realized_usd=str(mean_all),
        se_usd=str(se),
        # RULE 3
        ev_lower_usd=str(ev - z * se),
        z=str(z), prior_n=str(prior_n),
    )
    return out


def entry_is_justified(est: Mapping[str, Any], *,
                       min_ev_usd: Any = 0) -> Dict[str, Any]:
    """Would a new entry be economically justified on these rates?

    RULE 3: it reads `ev_lower_usd`. A refused estimate is never a
    permission -- an unmeasured rate cannot justify an entry.
    """
    floor = _d(min_ev_usd)
    if floor is None:
        return {"justified": False, "why": "MIN_EV_UNREADABLE"}
    if not est.get("ok"):
        return {"justified": False,
                "why": est.get("reason") or "RATES_NOT_ESTABLISHED"}
    lo = _d(est.get("ev_lower_usd"))
    if lo is None:
        return {"justified": False, "why": "EV_LOWER_UNREADABLE"}
    return {"justified": bool(lo > floor),
            "ev_lower_usd": str(lo), "min_ev_usd": str(floor),
            "why": None if lo > floor else "EV_LOWER_NOT_ABOVE_MIN"}
