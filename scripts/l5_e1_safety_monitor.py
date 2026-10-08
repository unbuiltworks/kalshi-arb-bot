"""THE INDEPENDENT READ-ONLY E1 SAFETY MONITOR. GET only, and structurally so.

WHY IT EXISTS. The one-window E1 probe can fail in a way the probe itself
cannot report: the process dies with an order resting at the venue. Nothing
in this project would have noticed. `pmus-readonly-recovery` runs a
completed one-shot settlement audit with `restartPolicyType: NEVER` and no
volume; `l5_pmus_btc15m_watcher` watches BOOK AVAILABILITY and never reads
an order or a position. So neither could see an unmanaged order, and there
was no channel to tell anyone. This is the smallest thing that can.

WHAT IT WATCHES, and nothing else:

  A1 OUTSTANDING AT HANDOVER   an order still open in the probe market at or
                               after the handover time. This is the
                               dangerous state, and it is the consequence of
                               executor failure WHATEVER the cause -- a
                               crash, a hang, a lost deploy, a bug -- so it
                               is the detection that matters most.
  A2 EXECUTOR PRESUMED FAILED  the probe's own deadline has passed while
                               exposure is still open at the venue.
  A3 UNEXPECTED POSITION       a non-zero position in the probe market.
  A4 MONITORING STALE          our own reads are failing or too old. A
                               monitor that has gone blind must SAY SO
                               rather than report quiet.
  A5 UNATTRIBUTED ORDER        an open order in the probe market whose id is
                               not one of E1's. Reported as POTENTIALLY
                               RELEVANT and never acted on: this PMUS
                               account is shared and another desk's resting
                               order is not ours to touch, or to cancel, or
                               to count as ours.

WHAT IT CANNOT DO. It holds exactly one venue HTTP object,
`pmus_auth.ReadOnlyTransport`, which raises `PermissionError` on any method
but GET. It imports no executor, no order body, no preview gate, no latch,
no authorization ledger. It never cancels anything -- there is no code here
that could. It refuses to start if any E1/Q1/XR2W arming key is set in its
container, because a monitor sharing an environment with an armed executor
is not independent of it.

LEAST-PRIVILEGED READ ACCESS. The venue offers no separate read-only
credential: one API key signs every request. So "least privileged" is the
strongest thing actually available -- the same key, in an image whose only
transport cannot form a non-GET request and which contains no order code.
That is a structural bound, not a promise.

ALERTS. Every alert is a structured `E1_ALERT` line on stdout, always. If
`E1_MONITOR_ALERT_WEBHOOK` names an HTTPS endpoint, each alert is also
POSTed there and the HTTP status is logged, which is the delivery
confirmation. The POST is refused if the URL names the venue host, so the
alert path can never become a venue path. No credential is ever placed in
an alert body or a URL.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as AUTH

#: the only two venue reads this monitor makes.
OPEN_PATH = "/v1/orders/open"
POSITIONS_PATH = "/v1/portfolio/positions"
ORDER_PATH = "/v1/order/%s"

#: live states, copied as literals rather than imported: this image does not
#: contain the cohort package, and a monitor that needs the engine to decide
#: what "open" means is not independent of the engine.
LIVE_STATES = {"ORDER_STATE_NEW", "ORDER_STATE_PENDING",
               "ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_OPEN",
               "ORDER_STATE_ACCEPTED", "ORDER_STATE_PENDING_NEW"}

#: arming keys that must NOT be present in the monitor's own container.
FORBIDDEN_ARMING_KEYS = (
    "PMUS_E1_LIVE", "PMUS_E1_ARM", "PMUS_E1_SESSION_ID",
    "PMUS_E1_GATE_EVIDENCE", "PMUS_E1_AUTHORIZED_FINGERPRINT",
    "PMUS_E1_MAX_ATTEMPTS", "PMUS_E1_MAX_WINDOWS",
    "PMUS_EXEC1_LIVE", "PMUS_EXEC1_ARM",
    "PMUS_Q1_LIVE", "PMUS_Q1_ARM",
)

MARKET_ENV = "E1_MONITOR_MARKET"
KNOWN_IDS_ENV = "E1_MONITOR_KNOWN_ORDER_IDS"
HANDOVER_ENV = "E1_MONITOR_HANDOVER_UNIX"
DEADLINE_ENV = "E1_MONITOR_EXECUTOR_DEADLINE_UNIX"
WEBHOOK_ENV = "E1_MONITOR_ALERT_WEBHOOK"
POLL_ENV = "E1_MONITOR_POLL_S"
RUNTIME_ENV = "E1_MONITOR_MAX_RUNTIME_S"
SELFTEST_ENV = "E1_MONITOR_SELF_TEST"

DEFAULT_POLL_S = 10.0
DEFAULT_RUNTIME_S = 3600.0
#: consecutive read failures before the monitor calls itself blind.
STALE_AFTER_FAILURES = 3


def _say(tag: str, payload: Mapping[str, Any]) -> None:
    print("%s %s" % (tag, json.dumps(payload, sort_keys=True, default=str)),
          flush=True)


def assert_not_armed() -> Dict[str, Any]:
    """FAIL CLOSED AT BOOT if this container also holds arming values.

    A monitor that could be armed, or that shares an authorization
    environment with an executor, is not an independent observer of it.
    """
    present = sorted(k for k in FORBIDDEN_ARMING_KEYS
                     if (os.getenv(k) or "").strip())
    if present:
        raise SystemExit(
            "REFUSED: this monitor's container holds arming values (%s). A "
            "monitor must not share an authorization environment with an "
            "executor. Blank these on the MONITOR service and redeploy."
            % ", ".join(present))
    return {"arming_keys_present": [], "values_never_read": True,
            "monitor_is_independent_of_the_executor": True}


def assert_cannot_mutate() -> Dict[str, Any]:
    """Prove the structural bound rather than assert it.

    The transport refuses a POST, and the modules that CAN post are absent
    from this image.
    """
    t = AUTH.ReadOnlyTransport()
    try:
        t.request("POST", "/v1/orders", {"x": 1})
    except PermissionError as exc:
        refused = str(exc)
    else:                                                      # pragma: no cover
        raise SystemExit("REFUSED: the transport accepted a POST")
    absent = []
    for mod in ("src.l5_pmus_transport", "analysis.l5.live_exec2.orders",
                "analysis.l5.e1_cohort.runner", "src.l5_order_body"):
        try:
            __import__(mod)
        except Exception:                                      # noqa: BLE001
            absent.append(mod)
    return {"post_refused_by_the_only_transport": refused,
            "mutating_modules_absent_from_this_image": absent,
            "can_submit_an_order": False,
            "can_cancel_an_order": False,
            "cancellation_is_never_attempted": True}


class AlertSink(object):
    """stdout always; an HTTPS webhook when the owner has configured one."""

    def __init__(self, webhook: Optional[str] = None) -> None:
        url = (webhook or "").strip()
        if url:
            if not url.startswith("https://"):
                raise SystemExit(
                    "REFUSED: %s must be an https:// URL" % WEBHOOK_ENV)
            if AUTH.API.split("//")[-1].split("/")[0] in url:
                raise SystemExit(
                    "REFUSED: %s names the venue host. The alert path must "
                    "never be able to become a venue path." % WEBHOOK_ENV)
        self.url = url or None
        self.deliveries: List[Dict[str, Any]] = []

    def report(self) -> Dict[str, Any]:
        return {"stdout": True, "webhook_configured": bool(self.url),
                "webhook_host": (self.url.split("/")[2] if self.url
                                 else None),
                "deliveries": self.deliveries,
                "MISSING_SETUP": (None if self.url else
                                  "%s is not set, so alerts are LOG ONLY and "
                                  "nothing is delivered to a person"
                                  % WEBHOOK_ENV)}

    def send(self, kind: str, detail: Mapping[str, Any]) -> Dict[str, Any]:
        body = {"source": "l5_e1_safety_monitor", "alert": kind,
                "unix": time.time(),
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "detail": dict(detail)}
        _say("E1_ALERT", body)
        if not self.url:
            rec = {"alert": kind, "delivered": False,
                   "why": "NO_WEBHOOK_CONFIGURED"}
            self.deliveries.append(rec)
            return rec
        data = json.dumps({"text": "E1 SAFETY ALERT: %s" % kind,
                           **body}).encode()
        req = urllib.request.Request(
            self.url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(
                    req, context=ssl.create_default_context(),
                    timeout=15) as r:
                rec = {"alert": kind, "delivered": True, "status": r.status}
        except urllib.error.HTTPError as exc:
            rec = {"alert": kind, "delivered": False, "status": exc.code,
                   "why": "HTTP_ERROR"}
        except Exception as exc:                               # noqa: BLE001
            rec = {"alert": kind, "delivered": False,
                   "why": type(exc).__name__}
        self.deliveries.append(rec)
        _say("E1_ALERT_DELIVERY", rec)
        return rec


def _num(x: Any) -> D:
    try:
        return D(str(x))
    except Exception:                                          # noqa: BLE001
        return D("0")


def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, Mapping):
        for k in ("orders", "data", "results", "positions", "items"):
            v = payload.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def _slug(row: Mapping[str, Any]) -> str:
    for k in ("marketSlug", "market_slug", "slug", "market"):
        v = row.get(k)
        if v:
            return str(v)
    return ""


def evaluate(*, open_rows: List[Dict[str, Any]],
             position_rows: List[Dict[str, Any]],
             market: str, known_ids: List[str], now: float,
             handover_unix: Optional[float],
             executor_deadline_unix: Optional[float]) -> Dict[str, Any]:
    """PURE. Venue rows in, alerts out -- so the detections are testable
    without a venue and without a clock."""
    known = {str(i) for i in known_ids if i}
    in_market = [r for r in open_rows
                 if _slug(r) == market
                 and str(r.get("state", "")) in LIVE_STATES]
    ours = [r for r in in_market if str(r.get("id")) in known]
    unattributed = [r for r in in_market if str(r.get("id")) not in known]
    positions = [r for r in position_rows
                 if _slug(r) == market and _num(r.get("quantity")
                                                or r.get("netQuantity")
                                                or r.get("size")) != 0]
    past_handover = (handover_unix is not None
                     and now >= float(handover_unix))
    past_deadline = (executor_deadline_unix is not None
                     and now >= float(executor_deadline_unix))
    alerts: List[Dict[str, Any]] = []
    if in_market and past_handover:
        alerts.append({
            "kind": "A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER",
            "severity": "CRITICAL",
            "market": market,
            "our_open_ids": sorted(str(r.get("id")) for r in ours),
            "unattributed_open_ids": sorted(str(r.get("id"))
                                            for r in unattributed),
            "handover_unix": handover_unix,
            "needs": "HUMAN INTERVENTION: cancel or reconcile by exact id",
            "monitor_will_not_cancel_anything": True})
    if past_deadline and (in_market or positions):
        alerts.append({
            "kind": "A2_EXECUTOR_PRESUMED_FAILED_WITH_EXPOSURE_OPEN",
            "severity": "CRITICAL",
            "market": market,
            "open_orders": len(in_market), "positions": len(positions),
            "executor_deadline_unix": executor_deadline_unix,
            "reading": ("the probe's own deadline has passed and exposure "
                        "is still open at the venue")})
    if positions:
        alerts.append({
            "kind": "A3_POSITION_IN_THE_PROBE_MARKET",
            "severity": "WARNING",
            "market": market,
            "rows": [{"slug": _slug(r),
                      "quantity": str(r.get("quantity")
                                      or r.get("netQuantity")
                                      or r.get("size")),
                      "outcomeSide": r.get("outcomeSide")}
                     for r in positions],
            "reading": ("inventory exists. Expected after a fill; it must "
                        "be hedged or ride to settlement, never unwound "
                        "by this monitor")})
    if unattributed:
        alerts.append({
            "kind": "A5_UNATTRIBUTED_ORDER_IN_THE_PROBE_MARKET",
            "severity": "INFO",
            "market": market,
            "ids": sorted(str(r.get("id")) for r in unattributed),
            "reading": ("POTENTIALLY RELEVANT, not attributed to E1. This "
                        "PMUS account is shared: another desk's resting "
                        "order is never ours to cancel, and never counted "
                        "as ours"),
            "never_cancelled": True})
    return {"market": market, "now": now,
            "open_in_market": len(in_market),
            "ours_open": len(ours), "unattributed_open": len(unattributed),
            "positions_in_market": len(positions),
            "past_handover": past_handover, "past_deadline": past_deadline,
            "alerts": alerts}


def _reader() -> Any:
    """The polling transport, as its OWN seam.

    Deliberately separate from `assert_cannot_mutate`, which must always
    exercise the real `ReadOnlyTransport`: a test that substitutes a fake
    reader must not be able to substitute the capability proof with it.
    """
    return AUTH.ReadOnlyTransport()


def poll_once(transport: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "errors": []}
    t0 = time.time()
    try:
        out["open_rows"] = _rows(transport.request("GET", OPEN_PATH))
    except Exception as exc:                                   # noqa: BLE001
        out["ok"] = False
        out["open_rows"] = []
        out["errors"].append("OPEN:%s" % type(exc).__name__)
    try:
        out["position_rows"] = _rows(transport.request("GET",
                                                       POSITIONS_PATH))
    except Exception as exc:                                   # noqa: BLE001
        out["ok"] = False
        out["position_rows"] = []
        out["errors"].append("POSITIONS:%s" % type(exc).__name__)
    out["latency_s"] = round(time.time() - t0, 3)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    boot = {"monitor": "l5_e1_safety_monitor",
            "arming": assert_not_armed(),
            "capability": assert_cannot_mutate(),
            "reads_only": [OPEN_PATH, POSITIONS_PATH],
            "no_volume_mounted": not os.path.isdir("/state/pmus-ws"),
            "watches_the_execution_loop": False,
            "is_imported_by_the_executor": False}
    _say("E1_MONITOR_BOOT", boot)

    market = (os.getenv(MARKET_ENV) or "").strip()
    if not market:
        _say("E1_MONITOR_REFUSED",
             {"why": "%s is not set; there is no probe market to watch"
                     % MARKET_ENV})
        return 2
    known_ids = [s.strip() for s in
                 (os.getenv(KNOWN_IDS_ENV) or "").split(",") if s.strip()]
    handover = os.getenv(HANDOVER_ENV)
    deadline = os.getenv(DEADLINE_ENV)
    poll_s = float(os.getenv(POLL_ENV) or DEFAULT_POLL_S)
    runtime_s = float(os.getenv(RUNTIME_ENV) or DEFAULT_RUNTIME_S)
    sink = AlertSink(os.getenv(WEBHOOK_ENV))
    _say("E1_MONITOR_CONFIG",
         {"market": market, "known_order_ids": len(known_ids),
          "handover_unix": handover, "executor_deadline_unix": deadline,
          "poll_s": poll_s, "max_runtime_s": runtime_s,
          "alerting": sink.report(),
          "KNOWN_IDS_ARE_OWNER_SUPPLIED": (
              "the probe's volume is mounted on the executor service and a "
              "Railway volume attaches to one service, so this monitor "
              "cannot read E1's tape. Ids it is not given are reported as "
              "unattributed rather than assumed to be someone else's")})

    if (os.getenv(SELFTEST_ENV) or "").strip().upper() == "YES":
        # THE END-TO-END TEST ALERT. Synthetic, labelled as such, and it
        # exercises the real sink -- so the delivery status printed here is
        # the delivery status a real alert would get.
        rec = sink.send("A0_SELF_TEST_SYNTHETIC", {
            "market": market, "IS_A_TEST": True,
            "reading": ("this is a deliberate test alert; it says nothing "
                        "about venue state")})
        _say("E1_MONITOR_SELF_TEST_RESULT",
             {"delivery": rec, "alerting": sink.report()})

    transport = _reader()
    started = time.time()
    polls = 0
    consecutive_failures = 0
    fired: Dict[str, int] = {}
    last_good: Optional[float] = None
    while time.time() - started < runtime_s:
        polls += 1
        p = poll_once(transport)
        now = time.time()
        if p["ok"]:
            consecutive_failures = 0
            last_good = now
        else:
            consecutive_failures += 1
        ev = evaluate(open_rows=p["open_rows"],
                      position_rows=p["position_rows"],
                      market=market, known_ids=known_ids, now=now,
                      handover_unix=float(handover) if handover else None,
                      executor_deadline_unix=(float(deadline) if deadline
                                              else None))
        if consecutive_failures >= STALE_AFTER_FAILURES:
            ev["alerts"].append({
                "kind": "A4_MONITORING_STALE_OR_BLIND",
                "severity": "CRITICAL",
                "consecutive_read_failures": consecutive_failures,
                "last_good_read_unix": last_good,
                "errors": p["errors"],
                "reading": ("this monitor can no longer see venue state. "
                            "Absence of an alert is NOT evidence of "
                            "safety while this is firing")})
        _say("E1_MONITOR_POLL",
             {"poll": polls, "ok": p["ok"], "latency_s": p["latency_s"],
              "errors": p["errors"],
              "open_in_market": ev["open_in_market"],
              "ours_open": ev["ours_open"],
              "unattributed_open": ev["unattributed_open"],
              "positions_in_market": ev["positions_in_market"],
              "past_handover": ev["past_handover"],
              "alerts": [a["kind"] for a in ev["alerts"]]})
        for a in ev["alerts"]:
            # ONE ALERT PER KIND PER STATE CHANGE, so a 15-minute window does
            # not deliver ninety copies of the same sentence. The state is
            # re-reported every poll on stdout regardless.
            key = "%s:%s" % (a["kind"], ev["open_in_market"])
            if fired.get(key):
                continue
            fired[key] = 1
            sink.send(a["kind"], a)
        time.sleep(poll_s)
    _say("E1_MONITOR_END",
         {"polls": polls, "alerting": sink.report(),
          "ran_for_s": round(time.time() - started, 1),
          "orders_cancelled_by_this_monitor": 0,
          "venue_writes_attempted": 0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
