"""The bounded BTC15m CONTINUOUS-INVENTORY probe: durable windows and slots.

WHAT THIS BOUNDS. How many times we may touch the venue, and it survives a
process death. It does NOT decide anything: every decision comes from the
frozen policy in `analysis/gemini/info_edge/btc15m_shadow_policy.py`, which
is imported and never reimplemented.

WHAT THIS PROBE IS FOR. To validate that OPEN -> EXTEND -> COMPLEMENT -> LOCK
EXECUTES CORRECTLY against the real BTC15m venue, with actual fills and
authoritative inventory reconciliation. It is an execution-mechanics audit.
It CANNOT and MUST NOT be read as evidence that EXTEND is profitable: three
windows of q=1 cannot distinguish any edge from zero, and the prospective
EXTEND-vs-NO-EXTEND shadow is kept entirely separate from it.

THE OWNER'S BOUNDS, and where each one lives:

  one BTC15m market at a time         `open_window()` refuses a second
  q = 1 per action                    QUANTITY; checked in `consume_slot`
  OPEN + 2 EXTENDs = 3 unmatched      MAX_UNMATCHED_SHARES
  max acquisition price 0.90          MAX_ACQUISITION_PRICE (= policy's
                                      HARD_MAX_PRICE, imported, not restated)
  max unmatched principal $2.70       MAX_UNMATCHED_PRINCIPAL, derived
  max 2 EXTENDs                       the frozen policy's own
                                      MAX_EXTENDS_BEFORE_COMPLEMENT
  max 6 submitted orders per window   ORDERS_PER_WINDOW
  complements only toward LOCK        the frozen policy's `want_complement`
  one outstanding order               checked against the VENUE, in the driver
  ambiguous consumes its slot         the row is fsynced BEFORE the send
  restart recovers consumed slots     every count is READ FROM DISK
  $2.00 confirmed realized loss stop  `settled.jsonl`, confirmed cashflow only
  3 completed live windows            WINDOW_BUDGET
  no automatic increase               no function here raises any bound

WHY A TORN ROW COUNTS AS CONSUMED. If the process dies mid-write the order
may or may not have reached the venue. Counting it spent can waste one slot;
counting it free can send an extra order. `cohort._consumed_in` already makes
that choice correctly and is reused rather than re-derived.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import cohort
from src.gemini_execution.continuous_probe import MAX_ACTIONS as CP_MAX_ACTIONS
from src.gemini_execution.ci15m_policy import POLICY

LABEL = "GEMINI_CI15M_BUDGET_V1"

#: completed live windows. There is no replenishment.
WINDOW_BUDGET = 3

#: submitted orders per window. OPEN + 2 EXTENDs + up to 3 complement
#: attempts; a maker-or-cancel CANCELLATION still spends one.
ORDERS_PER_WINDOW = 6

#: THE TOTAL ORDER CEILING, AND IT IS NOT MINE TO SET. The pre-existing live
#: envelope caps mutations at `continuous_probe.MAX_ACTIONS`, and
#: `cohort.create` refuses a budget above it. 3 windows x 6 orders would be
#: 18, so the envelope binds BEFORE the owner's per-window cap does, and it is
#: honoured rather than widened: a window may still spend its 6, but the probe
#: as a whole cannot exceed this. A full 6-order window therefore leaves 6 for
#: the remaining two. This is TIGHTER than what was authorized, deliberately.
TOTAL_ORDER_BUDGET = min(WINDOW_BUDGET * ORDERS_PER_WINDOW, CP_MAX_ACTIONS)

#: q is fixed. Not a parameter, not a minimum, not a starting point.
QUANTITY = D("1")

#: IMPORTED from the frozen policy rather than restated, so the two can never
#: drift apart. A local copy of this number would be a second policy.
MAX_ACQUISITION_PRICE = POLICY["HARD_MAX_PRICE"]            # 0.90
MAX_EXTENDS = POLICY["MAX_EXTENDS_BEFORE_COMPLEMENT"]       # 2
MAX_PAIR_COST = POLICY["MAX_PAIR_COST"]                     # 0.96

#: OPEN + MAX_EXTENDS, all unmatched in the worst case.
MAX_UNMATCHED_SHARES = QUANTITY * (1 + MAX_EXTENDS)         # 3
MAX_UNMATCHED_PRINCIPAL = MAX_UNMATCHED_SHARES * MAX_ACQUISITION_PRICE  # 2.70

#: the venue's taker fee formula peaks at P=0.5: ceil(0.07*1*0.25) = 0.02.
MAX_FEE_PER_ORDER = D("0.02")
MAX_FEES_PER_WINDOW = MAX_FEE_PER_ORDER * ORDERS_PER_WINDOW            # 0.12

#: the structural ceiling, DERIVED. Stated per window and in total; the
#: realized-loss stop below bites well before the total.
STRUCTURAL_MAX_LOSS_PER_WINDOW = MAX_UNMATCHED_PRINCIPAL + MAX_FEES_PER_WINDOW
STRUCTURAL_MAX_LOSS = STRUCTURAL_MAX_LOSS_PER_WINDOW * WINDOW_BUDGET   # 8.46

#: the stop that bites first, on CONFIRMED realized principal loss only.
REALIZED_LOSS_STOP = D("2.00")

WINDOWS = "windows.jsonl"
SETTLED = "settled.jsonl"
HALT = "HALT"

#: the statuses this probe reports, in order. Nothing skips forward and
#: nothing later is claimed on the evidence of something earlier.
STATUSES = ("SUBMITTED", "FILLED", "INVENTORY_RECONCILED",
            "COMPLEMENTED", "RESIDUAL", "SETTLED", "REALIZED")

#: every condition that stops the probe dead. A HALT is never cleared by
#: code; a human removes the file.
HALT_CONDITIONS = {
    "INVENTORY_DISAGREEMENT": "our inventory and the venue's authoritative "
                              "position for this symbol differ",
    "SIDE_MAPPING_DISAGREEMENT": "the venue's eventOutcome for a fill does "
                                 "not map to the leg we intended",
    "FILL_ABOVE_LIMIT": "a fill printed at a price worse than the limit we "
                        "submitted",
    "UNKNOWN_POSITION": "the venue reports a position we have no record of, "
                        "or we cannot read the position at all",
    "SEMANTIC_CONTRADICTION": "the venue's answer contradicts what our "
                              "parser asserted about the book or the order",
    "POLICY_FINGERPRINT_MISMATCH": "the frozen policy bytes are not the ones "
                                   "this authorization was granted against",
    "QUANTITY_NOT_ONE": "a submission was constructed at a quantity other "
                        "than exactly 1",
    "EXTEND_BUDGET_EXCEEDED": "a third EXTEND was reached before a complement",
    "LOCKED_MUTATION": "an action was proposed on an inventory that is "
                       "already LOCKED",
}


class BudgetRefused(RuntimeError):
    """A bound said no. Never retried, never relaxed, never widened."""


class Halted(RuntimeError):
    """A HALT file is present. Only a human clears it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_durable(path: str, row: Dict[str, Any]) -> None:
    """Append one row and FSYNC IT, file and directory. The caller may only
    touch the venue after this returns, so a crash leaves the slot SPENT."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(row, sort_keys=True, default=str) + "\n"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    d = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(d)
    finally:
        os.close(d)


def _paths(cohort_id: str, root: str) -> Dict[str, str]:
    d = cohort.directory(cohort_id, root=root)
    return {"dir": d,
            "ledger": os.path.join(d, cohort.LEDGER),
            "windows": os.path.join(d, WINDOWS),
            "settled": os.path.join(d, SETTLED),
            "halt": os.path.join(d, HALT)}


def _rows(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue            # a torn row is still a spent slot
    except OSError:
        pass
    return out


# ------------------------------------------------------------------- HALT
def halted(cohort_id: str, *, root: str = cohort.COHORT_ROOT) -> Optional[str]:
    """The HALT reason, or None. AN UNREADABLE HALT FILE IS A HALT."""
    p = _paths(cohort_id, root)["halt"]
    if not os.path.exists(p):
        return None
    try:
        return open(p, "r", encoding="utf-8").read().strip() or "HALTED"
    except OSError:
        return "HALTED (halt file present but unreadable -- fail closed)"


def halt(cohort_id: str, condition: str, detail: Any = None, *,
         root: str = cohort.COHORT_ROOT) -> str:
    """Write the HALT. Idempotent, durable, and never cleared by code."""
    if condition not in HALT_CONDITIONS:
        condition = "SEMANTIC_CONTRADICTION"
    p = _paths(cohort_id, root)
    os.makedirs(p["dir"], exist_ok=True)
    msg = "%s %s: %s | detail=%s" % (_now(), condition,
                                     HALT_CONDITIONS[condition], detail)
    fd = os.open(p["halt"], os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, (msg + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return msg


# ------------------------------------------------------------- the counts
def create(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
           note: str = "") -> Dict[str, Any]:
    """Bring the cohort into being ONCE. A second call is a refusal."""
    cohort.create(cohort_id, TOTAL_ORDER_BUDGET, root=root,
                  note=note or ("BTC15m CONTINUOUS-INVENTORY EXECUTION "
                                "VALIDATION ONLY. Not a profitability "
                                "estimate and never pooled with the "
                                "prospective EXTEND shadow."))
    return state(cohort_id, root=root)


def consumed(cohort_id: str, *, root: str = cohort.COHORT_ROOT) -> int:
    """Order slots spent, READ FROM DISK every time. Never cached."""
    return cohort._consumed_in(_paths(cohort_id, root)["ledger"])


def submissions_in_window(cohort_id: str, ticker: str, *,
                          root: str = cohort.COHORT_ROOT) -> int:
    """Orders submitted into THIS market, read from disk."""
    return sum(1 for r in _rows(_paths(cohort_id, root)["ledger"])
               if r.get("kind") == "PLACE" and r.get("ticker") == ticker)


def windows(cohort_id: str, *,
            root: str = cohort.COHORT_ROOT) -> Dict[str, str]:
    """Every window this probe has opened, and its disposition."""
    out: Dict[str, str] = {}
    for r in _rows(_paths(cohort_id, root)["windows"]):
        if r.get("ticker"):
            out[r["ticker"]] = r.get("outcome", "?")
    return out


def open_window_ticker(cohort_id: str, *,
                       root: str = cohort.COHORT_ROOT) -> Optional[str]:
    """The one market currently open, or None. ONE AT A TIME."""
    for tk, outcome in windows(cohort_id, root=root).items():
        if outcome == "OPEN":
            return tk
    return None


def completed_windows(cohort_id: str, *,
                      root: str = cohort.COHORT_ROOT) -> int:
    return sum(1 for v in windows(cohort_id, root=root).values()
               if v not in ("OPEN",))


def realized_principal_loss(cohort_id: str, *,
                            root: str = cohort.COHORT_ROOT) -> D:
    """CONFIRMED realized principal loss, as a positive number.

    Only rows whose cashflow is confirmed from an AUTHENTICATED account read
    count. A resolution side is not cash; a mark is not cash; a
    formula-derived fee is not cash. Unconfirmed contributes ZERO, which is
    safe only because the structural ceiling bounds the rest independently.
    """
    tot = D("0")
    for r in _rows(_paths(cohort_id, root)["settled"]):
        if not r.get("cashflow_confirmed"):
            continue
        try:
            net = D(str(r["realized_principal_net"]))
        except Exception:                                      # noqa: BLE001
            continue
        if net < 0:
            tot += -net
    return tot


def expired(cohort_id: str, *,
            root: str = cohort.COHORT_ROOT) -> Optional[str]:
    """Why this probe is over, or None. Checked before every submission."""
    h = halted(cohort_id, root=root)
    if h:
        return "HALTED: %s" % h
    used = consumed(cohort_id, root=root)
    if used >= TOTAL_ORDER_BUDGET:
        return ("TOTAL_ORDER_BUDGET_EXHAUSTED: %d of %d order slots "
                "consumed. This is the pre-existing live envelope's ceiling "
                "and is not raised here." % (used, TOTAL_ORDER_BUDGET))
    done = completed_windows(cohort_id, root=root)
    if done >= WINDOW_BUDGET:
        return ("WINDOW_BUDGET_EXHAUSTED: %d of %d live windows completed. "
                "There is no replenishment." % (done, WINDOW_BUDGET))
    loss = realized_principal_loss(cohort_id, root=root)
    if loss >= REALIZED_LOSS_STOP:
        return ("REALIZED_LOSS_STOP: confirmed realized principal loss $%s "
                ">= $%s." % (loss, REALIZED_LOSS_STOP))
    return None


def open_window(cohort_id: str, ticker: str, *,
                root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Bind the ONE market. Refuses while another window is open."""
    why = expired(cohort_id, root=root)
    if why:
        raise BudgetRefused(why)
    cur = open_window_ticker(cohort_id, root=root)
    if cur is not None:
        raise BudgetRefused(
            "ONE_MARKET_AT_A_TIME: %s is still open; %s is refused"
            % (cur, ticker))
    seen = windows(cohort_id, root=root)
    if ticker in seen:
        raise BudgetRefused(
            "WINDOW_ALREADY_USED: %s was already dispositioned as %s and is "
            "never revisited" % (ticker, seen[ticker]))
    row = {"utc": _now(), "ticker": ticker, "outcome": "OPEN"}
    _append_durable(_paths(cohort_id, root)["windows"], row)
    return row


