"""PMUS q=1 HOLD_OPEN_EXTEND canary — DISARMED BY DEFAULT.

WHAT THIS IS. The first bounded live probe of the frozen `HOLD_OPEN_EXTEND`
behaviour, on BTC 15m, q=1, one market, one campaign per invocation. It
COMPOSES the reviewed lifecycle primitives in `pmus_q1_executor` (preview
first, post-only maker, fresh-BBO cross guard, durable intent before
placement, authoritative fill polling, exact-ID cancel with verification,
market-scoped Cancel-All deadman on every exit path, strict accounting
labels) and adds only the behaviour under test. No primitive is modified,
so the reviewed paths stay byte-identical for the existing campaign.

WHAT IT IS NOT. It is not evidence that holding is profitable. The arm's
only prior evidence is a 5.01-minute partial slice measuring MECHANICS
(requotes avoided, fills, inventory formed); its settled economics have
never been computed. The success criterion here is a clean lifecycle and
genuinely completed paired inventory -- not $/window.

ARMING is triple-key and fail-closed. The executor's own dual key is not
sufficient: this behaviour is separately authorized, so it needs its own.
    PMUS_Q1_LIVE=YES
    PMUS_Q1_SESSION_ID=<fresh id>   PMUS_Q1_ARM=<same id>
    PMUS_CANARY_ARM=<the same id>            <-- canary-specific
    PMUS_Q1_MARKET_SLUG=<market>    PMUS_Q1_GATE_EVIDENCE=<same slug>
Any absence or mismatch -> DISARMED before a single network call.

BEHAVIOUR UNDER TEST, in the order the mandate states it:

1. Rest an economically valid maker OPEN/EXTEND.
2. Do NOT chase the touch merely because it moved. Keep the resting order,
   and with it the queue position, for as long as it stays valid.
3. On a fill, the objective switches immediately to reducing unmatched
   inventory. No new opening leg is considered while residual exists.
4. Keep the complement working continuously until matched or the deadline.
5. A partial complement fill reduces only the REMAINING quantity.
6. If a contemporaneous taker completion gives an acceptable fee-adjusted
   pair cost, complete immediately rather than waiting for a maker fill.
7. Never add to the long side unless the aggregate-basis rule permits it
   INSIDE the residual cap. At q=1 with RESIDUAL_CAP=1 a second long leg
   would put residual at 2 > cap, so an EXTEND is structurally impossible
   here; `extend_permitted` says so explicitly rather than leaving it to
   an accident of control flow.
8. Unconditional cancel/sweep on every exit path.

Every decision, submission, ACK, fill, cancel, inventory change, pair cost,
fee, rebate credit and cashflow is appended to the durable ledger. Realized
P&L stays null until the settlement audit reconciles it; this process never
guesses it.

Disarmed dry-run (safe anywhere, no network):
    python3 -m src.pmus_q1_canary
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_q1_executor as X
from src import pmus_ws_live_state as WS

CANARY_ARM_ENV = "PMUS_CANARY_ARM"
HOLD_DEADLINE_ENV = "PMUS_CANARY_DEADLINE_SECONDS"      # default 600
#: worst case for one unpaired long contract is its own cost, and `entry_ok`
#: already caps that at $0.80. This is the smallest practical loss cap for a
#: probe that actually trades: one contract, one market, one campaign.
CANARY_MAX_LOSS = D("0.80")


def canary_armed() -> bool:
    """The executor's dual key AND the canary-specific third key."""
    sid = os.getenv(X.SID_ENV, "").strip()
    return (X.armed() and bool(sid)
            and os.getenv(CANARY_ARM_ENV, "").strip() == sid)


def _ws_fields(snap: Mapping[str, Any]) -> dict:
    """The WS evidence every decision row must carry, per the mandate."""
    return {"ws_recv_t": snap.get("ws_recv_t"),
            "book_age_s": round(float(snap.get("book_age_s") or 0), 4),
            "bid": str(snap.get("bid")), "ask": str(snap.get("ask")),
            "bid_qty": str(snap.get("bid_qty")),
            "ask_qty": str(snap.get("ask_qty")),
            "depth": snap.get("depth"),
            "last_trade_px": str(snap.get("last_trade_px")),
            "last_trade_qty": str(snap.get("last_trade_qty")),
            "last_trade_venue_t": snap.get("last_trade_venue_t"),
            "last_trade_age_s": snap.get("last_trade_age_s"),
            "book_updates": snap.get("book_updates"),
            "feed": snap.get("source")}


