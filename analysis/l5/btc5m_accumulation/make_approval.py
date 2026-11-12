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
  max_windows          <= 8  (l5_campaign_approval.MAX_WINDOWS)
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
      N        how many consecutive 5-minute windows (default 4, max 8)
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

UTC = dt.timezone.utc
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
          daily_loss: str) -> dict:
    issued = dt.datetime.now(UTC).replace(microsecond=0)
    starts = [start + dt.timedelta(minutes=5 * i) for i in range(n)]
    # settle-out grace after the last window, inside the 4 h campaign bound
    expires = starts[-1] + dt.timedelta(minutes=35)
    if (expires - issued).total_seconds() > CA.MAX_CAMPAIGN_S:
        expires = issued + dt.timedelta(seconds=CA.MAX_CAMPAIGN_S - 60)
    return {
        "approved": True,
        "nonce": nonce,
        "series": SERIES,
        "issued_utc": issued.isoformat().replace("+00:00", "Z"),
        "expires_utc": expires.isoformat().replace("+00:00", "Z"),
        "max_windows": n,
        "max_total_exposure_usd": exposure,
        "max_daily_loss_usd": daily_loss,
        "window_starts": [t.isoformat().replace("+00:00", "Z")
                          for t in starts],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=4)
    ap.add_argument("--start")
    ap.add_argument("--nonce")
    ap.add_argument("--exposure", default="0.45")
    ap.add_argument("--daily-loss", default="1.00")
    a = ap.parse_args(argv)

    if not (1 <= a.n <= CA.MAX_WINDOWS):
        print("n must be 1..%d (l5_campaign_approval.MAX_WINDOWS)"
              % CA.MAX_WINDOWS, file=sys.stderr)
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

    doc = build(a.n, start, nonce, a.exposure, a.daily_loss)
    first = slug(start)

    # ---- verify against the REAL gate, exactly as the service will read it
    os.environ[CA.APPROVAL_ENV] = json.dumps(doc)
    verdict = CA.campaign_decision(first, now=dt.datetime.now(UTC),
                                   reserve=False)
    ok = bool(verdict.get("present") and verdict.get("ok"))

    print("=" * 72)
    print("BTC5m CAMPAIGN APPROVAL  --  %d window(s), first %s" % (a.n, first))
    print("=" * 72)
    for i, t in enumerate(doc["window_starts"], 1):
        print("  %2d. %s   %s" % (i, t, slug(
            dt.datetime.fromisoformat(t.replace("Z", "+00:00")))))
    print()
    print("SELF-CHECK against l5_campaign_approval.campaign_decision:")
    print("   accepted: %s" % ("YES" if ok else "NO"))
    if not ok:
        print("   reason  : %s" % (verdict.get("reason")
                                   or verdict.get("why")))
    sc = dict((verdict.get("scope") or {}))
    for k in ("max_windows", "max_total_exposure_usd", "max_daily_loss_usd",
              "expires_utc", "last_open_utc"):
        if k in sc:
            print("   %-24s %s" % (k, sc[k]))
    if sc.get("clamped"):
        red = [k for k, v in sc["clamped"].items() if v]
        print("   clamped by code ceilings: %s" % (red or "nothing"))
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
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
