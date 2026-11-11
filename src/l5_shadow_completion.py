"""Evaluate the bounded taker completion against MODELLED shadow inventory.

WHY THIS MODULE HAS TO EXIST, and it is not a convenience. Stage B was
approved as continuous realtime shadow with submission structurally blocked
by `ShadowOnlyTransport`. That lock is exactly right for safety and it makes
the obvious collection useless: `ts_completion_inputs` reads
`store.gross_inventory`, which is populated only by REAL fills, so with
nothing submitted there is never any inventory and the live completion path
logs `NO_INVENTORY_TO_COMPLETE` on every tick for every window. Twenty-four
windows of that is not evidence of anything.

A completion policy acts on HELD INVENTORY. To observe it out of sample we
need inventory that the maker-only opening policy would plausibly have held,
and the engine already models exactly that, continuously, on the
authenticated snapshot: `l5_two_sided_shadow.TwoSidedShadow` fills modelled
legs from real prints, tracks per-side quantity and basis, and is finalized
against the VENUE'S OWN binary resolution rather than a book price. Its
`LIVE_SEQUENTIAL` arm at the 0.96 ceiling, two ticks behind the touch, one
order at a time, is described in that module as "the arm that measures what
we actually run".

So the evidence is: MODELLED INVENTORY, REAL BOOK, REAL SETTLEMENT. The
inventory is modelled and is labelled so everywhere. The book the completion
is priced against is the authenticated one, and the outcome that scores it is
the venue's. That is the strongest statement available without submitting an
order, and the comparison it supports is the one that was asked for: the same
arm, with and without completion, on the same windows.

WHAT IS NOT CLAIMED. Nothing here is realized, submitted or filled. A
modelled maker fill is not a fill; the venue publishes no print endpoint, so
joint maker-fill probability is unmeasurable and the modelled inventory
inherits that uncertainty. The conservative/optimistic bands in the shadow
exist precisely because the true fill rate sits somewhere between them.
"""

from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_maker_fill_model as FM
from src import l5_taker_completion_policy as TCP
from src import l5_two_sided_probe as TSP

__all__ = ["arm_id", "is_live_arm", "evaluate", "score_window"]

#: the shadow's own label for the arm that mirrors the deployed engine
LIVE_SEQUENTIAL = "LIVE_SEQUENTIAL"


def _d(v: Any) -> Optional[D]:
    return TSP._d(v)


def arm_id(cfg: Any) -> str:
    """A stable name for one shadow cell, so rows can be joined across ticks.

    The shadow builds many cells from (policy, threshold, envelope, depth), and
    two cells at the same threshold and different depths are NOT comparable --
    that module says so itself. The id therefore carries all four.
    """
    return "%s|t%s|e%s|d%s" % (
        getattr(cfg, "policy", "?"), getattr(cfg, "thresh", "?"),
        getattr(cfg, "envelope", "?"), getattr(cfg, "depth_ticks", "?"))


def is_live_arm(cfg: Any) -> bool:
    """Is this the cell that mirrors what the deployed engine actually runs?"""
    return (str(getattr(cfg, "policy", "")) == LIVE_SEQUENTIAL
            and _d(getattr(cfg, "thresh", None)) == D("0.96")
            and int(getattr(cfg, "depth_ticks", -1) or 0) == 2)


def _own_cross_price(side: str, bid: Optional[D], ask: Optional[D]
                     ) -> Optional[D]:
    """The OWN-outcome price at which buying `side` crosses.

    Buying UP crosses the UP ask. Buying DOWN crosses the DOWN ask, which is
    `1 - the UP BID`. Passing the UP ask for a DOWN completion is the
    arithmetic error this names, and it is the same convention
    `ts_completion_inputs` uses so the two cannot drift.
    """
    if side == FM.UP:
        return None if ask is None else ask
    return None if bid is None else (D(1) - bid)


def _own_cross_depth(side: str, bid_shares: Any, ask_shares: Any) -> Any:
    return ask_shares if side == FM.UP else bid_shares