def record(kind: str, **fields: Any) -> None:
    """One ledger row per decision. Recording is part of the mandate, so a
    failure to record is not swallowed silently -- it is re-raised as a Halt
    so the campaign exits through the sweep."""
    try:
        X.ledger_append({"kind": kind, "campaign": "pmus-q1-canary", **fields})
    except Exception as exc:                                 # noqa: BLE001
        raise X.Halt(f"LEDGER_WRITE_FAILED:{kind}:{str(exc)[:80]}") from exc


# ----------------------------------------------------------------- inventory

class Inventory:
    """Authoritative inventory: only venue-confirmed fills move it."""

    def __init__(self) -> None:
        self.qty = {"yes": D(0), "no": D(0)}
        self.cost = {"yes": D(0), "no": D(0)}

    def add(self, side: str, qty: D, price: D) -> None:
        self.qty[side] += D(str(qty))
        self.cost[side] += D(str(qty)) * D(str(price))

    def residual(self) -> D:
        """Positive means long YES, negative long NO; 0 means fully paired."""
        return self.qty["yes"] - self.qty["no"]

    def deficient(self) -> Optional[str]:
        """The side that would REDUCE residual, or None when flat/paired."""
        r = self.residual()
        if r > 0:
            return "no"
        if r < 0:
            return "yes"
        return None

    def basis(self, side: str) -> D:
        """Average acquisition cost of the side we hold."""
        q = self.qty[side]
        return (self.cost[side] / q) if q > 0 else D(0)

    def matched(self) -> D:
        return min(self.qty["yes"], self.qty["no"])


def extend_permitted(inv: Inventory, *, residual_cap: int = X.RESIDUAL_CAP
                     ) -> bool:
    """Rule 7. An EXTEND adds to the side we are ALREADY long.

    Two independent conditions, both required:
      * the resulting residual must stay inside the cap; and
      * the new lot must lower the aggregate basis of that side -- which at
        q=1 cannot even be evaluated, because there is no second lot.
    At RESIDUAL_CAP = 1 the first condition already fails, so this returns
    False for every reachable state of this canary. It is written out so the
    prohibition is a stated rule rather than a side effect.
    """
    r = abs(inv.residual())
    return (r + 1) <= residual_cap


# -------------------------------------------------------- validity of a rest

def open_still_valid(px: D, snap: Mapping[str, Any]) -> bool:
    """Rule 2. May a resting OPEN keep its price, and its queue position?

    A better touch is explicitly NOT a reason to move. The order is given up
    only when it stops being a valid opening leg: its price leaves the
    entry band, or it would now cross the live opposite quote (a post-only
    rejection waiting to happen).
    """
    if not X.entry_ok(D(str(px))):
        return False
    return not X.would_cross("OUTCOME_SIDE_YES", D(str(px)), snap)


def complement_still_valid(px: D, first_cost: D, tick: Optional[D],
                           snap: Mapping[str, Any]) -> bool:
    """Rule 4. A resting complement keeps its place while it still pairs.

    The bound is recomputed from the CURRENT basis every poll. If the bound
    has moved BELOW our resting price the order no longer locks the required
    edge and must go; if the bound has moved above it, our price is still
    fee-safe and we keep our queue position rather than improving it.
    """
    bound = X.complement_bound(D(str(first_cost)), tick=tick)
    if bound is None or D(str(px)) > bound:
        return False
    return not X.would_cross("OUTCOME_SIDE_NO", D(str(px)), snap)


def place_taker_buy(t: X.Transport, slug: str, outcome_side: str, price: D,
                    stage: str, sleep=time.sleep) -> str:
    """DEFECT 2 FIX: a REAL crossing completion, not a counter increment.

    Same reviewed shape as `X.place_limit_buy` -- preview first, durable
    intent on disk BEFORE the POST, q=1, the venue's long-side wire price
    convention -- with exactly two deliberate differences:

    * `participateDontInitiate` is FALSE. A post-only order cannot cross,
      and crossing is the entire purpose of this leg.
    * there is no `would_cross` refusal. That guard exists to stop a MAKER
      being rejected for crossing; here crossing is intended, so refusing on
      it would make the branch unreachable.

    Marketable LIMIT, never a market order: the price is the cap that the
    fee-adjusted pair-cost check just approved, so a moving book can leave
    this unfilled but can never fill it worse than the approved bound.
    """
    wire_px = (X.ONE - D(str(price))) if str(outcome_side).upper().endswith("NO") \
        else D(str(price))
    body = {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(wire_px)}, "quantity": X.QTY,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": False,      # crossing leg
            "outcomeSide": outcome_side,
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}
    preview = t.request("POST", "/v1/order/preview", body)
    if preview.get("error") or preview.get("warnings"):
        raise X.Halt(f"PREVIEW_REJECTED:{stage}")
    X.persist_intent({"stage": stage, "slug": slug, "side": outcome_side,
                      "price": str(price), "qty": X.QTY, "taker": True,
                      "submitted_utc": datetime.now(timezone.utc).isoformat()})
    resp = t.request("POST", "/v1/orders", body)
    order_id = str(resp.get("id") or resp.get("orderId")
                   or (resp.get("order") or {}).get("id") or "")
    if not order_id:
        raise X.Halt(f"NO_ORDER_ID:{stage}")
    X.persist_intent({"stage": stage + ":accepted", "order_id": order_id})
    record("submitted", stage=stage, slug=slug, side=outcome_side,
           price=str(price), order_id=order_id, maker=False)
    return order_id


