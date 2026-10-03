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
AUTHORIZED_SHA = "bf14f29523f02a72187cea5d39102aa09c457122"

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
    checks = {
        "head_known": head is not None,
        "head_is_authorized_sha": head == AUTHORIZED_SHA,
        "head_is_authorized_or_descendant": bool(descendant),
        "policy_fingerprint_matches":
            fingerprint() == AUTHORIZED_POLICY_FINGERPRINT,
        "authorization_installed":
            arming.EXPECTED_AUTH_ID == PROBE_AUTH_ID == "OWNER-2026-10-03-"
                                                        "GEMINI-CI15M-1",
        "quantity_is_one": QUANTITY == D("1"),
        "consumed_slots_zero": st["order_slots_consumed"] == 0,
        "confirmed_realized_loss_zero":
            D(st["confirmed_realized_principal_loss"]) == D("0"),
        "no_halt": st["halted"] is None,
        "not_expired": st["expired"] is None,
        "taker_path_shut":
            payload.SEMANTICS_PROBE_AUTH_ID != arming.EXPECTED_AUTH_ID,
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
                 "src/gemini_execution/ci15m_probe.py",
                 "tests/test_ci15m_probe.py",
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
    sha_ok = checks["head_is_authorized_sha"] or (
        checks["head_is_authorized_or_descendant"]
        and bool(diff_files) and set(diff_files) <= AUTH_ONLY)
    checks["sha_condition"] = bool(sha_ok)
    required = [k for k in checks
                if k not in ("head_is_authorized_sha",
                             "head_is_authorized_or_descendant")]
    failed = [k for k in required if not checks[k]]
    return {
        "ok": not failed, "failed": failed, "checks": checks,
        "head": head, "authorized_sha": AUTHORIZED_SHA,
        "diff_from_authorized_sha": diff_files,
        "policy_fingerprint": fingerprint(),
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
    """The decision book: UP bid/ask from the presented contract, and the
    DISPLAYED SIZE from the venue's own ladder.

    TWO READS, DELIBERATELY. `outcome_book` publishes prices only and carries
    no depth at all, so "is there anything to trade against for q=1?" cannot
    be answered from it. The ladder answers that, and the complement identity
    between the two is CHECKED rather than assumed -- a book that violates it
    is not one we understand well enough to send an order into.
    """
    from src.gemini_execution import metadata
    ob = metadata.outcome_book(symbol)
    if not ob.get("readable"):
        return {"readable": False, "why": ob.get("why"), "stage": "prices"}
    if not ob.get("identity_consistent"):
        return {"readable": False, "stage": "identity",
                "why": "the complement identity does not hold: %s"
                       % ob.get("complement_identity"),
                "semantic_contradiction": True}
    dep = metadata.contract_depth(symbol)
    if not dep.get("readable"):
        return {"readable": False, "why": dep.get("why"), "stage": "depth"}
    return {
        "readable": True,
        "bid": D(ob["yes"]["bid"]), "ask": D(ob["yes"]["ask"]),
        "bid_size": dep["bids"].get("best_qty"),
        "ask_size": dep["asks"].get("best_qty"),
        "up_acquisition": ob["yes"]["ask"],
        "down_acquisition": str(D("1") - D(ob["yes"]["bid"])),
        "snapshot_age_s": dep.get("snapshot_age_s"),
        "raw": {"prices": ob, "depth": {"bids": dep["bids"],
                                        "asks": dep["asks"]}},
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
    rows = got.get("positions") if isinstance(got, Mapping) else None
    if rows is None and isinstance(got, Mapping):
        rows = got.get("result") or got.get("data")
    return reconcile_or_halt(cohort_id, symbol, inv, rows, root=root)


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
        if pl.get("stop") in ("LOCKED", "HALTED", "RISK_BLOCK"):
            ev("STOP", stop=pl["stop"], reason=pl["reason"],
               inventory=inv.snapshot())
            break
        if pl["action"] == HOLD:
            ev("HOLD", tte=round(tte, 1), bid=str(bk["bid"]),
               ask=str(bk["ask"]), reason=pl["reason"])
            await asyncio.sleep(poll_s)
            continue

        intent = intent_of(ticker, inv, pl, n_extends)
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

        # ---- what actually happened to it, read back from the venue
        filled = await _settle_order(cohort_id, symbol, ticker, intent, res,
                                     root=root)
        if filled["halted"]:
            ev("HALTED", why=filled["why"])
            break
        if not filled["filled"]:
            ev("NOT_FILLED", slot=res["slot"], outcome=filled["outcome"],
               labels="a maker-or-cancel CANCELLATION is a normal outcome. "
                      "The slot is spent, nothing is repriced or chased, and "
                      "the next decision comes from the next book.")
            await asyncio.sleep(poll_s)
            continue

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
