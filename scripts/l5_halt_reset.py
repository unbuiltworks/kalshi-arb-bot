"""A ONE-TIME, OWNER-GATED, AUDITABLE reset of a latched HALT.

WHAT THIS IS NOT. It is not automatic clearing. `HaltAsserter` still has no
clear method, the watchdog sweep still refuses to write `halt: false` over a
latch, and the executor still cannot touch the latch at all. None of that is
weakened here. This module is a separate admin entrypoint with its own
main(); `scripts/l5_halt_watchdog.py` does not import it, so there is no code
path by which a sweep, a restart or the executor can reach it.

WHY IT EXISTS. The latch is a file on the watchdog's volume, and a Railway
volume mounts to exactly one service, so the only process that can write it
is one running in that service. "Clear it by hand with the credentials
directly" therefore meant: no record of who cleared it, no check that the
condition had actually gone, and nothing stopping a second clear. This makes
the same owner action one-time, precondition-checked and audited, which is
strictly more protection than a hand edit, not less.

THE FOUR GATES, all fail-closed. Any one of them unmet writes nothing.

  1. INTENT. L5_HALT_RESET_CONFIRM must equal CONFIRM_PHRASE exactly, and
     L5_HALT_RESET_WHO must name a human. No default, no truthy shortcut.

  2. THE LATCH YOU REVIEWED, AND ONLY THAT ONE. L5_HALT_RESET_EXPECT must be
     the sha256 of the current halt.json bytes. If the watchdog re-latched
     for a NEW reason between the review and the reset, the digest moved and
     this refuses. That is the property a hand edit cannot have: you cannot
     clear a halt you have not read.

  3. ONE TIME. L5_HALT_RESET_ID is recorded in an append-only audit log on
     the same volume and refused if already present. So an admin start
     command left in place by mistake clears nothing on the next restart --
     the second boot refuses RESET_ID_ALREADY_CONSUMED.

  4. THE CONDITION IS ACTUALLY GONE, confirmed independently of the executor
     whose failure latched the halt:
       a. the VENUE reports zero open orders (authoritative, credentialed);
       b. the VENUE reports no non-zero position;
       c. the executor's ownership registry is READABLE, FRESH and EMPTY --
          readable-but-stale is refused, because a report that stopped
          arriving is the wedged signal, not an empty book;
       d. the operator attests watchdog health and the absence of any
          unresolved alarm, naming the evidence, and that attestation is
          recorded verbatim in the audit entry.
     (a)-(c) are machine-verified here. (d) is operator-attested and is
     labelled as such in the record rather than dressed up as a check.

ORDER OF WRITES. The audit entry is appended and fsynced BEFORE the latch is
cleared. If the process dies between the two, the record says a reset was
attempted and the halt is still latched -- the safe asymmetry. The reverse
order could clear a halt leaving no trace.

NOTHING HERE PLACES, PRICES OR CANCELS ANYTHING.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

CONFIRM_PHRASE = "RESET THE LATCHED HALT"

CONFIRM_ENV = "L5_HALT_RESET_CONFIRM"
WHO_ENV = "L5_HALT_RESET_WHO"
ID_ENV = "L5_HALT_RESET_ID"
EXPECT_ENV = "L5_HALT_RESET_EXPECT"
ATTEST_ENV = "L5_HALT_RESET_ATTEST"
REGISTRY_ENV = "L5_ORDER_REGISTRY_URL"
STATE_ENV = "L5_WATCHDOG_STATE_DIR"

#: the audit log, beside the latch on the same volume so they cannot be
#: separated by a redeploy.
AUDIT_KEY = "l5/halt_resets.jsonl"

#: a registry report older than this is STALE, and a stale report is not
#: evidence of an empty book. Deliberately tighter than the watchdog's own
#: report bound: this is a one-shot operator action, not a 24/7 sweep.
MAX_REGISTRY_AGE_S = 90.0


class ResetRefused(RuntimeError):
    """Nothing was written. The reason is the message."""


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _require_intent() -> Tuple[str, str, str, str]:
    """Gate 1 and the identifiers. Raises ResetRefused."""
    confirm = _env(CONFIRM_ENV)
    if confirm != CONFIRM_PHRASE:
        raise ResetRefused(
            "CONFIRM_PHRASE_MISMATCH: %s must be exactly %r" %
            (CONFIRM_ENV, CONFIRM_PHRASE))
    who = _env(WHO_ENV)
    if not who:
        raise ResetRefused("NO_OPERATOR: %s must name a human" % WHO_ENV)
    rid = _env(ID_ENV)
    if not rid:
        raise ResetRefused("NO_RESET_ID: %s must be a unique id" % ID_ENV)
    expect = _env(EXPECT_ENV).lower()
    if len(expect) != 64 or any(c not in "0123456789abcdef" for c in expect):
        raise ResetRefused(
            "NO_EXPECTED_DIGEST: %s must be the sha256 hex of the halt "
            "record you reviewed" % EXPECT_ENV)
    return who, rid, expect, _env(ATTEST_ENV)


def read_latch(store: Any) -> Tuple[bytes, Dict[str, Any], str]:
    """Gate 2's input: the raw bytes, the parsed record, and the digest."""
    from src import l5_halt_gate as HG
    try:
        raw = store.get(HG.HALT_KEY)
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "LATCH_UNREADABLE: %s -- an unreadable latch is never cleared"
            % type(exc).__name__)
    if raw is None:
        raise ResetRefused(
            "NO_LATCH_PRESENT: there is nothing to reset. An absent record "
            "already reads as halted to the gate; the watchdog's own "
            "heartbeat is what makes it permissive again.")
    try:
        rec = json.loads(raw)
    except BaseException:                                      # noqa: BLE001
        raise ResetRefused("LATCH_UNPARSEABLE: refusing to clear a torn "
                           "record whose meaning cannot be established")
    if not isinstance(rec, dict):
        raise ResetRefused("LATCH_NOT_AN_OBJECT")
    if rec.get("halt") is False:
        raise ResetRefused(
            "NOT_LATCHED: the record already says halt: false, so there is "
            "no latch to reset")
    return raw, rec, hashlib.sha256(raw).hexdigest()


