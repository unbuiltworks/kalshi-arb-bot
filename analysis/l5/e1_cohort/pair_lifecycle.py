"""THE PRODUCTION PAIR LIFECYCLE. What `run_cohort` was missing entirely.

WHY THIS FILE EXISTS, STATED WITHOUT SOFTENING.

`runner.submit_complement` was written, frozen into the spec's call graph,
authorized through its own `authorize_complement`, given its own latch, and
covered by tests. **Nothing ever called it.** A grep for its call sites
outside tests returned the definition, a string in `spec.py`, and three
comments. So the production loop placed first legs and stopped: no
complement, no `open_residual`, no deadman.

That is why the 1215z window filled twenty first legs and submitted zero
complements. I had told the owner it was because the process was stopped in
time. It was not. The complement path was unreachable from the loop.

It is the same defect as the one this cohort was already repaired for --
`our_outstanding_orders: 0`, a gate wired to a constant -- one level up: a
function wired to nothing. My own "real wiring" test called
`submit_complement` itself, which proved the function works and proved
nothing about whether the engine reaches it.

WHAT THIS MODULE DOES. It is the lifecycle step `run_cohort` runs on every
iteration, before considering any new first leg:

  1. read venue-confirmed cumulative fills for our OWN first-leg orders;
  2. register each filled quantity as RESIDUAL with the tracker, so it
     consumes forward-looking $5 headroom from the instant it exists;
  3. where a complement is authorized and not already outstanding, invoke
     `submit_complement`, sized to the cumulative fill minus what previous
     tranches already covered;
  4. at or below the frozen withdrawal TTE, run the approved deadman and
     verify it against venue state.

IT ADDS NO RULE AND RELAXES NONE. Every price, size, tranche, ceiling and
authorization decision belongs to the functions it calls. This module only
decides WHEN they are called, which is the part that was missing.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional

from analysis.l5.e1_cohort import order_state as OS

#: a complement is attempted only for a first leg whose venue-confirmed
#: cumulative fill exceeds what tranches have already covered by at least
#: the venue minimum. Below that it is explicitly residual, per rule C10.
VENUE_MIN_QTY = D("0.01")


def _fin(x: Any) -> Optional[D]:
    try:
        d = D(str(x))
    except Exception:                                          # noqa: BLE001
        return None
    return d if d.is_finite() else None


class PairLifecycle:
    """Per-first-leg bookkeeping, carried across iterations AND restarts.

    `complemented` is the quantity already covered by submitted tranches,
    keyed by the first leg's venue order id. It is the single defence
    against duplicate complement quantity: every tranche is sized as
    `cumulative_filled - complemented`, so a partial fill followed by a
    further fill produces one tranche each and never two for the same
    shares -- and a restart that reloads it from the tape cannot
    double-cover either.
    """

    def __init__(self, *, legs: Optional[Dict[str, Dict[str, Any]]] = None):
        #: order_id -> {"window", "slot", "complemented", "price"}
        self.legs: Dict[str, Dict[str, Any]] = dict(legs or {})
        self.events: List[Dict[str, Any]] = []
        self.residuals_opened: Dict[str, Dict[str, Any]] = {}
        self.deadman_result: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ tracking
    def record_first_leg(self, *, order_id: str, window: int, slot: int,
                         price: Any) -> None:
        if not order_id:
            return
        self.legs.setdefault(str(order_id), {
            "window": int(window), "slot": int(slot),
            "price": str(price), "complemented": "0"})

    def covered(self, order_id: str) -> D:
        return _fin((self.legs.get(str(order_id)) or {})
                    .get("complemented")) or D("0")

    def _cover(self, order_id: str, qty: Any) -> None:
        leg = self.legs.get(str(order_id))
        if leg is not None:
            leg["complemented"] = str(self.covered(order_id)
                                      + (_fin(qty) or D("0")))

    # ------------------------------------------------------------- the step
    def step(self, *, snapshot: Mapping[str, Any], tracker: Any,
             now: float, submit_complement: Callable[..., Dict[str, Any]],
             transport_factory: Any, session_auth: Any = None,
             recorder: Any = None, progress: Any = None,
             past_cutoff: bool = False) -> Dict[str, Any]:
        """Run the lifecycle once. Returns what it did and why.

        FAILS CLOSED. If order state could not be established, this does
        nothing at all -- it will not submit a complement against an
        inventory figure it cannot trust, and it will not conclude that
        there is no inventory either.

        `past_cutoff` MEANS WITHDRAW-ONLY, AND THE INTEGRATION TEST IS WHY
        THIS PARAMETER EXISTS. Without it the loop did this: the handover
        deadman cancelled an unfilled complement, which cleared the
        one-complement-at-a-time block, so the next iteration submitted a
        BRAND NEW complement for the uncovered shortfall -- past the
        trading cutoff, which `SPEC["timing_explicit"]` calls "REFUSED,
        never a later worse order", and in flat contradiction of rule C9,
        which says an unfilled complement's quantity is RECORDED AS
        RESIDUAL rather than re-placed. Residuals are still registered
        past the cutoff, because inventory does not stop existing; only
        submission stops.
        """
        out: Dict[str, Any] = {"complements": [], "residuals": [],
                               "skipped": None, "deadman": None,
                               "withdraw_only": bool(past_cutoff)}
        view = snapshot.get("order_state")
        if not isinstance(view, Mapping) or not view.get("ok"):
            out["skipped"] = "ORDER_STATE_NOT_ESTABLISHED:%s" % (
                (view or {}).get("reason") if isinstance(view, Mapping)
                else "ABSENT",)
            return out

        # AN OUTSTANDING COMPLEMENT BLOCKS ANOTHER ONE. One at a time, the
        # same discipline as first legs, so tranches cannot stack.
        gate = OS.complement_gate(view)
        n_comp = int(view.get("n_outstanding_complements") or 0)

        by_id = {str(o.get("id")): o for o in (view.get("orders") or [])}
        for oid, leg in sorted(self.legs.items()):
            row = by_id.get(oid)
            if row is None:
                continue
            cum = _fin(row.get("cum_quantity")) or D("0")
            if cum <= 0:
                continue
            px = _fin(row.get("own_side_price")) or _fin(leg.get("price")) \
                or D("0")

            # (2) RESIDUAL FIRST, ALWAYS. The filled quantity consumes
            # forward-looking headroom from the moment it exists, whether
            # or not a complement is about to be attempted. Registering it
            # after a successful complement would leave the dangerous
            # interval -- filled but unhedged -- outside the risk sum.
            key = (leg["window"], leg["slot"])
            prior = self.residuals_opened.get(oid)
            if prior is None or _fin(prior.get("filled_qty")) != cum:
                rec = tracker.open_residual(
                    window=leg["window"], slot=leg["slot"],
                    filled_qty=str(cum), fill_price_usd=str(px))
                self.residuals_opened[oid] = rec
                out["residuals"].append({"order_id": oid, **rec})
                if progress is not None:
                    try:
                        progress.record_residual_opened(
                            window_index=leg["window"], slot=leg["slot"],
                            filled_qty=str(cum))
                    except Exception:                          # noqa: BLE001
                        pass
                if recorder is not None:
                    recorder.record_event(
                        window=leg["window"], slot=leg["slot"],
                        event="FIRST_LEG_FILL_RESIDUAL_OPENED",
                        detail={"order_id": oid, "cumulative_filled": str(cum),
                                "own_side_price": str(px),
                                "max_remaining_loss_usd":
                                    rec["max_remaining_loss_usd"],
                                "counts_against_the_5_usd_ceiling": True})

            # (3) THE COMPLEMENT, sized to cumulative-minus-covered.
            shortfall = cum - self.covered(oid)
            if shortfall < VENUE_MIN_QTY:
                continue
            if past_cutoff:
                # RULE C9. The shortfall is residual, not a later order.
                out["complements"].append({
                    "order_id": oid, "submitted": False,
                    "because": "PAST_HANDOVER_CUTOFF_WITHDRAW_ONLY",
                    "uncovered_qty": str(shortfall),
                    "recorded_as_residual_not_re_placed": True})
                if recorder is not None:
                    recorder.record_event(
                        window=leg["window"], slot=leg["slot"],
                        event="COMPLEMENT_REFUSED_PAST_CUTOFF",
                        detail={"first_leg_order_id": oid,
                                "uncovered_qty": str(shortfall),
                                "rule": "C9 + timing_explicit."
                                        "placement_after_cutoff",
                                "re_priced": False})
                continue
            if n_comp:
                out["complements"].append({
                    "order_id": oid, "submitted": False,
                    "because": "COMPLEMENT_ALREADY_OUTSTANDING:%d" % n_comp})
                continue
            if not gate.get("may_submit_complement"):
                out["complements"].append({
                    "order_id": oid, "submitted": False,
                    "because": gate.get("because")})
                continue
            try:
                res = submit_complement(
                    window=leg["window"], slot=leg["slot"],
                    snapshot=snapshot,
                    first_leg_filled_qty=str(cum),
                    already_complemented_qty=str(self.covered(oid)),
                    tracker=tracker,
                    transport_factory=transport_factory,
                    session_auth=session_auth)
            except Exception as exc:                            # noqa: BLE001
                # NEVER RETRIED HERE. An ambiguous or refused complement is
                # recorded and the shortfall stays uncovered, which keeps it
                # visible as residual rather than silently retried into a
                # second order.
                out["complements"].append({
                    "order_id": oid, "submitted": False,
                    "raised": type(exc).__name__})
                if recorder is not None:
                    recorder.record_event(
                        window=leg["window"], slot=leg["slot"],
                        event="COMPLEMENT_SUBMISSION_FAILED",
                        detail={"order_id": oid,
                                "error": type(exc).__name__,
                                "never_retried": True})
                continue
            if res.get("submitted"):
                q = res.get("e1_complement_qty")
                self._cover(oid, q)
                n_comp += 1          # one at a time, within this step too
                out["complements"].append({
                    "order_id": oid, "submitted": True,
                    "qty": q,
                    "complement_order_id": res.get(
                        "e1_complement_order_id")})
                if recorder is not None:
                    recorder.record_event(
                        window=leg["window"], slot=leg["slot"],
                        event="COMPLEMENT_SUBMITTED",
                        detail={"order_id": res.get(
                                    "e1_complement_order_id"),
                                "first_leg_order_id": oid,
                                "qty": q,
                                "sized_to_cumulative_filled_qty": True})
            else:
                out["complements"].append({
                    "order_id": oid, "submitted": False,
                    "residual_qty": res.get("residual_qty"),
                    "residual_reason": res.get("residual_reason")})
                if recorder is not None:
                    recorder.record_event(
                        window=leg["window"], slot=leg["slot"],
                        event="COMPLEMENT_REFUSED_RESIDUAL_RECORDED",
                        detail={"first_leg_order_id": oid,
                                "residual_qty": res.get("residual_qty"),
                                "reason": res.get("residual_reason"),
                                "ceiling_relaxed": False})
        return out

    # ------------------------------------------------------------- deadman
    def deadman(self, *, snapshot: Mapping[str, Any], now: float,
                transport_factory: Any, orders_module: Any,
                owned_ids: Iterable[str] = (),
                recorder: Any = None,
                read_open: Optional[Callable[[], Any]] = None,
                read_order: Optional[Callable[[str], Any]] = None
                ) -> Dict[str, Any]:
        """RULES C6 AND C9, AND `open_at_boundary` FROM THE FROZEN SPEC:
        "CANCELLED, never left to ride to settlement".

        CANCELLED BY OWNED ORDER ID, NOT BY MARKET.
        ===========================================
        The first version used `deadman_cancel_all`, which POSTs
        `{"slugs": [slug]}` and therefore cancels EVERY open order in that
        market. On a shared PMUS account -- and this account is shared:
        1,100 activity rows, 901 trades, other desks at sizes like 16.21
        and 23.49 -- that would cancel another strategy's orders whenever
        it happened to be quoting the same BTC15m window. Market scope is
        not ownership scope, and the two only coincide by luck. (The
        twenty 1230z orders I cancelled that way earlier were all E1's, so
        nothing was harmed -- but that was luck, not design.)

        The venue supports `POST /v1/order/{id}/cancel`, which
        `orders.cancel_exact` wraps and `BoundedOrderTransport` permits as
        one of its four routes with no once-per-process bound. So the
        handover cancels EXACTLY the ids E1 submitted, taken from its own
        registry, and verifies each one individually.

        AN ID WE DO NOT OWN IS NEVER CANCELLED. If an unattributable order
        is resting in our market it is reported and left alone; E3 already
        counts it as outstanding, so it blocks our placements rather than
        being swept away. Membership in our registry is the ownership
        test, deliberately -- NOT the role field, which defaults an
        unknown id to `first_leg` and would otherwise claim a stranger's
        order as ours.

        A FILLED RESIDUAL IS NOT CANCELLED AND NOT UNWOUND. There is no
        taker rescue at any price and at any TTE, so inventory rides to
        settlement and its actual cashflow is reconciled afterwards. That
        is the authorized behaviour, not a gap.
        """
        slug = snapshot.get("market")
        owned = {str(i) for i in owned_ids if i}
        out: Dict[str, Any] = {
            "ran": False, "slug": slug,
            "CANCELS_BY_OWNED_ORDER_ID_NOT_BY_MARKET": True,
            "NOT_A_TAKER_RESCUE": True,
            "filled_residual_rides_to_settlement": True,
            "an_unowned_order_is_never_cancelled": True,
        }
        if not slug:
            out["because"] = "NO_SLUG"
            return out

        view = snapshot.get("order_state")
        if not isinstance(view, Mapping) or not view.get("ok"):
            # FAIL CLOSED. We will not fire a cancel sweep against a state
            # we cannot read, because we could not then tell our orders
            # from anyone else's.
            out["because"] = "ORDER_STATE_NOT_ESTABLISHED"
            out["FAILS_CLOSED"] = True
            self.deadman_result = out
            if recorder is not None:
                recorder.record_event(
                    window=int(snapshot.get("window", -1)), slot=-1,
                    event="HANDOVER_DEADMAN_REFUSED", detail=dict(out))
            return out

        outstanding = {str(i) for i in (view.get("outstanding_ids") or [])}
        targets = sorted(outstanding & owned)
        foreign = sorted(outstanding - owned)
        out["targets"] = targets
        out["foreign_open_ids_left_untouched"] = foreign
        out["ran"] = True

        cancels: List[Dict[str, Any]] = []
        for oid in targets:
            t = transport_factory()
            try:
                resp = orders_module.cancel_exact(t, order_id=oid,
                                                  slug=slug)
                cancels.append({"order_id": oid, "ok": True,
                                "response": resp})
            except Exception as exc:                            # noqa: BLE001
                # NEVER RETRIED. Recorded, and the verification below is
                # what decides whether it actually went.
                cancels.append({"order_id": oid, "ok": False,
                                "error": type(exc).__name__})
        out["cancels"] = cancels

        # VERIFY. Never trust a cancel's own answer.
        if read_order is not None:
            states: Dict[str, Any] = {}
            for oid in targets:
                try:
                    body = read_order(oid)
                    row = (body.get("order")
                           if isinstance(body, Mapping) else None) or body
                    states[oid] = (str(row.get("state"))
                                   if isinstance(row, Mapping) else None)
                except Exception as exc:                        # noqa: BLE001
                    states[oid] = "UNREADABLE:%s" % type(exc).__name__
            out["per_id_states_after"] = states
            out["all_cancelled"] = bool(targets) and all(
                str(v) in OS.TERMINAL_STATES for v in states.values())
            if not targets:
                out["all_cancelled"] = True
            out["VERIFIED_AGAINST"] = "GET /v1/order/{id} per owned id"
        if read_open is not None:
            try:
                body = read_open()
                rows = OS.rows(body, "orders", "data")
                still = [str(r.get("id")) for r in (rows or [])
                         if OS.slug_of(r) == str(slug)]
                out["open_after_in_our_market"] = still
                out["owned_still_open"] = sorted(set(still) & owned)
                # the foreign orders MUST still be there: if they vanished,
                # our cancel was not ownership-scoped after all.
                out["foreign_still_open"] = sorted(set(still) & set(foreign))
                out["foreign_untouched"] = (
                    sorted(set(still) & set(foreign)) == foreign)
                if out.get("all_cancelled") is None:
                    out["all_cancelled"] = not out["owned_still_open"]
                    out["VERIFIED_AGAINST"] = "GET /v1/orders/open"
            except Exception as exc:                            # noqa: BLE001
                out["verify_error"] = type(exc).__name__
                out.setdefault("all_cancelled", None)
        self.deadman_result = out
        if recorder is not None:
            recorder.record_event(
                window=int(snapshot.get("window", -1)), slot=-1,
                event="HANDOVER_DEADMAN",
                detail={k: v for k, v in out.items() if k != "cancels"})
        return out
