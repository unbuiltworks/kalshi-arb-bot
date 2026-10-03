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
from src.gemini_execution import ci15m_adapters as AD
from src.gemini_execution import ci15m_treatments as TREAT
from src.gemini_execution.continuous_probe import (COMPLETE, EXTEND, HOLD,
                                                   OPEN)

LABEL = "GEMINI_CI15M_PROBE_V1"

#: the authorization these bounds were granted against. It must match
#: `arming.EXPECTED_AUTH_ID` before anything is constructed.
PROBE_AUTH_ID = "OWNER-2026-10-03-GEMINI-CI15M-4"

#: THE EXECUTION TREATMENT, fixed for this cohort. T = capped marketable
#: limit at the REAL executable ask. M is implemented and tested but is NOT
#: authorized here, so it is not selectable at runtime: a probe that could
#: switch treatment mid-cohort would not be a controlled experiment.
TREATMENT = TREAT.TAKER

#: the policy bytes the authorization was granted against. INSTALLED
#: 2026-10-03 under the owner's FINAL AUTHORIZATION -- CI15M-1. None would
#: mean "whatever is on disk", which is refused for a live run.
#:
#: If the frozen policy file changes by one byte this no longer matches,
#: `submit` writes POLICY_FINGERPRINT_MISMATCH and the cohort HALTs. That is
#: what makes "do not modify the frozen policy" enforceable.
AUTHORIZED_POLICY_FINGERPRINT: Optional[str] = (
    "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329")

#: the commit the authorization names. Verified at runtime, before the first
#: venue submission, by `verify_runtime()`.
AUTHORIZED_SHA = "dfcee7b78a69db37272bbd2c5452f12385d202b9"

#: THE BOUNDS CI15M-4 WAS AUTHORIZED WITH, as literals.
#:
#: WHY THIS EXISTS. The authorization names new cohort bounds -- two
#: windows, six orders, three per window -- which cannot be installed
#: without changing `ci15m_budget.py`, and that file is deliberately
#: EXCLUDED from the allowed-diff set because it carries every risk bound.
#: Widening the file list would have thrown away the protection. So the
#: file is admitted ONLY while the bounds it defines equal these literals:
#: provenance is tied to the authorized NUMBERS, not to a filename. Change
#: any of them and the probe refuses, whatever the diff looks like.
AUTHORIZED_BOUNDS = {
    "profile": "MECHANICS2",
    "windows": 2,
    "orders_total": 6,
    "orders_per_window": 3,
    "quantity": "1",
    "max_acquisition_price": "0.90",
    "max_pair_cost": "0.96",
    "max_extends": "2",
    "extend_min_avg_improvement": "0.05",
    "max_unmatched_shares": "3",
    "max_unmatched_principal": "2.70",
    "open_side_rule": "NATIVE_UP",
    "treatment": "T",
}


def bounds_match() -> Dict[str, Any]:
    """Do the installed bounds equal what CI15M-4 authorized?"""
    got = {
        "profile": None, "windows": None, "orders_total": None,
        "orders_per_window": None,
        "quantity": str(QUANTITY),
        "max_acquisition_price": str(POLICY["HARD_MAX_PRICE"]),
        "max_pair_cost": str(POLICY["MAX_PAIR_COST"]),
        "max_extends": str(POLICY["MAX_EXTENDS_BEFORE_COMPLEMENT"]),
        "extend_min_avg_improvement":
            str(POLICY["EXTEND_MIN_AVG_IMPROVEMENT"]),
        "max_unmatched_shares": str(BUD.MAX_UNMATCHED_SHARES),
        "max_unmatched_principal": str(BUD.MAX_UNMATCHED_PRINCIPAL),
        "open_side_rule": POLICY["decide_once"].__globals__["OPEN_SIDE_RULE"],
        "treatment": TREATMENT,
    }
    prof = BUD.PROFILES.get(BUD.MECHANICS2) or {}
    got["profile"] = BUD.MECHANICS2
    got["windows"] = prof.get("windows")
    got["orders_total"] = prof.get("orders_total")
    got["orders_per_window"] = prof.get("orders_per_window")
    bad = {k: (AUTHORIZED_BOUNDS[k], got[k]) for k in AUTHORIZED_BOUNDS
           if got[k] != AUTHORIZED_BOUNDS[k]}
    return {"ok": not bad, "installed": got, "authorized": AUTHORIZED_BOUNDS,
            "mismatched": bad}

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
              n_extends: int, *, reconciled_signed: Any = None,
              ladder_bid: Any = None, ladder_ask: Any = None,
              telemetry: Any = None) -> Dict[str, Any]:
    """The submission intent, with every number the budget will re-check.

    The quantity is QUANTITY and is not derived from anything.
    """
    side = dec["side"]
    # TREATMENT T. The frozen policy already chose the side and the
    # acquisition price FROM THE LADDER; the treatment restates it as a
    # capped marketable limit and asserts the cap. It changes no decision.
    t = TREAT.taker_price(side, D(str(ladder_bid)), D(str(ladder_ask)),
                          ceiling=POLICY["HARD_MAX_PRICE"])
    if not t["ok"]:
        raise ProbeRefused("Treatment T refused: %s" % t["why"])
    px = D(str(t["price"]))
    if px != D(str(dec["price"])):
        raise ProbeRefused(
            "Treatment T price %s disagrees with the frozen policy's "
            "acquisition %s -- the treatment must restate the decision, "
            "never change it" % (px, dec["price"]))
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
        # TREATMENT T: a crossing LIMIT. Not post-only, never a market order.
        "maker_or_cancel": False,
        "treatment": TREATMENT,
        "ladder_bid": str(ladder_bid), "ladder_ask": str(ladder_ask),
        "price_source": "/v1/book executable ladder",
        "contract_price_telemetry": telemetry,
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
        # 3 -- THE STAMP. Every bound below is computed from `inv`, so the
        # submission must carry proof that `inv` was reconciled against the
        # venue. `consume_slot` refuses an unstamped intent.
        RECONCILED: reconciled_signed,
    }


async def one_outstanding_order(symbol: str) -> Dict[str, Any]:
    """B -- THE HARD INVARIANT, now decided by one normalised type.

    `AD.ActiveOrders` requires the read to have SUCCEEDED and its shape to
    have been RECOGNISED before it will report a count, so neither a failed
    read nor an unrecognised body can present as "zero outstanding orders".
    That was the hole four CI15M-1 orders rested through simultaneously.
    An order in a non-terminal state also blocks, not merely a non-empty
    list.
    """
    try:
        res = await transport.active_orders(symbol)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "readable": False, "read": None,
                "why": "active orders could not be read (%s); an account "
                       "whose orders cannot be read is not an account with "
                       "none" % exc}
    ao = AD.ActiveOrders.of(res)
    read = {k: res.get(k) for k in ("ok", "status", "classification",
                                    "request_shape")} \
        if isinstance(res, Mapping) else None
    if not ao.readable:
        return {"ok": False, "readable": False, "read": read, "why": ao.why}
    if ao.count:
        return {"ok": False, "readable": True, "open": ao.count, "read": read,
                "order_ids": [o.order_id for o in ao.orders][:10],
                "unresolved": ao.any_unresolved,
                "why": "%d order(s) already outstanding on %s; the probe "
                       "allows exactly one, and an unresolved order blocks "
                       "the next decision" % (ao.count, symbol)}
    return {"ok": True, "readable": True, "open": 0, "read": read,
            "why": None}