def audit_entries(store: Any) -> List[Dict[str, Any]]:
    """Every prior reset. An unreadable log is treated as UNKNOWN, not empty."""
    try:
        raw = store.get(AUDIT_KEY)
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "AUDIT_LOG_UNREADABLE: %s -- without it the one-time guarantee "
            "cannot be established" % type(exc).__name__)
    if raw is None:
        return []
    out: List[Dict[str, Any]] = []
    for line in (raw.decode("utf-8", "replace")).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except BaseException:                                  # noqa: BLE001
            raise ResetRefused(
                "AUDIT_LOG_CORRUPT: a line will not parse, so a prior reset "
                "may be unrecorded and the one-time guard cannot hold")
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _require_unconsumed(store: Any, rid: str) -> List[Dict[str, Any]]:
    """Gate 3."""
    prior = audit_entries(store)
    for rec in prior:
        if str(rec.get("reset_id") or "") == rid and rec.get("cleared") is True:
            raise ResetRefused(
                "RESET_ID_ALREADY_CONSUMED: %r cleared a latch at %s. This "
                "is the guard that makes an admin start command left in "
                "place harmless on restart." % (rid, rec.get("t")))
    return prior


def venue_evidence(transport: Any) -> Dict[str, Any]:
    """Gate 4(a) and 4(b), from the VENUE itself. Raises ResetRefused."""
    try:
        rows = transport.open_orders()
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "VENUE_OPEN_ORDERS_UNREADABLE: %s -- an unread venue is never "
            "taken as flat" % type(exc).__name__)
    if not isinstance(rows, list):
        raise ResetRefused("VENUE_OPEN_ORDERS_MALFORMED")
    from src import l5_watchdog as W
    working = [r for r in rows if not W.is_terminal(r)]
    if working:
        raise ResetRefused(
            "VENUE_STILL_HAS_OPEN_ORDERS: %d working order(s) %r -- the "
            "condition that latched the halt has NOT gone"
            % (len(working), [str(r.get("id") or "") for r in working][:8]))
    positions, pos_err = _positions(transport)
    if pos_err is not None:
        raise ResetRefused(
            "VENUE_POSITIONS_UNREADABLE: %s -- flatness must be established, "
            "never assumed" % pos_err)
    if positions:
        raise ResetRefused(
            "VENUE_NOT_FLAT: non-zero position(s) %r" % (positions[:8],))
    return {"venue_open_orders": 0, "venue_open_rows_seen": len(rows),
            "venue_non_zero_positions": 0,
            "source": "/v1/orders/open and /v1/portfolio/positions, signed"}


