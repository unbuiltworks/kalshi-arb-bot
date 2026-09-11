"""The risk-bearing set, watched independently of the scheduler.

OWNER 2026-09-11: "emit an alarm on scheduler-held -> absent while slug
remains in unsettled_slugs() and no terminal settlement evidence has been
observed. That gives us a durable signal without changing money behavior."

READ-ONLY. Pure functions over two sets of slugs plus a combined-loss
figure. No venue call, no order, no cap, no gate. Nothing here can change
what the engine does; it can only make what the engine is carrying visible.

WHY IT EXISTS, AND WHY THE ANSWER WAS ALREADY SAFE
--------------------------------------------------
On 2026-09-11 aec-county-not-ham-2026-09-08 was held and quarantined at
12:03:20Z and absent from `held` by 13:18:11Z, with no settlement row
captured. The first read of that -- mine -- was that combined_loss prices
`held`, so the position had fallen out of the daily-risk budget. That was
WRONG, and the correction is the reason this module is an alarm rather than
a stop: every risk path keys off `unsettled_slugs()`, the LEDGER's set of
markets filled into with no `settled` row, and a market leaves that set only
when the settlement auditor records a venue POSITION_RESOLUTION. Scheduler
state cannot erase economic exposure.

So the divergence this watches for is not a risk hole. It is an ACCOUNTING
hole, and it fails in the conservative direction: a market whose resolution
we missed keeps being charged against the daily stop while its settlement
cashflow never enters realized_today. That over-states loss, permanently
consumes budget, and never self-clears -- a slow leak toward a spurious
stop rather than toward unmonitored risk.

THE REASON IT COULD NOT BE DIAGNOSED FROM THE LOGS
--------------------------------------------------
The running probe never emitted its risk set at all. `unsettled` appears in
`rung_markets_reached`, which a continuous campaign never reaches, and in
the shutdown_idle budget re-price, which only runs once halted. So on the
normal path there was no row saying what the engine was carrying. That is
the gap this closes first; the alarm is the second thing it does.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional


def snapshot(unsettled: Iterable[str], held: Iterable[str],
             combined_loss_usd: Optional[str] = None,
             daily_loss_stop_usd: Optional[str] = None) -> dict:
    """What the engine is carrying, against what it is scheduling.

    `unsettled` is the RISK-BEARING set (ledger, filled-and-not-settled).
    `held` is the SCHEDULER's set (venue positions the loop is working).
    They are different questions and this row is where that is legible.
    """
    u, h = sorted(set(unsettled or ())), sorted(set(held or ()))
    charged_not_scheduled = sorted(set(u) - set(h))
    scheduled_not_charged = sorted(set(h) - set(u))
    return {
        "risk_bearing": u,
        "scheduled": h,
        # carried in the loss budget but no longer worked by the loop. Not
        # an error by itself: a quarantined or resolved-but-unaudited market
        # both land here, and so does any market the venue has dropped.
        "charged_not_scheduled": charged_not_scheduled,
        # the venue shows a position the ledger has already settled, or never
        # recorded. Rarer and more surprising than the other direction.
        "scheduled_not_charged": scheduled_not_charged,
        "n_risk_bearing": len(u),
        "n_scheduled": len(h),
        "combined_loss_usd": combined_loss_usd,
        "daily_loss_stop_usd": daily_loss_stop_usd,
        "labels": ("risk is priced over `risk_bearing` (unsettled_slugs), "
                   "never over `scheduled` (held). A market leaves the risk "
                   "set only on a venue-confirmed settlement row."),
    }


def detect(prev_held: Iterable[str], held: Iterable[str],
           unsettled: Iterable[str]) -> list:
    """The owner's condition, exactly: a slug that WAS scheduled, is now
    absent, and is still in the risk-bearing set.

    Being in `unsettled` IS the "no terminal settlement evidence" half of
    the condition -- that set is defined as filled-with-no-settled-row, so a
    slug cannot be in it and have terminal evidence recorded. Checking
    settlement separately would be a second, driftable definition of the
    same fact.

    Returns one row per slug. An empty list is the normal case and must stay
    cheap: this runs on every cycle of the live loop."""
    prev, now, un = set(prev_held or ()), set(held or ()), set(unsettled or ())
    out = []
    for slug in sorted((prev - now) & un):
        out.append({
            "alarm": "held_absent_while_risk_bearing",
            "slug": slug,
            "was_scheduled": True,
            "still_risk_bearing": True,
            "terminal_settlement_recorded": False,
            "reading": (
                "left the scheduler's held set with no settlement row. Its "
                "full downside is STILL charged against the daily stop, so "
                "this is an accounting/observability signal, not an "
                "uncovered-risk one: if the market did resolve, we are "
                "over-stating loss and withholding realized cashflow until "
                "the settlement audit records it."),
            "action": "none taken; read-only alarm by owner decision",
        })
    return out


def changed(prev: Optional[Mapping[str, Any]], now: Mapping[str, Any]) -> bool:
    """Emit the snapshot when the sets move, not on every cycle. The loop
    turns every SELECT_POLL_S and an unchanging row every 15 s buries the
    one row that matters."""
    if not prev:
        return True
    for k in ("risk_bearing", "scheduled"):
        if list(prev.get(k) or ()) != list(now.get(k) or ()):
            return True
    return False
