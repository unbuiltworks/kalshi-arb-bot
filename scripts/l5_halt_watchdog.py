"""The watchdog process: serve the latch, sweep the venue, act.

It runs on an EXISTING service. It needs no executor, no ledger, no order
body and no arming key -- it holds `l5_watchdog.CancelOnlyTransport`, whose
only mutating route is `POST /v1/order/{id}/cancel`.

Loop, once every L5_WATCHDOG_POLL_S:
  * read `GET /v1/orders/open`;
  * an order resting longer than L5_WATCHDOG_MAX_ORDER_AGE_S is an order the
    executor is not managing -- latch the halt, then (with
    L5_WATCHDOG_CANCEL=YES) cancel exactly that venue id and verify by
    re-reading it;
  * a clean sweep refreshes the positive "no halt" attestation, which is the
    only thing that lets the executor trade at all;
  * a failed read writes NOTHING, so the attestation ages out and the
    executor fails closed within HaltGate's max_age_s.

It restarts safely: the latch lives on the volume, so a restarted watchdog
finds the halt it asserted before and `heartbeat_no_halt` refuses to
overwrite it.
"""
from __future__ import annotations

import json
import os
import sys
import time

from src import l5_halt_gate as HG
from src import l5_mandate_signature as MS
from src import l5_halt_server as SRV
from src import l5_watchdog as W

# OPTIONAL, AND ABSENT IN THE CREDENTIAL-FREE IMAGE. This entrypoint must boot
# without any signing code; `A is None` is the normal case now, not an error.
try:
    from src import pmus_auth as A              # type: ignore
except Exception:                               # noqa: BLE001
    A = None                                    # type: ignore


#: EXACTLY "YES" arms the one-time owner reset at STARTUP. Anything else --
#: unset, "1", "true", "yes" in the wrong case -- is OFF. A normal deployment
#: or restart therefore clears nothing, which is the point.
BOOT_RESET_ENV = "L5_HALT_RESET_AT_BOOT"


def boot_reset(store) -> tuple:
    """The owner-gated HALT reset, ONCE, before the sweep loop begins.

    WHY HERE AND NOWHERE ELSE. The latch is a file on this service's volume
    and a Railway volume attaches to exactly one service, so only a process
    in this service can write it. `preDeployCommand` was probed on production
    on 2026-10-19 (deployment 8d2a37bc) and refused NO_LATCH_PRESENT while
    the latch demonstrably existed: Railway does NOT mount the volume for the
    pre-deploy step. This process's volume IS mounted -- it reads and writes
    the latch every 20s -- so startup is the only remaining place, and it
    costs the ordinary ~1s container handover and nothing more.

    THE SWEEP LOOP STILL CANNOT CLEAR, which is the invariant that was ever
    load-bearing. This runs ONCE, before the loop, and the loop never calls
    it; `HaltAsserter` still has no clear method and `heartbeat_no_halt`
    still refuses over a latch. The image already held permissive-write
    capability through that method, so "cannot clear" was always a GUARD
    rather than an absence of code.

    IT NEVER RAISES. A watchdog that fails to start is a watchdog that stops
    watching, so every refusal -- and every unexpected error -- is reported
    and swallowed, and the caller proceeds to sweep. Refusing is the EXPECTED
    outcome once a reset id is spent.

    EVERY GATE IS THE EXISTING ONE, unchanged: the confirm phrase and a named
    operator, the sha256 of the latch under review, the retyped reason, a
    reset id spent by the ATTEMPT (so a replayed id clears nothing), the
    venue reporting no L5-owned or unattributed-automated working order, the
    executor's registry readable + fresh + empty AND publishing
    submission_disabled, and the audit entry fsynced BEFORE the latch write.
    """
    try:
        from scripts import l5_halt_reset as RST
    except BaseException as exc:                               # noqa: BLE001
        return ("L5_HALT_BOOT_RESET_REFUSED",
                {"ok": False, "refused": "RESET_MODULE_UNAVAILABLE:%s"
                 % type(exc).__name__, "cleared": False})
    try:
        RST._require_reset_image()
        RST._require_clearable_store(store)
        out = RST.perform_reset(
            store=store, transport=RST.read_only_transport(),
            registry_url=os.getenv("L5_ORDER_REGISTRY_URL", "").strip())
        return ("L5_HALT_BOOT_RESET",
                {"ok": True, "cleared": True, "entry": out.get("entry"),
                 "labels": ("the owner-gated latch clear ran ONCE at startup. "
                            "The sweep loop has no clear path; this process "
                            "now proceeds to sweep normally.")})
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return ("L5_HALT_BOOT_RESET_REFUSED",
                {"ok": False, "cleared": False,
                 "refused": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                 "labels": ("NOTHING WAS CLEARED and the latch is untouched. "
                            "The watchdog starts and sweeps regardless: a "
                            "refusal must never stop the watching. A spent "
                            "reset id refusing here is the expected state.")})


