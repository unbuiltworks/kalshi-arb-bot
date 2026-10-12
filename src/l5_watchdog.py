"""The independent watchdog: the one process that can stop the executor.

WHY THIS EXISTS AS A SEPARATE PROCESS. `src/l5_halt_gate.py` has been the
right shape for a while -- it fails closed on a present halt, an unreachable
store, an unparseable record and a stale one, and `HaltAsserter` has no code
path that clears -- but nothing ever ASSERTED it. A latch with no hand on it
is documentation. `analysis/l5/CLAUDE.md` is explicit that the watchdog
"must be independent of the trading process and able to act or fail safe, not
merely print logs", and until this module there was no actor.

THE VENUE IS THE SHARED STATE, AND THAT IS THE WHOLE DESIGN. The executor has
no volume the watchdog can read and a Railway volume attaches to one service,
so the two cannot share a filesystem; a heartbeat channel would have to be
built and could itself wedge. It is not needed. The thing the owner actually
cares about -- AN UNMANAGED RESTING ORDER -- is visible to anyone with
read-only credentials at `GET /v1/orders/open`. An order still open past the
deadline the executor promised to cancel it by IS the wedged-executor signal,
read from the authority rather than from the suspect. A dead executor, a
hung one, a crashed container and a lost cancel all present identically, and
all are caught.

TWO POWERS, DELIBERATELY ASYMMETRIC.

  * ASSERT. Writes the halt record the executor reads over HTTP. One
    direction only: toward refusing to trade. `HaltAsserter` cannot clear,
    and neither can this. Clearing is an owner action with the credentials
    directly.
  * CANCEL EXACTLY. Cancels the venue ids it actually observed resting, one
    at a time, never a market-scoped or account-wide sweep -- this PMUS
    account is SHARED, so `POST /v1/orders/open/cancel {"slugs": [...]}`
    would cancel another desk's orders, and that mechanism is the reason an
    earlier engine fingerprint was superseded. A cancel ack proves nothing:
    every id is re-read and reported by its venue-confirmed terminal state.

WHAT IT CANNOT DO, STRUCTURALLY. `CancelOnlyTransport` permits GET and
exactly one POST route, `/v1/order/{id}/cancel`. It has no submit path, no
preview path, no market-scoped cancel and no account sweep -- not disabled by
a flag, absent. It also never reads an arming key, because refusing to clean
up because a flag is unset is the dangerous direction.

AND IT NEVER UNWINDS A FILL. A cancel removes what has not traded. Shares
that already filled are inventory with a cost basis, reported as residual
and left for the ordinary settlement path. This module does not trade out of
a position at any price.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as A

#: the only mutating route this watchdog may ever use.
CANCEL_ROUTE = re.compile(r"^/v1/order/[A-Za-z0-9_-]+/cancel$")

#: venue states that mean the order is no longer working. Checked against
#: leavesQuantity first, because "PARTIALLY_FILLED" contains "FILLED" and a
#: substring match would retire an order that is still resting.
TERMINAL_HINTS = ("CANCEL", "REJECT", "EXPIRED", "FILLED")

#: how long an order may rest before the watchdog treats the executor as
#: wedged. The executor's own cancellation deadline must be comfortably
#: inside this, or the watchdog will race it and cancel a healthy order.
DEFAULT_MAX_ORDER_AGE_S = 300.0


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_t(value: Any) -> Optional[dt.datetime]:
    """The venue's RFC3339, including the nine-digit fractions it sends."""
    s = str(value or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.*\.\d{6})\d*(\+\d\d:\d\d)$", s)
    if m:
        s = m.group(1) + m.group(2)
    try:
        out = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


class MutationRefused(PermissionError):
    """Raised for any write this transport does not structurally permit."""


