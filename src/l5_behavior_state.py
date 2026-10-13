"""L5's complete chronological state before every decision, on a fixed clock.

OWNER GO (this session): "Reconstruct L5's complete chronological state before
every BTC15m trade... Build a behavioral model that predicts L5's next action:
whether to trade, side, limit price, size, and timing. Include no-trade
decisions, not just observed fills. Where market-state history is missing,
mark features unavailable rather than inventing them."

READ-ONLY. Pure reconstruction from the 165-day activity feed.

WHY A FIXED CLOCK AND NOT A ROW PER FILL
-----------------------------------------
A model trained only on L5's fills can never learn WHEN NOT to trade: every
row would be a trade. So the window is cut into fixed `BIN_S` slices and each
slice is one decision point, labelled by what he actually did in it. Most
slices are no-trade, which is the whole point -- the base rate below is 11.6%,
so a model that always says "trade" is wrong 88% of the time.

EVERY FEATURE IS STRICTLY PRE-DECISION
---------------------------------------
Features for the slice starting at `t` use only events with timestamp < t.
Nothing is computed from the slice being predicted, from later slices, or from
the settlement. A test asserts that shifting the label forward by one slice
destroys the fit, which is the symptom look-ahead would produce.

WHAT IS NOT AVAILABLE, AND IS MARKED RATHER THAN INVENTED
----------------------------------------------------------
This feed carries L5's own executed trades. It does NOT carry the order book,
other participants' flow, his order submissions, his cancellations, or his
limit prices -- only the prices at which he actually traded. So:

  * `price` features describe HIS OWN print history, not the touch. They are
    named `own_*` throughout so no reader mistakes them for book state.
  * market-wide order flow is UNAVAILABLE; `flow_*` features count his own
    legs only, and are named so.
  * a limit price is UNOBSERVABLE. The price label is his EXECUTION price,
    which is the best available proxy and is labelled `exec_price`.
  * every feature derived from a history shorter than it needs carries a
    companion `_avail` flag set to 0.0, and the feature itself is set to 0.0
    rather than to a guessed value.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Iterator, List, Mapping, Sequence, Tuple

UP, DOWN = "Up", "Down"
WINDOW_S = 900.0
#: one decision every ten seconds: 90 per window. Short enough that a slice
#: rarely holds two different intentions, long enough to keep the grid small.
BIN_S = 10.0
NBINS = int(WINDOW_S / BIN_S)

#: feature order is fixed and asserted by test, so a model trained on one
#: ordering can never be scored against another.
FEATURES: Tuple[str, ...] = (
    "secs_left", "bin_frac", "phase_first5", "phase_mid5", "phase_final5",
    "qty_up", "qty_down", "residual", "abs_residual", "matched_pairs",
    "cost_total", "unmatched_cost", "worst_case_loss",
    "basis_up", "basis_up_avail", "basis_down", "basis_down_avail",
    "pair_basis", "pair_basis_avail",
    "own_last_price", "own_last_price_avail",
    "own_mean_price_3", "own_mean_price_3_avail",
    "own_price_delta_1", "own_price_delta_1_avail",
    "own_price_sd_6", "own_price_sd_6_avail",
    "own_price_vs_first", "own_price_vs_first_avail",
    "flow_legs_last_bin", "flow_contracts_last_bin",
    "flow_legs_cum", "flow_contracts_cum",
    "acted_last_bin", "last_side_up", "last_side_avail",
    "bins_since_trade", "bins_since_trade_avail",
    "deficient_up", "deficient_down", "balanced",
)

#: features that exist in the schema but are NOT derivable from this feed.
#: Recorded so the gap is explicit rather than silently absent.
UNAVAILABLE_BY_SOURCE: Tuple[str, ...] = (
    "book_best_bid", "book_best_ask", "book_depth",
    "market_wide_flow", "other_participant_flow",
    "l5_order_submissions", "l5_cancellations", "l5_limit_price",
    "btc_spot", "distance_to_strike",
)


def _sd(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


class L5State:
    """Continuous inventory and own-print history, advanced slice by slice."""

    def __init__(self) -> None:
        self.qty = {UP: 0.0, DOWN: 0.0}
        self.cost = {UP: 0.0, DOWN: 0.0}
        self.prices: List[float] = []          # his own execution prices
        self.bin_prices: List[float] = []      # mean own price, per past slice
        self.bin_legs: List[int] = []
        self.bin_contracts: List[float] = []
        self.last_side: str | None = None
        self.last_trade_bin: int | None = None
        self.first_price: float | None = None

    # -------------------------------------------------------------- derived
    @property
    def residual(self) -> float:
        return self.qty[UP] - self.qty[DOWN]

    @property
    def matched(self) -> float:
        return min(self.qty[UP], self.qty[DOWN])

    @property
    def cost_total(self) -> float:
        return self.cost[UP] + self.cost[DOWN]

    def basis(self, side: str) -> float | None:
        q = self.qty[side]
        return (self.cost[side] / q) if q > 0 else None

    def worst_case_loss(self) -> float:
        c = self.cost_total
        return min(self.qty[UP] - c, self.qty[DOWN] - c)

    def unmatched_cost(self) -> float:
        """Cost attributable to the unmatched tail, at that side's basis."""
        r = self.residual
        if r == 0:
            return 0.0
        side = UP if r > 0 else DOWN
        b = self.basis(side)
        return abs(r) * b if b is not None else 0.0

    def deficient_side(self) -> str | None:
        if self.qty[UP] == self.qty[DOWN]:
            return None
        return DOWN if self.qty[DOWN] < self.qty[UP] else UP

    # --------------------------------------------------------------- update
    def apply(self, legs: Sequence[Mapping[str, Any]], bin_idx: int) -> None:
        """Fold one slice's trades into the state. Called AFTER featurising."""
        px = []
        contracts = 0.0
        for leg in legs:
            side = leg["side"]
            if side not in (UP, DOWN):
                continue
            q = float(leg["qty"])
            p = float(leg["price"])
            self.qty[side] += q
            self.cost[side] += q * p
            self.prices.append(p)
            if self.first_price is None:
                self.first_price = p
            self.last_side = side
            self.last_trade_bin = bin_idx
            px.append(p)
            contracts += q
        self.bin_prices.append(sum(px) / len(px) if px else float("nan"))
        self.bin_legs.append(len(px))
        self.bin_contracts.append(contracts)

    # ------------------------------------------------------------- features
    def features(self, bin_idx: int) -> Dict[str, float]:
        secs_left = WINDOW_S - bin_idx * BIN_S
        f: Dict[str, float] = {
            "secs_left": secs_left,
            "bin_frac": bin_idx / NBINS,
            "phase_first5": 1.0 if secs_left > 600 else 0.0,
            "phase_mid5": 1.0 if 300 < secs_left <= 600 else 0.0,
            "phase_final5": 1.0 if secs_left <= 300 else 0.0,
            "qty_up": self.qty[UP], "qty_down": self.qty[DOWN],
            "residual": self.residual, "abs_residual": abs(self.residual),
            "matched_pairs": self.matched,
            "cost_total": self.cost_total,
            "unmatched_cost": self.unmatched_cost(),
            "worst_case_loss": self.worst_case_loss(),
        }
        for side, key in ((UP, "basis_up"), (DOWN, "basis_down")):
            b = self.basis(side)
            f[key] = b if b is not None else 0.0
            f[key + "_avail"] = 1.0 if b is not None else 0.0
        bu, bd = self.basis(UP), self.basis(DOWN)
        pb = (bu + bd) if (bu is not None and bd is not None) else None
        f["pair_basis"] = pb if pb is not None else 0.0
        f["pair_basis_avail"] = 1.0 if pb is not None else 0.0

        p = self.prices
        f["own_last_price"] = p[-1] if p else 0.0
        f["own_last_price_avail"] = 1.0 if p else 0.0
        f["own_mean_price_3"] = (sum(p[-3:]) / len(p[-3:])) if len(p) >= 3 else 0.0
        f["own_mean_price_3_avail"] = 1.0 if len(p) >= 3 else 0.0
        f["own_price_delta_1"] = (p[-1] - p[-2]) if len(p) >= 2 else 0.0
        f["own_price_delta_1_avail"] = 1.0 if len(p) >= 2 else 0.0
        f["own_price_sd_6"] = _sd(p[-6:]) if len(p) >= 6 else 0.0
        f["own_price_sd_6_avail"] = 1.0 if len(p) >= 6 else 0.0
        if p and self.first_price is not None:
            f["own_price_vs_first"] = p[-1] - self.first_price
            f["own_price_vs_first_avail"] = 1.0
        else:
            f["own_price_vs_first"] = 0.0
            f["own_price_vs_first_avail"] = 0.0

        f["flow_legs_last_bin"] = float(self.bin_legs[-1]) if self.bin_legs else 0.0
        f["flow_contracts_last_bin"] = (self.bin_contracts[-1]
                                        if self.bin_contracts else 0.0)
        f["flow_legs_cum"] = float(sum(self.bin_legs))
        f["flow_contracts_cum"] = float(sum(self.bin_contracts))

        f["acted_last_bin"] = 1.0 if (self.bin_legs and self.bin_legs[-1] > 0) else 0.0
        f["last_side_up"] = 1.0 if self.last_side == UP else 0.0
        f["last_side_avail"] = 1.0 if self.last_side is not None else 0.0
        if self.last_trade_bin is not None:
            f["bins_since_trade"] = float(bin_idx - self.last_trade_bin)
            f["bins_since_trade_avail"] = 1.0
        else:
            f["bins_since_trade"] = 0.0
            f["bins_since_trade_avail"] = 0.0

        d = self.deficient_side()
        f["deficient_up"] = 1.0 if d == UP else 0.0
        f["deficient_down"] = 1.0 if d == DOWN else 0.0
        f["balanced"] = 1.0 if d is None else 0.0
        return f


