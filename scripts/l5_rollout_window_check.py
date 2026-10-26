#!/usr/bin/env python3
"""Advisory GO/NO-GO for a configuration rollout during a live campaign.

Pure arithmetic over the wall clock and the window cadence. Imports nothing
from the money path, reads no ledger, changes nothing. See
analysis/l5/DEPLOYMENT_TIMING_SAFEGUARD.md for why this exists.

A window claim is irreversible, so a rollout that swaps the container while a
process holds a still-tradeable claim forfeits that window. Roll out only
after the entry cutoff and with enough room to be healthy before the next
window opens.
"""
import argparse
import datetime as dt

WINDOW_S = 900
#: conservative composite entry cutoff. `entry_cutoff_s` is 600, but
#: `may_enter` stopped at ~T+540 on 2030z and ~T+565 on an earlier 0000z,
#: because the venue's `t_end` can precede start + WINDOW_S.
ENTRY_CUTOFF_S = 540
#: observed healthy rollout latency on l5-pmus-live is 90-140s; a failed
#: snapshot fetch ran 312s. Require room for the bad case.
REQUIRED_MARGIN_S = 300


def verdict(now: dt.datetime):
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_day = (now - midnight).total_seconds()
    into = elapsed_day % WINDOW_S
    start = now - dt.timedelta(seconds=into)
    nxt = start + dt.timedelta(seconds=WINDOW_S)
    margin = WINDOW_S - into
    band_open = start + dt.timedelta(seconds=ENTRY_CUTOFF_S)
    band_close = start + dt.timedelta(seconds=WINDOW_S - REQUIRED_MARGIN_S)
    if into < ENTRY_CUTOFF_S:
        ok, why = False, (
            "inside the entry window (T+%.0fs < T+%ds): the running process "
            "may still open a position, so a swap now forfeits a tradeable "
            "window" % (into, ENTRY_CUTOFF_S))
    elif margin < REQUIRED_MARGIN_S:
        ok, why = False, (
            "only %.0fs until the next window opens, under the %ds a "
            "rollout needs to be healthy first; the replacement would miss "
            "the next claim" % (margin, REQUIRED_MARGIN_S))
    else:
        ok, why = True, (
            "past the entry cutoff (T+%.0fs) with %.0fs before the next "
            "window opens" % (into, margin))
    return ok, why, start, nxt, band_open, band_close


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--at", help="UTC instant, e.g. 2026-10-26T20:59:16Z")
    a = p.parse_args()
    now = (dt.datetime.fromisoformat(a.at.replace("Z", "+00:00"))
           if a.at else dt.datetime.now(dt.timezone.utc))
    ok, why, start, nxt, bo, bc = verdict(now)
    f = "%H:%M:%S"
    print("now                %s" % now.strftime(f))
    print("window start       %s   (T+%.0fs)"
          % (start.strftime(f), (now - start).total_seconds()))
    print("next window opens  %s" % nxt.strftime(f))
    print("safe band          %s - %s" % (bo.strftime(f), bc.strftime(f)))
    print()
    print("%s: %s" % ("GO" if ok else "NO-GO", why))
    if not ok:
        print("next safe band begins %s"
              % (bo if now < bo else bo + dt.timedelta(seconds=WINDOW_S)
                 ).strftime(f))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