# -------------------------------------------------------------- the campaign

def _tick(t: X.Transport, slug: str, sleep) -> Optional[D]:
    """The venue's own price grid for this market, or None."""
    try:
        md = X._get_with_retry(t, f"/v1/markets/{slug}", base=X.GATEWAY,
                               sleep=sleep)
    except Exception:                                        # noqa: BLE001
        return None
    market = md.get("market") or md.get("marketData") or md
    return X.tick_for_market(market)


def _poll_fill(t: X.Transport, order_id: str, sleep) -> dict:
    """One non-blocking look at an order: fill, terminal, or still working."""
    order = X._order_status(t, order_id, sleep=sleep)
    if X.terminal_rejection(order):
        return {"terminal": True}
    fill = X.authoritative_fill(order)
    return {"fill": fill} if fill is not None else {"working": True}


def run_canary(t: Optional[X.Transport] = None, clock=time.monotonic,
               sleep=time.sleep, state: Optional["WS.LiveState"] = None,
               feed: Optional["WS.WsFeed"] = None,
               max_book_age_s: float = WS.DEFAULT_MAX_BOOK_AGE_S) -> dict:
    slug = os.getenv(X.MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "campaign": "pmus-q1-canary", "arm": "HOLD_OPEN_EXTEND",
        "slug": slug, "qty": X.QTY, "realized_pnl": None,
        "max_loss_cap": str(CANARY_MAX_LOSS),
        "labels": ("submitted->filled->settled->realized; fees and rebates "
                   "reported separately; realized null until the settlement "
                   "audit reconciles"),
    }
    if not canary_armed():
        return {**report, "status": "DISARMED",
                "note": f"triple key absent/mismatched ({CANARY_ARM_ENV} must "
                        f"equal {X.SID_ENV}); no network activity"}
    if not X.has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not X.gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}
    if X.realized_today() <= X.DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}

    t = t or X.Transport()
    deadline_s = float(os.getenv(HOLD_DEADLINE_ENV, "600"))
    stop_at = clock() + deadline_s
    inv = Inventory()
    #: what is resting right now: at most ONE order, per the mandate
    resting: Optional[dict] = None
    holds = requotes = cancels = takes = 0
    first_cost: Optional[D] = None

    try:
        reason = X.preflight(t, slug, sleep=sleep)
        if reason:
            return {**report, "status": "PREFLIGHT_FAILED", "reason": reason}
        tick = _tick(t, slug, sleep)
        # DEFECT 1: strategy decisions come from the authenticated WS only.
        if state is None:
            feed = feed or WS.WsFeed(slug).start()
            state = feed.state
        ready = WS.wait_ready(state, slug, max_age_s=max_book_age_s,
                              sleep=sleep)
        record("ws_ready", **{k: str(v) for k, v in ready.items()})
        record("canary_start", slug=slug, tick=str(tick),
               deadline_s=deadline_s, max_loss=str(CANARY_MAX_LOSS),
               decision_feed="AUTHENTICATED_WS",
               rest_role="pre-submit cross check, order status, account only")

        while clock() < stop_at:
            # A stale feed REFUSES. There is deliberately no REST fallback
            # for a strategy decision: a cached book is not market state.
            snap = state.snapshot(slug, max_age_s=max_book_age_s)
            want_side = inv.deficient()

            # ---------------------------------------- 1 look at what rests
            if resting is not None:
                got = _poll_fill(t, resting["order_id"], sleep)
                if got.get("fill") is not None:
                    fill = got["fill"]
                    px, fq = D(str(fill["price"])), D(str(fill.get("qty") or 1))
                    inv.add(resting["inv_side"], fq, px)
                    record("filled", stage=resting["stage"], price=str(px),
                           qty=str(fq), side=resting["inv_side"],
                           maker_rebate_separate=str(X.maker_rebate(px)),
                           residual=str(inv.residual()),
                           matched=str(inv.matched()))
                    # rule 5: a partial leaves the REMAINDER working
                    remaining = D(str(resting["qty"])) - fq
                    if remaining > 0:
                        resting["qty"] = remaining
                        record("partial_remainder_kept",
                               stage=resting["stage"], remaining=str(remaining))
                    else:
                        resting = None
                    if resting is None and inv.qty["yes"] > 0 and \
                            first_cost is None:
                        first_cost = inv.basis("yes")
                    continue
                if got.get("terminal"):
                    record("terminal_rejection", stage=resting["stage"],
                           order_id=resting["order_id"])
                    resting = None
                    continue

                # ------------------------------ 2/4 hold, or give it up
                still = (open_still_valid(D(str(resting["px"])), snap)
                         if resting["stage"] == "open"
                         else complement_still_valid(
                             D(str(resting["px"])), first_cost, tick, snap))
                if still:
                    holds += 1
                    record("hold", stage=resting["stage"],
                           px=str(resting["px"]),
                           queue_depth_at_our_price=str(state.depth_at(
                               slug,
                               "bids" if resting["inv_side"] == "yes"
                               else "asks", D(str(resting["px"])))),
                           residual=str(inv.residual()),
                           note="valid; queue position preserved; a better "
                                "touch is not a reason to move",
                           **_ws_fields(snap))
                else:
                    ok = X.cancel_exact(t, resting["order_id"], slug,
                                        sleep=sleep)
                    cancels += 1
                    record("cancel", stage=resting["stage"],
                           px=str(resting["px"]), verified=ok,
                           reason="no_longer_economically_valid",
                           residual=str(inv.residual()), **_ws_fields(snap))
                    # a fill can land during the cancel race
                    late = X.authoritative_fill(
                        X._order_status(t, resting["order_id"], sleep=sleep))
                    if late is not None:
                        px = D(str(late["price"]))
                        inv.add(resting["inv_side"],
                                D(str(late.get("qty") or 1)), px)
                        record("filled", stage=resting["stage"] + "_late",
                               price=str(px), residual=str(inv.residual()))
                        if inv.qty["yes"] > 0 and first_cost is None:
                            first_cost = inv.basis("yes")
                    resting = None
                    requotes += 1
                    continue

            # ------------------------------------------- 6 reactive take
            if want_side is not None and first_cost is not None:
                # 1. contemporaneous EXECUTABLE price, from WS state
                ask = (snap.get("ask") if want_side == "yes"
                       else ((X.ONE - D(str(snap["bid"])))
                             if snap.get("bid") is not None else None))
                # 2. fee-adjusted pair-cost check
                if ask is not None and X.taker_completion_ok(first_cost,
                                                             D(str(ask))):
                    record("decision", action="taker_complete",
                           ask_cost=str(ask), first_cost=str(first_cost),
                           residual=str(inv.residual()),
                           reason="fee_adjusted_pair_cost_acceptable_now",
                           **_ws_fields(snap))
                    # 3. cancel the maker rest and resolve it AUTHORITATIVELY
                    #    before crossing, so the two legs cannot both fill.
                    if resting is not None:
                        ok = X.cancel_exact(t, resting["order_id"], slug,
                                           sleep=sleep)
                        settled = X.authoritative_fill(X._order_status(
                            t, resting["order_id"], sleep=sleep))
                        record("cancel", stage=resting["stage"], verified=ok,
                               reason="pre_take_double_fill_guard",
                               late_fill=(str(settled["price"])
                                          if settled else None))
                        if settled is not None:
                            # the maker filled during the race: that IS the
                            # completion. Book it and do NOT also cross.
                            px = D(str(settled["price"]))
                            inv.add(resting["inv_side"],
                                    D(str(settled.get("qty") or 1)), px)
                            record("filled",
                                   stage=resting["stage"] + "_race",
                                   price=str(px), maker=True,
                                   residual=str(inv.residual()),
                                   matched=str(inv.matched()))
                            resting = None
                            record("decision", action="take_aborted",
                                   reason="maker_filled_during_cancel_race")
                            continue
                        resting = None
                    # 4. durable intent then POST, inside place_taker_buy
                    side_wire = ("OUTCOME_SIDE_YES" if want_side == "yes"
                                 else "OUTCOME_SIDE_NO")
                    try:
                        tid = place_taker_buy(t, slug, side_wire,
                                              D(str(ask)), "taker_complete",
                                              sleep=sleep)
                    except X.Halt:
                        raise
                    takes += 1
                    # resolve THAT order authoritatively; only confirmed
                    # quantity moves inventory
                    got = X.authoritative_fill(
                        X._order_status(t, tid, sleep=sleep))
                    if got is None:
                        ok = X.cancel_exact(t, tid, slug, sleep=sleep)
                        got = X.authoritative_fill(
                            X._order_status(t, tid, sleep=sleep))
                        record("taker_unfilled", order_id=tid,
                               cancel_verified=ok,
                               note="no confirmed fill; inventory unchanged")
                    if got is not None:
                        px = D(str(got["price"]))
                        fq = D(str(got.get("qty") or 1))
                        inv.add(want_side, fq, px)
                        record("filled", stage="taker_complete", maker=False,
                               price=str(px), qty=str(fq),
                               taker_fee_separate=str(X.taker_fee(px)),
                               residual=str(inv.residual()),
                               matched=str(inv.matched()))
                    break

            # --------------------------------------- place what is missing
            if resting is None:
                if want_side is None:
                    # flat and paired. An OPEN is allowed; an EXTEND is not.
                    if inv.matched() > 0:
                        record("decision", action="stop",
                               reason="paired_inventory_complete")
                        break
                    bid = snap.get("bid")
                    if bid is None or not X.entry_ok(D(str(bid))):
                        record("decision", action="wait",
                               reason="entry_out_of_band",
                               **_ws_fields(snap))
                        sleep(X.POLL_S)
                        continue
                    stage, side, px = "open", "yes", D(str(bid))
                else:
                    if want_side == "yes":
                        record("decision", action="stop",
                               reason="long_NO_residual_unsupported_at_q1")
                        break
                    bound = X.complement_bound(first_cost, tick=tick)
                    if bound is None:
                        record("decision", action="hold_one_sided",
                               reason="no_fee_safe_complement",
                               first_cost=str(first_cost))
                        sleep(X.POLL_S)
                        continue
                    stage, side, px = "complement", "no", bound
                try:
                    oid = X.place_limit_buy(
                        t, slug,
                        "OUTCOME_SIDE_YES" if side == "yes"
                        else "OUTCOME_SIDE_NO",
                        px, stage, sleep=sleep)
                except X.StaleRefuse as stale:
                    record("stale_refused", stage=stage, reason=str(stale))
                    sleep(X.POLL_S)
                    continue
                resting = {"order_id": oid, "stage": stage, "px": px,
                           "inv_side": side, "qty": D(X.QTY)}
                record("resting", stage=stage, px=str(px), order_id=oid,
                       residual=str(inv.residual()), **_ws_fields(snap))
            sleep(X.POLL_S)

        matched = inv.matched()
        pair_cost = ((inv.cost["yes"] + inv.cost["no"]) / matched
                     if matched > 0 else None)
        status = ("MATCHED" if matched > 0 else
                  "ONE_SIDED_HOLD" if inv.residual() != 0 else "NO_FILL")
        out = {**report, "status": status,
               "matched_contracts": str(matched),
               "residual": str(inv.residual()),
               "pair_cost": (str(pair_cost) if pair_cost is not None else None),
               "first_cost": (str(first_cost) if first_cost is not None
                              else None),
               "holds": holds, "requotes": requotes, "cancels": cancels,
               "takes": takes,
               "rebates_separate": str(
                   sum((X.maker_rebate(inv.basis(s)) * inv.qty[s]
                        for s in ("yes", "no") if inv.qty[s] > 0), D(0))),
               "settlement": "PENDING — realized only after settlement rows "
                             "reconcile"}
        record("canary_end", **{k: v for k, v in out.items()
                                if k not in ("labels",)})
        return out
    except WS.StaleFeed as stale:
        record("ws_stale_refused", reason=str(stale),
               note="no REST fallback for a strategy decision")
        return {**report, "status": "WS_STALE_REFUSED", "reason": str(stale)}
    except X.StaleRefuse as stale:
        record("stale_refused", reason=str(stale))
        return {**report, "status": "STALE_PRICE_REFUSED", "reason": str(stale)}
    except X.Halt as halt:
        record("halt", reason=str(halt))
        return {**report, "status": "HALT", "reason": str(halt)}
    finally:
        # rule 8: unconditional, on EVERY exit path including Halt and
        # KeyboardInterrupt. Market-scoped so no unrelated order is touched.
        X.cancel_all_deadman(t, slug)
        if feed is not None:
            feed.close()
        record("sweep", note="market-scoped cancel-all on exit")


def main() -> int:
    out = run_canary()
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
