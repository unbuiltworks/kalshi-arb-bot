"""SHADOW ONLY: the entry band and the held-inventory band are two rules.

OWNER GO (this cycle): "Separate the existing band into TWO concepts in
analysis/shadow. A. ENTRY BAND, applied when flat / initiating new market
inventory. B. HELD-INVENTORY BAND, what happens when we already have
genuine inventory and the sampled action is EXTEND or COMPLETE. Do not
assume they should use the same rule."

They are already two rules in production and nobody wrote it down. The
live shell fires `entry_band_left` when

    not q1.entry_ok(bid)  OR  (flat AND bid outside the observation band)

so with HELD inventory only the LEGAL-PRICE band (0.20-0.80) can fire,
while FLAT is additionally gated by the observation band (0.25-0.75).
The consequence has never been stated: a rule whose name, comment and
justification are all about ADMITTING A NEW MARKET is, in the held case,
adjudicating whether to add to a position we already own.

Nothing here submits, prices, or reprices anything. It reads decisions the
live engine already made and scores counterfactual arms over them.

THE ARMS
  CONTROL   exactly what the shell did.
  SHADOW_1  ENTRY-ONLY: the band gates OPEN/new inventory; a held-inventory
            decision follows its sampled action, subject to every OTHER
            gate unchanged (they are re-checked, not waived).
  SHADOW_2  HELD-BAND BUFFER: entry unchanged; when holding, EXTEND is
            permitted only inside a widened continuation band. Fixed
            widths +-5c / +-10c / +-15c and unrestricted. FIXED, NOT FITTED
            -- no width is chosen by looking at the outcome.

TWO ECONOMICS, NEVER MIXED
  TAPE_SUPPORTED  a counterfactual EXTEND counts as filled ONLY where the
                  tape shows an actual EXTEND order filling in the same
                  market inside the support window. No support, no fill.
  FILL_ASSUMED    every admitted EXTEND fills at the held-side price. This
                  is an UPPER BOUND and is labelled as one. It exists so
                  this result is directly comparable with the 2026-09-07
                  band study, which was computed fill-assumed.

Settlement is venue-anchored: the winning side is derived from the
market's CONFIRMED realized number against its final inventory and spend,
not from a public settlement price. A market whose realized number we do
not have contributes to neither economics column -- it is reported as
UNSETTLED, never as zero.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional, Sequence

# production constants, mirrored as VALUES and tied to their owning module
# by a test, so a drift in production shows up as a red test and not as a
# quietly wrong counterfactual
LEGAL_LO, LEGAL_HI = D("0.20"), D("0.80")        # q1 MIN_PRICE / MAX_PRICE
OBS_LO, OBS_HI = D("0.25"), D("0.75")            # SPRING_BAND_LO / HI
BUFFERS = (D("0.05"), D("0.10"), D("0.15"))
# The OTHER gates, which the owner required be left in force. These are the
# ACTIVE R5FT rung's values, mirrored here and tied to the rung by a test.
# Leaving them out is what turns a counterfactual into a fantasy: without
# the residual cap the lad-mia arm "buys" 78 more contracts of a 5-cent
# tail, which no rung would ever have permitted.
MAX_RESIDUAL_UNITS = 6                 # R5FT rung residual_units
PER_MARKET_CAP_USD = D("30.00")        # R5FT rung per_market_cap_usd
ARMS = ("CONTROL", "SHADOW_1_ENTRY_ONLY", "SHADOW_2_BUF_5c",
        "SHADOW_2_BUF_10c", "SHADOW_2_BUF_15c", "SHADOW_2_UNRESTRICTED")
SUPPORT_WINDOW_S = 600.0
UNKNOWN = "unknown_not_observable_from_the_tape"


def _d(x: Any, default: Optional[D] = None) -> Optional[D]:
    try:
        return D(str(x))
    except Exception:                                        # noqa: BLE001
        return default


def band_fired(bid: D, held: D) -> bool:
    """Production's own condition, restated. Held inventory is gated ONLY
    by the legal-price band; flat is gated by both."""
    if not (LEGAL_LO <= bid <= LEGAL_HI):
        return True
    return held <= 0 and not (OBS_LO <= bid <= OBS_HI)


def distance_outside(bid: D) -> D:
    """How far the bid is outside the legal band, in price. 0 inside."""
    if bid < LEGAL_LO:
        return LEGAL_LO - bid
    if bid > LEGAL_HI:
        return bid - LEGAL_HI
    return D("0")


def admits(arm: str, *, bid: D, held: D, sampled: str) -> bool:
    """Would this arm permit the sampled action to be attempted?

    Only EXTEND and OPEN are ever refused by this gate; a sampled COMPLETE
    was never blocked by it, so every arm admits it identically and the
    comparison is confined to what actually differs.
    """
    if sampled == "complete":
        return True
    if arm == "CONTROL":
        return not band_fired(bid, held)
    if held <= 0:
        # ENTRY is unchanged in every arm. Widening the band for a market
        # we do not yet hold would be a different experiment, and a
        # riskier one, than the one the owner asked for.
        return not band_fired(bid, held)
    if arm == "SHADOW_1_ENTRY_ONLY":
        return True
    if arm == "SHADOW_2_UNRESTRICTED":
        return True
    if arm.startswith("SHADOW_2_BUF_"):
        cents = arm.rsplit("_", 1)[1].rstrip("c")
        w = D(cents) / D("100")
        return (LEGAL_LO - w) <= bid <= (LEGAL_HI + w)
    raise ValueError(f"unknown arm {arm!r}")


def held_side_price(bid: D, held_side: str) -> D:
    """The price the held side trades at, from the recorded book bid.

    A PROXY, and named one: refusal rows record only the bid, so the
    held-side ask is one tick away and unrecorded. Every price derived
    here is therefore +-1 tick, which is stated wherever it is used and
    never silently treated as the executed price.
    """
    return (D("1") - bid) if held_side == "dn" else bid


def winning_side(realized_usd: D, up: D, dn: D, spent: D,
                 tol: D = D("0.01")) -> Optional[str]:
    """Which outcome the venue actually paid, derived from OUR confirmed
    realized number rather than from a public settlement price.

    If UP paid, the account received `up`; if DOWN paid, `dn`. Exactly one
    of (up - spent) and (dn - spent) should reproduce the venue's realized
    figure. When neither does, the answer is None -- an unexplained
    realized number is a reconciliation failure, not a coin flip.
    """
    if abs((up - spent) - realized_usd) <= tol:
        return "up"
    if abs((dn - spent) - realized_usd) <= tol:
        return "dn"
    return None


def settlement_value(contracts: D, price: D, side: str,
                     winner: Optional[str]) -> Optional[D]:
    """P&L of holding `contracts` of `side` bought at `price` to
    settlement. None when the winner is unknown -- never 0."""
    if winner is None:
        return None
    return contracts * ((D("1") - price) if side == winner else -price)


def residual_cap_allows(residual_shares: D, unit: Optional[D],
                        max_units: int = MAX_RESIDUAL_UNITS) -> bool:
    """Production's own EXTEND bound, applied to the SHADOW inventory.

    `behavioral_residual() = residual / unit`, and the live shell refuses a
    further same-side EXTEND once its magnitude reaches the rung's cap. A
    counterfactual that lets extends fill must therefore re-check this on
    the inventory the counterfactual has created, not on the inventory the
    engine actually had -- otherwise the arm quietly buys through a risk
    bound the owner set and reports the proceeds as upside.
    """
    if unit is None or unit == 0:
        return True
    return abs(residual_shares / unit) < max_units


def spend_cap_allows(spent: D, extra: D,
                     cap: D = PER_MARKET_CAP_USD) -> bool:
    """The per-market dollar cap, likewise re-checked on shadow spend."""
    return (spent + extra) <= cap


def tape_support(t_epoch: float, slug: str,
                 extend_fills: Mapping[str, Sequence[float]],
                 window_s: float = SUPPORT_WINDOW_S) -> bool:
    """Did an ACTUAL extend order fill in this market near this moment?

    The strict arm's whole discipline. An extend that the shell refused
    was never posted, so nothing about its fill is observable; the closest
    honest evidence is that a real extend, in the same market and the same
    regime, did fill. Outside that window the answer is UNKNOWN and the
    strict column credits nothing.
    """
    return any(abs(t_epoch - f) <= window_s
               for f in extend_fills.get(slug, ()))
