"""The settled-window scoreboard for the frozen arms.

This module is the ONLY place allowed to emit $/window for a frozen arm, and
it refuses to do so unless the window is genuinely complete and genuinely
settled. Two gates, both fail-closed:

* **completeness** -- the event stream must cover the whole 15-minute window
  (first and last event within `EDGE_TOL_S` of the window bounds) and must
  contain no gap longer than `MAX_GAP_S`. A window stitched from a partial
  byte run fails here.
* **settlement** -- `winner` must be an authoritative outcome passed in by the
  caller. A `None` winner means `resid_pnl` is undefined, so no net exists.
  A terminal `last` price is NOT settlement evidence and this module has no
  way to accept one.

The headline ratio is `matched_per_extra_fill`: the ADDITIONAL matched
contracts an arm creates per ADDITIONAL fill it takes, measured against the
control on the same window. It is the test of whether extra fills pair or
merely strand. It is reported as `None` when an arm takes no extra fills,
never as zero, because "undefined" and "stranded" are different findings.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional, Sequence

WINDOW_S = 15 * 60
EDGE_TOL_S = 5.0        # a window may start/end a few seconds inside the bound
MAX_GAP_S = 20.0        # a silent book for longer than this is a hole


class WindowNotComplete(Exception):
    """Raised instead of returning economics for a partial window."""


class WindowNotSettled(Exception):
    """Raised instead of returning economics without an authoritative winner."""


def assert_complete(events: Sequence, *, window_start: float) -> Dict[str, Any]:
    """Fail closed unless the stream really covers the whole window."""
    if not events:
        raise WindowNotComplete("no events")
    ts = [e[0] for e in events]
    first, last = min(ts), max(ts)
    end = window_start + WINDOW_S
    if first > window_start + EDGE_TOL_S:
        raise WindowNotComplete(
            f"stream starts {first - window_start:.1f}s into the window")
    if last < end - EDGE_TOL_S:
        raise WindowNotComplete(
            f"stream ends {end - last:.1f}s before the window closes "
            f"(coverage {(last - first) / 60:.2f} of 15.00 min)")
    gaps = []
    sts = sorted(ts)
    for a, b in zip(sts, sts[1:]):
        if b - a > MAX_GAP_S:
            gaps.append((a, b - a))
    if gaps:
        worst = max(gaps, key=lambda g: g[1])
        raise WindowNotComplete(
            f"{len(gaps)} gap(s) over {MAX_GAP_S}s; worst {worst[1]:.1f}s")
    return {"first": first, "last": last, "gaps": 0,
            "coverage_min": (last - first) / 60.0}


def assert_settled(winner: Optional[str]) -> str:
    if winner not in ("up", "down"):
        raise WindowNotSettled(
            "winner must be an authoritative settled outcome ('up'/'down'); "
            "a terminal last price is not settlement evidence")
    return winner


def scoreboard(res: Dict[str, Any], *, control: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
    """The eleven requested metrics, plus the matched-per-extra-fill ratio."""
    matched = D(str(res["matched_qty"]))
    resid = D(str(res["resid_qty"]))
    #: pair cost per matched pair: $1 payout minus the matched P&L per pair,
    #: i.e. what the pair actually cost us to assemble after fees attribution.
    pair_cost = ((matched - D(str(res["matched_pnl"]))) / matched
                 if matched > 0 else None)
    out = {
        "matched_contracts": matched,
        "unmatched_residual": resid,
        "pair_cost": pair_cost,
        "maker_fills": res["maker_fills"],
        "taker_completions": res["taker_fills"],
        "fees": D(str(res["fees"])),
        "confirmed_incentives": D(str(res.get("credited_rebate") or 0)),
        "sourced_incentives": D(str(res.get("sourced_incentive") or 0)),
        "settlement_pnl": res["resid_pnl"],
        "net_per_window": res["net"],
        "peak_capital": D(str(res["peak_cash"])),
        "worst_residual": D(str(res["worst_residual"])),
        "n_fills": res["n_fills"],
    }
    if control is not None:
        extra_fills = res["n_fills"] - control["n_fills"]
        extra_matched = matched - D(str(control["matched_qty"]))
        out["extra_fills_vs_control"] = extra_fills
        out["extra_matched_vs_control"] = extra_matched
        #: None, not 0, when the arm took no extra fills: undefined is not the
        #: same finding as "the extra fills stranded".
        out["matched_per_extra_fill"] = (extra_matched / D(extra_fills)
                                        if extra_fills > 0 else None)
    return out