def emit(tag: str, payload) -> None:
    print("%s %s" % (tag, json.dumps(payload, default=str, sort_keys=True)),
          flush=True)


def main() -> int:
    root = os.getenv("L5_WATCHDOG_STATE_DIR", "/data")
    port = int(os.getenv("PORT", "8080"))
    poll_s = float(os.getenv("L5_WATCHDOG_POLL_S", "20"))
    store = HG.FileStore(root)
    SRV.serve(store, port=port)
    # THE OWNER-MANDATE DOCUMENTS. The watchdog publishes the replay floor
    # and the revocation list for the same reason it owns the latch: the
    # executor must not be able to change what governs it. The executor
    # REFUSES a document older than MS.DEFAULT_MAX_AGE_S, so these are
    # re-stamped on every sweep and a watchdog that stops refreshing them
    # stops authorizing arming -- which is the liveness property that bound
    # exists to create.
    _pub = MS.WatchdogPublisher(root, root)
    _first_publish = True
    wd = W.from_env(store=store)
    emit("L5_WATCHDOG_BOOT", {
        "state_dir": root, "port": port, "poll_s": poll_s,
        "mandate_documents_served": list(SRV.SERVED_KEYS),
        "mandate_doc_max_age_s": MS.DEFAULT_MAX_AGE_S,
        "mandate_trusted_keys": len(MS.MANDATE_PUBKEYS),
        "max_order_age_s": wd.max_order_age_s,
        "cancel_enabled": wd.cancel_enabled,
        "credential_free": A is None,
        "credentials": (A.has_credentials() if A is not None else False),
        "order_source": getattr(wd.t, "name", "?"),
        "serves": "/" + HG.HALT_KEY,
        "labels": ("GET-only server; the only mutating route this process "
                   "can build is POST /v1/order/{id}/cancel"),
    })
    # CREDENTIAL-FREE IS NOT AN ALARM. It is the configured shape: the
    # watchdog judges the executor from the executor's own GET-only report and
    # never reads the venue. Only a watchdog that has NEITHER a credential NOR
    # a registry to read is blind, and that one cannot attest at all.
    if A is None and not os.getenv("L5_ORDER_REGISTRY_URL", "").strip():
        emit("L5_WATCHDOG_ALARM", {
            "kind": "no_order_source",
            "labels": ("no credentials and no L5_ORDER_REGISTRY_URL, so "
                       "there is nothing to judge the executor from; serving "
                       "the latch and attesting nothing, which fails the "
                       "executor closed when the record goes stale")})
    # ONE-TIME OWNER RESET, BEFORE THE LOOP. Default OFF; never raises; the
    # loop below has no clear path and never calls it.
    if str(os.getenv(BOOT_RESET_ENV, "")).strip().upper() == "YES":
        emit(*boot_reset(store))
    # THE OWNER'S EMERGENCY HALT, on the sweep loop that already exists.
    #
    # Wired HERE rather than inside `sweep()` so the sweep's contract and its
    # 166 tests are untouched. Off unless BOTH the URL and the HMAC secret
    # are set; a half-configured mechanism is off, because a URL with no
    # secret would accept an unsigned marker.
    #
    # It can only ever reach `assert_halt`. It cannot clear a latch, cannot
    # attest "no halt", and cannot authorize trading -- `HaltAsserter` has no
    # clear by construction. A latched HALT is what makes the EXECUTOR cancel:
    # `Runner.maybe_halt_deadman` runs `deadman()`, which cancels by exact
    # venue id and proves it by re-reading the order.
    from src import l5_emergency_halt_marker as EHM
    _ehm = EHM.from_env(store, wd.asserter)
    emit("L5_EMERGENCY_HALT_MARKER_CONFIG", {
        "configured": _ehm is not None,
        "url_env": EHM.MARKER_URL_ENV, "secret_env": EHM.MARKER_SECRET_ENV,
        "read_credential_set": bool(
            (os.getenv(EHM.MARKER_BEARER_ENV) or "").strip()),
        "channel_max_down_s": EHM.channel_max_down_s(),
        "poll_s": poll_s,
        "timeout_s": (None if _ehm is None else _ehm["source"].timeout_s),
        "max_marker_age_s": EHM.MAX_MARKER_AGE_S,
        "service": (None if _ehm is None else _ehm["service"]),
        "labels": ("HALT only. Unreachable retains the existing state and "
                   "asserts nothing; the executor is failed closed by the "
                   "attestation going stale, not by this.")})
    # THE OWNER'S ONE-ACTION HALT, checked EVERY sweep and independent of
    # whether the signed-marker path is configured at all. The marker needs
    # a secret and a published file; this needs one Railway variable, whose
    # write is already an authenticated action carrying more authority than
    # halting. HALT-only, idempotent, and it cannot clear: unsetting the
    # variable leaves the latch exactly where it is.
    emit("L5_EMERGENCY_HALT_NOW_CONFIG", {
        "env": EHM.HALT_NOW_ENV,
        "armed": EHM.halt_now_reason() is not None,
        "poll_s": poll_s,
        "labels": ("set this variable to any non-empty reason to latch HALT. "
                   "Clearing it does NOT clear the halt -- only "
                   "scripts/l5_halt_reset.py can, under its four gates. The "
                   "latch is what makes the EXECUTOR cancel by exact venue "
                   "order id via Runner.maybe_halt_deadman -> deadman().")})
    _chan = EHM.ChannelHealth(EHM.channel_max_down_s()) \
        if _ehm is not None else None
    _chan_was = None
    while True:
        try:
            # FIRST in the sweep, before anything that could fail: the one
            # action the owner has must not sit behind a marker fetch.
            _hn = EHM.check_halt_now(
                wd.asserter, store,
                # whatever this watchdog is guarding, by name, for the
                # audit record only -- the trigger is not scoped by it.
                service=((os.getenv(EHM.MARKER_SERVICE_ENV) or "").strip()
                         or (os.getenv("RAILWAY_SERVICE_NAME") or "").strip()
                         or "l5-pmus-live"))
            if _hn.get("acted") or _hn.get("reason") not in (
                    "TRIGGER_NOT_SET", "ALREADY_LATCHED"):
                emit("L5_EMERGENCY_HALT_NOW", _hn)
            if _ehm is not None:
                # never allowed to break the sweep: check() does not raise.
                _m = EHM.check(_ehm["source"], _ehm["asserter"],
                               _ehm["store"], service=_ehm["service"],
                               secret=_ehm["secret"])
                _chan.note(_m.get("reason"))
                _down = _chan.down()
                wd.attestation_hold = _down
                if _down != _chan_was:
                    _chan_was = _down
                    emit("L5_EMERGENCY_HALT_CHANNEL", {
                        "state": "DOWN" if _down else "OK",
                        "silent_s": round(_chan.silent_s(), 1),
                        "max_down_s": _chan.max_down_s,
                        "last_reason": _m.get("reason"),
                        "error": _m.get("error"),
                        "labels": ("DOWN: the no-halt attestation is "
                                   "withheld, so the executor stops "
                                   "opening exposure when its last one "
                                   "goes stale. Nothing is latched or "
                                   "cancelled. OK: attestation resumed.")})
                if _m.get("acted") or _m.get("reason") not in (
                        "MARKER_UNREACHABLE", "MARKER_COMMAND_NOT_HALT",
                        "MARKER_ALREADY_ACTED", "MARKER_ABSENT"):
                    # quiet on the three steady-state outcomes; loud on
                    # anything that latched or looked wrong.
                    emit("L5_EMERGENCY_HALT_MARKER", _m)
            # BEFORE the sweep, and never allowed to break it: a
            # publishing failure must leave the documents to go stale and
            # refuse, not stop the watchdog from watching.
            try:
                _p = _pub.publish()
                # EMIT THE FIRST SUCCESS OF EACH PROCESS, and thereafter
                # only when something is revoked. Silence on the happy path
                # kept the log clean and made the fix UNVERIFIABLE: these
                # documents are served over private networking and this
                # service has no public domain, so this line is the only
                # evidence available that publishing works at all and that
                # its stamp is fresh. Still bounded -- one line per process,
                # not one per 20-second sweep.
                if _first_publish or _p.get("revoked"):
                    emit("L5_MANDATE_PUBLISHED",
                         dict(_p, first_of_process=_first_publish))
                    _first_publish = False
            except BaseException as _pexc:                     # noqa: BLE001
                if isinstance(_pexc, (KeyboardInterrupt, SystemExit)):
                    raise
                emit("L5_MANDATE_PUBLISH_FAILED",
                     {"error": type(_pexc).__name__,
                      "detail": str(_pexc)[:200],
                      "effect": ("the served documents will go stale and "
                                 "the executor will refuse to arm")})
            emit("L5_WATCHDOG_SWEEP", wd.sweep())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # never attest on an error path, and never die: a watchdog that
            # exits is a watchdog that stops watching.
            emit("L5_WATCHDOG_ALARM", {"kind": "sweep_raised",
                                       "error": type(exc).__name__,
                                       "detail": str(exc)[:200]})
        # LIVE 2026-11-23 16:31Z: a bot died 10.8 s after placing an order;
        # this loop had last read its ownership claim 1.7 s BEFORE that
        # order, so the order it remembered was an older one and the
        # resting order was never cancelled. Ownership is therefore
        # refreshed between sweeps (an internal read; no venue request).
        W.wait_refreshing_claims(wd, poll_s)


if __name__ == "__main__":
    sys.exit(main())
