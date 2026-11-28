"""Ten seconds of checks before a twenty-three minute run. READ-ONLY.

WHY THIS EXISTS. Three consecutive four-lane rollover verifications were
lost to startup faults that any of these checks would have caught in
seconds: twice the durable state was not ARMED when the fleet began
(a shell `&` backgrounded an `rm -rf` that raced the write), and once a
NameError in `one_window -> _run_one -> Session` killed the first lane
and, correctly, the whole fleet. Each cost a full window cycle to
discover.

IT STARTS NOTHING AND TRADES NOTHING. It imports the real modules,
walks the real call chain by signature, reads the state file it was
given, and GETs the watchdog endpoint once. No order, no credential, no
process.

usage: python3 -I analysis/l5/btc5m_accumulation/rollover_preflight.py
       Reads the same environment the fleet would. Exit 0 = safe to
       launch; any other exit names every failure rather than the first.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import sys
import urllib.request

UTC = dt.timezone.utc
FAIL: list = []
WARN: list = []


def bad(check: str, detail: str) -> None:
    FAIL.append((check, detail))


def warn(check: str, detail: str) -> None:
    WARN.append((check, detail))


def check_imports():
    """A missing module or a broken import is instant, not at window 1."""
    try:
        from src import l5_btc5m_runner as RUN
        from src import l5_btc5m_execution as X
        from src import l5_persistent_mode as P
        from src import l5_shared_reservation as R
        from src import l5_series_registry as REG
        from src import l5_late_favorite as LF
        from src import l5_strategy_arbiter as AR
        from src import l5_straddle_ev as SEV
        return RUN, X, P, R, REG
    except BaseException as exc:                               # noqa: BLE001
        bad("imports", "%s: %s" % (type(exc).__name__, exc))
        return (None,) * 5


def check_call_chain(RUN):
    """The chain two NameErrors hid in. Checked by SIGNATURE, so a link
    that does not accept or pass the parameter fails here."""
    if RUN is None:
        return
    try:
        src = inspect.getsource(RUN.main)
    except BaseException as exc:                               # noqa: BLE001
        bad("call_chain", "cannot read main: %s" % type(exc).__name__)
        return
    for needle, why in (
            ("def one_window(slug: str, max_windows: int,",
             "one_window must accept settle_wait_s"),
            ("_run_one(slug, max_windows, settle_wait_s)",
             "one_window must PASS it to _run_one"),
            ("def _run_one(slug: str, max_windows: int,",
             "_run_one must accept it"),
            ("settle_wait_s=settle_wait_s)",
             "_run_one must pass it to Session"),
            ("settle_wait_s=0.0",
             "the persistent loop must close without waiting"),
            ("reconcile_pending(ex, ex.transport)",
             "the persistent loop must reconcile out of band, using the "
             "transport the window left on the executor")):
        if needle not in src:
            bad("call_chain", why)
    #: and EXECUTE the leaf, because a signature walk is not a run
    try:
        import types

        class _T:
            def __init__(self): self.n = 0
            def settlement(self, m):
                self.n += 1
                return {"terminal": False, "settlement_px": None}
        t = _T()
        sess = RUN.Session(
            market="cpc-btc-updown-5m-2026-11-16-1600z",
            executor=types.SimpleNamespace(), transport=t, root="/tmp",
            book_fn=lambda m: {}, p_fn=lambda m, w, n: {},
            risk_fn=lambda n, o: {}, halt_fn=lambda: {},
            clock=lambda: dt.datetime(2026, 11, 16, 16, 10,
                                      tzinfo=UTC).timestamp(),
            sleep=lambda s: None, settle_wait_s=0.0)
        sess.settle()
        if t.n != 1:
            bad("call_chain", "a zero wait asked the venue %d times, want 1"
                % t.n)
    except BaseException as exc:                               # noqa: BLE001
        bad("call_chain", "Session.settle raised %s: %s"
            % (type(exc).__name__, exc))


def check_undefined_names():
    """The class of fault that cost three runs.

    Python resolves a name a function never assigns as an IMPLICIT
    GLOBAL, so `reconcile_pending(ex, t)` inside `main` compiles cleanly
    and raises only when that line runs -- which, in a loop entered once
    every five minutes, means minutes after launch. Two of the three
    hid behind a green test suite as well, because no test executed the
    persistent branch.

    A string search cannot see this and neither can `ast.parse`; the
    preflight's own call-chain check matched the exact broken line and
    passed it. This asks the compiler's own symbol tables instead.
    """
    try:
        from src import l5_undefined_names as UN
    except BaseException as exc:                               # noqa: BLE001
        bad("undefined_names", "checker unavailable: %s" % type(exc).__name__)
        return
    targets = ["src/l5_btc5m_runner.py", "src/l5_btc5m_execution.py",
               "src/l5_btc5m_controller.py", "src/l5_persistent_mode.py",
               "src/l5_shared_reservation.py", "src/l5_late_favorite.py",
               "src/l5_strategy_arbiter.py", "src/l5_straddle_ev.py"]
    total = 0
    for path in targets:
        if not os.path.exists(path):
            continue
        for where, name, line in UN.undefined(path):
            total += 1
            bad("undefined_names", "%s %s name=%r near line %d"
                % (path, where, name, line))
    if not total:
        print("  undefined names: none across %d modules" % len(targets))


def check_env(P, R):
    """Every variable the persistent loop needs, named individually."""
    need = {
        P.ENVELOPE_ENV: "the standing envelope",
        P.STATE_PATH_ENV: "the durable RUN/STOP state file",
        P.DEPLOYMENT_ENV: "this deployment's identity",
        R.PATH_ENV: "the shared account-wide reservation",
        "L5_HALT_URL": "the watchdog halt record",
        "L5_ACCOUNT_RISK_URL": "the account risk figures",
        "L5_ACCOUNT_MAX_EXPOSURE_USD": "the account ceiling",
    } if P and R else {}
    for k, what in need.items():
        if not str(os.environ.get(k) or "").strip():
            bad("env", "%s is unset (%s)" % (k, what))


def check_envelope(P):
    if P is None:
        return
    got = P.envelope(now=dt.datetime.now(UTC))
    if not got.get("ok"):
        bad("envelope", str(got.get("reason")))
        return
    s = got["scope"]
    print("  envelope ok: strategies=%s series=%d caps=%s/%s/%s expires=%s"
          % (",".join(s["strategies"]), len(s["series"]),
             s["max_aggregate_usd"], s["max_per_market_usd"],
             s["max_daily_loss_usd"], s["expires_utc"]))


def check_state(P):
    """The fault that cost two runs: the state file must exist and be
    ARMED before a single process starts."""
    if P is None:
        return
    st = P.read_state()
    if not st.get("ok"):
        bad("state", str(st.get("reason")))
        return
    print("  state: %s%s" % (st["state"],
                             " (born)" if st.get("born") else ""))
    if st["state"] != P.ARMED:
        bad("state", "is %s, not ARMED -- the fleet would gate and trade "
                     "nothing" % st["state"])


def check_reservation(R):
    if R is None:
        return
    got = R.aggregate()
    if not got.get("ok"):
        bad("reservation", str(got.get("reason")))
        return
    print("  reservation: $%s held across %d holds %s"
          % (got["usd"], got["holds"], got.get("by_lane") or {}))


def check_watchdog():
    url = str(os.environ.get("L5_HALT_URL") or "").strip()
    if not url:
        return                              # already reported by check_env
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as fh:   # noqa: S310
            doc = json.loads(fh.read().decode())
    except BaseException as exc:                               # noqa: BLE001
        bad("watchdog", "%s unreachable: %s" % (url, type(exc).__name__))
        return
    if doc.get("halt") is not False:
        bad("watchdog", "halt is %r -- the loop would latch HALTED at once"
            % doc.get("halt"))
    try:
        t = dt.datetime.fromisoformat(str(doc.get("t")))
        age = (dt.datetime.now(UTC) - t).total_seconds()
    except BaseException:                                      # noqa: BLE001
        bad("watchdog", "record time %r unreadable" % doc.get("t"))
        return
    print("  watchdog: clear, record %.1fs old" % age)
    if age > 600:
        warn("watchdog", "record is %.0fs old; it goes stale at 900" % age)


def check_lanes(REG, X):
    if REG is None:
        return
    want = str(os.environ.get("L5_5M_FLEET") or "").strip()
    lanes = [s.strip().upper() for s in want.split(",") if s.strip()] or ["BTC5M"]
    for s in lanes:
        row = REG.BY_STRATEGY.get(s)
        if row is None:
            bad("lanes", "%s is not a registered strategy" % s)
            continue
        if row.horizon_s != 300.0:
            bad("lanes", "%s is a %ss series, not five-minute"
                % (s, row.horizon_s))
    print("  lanes: %s" % ", ".join(
        "%s(%s/%s)" % (s, REG.BY_STRATEGY[s].asset,
                       REG.BY_STRATEGY[s].index_symbol)
        for s in lanes if s in REG.BY_STRATEGY))


def main() -> int:
    print("L5 ROLLOVER PREFLIGHT  %s" % dt.datetime.now(UTC).isoformat())
    RUN, X, P, R, REG = check_imports()
    check_call_chain(RUN)
    check_undefined_names()
    check_env(P, R)
    check_envelope(P)
    check_state(P)
    check_reservation(R)
    check_watchdog()
    check_lanes(REG, X)
    print()
    for c, d in WARN:
        print("  WARN  %-12s %s" % (c, d))
    if not FAIL:
        print("PREFLIGHT OK -- safe to launch")
        return 0
    #: EVERY failure, not the first: one run should not be spent
    #: discovering faults one at a time.
    for c, d in FAIL:
        print("  FAIL  %-12s %s" % (c, d))
    print("\nPREFLIGHT FAILED (%d) -- do not launch" % len(FAIL))
    return 2


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    raise SystemExit(main())