# ------------------------------------------------- the order lifecycle
#
# A PLACE ACK IS NOT A TERMINAL ORDER RESULT. CI15M-1 treated it as one and
# therefore never learned about its own four fills: slots 2-5 acked
# `status: open, filledQuantity: 0`, the engine recorded NOT_FILLED, and the
# orders then rested and filled with nobody watching. Inventory stayed FLAT,
# the risk gates were evaluated against that phantom-flat inventory, and 4
# shares / $2.86 got past a 3-share / $2.70 cap.
SUBMITTED = "SUBMITTED"
ACK_FILLED = "ACK_FILLED"
ACK_CANCELLED = "ACK_CANCELLED"
ACK_RESTING = "ACK_RESTING"
ACK_UNRESOLVED = "ACK_UNRESOLVED"
TERMINAL_FILLED = "TERMINAL_FILLED"
TERMINAL_CANCELLED = "TERMINAL_CANCELLED"

#: venue statuses that are TERMINAL. Nothing else is, and absent is not.
TERMINAL_STATUSES = ("filled", "cancelled", "canceled", "expired",
                     "rejected", "closed")

UNRESOLVED_STATUSES = ("open", "accepted", "pending", "partially_filled",
                       "partially filled", "")


def classify_ack(body: Mapping[str, Any]) -> Dict[str, Any]:
    """SUBMITTED -> ACK_CANCELLED | ACK_FILLED | ACK_RESTING | ACK_UNRESOLVED.

    `open` IS NOT `NOT_FILLED`. It is a resting order whose fate is still
    open, and it must be polled to a terminal state before anything else
    happens.
    """
    o = AD.OrderState.of(body)
    state = {AD.FILLED: ACK_FILLED, AD.CANCELLED: ACK_CANCELLED,
             AD.RESTING: ACK_RESTING,
             AD.UNRESOLVED: ACK_UNRESOLVED}[o.state]
    return {"state": state, "status": o.status, "filled": o.filled,
            "terminal": o.terminal, "why": o.why, "order": o}


async def poll_terminal(cohort_id: str, symbol: str, order_id: Any, *,
                        attempts: int = 12, pause: float = 5.0,
                        root: str = cohort.COHORT_ROOT,
                        sleeper: Optional[Any] = None) -> Dict[str, Any]:
    """Poll the AUTHORITATIVE order state until it is terminal.

    Reads the venue's own order history, which is the only surface that can
    show a FILLED order -- an active-orders read by construction cannot.
    An unreadable or uninterpretable state HALTs rather than being guessed.
    """
    from src.gemini_execution import metadata
    sleep = sleeper or asyncio.sleep
    last: Dict[str, Any] = {}
    for i in range(attempts):
        try:
            hist = await metadata.order_history(
                symbol=symbol, statuses=("filled", "cancelled", "open"),
                limit=100, include_rows=True)
        except Exception as exc:                               # noqa: BLE001
            last = {"error": str(exc)}
            await sleep(pause)
            continue
        h = AD.OrderHistory.of(hist)
        if not h.readable:
            last = {"unreadable": h.why}
            await sleep(pause)
            continue
        found = h.find(order_id)
        if found is not None:
            if found.terminal:
                return {"resolved": True,
                        "state": (TERMINAL_FILLED if found.state == AD.FILLED
                                  else TERMINAL_CANCELLED),
                        "status": found.status, "filled": found.filled,
                        "row": {"price": (None if found.price is None
                                          else str(found.price)),
                                "outcome": found.outcome,
                                "orderId": found.order_id},
                        "attempts": i + 1}
            last = {"status": found.status, "why": found.why}
        await sleep(pause)
    BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
             "order %s never reached a terminal state after %d polls: %r"
             % (order_id, attempts, last), root=root)
    return {"resolved": False, "state": ACK_UNRESOLVED, "last": last,
            "attempts": attempts,
            "why": ("the order's terminal state could not be established. "
                    "An unreadable order state fails closed; the engine may "
                    "not decide again.")}


def _position_rows(got: Any) -> Optional[List[Mapping[str, Any]]]:
    """Kept as a thin shim over `AD.PositionRead` so the normalisation lives
    in ONE place at the boundary. See ci15m_adapters for the contract and
    for why "empty" and "unknown" must never be the same value."""
    return AD.PositionRead.of(got).rows


def _position_rows_legacy(got: Any) -> Optional[List[Mapping[str, Any]]]:
    """The rows out of `metadata.positions()`, READ FROM ITS OWN CONTRACT.

    THE DEFECT THIS REPLACES, AND IT IS THE THIRD OF ITS KIND. The previous
    code looked for `positions`, `result` or `data`. `metadata.positions()`
    returns neither: its contract is {"ok": bool, "rows": list|None,
    "status": int, "why": str}, where `rows` is None iff the read failed.
    So a SUCCESSFUL read was thrown away, `venue_signed(None, ...)` said
    UNREADABLE, and CI15M-2 wrote an UNKNOWN_POSITION halt and stopped
    before placing a single order -- on its only authorized window.

    Fail-closed behaved correctly; the adapter did not. Three times now I
    have written a reader against a shape I guessed rather than the one the
    producer documents: the place ack, the active-orders body, and now this.
    So this function reads `ok` FIRST and returns None on anything else,
    rather than hunting for a key that might happen to be there.
    """
    if not isinstance(got, Mapping):
        return None
    if got.get("ok") is not True:
        return None                 # an unsuccessful read is UNREADABLE
    rows = got.get("rows")
    return rows if isinstance(rows, list) else None


RECONCILED = "reconciled_venue_inventory"


async def reconciled_inventory(cohort_id: str, symbol: str, *,
                               root: str = cohort.COHORT_ROOT,
                               ours: Any = None) -> Dict[str, Any]:
    """Inventory AS THE VENUE'S AUTHENTICATED FILL RECORD REPORTS IT.

    THE RISK GATES RUN ON THIS. `positions()` is still read and still
    reported, but only as TELEMETRY: for BTC15m it has never carried one of
    these contracts, so a successful EMPTY positions result may not
    contradict a successfully reconstructed fill ledger. An UNREADABLE fill
    record still HALTs -- the authority moved, the fail-closed rule did not.

    `ours` is the executor's durable belief. When supplied it must AGREE
    with the reconstruction or the cohort HALTs: a reconstruction that
    disagrees with our own ledger means we do not know what we hold.
    """
    got = await authenticated_fills(cohort_id, symbol, root=root)
    if not got["ok"]:
        return {"ok": False, "why": got["why"], "signed": None}
    rec = inventory_from_fills(got["rows"], symbol)
    if not rec["ok"]:
        BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION", rec["why"], root=root)
        return {"ok": False, "why": rec["why"], "signed": None}
    signed = rec["up_qty"] - rec["down_qty"]

    if ours is not None:
        mine = D(str(ours.q_up)) - D(str(ours.q_down))
        if mine != signed:
            BUD.halt(cohort_id, "INVENTORY_DISAGREEMENT",
                     "authenticated fills reconstruct a signed position of "
                     "%s; the executor's durable ledger holds %s"
                     % (signed, mine), root=root)
            return {"ok": False, "signed": signed,
                    "why": "fill record %s != our ledger %s" % (signed, mine)}

    # ---- TELEMETRY ONLY below. It can never block and never HALTs.
    tel = {"read": False}
    try:
        from src.gemini_execution import metadata
        pr = AD.PositionRead.of(await metadata.positions())
        if pr.readable:
            v = position_ledger.venue_signed(pr.rows, symbol)
            tel = {"read": True, "rows_for_symbol": v.get("rows", 0),
                   "representation": v.get("representation"),
                   "positions_signed": (None if v.get("signed") is None
                                        else str(v["signed"])),
                   "agrees_with_fills": (v.get("signed") == signed),
                   "labels": ("TELEMETRY. For BTC15m no contract has ever "
                              "appeared in a positions row, so a successful "
                              "EMPTY result here is NOT evidence of flat and "
                              "does not contradict the fill record.")}
        else:
            tel = {"read": False, "why": pr.why}
    except Exception as exc:                                   # noqa: BLE001
        tel = {"read": False, "why": "%s: %s" % (type(exc).__name__, exc)}

    return {"ok": True, "signed": signed, "inventory": rec["inventory"],
            "fills": got["rows"],
            "attribution": got.get("attribution"),
            "reconstruction": {k: (None if rec.get(k) is None
                                   else str(rec[k]))
                               for k in ("up_qty", "down_qty", "up_cost",
                                         "down_cost", "matched",
                                         "matched_pair_cost", "residual_qty",
                                         "avg_up", "avg_down")},
            "residual_side": rec["residual_side"],
            "n_fills": rec["n_fills"], "source": got["source"],
            "positions_telemetry": tel}


