"""The bounded BTC15m CONTINUOUS-INVENTORY live probe: ONE venue-order path.

WHAT IT VALIDATES: that OPEN -> EXTEND -> COMPLEMENT -> LOCK executes
correctly against the real BTC15m venue, with actual fills and AUTHORITATIVE
inventory reconciliation after every fill.

WHAT IT DOES NOT ANSWER: whether EXTEND is profitable. Three windows of q=1
cannot distinguish any edge from zero. The prospective EXTEND-vs-NO-EXTEND
shadow answers that, runs separately, and is never pooled with these rows.

NO SECOND POLICY. Every decision is `ci15m_policy.POLICY["decide_once"]`,
which IS the frozen function in
`analysis/gemini/info_edge/btc15m_shadow_policy.py` that the shadow calls.
Nothing in this file decides a side, a price, a size or a time.

NO SECOND SIZE. `QUANTITY` is imported from `ci15m_policy` and is the only
quantity this module can name. It is passed to `decide_once`, re-asserted in
the budget, re-asserted here, and asserted again against the built payload
before anything is sent. There is no multiplier, no progression, no
martingale and no sizing function anywhere on the path.

ONE ORDER AT A TIME, ESTABLISHED FROM THE VENUE. Not from our own belief:
`active_orders` is read, and a non-empty answer refuses the next submission.
An UNREADABLE answer also refuses, because an account whose orders cannot be
read is not an account with none.

FAIL CLOSED EVERYWHERE. Every anomaly writes a durable HALT and stops: the
HALT conditions are enumerated in `ci15m_budget.HALT_CONDITIONS` and none of
them is a warning.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import (arming, ci15m_budget as BUD, cohort,
                                  payload, position_ledger, spec, transport)
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY, fingerprint
from src.gemini_execution.continuous_probe import (COMPLETE, EXTEND, HOLD,
                                                   OPEN)

LABEL = "GEMINI_CI15M_PROBE_V1"

#: the authorization these bounds were granted against. It must match
#: `arming.EXPECTED_AUTH_ID` before anything is constructed.
PROBE_AUTH_ID = "OWNER-2026-10-03-GEMINI-CI15M-1"

#: the policy bytes the authorization was granted against. Set by the human
#: who arms the probe; None means "whatever is on disk", which is refused for
#: a live run.
AUTHORIZED_POLICY_FINGERPRINT: Optional[str] = None

#: the ONE contract family. A symbol outside it is refused by name.
SYMBOL_PREFIX = "GEMI-BTC15M"
WINDOW_SECONDS = 900.0

#: Up is YES, Down is NO. The outcome strings are TAKEN FROM `spec`, not
#: spelled out here: the REST dialect is lower-case "yes"/"no" and the WS
#: dialect is upper-case, and writing either literal by hand is how a
#: side-mapping bug gets in. (It did, in the first draft of this file, and
#: the payload validator refused it before any venue saw it.)
SIDE_TO_OUTCOME = {"UP": spec.OUTCOME_YES, "DOWN": spec.OUTCOME_NO}
OUTCOME_TO_SIDE = {str(v).lower(): k for k, v in SIDE_TO_OUTCOME.items()}


class ProbeRefused(RuntimeError):
    """A gate said no before the venue was touched."""


# ----------------------------------------------------------------- shapes
class LegView:
    """A shape bridge, not a policy. `position_ledger.reconcile` reads
    yes_qty/no_qty/matched(); `fi_core.Inventory` names them q_up/q_down.
    Translating between two spellings of the same two numbers is all this
    does, and it holds no state of its own."""

    def __init__(self, inv: Any) -> None:
        self._inv = inv

    @property
    def yes_qty(self) -> D:
        return self._inv.q_up

    @property
    def no_qty(self) -> D:
        return self._inv.q_down

    def matched(self) -> D:
        return self._inv.matched


def new_inventory() -> Any:
    return POLICY["Inventory"]()


# ------------------------------------------------------------- selectors
def ticker_of(symbol: str) -> str:
    """BTC15M2610031530 from GEMI-BTC15M2610031530-UP."""
    parts = symbol.split("-")
    return parts[1] if len(parts) >= 2 else symbol


def eligible(market: Mapping[str, Any], now: float) -> Dict[str, Any]:
    """Is this one market selectable? Every condition stated and answered.

    STRICTLY ONE FAMILY, a PUBLISHED and AVAILABLE strike, and a TTE inside
    the frozen policy's own OPEN band -- not a band invented here.
    """
    sym = str(market.get("symbol") or "")
    why: List[str] = []
    if not sym.startswith(SYMBOL_PREFIX):
        why.append("symbol %r is not in the %s family" % (sym, SYMBOL_PREFIX))
    strike = (market.get("strike") or {})
    if strike.get("value") in (None, ""):
        why.append("no published strike value")
    av = strike.get("availableAt")
    if av in (None, ""):
        why.append("strike.availableAt is absent")
    elif float(av) > now:
        why.append("strike.availableAt %s has not passed" % av)
    exp = market.get("expiry_ts")
    tte = None if exp in (None, "") else float(exp) - now
    if tte is None:
        why.append("no readable expiry")
    elif not (POLICY["OPEN_TTE_LO"] <= tte <= POLICY["OPEN_TTE_HI"]):
        why.append("tte %.1f is outside the frozen OPEN band [%s, %s]"
                   % (tte, POLICY["OPEN_TTE_LO"], POLICY["OPEN_TTE_HI"]))
    return {"symbol": sym, "ticker": ticker_of(sym), "tte": tte,
            "eligible": not why, "why": why}


def select_market(markets: Sequence[Mapping[str, Any]],
                  now: float) -> Dict[str, Any]:
    """The ONE market, or none. The EARLIEST-expiring eligible one, so the
    choice is deterministic rather than whichever the slate listed first."""
    rated = [eligible(m, now) for m in markets]
    ok = sorted((r for r in rated if r["eligible"]),
                key=lambda r: (r["tte"], r["ticker"]))
    return {"selected": ok[0] if ok else None, "considered": rated,
            "rule": ("one family, published+available strike, tte inside the "
                     "frozen OPEN band; earliest expiry wins")}


# ------------------------------------------------------- reconciliation
def reconcile_or_halt(cohort_id: str, symbol: str, inv: Any,
                      position_rows: Optional[Sequence[Mapping[str, Any]]], *,
                      root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """AUTHORITATIVE reconciliation. Anything but exact agreement HALTs.

    `position_rows is None` means the read did not succeed, which is
    UNKNOWN_POSITION and never zero.
    """
    rec = position_ledger.reconcile(LegView(inv), position_rows, symbol)
    if rec.get("verdict") == position_ledger.UNREADABLE:
        BUD.halt(cohort_id, "UNKNOWN_POSITION", rec.get("why"), root=root)
        rec["halted"] = True
        return rec
    if not rec.get("tradeable"):
        BUD.halt(cohort_id, "INVENTORY_DISAGREEMENT", rec.get("why"),
                 root=root)
        rec["halted"] = True
        return rec
    rec["halted"] = False
    return rec


def check_fill(cohort_id: str, intent: Mapping[str, Any],
               fill: Mapping[str, Any], *,
               root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """A fill against the intent that produced it. Two HALTs live here.

    FILL_ABOVE_LIMIT: we buy, so a fill price ABOVE our limit is worse than
    we agreed to and is a semantic contradiction, not a rounding detail.
    SIDE_MAPPING_DISAGREEMENT: the venue's outcome must map back to the leg
    we intended through SIDE_TO_OUTCOME, in both directions.
    """
    out: Dict[str, Any] = {"ok": True, "halted": None}
    limit = D(str(intent["price"]))
    try:
        got = D(str(fill["price"]))
    except Exception:                                          # noqa: BLE001
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "SEMANTIC_CONTRADICTION",
            "fill carried an unreadable price %r" % (fill.get("price"),),
            root=root))
        return out
    if got > limit:
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "FILL_ABOVE_LIMIT",
            "filled at %s against a submitted limit of %s" % (got, limit),
            root=root))
        return out
    venue_outcome = str(fill.get("outcome") or "").lower()
    want_side = str(intent["side"])
    if OUTCOME_TO_SIDE.get(venue_outcome) != want_side:
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "SIDE_MAPPING_DISAGREEMENT",
            "venue outcome %r maps to %r, we intended %r"
            % (fill.get("outcome"), OUTCOME_TO_SIDE.get(venue_outcome),
               want_side),
            root=root))
        return out
    try:
        if D(str(fill["quantity"])) > QUANTITY:
            out.update(ok=False, halted=BUD.halt(
                cohort_id, "QUANTITY_NOT_ONE",
                "filled quantity %s exceeds q=%s" % (fill["quantity"],
                                                     QUANTITY),
                root=root))
            return out
    except Exception:                                          # noqa: BLE001
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "SEMANTIC_CONTRADICTION",
            "fill carried an unreadable quantity %r" % (fill.get("quantity"),),
            root=root))
    return out


# --------------------------------------------------------------- the step
def plan(cohort_id: str, inv: Any, *, bid: Optional[D], ask: Optional[D],
         bid_size: Any, ask_size: Any, tte: float, n_extends: int,
         actions: int, root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """What the FROZEN policy says to do next, plus the live-only bounds.

    The decision itself is not made here and cannot be: `decide_once` is the
    frozen function, called with the frozen thresholds and q=QUANTITY.
    """
    h = BUD.halted(cohort_id, root=root)
    if h:
        return {"action": HOLD, "stop": "HALTED", "reason": h}

    # LOCKED IS CHECKED BEFORE ANYTHING ELSE AND INDEPENDENTLY OF THE POLICY.
    # decide_once also refuses on a locked book; this is a second, closer
    # gate so that a locked inventory cannot be mutated even if a caller
    # reached here with a decision in hand.
    if POLICY["locked"](inv):
        return {"action": HOLD, "stop": "LOCKED",
                "reason": ("LOCKED: both outcomes positive after fees. A "
                           "locked book is never unlocked for another "
                           "trade.")}

    dec = POLICY["decide_once"](
        inv, bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
        tte=tte, qty=QUANTITY, n_extends=n_extends, actions=actions,
        allow_extend=True)

    if dec["action"] == EXTEND and n_extends >= BUD.MAX_EXTENDS:
        # unreachable through the frozen rule, which refuses first; kept as a
        # named HALT so a policy change could never silently widen it.
        BUD.halt(cohort_id, "EXTEND_BUDGET_EXCEEDED",
                 "n_extends=%s, max=%s" % (n_extends, BUD.MAX_EXTENDS),
                 root=root)
        return {"action": HOLD, "stop": "HALTED",
                "reason": "a third EXTEND was proposed"}
    return dec


def intent_of(ticker: str, inv: Any, dec: Mapping[str, Any],
              n_extends: int) -> Dict[str, Any]:
    """The submission intent, with every number the budget will re-check.

    The quantity is QUANTITY and is not derived from anything.
    """
    side, px = dec["side"], D(str(dec["price"]))
    trial = POLICY["Inventory"](
        q_up=inv.q_up, q_down=inv.q_down, c_up=inv.c_up, c_down=inv.c_down,
        fees=inv.fees, rebates=inv.rebates, fills=list(inv.fills))
    trial.add(POLICY["make_fill"](0.0, side, "maker", px, QUANTITY,
                                 "intent " + str(dec["action"])))
    rs = trial.residual_side
    avg = (trial.avg_up if rs == "UP"
           else (trial.avg_down if rs == "DOWN" else D("0")))
    return {
        "action": dec["action"], "side": side,
        "outcome": SIDE_TO_OUTCOME[side],
        "price": str(px), "quantity": str(QUANTITY),
        "maker_or_cancel": True,
        "extends_before": n_extends,
        "unmatched_shares_after": str(trial.residual),
        "unmatched_principal_after": str(trial.residual * avg),
        "pair_cost_after": (None if trial.matched_pair_cost() is None
                            else str(trial.matched_pair_cost())),
        "required_complement_price_for_0.96":
            (lambda v: None if v is None else str(v))(
                POLICY["required_complement_price"](trial, QUANTITY)),
        "reason": dec["reason"],
        "policy_fingerprint": fingerprint(),
    }


async def one_outstanding_order(symbol: str) -> Dict[str, Any]:
    """Established FROM THE VENUE. Unreadable refuses, same as non-empty."""
    try:
        res = await transport.active_orders(symbol)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "readable": False,
                "why": "active orders could not be read (%s); an account "
                       "whose orders cannot be read is not an account with "
                       "none" % exc}
    orders = transport._orders_of(res)
    if orders:
        return {"ok": False, "readable": True, "open": len(orders),
                "why": "%d order(s) already outstanding on %s; the probe "
                       "allows exactly one" % (len(orders), symbol)}
    return {"ok": True, "readable": True, "open": 0, "why": None}


def assert_payload_is_q1(symbol: str, intent: Mapping[str, Any]
                         ) -> Dict[str, Any]:
    """Build the EXACT body that would be signed and assert q is 1 IN IT.

    This is the last link in the q=1 chain: policy -> intent -> budget ->
    PAYLOAD. Asserting on the intent alone would leave the possibility that
    the transport call passes something else, so the body itself is built,
    structurally validated and read back.
    """
    body = payload.build_order(symbol=symbol, price=str(intent["price"]),
                               quantity=str(intent["quantity"]),
                               outcome=intent["outcome"],
                               maker_or_cancel=True)
    payload.validate_structure(body)
    payload.assert_types(body)                 # allow_taker stays FALSE
    if D(str(body["quantity"])) != QUANTITY:
        raise ProbeRefused("payload quantity %r is not q=%s"
                           % (body["quantity"], QUANTITY))
    if body["makerOrCancel"] is not True:
        raise ProbeRefused("payload is not maker-or-cancel")
    return body


async def submit(cohort_id: str, symbol: str, ticker: str,
                 intent: Dict[str, Any], *,
                 root: str = cohort.COHORT_ROOT,
                 slot_hook: Optional[Any] = None) -> Dict[str, Any]:
    """Consume the slot DURABLY, then send exactly one maker-or-cancel order.

    ORDER OF OPERATIONS IS THE SAFETY PROPERTY. The slot is fsynced before
    the venue is touched, so an ambiguous send finds its slot already spent.
    There is no retry path in this function and no rollback in the budget.
    """
    if BUD.halted(cohort_id, root=root):
        raise ProbeRefused("HALTED: %s" % BUD.halted(cohort_id, root=root))
    if arming.EXPECTED_AUTH_ID != PROBE_AUTH_ID:
        raise ProbeRefused(
            "this build's authorization is %r, not the continuous-inventory "
            "probe's %r" % (arming.EXPECTED_AUTH_ID, PROBE_AUTH_ID))
    if (AUTHORIZED_POLICY_FINGERPRINT is None
            or AUTHORIZED_POLICY_FINGERPRINT != fingerprint()):
        BUD.halt(cohort_id, "POLICY_FINGERPRINT_MISMATCH",
                 "authorized=%r on_disk=%r"
                 % (AUTHORIZED_POLICY_FINGERPRINT, fingerprint()), root=root)
        raise ProbeRefused("the frozen policy on disk is not the one this "
                           "authorization was granted against")
    body = assert_payload_is_q1(symbol, intent)
    out = await one_outstanding_order(symbol)
    if not out["ok"]:
        raise ProbeRefused(out["why"])

    row = BUD.consume_slot(cohort_id, ticker, intent, root=root)
    if slot_hook is not None:
        try:
            ok = slot_hook(row)
        except Exception as exc:                               # noqa: BLE001
            ok = False
            row["slot_hook_error"] = str(exc)
        if not ok:
            # THE SLOT STAYS SPENT. Durability failed, so we must not send,
            # and we must not pretend the slot is free either.
            return {"status": "REFUSED_SLOT_NOT_DURABLE", "slot": row["slot"],
                    "sent": False, "intent": intent,
                    "why": "the durability hook did not confirm; the slot is "
                           "SPENT and is never retried"}
    try:
        res = await transport.place(symbol=symbol, side=payload.spec.SIDE_BUY,
                                    outcome=intent["outcome"],
                                    price=str(intent["price"]),
                                    quantity=str(intent["quantity"]))
    except Exception as exc:                                   # noqa: BLE001
        BUD.record_status(cohort_id, ticker, slot=row["slot"],
                          status="SUBMITTED",
                          detail={"ambiguous": True, "error": str(exc)},
                          root=root)
        return {"status": "AMBIGUOUS", "slot": row["slot"], "sent": True,
                "intent": intent, "body": body, "error": str(exc),
                "why": "the send outcome is unknown. The slot is SPENT and "
                       "is never retried."}
    BUD.record_status(cohort_id, ticker, slot=row["slot"], status="SUBMITTED",
                      detail={"venue": res}, root=root)
    return {"status": "SUBMITTED", "slot": row["slot"], "sent": True,
            "intent": intent, "body": body, "venue": res,
            "labels": "SUBMITTED is not FILLED. A maker-or-cancel "
                      "CANCELLATION is a normal outcome and is never chased "
                      "or repriced outside the frozen policy."}


# ------------------------------------------------------------- preflight
def preflight(cohort_id: str, *,
              root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Everything checkable without touching the venue. Opens no path."""
    from src.gemini_execution import ci15m_policy
    return {
        "label": LABEL,
        "authorization_id": PROBE_AUTH_ID,
        "build_authorization_id": arming.EXPECTED_AUTH_ID,
        "authorization_matches": arming.EXPECTED_AUTH_ID == PROBE_AUTH_ID,
        "arming_state": arming.state(),
        "policy": ci15m_policy.summary(),
        "authorized_policy_fingerprint": AUTHORIZED_POLICY_FINGERPRINT,
        "policy_fingerprint_matches":
            AUTHORIZED_POLICY_FINGERPRINT == fingerprint(),
        "budget": BUD.state(cohort_id, root=root),
        "symbol_family": SYMBOL_PREFIX,
        "side_mapping": SIDE_TO_OUTCOME,
        "maker_or_cancel": True,
        "taker_path_reachable": False,
        "taker_note": ("transport.place is called WITHOUT allow_taker, and "
                       "allow_taker additionally requires the semantics "
                       "authorization, so a crossing order is not "
                       "constructible under this probe."),
        "separation": ("the prospective EXTEND-vs-NO-EXTEND shadow is a "
                       "different process writing different files, and its "
                       "results are never pooled with these rows."),
    }
