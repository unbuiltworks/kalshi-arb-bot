"""Recorded venue books replayed through the REAL runner, tick by tick.

WHAT THIS IS. Every earlier replay on this branch used a stand-alone
simulator of the policy. This one drives `l5_pmus_live_runner.Runner` and
`l5_live_execution.FaithfulExecutor` themselves -- the harness of
`tests/test_l5_runner_pair_and_cancel_state.py`, production's own stage
order, a scripted venue -- over the book of each recorded BTC 15m window
(`data/l5/recent/wide_20261111/books_380.json`, the authenticated feed as
the service logged it, a sample about every 6.6 s). So what it reports is
what the code would have SENT, cancelled and booked, including every gate.

WHAT IS MODELLED, AND HOW CONSERVATIVELY. Only the venue's fills. A resting
order is filled when the recorded touch trades THROUGH its price (strictly
better), never on a touch at the price. That under-counts fills and makes
every first-leg fill an adverse one by construction, so the P&L here is a
pessimistic replay figure. It is not realized, it is not our live record,
and no rebate is assumed. Queue position is not modelled beyond that rule.

WHAT IT IS FOR. Two questions the simulator could not answer:
  1. PRESENCE. On what share of ticks is each side actually quoted, and
     while inventory is unmatched, is a completion order on the book?
  2. REGRESSION. Does a switch change what the real code sends in a way
     nobody decided? (It found one: see `held_side_add_refusal`.)

usage:  python3 analysis/l5/runner_replay_20261111.py [N_WINDOWS] [PROCS]
"""
from __future__ import annotations

import bisect
import collections
import contextlib
import io
import json
import multiprocessing as mp
import os
import pathlib
import re
import sys
import tempfile
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOKS_PATH = ROOT / "data" / "l5" / "recent" / "wide_20261111" / "books_380.json"
OUT_PATH = pathlib.Path(__file__).with_suffix(".json")

WAIVE = {"L5_TS_OWN_EVIDENCE_GATE": "WAIVED_BY_OWNER"}
RECOVERY = {"L5_INVENTORY_RECOVERY": "ON",
            "L5_TS_REQUOTE_KEEPS_ROUND": "ON",
            "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": "ON"}
#: same $0.90 cash, same two live orders, same clip: a completed pair's cash
#: is released so another round can open
RECYCLE = {"L5_CASH_POLICY": "RECYCLE_ON_COMPLETE",
           "L5_HEDGE_CASH_AT_LIMIT": "ON",
           "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON",
           "L5_REOPEN_FLOOR_PER_CONTRACT": "ON", "L5_ACC_MAX_ROUNDS": "32"}
CONFIGS = collections.OrderedDict([
    ("as_deployed", dict(WAIVE)),
    ("recovery_0.98", dict(WAIVE, L5_TS_COMPLETION_MAX_PAIR="0.98",
                           **RECOVERY)),
    ("recovery_1.00", dict(WAIVE, L5_TS_COMPLETION_MAX_PAIR="1.00",
                           **RECOVERY)),
    ("as_deployed+recycle", dict(WAIVE, **RECYCLE)),
    ("recovery_1.00+recycle", dict(WAIVE, L5_TS_COMPLETION_MAX_PAIR="1.00",
                                   **dict(RECOVERY, **RECYCLE))),
])

_BOOKS = None


def books():
    global _BOOKS
    if _BOOKS is None:
        _BOOKS = json.loads(BOOKS_PATH.read_text())
    return _BOOKS


