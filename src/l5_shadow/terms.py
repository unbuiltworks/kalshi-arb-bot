"""VenueTerms for each venue, built from what the VENUE declared.

Polymarket's crypto markets publish their own schedule on every market object
and it is read per window, live:

    feeSchedule {"rate": 0.07, "takerOnly": true, "rebateRate": 0.2}
    orderMinSize 5      orderPriceMinTickSize 0.01

`takerOnly: true` is the single most consequential difference between the two
venues in this program: a Polymarket maker pays NO fee, while a Gemini maker
pays ceil-cent 0.0175*C*P*(1-P). L5's measured trading drag is -0.037% per
dollar, and a fee-free maker side is a large part of why. If the venue stops
declaring the term, this module returns a VenueTerms whose fee functions are
None and every evaluation against it REFUSES -- it never falls back to a
remembered rate.

Gemini's schedule is vendored from `polymarket_l5.transfer_backtest`
(user-verified 2026-08-31). The Sep 2 - Dec 31 maker waiver is deliberately NOT
applied: it is promotional and time-bounded, and pricing against a promo
overstates Gemini's economics the moment it lapses.

THE 0.20-0.80 BAND IS POLICY, NOT A VENUE LIMIT. Gemini quoted 0.95 bids and
0.99 asks in the same session, so the band belongs to the risk shell and is
reported as a flag here rather than enforced as a venue rule -- the evaluator's
job is what the venue can do, and the shell's is what we permit.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.l5_shadow.executable import VenueTerms

POLICY_BAND = (D("0.20"), D("0.80"))


def _tb():
    from src.polymarket_l5 import transfer_backtest as tb
    return tb


def pm_terms(window: Optional[Dict[str, Any]]) -> VenueTerms:
    """Polymarket crypto terms, straight off the live market object."""
    if not window:
        return VenueTerms(venue="PMUS", source="gamma unreachable: terms UNKNOWN")
    rate = window.get("fee_rate")
    taker_only = window.get("fee_taker_only")
    if rate is None:
        return VenueTerms(
            venue="PMUS", min_order_size=_dec(window.get("order_min_size")),
            tick=_dec(window.get("tick")),
            source=(str(window.get("terms_source") or "")
                    + " -- no feeSchedule.rate declared: fee UNKNOWN"))
    r = D(str(rate))

    def taker(px: D, qty: D) -> D:
        # measured against 761,020 L5 fills: usdcSize exceeds price*size on a
        # BUY by EXACTLY rate*p*(1-p) per share, or by nothing at all. No
        # rounding is applied because none was observed.
        return (r * D(str(px)) * (D("1") - D(str(px))) * D(str(qty)))

    maker = None
    if taker_only is True:
        def maker(px: D, qty: D) -> D:      # noqa: F811
            return D("0")                   # takerOnly: the maker pays nothing
    return VenueTerms(
        venue="PMUS", taker_fee=taker, maker_fee=maker,
        min_order_size=_dec(window.get("order_min_size")),
        tick=_dec(window.get("tick")),
        maker_pays_fee=(False if taker_only is True else None),
        source=str(window.get("terms_source") or ""),
        extra={"fee_rate": str(r), "taker_only": taker_only,
               "rebate_rate": window.get("fee_rebate_rate"),
               "rewards_min_size": window.get("rewards_min_size"),
               "rewards_max_spread": window.get("rewards_max_spread"),
               "maker_fee_basis": ("0 -- feeSchedule.takerOnly is true"
                                   if taker_only is True else
                                   "UNKNOWN: venue did not declare takerOnly"),
               "rebate_note": ("rebateRate is declared but NEVER netted here; "
                               "a rebate counts only once credited")})


def gemini_terms() -> VenueTerms:
    """Vendored, user-verified schedule. The promotional waiver is NOT applied."""
    tb = _tb()
    return VenueTerms(
        venue="GEMINI", taker_fee=tb.taker_fee, maker_fee=tb.maker_fee,
        # Gemini's book quotes 1.0-share levels, and no venue minimum above 1
        # has been observed; None would fail closed on submittability, so the
        # observed floor is stated explicitly rather than left unknown.
        min_order_size=D("1"), tick=tb.TICK, maker_pays_fee=True,
        source=("polymarket_l5.transfer_backtest, vendored from the official "
                "Gemini prediction-market schedule (user-verified 2026-08-31): "
                "maker ceil-cent 0.0175*C*P*(1-P), taker ceil-cent "
                "0.07*C*P*(1-P)"),
        extra={"maker_waiver_sep2_dec31": "NOT applied (promotional)",
               "maker_rebate": ("30% of the TAKER formula on maker fills "
                                "priced 20c-80c -- credited, never netted here"),
               "policy_band": [str(POLICY_BAND[0]), str(POLICY_BAND[1])]})


def _dec(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def outside_policy_band(px: Any) -> bool:
    p = _dec(px)
    return p is not None and not (POLICY_BAND[0] <= p <= POLICY_BAND[1])
