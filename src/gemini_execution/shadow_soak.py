#!/usr/bin/env python3
"""Continuous L5 SHADOW soak -- the production lifecycle, no order path.

IT LIVES IN THIS PACKAGE ON PURPOSE. `test_gemini_write_path_isolation`
allows the mutation surface in `src/gemini_execution/` and nowhere else, and
this soak drives that package's own lifecycle -- it is execution tooling, not
a research surface. Putting it under `scripts/` and widening that guard would
have loosened a safety test to fit a convenience. Its own no-mutation
property is proven directly instead.

Runs the same sequence a live continuous run would: discover, wait for the
entry band, establish authoritative state, run the FROZEN policy against it,
record the full chain, check the rollover gate, and only then consider the
next window.

WHAT MAKES IT SHADOW: `Driver.place` and `Driver.cancel` are never called,
and no session is ever constructed. The policy is asked what it would do and
the answer is recorded. Nothing here can reach the venue's write path, which
is a property of what it calls, not a flag it sets.

WHAT SHADOW CANNOT PROVE, stated rather than assumed: with nothing resting
there are no fills, so inventory stays flat and the policy stays in OPEN.
KEEP/CANCEL and the fill-driven transitions are not exercised here -- C8B
proved those live. This proves discovery, banding, state maintenance,
evidence completeness, rollover and the failure states.

Read-only credentials are used for the authoritative account reads. When they
are absent the soak still runs on public reads and records the account half
as UNREADABLE rather than as empty.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import continuous_runner as CR
from src.gemini_execution import executions as EX
from src.gemini_execution import habitat as HB
from src.gemini_execution import metadata as MD
from src.gemini_execution import preentry as PE
from src.gemini_execution import run_continuous as RC


async def _account_state(symbol: str):
    """Authoritative account reads, or an explicit UNREADABLE."""
    try:
        return await RC.authoritative_state(symbol)
    except Exception as exc:                                     # noqa: BLE001
        return {"symbol": symbol, "fills": None, "open_orders": None,
                "orders_seen": None, "trades": None, "terminal_seen": None,
                "unreadable_why": "%s: %s" % (type(exc).__name__, exc)}


async def one_window(runner: CR.ContinuousRunner, *, cycles: int,
                     interval_s: float) -> dict:
    """Discover -> band -> state -> policy -> rollover, once."""
    started = datetime.now(timezone.utc).isoformat()
    sched = await RC.await_entry_band(runner.entry_band)
    if not sched.get("in_band"):
        runner.stop(CR.ENTRY_BAND_UNREACHABLE, sched.get("why") or "no band")
        return {"kind": "window", "started_utc": started,
                "state": CR.ENTRY_BAND_UNREACHABLE, "schedule": sched}

    sel = (sched.get("selection") or {}).get("chosen") or {}
    symbol = sel.get("symbol")
    runner.state.windows_started += 1

    # PRE-ENTRY HABITAT, taken before the window commits to anything. It is
    # PUBLIC, read-only, and its result is recorded and then IGNORED: the
    # window proceeds exactly as it would have without it. That is the point
    # -- a selector cannot be evaluated by a run it is already steering.
    pre = PE.read_snapshot(symbol, sched.get("entered_tte_s"))

    # a REAL driver, recovered from REAL authoritative state. It is never
    # given a session, so its mutation path is unreachable from here.
    st = await _account_state(symbol)
    view = EX.canonical_executions(st.get("orders_seen"), st.get("trades"),
                                   symbol=symbol, fills=st.get("fills"))
    if view.get("contradictions"):
        runner.stop(CR.EXECUTION_CONTRADICTION, json.dumps(
            view["contradictions"], default=str)[:300])
        return {"kind": "window", "symbol": symbol,
                "state": CR.EXECUTION_CONTRADICTION, "executions": view}

    inv, why = CP.recover("SHADOW", symbol, EX.as_fills(view))
    if inv is None:
        # UNREADABLE is not flat. In shadow this is recorded and the soak
        # continues on public evidence; in LIVE it would stop the run.
        inv = CP.Inventory()
        account_readable = False
    else:
        account_readable = True

    guard = CP.WindowGuard(symbol, auth_id="SHADOW")
    rows, n = [], 0
    while n < cycles:
        n += 1
        tte = RC.tte_of(symbol)()
        if tte is None or tte < CP.WITHDRAW_ALL_TTE_S:
            break
        book = MD.outcome_book(symbol)
        ms = CP.state_from_venue(symbol, tte, inv, book=book)
        depth = MD.contract_depth(symbol)
        flow = HB.public_flow(symbol)
        turn = guard.step(ms, inv, now=time.time())
        decision = {"turn": turn, "plan": {"keep": [], "place": [
            {"rung": a} for a in (turn.get("actions") or [])],
            "cancel": [], "mutations": len(turn.get("actions") or [])}}
        row = runner.observe_cycle(
            symbol=symbol, tte_s=tte, market_state=ms, depth=depth, flow=flow,
            inventory=inv.report(), working=[], decision=decision)
        row["cycle"] = n
        row["account_readable"] = account_readable
        rows.append(row)
        runner._persist(row)
        await asyncio.sleep(interval_s)

    # ------------------------------------------------- the rollover gate
    after = await _account_state(symbol)
    after_view = EX.canonical_executions(after.get("orders_seen"),
                                         after.get("trades"), symbol=symbol,
                                         fills=after.get("fills"))
    gate = CR.rollover_safe(symbol, {**after, "executions": after_view})
    if not gate["safe"]:
        runner.stop(gate["state"], gate["why"])
    else:
        runner.state.windows_completed += 1

    samples = [r["habitat"] for r in rows]
    w = {"kind": "window", "started_utc": started,
         "pre_entry": pre,
         "realised_flow": PE.realised_window_flow(rows),
         "pre_entry_labels": ("MEASURED BEFORE ENTRY AND NOT ACTED ON. The "
                              "window ran identically to one taken without "
                              "it; admission is unchanged and no threshold "
                              "exists."),
         "finished_utc": datetime.now(timezone.utc).isoformat(),
         "symbol": symbol, "entry_tte_s": sched.get("entered_tte_s"),
         "waited_s": sched.get("waited_s"), "polls": sched.get("polls"),
         "cycles": len(rows), "account_readable": account_readable,
         "rollover": gate,
         "habitat": HB.window_summary(samples, rungs_placed=0, fills=(),
                                      window_s=len(rows) * interval_s),
         "state": (CR.COMPLETED if gate["safe"] else gate["state"]),
         "mutations_sent": 0,
         "labels": ("SHADOW: zero orders were sent and no session exists. "
                    "SUBMITTED/FILLED/SETTLED/REALIZED are all zero and a "
                    "recorded plan is none of them.")}
    runner.windows.append(w)
    runner._persist(w)
    return w


async def main_async(a) -> int:
    runner = CR.ContinuousRunner(mode=CR.SHADOW,
                                 budget=CR.RiskBudget(max_windows=a.windows),
                                 entry_band=(a.band_lo, a.band_hi),
                                 evidence_dir=a.evidence_dir)
    while True:
        adm = CR.admits_another_window(runner.budget, runner.state)
        if not adm["admits"]:
            runner.stop(adm["state"] or CR.COMPLETED, adm["why"] or "done")
            break
        w = await one_window(runner, cycles=a.cycles, interval_s=a.interval)
        print("WINDOW %s tte=%s cycles=%s -> %s" % (
            w.get("symbol"), w.get("entry_tte_s"), w.get("cycles"),
            w.get("state")), flush=True)
        if runner.state.stopped:
            break

    rep = runner.report()
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print("CONTINUOUS_SHADOW " + json.dumps(
        {k: rep[k] for k in ("mode", "windows_started", "windows_completed",
                             "stopped", "why", "elapsed_s")}, default=str),
          flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=2)
    ap.add_argument("--cycles", type=int, default=12)
    ap.add_argument("--interval", type=float, default=8.0)
    ap.add_argument("--band-lo", type=float, default=240.0)
    ap.add_argument("--band-hi", type=float, default=280.0)
    ap.add_argument("--evidence-dir", default="analysis/gemini/continuous_v1")
    ap.add_argument("--out",
                    default="analysis/gemini/continuous_shadow_soak.json")
    a = ap.parse_args(argv)
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