def replay(rows, env, *, step=2.0, start=7.0, end=600.0, on_submit=None):
    """One window. `rows` is [(seconds, bid, ask), ...] of the YES book.

    Returns the per-tick log, the orders sent, the venue's filled quantity
    and dollars paid per side, and the runner's own counters.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import pytest
    import tests.test_l5_ladder_runner as LAD

    ts = [r[0] for r in rows]

    def book_at(t):
        return rows[max(bisect.bisect_right(ts, t) - 1, 0)]

    patch = pytest.MonkeyPatch()
    cwd = os.getcwd()
    os.chdir(str(ROOT))
    try:
        h = LAD.harness(pathlib.Path(tempfile.mkdtemp()), patch, **env)
        LAD.seed(h, 30, D("-0.02"))
        b = book_at(start)
        h.book.set(b[1], b[2])
        if env.get("L5_CASH_POLICY"):
            import tests.test_l5_cash_recycle_on_pair as RC
            h = RC.with_venue_cash(h)
        if on_submit is not None:
            _submit = h.venue.submit

            def submit(intent):
                on_submit(h, intent)
                return _submit(intent)
            h.venue.submit = submit
        log, t = [], start
        h.at(start - 0.5).l5_action()
        while t <= end + 1e-9:
            b = book_at(t)
            bid, ask = D(b[1]), D(b[2])
            h.book.set(b[1], b[2])
            for oid in LAD.resting_ids(h):          # through-only fills
                o = h.venue.by_oid[oid]
                px = D(str(o["price"]))
                if (str(o["side"]) == "UP" and ask < px) or (
                        str(o["side"]) == "DOWN" and bid > D(1) - px):
                    h.venue.fill(oid)
            before = dict(h.r.stats)
            h.tick(t)
            rest = LAD.resting(h)
            log.append({
                "t": t, "bid": b[1], "ask": b[2],
                "up_rest": [str(p) for s, p in rest if s == "UP"],
                "dn_rest": [str(p) for s, p in rest if s == "DOWN"],
                "up": str(h.pos("UP")), "dn": str(h.pos("DOWN")),
                "why": sorted(k[len("controller_open:"):]
                              for k, v in h.r.stats.items()
                              if k.startswith("controller_open:")
                              and k != "controller_open:ENTERED"
                              and v != before.get(k, 0))})
            t += step
        filled, paid = {}, {}
        for side in ("UP", "DOWN"):
            mine = [(o, i) for o, i in h.venue.by_oid.items()
                    if str(i["side"]) == side]
            filled[side] = sum(D(h.venue.cum[o]) for o, _ in mine)
            paid[side] = sum(D(h.venue.cum[o]) * D(str(i["price"]))
                             for o, i in mine)
        return {"log": log, "sent": h.sent(), "filled": filled, "paid": paid,
                "cancelled": len(h.venue.cancelled),
                "stats": dict(h.r.stats)}
    finally:
        os.chdir(cwd)
        patch.undo()


def presence(log):
    """Ticks by state, from the first order of the window on."""
    c, started = collections.Counter(), False
    for r in log:
        u, d = bool(r["up_rest"]), bool(r["dn_rest"])
        if not started and not (u or d):
            continue
        started = True
        net = D(r["up"]) - D(r["dn"])
        if net == 0:
            c["flat:both_sides_quoted" if u and d else
              "flat:one_side_quoted" if u or d else "flat:no_quote"] += 1
            if not (u or d):
                why = [re.sub(r"[0-9]+\.[0-9]+", "N", w) for w in r["why"]]
                c["idle:%s %s" % ("0-480s" if r["t"] < 480 else "480-600s",
                                  (why or ["(no controller record)"])[-1][:48]
                                  )] += 1
        else:
            working = d if net > 0 else u
            c["unmatched:completion_resting" if working
              else "unmatched:no_completion_order"] += 1
    return c


def _one(job):
    slug, name = job
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            res = replay(books()[slug]["rows"], CONFIGS[name])
    except BaseException as exc:                               # noqa: BLE001
        return {"slug": slug, "config": name,
                "fatal": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    up, dn = res["filled"]["UP"], res["filled"]["DOWN"]
    win = books()[slug]["winner"]
    pnl = (up if win == "UP" else dn) - res["paid"]["UP"] - res["paid"]["DOWN"]
    return {"slug": slug, "config": name, "presence": dict(presence(res["log"])),
            "orders": len(res["sent"]), "cancels": res["cancelled"],
            "matched": str(min(up, dn)), "unmatched": str(abs(up - dn)),
            "paid": str(res["paid"]["UP"] + res["paid"]["DOWN"]),
            "pnl": str(pnl)}


def summarise(rows, label):
    ok = [r for r in rows if "fatal" not in r]
    p = collections.Counter()
    for r in ok:
        p.update(r["presence"])
    m = sum(D(r["matched"]) for r in ok)
    u = sum(D(r["unmatched"]) for r in ok)
    um = p["unmatched:completion_resting"] + p["unmatched:no_completion_order"]
    fl = (p["flat:both_sides_quoted"] + p["flat:one_side_quoted"]
          + p["flat:no_quote"])
    pnl = [D(r["pnl"]) for r in ok]
    share = lambda a, b: ("%.0f%%" % (100 * a / b)) if b else "n/a"  # noqa
    print(f"  {label:24s} windows {len(ok):3d}  matched {m:7.2f}  unmatched "
          f"{u:6.2f}  completed {share(float(m), float(m + u)):>4s}  "
          f"completion order resting {share(p['unmatched:completion_resting'], um):>4s}"
          f" of unmatched ticks  flat ticks: both sides "
          f"{share(p['flat:both_sides_quoted'], fl):>4s}, none "
          f"{share(p['flat:no_quote'], fl):>4s}  orders "
          f"{sum(r['orders'] for r in ok):5d}  replay P&L {sum(pnl):+8.4f} "
          f"({100 * float(sum(pnl)) / max(len(ok), 1):+.1f}c/window)"
          + (f"  FATAL {len(rows) - len(ok)}" if len(ok) != len(rows) else ""))
    return p


def main(argv):
    n = int(argv[1]) if len(argv) > 1 else 0
    procs = int(argv[2]) if len(argv) > 2 else 4
    slugs = sorted(books())
    if n and n < len(slugs):
        slugs = slugs[:: len(slugs) // n][:n]
    jobs = [(s, c) for c in CONFIGS for s in slugs]
    with mp.Pool(procs) as pool:
        rows = pool.map(_one, jobs, chunksize=2)
    OUT_PATH.write_text(json.dumps(rows))
    half = len(slugs) // 2
    first, second = set(slugs[:half]), set(slugs[half:])
    print(f"recorded windows {len(slugs)}  ({slugs[0][-16:]} .. "
          f"{slugs[-1][-16:]}); through-only fills; replay, not realized")
    for title, keep in (("ALL", set(slugs)), ("FIRST HALF", first),
                        ("SECOND HALF", second)):
        print(title)
        for name in CONFIGS:
            p = summarise([r for r in rows if r["config"] == name
                           and r["slug"] in keep], name)
            if title == "ALL":
                idle = sorted(((v, k) for k, v in p.items()
                               if k.startswith("idle:")), reverse=True)[:5]
                print("      flat with no quote, by the controller's reason: "
                      + "; ".join("%s %d" % (k[5:], v) for v, k in idle))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