def close_window(cohort_id: str, ticker: str, outcome: str, detail: Any = None,
                 *, root: str = cohort.COHORT_ROOT) -> None:
    """Disposition the window. Consumes NO order slot."""
    if outcome == "OPEN":
        raise BudgetRefused("close_window cannot reopen a window")
    _append_durable(_paths(cohort_id, root)["windows"],
                    {"utc": _now(), "ticker": ticker, "outcome": outcome,
                     "detail": detail})


def may_submit(cohort_id: str, ticker: str, *,
               root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Every budget bound, answered before any venue write."""
    why = expired(cohort_id, root=root)
    if why:
        return {"ok": False, "why": why}
    cur = open_window_ticker(cohort_id, root=root)
    if cur != ticker:
        return {"ok": False,
                "why": "MARKET_NOT_OPEN: the open window is %r, not %s"
                       % (cur, ticker)}
    n = submissions_in_window(cohort_id, ticker, root=root)
    if n >= ORDERS_PER_WINDOW:
        return {"ok": False,
                "why": "ORDERS_PER_WINDOW: %d of %d already submitted into "
                       "%s" % (n, ORDERS_PER_WINDOW, ticker)}
    return {"ok": True, "slot": consumed(cohort_id, root=root),
            "submitted_in_window": n,
            "window_orders_remaining": ORDERS_PER_WINDOW - n}


def consume_slot(cohort_id: str, ticker: str, intent: Dict[str, Any], *,
                 root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Spend ONE numbered slot, durably, BEFORE the venue is touched.

    An AMBIGUOUS send therefore finds its slot already spent, which is the
    whole point: it is never retried. There is deliberately NO release,
    refund, unconsume or rollback function in this module.
    """
    gate = may_submit(cohort_id, ticker, root=root)
    if not gate["ok"]:
        raise BudgetRefused(gate["why"])
    q = D(str(intent.get("quantity", "0")))
    if q != QUANTITY:
        raise BudgetRefused("q must be exactly %s; got %r"
                            % (QUANTITY, intent.get("quantity")))
    price = D(str(intent.get("price", "0")))
    action = str(intent.get("action", ""))
    if action in ("OPEN", "EXTEND"):
        if not (D("0") < price <= MAX_ACQUISITION_PRICE):
            raise BudgetRefused(
                "%s price %s is outside (0, %s]"
                % (action, price, MAX_ACQUISITION_PRICE))
        unmatched = D(str(intent.get("unmatched_shares_after", "0")))
        if unmatched > MAX_UNMATCHED_SHARES:
            raise BudgetRefused(
                "unmatched shares after this action would be %s > %s"
                % (unmatched, MAX_UNMATCHED_SHARES))
        principal = D(str(intent.get("unmatched_principal_after", "0")))
        if principal > MAX_UNMATCHED_PRINCIPAL:
            raise BudgetRefused(
                "unmatched principal after this action would be $%s > $%s"
                % (principal, MAX_UNMATCHED_PRINCIPAL))
    if action == "EXTEND":
        n = D(str(intent.get("extends_before", "0")))
        if n >= MAX_EXTENDS:
            raise BudgetRefused(
                "EXTEND_BUDGET: %s extensions already taken before a "
                "complement; the maximum is %s" % (n, MAX_EXTENDS))
    if action == "COMPLETE":
        pair = intent.get("pair_cost_after")
        if pair is None or D(str(pair)) > MAX_PAIR_COST:
            raise BudgetRefused(
                "COMPLEMENT pair cost after would be %s; the frozen "
                "discipline requires <= %s" % (pair, MAX_PAIR_COST))
    if action not in ("OPEN", "EXTEND", "COMPLETE"):
        raise BudgetRefused("unknown action %r; only OPEN, EXTEND and "
                            "COMPLETE may submit" % action)
    slot = gate["slot"]
    row = {"kind": "PLACE", "slot": slot, "utc": _now(),
           "cohort_id": cohort_id, "ticker": ticker,
           "status": "SUBMITTED", "intent": intent,
           "policy_fingerprint": POLICY["fingerprint"],
           "labels": ("fsynced BEFORE the venue sees anything, so a crash or "
                      "an ambiguous send leaves the slot SPENT rather than "
                      "free. It is never retried. SUBMITTED is not FILLED.")}
    _append_durable(_paths(cohort_id, root)["ledger"], row)
    return row


def record_status(cohort_id: str, ticker: str, *, slot: int, status: str,
                  detail: Any = None,
                  root: str = cohort.COHORT_ROOT) -> None:
    """Advance one slot's status. Each label is recorded SEPARATELY; nothing
    later is inferred from something earlier."""
    if status not in STATUSES:
        raise BudgetRefused("unknown status %r; expected one of %s"
                            % (status, list(STATUSES)))
    _append_durable(_paths(cohort_id, root)["ledger"],
                    {"kind": "STATUS", "slot": slot, "utc": _now(),
                     "ticker": ticker, "status": status, "detail": detail})


def record_settlement(cohort_id: str, ticker: str, *,
                      realized_principal_net: Any, cashflow_confirmed: bool,
                      evidence: Any, fee_observed: Any = None,
                      root: str = cohort.COHORT_ROOT) -> None:
    """Record settled cashflow. `cashflow_confirmed` must come from an
    AUTHENTICATED account read -- never from a resolution side."""
    _append_durable(_paths(cohort_id, root)["settled"],
                    {"utc": _now(), "ticker": ticker,
                     "realized_principal_net": str(realized_principal_net),
                     "cashflow_confirmed": bool(cashflow_confirmed),
                     "fee_observed": (None if fee_observed is None
                                      else str(fee_observed)),
                     "evidence": evidence})


def state(cohort_id: str, *,
          root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Everything a report needs. Opens no mutation path."""
    w = windows(cohort_id, root=root)
    return {
        "label": LABEL,
        "cohort_id": cohort_id,
        "purpose": ("BTC15m CONTINUOUS-INVENTORY EXECUTION VALIDATION ONLY. "
                    "It does not answer whether EXTEND is profitable, and it "
                    "is never pooled with the prospective EXTEND shadow."),
        "window_budget": WINDOW_BUDGET,
        "windows": w,
        "window_open": open_window_ticker(cohort_id, root=root),
        "windows_completed": completed_windows(cohort_id, root=root),
        "orders_per_window": ORDERS_PER_WINDOW,
        "total_order_budget": TOTAL_ORDER_BUDGET,
        "total_order_budget_source": ("continuous_probe.MAX_ACTIONS, the "
                                      "pre-existing live envelope; it binds "
                                      "before the per-window cap and is not "
                                      "widened here"),
        "order_slots_consumed": consumed(cohort_id, root=root),
        "quantity_fixed": str(QUANTITY),
        "max_acquisition_price": str(MAX_ACQUISITION_PRICE),
        "max_extends": str(MAX_EXTENDS),
        "max_pair_cost": str(MAX_PAIR_COST),
        "max_unmatched_shares": str(MAX_UNMATCHED_SHARES),
        "max_unmatched_principal": str(MAX_UNMATCHED_PRINCIPAL),
        "structural_max_loss_per_window": str(STRUCTURAL_MAX_LOSS_PER_WINDOW),
        "structural_max_loss": str(STRUCTURAL_MAX_LOSS),
        "realized_loss_stop": str(REALIZED_LOSS_STOP),
        "confirmed_realized_principal_loss":
            str(realized_principal_loss(cohort_id, root=root)),
        "policy_fingerprint": POLICY["fingerprint"],
        "halted": halted(cohort_id, root=root),
        "halt_conditions": HALT_CONDITIONS,
        "statuses": list(STATUSES),
        "expired": expired(cohort_id, root=root),
        "no_replenishment": ("no function in this module raises any bound. A "
                             "further probe needs a new cohort, created by a "
                             "human."),
    }
