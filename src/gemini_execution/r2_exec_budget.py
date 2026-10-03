"""The R2 execution-validation cohort: 10 numbered slots, durable, no rollover.

WHAT THIS BOUNDS, and what it deliberately does not. It bounds how many times
we may touch the venue, and it survives a process death. It does NOT decide
whether an opportunity qualifies -- that is frozen R2, imported from
`analysis/gemini/info_edge/r2_frozen.py` and never reimplemented here.

THE PROPERTIES THE OWNER ASKED FOR, and where each one lives:

  max 10 submitted probes          BUDGET, fixed at creation, no replenishment
  max 1 submission per market      `markets.jsonl`, append-only
  one outstanding order            checked against the VENUE, in the driver
  realized-loss stop $8.00         `settled.jsonl`, CONFIRMED cashflow only
  structural max $10.20            10 x (price 1.00 + fee 0.02); proven below
  expires at probe 10 or the gate  `expired()`
  slot consumed BEFORE submission  `consume()` fsyncs, then the caller sends
  ambiguous consumes the slot      the row is already on disk; never retried
  restart recovers consumed slots  the count is READ FROM DISK, never held
  no automatic increase            there is no function that raises BUDGET

WHY A TORN ROW COUNTS AS CONSUMED. If the process dies mid-write, the slot
may or may not have reached the venue. Counting it as spent can waste one
probe; counting it as free can send an eleventh. `cohort._consumed_in`
already makes that choice correctly and is reused rather than re-derived.

WHY THE COUNT IS NEVER CACHED. A cached remaining-budget is a budget that
resets when the process does. Every reader re-counts the file.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import cohort

#: THE BUDGET. Ten submissions, and nothing in this module can raise it.
BUDGET = 10

#: q is fixed. Not a parameter, not a minimum, not a starting point.
QUANTITY = D("1")

#: the worst a single q=1 probe can cost: pay the whole price, plus the fee
#: the venue's own formula charges at that price.
MAX_PRICE = D("1.00")
MAX_FEE_AT_ANY_PRICE = D("0.02")        # ceil(0.07*1*P*(1-P)) peaks at P=0.5
MAX_LOSS_PER_PROBE = MAX_PRICE + MAX_FEE_AT_ANY_PRICE          # 1.02

#: the structural ceiling, derived rather than asserted.
STRUCTURAL_MAX_LOSS = MAX_LOSS_PER_PROBE * BUDGET              # 10.20

#: the stop that bites first, on CONFIRMED realized principal loss only.
REALIZED_LOSS_STOP = D("8.00")

#: the statistical gate. Its arrival expires this cohort.
STAT_GATE_WINDOWS = 100

MARKETS = "markets.jsonl"
SETTLED = "settled.jsonl"


class BudgetRefused(RuntimeError):
    """A bound said no. Never retried, never relaxed, never widened."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_durable(path: str, row: Dict[str, Any]) -> None:
    """Append one row and FSYNC IT. The caller may only touch the venue after
    this returns, so a crash leaves the slot spent rather than free."""
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
        os.fsync(d)          # the directory entry too, or the file may vanish
    finally:
        os.close(d)


def _paths(cohort_id: str, root: str) -> Dict[str, str]:
    d = cohort.directory(cohort_id, root=root)
    return {"dir": d,
            "ledger": os.path.join(d, cohort.LEDGER),
            "markets": os.path.join(d, MARKETS),
            "settled": os.path.join(d, SETTLED)}


def create(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
           note: str = "") -> Dict[str, Any]:
    """Bring the cohort into being ONCE. A second call is a refusal."""
    cohort.create(cohort_id, BUDGET, root=root,
                  note=note or "R2 EXECUTION VALIDATION ONLY. Not a "
                               "profitability estimate. Frozen R2 qualification "
                               "imported, never reimplemented.")
    return state(cohort_id, root=root)


def consumed(cohort_id: str, *, root: str = cohort.COHORT_ROOT) -> int:
    """Slots spent, READ FROM DISK every time. Never cached, never reset."""
    return cohort._consumed_in(_paths(cohort_id, root)["ledger"])


def remaining(cohort_id: str, *, root: str = cohort.COHORT_ROOT) -> int:
    return max(0, BUDGET - consumed(cohort_id, root=root))