def evaluate(*, cfg: Any, snapshot: Mapping[str, Any],
             rungs: Any = None,
             tick: Any = D("0.01"), min_qty: Any = D("0.01"),
             worst_case_ceiling: Any = D("1.50"),
             maker_attempted: Optional[bool] = True,
             maker_rested_s: Any = None,
             window_outlay_spent_usd: Any = D(0),
             enabled: bool = True,
             owner_authorized: bool = True,
             halted: Optional[bool] = False,
             **policy_kw: Any) -> Dict[str, Any]:
    """One arm, one tick: would the bounded policy complete this inventory?

    `enabled`/`owner_authorized` default True because this is a MEASUREMENT of
    the policy, not an execution path -- there is no transport here and
    nothing it returns can place an order. The live submission gates are
    separate and untouched.
    """
    out: Dict[str, Any] = {
        "arm": arm_id(cfg), "live_arm": is_live_arm(cfg),
        "policy": str(getattr(cfg, "policy", "?")),
        "basis": "MODELLED INVENTORY, REAL BOOK. Not a fill, not realized.",
    }
    try:
        up = _d(cfg.qty[FM.UP]) or D(0)
        dn = _d(cfg.qty[FM.DOWN]) or D(0)
        bu = _d(cfg.basis(FM.UP)) or D(0)
        bd = _d(cfg.basis(FM.DOWN)) or D(0)
    except BaseException as exc:                              # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return dict(out, ok=False, reason="ARM_UNREADABLE:%s"
                    % type(exc).__name__)
    out.update(up_qty=str(up), dn_qty=str(dn),
               up_basis=str(bu), dn_basis=str(bd))

    if up == dn:
        return dict(out, ok=False, reason="NO_MODELLED_DEFICIT")
    side = FM.DOWN if up > dn else FM.UP
    out["side"] = side

    bid = _d(snapshot.get("bid"))
    ask = _d(snapshot.get("ask"))
    px = _own_cross_price(side, bid, ask)
    depth = _own_cross_depth(side, snapshot.get("bid_shares"),
                             snapshot.get("ask_shares"))
    out.update(bid=None if bid is None else str(bid),
               ask=None if ask is None else str(ask),
               cross_own_px=None if px is None else str(px),
               displayed_depth=None if depth is None else str(depth))
    if px is None or depth is None:
        return dict(out, ok=False, reason="BOOK_INCOMPLETE")

    # THE CASH BOUND IS THE ARM'S OWN ENVELOPE, not the account's. A modelled
    # arm that was sized by a $3 envelope must not be credited with a
    # completion the account could afford but that arm could not.
    try:
        head = (_d(cfg.envelope) or D(0)) - (_d(cfg.spent()) or D(0))
    except BaseException:                                      # noqa: BLE001
        head = None
    if head is None or head < 0:
        return dict(out, ok=False, reason="ARM_HEADROOM_UNREADABLE")
    out["arm_cash_headroom"] = str(head)

    dec = TSP.completion_decision(
        up_qty=up, dn_qty=dn, up_cost_px=bu, dn_cost_px=bd,
        taker_price=px, available_depth=depth,
        tick=tick, min_qty=min_qty, cash_headroom=head,
        worst_case_ceiling=worst_case_ceiling,
        book_age_s=snapshot.get("stream_age_s"),
        gap_suspected=bool(snapshot.get("gap_suspected")))
    out["decision"] = {k: v for k, v in dec.items() if k != "labels"}

    pol = TCP.authorize(
        decision=dec, enabled=enabled, owner_authorized=owner_authorized,
        held_up_qty=up, held_dn_qty=dn, held_up_px=bu, held_dn_px=bd,
        maker_attempted=maker_attempted,
        maker_rested_s=maker_rested_s,
        window_outlay_spent_usd=window_outlay_spent_usd,
        book_rungs=rungs, halted=halted, **policy_kw)
    out["taker_policy"] = {k: v for k, v in pol.items() if k != "labels"}
    out["ok"] = bool(pol.get("authorized"))
    out["reason"] = pol.get("reason")
    return out


