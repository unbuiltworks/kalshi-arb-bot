"""GEMINI_COMPLETION_LABELS_V1 -- "will this market give us the other side?"

P8 isolated the failure mode: 53 of 115 BTC5m residual markets never offered a
complement at all, so the first leg we bought there was never a pair in
progress -- it was a directional bet we had not decided to make. No completion
threshold, WAIT rule or probability curve can reach those markets. The only
move left is not to open in them.

THE PREDICTION TARGET IS COMPLETION AVAILABILITY, NOT SETTLEMENT DIRECTION.
Nothing in this module reads `resolution`, `settlementValue` or any outcome.
The question is whether the tape later offers the other side at a price that
would pair, which is a property of the market's liquidity, not of BTC.

THE CAUSAL SEAM IS THE POINT OF THE MODULE. For a candidate OPEN at print i:

    features  come from prints[0..i]      -- what a live decider could see
    labels    come from prints[i+1..]     -- what actually happened next

They are built by two functions that never see each other's inputs, so a
feature cannot accidentally be computed from the future. A label is scored
after the fact and may never be passed to a policy.

WHAT A PRINT MEANS, calibrated earlier against 56 confirmed maker fills: a
SELL-aggressor print at p made a YES buyable at p; a BUY print at b made a NO
buyable at 1-b.

FILLABILITY IS COUNTED, NOT ASSUMED. The label records HOW MANY qualifying
complement prints followed, so an analysis can apply whatever capture
assumption it is willing to defend rather than inheriting one from here. At
capture c, the chance of winning at least one of n qualifying prints is
1-(1-c)^n; `n_qualifying` is what makes that computable downstream.

OFFLINE AND PURE. No socket, no venue read, no clock.
"""
from __future__ import annotations

import datetime as _dt
from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

LABEL = "GEMINI_COMPLETION_LABELS_V1"

YES, NO = "YES", "NO"

#: pair-cost ceilings the labels are computed at. 1.00 is the break-even pair;
#: 0.98 and 0.95 are the fee-safe and spring-discipline targets.
CEILINGS: Tuple[F, ...] = (F(1), F(98, 100), F(95, 100))


def _side_of(print_side: str) -> Optional[str]:
    if print_side == "sell":
        return YES
    if print_side == "buy":
        return NO
    return None


def _unit(offered: str, px: F) -> F:
    return px if offered == YES else (F(1) - px)


def _expiry_s(contract: Mapping[str, Any]) -> Optional[int]:
    """The contract's published end, in SECONDS to match the print clock.

    Print timestamps in this corpus are UNIX SECONDS -- see
    `tape_normalize.PRINT_TIME_UNIT`. Every duration below is therefore a
    plain difference of seconds.
    """
    v = contract.get("expiry") or contract.get("expiryDate")
    if v is None:
        return None
    try:
        return int(_dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp())
    except Exception:                                          # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# features: prints[0..i] and the contract's published definition, nothing else
# --------------------------------------------------------------------------
def features_at(contract: Mapping[str, Any], prints: Sequence[Sequence[Any]],
                i: int, *, prior: Optional[Mapping[str, Any]] = None
                ) -> Dict[str, Any]:
    """What a decider standing at print `i` could actually know.

    `prior` is the PRECEDING contract's realized completion summary. It is
    causal for a series where one contract ends before the next begins, which
    is true of BTC5m/15m/1h: at the moment market k opens, market k-1 is over.
    It is the only cross-market input and it is optional.
    """
    t, side, px_s, qty_s = prints[i]
    t = int(t)
    offered = _side_of(str(side))
    px = F(str(px_s))
    unit = _unit(offered, px) if offered else None
    need = (NO if offered == YES else YES) if offered else None

    hist = prints[:i + 1]
    t0 = int(hist[0][0])
    elapsed_s = t - t0
    exp = _expiry_s(contract)

    n_same = n_opp = 0
    last_opp_t: Optional[int] = None
    notional = F(0)
    series: List[F] = []
    for tt, ss, pp, qq in hist:
        o = _side_of(str(ss))
        if o is None:
            continue
        u = _unit(o, F(str(pp)))
        series.append(u if o == YES else (F(1) - u))   # a common YES scale
        notional += F(str(pp)) * F(str(qq))
        if o == offered:
            n_same += 1
        else:
            n_opp += 1
            last_opp_t = int(tt)

    mins = max(F(elapsed_s, 60), F(1, 60))         # never divide by zero
    yes_scale = series                              # every print as a YES price
    move_5 = (yes_scale[-1] - yes_scale[-6]) if len(yes_scale) >= 6 else None
    move_all = (yes_scale[-1] - yes_scale[0]) if len(yes_scale) >= 2 else None
    reversals = 0
    for a, b in zip(yes_scale, yes_scale[1:]):
        if (a < F(1, 2)) != (b < F(1, 2)):
            reversals += 1

    return {
        "symbol": contract.get("symbol"), "day": contract.get("day"),
        "hour": contract.get("hour"), "duration": contract.get("duration"),
        "idx": i, "t": t,
        "offered": offered, "need": need,
        "price": unit,
        "dist_50": (abs(unit - F(1, 2)) if unit is not None else None),
        "tte_s": (None if exp is None else float(exp - t)),
        "elapsed_s": float(elapsed_s),
        "n_prints": len(hist),
        "n_same": n_same, "n_opposite": n_opp,
        "frac_opposite": (F(n_opp, len(hist)) if hist else None),
        "since_opposite_s": (None if last_opp_t is None
                             else float(t - last_opp_t)),
        "ever_opposite": n_opp > 0,
        "trades_per_min": F(len(hist), 1) / mins,
        "dollars_per_min": notional / mins,
        "move_5": move_5, "move_all": move_all, "reversals": reversals,
        "prior_two_sided": (None if prior is None else prior.get("two_sided")),
        "prior_completion_rate": (None if prior is None
                                  else prior.get("completion_rate")),
    }


