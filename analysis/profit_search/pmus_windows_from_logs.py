"""Assemble PMUS 15m up/down windows from the live tape service's echoed logs.

WHY FROM LOGS. `pmus-btc15m-tape` runs read-only against the public PMUS
gateway with `--echo-rows`, and has NO VOLUME mounted, so its `--out-dir` is
ephemeral container disk. The echoed stream is therefore the only durable
source, bounded by log retention rather than by instrumentation.

WHAT EACH WINDOW CARRIES. The tape quotes the YES token, so the conversion into
the engine's own-cost convention is direct and is done once, here:

    yes_bid     = bids[0][0]       yes_bid_qty = bids[0][1]
    yes_ask     = offers[0][0]  => no_bid      = 1 - yes_ask
                                  no_bid_qty   = offers[0][1]

SETTLEMENT IS READ FROM THE TERMINAL PRINT, AFTER THE FACT ONLY. A window is
kept only once the venue has declared it `MARKET_STATE_EXPIRED`, and the label
is that row's `last`: ~0.99 means YES settled 1, ~0.01 means YES settled 0.
Anything else is UNRESOLVED and the window is dropped rather than guessed. The
label is attached to the window as a whole and is never visible to a decision
inside it -- the replay only reads `yes_won` after its final frame.

NO PRINTS, AND WHY THAT IS NOW FINE. This stream carries quotes and depth, not
a trade tape, so a maker queue cannot be simulated from volume. It no longer
needs to be: P(fill) comes from `pmus_execution_model.CalibratedFillModel`,
measured on 965 of our own real submitted orders, which is the correction this
whole pass exists to make.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from decimal import Decimal as D

OPEN = "MARKET_STATE_OPEN"
EXPIRED = "MARKET_STATE_EXPIRED"
CLOSED = "MARKET_STATE_CLOSED"


def _rows(path):
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    for r in (blob.get("deploy") or []):
        m = r.get("message") or ""
        i = m.find("{")
        if i < 0:
            continue
        try:
            yield json.loads(m[i:])
        except json.JSONDecodeError:
            continue


def _ts(s):
    import datetime as dt
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def _close_of(slug):
    """A slug names the window's OPEN (…-2026-10-25-1745z); close is +15m."""
    import datetime as dt
    tail = slug.rsplit("-", 1)[-1]          # '1745z'
    date = slug.rsplit("-", 2)[-2]          # '25'
    parts = slug.split("-")
    y, mo, d = int(parts[-4]), int(parts[-3]), int(parts[-2])
    hh, mm = int(tail[:2]), int(tail[2:4])
    o = dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc)
    return o.timestamp(), o.timestamp() + 15 * 60


def assemble(paths):
    by_slug = {}
    for p in paths:
        for o in _rows(p):
            slug = o.get("slug")
            if not slug or "-updown-15m-" not in slug:
                continue
            by_slug.setdefault(slug, []).append(o)

    windows, dropped = [], {}
    for slug, rows in sorted(by_slug.items()):
        rows.sort(key=lambda o: str(o.get("t")))
        # --- settlement: the terminal print, and only once EXPIRED
        yes_won = None
        for o in rows:
            if o.get("state") == EXPIRED and o.get("last") not in (None, "None"):
                last = D(str(o["last"]))
                if last >= D("0.90"):
                    yes_won = 1
                elif last <= D("0.10"):
                    yes_won = 0
                break
        if yes_won is None:
            dropped[slug] = "UNRESOLVED_no_expired_terminal_print"
            continue

        try:
            t_open, t_close = _close_of(slug)
        except (ValueError, IndexError):
            dropped[slug] = "UNPARSEABLE_SLUG"
            continue

        bbo = []
        for o in rows:
            if o.get("kind") != "book" or o.get("state") != OPEN:
                continue
            bids, offers = o.get("bids") or [], o.get("offers") or []
            if not bids or not offers:
                continue
            t = _ts(o["t"])
            if t >= t_close:
                continue
            yb, ybq = D(str(bids[0][0])), D(str(bids[0][1]))
            ya, yaq = D(str(offers[0][0])), D(str(offers[0][1]))
            nb = D(1) - ya
            if yb <= 0 or nb <= 0 or yb >= 1 or nb >= 1:
                continue
            bbo.append([t, str(yb), str(ybq), str(nb), str(yaq)])
        if len(bbo) < 5:
            dropped[slug] = f"TOO_FEW_FRAMES({len(bbo)})"
            continue

        windows.append(dict(market=slug, series="PMUS-BTC-UPDOWN-15M",
                            open=t_open, close=t_close, yes_won=yes_won,
                            settle_src="terminal_print_expired",
                            bbo=bbo, prints=[]))
    windows.sort(key=lambda w: w["close"])
    return windows, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    paths = [p for g in a.logs for p in glob.glob(g)]
    ws, dropped = assemble(paths)
    json.dump(ws, open(a.out, "w"))
    import datetime as dt
    print(f"log files {len(paths)}  windows kept {len(ws)}  dropped {len(dropped)}")
    for s, why in sorted(dropped.items()):
        print(f"  dropped {s}: {why}")
    if ws:
        print(f"span {dt.datetime.fromtimestamp(ws[0]['close'], dt.timezone.utc)}"
              f" -> {dt.datetime.fromtimestamp(ws[-1]['close'], dt.timezone.utc)}")
        fr = sorted(len(w["bbo"]) for w in ws)
        print(f"frames/window: median {fr[len(fr)//2]}  min {fr[0]}  max {fr[-1]}")
        print(f"yes_won: {sum(w['yes_won'] for w in ws)} up / "
              f"{len(ws)-sum(w['yes_won'] for w in ws)} down")
        print(f"-> {a.out}")


if __name__ == "__main__":
    main()