class CancelOnlyTransport:
    """Signed HTTP that can GET, and can POST exactly one cancel route.

    The refusal lives in the transport rather than in a caller's flag,
    exactly as `pmus_auth.ReadOnlyTransport` does, because a process that
    cannot construct a submit cannot be argued into one.
    """

    name = "CANCEL_ONLY"

    def __init__(self, opener=None) -> None:
        #: injected in tests; production goes through pmus_auth
        self._opener = opener

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = A.API) -> Any:
        m = str(method).upper()
        if m == "GET":
            return self._send("GET", path, None, base)
        if m == "POST" and CANCEL_ROUTE.match(str(path) or ""):
            return self._send("POST", path, body or {}, base)
        raise MutationRefused(
            "%s %s refused: this transport may only GET, or POST a single "
            "/v1/order/{id}/cancel" % (m, path))

    def _send(self, method: str, path: str, body, base: str) -> Any:
        if self._opener is not None:
            return self._opener(method, path, body, base)
        import ssl
        import urllib.request
        data = None if body is None else json.dumps(body).encode()
        headers = A.signed_headers(method, path, body) if base == A.API \
            else {"User-Agent": "l5-watchdog"}
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method)
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=15) as r:
            return json.loads(r.read() or b"{}")

    # the two verbs the watchdog actually uses, named for what they mean
    def open_orders(self) -> List[Dict[str, Any]]:
        body = self.request("GET", "/v1/orders/open") or {}
        rows = body.get("orders") if isinstance(body, Mapping) else None
        return [r for r in (rows or []) if isinstance(r, Mapping)]

    def order(self, order_id: str) -> Dict[str, Any]:
        body = self.request("GET", "/v1/order/%s" % order_id) or {}
        row = body.get("order") if isinstance(body, Mapping) else None
        return dict(row) if isinstance(row, Mapping) else dict(body or {})

    def cancel(self, order_id: str) -> Any:
        """By the VENUE's id. There is no market-scoped route on this class."""
        oid = str(order_id or "").strip()
        if not oid:
            raise MutationRefused("cancel needs a venue order id")
        return self.request("POST", "/v1/order/%s/cancel" % oid, {})


def is_terminal(row: Mapping[str, Any]) -> bool:
    leaves = row.get("leavesQuantity")
    if leaves is not None:
        try:
            return float(str(leaves)) <= 0
        except (TypeError, ValueError):
            pass
    return any(h in str(row.get("state") or "").upper()
               for h in TERMINAL_HINTS)


def order_age_s(row: Mapping[str, Any], *, now: Optional[dt.datetime] = None
                ) -> Optional[float]:
    t = _parse_t(row.get("createTime") or row.get("insertTime"))
    if t is None:
        return None
    return ((now or _utc()) - t).total_seconds()


AUTOMATIC = "MANUAL_ORDER_INDICATOR_AUTOMATIC"


class OwnershipOracle:
    """Reads the executor's own durable claim of which ids are its own.

    THE OWNER'S CORRECTION, IN CODE. Account-wide open orders plus an age test
    is not ownership on a SHARED account: it would have cancelled the owner's
    manual orders, D23N1EHTTZ7D among them. Ownership has two independent
    requirements here and both must hold:

      1. the id appears in the executor's durable live-order record, served
         read-only by `l5_order_registry_server` off the volume the executor
         writes its ACKs to;
      2. the VENUE's own row for that id agrees on the market and reports
         `manualOrderIndicator` AUTOMATIC.

    (1) without (2) means our record disagrees with the venue, which is
    uncertain identity. (2) without (1) is any other automated desk on this
    account. Neither is sufficient, which is why neither alone is accepted.

    FAILING TO READ IS FAILING TO OWN. An unreachable registry, an unparseable
    body, or a registry reporting `readable: false` all yield NO owned ids, so
    the watchdog latches the halt and cancels nothing. That is the safe
    direction: an uncancelled order with the executor already halted is
    recoverable by hand; somebody else's cancelled order is not.
    """

    DEFAULT_TIMEOUT_S = 3.0

    def __init__(self, url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 opener=None) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("OwnershipOracle needs a url")
        self.url = url.strip().rstrip("/")
        self.timeout_s = float(timeout_s)
        self._opener = opener

    def claimed(self) -> Dict[str, Dict[str, Any]]:
        """{venue_order_id: row} the executor says it owns. {} on any doubt."""
        from src import l5_order_registry_server as REG
        url = "%s/%s" % (self.url, REG.REGISTRY_KEY)
        try:
            if self._opener is not None:
                raw = self._opener(url, self.timeout_s)
            else:
                import urllib.request
                with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
                    if getattr(r, "status", 200) != 200:
                        return {}
                    raw = r.read()
            doc = json.loads(raw or b"{}")
        except BaseException:                                  # noqa: BLE001
            return {}
        if not isinstance(doc, dict) or doc.get("readable") is not True:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for row in (doc.get("orders") or []):
            if not isinstance(row, Mapping):
                continue
            vid = str(row.get("venue_order_id") or "").strip()
            if vid:
                out[vid] = dict(row)
        return out