def score_window(*, arm_report: Mapping[str, Any],
                 cross: Optional[Mapping[str, Any]],
                 settle_up: Optional[bool]) -> Dict[str, Any]:
    """Maker-only vs maker-plus-completion, on the VENUE'S resolution.

    `arm_report` is one cell of `TwoSidedShadow.report(settle_up)`, whose
    `net_pnl_modelled` is the maker-only outcome. `cross` is the authorized
    completion this module recorded for that cell, or None if the policy
    never authorized one -- in which case the two outcomes are identical by
    construction, which is the honest result and is reported as such.

    THE COMPLETION IS SCORED AT THE QUANTITY AND VWAP IT WAS AUTHORIZED FOR,
    charged a taker fee, and NOT at the quantity we wished for. A partial
    fill would deliver less; that shortfall is named rather than hidden.
    """
    out: Dict[str, Any] = {
        "settle_up": settle_up,
        "maker_only_net_usd": arm_report.get("net_pnl_modelled"),
        "crossed": bool(cross),
        "basis": ("MODELLED. maker_only is the shadow cell's own figure; the "
                  "completed figure re-prices the same inventory with the "
                  "authorized cross added, both scored on the venue's own "
                  "resolution. Neither is realized."),
    }
    if settle_up is None:
        return dict(out, ok=False, reason="NOT_SETTLED")
    base = _d(arm_report.get("net_pnl_modelled"))
    if base is None:
        return dict(out, ok=False, reason="MAKER_ONLY_NET_UNREADABLE")
    if not cross:
        return dict(out, ok=True, reason="NO_CROSS_AUTHORIZED",
                    completed_net_usd=str(base), delta_usd="0")

    up = _d(arm_report.get("up")) or D(0)
    dn = _d(arm_report.get("dn")) or D(0)
    # the shadow reports cost, not basis, so recover per-share basis from it
    m = _d(arm_report.get("matched_shares")) or D(0)
    mc = _d(arm_report.get("matched_cost")) or D(0)
    rc = _d(arm_report.get("residual_cost")) or D(0)
    side = str(cross.get("side", "")).strip().upper()
    q = _d(cross.get("qty"))
    p = _d(cross.get("price"))
    if q is None or p is None or side not in (FM.UP, FM.DOWN):
        return dict(out, ok=False, reason="CROSS_UNREADABLE")

    nu = up + (q if side == FM.UP else D(0))
    nd = dn + (q if side == FM.DOWN else D(0))
    # total cost after the cross: everything the maker legs cost, plus this
    cost = mc + rc + q * p + TSP.worst_fee(q, p)
    payout = nu if settle_up else nd
    completed = payout - cost
    out.update(ok=True, reason="SCORED",
               cross_side=side, cross_qty=str(q), cross_px=str(p),
               cross_outlay_usd=str(q * p + TSP.worst_fee(q, p)),
               up_after=str(nu), dn_after=str(nd),
               total_cost_usd=str(cost), payout_usd=str(payout),
               completed_net_usd=str(completed),
               delta_usd=str(completed - base),
               matched_shares=arm_report.get("matched_shares"))
    return out


def arm_id_from_cell(cell: Mapping[str, Any]) -> str:
    """`arm_id` for a SCORED report cell rather than a live Config.

    The cell spells the same four fields under different names (`threshold`
    for `thresh`), so the join between what was evaluated during the window
    and what was scored after it needs one translation, in one place.
    """
    return "%s|t%s|e%s|d%s" % (
        cell.get("policy", "?"), cell.get("threshold", "?"),
        cell.get("envelope", "?"), cell.get("depth_ticks", "?"))


def live_arm_cells(report: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The scored cells that mirror the deployed engine."""
    out: List[Dict[str, Any]] = []
    for c in report.get("cells") or ():
        if (str(c.get("policy") or "") == LIVE_SEQUENTIAL
                and _d(c.get("threshold")) == D("0.96")
                and int(c.get("depth_ticks") or 0) == 2):
            out.append(dict(c))
    return out
