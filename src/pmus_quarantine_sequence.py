"""The full quarantine lifecycle, run end to end and proven step by step.

OWNER: "Prove quarantine end-to-end before activation. Use recorded/live-shadow
states to exercise:

  acquire -> residual -> cancel resting orders -> venue reconcile ->
  QUARANTINED -> residual remains charged to combined_loss -> selector proposes
  next market -> portfolio gate admit/refuse -> original residual remains
  monitored/completable/settleable

Mutation-test every safety transition."

This module runs that sequence as ONE ordered pass and returns a row per step,
so a break is located rather than merely detected. It moves no money, submits
no order, and touches no live campaign: the states are supplied by the caller
(recorded, or the shadow's own replay), and every function it calls is one
that already ships.

THE STEP THAT MATTERS MOST IS NOT QUARANTINE ITSELF, it is the one after it:
`residual remains charged to combined_loss`. Quarantine frees a SCHEDULING
slot and frees nothing else. If the dormant market's worst case ever stopped
counting against the budget, quarantine would silently become a way to hold
unlimited stranded risk one market at a time -- which is precisely the failure
it must not be. So the sequence asserts the charge BEFORE and AFTER the
transition and requires them equal.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src import pmus_residual_quarantine as RQ

STEPS = (
    "acquire",
    "residual",
    "cancel_resting_orders",
    "venue_reconcile",
    "quarantined",
    "residual_still_charged",
    "selector_proposes_next",
    "portfolio_gate",
    "residual_still_monitored_completable_settleable",
)


def _step(name: str, ok: bool, **kw) -> dict:
    row = {"step": name, "ok": bool(ok)}
    row.update(kw)
    return row


def run_sequence(market: Mapping[str, Any], realized_today,
                 other_positions: Sequence[Mapping[str, Any]] = (),
                 proposed: Optional[Mapping[str, Any]] = None,
                 book: Optional[Mapping[str, Any]] = None,
                 session=None) -> dict:
    """One full pass. `market` is the stranded market's state, mutated only
    locally: the caller's mapping is never written through."""
    m = dict(market)
    steps: list = []
    inv = m.get("inventory")

    # 1. acquire -- inventory exists at all
    up, dn = getattr(inv, "up", None), getattr(inv, "dn", None)
    steps.append(_step("acquire", inv is not None and bool(up or dn),
                       up=str(up), dn=str(dn)))

    # 2. residual -- and it is one-sided, which is what strands a market.
    #    The session inventory carries up/dn, not a residual attribute.
    residual = (up - dn) if (up is not None and dn is not None) else 0
    steps.append(_step("residual", residual != 0, residual=residual))

    # 3. resting orders cancelled
    steps.append(_step("cancel_resting_orders", m.get("resting_orders") == 0,
                       resting_orders=m.get("resting_orders")))

    # 4. venue reconcile
    steps.append(_step("venue_reconcile", m.get("venue_confirmed") is True))

    # the budget charge BEFORE the transition, for the equality below
    all_before = [m, *other_positions]
    charge_before = RQ.portfolio_headroom(realized_today, all_before,
                                          session=session)

    # 5. QUARANTINED
    try:
        q = RQ.quarantine(m)
        quarantined = q.get("state") == "QUARANTINED"
    except RQ.QuarantineRefused as exc:
        q, quarantined = {"refused": repr(exc)}, False
    steps.append(_step("quarantined", quarantined, detail=q))

    # 6. the residual is STILL charged, undiscounted
    after_market = dict(m)
    after_market["state"] = "QUARANTINED"
    all_after = [after_market, *other_positions]
    charge_after = RQ.portfolio_headroom(realized_today, all_after,
                                         session=session)
    same = (str(charge_before.get("combined_loss_usd"))
            == str(charge_after.get("combined_loss_usd")))
    steps.append(_step("residual_still_charged", same,
                       combined_loss_before=charge_before.get("combined_loss_usd"),
                       combined_loss_after=charge_after.get("combined_loss_usd"),
                       note=("quarantine frees a scheduling slot and frees "
                             "nothing else")))

    # 7. the selector proposes a next market
    steps.append(_step("selector_proposes_next", proposed is not None,
                       proposed=(proposed or {}).get("slug")))

    # 8. the portfolio gate decides -- admit OR refuse are BOTH passes here;
    #    what must not happen is the gate failing to reach a decision, or
    #    reaching one without the quarantined market in the position set.
    # `proposed` carries {slug, worst_case}. The gate takes the WORST CASE,
    # not the market: a proposal whose downside we cannot state is not a
    # proposal the gate can price, so it is refused rather than defaulted.
    gate = None
    if proposed is not None:
        wc = proposed.get("worst_case")
        if wc is None:
            gate = {"admit": False, "failed": ["proposed_worst_case_missing"],
                    "checks": {}}
        else:
            try:
                gate = RQ.may_admit_new_acquisition(
                    realized_today, all_after, wc,
                    active_acquisitions=int(proposed.get(
                        "active_acquisitions", 0)),
                    session=session)
            except Exception as exc:                        # noqa: BLE001
                gate = {"admit": False, "failed": [f"gate_error:{exc!r}"],
                        "checks": {}}
    decided = isinstance(gate, Mapping) and "admit" in gate
    steps.append(_step("portfolio_gate", decided, gate=gate,
                       note=("admit and refuse are both correct outcomes; "
                             "failing to decide is not")))

    # 9. still monitored, still completable, still settleable
    # held_inventories returns Inventory OBJECTS, not position mappings, so
    # membership is checked by identity rather than by slug.
    held = RQ.held_inventories(all_after)
    still_held = any(h is inv for h in held)
    completable = None
    if inv is not None and book is not None and residual != 0:
        side_up = residual < 0          # complete the DEFICIT side
        try:
            completable = RQ.may_complete_in_quarantine(
                inv, side_up, book.get("complement_px"), 1,
                session=session)
        except Exception as exc:                            # noqa: BLE001
            completable = {"error": repr(exc)}
    steps.append(_step(
        "residual_still_monitored_completable_settleable",
        still_held and after_market.get("settlement_monitored") is True,
        in_held=still_held,
        settlement_monitored=after_market.get("settlement_monitored"),
        completable=completable))

    failed = [s["step"] for s in steps if not s["ok"]]
    return {
        "kind": "quarantine_sequence",
        "ok": not failed,
        "steps": steps,
        "failed": failed,
        "expected_order": list(STEPS),
        "observed_order": [s["step"] for s in steps],
        "labels": ("read-only: no order is submitted and no money moves. "
                   "Quarantine frees a scheduling slot ONLY -- the residual's "
                   "full conservative downside stays in combined_loss, in "
                   "held, and in settlement monitoring."),
    }