def _rows(path: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def finished_markets(cohort_id: str, *,
                     root: str = cohort.COHORT_ROOT) -> Dict[str, str]:
    """Every market this cohort has finished with, and why.

    A MISSED market consumes NO slot and is STILL finished: we do not return
    to a market whose qualifying offer we already failed to take.
    """
    return {r["ticker"]: r.get("outcome", "?")
            for r in _rows(_paths(cohort_id, root)["markets"])
            if r.get("ticker")}


def realized_principal_loss(cohort_id: str, *,
                            root: str = cohort.COHORT_ROOT) -> D:
    """CONFIRMED realized principal loss, in dollars, as a positive number.

    Only rows whose cashflow is confirmed from the account are counted. A
    resolution side is not cash; a mark is not cash; a formula-derived fee is
    not cash. Anything unconfirmed contributes ZERO, which is the conservative
    direction for a loss stop only because the stop is checked against
    confirmed losses AND the structural ceiling independently bounds the rest.
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


def expired(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
            stat_gate_windows: Optional[int] = None) -> Optional[str]:
    """Why this cohort is over, or None. Checked before every probe."""
    if remaining(cohort_id, root=root) <= 0:
        return ("BUDGET_EXHAUSTED: %d of %d slots consumed. There is no "
                "replenishment." % (consumed(cohort_id, root=root), BUDGET))
    loss = realized_principal_loss(cohort_id, root=root)
    if loss >= REALIZED_LOSS_STOP:
        return ("REALIZED_LOSS_STOP: confirmed realized principal loss $%s "
                ">= $%s." % (loss, REALIZED_LOSS_STOP))
    if (stat_gate_windows is not None
            and stat_gate_windows >= STAT_GATE_WINDOWS):
        return ("STATISTICAL_GATE_REACHED: %d >= %d independent R2 windows. "
                "Probing pauses for the single statistical read."
                % (stat_gate_windows, STAT_GATE_WINDOWS))
    return None


def may_probe(cohort_id: str, ticker: str, *,
              root: str = cohort.COHORT_ROOT,
              stat_gate_windows: Optional[int] = None) -> Dict[str, Any]:
    """Every budget bound, answered before any venue read is acted on."""
    why = expired(cohort_id, root=root, stat_gate_windows=stat_gate_windows)
    if why:
        return {"ok": False, "why": why}
    fin = finished_markets(cohort_id, root=root)
    if ticker in fin:
        return {"ok": False,
                "why": "MARKET_ALREADY_FINISHED: %s was finished as %s. One "
                       "probe per distinct market, and a MISSED market is "
                       "not revisited." % (ticker, fin[ticker])}
    return {"ok": True, "slot": consumed(cohort_id, root=root),
            "remaining_before": remaining(cohort_id, root=root)}


def finish_market(cohort_id: str, ticker: str, outcome: str, detail: Any = None,
                  *, root: str = cohort.COHORT_ROOT) -> None:
    """Close a market for this cohort. Consumes NO slot."""
    _append_durable(_paths(cohort_id, root)["markets"],
                    {"utc": _now(), "ticker": ticker, "outcome": outcome,
                     "detail": detail})


def consume_slot(cohort_id: str, ticker: str, intent: Dict[str, Any], *,
                 root: str = cohort.COHORT_ROOT,
                 stat_gate_windows: Optional[int] = None) -> Dict[str, Any]:
    """Spend ONE numbered slot, durably, BEFORE the venue is touched.

    Returns the persisted row. The caller may submit only after this returns.
    An AMBIGUOUS send therefore finds its slot already spent, which is the
    whole point: it is never retried.
    """
    gate = may_probe(cohort_id, ticker, root=root,
                     stat_gate_windows=stat_gate_windows)
    if not gate["ok"]:
        raise BudgetRefused(gate["why"])
    if D(str(intent.get("quantity", "0"))) != QUANTITY:
        raise BudgetRefused("q must be exactly %s; got %r"
                            % (QUANTITY, intent.get("quantity")))
    price = D(str(intent.get("price", "0")))
    if not (D("0") < price <= MAX_PRICE):
        raise BudgetRefused("price %s is outside (0, %s]" % (price, MAX_PRICE))
    slot = gate["slot"]
    row = {"kind": "PLACE", "slot": slot, "utc": _now(),
           "cohort_id": cohort_id, "ticker": ticker,
           "intent": intent,
           "max_loss_this_probe": str(MAX_LOSS_PER_PROBE),
           "labels": ("this row is written and fsynced BEFORE the venue sees "
                      "anything, so a crash or an ambiguous send leaves the "
                      "slot SPENT rather than free. It is never retried.")}
    _append_durable(_paths(cohort_id, root)["ledger"], row)
    # the market is finished the moment we commit to submitting into it
    finish_market(cohort_id, ticker, "SUBMITTED", {"slot": slot}, root=root)
    return row


def record_settlement(cohort_id: str, ticker: str, *,
                      slot: int, realized_principal_net: Any,
                      cashflow_confirmed: bool, evidence: Any,
                      fee_observed: Any = None,
                      root: str = cohort.COHORT_ROOT) -> None:
    """Record settled cashflow. `cashflow_confirmed` must come from an
    AUTHENTICATED account read -- never from a resolution side."""
    _append_durable(_paths(cohort_id, root)["settled"],
                    {"utc": _now(), "ticker": ticker, "slot": slot,
                     "realized_principal_net": str(realized_principal_net),
                     "cashflow_confirmed": bool(cashflow_confirmed),
                     "fee_observed": (None if fee_observed is None
                                      else str(fee_observed)),
                     "evidence": evidence})


def state(cohort_id: str, *, root: str = cohort.COHORT_ROOT,
          stat_gate_windows: Optional[int] = None) -> Dict[str, Any]:
    """Everything a report needs. Opens no mutation path."""
    base = cohort.state(cohort_id, root=root)
    used = consumed(cohort_id, root=root)
    loss = realized_principal_loss(cohort_id, root=root)
    fin = finished_markets(cohort_id, root=root)
    return {
        "label": "GEMINI_R2_EXEC_BUDGET_V1",
        "cohort_id": cohort_id,
        "purpose": "R2 EXECUTION VALIDATION ONLY -- not a profitability "
                   "estimate, and never pooled with the statistical gate.",
        "budget": BUDGET, "consumed": used, "remaining": BUDGET - used,
        "quantity_fixed": str(QUANTITY),
        "max_loss_per_probe": str(MAX_LOSS_PER_PROBE),
        "structural_max_loss": str(STRUCTURAL_MAX_LOSS),
        "realized_loss_stop": str(REALIZED_LOSS_STOP),
        "confirmed_realized_principal_loss": str(loss),
        "markets_finished": fin,
        "markets_submitted": sum(1 for v in fin.values() if v == "SUBMITTED"),
        "markets_missed": sum(1 for v in fin.values() if v != "SUBMITTED"),
        "expired": expired(cohort_id, root=root,
                           stat_gate_windows=stat_gate_windows),
        "cohort_manifest": base,
        "no_replenishment": "there is no function in this module that raises "
                            "BUDGET; a further probe needs a new cohort, "
                            "created by a human.",
    }
