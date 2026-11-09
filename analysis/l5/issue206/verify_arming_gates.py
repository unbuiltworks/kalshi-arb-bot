"""Every arming gate, walked against ONE exact market, read-only.

WHAT THIS IS FOR. The owner asked to refresh the durable campaign
authorization, bind it to one exact BTC15m market, and verify that all
arming gates pass. The first of those three is not mine to do and the
module says so in its own first paragraph:

    "Claude cannot write it, so Claude cannot approve a campaign -- which
     is the invariant `pmus_q1_executor` states as 'Claude never arms
     this; the operator does.'"

The approval lives in an object only the owner can write. So this tool
does the other two completely: it BINDS a document to one exact market and
it VERIFIES the gates, by driving the REAL `authorize_arming` rather than
by re-describing it.

HOW THE WALK WORKS, and why it is a walk. `authorize_arming` raises on the
OUTERMOST unmet precondition and names it, so a single call reports one
gate and says nothing about the forty behind it. This calls it repeatedly:
each refusal is recorded, the one environmental fact that gate reads is
then set to its satisfied value, and the call is repeated. The result is an
ORDERED LEDGER of every gate between here and a claim, each marked with
what satisfied it.

THE FACTS ARE DECLARED, NOT DISCOVERED. `FIXES` below maps a refusal to
the exact input that answers it and to who owns that input. Nothing is
guessed and no gate is bypassed: a gate this tool cannot satisfy is
reported as BLOCKED with its owner named, which is the useful output.

READ-ONLY, AND IT TOUCHES NOTHING REAL.
  * no Railway variable is read or written -- the document is passed in;
  * the claim ledger and the window ledger are temporary directories, so
    no production claim is consumed and no owner window is spent;
  * `reserve=False` throughout, so even the campaign verdict spends
    nothing;
  * it submits nothing and arms nothing. It cannot: it holds no transport.

A PASS HERE IS NOT AN AUTHORIZATION. It says the document and the market
agree and that the gates reading them are satisfied. The gates that read
the LIVE SERVICE -- the halt latch, venue reconciliation, order
uncertainty, the authenticated book -- can only be verified against a
running deployment's heartbeat, and this says so for each one rather than
pretending otherwise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src import l5_campaign_approval as CA        # noqa: E402
from src import l5_live_authorization as AUTH     # noqa: E402
from src import l5_live_execution as LX           # noqa: E402
from src import pmus_auth as Q1                   # noqa: E402

#: who owns the input a gate reads. The point of the column.
OWNER = "owner"           # only the owner can set it
SERVICE = "service"       # only a running deployment can evidence it
DOC = "document"          # the approval document itself
CODE = "code"             # a code constant or a local fact

#: gates this walk cannot satisfy by setting an input, because what they
#: read is the CLOCK. Reported the first time they are reached rather than
#: labelled "satisfied" and then looping to STUCK.
TIME_GATES = {
    "WINDOW_NOT_OPEN":
        "the window has not started. Arming cannot be pre-verified past "
        "this point: the boundary refuses a window that is not open, so "
        "this gate is answered by waiting, not by configuring.",
    "WINDOW_CLOSING":
        "inside the pre-close margin. Too late to arm this window.",
}

#: refusal -> (what it reads, who owns it, how this walk satisfies it)
FIXES: Dict[str, Tuple[str, str, str]] = {
    "SHADOW_ONLY_LOCK": (
        "L5_SHADOW_ONLY", OWNER,
        "set shadow_locked False -- the owner clears the lock"),
    "OWNER_AUTHORIZATION_ABSENT": (
        "PMUS_Q1_LIVE / PMUS_Q1_SESSION_ID / PMUS_Q1_ARM", OWNER,
        "set the Q1 handshake env trio"),
    "CAMPAIGN_IDENTITY_UNKNOWN": (
        "approval nonce", DOC, "the document must carry a nonce"),
    "CAMPAIGN_RUNNING_LIMITS_UNREADABLE": (
        "SafetyLimits", SERVICE,
        "pass the real SafetyLimits the executor holds"),
    "CAMPAIGN_LIMITS_WIDER_THAN_APPROVED": (
        "SafetyLimits vs the document", DOC,
        "the document must not be narrower than the running caps"),
    "HALTED": ("halt latch", SERVICE, "set halted False"),
    "HALT_GATE_ABSENT": (
        "L5_HALT_URL / executor.halt_gate", SERVICE,
        "set halt_gate_present True"),
    "PREFLIGHT_NOT_PASSED": (
        "Runner.preflight_status", SERVICE,
        "pass preflight PASSED"),
    "NOT_RECONCILED": (
        "venue reconciliation", SERVICE, "set reconciled True"),
    "ORDER_STATE_UNCERTAIN": (
        "store.orders_uncertain", SERVICE, "clear uncertain orders"),
    "ORDER_STATE_UNREADABLE": (
        "store.orders_uncertain", SERVICE, "make the store readable"),
    "UNRESOLVED_ACCOUNTING": (
        "unresolved_discrepancies", SERVICE, "pass no discrepancies"),
    "CAPITAL_SCOPE_UNKNOWN": (
        "SafetyLimits.as_dict", SERVICE,
        "pass real SafetyLimits carrying the three scoped fields"),
    "WINDOW_BOUNDS_UNKNOWN": (
        "venue window bounds", SERVICE, "pass venue-confirmed bounds"),
    "WINDOW_BOUNDS_UNCONFIRMED": (
        "venue window bounds", SERVICE, "source must be the VENUE"),
    "WINDOW_NOT_OPEN": (
        "the clock", CODE,
        "the window has not started -- this is TIME, not a setting"),
    "WINDOW_CLOSING": (
        "the clock", CODE, "inside the pre-close margin"),
    "TRANSPORT_CANNOT_ARM": (
        "PMUS_Q1_LIVE / PMUS_Q1_SESSION_ID / PMUS_Q1_ARM", OWNER,
        "the Q1 handshake -- the transport's own arm check would refuse "
        "this process, and NO WINDOW IS SPENT on it. This gate was found "
        "by this tool: it was nested inside `if mw > 1:` and so was "
        "skipped for a ONE-window campaign, the only shape in use"),
    "DEPLOYMENT_IDENTITY_UNKNOWN": (
        "RAILWAY_DEPLOYMENT_ID", SERVICE, "set the deployment id"),
}

#: gates this walk will NOT satisfy, with why. Being explicit about what is
#: deliberately left refusing is the whole value of the output.
NEVER_SATISFIED = {
    "MANDATE_EXECUTION_MODE_NOT_MAKER_ONLY":
        "taker execution is not authorized and must stay refused",
}


def doc_for(start: dt.datetime, now: dt.datetime, *,
            nonce: Optional[str] = None, **over) -> Dict[str, Any]:
    """The nine-field document, bound to ONE window start.

    `window_starts` is an EXACT-MATCH allowlist, which is what makes this a
    binding rather than a description: every other window is refused
    WINDOW_NOT_IN_AUTHORIZED_SET by the authorization layer itself.
    """
    def iso(d: dt.datetime) -> str:
        return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    d = {"approved": True,
         "nonce": nonce or ("probe-depth2-%s"
                            % start.strftime("%Y%m%d-%H%Mz")),
         "series": "cpc-btc-updown-15m-",
         "issued_utc": iso(now),
         "expires_utc": iso(start + dt.timedelta(minutes=30)),
         "max_windows": 1,
         "max_total_exposure_usd": "1.00",
         "max_daily_loss_usd": "5.00",
         "window_starts": [iso(start)]}
    d.update(over)
    return d


def slug_of(start: dt.datetime) -> str:
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        start.strftime("%Y-%m-%d"), start.hour, start.minute)


def next_window(now: dt.datetime, lead_min: int) -> dt.datetime:
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    while (start - now).total_seconds() < lead_min * 60:
        start += dt.timedelta(minutes=15)
    return start


class _Store:
    """Only `orders_uncertain`, which is all the boundary reads here."""

    def __init__(self, uncertain: Any = None) -> None:
        self._u = uncertain

    def orders_uncertain(self) -> Any:
        return self._u


def production_limits() -> Any:
    """The caps the service reports, as the real type.

    THE REAL `SafetyLimits`, not a stand-in, because two gates read it two
    different ways: `_limit_of` uses `getattr` and `scoped_limits` uses
    `as_dict()`. A double with only attributes passes the first and refuses
    the second CAPITAL_SCOPE_UNKNOWN, which is a false negative that costs
    a reader an hour.
    """
    from decimal import Decimal as D
    return LX.SafetyLimits(
        max_usd_per_market=D("20"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("1.00"), max_concurrent_markets=1,
        max_live_orders=2, daily_realized_loss_halt_usd=D("5"))


def venue_bounds(start: dt.datetime) -> Dict[str, Any]:
    return {"source": AUTH.WINDOW_SOURCE_VENUE,
            "start": start.isoformat(),
            "end": (start + dt.timedelta(seconds=900)).isoformat()}


def walk(document: Dict[str, Any], market: str, start: dt.datetime, *,
         now: Optional[dt.datetime] = None,
         max_steps: int = 60) -> Dict[str, Any]:
    """Call the real boundary until it stops refusing, recording each gate."""
    now = now or dt.datetime.now(dt.timezone.utc)
    tmp = tempfile.mkdtemp(prefix="l5-gate-verify-")
    ledger = os.path.join(tmp, "auth.jsonl")
    os.environ[CA.APPROVAL_ENV] = json.dumps(document)
    os.environ[CA.ROOT_ENV] = os.path.join(tmp, "camp")

    # the inputs, every one at its REFUSING value to begin with, so each
    # gate has to be reached and named rather than skipped.
    state: Dict[str, Any] = {
        "shadow_locked": True,
        "halted": True,
        "halt_gate_present": False,
        "preflight": None,
        "reconciled": False,
        "unresolved": (),
        "store": _Store(),
        "limits": None,
        "window_bounds": None,
        "transport_armable": False,
        "owner_handshake": False,
    }
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    claim = None

    for _ in range(max_steps):
        if state["owner_handshake"]:
            os.environ[Q1.LIVE_ENV] = "YES"
            os.environ[Q1.SID_ENV] = "sess-gate-verify"
            os.environ[Q1.ARM_ENV] = "sess-gate-verify"
            os.environ["RAILWAY_DEPLOYMENT_ID"] = "dep-gate-verify"
        else:
            for k in (Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV):
                os.environ.pop(k, None)
        campaign = CA.campaign_decision(market, now=now, reserve=False)
        try:
            claim = AUTH.authorize_arming(
                campaign=campaign, market=market,
                preflight=state["preflight"], store=state["store"],
                halted=state["halted"],
                halt_gate_present=state["halt_gate_present"],
                reconciled=state["reconciled"],
                unresolved=state["unresolved"],
                limits=state["limits"],
                shadow_locked=state["shadow_locked"],
                require_durable=False,
                window_bounds=state["window_bounds"],
                nominal_window_s=900.0,
                transport_armable=state["transport_armable"],
                path=ledger, now=now)
            break
        except AUTH.Refused as exc:
            reason = str(getattr(exc, "reason", None) or exc).split(":")[0]
            if reason in seen:
                rows.append({"gate": reason, "status": "STUCK",
                             "reads": FIXES.get(reason, ("?", "?", "?"))[0],
                             "owned_by": FIXES.get(reason,
                                                   ("?", "?", "?"))[1],
                             "note": "this walk cannot satisfy it"})
                break
            seen.add(reason)
            if reason in TIME_GATES:
                rows.append({"gate": reason, "status": "NOT SATISFIABLE HERE",
                             "reads": "the clock", "owned_by": CODE,
                             "note": TIME_GATES[reason]})
                break
            if reason in NEVER_SATISFIED:
                rows.append({"gate": reason, "status": "LEFT REFUSING",
                             "reads": "-", "owned_by": CODE,
                             "note": NEVER_SATISFIED[reason]})
                break
            fix = FIXES.get(reason)
            if fix is None:
                rows.append({"gate": reason, "status": "UNMAPPED",
                             "reads": "?", "owned_by": "?",
                             "note": str(exc)[:160]})
                break
            rows.append({"gate": reason, "status": "reached and satisfied",
                         "reads": fix[0], "owned_by": fix[1],
                         "note": fix[2]})
            _apply(state, reason, start)
    return {"market": market, "window_start_utc": start.isoformat(),
            "gates_walked": rows,
            "claim": None if claim is None else {
                "market": claim.get("market"),
                "session_id": claim.get("session_id"),
                "boundary": claim.get("boundary"),
                "approved_limits": claim.get("approved_limits")},
            "ledger_dir": tmp}


def _apply(state: Dict[str, Any], reason: str,
           start: dt.datetime) -> None:
    """Set the ONE input that gate reads to its satisfied value."""
    if reason == "SHADOW_ONLY_LOCK":
        state["shadow_locked"] = False
    elif reason in ("OWNER_AUTHORIZATION_ABSENT", "TRANSPORT_CANNOT_ARM"):
        state["owner_handshake"] = True
        state["transport_armable"] = True
    elif reason in ("CAMPAIGN_RUNNING_LIMITS_UNREADABLE",
                    "CAPITAL_SCOPE_UNKNOWN"):
        state["limits"] = production_limits()
    elif reason == "HALTED":
        state["halted"] = False
    elif reason == "HALT_GATE_ABSENT":
        state["halt_gate_present"] = True
    elif reason == "PREFLIGHT_NOT_PASSED":
        state["preflight"] = {"preflight": "PASSED"}
    elif reason == "NOT_RECONCILED":
        state["reconciled"] = True
    elif reason in ("ORDER_STATE_UNCERTAIN", "ORDER_STATE_UNREADABLE"):
        state["store"] = _Store(())
    elif reason == "UNRESOLVED_ACCOUNTING":
        state["unresolved"] = ()
    elif reason.startswith("WINDOW_BOUNDS"):
        state["window_bounds"] = venue_bounds(start)
    elif reason == "DEPLOYMENT_IDENTITY_UNKNOWN":
        os.environ["RAILWAY_DEPLOYMENT_ID"] = "dep-gate-verify"


def binding_check(document: Dict[str, Any], market: str,
                  start: dt.datetime, now: dt.datetime) -> Dict[str, Any]:
    """The BINDING: this market is permitted and its neighbours are not."""
    os.environ[CA.APPROVAL_ENV] = json.dumps(document)
    os.environ[CA.ROOT_ENV] = tempfile.mkdtemp(prefix="l5-bind-")
    this = CA.campaign_decision(market, now=now, reserve=False)
    others = []
    for delta in (-30, -15, 15, 30):
        other = start + dt.timedelta(minutes=delta)
        o = CA.campaign_decision(slug_of(other), now=now, reserve=False)
        others.append({"slug": slug_of(other), "ok": o.get("ok"),
                       "reason": o.get("reason")})
    off_series = CA.campaign_decision(
        "cpceth-eth-updown-15m-%s-%02d%02dz" % (
            start.strftime("%Y-%m-%d"), start.hour, start.minute),
        now=now, reserve=False)
    return {
        "this_market": {"slug": market, "ok": this.get("ok"),
                        "reason": this.get("reason"),
                        "document_sha256": this.get("document_sha256"),
                        "approval_state": this.get("approval_state"),
                        "scope": {k: v for k, v in
                                  (this.get("scope") or {}).items()
                                  if k != "nonce"}},
        "neighbour_windows": others,
        "off_series": {"ok": off_series.get("ok"),
                       "reason": off_series.get("reason")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead-min", type=int, default=40)
    ap.add_argument("--market", default=None,
                    help="bind to this exact slug instead of deriving it")
    ap.add_argument("--at-window", action="store_true",
                    help="evaluate the gates as of a moment INSIDE the "
                         "window (start+300s), so the gates behind "
                         "WINDOW_NOT_OPEN can be reached. Changes nothing "
                         "real: it only moves the `now` passed to the "
                         "boundary.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if a.market:
        import src.l5_pmus_auth_book as AB
        ws = AB.slug_start(a.market)
        if ws is None:
            print("REFUSED: cannot read a window start from %r" % a.market)
            return 2
        start = dt.datetime.fromtimestamp(ws, dt.timezone.utc)
        market = a.market
    else:
        start = next_window(now, a.lead_min)
        market = slug_of(start)

    document = doc_for(start, now)
    binding = binding_check(document, market, start, now)
    # THE MOMENT THE GATES ARE EVALUATED AT. The document is always issued
    # NOW -- that is when the owner would publish it. `--at-window` only
    # moves the instant the BOUNDARY is asked, which is the one thing that
    # lets the gates behind WINDOW_NOT_OPEN be reached before the window
    # exists. Five minutes in, ten to go: outside the pre-close margin,
    # which is the same offset the arming-boundary suite uses.
    eval_at = (start + dt.timedelta(seconds=300)) if a.at_window else now
    gates = walk(document, market, start, now=eval_at)

    if a.json:
        print(json.dumps({"now_utc": now.isoformat(), "document": document,
                          "binding": binding, "gates": gates}, indent=1))
        return 0

    print("now                 %s" % now.replace(
        microsecond=0).isoformat().replace("+00:00", "Z"))
    print("bound market        %s" % market)
    print("window start (UTC)  %s" % start.replace(
        microsecond=0).isoformat().replace("+00:00", "Z"))
    print("lead time           %d s" % (start - now).total_seconds())
    print()
    print("L5_TS_MARKET=%s" % market)
    print("L5_TS_EXPERIMENT_ID=%s" % document["nonce"])
    print()
    print("L5_CAMPAIGN_APPROVAL (the OWNER sets this; one line):")
    print(json.dumps(document, separators=(",", ":")))
    print()
    print("=== THE BINDING ===")
    t = binding["this_market"]
    print("  %-52s ok=%s %s" % (t["slug"], t["ok"], t["reason"] or ""))
    print("  document_sha256   %s" % t["document_sha256"])
    print("  approval_state    %s" % t["approval_state"])
    for o in binding["neighbour_windows"]:
        print("  %-52s ok=%s %s" % (o["slug"], o["ok"], o["reason"] or ""))
    print("  %-52s ok=%s %s" % ("(same time, ETH series)",
                                binding["off_series"]["ok"],
                                binding["off_series"]["reason"] or ""))
    print()
    print("=== THE ARMING GATES, in the order the boundary applies them ===")
    print("    evaluated as of %s%s" % (
        eval_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "  (INSIDE the window, --at-window)" if a.at_window
        else "  (now)"))
    print("  %-42s %-22s %s" % ("gate", "status", "owned by"))
    for r in gates["gates_walked"]:
        print("  %-42s %-22s %s" % (r["gate"], r["status"], r["owned_by"]))
    print()
    if gates["claim"]:
        print("VERDICT: every gate between the document and a claim was "
              "reached and satisfied.")
        print("  claim market      %s" % gates["claim"]["market"])
        print("  claim boundary    %s" % gates["claim"]["boundary"])
        print("  approved limits   %s" % json.dumps(
            gates["claim"]["approved_limits"]))
        print()
        print("THAT IS NOT AN AUTHORIZATION. The claim above was written to "
              "a temporary\nledger with every SERVICE fact supplied by this "
              "tool. Each gate marked\n'service' is only evidenced by a "
              "running deployment's heartbeat, and each\ngate marked 'owner' "
              "is only set by the owner.")
    else:
        print("VERDICT: the walk stopped before a claim. The last row says "
              "where and why.")
    print()
    print("NOTHING WAS CHANGED. No Railway variable was read or written, no "
          "production\nclaim was consumed, no owner window was spent "
          "(reserve=False throughout), and\nno order was submitted. The "
          "approval document above is TEXT for the owner to\npublish; this "
          "tool cannot publish it and the design says so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