def order_is_resolved(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Is this order's fate SETTLED? submit ACK -> venue order status.

    `filled` and `cancelled` are resolved. `open` is NOT: the order is
    resting and the engine may not decide again until it is gone. An
    unreadable or unrecognised status is NOT resolved either -- fail closed.
    """
    st = str(body.get("status") or "").strip().lower()
    if st in ("filled", "closed", "cancelled", "canceled", "expired",
              "rejected"):
        return {"resolved": True, "status": st}
    if st in UNRESOLVED_STATUSES:
        return {"resolved": False, "status": st or "(absent)",
                "why": "the order is still live or its status is absent; "
                       "the engine may not submit again while any previous "
                       "order is unresolved"}
    return {"resolved": False, "status": st,
            "why": "unrecognised order status %r -- fail closed" % st}


def assert_payload_is_q1(symbol: str, intent: Mapping[str, Any]
                         ) -> Dict[str, Any]:
    """Build the EXACT body that would be signed and assert q is 1 IN IT.

    This is the last link in the q=1 chain: policy -> intent -> budget ->
    PAYLOAD. Asserting on the intent alone would leave the possibility that
    the transport call passes something else, so the body itself is built,
    structurally validated and read back.
    """
    # the intent's own flag is GUARDED, not ignored: under Treatment T a
    # post-only intent is a contradiction, and silently overriding it would
    # hide a caller that had the wrong model of the order.
    if intent.get("maker_or_cancel") is not False:
        raise ProbeRefused(
            "Treatment T requires maker_or_cancel False in the intent; got "
            "%r" % (intent.get("maker_or_cancel"),))
    body = payload.build_order(symbol=symbol, price=str(intent["price"]),
                               quantity=str(intent["quantity"]),
                               outcome=intent["outcome"],
                               maker_or_cancel=False)
    payload.validate_structure(body)
    payload.assert_types(body, allow_taker=True)
    if D(str(body["quantity"])) != QUANTITY:
        raise ProbeRefused("payload quantity %r is not q=%s"
                           % (body["quantity"], QUANTITY))
    if body["makerOrCancel"] is not False:
        raise ProbeRefused("Treatment T requires makerOrCancel false")
    if body["orderType"] != payload.spec.ORDER_TYPE_LIMIT:
        raise ProbeRefused("not a LIMIT order -- there is no unrestricted "
                           "market order on this path")
    if not str(body.get("price") or "").strip():
        raise ProbeRefused("a marketable limit must carry an explicit price")
    if D(str(body["price"])) > POLICY["HARD_MAX_PRICE"]:
        raise ProbeRefused("payload price %s exceeds the ceiling %s"
                           % (body["price"], POLICY["HARD_MAX_PRICE"]))
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
                                    quantity=str(intent["quantity"]),
                                    allow_taker=True)
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
        "treatment": TREATMENT,
        "taker_path_reachable": True,
        "taker_note": ("Treatment T is a CAPPED MARKETABLE LIMIT: "
                       "orderType limit, explicit price from the /v1/book "
                       "executable ladder, bounded by the 0.90 ceiling, "
                       "makerOrCancel false. allow_taker is reachable ONLY "
                       "because arming.EXPECTED_AUTH_ID equals "
                       "payload.SEMANTICS_PROBE_AUTH_ID under THIS "
                       "authorization. There is no unrestricted market "
                       "order on this path."),
        "separation": ("the prospective EXTEND-vs-NO-EXTEND shadow is a "
                       "different process writing different files, and its "
                       "results are never pooled with these rows."),
    }


# ================================================================= RUNTIME
#
# THE SEVEN CONDITIONS THE OWNER REQUIRED BEFORE THE FIRST VENUE SUBMISSION.
# Checked at RUNTIME, in the credentialed environment, not here at authoring
# time -- an authoring-time check proves nothing about the process that will
# actually send the order.
def head_sha() -> Optional[str]:
    """The commit this process is running from, read from git."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              timeout=20).stdout.strip() or None
    except Exception:                                          # noqa: BLE001
        return None


def verify_runtime(cohort_id: str, *,
                   root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Every pre-submission condition, each answered independently.

    `ok` is True only when ALL of them pass. The SHA check is reported as
    `sha_is_authorized_or_descendant` because installing the authorization
    itself necessarily advances HEAD past the authorized commit -- see
    `sha_note`.
    """
    import subprocess
    head = head_sha()
    descendant = None
    diff_files: List[str] = []
    if head:
        try:
            descendant = subprocess.run(
                ["git", "merge-base", "--is-ancestor", AUTHORIZED_SHA, head],
                capture_output=True, timeout=20).returncode == 0
            diff_files = [x for x in subprocess.run(
                ["git", "diff", "--name-only", AUTHORIZED_SHA, head],
                capture_output=True, text=True,
                timeout=20).stdout.split("\n") if x.strip()]
        except Exception:                                      # noqa: BLE001
            descendant = None

    st = BUD.state(cohort_id, root=root)
    bm = bounds_match()
    checks = {
        "head_known": head is not None,
        "head_is_authorized_sha": head == AUTHORIZED_SHA,
        "head_is_authorized_or_descendant": bool(descendant),
        "policy_fingerprint_matches":
            fingerprint() == AUTHORIZED_POLICY_FINGERPRINT,
        "authorization_installed":
            arming.EXPECTED_AUTH_ID == PROBE_AUTH_ID
            == "OWNER-2026-10-03-GEMINI-CI15M-4",
        "quantity_is_one": QUANTITY == D("1"),
        "consumed_slots_zero": st["order_slots_consumed"] == 0,
        "confirmed_realized_loss_zero":
            D(st["confirmed_realized_principal_loss"]) == D("0"),
        "no_halt": st["halted"] is None,
        "not_expired": st["expired"] is None,
        # CI15M-2 Treatment T REQUIRES the taker path, so the condition
        # inverts: the two constants must AGREE, and both must be CI15M-2.
        "authorized_bounds_installed": bool(bm["ok"]),
        "treatment_t_authorized":
            (payload.SEMANTICS_PROBE_AUTH_ID == arming.EXPECTED_AUTH_ID
             == PROBE_AUTH_ID),
        "arming_keys_present": arming.armed(),
    }
    # The SHA condition is satisfied by EITHER an exact match or a descendant
    # whose diff touches only the two authorization constants.
    # The ONLY files a descendant of the authorized commit may differ in.
    # arming.py and ci15m_probe.py carry the two authorization constants and
    # the runtime gate itself. The test file is included because it is not
    # imported by any execution path -- it cannot change what is sent -- and
    # installing the authorization necessarily made its pre-arm assertions
    # about those constants stale. The FROZEN POLICY FILE IS NOT IN THIS SET,
    # and is additionally checked exactly, by fingerprint, above.
    AUTH_ONLY = {"src/gemini_execution/arming.py",
                 # payload.py carries SEMANTICS_PROBE_AUTH_ID, the constant
                 # that gates `allow_taker`. Treatment T needs
                 # makerOrCancel false, so the owner's authorization of T
                 # necessarily moves it. It is an AUTHORIZATION CONSTANT,
                 # the same category as arming.py -- and a test asserts the
                 # diff on this file touches nothing but that constant.
                 "src/gemini_execution/payload.py",
                 "src/gemini_execution/ci15m_probe.py",

                 # WIDENED 2026-10-03, and the widening is stated rather than
                 # quiet. The owner's instruction was explicit: "Add the
                 # minimal ci15m workflow path/step required to run the
                 # already-authorized CI15M probe." The CLI entry point and
                 # the workflow step ARE that path, and they did not exist at
                 # the authorized commit, so a descendant carrying them must
                 # pass or the authorized probe cannot run at all.
                 #
                 # This DOES widen the gate, and it is the weakest link in
                 # the SHA condition: ci15m_runner.py is execution code.
                 # What it does NOT widen is the substantive protection --
                 # the FROZEN POLICY FILE is still excluded, and is checked
                 # separately and EXACTLY by fingerprint, so no decision
                 # rule, threshold or bound can change through this door.
                 "src/gemini_execution/ci15m_runner.py",
                 ".github/workflows/gemini-settlement-gate.yml"}
    def _permitted(f: str) -> bool:
        if f in AUTH_ONLY:
            return True
        # ci15m_budget.py carries every risk bound and is NOT in AUTH_ONLY.
        # CI15M-4's authorization names new cohort bounds, so the file must
        # change -- and it is admitted ONLY while the installed bounds equal
        # the authorized literals. The numbers are the provenance here, not
        # the filename.
        if f == "src/gemini_execution/ci15m_budget.py":
            return bool(bm["ok"])
        # A TEST FILE IS ADMITTED BY RULE, because it is imported by no
        # execution path and cannot change what is sent. Nothing else is:
        # any other .py, and the frozen policy above all, is refused -- and
        # the frozen policy is additionally pinned EXACTLY by fingerprint.
        return (f.startswith("tests/test_ci15m_") and f.endswith(".py"))

    not_permitted = sorted(f for f in diff_files if not _permitted(f))
    sha_ok = checks["head_is_authorized_sha"] or (
        checks["head_is_authorized_or_descendant"]
        and bool(diff_files) and not not_permitted)
    checks["sha_condition"] = bool(sha_ok)
    required = [k for k in checks
                if k not in ("head_is_authorized_sha",
                             "head_is_authorized_or_descendant")]
    failed = [k for k in required if not checks[k]]
    return {
        "ok": not failed, "failed": failed, "checks": checks,
        "head": head, "authorized_sha": AUTHORIZED_SHA,
        "diff_from_authorized_sha": diff_files,
        "diff_paths_not_permitted": not_permitted,
        "policy_fingerprint": fingerprint(),
        "bounds": bm,
        "sha_note": ("the authorized SHA names the POLICY AND LOGIC commit. "
                     "Installing the authorization id and the authorized "
                     "fingerprint necessarily advances HEAD past it, so the "
                     "condition is: HEAD is that commit, or a descendant of "
                     "it whose diff touches ONLY arming.py and "
                     "ci15m_probe.py. The frozen policy file is not in that "
                     "set, and its fingerprint is checked separately and "
                     "exactly."),
        "labels": ("every condition is answered independently; `ok` requires "
                   "all of them. Nothing may be submitted while `ok` is "
                   "False."),
    }


def book_from_venue(symbol: str) -> Dict[str, Any]:
    """The decision book, PRICED FROM THE EXECUTABLE LADDER.

    WHAT CHANGED AND WHY. CI15M-1 took the PRICE from the contract object's
    `prices.buy.yes` / `bestAsk` and only the SIZE from `/v1/book`. Measured
    against the real ladder over 372 instants on 15 BTC15m symbols, that
    contract price agreed with the executable best ask only 23.1% of the
    time: it sat BELOW it 39.5% of the time (to 12c) and ABOVE it 60.5%
    (to 5c). A limit at a price below the real ask does not cross -- it
    rests. That is the whole explanation for CI15M-1's four accidental
    maker fills and eight instant cancellations, and the tape's 39.5/60.5
    split predicted the live 33/67 outcome.

    So the frozen policy's "immediately executable acquisition price" is now
    taken from the SAME authoritative ladder that was already supplying
    size. The contract price is still read and is carried through as
    TELEMETRY, so `contract price <-> executable ladder price` keeps being
    measured rather than forgotten.

    Nothing about the policy changes. This is the market-state INPUT.
    """
    from src.gemini_execution import metadata
    lad = AD.ExecutableBook.of(metadata.contract_depth(symbol))
    if not lad.readable:
        return {"readable": False, "why": lad.why, "stage": "ladder",
                "semantic_contradiction": bool(lad.crossed)}
    bid, ask = lad.bid, lad.ask

    # ---- TELEMETRY ONLY. Never used to price an order.
    tel: Dict[str, Any] = {"read": False}
    try:
        ob = metadata.outcome_book(symbol)
        if ob.get("readable"):
            c_ask = D(ob["yes"]["ask"])
            c_bid = D(ob["yes"]["bid"])
            tel = {"read": True,
                   "contract_ask": str(c_ask), "contract_bid": str(c_bid),
                   "ladder_ask": str(ask), "ladder_bid": str(bid),
                   "ask_gap_contract_minus_ladder": str(c_ask - ask),
                   "bid_gap_contract_minus_ladder": str(c_bid - bid),
                   "contract_ask_would_have": (
                       "CROSSED" if c_ask >= ask else "RESTED"),
                   "identity_consistent": ob.get("identity_consistent"),
                   "labels": ("TELEMETRY. The contract price is recorded so "
                              "the disagreement keeps being measured; it "
                              "prices nothing.")}
        else:
            tel = {"read": False, "why": ob.get("why")}
    except Exception as exc:                                   # noqa: BLE001
        tel = {"read": False, "why": "%s: %s" % (type(exc).__name__, exc)}

    return {
        "readable": True,
        "source": "contract_depth /v1/book -- the EXECUTABLE ladder",
        "bid": bid, "ask": ask,
        "bid_size": lad.bid_size,
        "ask_size": lad.ask_size,
        "up_acquisition": str(ask),
        "down_acquisition": str(D("1") - bid),
        "snapshot_age_s": lad.snapshot_age_s,
        "contract_price_telemetry": tel,
        "ladder": lad,
    }


async def reconcile_after_fill(cohort_id: str, symbol: str, inv: Any, *,
                               root: str = cohort.COHORT_ROOT
                               ) -> Dict[str, Any]:
    """AUTHORITATIVE reconciliation against the account, after every fill."""
    from src.gemini_execution import metadata
    try:
        got = await metadata.positions()
    except Exception as exc:                                   # noqa: BLE001
        BUD.halt(cohort_id, "UNKNOWN_POSITION",
                 "positions read failed: %s" % exc, root=root)
        return {"halted": True, "verdict": "UNREADABLE", "why": str(exc)}
    rows = _position_rows(got)
    return reconcile_or_halt(cohort_id, symbol, inv, rows, root=root)


def decision_telemetry(ticker: str, inv: Any, bk: Mapping[str, Any],
                       tte: float, dec: Mapping[str, Any], *,
                       t: float, n_extends: int) -> Dict[str, Any]:
    """Every field the owner named, on EVERY decision including HOLD.

    THE OPPOSITE EXECUTABLE ASK IS PRESERVED ON EVERY TICK. With an UP
    residual the complement costs `1 - bid`, and recording it even while
    the policy HOLDs is the only way to answer, afterwards, how long the
    complement took to become reachable and at what pair cost it first
    could have been taken. That cannot be reconstructed later.
    """
    bid, ask = bk.get("bid"), bk.get("ask")
    rs = inv.residual_side
    req = POLICY["required_complement_price"](inv, QUANTITY)
    down_acq = None if bid is None else D("1") - bid
    down_bid = None if ask is None else D("1") - ask
    opp_acq = (down_acq if rs == "UP" else (ask if rs == "DOWN" else None))
    return {
        "kind": "DECISION", "utc": BUD._now(), "t": round(t, 3),
        "ticker": ticker, "tte": round(tte, 1),
        "up_bid": None if bid is None else str(bid),
        "up_ask": None if ask is None else str(ask),
        "down_bid": None if down_bid is None else str(down_bid),
        "down_ask": None if down_acq is None else str(down_acq),
        "up_size": None if bk.get("ask_size") is None else str(bk["ask_size"]),
        "down_size": (None if bk.get("bid_size") is None
                      else str(bk["bid_size"])),
        "up_qty": str(inv.q_up), "up_cost": str(inv.c_up),
        "up_avg": None if inv.avg_up is None else str(inv.avg_up),
        "down_qty": str(inv.q_down), "down_cost": str(inv.c_down),
        "down_avg": None if inv.avg_down is None else str(inv.avg_down),
        "matched_qty": str(inv.matched),
        "matched_cost": str(inv.matched_cost),
        "matched_pair_cost": (None if inv.matched_pair_cost() is None
                              else str(inv.matched_pair_cost())),
        "residual_side": rs, "residual_qty": str(inv.residual),
        "residual_principal": str(
            inv.residual * ((inv.avg_up if rs == "UP" else inv.avg_down)
                            or D("0"))),
        "required_complement_price_for_0.96": (None if req is None
                                               else str(req)),
        "opposite_executable_ask": (None if opp_acq is None
                                    else str(opp_acq)),
        "complement_reachable_at_0.96": (
            None if (req is None or opp_acq is None)
            else bool(opp_acq <= req)),
        "policy_action": dec.get("action"),
        "policy_stop": dec.get("stop"),
        "policy_side": dec.get("side"),
        "submitted_price": (None if dec.get("price") is None
                            else str(dec["price"])),
        "extends_so_far": n_extends,
        "locked": bool(POLICY["locked"](inv)),
        "reason": str(dec.get("reason") or "")[:300],
        "labels": ("one row per DECISION, HOLD included. The opposite "
                   "executable ask is preserved on every tick so time to "
                   "first executable complement and best executable pair "
                   "cost can be computed later."),
    }


def _extend_report(inv_before: Any, inv_after: Any, px: D,
                   req_before: Optional[D], req_after: Optional[D]
                   ) -> Dict[str, Any]:
    """Exactly the fields the owner asked for on every EXTEND."""
    rs = inv_after.residual_side
    avg_after = (inv_after.avg_up if rs == "UP" else inv_after.avg_down)
    rs_b = inv_before.residual_side
    avg_before = (inv_before.avg_up if rs_b == "UP"
                  else (inv_before.avg_down if rs_b == "DOWN" else None))
    return {
        "old_avg": None if avg_before is None else str(avg_before),
        "fill_price": str(px),
        "new_avg": str(avg_after),
        "complement_ceiling_before":
            None if req_before is None else str(req_before),
        "complement_ceiling_after":
            None if req_after is None else str(req_after),
        "ceiling_widened_by":
            (None if (req_before is None or req_after is None)
             else str(req_after - req_before)),
        "residual_shares": str(inv_after.residual),
        "residual_principal": str(inv_after.residual * avg_after),
    }


async def run_window(cohort_id: str, symbol: str, ticker: str, expiry_ts: float,
                     *, root: str = cohort.COHORT_ROOT, poll_s: float = 5.0,
                     slot_hook: Optional[Any] = None,
                     now_fn: Optional[Any] = None) -> Dict[str, Any]:
    """ONE live BTC15m window, decided by the frozen policy and nothing else.

    THE LOOP IS DELIBERATELY DULL. Read the book, ask the frozen policy, and
    if it says act: build the payload, spend a durable slot, send ONE
    maker-or-cancel order, read back what happened, reconcile against the
    account, and go round again. Every bound lives in `plan` and
    `consume_slot`; this function adds none of its own.

    A MOC CANCELLATION IS A NORMAL OUTCOME. It spends its slot and the loop
    continues. There is no reprice and no chase: the next decision is taken
    from the next book by the same frozen rule.
    """
    import time
    clock = now_fn or time.time
    inv = new_inventory()
    n_extends, actions = 0, 0
    events: List[Dict[str, Any]] = []

    def ev(kind: str, **kw) -> Dict[str, Any]:
        row = {"utc": BUD._now(), "kind": kind, "ticker": ticker}
        row.update(kw)
        events.append(row)
        return row

    while True:
        tte = expiry_ts - clock()
        if tte <= 0:
            ev("WINDOW_EXPIRED", tte=tte)
            break
        h = BUD.halted(cohort_id, root=root)
        if h:
            ev("HALTED", why=h)
            break
        why = BUD.expired(cohort_id, root=root)
        if why:
            ev("COHORT_STOP", why=why)
            break

        bk = book_from_venue(symbol)
        if not bk["readable"]:
            if bk.get("semantic_contradiction"):
                BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION", bk["why"],
                         root=root)
                ev("HALTED", why=bk["why"])
                break
            ev("BOOK_UNREADABLE", stage=bk.get("stage"), why=bk.get("why"))
            await asyncio.sleep(poll_s)
            continue

        pl = plan(cohort_id, inv, bid=bk["bid"], ask=bk["ask"],
                  bid_size=bk["bid_size"], ask_size=bk["ask_size"],
                  tte=tte, n_extends=n_extends, actions=actions, root=root)

        # MANDATORY TELEMETRY, written on EVERY decision including HOLD.
        # The opposite executable ask is preserved on every tick so time to
        # first executable complement and best executable pair cost can be
        # computed later, even across windows the policy never acted in.
        tel = decision_telemetry(ticker, inv, bk, tte, pl, t=clock(),
                                 n_extends=n_extends)
        BUD.record_telemetry(cohort_id, tel, root=root)
        events.append(tel)

        if pl.get("stop") in ("LOCKED", "HALTED", "RISK_BLOCK"):
            ev("STOP", stop=pl["stop"], reason=pl["reason"],
               inventory=inv.snapshot())
            break
        if pl["action"] == HOLD:
            ev("HOLD", tte=round(tte, 1), bid=str(bk["bid"]),
               ask=str(bk["ask"]), reason=pl["reason"])
            await asyncio.sleep(poll_s)
            continue

        # 3 -- reconcile against the venue BEFORE building an intent, so
        # every cap is evaluated on inventory the venue confirms.
        rec0 = await reconciled_inventory(cohort_id, symbol, root=root)
        if not rec0["ok"]:
            ev("HALTED", why=rec0.get("why"))
            break
        ours = inv.q_up - inv.q_down
        if D(str(rec0["signed"])) != ours:
            BUD.halt(cohort_id, "INVENTORY_DISAGREEMENT",
                     "venue signed %s != ours %s" % (rec0["signed"], ours),
                     root=root)
            ev("HALTED", why="venue %s != ours %s" % (rec0["signed"], ours),
               labels="the engine may not decide against an inventory the "
                      "venue does not confirm")
            break
        intent = intent_of(ticker, inv, pl, n_extends,
                           reconciled_signed=str(rec0["signed"]),
                           ladder_bid=bk["bid"], ladder_ask=bk["ask"],
                           telemetry=bk.get("contract_price_telemetry"))
        before = POLICY["Inventory"](
            q_up=inv.q_up, q_down=inv.q_down, c_up=inv.c_up,
            c_down=inv.c_down, fees=inv.fees, rebates=inv.rebates,
            fills=list(inv.fills))
        req_before = POLICY["required_complement_price"](inv, QUANTITY)

        try:
            res = await submit(cohort_id, symbol, ticker, intent, root=root,
                               slot_hook=slot_hook)
        except Exception as exc:                               # noqa: BLE001
            ev("SUBMIT_REFUSED", why=str(exc), intent=intent)
            break
        ev("SUBMITTED", status=res["status"], slot=res.get("slot"),
           intent=intent, tte=round(tte, 1), venue=res.get("venue"))
        if res["status"] in ("AMBIGUOUS", "REFUSED_SLOT_NOT_DURABLE"):
            # the slot is spent and is NEVER retried. The window stops so a
            # human can establish what the venue did with it.
            ev("STOP", stop=res["status"], reason=res.get("why"))
            break

        # ---- B: the order's fate must be RESOLVED before the next
        # decision. submit ACK -> venue order status -> active_orders ->
        # next decision, in that order and with no step skipped. A resting
        # order blocks the loop; it does not get a sibling.
        ack_body = ((res.get("venue") or {}).get("body")
                    if isinstance(res.get("venue"), Mapping) else None)
        if not isinstance(ack_body, Mapping):
            BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                     "the place ack carried no readable order object",
                     root=root)
            ev("HALTED", why="unreadable place ack")
            break
        ack = classify_ack(ack_body)
        ev("ACK", slot=res["slot"], state=ack["state"],
           status=ack["status"], filled=str(ack["filled"]),
           why=ack.get("why"))

        # 2 -- A PLACE ACK IS NOT A TERMINAL RESULT. `open` is not
        # NOT_FILLED: CI15M-1's four fills all began exactly there.
        order_id = ack_body.get("orderId") or ack_body.get("id")
        if ack["terminal"]:
            term = {"resolved": True, "filled": ack["filled"],
                    "state": (TERMINAL_FILLED if ack["filled"] > 0
                              else TERMINAL_CANCELLED),
                    "row": ack_body, "from": "ack"}
        else:
            term = await poll_terminal(cohort_id, symbol, order_id, root=root)
            ev("TERMINAL", slot=res["slot"], **{
                k: str(term.get(k)) for k in ("resolved", "state", "filled",
                                              "attempts")})
        if not term["resolved"]:
            ev("STOP", stop="ORDER_UNRESOLVED", reason=term.get("why"))
            break

        if term["state"] == TERMINAL_CANCELLED:
            BUD.record_status(cohort_id, ticker, slot=res["slot"],
                              status="SUBMITTED",
                              detail={"terminal": "CANCELLED"}, root=root)
            ev("NOT_FILLED", slot=res["slot"], outcome="CANCELLED",
               labels="a maker-or-cancel CANCELLATION is a normal outcome. "
                      "The slot is spent, nothing is repriced or chased, and "
                      "the OPEN lifecycle does not reset.")
            await asyncio.sleep(poll_s)
            continue

        row = term.get("row") or ack_body
        fill = {"price": str(row.get("avgExecutionPrice")
                             or row.get("executedPrice")
                             or row.get("price") or intent["price"]),
                "quantity": str(term["filled"]),
                "outcome": row.get("outcome") or intent["outcome"],
                "orderId": order_id}
        chk = check_fill(cohort_id, intent, fill, root=root)
        if not chk["ok"]:
            ev("HALTED", why=chk["halted"])
            break
        filled = {"filled": True, "halted": False, "fill": fill}

        BUD.record_status(cohort_id, ticker, slot=res["slot"], status="FILLED",
                          detail=filled["fill"], root=root)
        inv.add(POLICY["make_fill"](clock(), intent["side"], "maker",
                                    D(str(filled["fill"]["price"])),
                                    D(str(filled["fill"]["quantity"])),
                                    "live " + pl["action"]))
        inv.assert_identities()
        actions += 1

        rec = await reconcile_after_fill(cohort_id, symbol, inv, root=root)
        if rec["halted"]:
            ev("HALTED", why=rec.get("why"), reconcile=rec)
            break
        # THE ACTUAL VENUE FEE, from /v1/mytrades `fee_amount`. The model
        # is NOT substituted when authenticated fee data exists -- CI15M-3
        # measured $0.02 on a taker fill against $0.00 on CI15M-1's maker
        # fills, so the model would have been wrong either way.
        fees = [AD._dec(f.get("fee_amount")) for f in (rec.get("fills") or [])
                if f.get("fee_amount") is not None]
        BUD.record_status(cohort_id, ticker, slot=res["slot"],
                          status="FILLED",
                          detail={"fill": fill,
                                  "actual_venue_fees": [str(x) for x in fees
                                                        if x is not None],
                                  "fee_source": "/v1/mytrades fee_amount",
                                  "fee_model_substituted": False},
                          root=root)
        BUD.record_status(cohort_id, ticker, slot=res["slot"],
                          status="INVENTORY_RECONCILED",
                          detail={"verdict": rec["verdict"],
                                  "venue_signed": rec.get("venue_signed"),
                                  "ours": rec.get("our_signed")}, root=root)
        ev("INVENTORY_RECONCILED", slot=res["slot"], verdict=rec["verdict"],
           venue_signed=rec.get("venue_signed"), ours=rec.get("our_signed"),
           inventory=inv.snapshot())

        if pl["action"] == EXTEND:
            n_extends += 1
            ev("EXTEND_REPORT", slot=res["slot"], **_extend_report(
                before, inv, D(str(filled["fill"]["price"])), req_before,
                POLICY["required_complement_price"](inv, QUANTITY)))
        elif pl["action"] == COMPLETE:
            n_extends = 0
            pc = inv.matched_pair_cost()
            BUD.record_status(cohort_id, ticker, slot=res["slot"],
                              status="COMPLEMENTED",
                              detail={"matched_pair_cost": str(pc)},
                              root=root)
            ev("COMPLEMENT_REPORT", slot=res["slot"],
               effective_matched_pair_cost=(None if pc is None else str(pc)),
               matched=str(inv.matched), residual=str(inv.residual))

        if POLICY["locked"](inv):
            ev("LOCKED", inventory=inv.snapshot(),
               labels="both settlement outcomes positive after modelled "
                      "fees. NOT realized: settlement and confirmed cashflow "
                      "decide that, and neither has happened.")
            break

    snap = inv.snapshot()
    BUD.close_window(cohort_id, ticker,
                     "LOCKED" if POLICY["locked"](inv) else
                     ("RESIDUAL" if inv.fills else "NO_FILL"),
                     detail={"actions": actions, "inventory": snap},
                     root=root)
    return {"ticker": ticker, "symbol": symbol, "events": events,
            "inventory": snap, "n_actions": actions,
            "halted": BUD.halted(cohort_id, root=root),
            "labels": ("SUBMITTED / FILLED / INVENTORY_RECONCILED / "
                       "COMPLEMENTED / RESIDUAL are recorded separately. "
                       "Nothing here is SETTLED or REALIZED.")}


async def _settle_order(cohort_id: str, symbol: str, ticker: str,
                        intent: Mapping[str, Any], res: Mapping[str, Any], *,
                        root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Did that order fill? Read from the venue's ACK and its own records.

    THE ACK IS AUTHORITATIVE ON A MAKER-OR-CANCEL ORDER's immediate fate --
    it either rested, filled or was cancelled, and the venue says which. An
    active-orders read alone CANNOT see a filled order, which produced a
    false "venue disagrees" verdict once already; that lesson is why the ack
    is read first here.
    """
    ack = res.get("venue") or {}
    body = ack.get("order") if isinstance(ack.get("order"), Mapping) else ack
    if not isinstance(body, Mapping):
        BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                 "the place ack carried no readable order object: %r" % (ack,),
                 root=root)
        return {"filled": False, "halted": True, "outcome": "UNREADABLE",
                "why": "unreadable place ack"}
    try:
        got = D(str(body.get("filledQuantity")
                    if body.get("filledQuantity") is not None
                    else body.get("executedQuantity") or "0"))
    except Exception:                                          # noqa: BLE001
        got = D("0")
    status = str(body.get("status") or body.get("state") or "").upper()
    if got <= 0:
        return {"filled": False, "halted": False,
                "outcome": status or "NO_FILL", "why": None}
    price = (body.get("avgExecutionPrice") or body.get("executedPrice")
             or body.get("price") or intent["price"])
    fill = {"price": str(price), "quantity": str(got),
            "outcome": body.get("outcome") or intent["outcome"],
            "orderId": body.get("orderId") or body.get("id"),
            "status": status}
    chk = check_fill(cohort_id, intent, fill, root=root)
    if not chk["ok"]:
        return {"filled": True, "halted": True, "fill": fill,
                "outcome": status, "why": chk["halted"]}
    return {"filled": True, "halted": False, "fill": fill,
            "outcome": status, "why": None}


# ================================================ AUTHENTICATED FILL GATE
#
# WHY THIS REPLACES positions() AS THE POST-FILL GATE FOR BTC15m.
#
# CI15M-3's OPEN filled -- venue ACK, filledQuantity 1, avgExecutionPrice
# 0.34 -- and the probe then HALTED because `metadata.positions()` returned
# a SUCCESSFUL EMPTY list while our ledger held 1 UP. The evidence says the
# halt was the gate's fault, not the venue's:
#
#   * positions() IS a real prediction-inventory surface. It has returned
#     rows carrying totalQuantity/avgPrice/outcome/instrumentId, e.g.
#     GEMI-NFL-2609220015-NYG-LAR-S-LAR6 qty 1.81 avg 0.53, stable across
#     four reads over two hours.
#   * But across every committed authenticated artifact, NO BTC5m or BTC15m
#     contract has ever appeared in a positions row -- not once, against
#     hundreds of filled BTC orders in those same artifacts.
#
# So for these contracts positions() is not the authoritative current-
# inventory surface, and `position_ledger` said as much itself: "model B or
# C -- nothing is held, so it netted or was cashed out".
#
# THE AUTHORITATIVE SURFACE IS THE AUTHENTICATED FILL RECORD, and a proven
# primitive for it already exists and is REUSED here rather than
# reinterpreted -- otherwise this would be a FOURTH interpretation of held
# inventory in one repository:
#
#   fill_reconciliation.authoritative_fills()  order history JOINED to
#                                              mytrades on order_id
#   continuous_probe.recover()                 fills -> Inventory, where
#                                              UNREADABLE is not empty
#
# Every prior Gemini execution, the q10 both-outcomes trade included,
# established held YES/NO this way.
#
# UNKNOWN HANDLING IS NOT WEAKENED ANYWHERE. Each of these HALTs:
# unreadable order history, unreadable mytrades, an unjoinable order needed
# for the reconstruction, an ambiguous fill quantity/side/price, and
# authenticated fills disagreeing with our durable ledger. A successful
# EMPTY positions() result is telemetry and may not contradict a
# successfully reconstructed fill ledger -- but a successful empty FILL
# record does still mean flat, because that surface is the authority.

async def _mytrades(symbol: str) -> Dict[str, Any]:
    """The authenticated trade record. `rows` is None iff UNREADABLE."""
    from src.gemini_prediction_auth import private_rest_request
    try:
        r = await private_rest_request(
            "POST", "/v1/mytrades",
            params={"symbol": symbol, "limit_trades": 500}, timeout=20.0)
    except Exception as exc:                                   # noqa: BLE001
        return {"rows": None, "why": "mytrades raised %s" % exc}
    if getattr(r, "status_code", None) != 200:
        return {"rows": None,
                "why": "mytrades returned %s" % getattr(r, "status_code",
                                                        None)}
    try:
        body = r.json()
    except ValueError:
        return {"rows": None, "why": "mytrades body was not JSON"}
    rows = body.get("trades") if isinstance(body, Mapping) else body
    if not isinstance(rows, list):
        return {"rows": None,
                "why": "mytrades payload carried no trade list; the shape is "
                       "unrecognised and is not read as empty"}
    return {"rows": [t for t in rows if isinstance(t, Mapping)],
            "status": 200}


def authorized_intents(cohort_id: str, *,
                       root: str = cohort.COHORT_ROOT
                       ) -> List[Dict[str, Any]]:
    """Every submission this cohort DURABLY authorized, read from disk.

    These rows are fsynced before the venue is touched and pushed to a
    latch ref, so they survive the crash this exists to handle.
    """
    out = []
    for r in BUD._rows(BUD._paths(cohort_id, root)["ledger"]):
        if r.get("kind") != "PLACE":
            continue
        i = r.get("intent") or {}
        out.append({"slot": r.get("slot"), "ticker": r.get("ticker"),
                    "utc": r.get("utc"), "action": i.get("action"),
                    "side": i.get("side"), "outcome": i.get("outcome"),
                    "price": i.get("price"), "quantity": i.get("quantity")})
    return out


def attribute_fills(cohort_id: str, symbol: str,
                    fills: Sequence[Mapping[str, Any]], *,
                    root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Is every venue fill on this symbol OURS, and authorized?

    THE DISTINCTION THIS DRAWS, and it is the whole point:

      a fill that MATCHES an authorized durable submission is OURS. If our
      local terminal ledger is missing it, that is a CRASH to be recovered
      from, not a disagreement -- the authorized intent is on disk and the
      venue executed it.

      a fill that matches NO authorized submission is FOREIGN. It HALTs.
      We will not absorb a position we cannot account for, whatever its
      symbol.

    WHY MATCHING AND NOT A CLIENT ORDER ID. `transport.place` refuses
    clientOrderId -- the documented OrderRequest schema does not admit it,
    and canary #1 was answered 400 for sending one. So a fill cannot carry
    our tag, and attribution is done against the authorized intents
    instead: same outcome, a fill price AT OR BELOW the submitted limit (a
    capped marketable limit never pays more), a quantity no larger than
    authorized, and a fill no earlier than the authorization. Each intent
    may absorb at most ONE fill, so an extra fill cannot hide behind a
    genuine one.
    """
    intents = [i for i in authorized_intents(cohort_id, root=root)
               if i.get("ticker") and i["ticker"] in symbol]
    used = set()
    attributed, foreign = [], []
    for f in fills:
        px, qty = AD._dec(f.get("price")), AD._dec(f.get("qty"))
        leg = str(f.get("leg") or "").strip().lower()
        hit = None
        for n, i in enumerate(intents):
            if n in used:
                continue
            if str(i.get("outcome") or "").strip().lower() != leg:
                continue
            lim, aq = AD._dec(i.get("price")), AD._dec(i.get("quantity"))
            if lim is None or aq is None or px is None or qty is None:
                continue
            if px > lim or qty > aq:
                continue
            hit = n
            break
        if hit is None:
            foreign.append(f)
        else:
            used.add(hit)
            attributed.append(dict(f, _slot=intents[hit]["slot"]))
    if foreign:
        BUD.halt(cohort_id, "UNATTRIBUTABLE_FILL",
                 "%d venue fill(s) on %s match no authorized submission in "
                 "this cohort: %r. %d authorized intent(s) on record."
                 % (len(foreign), symbol,
                    [{k: f.get(k) for k in ("tid", "order_id", "leg",
                                            "price", "qty")}
                     for f in foreign][:4], len(intents)),
                 root=root)
        return {"ok": False, "attributed": attributed, "foreign": foreign,
                "why": "unattributable venue fill(s)"}
    return {"ok": True, "attributed": attributed, "foreign": [],
            "n_intents": len(intents), "n_attributed": len(attributed)}


async def authenticated_fills(cohort_id: str, symbol: str, *,
                              root: str = cohort.COHORT_ROOT
                              ) -> Dict[str, Any]:
    """The venue's own fill record for this symbol, or a HALT.

    Order history is read WITH include_rows=True -- without it the producer
    omits its `orders` key entirely and a caller sees "readable and empty"
    on every call -- then joined to mytrades through the existing
    `authoritative_fills`.
    """
    from src.gemini_execution import fill_reconciliation as FR
    from src.gemini_execution import metadata

    try:
        hist = await metadata.order_history(
            symbol=symbol, statuses=("filled",), limit=100,
            include_rows=True)
    except Exception as exc:                                   # noqa: BLE001
        BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                 "order history read raised %s" % exc, root=root)
        return {"ok": False, "why": "order history raised %s" % exc}
    oh = AD.OrderHistory.of(hist)
    if not oh.readable:
        BUD.halt(cohort_id, "UNKNOWN_POSITION",
                 "order history unreadable: %s" % oh.why, root=root)
        return {"ok": False, "why": oh.why}

    orders_seen = []
    for grp in (hist.get("per_status") or []):
        for o in (grp.get("orders") or []):
            if isinstance(o, Mapping) and o.get("symbol") == symbol:
                orders_seen.append(o)

    try:
        tr = await _mytrades(symbol)
    except Exception as exc:                                   # noqa: BLE001
        BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                 "mytrades read raised %s" % exc, root=root)
        return {"ok": False, "why": "mytrades raised %s" % exc}
    if tr.get("rows") is None:
        BUD.halt(cohort_id, "UNKNOWN_POSITION",
                 "mytrades unreadable: %s" % tr.get("why"), root=root)
        return {"ok": False, "why": tr.get("why") or "mytrades unreadable"}

    auth = FR.authoritative_fills({"orders_seen": orders_seen,
                                   "trades": tr["rows"]})
    if not auth.get("readable"):
        BUD.halt(cohort_id, "UNKNOWN_POSITION",
                 "authoritative fills unreadable: %s" % auth.get("why"),
                 root=root)
        return {"ok": False, "why": auth.get("why")}

    rows = auth.get("rows") or []
    # every row must be fully determined: an unjoinable order, an
    # unresolved side or an unreadable qty/price is UNKNOWN, never zero.
    for r in rows:
        if not r.get("leg_resolved"):
            BUD.halt(cohort_id, "SIDE_MAPPING_DISAGREEMENT",
                     "fill %r carries no resolvable side; its order could "
                     "not be joined" % r.get("tid"), root=root)
            return {"ok": False, "why": "unjoinable fill side"}
        if r.get("order_id") is None:
            BUD.halt(cohort_id, "UNKNOWN_POSITION",
                     "fill %r has no order_id to join on" % r.get("tid"),
                     root=root)
            return {"ok": False, "why": "fill without an order_id"}
        for k in ("qty", "price"):
            if AD._dec(r.get(k)) is None:
                BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                         "fill %r has an unreadable %s: %r"
                         % (r.get("tid"), k, r.get(k)), root=root)
                return {"ok": False, "why": "unreadable fill %s" % k}
    # EVERY fill must be positively attributable to an authorized durable
    # submission. A foreign fill HALTs; a crash-missing one of ours does
    # not, because its authorization is on disk.
    att = attribute_fills(cohort_id, symbol, rows, root=root)
    if not att["ok"]:
        return {"ok": False, "why": att["why"], "foreign": att["foreign"]}
    return {"ok": True, "rows": att["attributed"], "n": len(rows),
            "orders_joined": auth.get("orders_joined"),
            "attribution": {"intents": att["n_intents"],
                            "attributed": att["n_attributed"]},
            "source": auth.get("source")}


def inventory_from_fills(rows: Sequence[Mapping[str, Any]], symbol: str,
                         *, auth_id: str = "") -> Dict[str, Any]:
    """Authenticated fills -> inventory, through the PROVEN primitive.

    `continuous_probe.recover` does the reconstruction (and refuses a None
    fill list), then the result is carried into the `fi_core.Inventory` the
    frozen policy reasons about. BOTH reconstructions are compared: if the
    proven primitive and the policy accounting disagree we do not
    understand our own inventory and may not decide on it.
    """
    from src.gemini_execution import continuous_probe as CP
    # THE TWO PRIMITIVES HAVE NEVER BEEN COMPOSED BEFORE, and composing
    # them surfaced it: `authoritative_fills` emits `leg` lower-case
    # ("yes"/"no"), while `CP.Inventory.apply_fill` raises "leg must be YES
    # or NO". Neither producer is modified -- the mismatch is normalised
    # HERE, at the boundary, which is the whole lesson of the adapter
    # sweep. A row whose leg is neither is refused rather than guessed.
    cp_rows = []
    for r in rows:
        leg = str(r.get("leg") or "").strip().lower()
        if leg not in ("yes", "no"):
            return {"ok": False,
                    "why": "fill %r carries leg %r, which is neither yes nor "
                           "no" % (r.get("tid"), r.get("leg"))}
        cp_rows.append(dict(r, leg=leg.upper()))
    cp_inv, why = CP.recover(auth_id, symbol, cp_rows)
    if cp_inv is None:
        return {"ok": False, "why": why}
    inv = POLICY["Inventory"]()
    for r in rows:
        side = "UP" if str(r.get("leg")).lower() == "yes" else "DOWN"
        inv.add(POLICY["make_fill"](0.0, side,
                                    ("maker" if r.get("maker") == "maker"
                                     else "taker"),
                                    D(str(r["price"])), D(str(r["qty"])),
                                    "authenticated venue fill"))
    inv.assert_identities()
    if (cp_inv.yes_qty != inv.q_up) or (cp_inv.no_qty != inv.q_down):
        return {"ok": False,
                "why": ("the proven primitive and the policy accounting "
                        "disagree: CP yes/no %s/%s vs policy UP/DOWN %s/%s"
                        % (cp_inv.yes_qty, cp_inv.no_qty, inv.q_up,
                           inv.q_down))}
    return {"ok": True, "inventory": inv, "cp": cp_inv,
            "up_qty": inv.q_up, "down_qty": inv.q_down,
            "up_cost": inv.c_up, "down_cost": inv.c_down,
            "matched": inv.matched,
            "matched_pair_cost": inv.matched_pair_cost(),
            "residual_side": inv.residual_side,
            "residual_qty": inv.residual,
            "avg_up": inv.avg_up, "avg_down": inv.avg_down,
            "n_fills": len(rows)}