class Watchdog:
    """One sweep: look, latch, cancel exactly, verify.

    `cancel_enabled` is off by default. A watchdog that can only assert is
    still useful -- it stops the next order -- and the cancel power is the
    one that touches a shared account, so it is opt-in and its absence is
    reported rather than silent.
    """

    def __init__(self, transport: Any, asserter: Any, *,
                 max_order_age_s: float = DEFAULT_MAX_ORDER_AGE_S,
                 cancel_enabled: bool = False, who: str = "l5-watchdog",
                 oracle: Any = None) -> None:
        self.t = transport
        self.asserter = asserter
        self.max_order_age_s = float(max_order_age_s)
        self.who = str(who)
        self.oracle = oracle
        #: NO ORACLE, NO CANCEL -- and not as a default that a flag can
        #: override. Without a durable ownership record there is nothing that
        #: distinguishes our order from the owner's manual one, so the cancel
        #: power is not merely off, it is unavailable.
        self.cancel_enabled = bool(cancel_enabled) and oracle is not None
        self.cancel_requested = bool(cancel_enabled)

    def sweep(self, *, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
        now = now or _utc()
        out: Dict[str, Any] = {
            "LABEL": "L5_WATCHDOG_SWEEP", "as_of_utc": now.isoformat(),
            "max_order_age_s": self.max_order_age_s,
            "cancel_enabled": self.cancel_enabled,
            "open_orders": 0, "overdue": [], "halted": None,
            "cancelled": [], "still_open": [], "unverified": [],
            "not_ours": [], "read_error": None,
        }
        try:
            rows = self.t.open_orders()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # A watchdog that cannot see is not evidence of safety. It says
            # so and does NOT assert -- a halt asserted on a failed read
            # would latch the system on every transient network error, and
            # the executor's own gate already refuses when IT cannot read.
            # NOT heartbeated: the positive attestation is deliberately
            # left to go stale. HaltGate's only path to True is a FRESH
            # "no halt", so a watchdog that has gone blind stops the
            # executor by omission within max_age_s -- without latching the
            # system on a single transient network error.
            out["read_error"] = type(exc).__name__
            return out
        out["open_orders"] = len(rows)
        overdue = []
        for r in rows:
            if is_terminal(r):
                continue
            age = order_age_s(r, now=now)
            # age UNKNOWN is treated as overdue: an order whose creation time
            # we cannot read is an order we cannot vouch for.
            if age is None or age > self.max_order_age_s:
                overdue.append({"venue_order_id": str(r.get("id") or ""),
                                "market": str(r.get("marketSlug") or ""),
                                "age_s": age,
                                "age_known": age is not None,
                                "leaves": str(r.get("leavesQuantity"))})
        out["overdue"] = overdue
        if not overdue:
            # THE POSITIVE ATTESTATION. `HaltGate` returns True only for a
            # fresh, parseable, explicit "no halt" -- absence reads as a
            # halt -- so a watchdog that merely stayed quiet on a clean
            # sweep would stop the executor permanently. Found by
            # test_a_young_order_is_left_alone, before any of this was
            # wired to a running service.
            #
            # It is not a clear: `heartbeat_no_halt` refuses to write over
            # a latched halt, so once latched this line cannot undo it.
            try:
                out["heartbeat"] = self.asserter.heartbeat_no_halt()
            except PermissionError as exc:
                out["heartbeat"] = None
                out["heartbeat_refused"] = str(exc)[:160]
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["heartbeat"] = None
                out["heartbeat_error"] = type(exc).__name__
            return out
        # THE LATCH FIRST. If the cancel loop then dies, the executor is
        # already stopped; the other order would leave it trading on.
        try:
            out["halted"] = self.asserter.assert_halt(
                reason="UNMANAGED_RESTING_ORDER",
                evidence={"orders": overdue, "who": self.who,
                          "why": ("an order resting past the executor's own "
                                  "cancellation deadline means the executor "
                                  "is not managing it")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["halt_error"] = type(exc).__name__
        if not self.cancel_enabled:
            out["cancel_skipped"] = (
                "cancel is not enabled on this watchdog; the halt is "
                "asserted and the order is STILL RESTING")
            out["cancel_unavailable_no_oracle"] = (
                self.cancel_requested and self.oracle is None)
            return out
        # OWNERSHIP, from the executor's own durable record. Read ONCE per
        # sweep: a per-id read would let the answer change mid-loop.
        claimed = self.oracle.claimed()
        out["claimed_ids"] = len(claimed)
        if not claimed:
            out["cancel_skipped"] = (
                "the ownership registry named no ids, so NOTHING is "
                "cancellable; the halt is asserted and the order is STILL "
                "RESTING")
            return out
        for o in overdue:
            vid = o["venue_order_id"]
            if not vid:
                out["unverified"].append(dict(o, verify_error="NO_ID"))
                continue
            mine = claimed.get(vid)
            if mine is None:
                # Not ours as far as any durable record goes. On a SHARED
                # account that includes the owner's manual orders, so this
                # is the branch that must never be relaxed.
                out["not_ours"].append(dict(o, why="NOT_IN_OWNERSHIP_REGISTRY"))
                continue
            # VENUE CORROBORATION BEFORE ANY POST. Our record is one
            # witness; the venue is the other, and they must agree before a
            # cancel is sent. A row the venue says is MANUAL, or that names a
            # different market than our claim, is uncertain identity -- the
            # same contract l5_e1_cancel_by_id enforces -- and nothing is
            # sent for it.
            try:
                pre = self.t.order(vid)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["unverified"].append(
                    dict(o, verify_error=type(exc).__name__,
                         why="OWNERSHIP_UNCONFIRMED_NO_POST_SENT"))
                continue
            indicator = str(pre.get("manualOrderIndicator") or "")
            venue_slug = str(pre.get("marketSlug") or "")
            claim_slug = str(mine.get("market") or "")
            if indicator != AUTOMATIC:
                out["not_ours"].append(
                    dict(o, why="VENUE_SAYS_MANUAL",
                         manual_order_indicator=indicator or None,
                         labels=("a MANUAL order is never cancellable by "
                                 "this path, however it is claimed")))
                continue
            if claim_slug and venue_slug and claim_slug != venue_slug:
                out["not_ours"].append(
                    dict(o, why="MARKET_MISMATCH", venue_market=venue_slug,
                         claimed_market=claim_slug))
                continue
            ack_error = None
            try:
                self.t.cancel(vid)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                ack_error = type(exc).__name__
            # the ack decides nothing; the re-read does
            try:
                row = self.t.order(vid)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["unverified"].append(dict(o, cancel_error=ack_error,
                                              verify_error=type(exc).__name__))
                continue
            rec = dict(o, cancel_error=ack_error,
                       venue_state=row.get("state"),
                       cum_qty=str(row.get("cumQuantity")),
                       leaves_qty=str(row.get("leavesQuantity")))
            if is_terminal(row):
                out["cancelled"].append(rec)
            else:
                out["still_open"].append(rec)
        return out


def asserter_for(store: Any, *, who: str = "l5-watchdog"):
    from src import l5_halt_gate as HG
    return HG.HaltAsserter(store, who=who)


def from_env(*, transport: Any = None, store: Any = None) -> Watchdog:
    """Build from the service's variables. Cancel needs an explicit YES."""
    from src import l5_halt_gate as HG
    st = store if store is not None else HG.FileStore(
        os.getenv("L5_WATCHDOG_STATE_DIR", "/data"))
    return Watchdog(
        transport if transport is not None else CancelOnlyTransport(),
        asserter_for(st, who=os.getenv("L5_WATCHDOG_WHO", "l5-watchdog")),
        max_order_age_s=float(os.getenv("L5_WATCHDOG_MAX_ORDER_AGE_S",
                                        str(DEFAULT_MAX_ORDER_AGE_S))),
        cancel_enabled=str(
            os.getenv("L5_WATCHDOG_CANCEL", "")).strip().upper() == "YES",
        oracle=oracle_from_env())


def oracle_from_env() -> Optional[OwnershipOracle]:
    """The executor's read-only registry, or None -- which disables cancel."""
    url = str(os.getenv("L5_ORDER_REGISTRY_URL", "")).strip()
    if not url:
        return None
    try:
        return OwnershipOracle(url)
    except BaseException:                                      # noqa: BLE001
        return None