# --------------------------------------------------------------------------
# labels: prints[i+1..] only. never an outcome.
# --------------------------------------------------------------------------
def labels_after(prints: Sequence[Sequence[Any]], i: int, *,
                 need: str, price: F,
                 ceilings: Sequence[F] = CEILINGS) -> Dict[str, Any]:
    """Did the other side actually show up, at what price, and how fast?"""
    out: Dict[str, Any] = {"comp_any": False, "latency_any_s": None,
                           "n_complement": 0}
    per: Dict[str, Any] = {}
    t0 = int(prints[i][0])
    firsts: Dict[str, Optional[int]] = {}
    counts: Dict[str, int] = {}
    best: Optional[F] = None
    for tt, ss, pp, _qq in prints[i + 1:]:
        o = _side_of(str(ss))
        if o != need:
            continue
        q = _unit(o, F(str(pp)))
        out["n_complement"] += 1
        if not out["comp_any"]:
            out["comp_any"] = True
            out["latency_any_s"] = float(int(tt) - t0)
        best = q if best is None else min(best, q)
        for c in ceilings:
            k = f"{float(c):.2f}"
            if price + q <= c:
                counts[k] = counts.get(k, 0) + 1
                if firsts.get(k) is None:
                    firsts[k] = int(tt)
    for c in ceilings:
        k = f"{float(c):.2f}"
        per[f"pair_le_{k}"] = counts.get(k, 0) > 0
        per[f"n_qualifying_{k}"] = counts.get(k, 0)
        per[f"latency_{k}_s"] = (None if firsts.get(k) is None
                                 else float(firsts[k] - t0))
    out.update(per)
    out["best_complement"] = best
    out["best_pair"] = (None if best is None else price + best)
    return out


def label_opens(contract: Mapping[str, Any], *,
                prior: Optional[Mapping[str, Any]] = None,
                ceilings: Sequence[F] = CEILINGS,
                max_price: Optional[F] = None) -> List[Dict[str, Any]]:
    """Every print treated as a candidate first leg, with features and labels.

    Every print is a candidate because the question is not "given we opened,
    what happened" -- that population is already selected by the policy that
    opened. It is "standing here, with no position, should we open at all".
    """
    prints = sorted(contract.get("prints") or [],
                    key=lambda r: (int(r[0]), str(r[1])))
    rows: List[Dict[str, Any]] = []
    for i, pr in enumerate(prints):
        offered = _side_of(str(pr[1]))
        if offered is None:
            continue
        unit = _unit(offered, F(str(pr[2])))
        if unit <= 0 or unit >= 1:
            continue
        if max_price is not None and unit > max_price:
            continue
        f = features_at(contract, prints, i, prior=prior)
        need = f["need"]
        lab = labels_after(prints, i, need=need, price=unit, ceilings=ceilings)
        row = dict(f)
        row.update({("y_" + k): v for k, v in lab.items()})
        rows.append(row)
    return rows


def contract_summary(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """The one-line description of a FINISHED contract, for use as the `prior`
    of the next one in its series. Reading it about an earlier contract is
    causal; reading it about the current one would not be."""
    prints = contract.get("prints") or []
    sides = {_side_of(str(p[1])) for p in prints}
    sides.discard(None)
    rows = label_opens(contract)
    n = len(rows)
    ok = sum(1 for r in rows if r.get("y_pair_le_1.00"))
    return {
        "symbol": contract.get("symbol"), "n_prints": len(prints),
        "two_sided": len(sides) > 1,
        "completion_rate": (F(ok, n) if n else None),
        "n_opens": n,
    }
