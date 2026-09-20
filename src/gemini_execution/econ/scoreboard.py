"""THE SCOREBOARD. Fill count is not on it as an objective.

"Do NOT optimize raw fills." So `first_fills` appears as a DENOMINATOR and a
diagnostic, never as a score, and the headline is

    net cents per capital-dollar-minute

which a strategy cannot improve by trading more. Every component that feeds it
is reported apart: trading P&L, residual P&L, fees, accrued rebate, credited
rebate and settlement cashflow never merge into one number, because the only
way a negative lane looks positive is by summing things that settle
differently.

THE LADDER IS CARRIED ON EVERY AGGREGATE. `historical -> shadow -> submitted ->
filled -> settled -> credited -> realized`. A shadow row is stamped `shadow`
and no arithmetic over shadow rows is ever labelled realized. `credited_rebate`
is None unless a venue record supplied it; an accrual never fills that field.

TWO COUNTS THAT ARE DELIBERATELY DIFFERENT, because collapsing them is how a
policy failure gets reported as a market failure:

    complement_opportunities            the complement was QUOTED at all
    acceptable_complement_opportunities it was quoted at or below the veto's
                                        ceiling

The gap between them is the lane's own doing. `MECHANISM_STUDIES_1` and the
PMUS work both had to split exactly this apart after the fact; it is split from
the start here.

MEDIANS, NOT MEANS, on every time-to-event. One 800-second recovery in a
900-second window moves a mean and tells you nothing about the typical case.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA = "gemini_econ_scoreboard/v1"

LADDER = ("historical", "shadow", "submitted", "filled", "settled",
          "credited", "realized")

#: horizons for post-fill markout, in seconds. Preregistered.
MARKOUT_HORIZONS_S = (5, 15, 30, 60, 120)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _median(xs: Sequence[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _pct(num: int, den: int) -> Optional[float]:
    return (num / den) if den else None


@dataclass
class Scoreboard:
    """One cohort's running tally. Counters only -- it decides nothing."""

    cohort: str
    stage: str = "shadow"

    windows: int = 0
    first_fills: int = 0
    maker_first_fills: int = 0
    zero_ahead_fills: int = 0

    complement_opportunities: int = 0
    acceptable_complement_opportunities: int = 0
    complement_fills: int = 0
    maker_to_maker_completions: int = 0

    same_side_exits: int = 0
    aggressive_exits: int = 0
    rescue_exits: int = 0

    #: entry admission, so a low fill count can be attributed
    entries_considered: int = 0
    entries_admitted: int = 0
    entry_refusals: Dict[str, int] = field(default_factory=dict)

    #: completion admission, the veto's own record
    completions_considered: int = 0
    completions_vetoed: int = 0
    veto_reasons: Dict[str, int] = field(default_factory=dict)
    #: THE OVERLAY'S STATED COST. L5_OPERATING_MODEL.md 2d permits a pair-cost
    #: ceiling only if it is labelled a risk overlay and CARRIES ITS COST. The
    #: cost is what it forbade, measured on this tape -- not restated from the
    #: ruling's historical shares.
    forbidden_bases: List[str] = field(default_factory=list)
    forbidden_would_have_been_fee_positive: int = 0

    matched_qty: D = D("0")
    residual_qty: D = D("0")
    pair_bases: List[str] = field(default_factory=list)
    pairs_at_or_below_one: int = 0
    fee_positive_pairs: int = 0
    #: THE LEAK COUNTER. Any pair formed above the veto ceiling is a DEFECT.
    pairs_above_ceiling: int = 0

    trading_pnl: D = D("0")
    residual_settlement_pnl: Optional[D] = None
    fees_paid: D = D("0")
    rebate_accrued: D = D("0")
    rebate_credited: Optional[D] = None

    capital_dollar_minutes: D = D("0")
    time_to_flat_s: List[float] = field(default_factory=list)
    markouts: Dict[str, List[float]] = field(default_factory=dict)

    # ---------------------------------------------------------------- record
    def window(self) -> None:
        self.windows += 1

    def entry(self, decision: Mapping[str, Any]) -> None:
        self.entries_considered += 1
        if decision.get("admitted"):
            self.entries_admitted += 1
            return
        for g in (decision.get("failed_gates") or
                  decision.get("unknown_gates") or ["UNKNOWN"]):
            self.entry_refusals[g] = self.entry_refusals.get(g, 0) + 1

    def first_fill(self, *, maker: bool, zero_ahead: bool) -> None:
        self.first_fills += 1
        if maker:
            self.maker_first_fills += 1
        if zero_ahead:
            self.zero_ahead_fills += 1

    def complement_seen(self, *, acceptable: bool) -> None:
        self.complement_opportunities += 1
        if acceptable:
            self.acceptable_complement_opportunities += 1

    def completion_considered(self, admitted: bool, reason: Any = None,
                              forbidden: Optional[Mapping[str, Any]] = None
                              ) -> None:
        self.completions_considered += 1
        if not admitted:
            self.completions_vetoed += 1
            key = str(reason or "UNSPECIFIED").split(":")[0]
            self.veto_reasons[key] = self.veto_reasons.get(key, 0) + 1
            if forbidden:
                b = forbidden.get("pair_basis_before_fees")
                if b is not None:
                    self.forbidden_bases.append(str(b))
                if forbidden.get("fee_positive"):
                    # the overlay refused a pair that WOULD have paid. This is
                    # the counter it exists to expose, not to hide.
                    self.forbidden_would_have_been_fee_positive += 1

    def pair(self, econ: Mapping[str, Any], *, both_maker: bool,
             ceiling: Any = None, complement_price: Any = None) -> None:
        """Record a formed pair, and flag it if the veto should have stopped it."""
        self.complement_fills += 1
        if both_maker:
            self.maker_to_maker_completions += 1
        basis = econ.get("pair_basis_before_fees")
        if basis is not None:
            self.pair_bases.append(str(basis))
            if _dec(basis) is not None and _dec(basis) <= D("1"):
                self.pairs_at_or_below_one += 1
        if econ.get("fee_positive"):
            self.fee_positive_pairs += 1
        self.matched_qty += _dec(econ.get("matched_qty")) or D("0")
        self.trading_pnl += _dec(econ.get("trading_pnl_before_fees")) or D("0")
        self.fees_paid += _dec(econ.get("fees_total")) or D("0")
        acc = (econ.get("rebate_accrued_complement") or {})
        self.rebate_accrued += _dec(acc.get("accrual")) or D("0")

        cap, px = _dec(ceiling), _dec(complement_price)
        if cap is not None and px is not None and px > cap:
            self.pairs_above_ceiling += 1

    def exit_(self, econ: Mapping[str, Any], *, kind: str) -> None:
        """kind: SAME_SIDE_MAKER | AGGRESSIVE | RESCUE."""
        if kind == "SAME_SIDE_MAKER":
            self.same_side_exits += 1
        elif kind == "RESCUE":
            self.rescue_exits += 1
        else:
            self.aggressive_exits += 1
        self.trading_pnl += _dec(econ.get("trading_pnl_before_fees")) or D("0")
        self.fees_paid += _dec(econ.get("fees_total")) or D("0")

    def flat_after(self, seconds: float) -> None:
        self.time_to_flat_s.append(float(seconds))

    def markout(self, horizon_s: int, value: Any) -> None:
        v = _dec(value)
        if v is None:
            return
        self.markouts.setdefault(str(horizon_s), []).append(float(v))

    def capital(self, dollars: Any, minutes: Any) -> None:
        d, m = _dec(dollars), _dec(minutes)
        if d is not None and m is not None and d > 0 and m > 0:
            self.capital_dollar_minutes += d * m

    def credited(self, amount: Any) -> None:
        """A VENUE RECORD ONLY. An accrual must never reach this."""
        v = _dec(amount)
        if v is None:
            return
        self.rebate_credited = (v if self.rebate_credited is None
                                else self.rebate_credited + v)

    # ---------------------------------------------------------------- report
    def net_cents(self) -> D:
        """Trading P&L net of fees. Rebates and residual settlement EXCLUDED."""
        return self.trading_pnl - self.fees_paid

    def primary(self) -> Optional[str]:
        """Net cents per capital-dollar-minute. None when no capital was used --
        which is not zero, and must not be reported as a zero score."""
        if self.capital_dollar_minutes <= 0:
            return None
        return str(self.net_cents() / self.capital_dollar_minutes)

    def report(self) -> Dict[str, Any]:
        bases = [float(b) for b in self.pair_bases if _dec(b) is not None]
        return {
            "schema": SCHEMA, "cohort": self.cohort, "stage": self.stage,
            "accounting_ladder": list(LADDER),

            "PRIMARY_net_cents_per_capital_dollar_minute": self.primary(),
            "net_cents_excl_rebate_and_residual": str(self.net_cents()),

            "windows": self.windows,
            "entries_considered": self.entries_considered,
            "entries_admitted": self.entries_admitted,
            "entry_admission_rate": _pct(self.entries_admitted,
                                         self.entries_considered),
            "entry_refusals_by_gate": dict(self.entry_refusals),

            "first_fills": self.first_fills,
            "maker_first_fill_rate": _pct(self.maker_first_fills,
                                          self.first_fills),
            "zero_ahead_fills": self.zero_ahead_fills,

            "complement_opportunities": self.complement_opportunities,
            "acceptable_complement_opportunities":
                self.acceptable_complement_opportunities,
            "acceptable_share_of_complement_opportunities": _pct(
                self.acceptable_complement_opportunities,
                self.complement_opportunities),
            "complement_fills": self.complement_fills,
            "maker_to_maker_completion_rate": _pct(
                self.maker_to_maker_completions, self.first_fills),

            "completions_considered": self.completions_considered,
            "completions_vetoed": self.completions_vetoed,
            "veto_reasons": dict(self.veto_reasons),

            "RISK_OVERLAY_cost": {
                "classification": "RISK_OVERLAY, not strategy fidelity",
                "ruling": ("L5_OPERATING_MODEL.md 2d (owner 2026-09-18): the "
                           "Gemini live path takes NO pair-cost ceiling; any "
                           "future ceiling must be labelled a risk overlay and "
                           "carry its stated cost. This is that cost."),
                "completes_forbidden": self.completions_vetoed,
                "share_of_completes_forbidden": _pct(
                    self.completions_vetoed, self.completions_considered),
                "forbidden_basis_median": _median(
                    [float(b) for b in self.forbidden_bases
                     if _dec(b) is not None]),
                "forbidden_that_would_have_been_fee_positive":
                    self.forbidden_would_have_been_fee_positive,
                "limit": ("INSTANTANEOUS. Does not capture the ruling's "
                          "downstream argument that 97.21% of expensive "
                          "completes were followed by further fills and 96.78% "
                          "of those windows ended better. Only the window-level "
                          "comparison against the untouched control can."),
            },

            "same_side_exits": self.same_side_exits,
            "aggressive_exits": self.aggressive_exits,
            "rescue_exits": self.rescue_exits,

            "median_time_first_fill_to_flat_s": _median(self.time_to_flat_s),
            "matched_qty": str(self.matched_qty),
            "pair_basis_n": len(bases),
            "pair_basis_median": _median(bases),
            "pair_basis_min": (min(bases) if bases else None),
            "pair_basis_max": (max(bases) if bases else None),
            "pairs_at_or_below_one": self.pairs_at_or_below_one,
            "fee_positive_pairs": self.fee_positive_pairs,
            "PAIRS_ABOVE_CEILING_defect_counter": self.pairs_above_ceiling,

            "residual_qty": str(self.residual_qty),
            "residual_settlement_pnl": (None
                                        if self.residual_settlement_pnl is None
                                        else str(self.residual_settlement_pnl)),

            "trading_pnl_before_fees": str(self.trading_pnl),
            "fees_paid": str(self.fees_paid),
            "rebate_accrued_NOT_INCOME": str(self.rebate_accrued),
            "rebate_credited_venue_record": (
                None if self.rebate_credited is None
                else str(self.rebate_credited)),

            "capital_dollar_minutes": str(self.capital_dollar_minutes),
            "markout_medians_by_horizon_s": {
                k: _median(v) for k, v in sorted(self.markouts.items())},

            "labels": [
                "Fill count is a denominator here, never an objective.",
                "PAIRS_ABOVE_CEILING is a DEFECT counter. Any value above "
                "zero means the veto leaked and the run is not a result.",
                "rebate_accrued is ACCRUED FROM PUBLISHED RULES and is "
                "excluded from net_cents. Only rebate_credited is a venue "
                "record.",
                "residual settlement P&L is reported apart and is never "
                "folded into matched-pair economics.",
            ],
        }