def _positions(transport: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Non-zero rows, or an error string. Handles BOTH venue shapes.

    `positions` has been observed as an OBJECT keyed by market slug and as an
    array; iterating the object yields its string KEYS, which is how an
    earlier close-out raised TypeError on p["market"]. Both are handled.
    """
    try:
        body = transport.request("GET", "/v1/portfolio/positions") or {}
    except BaseException as exc:                               # noqa: BLE001
        return ([], type(exc).__name__)
    if not isinstance(body, Mapping):
        return ([], "positions_body_not_an_object")
    raw = body.get("positions")
    rows: List[Dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for slug, row in raw.items():
            if isinstance(row, Mapping):
                rows.append(dict(row, market=row.get("market") or slug))
    elif isinstance(raw, list):
        rows = [dict(r) for r in raw if isinstance(r, Mapping)]
    elif raw is None:
        rows = []
    else:
        return ([], "positions_unexpected_shape")
    out: List[Dict[str, Any]] = []
    for r in rows:
        qty = r.get("quantity", r.get("netQuantity", r.get("size")))
        try:
            if qty is not None and abs(float(str(qty))) > 0:
                out.append({"market": str(r.get("market") or ""),
                            "quantity": str(qty)})
        except BaseException:                                  # noqa: BLE001
            # a quantity we cannot read is not a zero
            out.append({"market": str(r.get("market") or ""),
                        "quantity": "UNREADABLE:%r" % (qty,)})
    return (out, None)


def registry_evidence(url: str, *, opener=None) -> Dict[str, Any]:
    """Gate 4(c). READABLE, FRESH and EMPTY, or ResetRefused."""
    if not url:
        raise ResetRefused(
            "NO_REGISTRY_URL: %s must name the executor's GET-only report, "
            "which is the independent witness that it holds no live order"
            % REGISTRY_ENV)
    from src import l5_watchdog as W
    src = W.RegistryOrderSource(url, opener=opener)
    try:
        src.fetch()
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "REGISTRY_UNREADABLE: %s -- a report that stopped arriving is "
            "the WEDGED signal, not an empty book" % exc)
    age = src.report_age_s()
    if age is None:
        raise ResetRefused(
            "REGISTRY_AGE_UNKNOWN: a report whose age cannot be established "
            "is treated as stale")
    if age > MAX_REGISTRY_AGE_S:
        raise ResetRefused(
            "REGISTRY_STALE: report is %.1fs old, bound is %.1fs"
            % (age, MAX_REGISTRY_AGE_S))
    rows = src.open_orders()
    if rows:
        raise ResetRefused(
            "EXECUTOR_STILL_CLAIMS_LIVE_ORDERS: %r"
            % ([str(r.get("id") or "") for r in rows][:8],))
    return {"registry_readable": True, "registry_age_s": round(age, 3),
            "registry_live_orders": 0, "registry_url": url}


def _append_audit(store: Any, prior: List[Dict[str, Any]],
                  entry: Dict[str, Any]) -> None:
    """Append-only. Rewrites the whole object because the store's verb is PUT.

    Every prior line is preserved verbatim; a store that cannot PUT raises,
    and the caller then clears nothing.
    """
    lines = [json.dumps(r, sort_keys=True) for r in prior]
    lines.append(json.dumps(entry, sort_keys=True))
    store.put(AUDIT_KEY, ("\n".join(lines) + "\n").encode())


def perform_reset(*, store: Any, transport: Any, registry_url: str,
                  now: Optional[dt.datetime] = None,
                  registry_opener=None) -> Dict[str, Any]:
    """All four gates, then audit, then clear. Raises ResetRefused."""
    who, rid, expect, attest = _require_intent()
    raw, rec, digest = read_latch(store)
    if digest != expect:
        raise ResetRefused(
            "LATCH_CHANGED_SINCE_REVIEW: current sha256 is %s, you named %s. "
            "The watchdog may have re-latched for a DIFFERENT reason (%r). "
            "Re-read the latch and review it again."
            % (digest, expect, rec.get("reason")))
    prior = _require_unconsumed(store, rid)
    if not attest:
        raise ResetRefused(
            "NO_ATTESTATION: %s must name the watchdog-health and "
            "no-open-alarm evidence you checked. It is recorded as an "
            "operator attestation, not as a machine check." % ATTEST_ENV)
    venue = venue_evidence(transport)
    registry = registry_evidence(registry_url, opener=registry_opener)
    t = (now or _utc()).isoformat()
    entry = {
        "t": t, "reset_id": rid, "who": who, "cleared": True,
        "prior_latch": rec, "prior_latch_sha256": digest,
        "machine_verified": {"venue": venue, "registry": registry},
        "operator_attested": {
            "text": attest,
            "labels": ("watchdog health and absence of an unresolved alarm "
                       "are ATTESTED by the operator, not verified here")},
        "labels": ("a one-time owner-gated reset of a latched HALT. The "
                   "watchdog and the executor have no code path to this."),
    }
    # the record lands BEFORE the clear: dying between the two leaves an
    # audited attempt and a still-latched halt, which is the safe asymmetry.
    _append_audit(store, prior, entry)
    from src import l5_halt_gate as HG
    cleared = {"halt": False, "t": t,
               "who": "owner-reset:%s" % who, "reset_id": rid,
               "cleared_reason": str(rec.get("reason") or ""),
               "labels": ("written by the one-time owner reset. The "
                          "watchdog's heartbeat keeps it fresh from here.")}
    store.put(HG.HALT_KEY, json.dumps(cleared, sort_keys=True).encode())
    return {"ok": True, "entry": entry, "record": cleared}


def main(argv: Optional[List[str]] = None) -> int:
    from src import l5_halt_gate as HG
    from src import l5_watchdog as W
    store = HG.FileStore(os.getenv(STATE_ENV, "/data"))
    out: Dict[str, Any]
    try:
        transport = W.CancelOnlyTransport()
        out = perform_reset(store=store, transport=transport,
                            registry_url=_env(REGISTRY_ENV))
        print("L5_HALT_RESET " + json.dumps(out, sort_keys=True))
        return 0
    except ResetRefused as exc:
        print("L5_HALT_RESET_REFUSED " + json.dumps(
            {"ok": False, "refused": str(exc)}, sort_keys=True))
        return 2
    except BaseException as exc:                               # noqa: BLE001
        print("L5_HALT_RESET_REFUSED " + json.dumps(
            {"ok": False, "refused": "%s: %s" % (type(exc).__name__,
                                                 str(exc)[:200])},
            sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
