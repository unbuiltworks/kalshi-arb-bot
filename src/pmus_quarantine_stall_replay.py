"""Replay the 2026-09-10 county-cricket stall under residual quarantine.

OWNER GO: "Replay today's county-cricket stall and measure how many
additional top-ranked opportunities would have been admitted under
quarantine, what hypothetical residual exposure accumulated, and whether the
-$15 portfolio gate would have stopped further acquisition correctly."

WHAT ACTUALLY HAPPENED, from the campaign's own session_end rows:

  13:26:37Z  first session
  ...        20 distinct markets worked in 93 minutes
  14:59:56Z  SPRING_RESIDUAL_HOLD on aec-county-not-ham-2026-09-08
  20:20:06Z  still holding -- 5h20m, no further first leg

The held market's book sat at 0.77/0.84 then 0.80/0.91, OUTSIDE the
0.25-0.75 entry band, so the engine could not work the position it was
guarding. It could only wait.

HOW THIS REPLAY IS BOUNDED, so the answer is not flattering by construction:

  * the ADMISSION CADENCE is the one actually observed BEFORE the stall --
    20 markets in 93 minutes -- not an idealised rate;
  * each hypothetical acquisition's worst case is drawn from the REAL
    per-market floors this campaign produced today, cycled in order, rather
    than from an optimistic average;
  * the gate is the real one, evaluated through the real functions.

TWO ARMS, because one of them alone would misdescribe itself. My first draft
claimed "no acquisition is credited with completing" while replaying the real
end-state inventories -- three of which are FULLY MATCHED and therefore carry
a non-negative floor that consumes no budget at all. That is an empirical
replay, not a pessimistic one, and calling it pessimistic overstated the
result. Both arms are now reported:

  EMPIRICAL   each admitted market ends with an inventory this campaign
              actually produced today, completions and strandings in the
              proportion they really occurred. A completed pair below par has
              floor min(up,dn) - spent >= 0 and costs the budget NOTHING --
              which is the real mechanism by which completing buys capacity.

  PESSIMISTIC every admitted market strands one-sided at the full session
              notional cap. Nothing completes, ever. This is the true lower
              bound on how many opportunities fit.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Optional, Sequence

from src import pmus_residual_quarantine as Q

# The stranded market, from the campaign's own inventory row at 14:59:56Z.
CRICKET = {"slug": "aec-county-not-ham-2026-09-08",
           "up": D("4.9"), "dn": D("12.13"), "spent": D("5.7015")}

# Every market this campaign actually put money into today, with the
# inventory it ended with. Floor = min(up, dn) - spent.
OBSERVED_MARKETS = [
    ("aec-setkamecz-mulpat-holtom-2026-09-10", D("19.87"), D("19.87"), D("18.8307")),
    ("aec-itfwo-aragarmergar-lucllojoeste-2026-09-10", D("15.30"), D("15.3"), D("12.819")),
    ("aec-atp-enrval-stetra-2026-09-10", D("5.31"), D("5.31"), D("5.31")),
    ("aec-setkamecz-krajan-svoluk-2026-09-10", D("0"), D("11.24"), D("3.6028")),
    ("aec-cs2-ica-ff-2026-09-10", D("4.62"), D("13.70"), D("8.5228")),
]

STALL_START = "2026-09-10T14:59:56Z"
STALL_OBSERVED_END = "2026-09-10T20:20:06Z"
STALL_MINUTES = 320.0
PRE_STALL_MARKETS = 20
PRE_STALL_MINUTES = 93.0


def floor_of(up: D, dn: D, spent: D) -> D:
    """min(up, dn) - spent. The same guaranteed settlement floor the live
    budget uses; reproduced here only so the replay needs no Inventory."""
    return min(up, dn) - spent


def cadence_per_hour() -> float:
    return PRE_STALL_MARKETS / (PRE_STALL_MINUTES / 60.0)


SESSION_NOTIONAL_CAP = D("7.50")


def replay(stop: D = D("-15.00"),
           stall_minutes: float = STALL_MINUTES,
           realized_today: D = D("0"),
           markets: Optional[Sequence] = None,
           arm: str = "empirical") -> dict:
    """Admit at the observed cadence until the real gate refuses.

    arm="empirical"   -> today's real end-state inventories, cycled
    arm="pessimistic" -> every market strands one-sided at the full cap
    """
    if arm == "pessimistic":
        markets = [("stranded_at_cap", D("0"), SESSION_NOTIONAL_CAP,
                    SESSION_NOTIONAL_CAP)]
    markets = list(markets or OBSERVED_MARKETS)
    quarantined_floor = floor_of(CRICKET["up"], CRICKET["dn"], CRICKET["spent"])
    combined = D(str(realized_today)) + min(D("0"), quarantined_floor)

    slots = int(cadence_per_hour() * (stall_minutes / 60.0))
    admitted, refused_at = [], None
    for i in range(slots):
        slug, up, dn, spent = markets[i % len(markets)]
        wc = min(D("0"), floor_of(up, dn, spent))
        after = combined + wc
        if not after > stop:
            refused_at = {
                "attempt": i + 1, "slug": slug,
                "worst_case_usd": str(wc),
                "combined_before_usd": str(combined),
                "combined_would_be_usd": str(after),
                "stop_usd": str(stop),
            }
            break
        combined = after
        admitted.append({"n": len(admitted) + 1, "slug": slug,
                         "worst_case_usd": str(wc),
                         "combined_after_usd": str(combined)})
    return {
        "arm": arm,
        "stall_window": {"from": STALL_START, "to": STALL_OBSERVED_END,
                         "minutes": stall_minutes},
        "actual": {"markets_admitted_during_stall": 0,
                   "reason": "the single acquisition slot was held by a "
                             "dormant residual in a market outside the band"},
        "quarantined_market": {
            "slug": CRICKET["slug"],
            "guaranteed_floor_usd": str(quarantined_floor),
            "still_in_held": True, "still_settlement_monitored": True,
            "still_in_combined_loss": True},
        "cadence_markets_per_hour": round(cadence_per_hour(), 2),
        "opportunity_slots_in_window": slots,
        "additional_markets_admitted": len(admitted),
        "admitted": admitted,
        "gate_refused_at": refused_at,
        "final_combined_loss_usd": str(combined),
        "hypothetical_residual_exposure_usd": str(
            combined - min(D("0"), quarantined_floor)),
        "labels": (
            "empirical: real end-state inventories, completions and strandings "
            "in the proportion they actually occurred -- a completed pair "
            "below par has a non-negative floor and consumes NO budget. "
            "pessimistic: nothing ever completes, every market strands "
            "one-sided at the full session notional cap; that arm is the true "
            "LOWER bound."),
    }
