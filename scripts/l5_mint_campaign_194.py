#!/usr/bin/env python3
"""Mint the issue-194 two-hour bounded campaign document, AT INSTALL TIME.

WHY THIS IS A SCRIPT AND NOT A DOCUMENT PASTED INTO A REPORT. This program
has lost at least four campaigns to the same operational failure: a document
is minted, reported for approval, and has aged past its bound by the time the
owner approves it ("The reported authorization has expired, so do not
activate or reuse it"). `issued_utc` is what ages, so the only fix is to stamp
it when the variable is actually installed -- not when the proposal is
written. Run this, then install what it prints, in the same sitting.

IT INSTALLS NOTHING AND IT TRADES NOTHING. It prints a document, its campaign
id and its SHA256, and validates it against the REAL approval gate read-only.
Installing the variable is the owner's action and is the approval.

READ-ONLY VALIDATION, DELIBERATELY. It calls `ApprovalGate.state()`, never
`authorize_window`, because `authorize_window` RESERVES a window -- a
verification that consumed the very window it was verifying has already
happened once in this program and must not happen again.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys
from typing import Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_campaign_approval as CA          # noqa: E402
from src import l5_live_authorization as AUTH       # noqa: E402

#: THE OWNER'S AGREED SCOPE, issue #194 comment 2026-11-05T20:20:35Z:
#: "500 total submissions (not 500 simultaneous orders), 2 concurrent resting
#:  orders (one each side), $20 total exposure, $12 deployed cash, $5 daily
#:  realized-loss halt; repricing and completion allowed within limits."
#:
#: EVERY ONE OF THESE IS AT OR INSIDE A CODE CEILING. Nothing is widened:
#:   max_windows 8            == CA.MAX_WINDOWS (8)        -> 8 x 15m = 2h
#:   max_total_exposure_usd 20 == CA.MAX_TOTAL_EXPOSURE_USD
#:   max_daily_loss_usd 5      == CA.MAX_DAILY_LOSS_USD
#:   series                    in CA.ALLOWED_SERIES
#:   duration 2h              <= CA.MAX_CAMPAIGN_S (4h)
WINDOWS = 8
DURATION_S = 2 * 3600

#: THE THREE-WINDOW VARIANT, and why the count is a parameter rather than a
#: second script. The owner approved a THREE-window paired-only experiment on
#: 2026-11-06, and three is not a smaller taste of eight: it is the largest
#: count whose WORST CASE fits the cumulative-loss headroom that remains.
#:
#:   deployed max_cumulative_loss_usd      18.6325   (L5_LIVE_GATES, live)
#:   lifetime realized loss already counted 13.7795   (realized_today, live)
#:   remaining headroom                      4.8530
#:   3 windows x max_worst_case_deficit_usd  4.5000
#:   margin                                  0.3530
#:
#: A FOURTH WINDOW DOES NOT FIT: 4 x 1.50 = 6.0000 exceeds 4.8530 by 1.1470,
#: and the fourth would be refused mid-campaign at CUMULATIVE_LOSS_LIMIT.
#: Pass --windows to choose; the default stays 8 so nothing already approved
#: changes shape.
def _windows_from_argv(argv: Sequence[str]) -> int:
    """`--windows N`, clamped to the code ceiling. Never widens anything."""
    n = WINDOWS
    for i, a in enumerate(argv):
        if a == "--windows" and i + 1 < len(argv):
            n = int(argv[i + 1])
        elif a.startswith("--windows="):
            n = int(a.split("=", 1)[1])
    if n < 1:
        raise SystemExit("--windows must be >= 1")
    if n > CA.MAX_WINDOWS:
        raise SystemExit("--windows %d exceeds the code ceiling CA.MAX_WINDOWS "
                         "(%d); this script never widens a ceiling"
                         % (n, CA.MAX_WINDOWS))
    return n
def _usd_from_argv(argv: Sequence[str], flag: str, default: str,
                   ceiling: str) -> str:
    """`--flag USD`, which may only ever TIGHTEN. Never widens a ceiling.

    Refuses anything above the module default OR above the code constant,
    so an owner grant smaller than the script's historical scope can be
    minted without editing the script, and a LARGER one cannot be minted
    at all.
    """
    from decimal import Decimal as _D
    raw = None
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            raw = argv[i + 1]
        elif a.startswith(flag + "="):
            raw = a.split("=", 1)[1]
    if raw is None:
        return default
    try:
        v = _D(raw)
    except BaseException:                                      # noqa: BLE001
        raise SystemExit("%s %r is not a number" % (flag, raw))
    if v <= 0:
        raise SystemExit("%s must be > 0" % flag)
    if v > _D(default):
        raise SystemExit("%s %s exceeds this script's scope (%s); it only "
                         "ever tightens" % (flag, raw, default))
    if v > _D(ceiling):
        raise SystemExit("%s %s exceeds the code ceiling (%s)"
                         % (flag, raw, ceiling))
    return str(v)


def _start_from_argv(argv: Sequence[str]):
    """`--start-utc ISO` -- mint for ONE named window instead of the next.

    WHY. `upcoming_window_starts` returns the windows strictly after NOW, so
    a one-window mint always lands on the NEXT quarter hour. That is often
    minutes away -- too soon for a variable write, a redeploy, a boot and a
    preflight -- and an owner who approved a specific window must get that
    window and no other. Validated to be a real BTC15m boundary strictly in
    the future; it cannot name a window that has already opened.
    """
    raw = None
    for i, a in enumerate(argv):
        if a == "--start-utc" and i + 1 < len(argv):
            raw = argv[i + 1]
        elif a.startswith("--start-utc="):
            raw = a.split("=", 1)[1]
    if raw is None:
        return None
    try:
        t = dt.datetime.fromisoformat(raw)
    except BaseException:                                      # noqa: BLE001
        raise SystemExit("--start-utc %r is not an ISO timestamp" % raw)
    if t.tzinfo is None:
        raise SystemExit("--start-utc must carry an explicit timezone")
    t = t.astimezone(dt.timezone.utc).replace(microsecond=0)
    if int(t.timestamp()) % WINDOW_PERIOD_S != 0:
        raise SystemExit("--start-utc %s is not a %ds window boundary"
                         % (t.isoformat(), WINDOW_PERIOD_S))
    now = dt.datetime.now(dt.timezone.utc)
    if t <= now:
        raise SystemExit("--start-utc %s is not in the future (now %s); a "
                         "window that has already opened must never be "
                         "minted" % (t.isoformat(), now.isoformat()))
    return t


EXPOSURE_USD = "20"
DAILY_LOSS_USD = "5"
CASH_USD = "12"

#: THE CLASS SPLIT, and the reserve is why it is not even.
#: `COMPLETION_SUBMISSIONS_PER_OBLIGATION` is 2 -- a complement plus one
#: re-derivation after a partial fill or a book that moved. So COMPLETE must
#: hold at least 2 per OPEN or `completion_capacity_refusal` will (correctly)
#: refuse to open exposure the completion budget could no longer close.
#:   OPEN 140 -> needs 280 COMPLETE; 300 is granted, 20 spare.
#: They sum to exactly the 500 total the owner set, so the flat backstop and
#: the class budgets bind at the same place.
MAX_SUBMISSIONS = 500
CLASS_BUDGETS = {"OPEN": 140, "COMPLETE": 300, "REPLACE": 60}


#: BTC15m windows open on the quarter hour.
WINDOW_PERIOD_S = 900
#: SETTLEMENT GRACE after the last authorized window CLOSES. `expires_utc`
#: is a settlement grace and never authorizes opening a window -- see
#: `upcoming_window_starts` for the bound that does.
GRACE_S = 900


def upcoming_window_starts(now: dt.datetime, n: int = WINDOWS) -> list:
    """The next `n` BTC15m window starts strictly after `now`.

    THE OPENING BOUND, AND WHY IT IS AN EXPLICIT SET. `opening_refusal`
    needs `window_starts` or `last_open_utc`; with NEITHER it returns
    CAMPAIGN_OPENING_BOUND_ABSENT and refuses every window, which is exactly
    what happened to camp1.29bbb533 at 21:34:07Z -- the document parsed as
    `approved`, the preflight PASSED, and arming was refused at
    `authorize_arming` on every tick of the 2130z window.

    A SET RATHER THAN JUST A LAST-OPEN, because it names precisely which
    eight windows the grant covers. `last_open_utc` alone would authorize
    every window up to it, including ones we never intended and any the
    venue happens to list in between.
    """
    step = dt.timedelta(seconds=WINDOW_PERIOD_S)
    t = now.replace(second=0, microsecond=0)
    t -= dt.timedelta(minutes=t.minute % (WINDOW_PERIOD_S // 60))
    while t <= now:
        t += step
    return [t + i * step for i in range(n)]


def mint(now: dt.datetime | None = None, nonce: str | None = None,
         windows: int = WINDOWS, exposure_usd: str | None = None,
         daily_loss_usd: str | None = None,
         first_start: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    nonce = nonce or secrets.token_hex(16)
    exposure_usd = exposure_usd or EXPOSURE_USD
    daily_loss_usd = daily_loss_usd or DAILY_LOSS_USD
    if first_start is not None:
        step = dt.timedelta(seconds=WINDOW_PERIOD_S)
        starts = [first_start + i * step for i in range(windows)]
    else:
        starts = upcoming_window_starts(now, windows)
    last_open = starts[-1]
    # expiry covers the last window's CLOSE plus a settlement grace, so a
    # position opened in the final window can still settle inside the
    # document. It is NOT what authorizes opening -- `window_starts` is.
    expires = last_open + dt.timedelta(
        seconds=WINDOW_PERIOD_S + GRACE_S)
    return {
        "approved": True,
        "nonce": nonce,
        "series": CA.ALLOWED_SERIES[0],
        "issued_utc": now.replace(microsecond=0).isoformat(),
        "expires_utc": expires.isoformat(),
        # ---- THE OPENING BOUND, all three stated so none has to be guessed
        "starts_utc": starts[0].isoformat(),
        "last_open_utc": last_open.isoformat(),
        "window_starts": [t.isoformat() for t in starts],
        "max_windows": windows,
        "max_total_exposure_usd": exposure_usd,
        "max_daily_loss_usd": daily_loss_usd,
        "note": ("issue-194 bounded L5 PMUS paired-only campaign, %d x 15m "
                 "consecutive; 2 resting orders, $12 deployed cash, $5 daily "
                 "realized-loss halt. PAIRED-ONLY: the probability model is "
                 "disabled and no directional underdog accumulation is "
                 "authorized. exposure=%s daily_loss=%s"
                 % (windows, exposure_usd, daily_loss_usd)),
    }


def slug_for(start: dt.datetime) -> str:
    """The BTC15m slug of the window beginning at `start`."""
    return "%s%s-%s" % (CA.ALLOWED_SERIES[0],
                        start.strftime("%Y-%m-%d"),
                        start.strftime("%H%M").lower() + "z")


def opening_check(doc: dict) -> list:
    """Can EVERY authorized window actually be opened? One row each.

    WHY THIS EXISTS, and it is the exact gap that cost camp1.29bbb533.
    `ApprovalGate.state()` approves a document with no opening bound ON
    PURPOSE -- parsing cannot trade, so a legacy document stays inert rather
    than invalid. Validating with `state()` alone therefore CANNOT catch a
    missing opening bound, and the first evidence is a live arming refusal.
    `opening_refusal` is the function the money path actually consults, so
    it is the one this script must ask.
    """
    st, _raw = validate(doc)
    scope = (st or {}).get("scope") or {}
    rows = []
    for iso in doc.get("window_starts") or []:
        start = dt.datetime.fromisoformat(iso)
        slug = slug_for(start)
        ref = CA.opening_refusal(scope, slug)
        rows.append({"slug": slug,
                     "openable": ref is None,
                     "refusal": None if ref is None else ref.get("reason")})
    return rows


def validate(doc: dict) -> dict:
    """Run the document past the REAL gate, read-only, in a throwaway env."""
    raw = json.dumps(doc, separators=(",", ":"), sort_keys=True)
    prev = os.environ.get(CA.APPROVAL_ENV)
    try:
        os.environ[CA.APPROVAL_ENV] = raw
        st = CA.approval_gate().state() or {}
    finally:
        if prev is None:
            os.environ.pop(CA.APPROVAL_ENV, None)
        else:
            os.environ[CA.APPROVAL_ENV] = prev
    return st, raw


def reserve_holds() -> tuple:
    """Does COMPLETE stay able to close every OPEN this budget permits?

    Walks the worst case: every OPEN consumes its full obligation of 2
    COMPLETE submissions. Reports the tightest margin reached.
    """
    o, c = CLASS_BUDGETS["OPEN"], CLASS_BUDGETS["COMPLETE"]
    per = AUTH.COMPLETION_SUBMISSIONS_PER_OBLIGATION
    worst = min(c - per * k - per for k in range(o))
    total_ok = (sum(CLASS_BUDGETS.values()) <= MAX_SUBMISSIONS)
    return worst, worst >= 0, total_ok, per


if __name__ == "__main__":
    _w = _windows_from_argv(sys.argv[1:])
    _exp = _usd_from_argv(sys.argv[1:], "--exposure", EXPOSURE_USD,
                          str(CA.MAX_TOTAL_EXPOSURE_USD))
    _loss = _usd_from_argv(sys.argv[1:], "--loss", DAILY_LOSS_USD,
                           str(CA.MAX_DAILY_LOSS_USD))
    _first = _start_from_argv(sys.argv[1:])
    doc = mint(windows=_w, exposure_usd=_exp, daily_loss_usd=_loss,
               first_start=_first)
    st, raw = validate(doc)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    # THE ID THE ENGINE ACTUALLY USES, not a second derivation of it.
    # This printed "camp1." + sha256(nonce)[:24], which is a string no gate
    # ever computes: `CA.nonce_sid` returns "camp1." + the nonce verbatim,
    # and that is what the claim ledger, the budget ledger, refuse_arming
    # and the L5_LIVE_GATES telemetry all key on. Printing the other one
    # meant an owner authorizing "campaign camp1.a8a513d6..." named an id
    # that appears nowhere in the running system, and a later close() or
    # release_stale() keyed on it would not match.
    cid = CA.nonce_sid(doc["nonce"])

    worst, ok, total_ok, per = reserve_holds()
    print("=" * 72)
    print("ISSUE 194 - BOUNDED PAIRED-ONLY CAMPAIGN - %d window(s) - mint %s"
          % (_w, doc["issued_utc"]))
    print("=" * 72)
    print("\n--- gate verdict (read-only; nothing reserved) ---")
    for k in ("approved", "reason", "state", "max_windows",
              "max_total_exposure_usd", "max_daily_loss_usd",
              "effective_expires_utc", "series"):
        if k in st:
            print("  %-26s %s" % (k, st[k]))
    if not st.get("approved"):
        print("\n  REFUSED: %s" % st.get("reason"))
        raise SystemExit(1)

    print("\n--- ceilings: asked vs code constant ---")
    for label, asked, ceil in (
            ("max_windows", _w, CA.MAX_WINDOWS),
            ("max_total_exposure_usd", _exp,
             CA.MAX_TOTAL_EXPOSURE_USD),
            ("max_daily_loss_usd", _loss, CA.MAX_DAILY_LOSS_USD),
            # THE SPAN THE DOCUMENT ACTUALLY STATES, not the module default.
            # DURATION_S described the 8-window shape; printing it for a
            # 3-window mint reported 7200 s for a 2700 s campaign.
            ("duration_s", int(
                (dt.datetime.fromisoformat(doc["expires_utc"])
                 - dt.datetime.fromisoformat(doc["issued_utc"])
                 ).total_seconds()), int(CA.MAX_CAMPAIGN_S))):
        print("  %-26s asked %-8s ceiling %-8s %s"
              % (label, asked, ceil,
                 "OK" if float(str(asked)) <= float(str(ceil)) else "EXCEEDS"))

    print("\n--- completion reserve (per obligation = %d) ---" % per)
    print("  class budgets            %s (sum %d / %d total)"
          % (CLASS_BUDGETS, sum(CLASS_BUDGETS.values()), MAX_SUBMISSIONS))
    print("  tightest COMPLETE margin %d  -> %s"
          % (worst, "reserve holds for every permitted OPEN" if ok
             else "RESERVE FAILS"))
    print("  class sum within total   %s" % total_ok)

    print("\n--- opening bound: can every authorized window OPEN? ---")
    rows = opening_check(doc)
    for r in rows:
        print("  %-40s openable=%-5s %s"
              % (r["slug"], r["openable"], r["refusal"] or ""))
    if not rows or not all(r["openable"] for r in rows):
        print("\n  REFUSED: at least one authorized window cannot be opened.")
        print("  This is the check `state()` alone cannot make -- it is why")
        print("  camp1.29bbb533 parsed as approved and still refused every")
        print("  window with CAMPAIGN_OPENING_BOUND_ABSENT.")
        raise SystemExit(1)
    print("  all %d authorized windows are openable" % len(rows))

    print("\n--- 1. install this campaign document ---")
    print("  %s=%s" % (CA.APPROVAL_ENV, raw))
    print("\n--- 2. and these budget variables ---")
    print("  L5_LIVE_AUTH_MAX_SUBMISSIONS=%d" % MAX_SUBMISSIONS)
    for k, v in CLASS_BUDGETS.items():
        print("  L5_LIVE_AUTH_MAX_%-9s=%d" % (k, v))
    print("  L5_MAX_TOTAL_EXPOSURE_USD=%s" % _exp)
    print("  L5_MAX_CASH_DEPLOYED_USD=%s" % CASH_USD)
    print("  L5_MAX_L5_DAILY_LOSS_USD=%s" % _loss)
    print("  L5_MAX_LIVE_ORDERS=2")
    # MUST MATCH THE DOCUMENT. The gate takes the min of the env and the
    # document, so a stale 8 here was not unsafe -- but the runtime-config
    # verifier grades the DEPLOYED variable against the document that
    # approved it, and printing 8 beside a 3-window grant is an instruction
    # to install a disagreement.
    print("  L5_LIVE_AUTH_MAX_WINDOWS=%d" % doc["max_windows"])

    print("\n--- audit identity ---")
    print("  campaign id     %s" % cid)
    print("  document_sha256 %s" % digest)
    print("  issued_utc      %s" % doc["issued_utc"])
    print("  expires_utc     %s" % doc["expires_utc"])
    print("  stale after     %s (issued + MAX_CAMPAIGN_S)"
          % (dt.datetime.fromisoformat(doc["issued_utc"])
             + dt.timedelta(seconds=CA.MAX_CAMPAIGN_S)).isoformat())
    print("\nHALT IS NOT CLEARED BY ANY OF THIS. Installing these variables")
    print("authorizes a campaign; it does not arm. L5_LIVE_HALT must still")
    print("be withdrawn deliberately, and that is a separate owner action.")
