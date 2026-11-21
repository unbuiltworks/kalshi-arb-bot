"""The window a bounded probe WOULD run, decided and rendered without a socket.

SCOPE, STATED FIRST BECAUSE IT IS THE POINT. This module contains no order
transport and cannot acquire one: it opens no connection, imports no session,
imports no asyncio, and never consumes an authorization latch. It answers two
questions that must be answerable BEFORE anyone writes a driver, and that are
worth answering whether or not one is ever written:

  1. does the venue still agree with the assumptions the probe was frozen on?
  2. what would the frozen controller actually DO across a whole window?

WHY THE FIRST QUESTION IS A COMPARISON AND NOT A HEALTH CHECK. A preflight
that merely succeeds proves the venue answered, not that it answered the same
way. The envelope -- action size 0.01, residual 0.04, spend $0.15 -- was sized
against specific contract increments; a contract with different ones passes
every liveness check and still makes the envelope mean something else.
`check_frozen` therefore compares field by field, and a value it could not
read is a DISCREPANCY rather than a pass, because "unread" and "agrees" are
different answers and only one of them is evidence.

WHY THE SECOND QUESTION IS ANSWERABLE WITHOUT A VENUE. `continuous_probe`
already decides a turn from a market state; what it could never do is take the
next turn, because nothing advanced the clock or the inventory. `walk_window`
does exactly that and nothing more: it feeds the controller a sequence of
states and, when a rung is marked filled by the CALLER's own scenario, applies
it. THE FILLS ARE THE SCENARIO'S, NEVER THE MODULE'S. Nothing here decides
that a quote traded, and no output of this module is evidence about the
venue -- it is evidence about the CONTROLLER, which is what a review of a
bounded probe actually needs and what no amount of staring at `plan()` gives
you.

Everything it reports is labelled RENDERED or SIMULATED. None of it is a
fill, a shadow result, or anything that may appear in a P&L.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.gemini_execution import continuous_probe as CP, spec

LABEL = "GEMINI_L5_CONTINUOUS_PREFLIGHT_V1"

#: the readiness facts the frozen probe was reviewed against
FROZEN_ASSUMPTIONS: Dict[str, Any] = {
    "quantityMinimum": "0.01",
    "quantityIncrement": "0.01",
    "priceMinimum": "0.01",
    "priceIncrement": "0.01",
    "contract_status": "active",
    "market_state": "open",
    "terms_accepted": True,
    "maker_tif": spec.WS_TIF_MAKER_ONLY,
}

AGREES = "AGREES"
DISCREPANT = "DISCREPANT"


def check_frozen(preflight: Mapping[str, Any]
                 ) -> Tuple[bool, List[Dict[str, Any]]]:
    """Compare a fresh preflight to the assumptions the probe was frozen on.

    Returns (ok, discrepancies). A MISSING value is a discrepancy: an unread
    field is never treated as agreeing.
    """
    bad: List[Dict[str, Any]] = []
    for key, want in FROZEN_ASSUMPTIONS.items():
        got = preflight.get(key, None)
        if got is None:
            bad.append({"field": key, "expected": str(want), "got": None,
                        "why": ("not present in the fresh preflight; an "
                                "unread value is never read as agreeing")})
        elif str(got) != str(want):
            bad.append({"field": key, "expected": str(want), "got": str(got),
                        "why": ("the venue's current answer differs from the "
                                "assumption the envelope was sized against")})
    return (not bad), bad


def preflight_now(symbol: str,
                  *, readers: Optional[Mapping[str, Callable]] = None
                  ) -> Dict[str, Any]:
    """The venue's CURRENT answers for this contract. Reads only.

    `readers` is injectable so this is testable without a venue; unsupplied it
    uses the same read paths the settlement gate already uses.
    """
    r = dict(readers or {})
    if not r:
        from src.gemini_execution import metadata as MD
        r = {"terms_for": MD.terms_for, "terms_status": MD.terms_status,
             "outcome_book": MD.outcome_book}
    out: Dict[str, Any] = {"label": LABEL, "symbol": symbol, "t": time.time()}
    try:
        terms = r["terms_for"](symbol) or {}
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, readable=False,
                   why="contract metadata unreadable: %s" % type(exc).__name__)
    out.update({
        "readable": bool(terms),
        "quantityMinimum": terms.get("quantityMinimum"),
        "quantityIncrement": terms.get("quantityIncrement"),
        "priceMinimum": terms.get("priceMinimum"),
        "priceIncrement": terms.get("priceIncrement"),
        "contract_status": terms.get("contract_status") or terms.get("status"),
        "market_state": terms.get("market_state") or terms.get("marketState"),
        "expiry": terms.get("expiry") or terms.get("expiryDate"),
        "maker_tif": spec.WS_TIF_MAKER_ONLY,
    })
    # TERMS: THE READER MAY BE ASYNC, AND THIS FUNCTION IS NOT.
    # MD.terms_status is a coroutine function. Calling it here without
    # awaiting returns a coroutine object whose .get() raises AttributeError,
    # which the except below turned into terms_accepted=None -- a DISCREPANCY,
    # so the probe failed closed on every single run and could never trade.
    # That is safe and it is still wrong, and it cost a live authorization to
    # find because no test ever passed the REAL async reader.
    #
    # A sync function cannot await, so the fix is not here: an async caller
    # must await the read and pass the VALUE in `readers`. What this does is
    # make the mistake loud instead of silent.
    import inspect as _inspect
    try:
        ts = r["terms_status"]()
        if _inspect.iscoroutine(ts) or _inspect.isawaitable(ts):
            try:
                ts.close()
            except Exception:                                  # noqa: BLE001
                pass
            raise TypeError(
                "terms_status returned a coroutine. preflight_now is "
                "synchronous and cannot await it; an async caller must await "
                "the read and pass the VALUE in readers['terms_status'].")
        ts = ts or {}
        out["terms_accepted"] = bool(ts.get("hasAcceptedLatest"))
        out["terms_version"] = ts.get("acceptedVersion")
    except Exception as exc:                                   # noqa: BLE001
        out["terms_accepted"] = None
        out["terms_why"] = "terms unreadable: %s: %s" % (type(exc).__name__,
                                                         exc)
    try:
        out["book"] = r["outcome_book"](symbol)
    except Exception as exc:                                   # noqa: BLE001
        out["book"] = {"readable": False, "why": type(exc).__name__}
    ok, bad = check_frozen(out)
    out["frozen_comparison"] = {
        "verdict": AGREES if ok else DISCREPANT,
        "discrepancies": bad,
        "labels": ("a preflight that merely SUCCEEDS is not the same claim as "
                   "one that AGREES; only the second licenses the envelope"),
    }
    return out


# ------------------------------------------------ the controller, turn by turn

def walk_window(symbol: str, states: Sequence[Mapping[str, Any]], *,
                auth_id: str = "RENDER-ONLY",
                fills: Optional[Callable[[int, Mapping[str, Any]],
                                         Sequence[Mapping[str, Any]]]] = None,
                inv: Optional[CP.Inventory] = None) -> Dict[str, Any]:
    """Run the FROZEN controller across a sequence of states and render it.

    `states` are market states as `continuous_probe.state_from_venue` builds
    them -- supplied by the caller, from a recorded book or a constructed
    scenario. `fills(i, turn)` is the caller's scenario saying which of that
    turn's decided actions filled; returning nothing is a window in which
    nothing traded, which is the honest default.

    NOTHING HERE IS EVIDENCE ABOUT THE VENUE. It is evidence about the
    controller: which actions it classifies as OPEN / EXTEND / COMPLETE, where
    the OSCILLATOR withholds a rung versus where the ENVELOPE does, when the
    end-of-window floors bite, and what the matched/residual split looks like
    at the end. That is the thing a review of a bounded probe needs and that
    reading `plan()` does not give you.
    """
    inv = inv if inv is not None else CP.Inventory()
    guard = CP.WindowGuard(symbol, auth_id=auth_id)
    turns: List[Dict[str, Any]] = []
    classified = {CP.OPEN: 0, CP.EXTEND: 0, CP.COMPLETE: 0}
    held_by: Dict[str, int] = {}

    for i, st in enumerate(states):
        if guard.state != CP.PROBE_RUNNING:
            break
        out = guard.step(dict(st), inv, now=st.get("t_utc"))
        frames = CP.frames_for(out, symbol)
        for a in (out.get("actions") or []):
            if a.get("action") in classified:
                classified[a["action"]] += 1
            # THE BUDGET ONLY EXISTS IF THE CALLER SPENDS IT. `step()` passes
            # actions_taken INTO plan() and never increments it; only
            # `record()` does, and nothing in the frozen artifact calls
            # record(). So MAX_ACTIONS is a caller obligation, not a control
            # -- see budget_is_caller_enforced() below. A correct driver calls
            # record() per action, and this walk is a correct driver.
            guard.record(a)
        for h in (out.get("holds") or []):
            key = str(h.get("held_by") or h.get("why") or "unstated")[:60]
            held_by[key] = held_by.get(key, 0) + 1

        applied = []
        for f in (fills(i, out) if fills else []):
            inv.apply_fill(f["leg"], f["qty"], f["price"],
                           source="SIMULATED_SCENARIO")
            applied.append({"leg": f["leg"], "qty": str(f["qty"]),
                            "price": str(f["price"])})

        turns.append({
            "i": i, "tte_s": st.get("tte_s"),
            "window_phase": out.get("window_phase"),
            "probe_state": out.get("probe_state"),
            "actions_taken": out.get("actions_taken"),
            "decided": [{"action": a.get("action"), "leg": a.get("leg"),
                         "price": str(a.get("price")),
                         "qty": str(a.get("qty"))}
                        for a in (out.get("actions") or [])],
            "holds": out.get("holds"),
            "stop": out.get("stop"),
            "frames_rendered": len(frames),
            "simulated_fills": applied,
            "inventory_after": inv.report(),
            "residual_after": str(inv.residual()),
        })
        if out.get("stop"):
            break

    return {
        "label": LABEL, "symbol": symbol, "turns": turns,
        "turns_run": len(turns),
        "classified": classified,
        "held_by": held_by,
        "actions_taken": guard.actions_taken,
        "max_actions": CP.MAX_ACTIONS,
        "probe_state": guard.state,
        "stopped_because": guard.stopped_because,
        "final_inventory": inv.report(),
        "residual": str(inv.residual()),
        "matched": str(inv.matched()),
        "spend": str(inv.spend()),
        "bounds_held": bounds_held(inv, guard.actions_taken),
        "budget_enforcement": budget_is_caller_enforced(),
        "labels": ("RENDERED and SIMULATED. Fills are the CALLER's scenario, "
                   "never a claim that anything traded. Nothing here is a "
                   "shadow result, a fill, or admissible in any P&L."),
    }


def budget_is_caller_enforced() -> Dict[str, Any]:
    """Is MAX_ACTIONS a control, or a description a driver has to honour?

    MEASURED, not asserted: this walks `WindowGuard`'s own AST and reports
    which of its methods increments `actions_taken`. `step()` reads the count
    and passes it to `plan()`; only `record()` raises it, and no module in the
    frozen artifact calls `record()`. A driver that forgets it therefore runs
    an unbounded number of actions while `WindowGuard` reports 0 taken and
    every bound check passes -- which is the same shape as the defect commit
    6342171e fixed for this very field, still present one level up.

    It is reported rather than fixed here because `continuous_probe` is the
    frozen, reviewed artifact and this module does not get to edit it.
    """
    import ast
    src = open(CP.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    incs, calls_record = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WindowGuard":
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef):
                    incs[fn.name] = any(
                        isinstance(n, ast.AugAssign)
                        and isinstance(n.target, ast.Attribute)
                        and n.target.attr == "actions_taken"
                        for n in ast.walk(fn))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"):
            calls_record.add("continuous_probe")
    return {
        "increments_actions_taken": incs,
        "step_increments": bool(incs.get("step")),
        "record_increments": bool(incs.get("record")),
        "record_called_inside_the_artifact": bool(calls_record),
        "verdict": ("CALLER_ENFORCED" if not incs.get("step")
                    else "SELF_ENFORCED"),
        "why": ("step() passes actions_taken to plan() and does not raise it. "
                "Only record() raises it, and the frozen artifact never calls "
                "record(). MAX_ACTIONS therefore binds only a driver that "
                "opts in, and a driver that forgets sees every bound check "
                "pass against a count of 0."),
        "labels": ("a finding about the CONTROLLER, reported not repaired: "
                   "continuous_probe is the frozen artifact and this module "
                   "does not edit it."),
    }


def bounds_held(inv: CP.Inventory, actions_taken: int) -> Dict[str, Any]:
    """Did the walk stay inside every hard bound? Reported, never enforced
    here -- enforcement is `continuous_probe`'s, and a second copy of a limit
    is a second place for it to drift."""
    checks = {
        "actions": (actions_taken <= CP.MAX_ACTIONS,
                    "%d <= %d" % (actions_taken, CP.MAX_ACTIONS)),
        "residual": (abs(inv.residual()) <= CP.MAX_RESIDUAL,
                     "%s <= %s" % (abs(inv.residual()), CP.MAX_RESIDUAL)),
        "imbalance": (inv.imbalance() <= CP.MAX_IMBALANCE,
                      "%s <= %s" % (inv.imbalance(), CP.MAX_IMBALANCE)),
        "yes_qty": (inv.yes_qty <= CP.MAX_YES,
                    "%s <= %s" % (inv.yes_qty, CP.MAX_YES)),
        "no_qty": (inv.no_qty <= CP.MAX_NO,
                   "%s <= %s" % (inv.no_qty, CP.MAX_NO)),
        "spend": (inv.spend() <= CP.MAX_TOTAL_SPEND,
                  "%s <= %s" % (inv.spend(), CP.MAX_TOTAL_SPEND)),
    }
    return {"all_held": all(ok for ok, _ in checks.values()),
            "checks": {k: {"held": ok, "measured": m}
                       for k, (ok, m) in checks.items()}}


def render(symbol: str, states: Sequence[Mapping[str, Any]], **kw: Any) -> str:
    """The walk as one JSON line, for a reviewer or an evidence artifact."""
    return json.dumps(walk_window(symbol, states, **kw), default=str,
                      separators=(",", ":"))
