"""ONE operational status for the whole live path, with the precise reason.

WHY THIS EXISTS. Readiness has repeatedly been declared from unit tests and
then failed at activation on something no test covered: a stale remote halt
record, a window budget variable left at 1, an authorization that expired
while a document was being reviewed, a verification step that consumed the
window it was verifying. Each was visible in evidence the system already
emits; nothing put that evidence in one place and named the single state.

This module does not collect evidence and cannot reach a venue or a service.
It CLASSIFIES evidence the caller gathers, so the same classifier runs against
live records and against fixtures in tests.

THE FIVE STATES, in strict precedence order. The first that matches wins,
because reporting the weaker one hides the stronger:

  RECONCILIATION_BLOCKED  unresolved execution state -- uncertain orders, an
                          unbooked terminal, quarantine, or an unreadable
                          ledger. Needs a human; nothing else matters until
                          it clears.
  HALTED                  submissions are refused: the local env lock, the
                          remote durable latch, or a halt record that is
                          stale/missing/unparseable. A stale record is a HALT,
                          never an unknown.
  AUTH_EXPIRED            no authorization in force, or it expired, or the
                          window is outside the authorized set, or the window
                          budget is spent.
  RUNNING                 armed inside an authorized window.
  READY                   everything above is clear and activation is the only
                          remaining act.

UNKNOWN IS NEVER READY. Any check whose answer could not be established
yields its blocking state, not a pass. A missing field is treated as
unestablished rather than as good news.
"""
from typing import Any, Dict, List, Mapping, Optional, Tuple

RECONCILIATION_BLOCKED = "RECONCILIATION_BLOCKED"
HALTED = "HALTED"
AUTH_EXPIRED = "AUTH_EXPIRED"
RUNNING = "RUNNING"
READY = "READY"

#: strict precedence; index 0 wins over index 1 and so on.
PRECEDENCE: Tuple[str, ...] = (RECONCILIATION_BLOCKED, HALTED, AUTH_EXPIRED,
                               RUNNING, READY)


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def classify(ev: Mapping[str, Any]) -> Dict[str, Any]:
    """The single status, its reason, and every check that contributed.

    `ev` is a flat mapping of live evidence. Every key is OPTIONAL and a
    missing one is UNESTABLISHED, which blocks -- see the module docstring.
    """
    checks: List[Dict[str, Any]] = []

    def note(name: str, ok: Optional[bool], detail: Any = None,
             blocks: Optional[str] = None) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail,
                       "blocks": blocks})

    blockers: List[Tuple[str, str]] = []

    # ---------------------------------------- 1. reconciliation
    uncertain = ev.get("orders_uncertain")
    if uncertain is None:
        note("orders_uncertain", True, "null = no uncertainty recorded")
    else:
        note("orders_uncertain", False, uncertain, RECONCILIATION_BLOCKED)
        blockers.append((RECONCILIATION_BLOCKED,
                         "orders_uncertain:%s" % (uncertain,)))
    if "reconciled" in ev and not _truthy(ev.get("reconciled")):
        note("reconciled", False, ev.get("reconciled"),
             RECONCILIATION_BLOCKED)
        blockers.append((RECONCILIATION_BLOCKED, "NOT_RECONCILED"))
    for key, label in (("pending_terminal", "PENDING_TERMINAL"),
                       ("quarantine", "QUARANTINED")):
        n = _num(ev.get(key))
        if n is None:
            note(key, None, "unestablished", RECONCILIATION_BLOCKED)
            blockers.append((RECONCILIATION_BLOCKED, "%s_UNKNOWN" % label))
        elif n > 0:
            note(key, False, n, RECONCILIATION_BLOCKED)
            blockers.append((RECONCILIATION_BLOCKED,
                             "%s:%g" % (label, n)))
        else:
            note(key, True, 0)

    # ---------------------------------------- 2. halt
    if _truthy(ev.get("halt_local_env")):
        note("halt_local_env", False, True, HALTED)
        blockers.append((HALTED, "L5_LIVE_HALT_ASSERTED"))
    else:
        note("halt_local_env", True, False)

    latch = ev.get("halt_latch")
    gate_why = ev.get("halt_gate_why")
    permitted = ev.get("halt_gate_permitted")
    if permitted is None and latch is None:
        note("remote_halt", None, "unestablished", HALTED)
        blockers.append((HALTED, "HALT_STATE_UNESTABLISHED"))
    elif permitted is False or (latch is not None
                                and str(latch).upper() == "HALT"):
        note("remote_halt", False, gate_why or latch, HALTED)
        blockers.append((HALTED, "REMOTE_%s" % (gate_why or "HALT_LATCHED")))
    else:
        note("remote_halt", True, {"latch": latch, "why": gate_why})

    # ---------------------------------------- 3. authorization
    appr = ev.get("campaign_approval_state")
    if appr is None:
        note("campaign_approval", None, "unestablished", AUTH_EXPIRED)
        blockers.append((AUTH_EXPIRED, "APPROVAL_UNESTABLISHED"))
    elif str(appr).lower() != "approved":
        note("campaign_approval", False, appr, AUTH_EXPIRED)
        blockers.append((AUTH_EXPIRED, "APPROVAL_%s" % str(appr).upper()))
    else:
        note("campaign_approval", True, appr)

    exp = _num(ev.get("auth_expires_in_s"))
    if exp is None:
        note("auth_deadline", None, "unestablished", AUTH_EXPIRED)
        blockers.append((AUTH_EXPIRED, "DEADLINE_UNESTABLISHED"))
    elif exp <= 0:
        note("auth_deadline", False, exp, AUTH_EXPIRED)
        blockers.append((AUTH_EXPIRED, "AUTHORIZATION_EXPIRED:%gs" % exp))
    else:
        note("auth_deadline", True, exp)

    wr = _num(ev.get("windows_remaining"))
    if wr is not None and wr <= 0 and not _truthy(ev.get("window_in_progress")):
        note("window_budget", False, wr, AUTH_EXPIRED)
        blockers.append((AUTH_EXPIRED, "WINDOW_BUDGET_SPENT"))
    else:
        note("window_budget", True, wr)

    wref = ev.get("window_refusal")
    if wref:
        note("authorized_window", False, wref, AUTH_EXPIRED)
        blockers.append((AUTH_EXPIRED, str(wref)))
    else:
        note("authorized_window", True, None)

    # ---------------------------------------- 4. running / ready
    armed = _truthy(ev.get("armed"))
    note("armed", True, armed)

    # the first state in PRECEDENCE that has a blocker wins
    by_state = {}
    for state, why in blockers:
        by_state.setdefault(state, []).append(why)
    for state in PRECEDENCE:
        if state in by_state:
            return {
                "label": "L5_OPERATIONAL_STATUS",
                "status": state,
                "reason": by_state[state][0],
                "all_reasons": by_state[state],
                "other_blockers": {s: v for s, v in by_state.items()
                                   if s != state},
                "checks": checks,
                "labels": ("the FIRST state in strict precedence wins; "
                           "reporting a weaker one would hide this. UNKNOWN "
                           "is never READY."),
            }
    status = RUNNING if armed else READY
    return {
        "label": "L5_OPERATIONAL_STATUS",
        "status": status,
        "reason": None,
        "all_reasons": [],
        "other_blockers": {},
        "checks": checks,
        "labels": ("no blocker in any state. RUNNING means armed inside an "
                   "authorized window; READY means activation is the only "
                   "remaining act."),
    }
