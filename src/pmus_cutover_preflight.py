"""The authorization to cut over is only as good as the moment it was read.

WHY THIS EXISTS. 2026-09-13T16:33:43Z I triggered a live cutover on a flat
state I had measured at 16:21:48Z and a funnel I had read at 16:29:10Z. In
between, aec-cs2-mzp-ezt-2026-09-13 filled 8.11 NO contracts for $3.0443. The
deploy log I wrote said "no position held". It was true when measured and
stale when acted on.

Nothing was lost -- the boot gate swept zero orphaned orders and all eleven
assertions passed -- but that was the gates working, not the operator. A
several-minute-old flat-state observation must never authorize a live
deployment again.

THE RULE THIS ENCODES:

  1. The authoritative read (positions, open orders, risk set) happens INSIDE
     the action window, not before it.
  2. The authorization carries the instant it was taken and REFUSES once it is
     older than the window.
  3. Between authorization and action the state is read AGAIN, and any change
     ABORTS rather than proceeding.

Time can only ever INVALIDATE an authorization here; it can never create one.
A stale read is refused, an unreadable venue is refused, and an authorization
that cannot be re-established at the moment of action is refused. That is the
same shape as the re-arm gate in pmus_l5_continuous_session: every condition
established by this process from a fresh read, and anything that cannot be
established FAILS.

This module is READ-ONLY. It never places, cancels or repices an order, and it
never deploys anything -- it answers one question, "may the operator act right
now", and the operator still has to obey it.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from typing import Any, Callable, Mapping, Optional

# The action window. A cutover authorization older than this is refused.
# Deliberately short: reading positions and open orders costs two GETs, so
# there is no reason for the read to be minutes old. 16:33Z cost us a stale
# twelve minutes; sixty seconds is the outer bound of "immediately".
CUTOVER_MAX_AGE_S = 60.0

# The fields whose CHANGE between authorization and action aborts the cutover.
# Ordered, so a diff reads the same way every time.
MATERIAL_STATE_KEYS = ("held", "open_orders_ours", "open_orders_foreign",
                       "risk_bearing", "unmatched_usd")


def _norm(state: Mapping[str, Any]) -> dict:
    """The comparable projection of a venue read. Lists are sorted so that
    ordering noise from the venue is never mistaken for a state change, and
    dollars are Decimal-normalised so '3.0443' and '3.04430' agree."""
    out: dict = {}
    for k in MATERIAL_STATE_KEYS:
        v = state.get(k)
        if isinstance(v, (list, tuple, set)):
            out[k] = sorted(str(x) for x in v)
        elif k == "unmatched_usd":
            try:
                # normalize() then 'f' so 3.0443 and 3.04430 compare EQUAL
                # while 3.0443 and 3.0543 do not, and 100 stays '100' rather
                # than becoming '1E+2'.
                out[k] = format(D(str(v if v is not None else "0")).normalize(), "f")
            except Exception:              # noqa: BLE001
                out[k] = None              # unreadable is not zero
        else:
            out[k] = v
    return out


def read_state(reader: Callable[[], Mapping[str, Any]],
               now: Optional[Callable[[], float]] = None) -> dict:
    """Take one authoritative read and STAMP it. A read that raises is a
    refusal carrying its reason, never an empty state that looks clean."""
    clock = now or time.time
    t0 = clock()
    try:
        raw = reader()
    except Exception as exc:               # noqa: BLE001
        return {"ok": False, "read_at": t0, "error": repr(exc)[:200],
                "state": None, "reason": "venue_read_failed"}
    if not isinstance(raw, Mapping):
        return {"ok": False, "read_at": t0, "state": None,
                "reason": "venue_read_malformed"}
    return {"ok": True, "read_at": t0, "state": _norm(raw), "raw": dict(raw)}


def authorize(reader: Callable[[], Mapping[str, Any]],
              require_flat: bool = True,
              now: Optional[Callable[[], float]] = None) -> dict:
    """May a cutover be authorized from a read taken RIGHT NOW?

    `require_flat` is the strict boundary: no held market, no order of ours,
    no foreign order, nothing risk-bearing. Callers deploying at the revised
    boundary (a dormant quarantined residual) pass require_flat=False and take
    responsibility for saying so -- but the residual is still REPORTED either
    way, so a reader of the deploy log can see what was carried."""
    r = read_state(reader, now=now)
    if not r["ok"]:
        return {"authorized": False, "reason": r.get("reason"), "read_at": r["read_at"],
                "error": r.get("error"), "state": None}
    st = r["state"]
    checks = [
        {"check": "no_open_orders_ours", "ok": st.get("open_orders_ours") == 0,
         "got": st.get("open_orders_ours"), "want": 0},
        {"check": "no_open_orders_foreign", "ok": st.get("open_orders_foreign") == 0,
         "got": st.get("open_orders_foreign"), "want": 0},
    ]
    if require_flat:
        checks += [
            {"check": "no_held_market", "ok": st.get("held") == [],
             "got": st.get("held"), "want": []},
            {"check": "no_risk_bearing_market", "ok": st.get("risk_bearing") == [],
             "got": st.get("risk_bearing"), "want": []},
        ]
    failed = [c["check"] for c in checks if not c["ok"]]
    return {"authorized": not failed, "failed": failed, "checks": checks,
            "read_at": r["read_at"], "state": st, "raw": r.get("raw"),
            "require_flat": require_flat,
            "labels": "authorization is valid only inside CUTOVER_MAX_AGE_S of "
                      "read_at, and only until the state is re-read unchanged"}


def may_act(auth: Mapping[str, Any],
            reader: Callable[[], Mapping[str, Any]],
            max_age_s: float = CUTOVER_MAX_AGE_S,
            now: Optional[Callable[[], float]] = None) -> dict:
    """THE GATE AT THE MOMENT OF ACTION. Re-reads the venue and compares.

    Three ways to be refused and they are kept distinct, because an operator
    needs to know WHICH one happened:
      stale      -- the authorization aged past the window; take a fresh one
      changed    -- the world moved between authorizing and acting; ABORT and
                    re-evaluate, because the reason for authorizing may be gone
      unreadable -- we could not establish the state; that is never a pass
    """
    clock = now or time.time
    t = clock()
    if not auth.get("authorized"):
        return {"act": False, "reason": "not_authorized",
                "failed": list(auth.get("failed") or [])}
    age = t - float(auth.get("read_at") or 0.0)
    if age > max_age_s:
        return {"act": False, "reason": "stale_authorization",
                "age_s": round(age, 3), "max_age_s": max_age_s}
    fresh = read_state(reader, now=now)
    if not fresh["ok"]:
        return {"act": False, "reason": "recheck_" + str(fresh.get("reason")),
                "age_s": round(age, 3), "error": fresh.get("error")}
    before, after = auth.get("state") or {}, fresh["state"]
    diff = {k: {"before": before.get(k), "after": after.get(k)}
            for k in MATERIAL_STATE_KEYS if before.get(k) != after.get(k)}
    if diff:
        return {"act": False, "reason": "state_changed_since_authorization",
                "age_s": round(age, 3), "diff": diff,
                "labels": "ABORT and re-evaluate: the state the authorization "
                          "was granted on no longer exists"}
    return {"act": True, "age_s": round(age, 3), "state": after,
            "labels": "authoritative read taken inside the action window and "
                      "re-confirmed unchanged at the moment of action"}
