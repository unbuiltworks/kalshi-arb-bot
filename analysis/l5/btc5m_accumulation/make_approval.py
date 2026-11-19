"""Generate a BTC5m campaign approval document for the OWNER to set.

This prints a document. It does NOT set it, does not reach Railway, and
authorizes nothing by itself: the approval becomes real only when the owner
pastes it into `L5_CAMPAIGN_APPROVAL` on the service and supplies the arm
token separately. Running this file is not an authorization event.

IT VERIFIES ITSELF. Before printing, the document is fed through the REAL
`l5_campaign_approval.campaign_decision` against the first window it names,
so a document that the service would reject is reported here rather than
discovered at the wire.

THE CONSTRAINTS IT ENFORCES, all from the code's own ceilings:

  nonce                >= 8 characters, single use. Reusing a spent nonce
                       does not buy a second campaign: consumption is
                       durable and keyed on the nonce.
  max_windows          <= 48 for this series
                       (l5_campaign_approval.max_windows_for). The GENERAL
                       ceiling stays 8; only BTC5m has the wider count, and
                       48 five-minute windows is exactly the 4h campaign cap.
  campaign length      <= 4 h from issued_utc to expires_utc
  document age         the service treats a document as STALE one hour
                       after `issued_utc`, so generate it shortly before
                       you set it -- this is the usual reason a freshly
                       written approval is refused.
  max_total_exposure   clamped by code to $5 for this series, and in
                       practice by `L5_ACCOUNT_MAX_EXPOSURE_USD` ($0.45).
  max_daily_loss_usd   <= $5
  window_starts        5-minute boundaries, in the future, inside the
                       campaign, and no more than max_windows of them.

usage:
  python3 -I analysis/l5/btc5m_accumulation/make_approval.py [N] [--start ISO]
      N        how many consecutive 5-minute windows (default 4, max 48)
      --start  first window start, ISO 8601 UTC. Default: the next
               5-minute boundary at least 6 minutes away.
      --nonce  supply your own; otherwise a fresh random one is generated.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys

sys.path.insert(0, os.getcwd())

from src import l5_campaign_approval as CA        # noqa: E402
from src import l5_origin as ORIGIN                # noqa: E402
from src import l5_series_registry as REG          # noqa: E402

UTC = dt.timezone.utc
#: WHICH SERIES THIS DOCUMENT NAMES. One document names one series, so
#: the asset is chosen here and nowhere else. `--strategy` rebinds it in
#: `main`; the default keeps every existing invocation byte-identical.
SERIES = CA.BTC5M_SERIES


def next_boundary(min_lead_s: float = 360.0) -> dt.datetime:
    now = dt.datetime.now(UTC).replace(second=0, microsecond=0)
    t = now + dt.timedelta(minutes=(5 - now.minute % 5) % 5 or 5)
    while (t - dt.datetime.now(UTC)).total_seconds() < min_lead_s:
        t += dt.timedelta(minutes=5)
    return t


def slug(t: dt.datetime) -> str:
    return "%s%s-%sz" % (SERIES, t.strftime("%Y-%m-%d"), t.strftime("%H%M"))


def build(n: int, start: dt.datetime, nonce: str, exposure: str,
          daily_loss: str, rolling: bool = False) -> dict:
    """The approval document.

    TWO OPENING BOUNDS, and the approval layer accepts either:

      ENUMERATED  `window_starts` names each window. Exact, and what to use
                  when the windows wanted are known.
      ROLLING     `last_open_utc` says "no window may OPEN after this", and
                  the runner takes the next windows in order up to
                  `max_windows`. This is the form the 15-minute lane is
                  bounded by, and it is what `L5_BTC5M_CAMPAIGN=ON` with no
                  enumeration consumes.

    Neither is more permissive: `max_windows` caps both, the approval layer
    clamps it to 8, and the runner holds its own ceiling at 8 as well. A
    document carrying NEITHER bound is refused outright, so there is no
    permissive default.
    """
    issued = dt.datetime.now(UTC).replace(microsecond=0)
    starts = [start + dt.timedelta(minutes=5 * i) for i in range(n)]
    # settle-out grace after the last window, inside the 4 h campaign bound
    expires = starts[-1] + dt.timedelta(minutes=35)
    if (expires - issued).total_seconds() > CA.MAX_CAMPAIGN_S:
        expires = issued + dt.timedelta(seconds=CA.MAX_CAMPAIGN_S - 60)
    doc = {
        "approved": True,
        "nonce": nonce,
        "series": SERIES,
        "issued_utc": issued.isoformat().replace("+00:00", "Z"),
        "expires_utc": expires.isoformat().replace("+00:00", "Z"),
        "max_windows": n,
        "max_total_exposure_usd": exposure,
        "max_daily_loss_usd": daily_loss,
    }
    if rolling:
        doc["last_open_utc"] = starts[-1].isoformat().replace("+00:00", "Z")
    else:
        doc["window_starts"] = [t.isoformat().replace("+00:00", "Z")
                                for t in starts]
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=4)
    ap.add_argument("--start")
    ap.add_argument("--nonce")
    ap.add_argument("--exposure", default="0.45")
    ap.add_argument("--daily-loss", default="1.00")
    ap.add_argument("--rolling", action="store_true",
                    help="REFUSED FOR BTC5m, and kept only so the refusal "
                         "is explicit rather than silent: bounding the "
                         "campaign with last_open_utc passes the campaign "
                         "planner but is rejected on every ORDER by the "
                         "claim layer (BTC5M_WINDOWS_NOT_ENUMERATED). The "
                         "15-minute lane accepts that form; this series "
                         "does not")
    ap.add_argument("--strategy", default=ORIGIN.BTC5M,
                    help="which 5-minute series this approval names, e.g. "
                         "ETH5M. One document names ONE series, so arming "
                         "four assets takes four documents. Default BTC5M.")
    a = ap.parse_args(argv)

    #: REBIND THE SERIES BEFORE ANYTHING READS IT. `slug` and `build`
    #: close over this module global, so it has to be set before either
    #: runs -- which is why this sits at the top of main and not beside
    #: the first use.
    global SERIES
    want = str(a.strategy).strip().upper()
    if want not in REG.BY_STRATEGY:
        print("--strategy %r is not a registered strategy; known: %s"
              % (want, ",".join(sorted(REG.BY_STRATEGY))), file=sys.stderr)
        return 2
    row = REG.BY_STRATEGY[want]
    if row.horizon_s != 300.0:
        print("--strategy %r is a %ss series; this generator is the "
              "FIVE-MINUTE one" % (want, row.horizon_s), file=sys.stderr)
        return 2
    SERIES = row.prefix
    if SERIES not in CA.ALLOWED_SERIES:
        print("series %r is not in the approval allowlist" % SERIES,
              file=sys.stderr)
        return 2
    print("series: %s (%s)" % (SERIES, want))

    ceiling = CA.max_windows_for(SERIES)
    if not (1 <= a.n <= ceiling):
        print("n must be 1..%d (l5_campaign_approval.max_windows_for(%r))"
              % (ceiling, SERIES), file=sys.stderr)
        return 2
    start = (dt.datetime.fromisoformat(a.start.replace("Z", "+00:00"))
             if a.start else next_boundary())
    if start.minute % 5 or start.second or start.microsecond:
        print("--start must be a 5-minute boundary", file=sys.stderr)
        return 2
    nonce = a.nonce or ("b5m-" + secrets.token_hex(12))
    if len(nonce) < 8:
        print("nonce must be at least 8 characters", file=sys.stderr)
        return 2

    doc = build(a.n, start, nonce, a.exposure, a.daily_loss,
                rolling=a.rolling)
    first = slug(start)

    # ---- verify against the REAL gate, exactly as the service will read it
    os.environ[CA.APPROVAL_ENV] = json.dumps(doc)
    verdict = CA.campaign_decision(first, now=dt.datetime.now(UTC),
                                   reserve=False)
    ok = bool(verdict.get("present") and verdict.get("ok"))

    # ---- AND against the PER-ORDER claim layer, which is a stricter gate.
    # The campaign planner and the order claim are two different checks, and
    # passing the first says nothing about the second. A ROLLING document
    # clears `campaign_decision` -- the runner plans its windows happily --
    # and is then refused on EVERY order by
    # `l5_live_authorization.claim_btc5m_submission` with
    # BTC5M_WINDOWS_NOT_ENUMERATED, because for this series "an interval is
    # not an explicit approval".
    #
    # Observed live 2026-11-12 17:30z/17:35z: a self-verified rolling
    # document armed the transport, planned 8 windows, chose OPEN_FAVORITE
    # on every step with EV_AND_ROOM_OK -- and submitted nothing at all,
    # because each order was refused at the claim. The document was valid
    # and useless, and the generator had said YES.
    #
    # This tests the SAME predicate the claim layer tests rather than
    # restating the rule, so the two cannot drift apart.
    scope_now = dict(verdict.get("scope") or {})
    enumerated = bool(scope_now.get("window_starts"))
    claim_ok = ok and enumerated

    print("=" * 72)
    print("BTC5m CAMPAIGN APPROVAL  --  %d window(s), %s, first %s"
          % (a.n, "ROLLING" if a.rolling else "ENUMERATED", first))
    print("=" * 72)
    if a.rolling:
        print("  last_open_utc  %s" % doc["last_open_utc"])
        print("  the runner takes the next %d windows as they come, and may"
              % a.n)
        print("  open none after that time. Set L5_BTC5M_CAMPAIGN=ON and")
        print("  leave L5_BTC5M_MARKET empty.")
    else:
        for i, t in enumerate(doc["window_starts"], 1):
            print("  %2d. %s   %s" % (i, t, slug(
                dt.datetime.fromisoformat(t.replace("Z", "+00:00")))))
    print()
    print("SELF-CHECK against l5_campaign_approval.campaign_decision:")
    print("   campaign planner accepted: %s" % ("YES" if ok else "NO"))
    if not ok:
        print("   reason  : %s" % (verdict.get("reason")
                                   or verdict.get("why")))
    print("   per-order claim accepted : %s" % ("YES" if claim_ok else "NO"))
    if ok and not enumerated:
        print("   reason  : BTC5M_WINDOWS_NOT_ENUMERATED -- the approval")
        print("             must list each window it authorizes; an")
        print("             interval is not an explicit approval. This")
        print("             document would arm the service and then have")
        print("             EVERY order refused. Drop --rolling.")
    sc = dict((verdict.get("scope") or {}))
    for k in ("max_windows", "max_total_exposure_usd", "max_daily_loss_usd",
              "expires_utc", "last_open_utc"):
        if k in sc:
            print("   %-24s %s" % (k, sc[k]))
    if sc.get("clamped"):
        red = [k for k, v in sc["clamped"].items() if v]
        print("   clamped by code ceilings: %s" % (red or "nothing"))
    print()
    if not claim_ok:
        print()
        print("!" * 72)
        print("NOT USABLE FOR BTC5m. The document is printed below only so")
        print("you can see what was rejected; do not set it.")
        print("!" * 72)
    print()
    print("NOTE: the service treats this as STALE one hour after issued_utc")
    print("      (%s). Generate it shortly before you set it."
          % doc["issued_utc"])
    print()
    print("-" * 72)
    print("Set this as L5_CAMPAIGN_APPROVAL (single line):")
    print("-" * 72)
    print(json.dumps(doc, separators=(",", ":")))
    print()
    print("Then, to run every window above rather than one:")
    print("   L5_BTC5M_ENTRY_MODE = ACCUMULATE")
    print("   L5_BTC5M_CAMPAIGN   = ON")
    print("   L5_BTC5M_MARKET     = (leave EMPTY)")
    print("   L5_BTC5M_LIVE       = ARMED_BY_OWNER")
    print("   L5_SHADOW_ONLY      = 0")
    print("   plus: release both shared halts, and touch")
    print("         deploy/l5_btc5m_live/DEPLOY_TRIGGER to ship the code")
    return 0 if claim_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