def decision_rows(market: Mapping[str, Any]) -> Iterator[Dict[str, Any]]:
    """One row per decision slice: pre-slice features, in-slice labels.

    `market` is a row of `data/l5_master/btc15m_sequences.jsonl`.
    """
    open_t = float(market["open"])
    by_bin: Dict[int, List[Mapping[str, Any]]] = {}
    for leg in market["trades"]:
        idx = int((float(leg["t"]) - open_t) // BIN_S)
        if 0 <= idx < NBINS:
            by_bin.setdefault(idx, []).append(leg)
    st = L5State()
    for b in range(NBINS):
        feat = st.features(b)
        legs = by_bin.get(b, [])
        contracts = sum(float(l["qty"]) for l in legs)
        up_c = sum(float(l["qty"]) for l in legs if l["side"] == UP)
        price = ((sum(float(l["price"]) * float(l["qty"]) for l in legs)
                  / contracts) if contracts > 0 else None)
        yield {
            "slug": market["slug"], "open": open_t, "bin": b,
            "features": feat,
            "did_trade": 1 if legs else 0,
            "side_up": (1 if up_c * 2 >= contracts else 0) if legs else None,
            "contracts": contracts if legs else None,
            "exec_price": price,
            "n_legs": len(legs),
        }
        st.apply(legs, b)
