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

WHAT SHADOW CANNOT PROVE, stated rather than assumed: no order rests, so no
REAL fill can occur and no real inventory can move. This proves discovery,
banding, state maintenance, evidence completeness, rollover and the failure
states -- and, with the ledger below, what the flow WOULD have done to a rung
that was there.

THE HYPOTHETICAL LEDGER. Without it this lane reports zero fills for ever,
which reads as a market that declined our quote rather than as a lane with no
fill mechanism. `hypothetical.HypotheticalBook` drives the SAME queue
accounting `tape_replay` uses offline -- displayed queue first, opposite
aggressor required, price change loses position -- forward against the live
public tape, and moves a SHADOW inventory. Every number it produces is
HYPOTHETICAL and an upper bound; none of it is submitted, filled, settled,
realized or a cost basis.

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
from src.gemini_execution import hypothetical as HY
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
                     interval_s: float, cohort=None,
                     policy: str = "FROZEN_L5_NATIVE") -> dict:
    """Discover -> band -> state -> policy -> rollover, once.

    `cohort` binds the contract FAMILY (BTC5m, BTC15m). `policy` selects the
    execution ADAPTER: the frozen native surface, or GEMINI_RECYCLE, which
    suppresses EXTEND while residual exists and works the COMPLETE instead.
    Model A is asked either way and WHAT IT WANTED IS RECORDED either way, so
    fidelity stays measurable under the adapter.
    """
    from src.gemini_execution import tape_replay as TR
    started = datetime.now(timezone.utc).isoformat()
    sched = await RC.await_entry_band(runner.entry_band, cohort=cohort)
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

    guard = CP.WindowGuard(
        symbol, auth_id="SHADOW",
        cohort_prefix=("GEMI-%s" % cohort.prefix) if cohort else None)
    # the rungs this lane WOULD have rested, and what the tape would have
    # done to them. No order, no session, no write path -- see hypothetical.py
    hyp = HY.HypotheticalBook(symbol=symbol, policy=policy)
    rows, n = [], 0
    while n < cycles:
        n += 1
        tte = RC.tte_of(symbol)()
        if tte is None or tte < CP.WITHDRAW_ALL_TTE_S:
            break
        book = MD.outcome_book(symbol)
        depth = MD.contract_depth(symbol)
        flow = HB.public_flow(symbol)
        # THE PRINTS CAME FIRST. Everything on this tape landed while the
        # PREVIOUS cycle's rungs were resting, so they are walked against
        # those -- before anything is requoted. Retargeting first would let
        # a rung priced now catch a print from eight seconds ago, which is
        # the single easiest way to manufacture fills that never existed.
        hyp_fills = hyp.consume(flow.get("prints") or [], inv=inv, book=book,
                                depth=depth, tte_s=tte)
        # and the policy is asked against the inventory those fills produced
        ms = CP.state_from_venue(symbol, tte, inv, book=book)
        turn = guard.step(ms, inv, now=time.time())
        # ---- GEMINI_RECYCLE: filter what the frozen policy asked for, never
        # add to it. `model_a_wanted` keeps fidelity measurable.
        wanted = list(turn.get("actions") or [])
        vetoed = []
        if policy == TR.POLICY_B and inv.residual() != 0:
            keep = [a for a in wanted if a.get("action") == CP.COMPLETE]
            vetoed = [a for a in wanted if a.get("action") != CP.COMPLETE]
            turn = {**turn, "actions": keep}
        acted = list(turn.get("actions") or [])
        decision = {"turn": turn, "policy": policy,
                    "model_a_wanted": wanted, "vetoed_by_adapter": vetoed,
                    "plan": {"keep": [], "place": [{"rung": a} for a in acted],
                             "cancel": [], "mutations": len(acted)}}
        row = runner.observe_cycle(
            symbol=symbol, tte_s=tte, market_state=ms, depth=depth, flow=flow,
            inventory=inv.report(), working=[], decision=decision)
        row["cycle"] = n
        row["account_readable"] = account_readable
        row["cohort"] = (cohort.name if cohort is not None else None)
        row["execution_policy"] = policy
        row["model_a_wanted"] = wanted
        row["vetoed_by_adapter"] = vetoed
        hyp.retarget(acted, now_ms=int(time.time() * 1000), depth=depth)
        row["hypothetical_fills"] = hyp_fills
        row["hypothetical_rungs_live"] = sorted(hyp.live)
        rows.append(row)
        runner._persist(row)
        await asyncio.sleep(interval_s)

    # ------------------------------------------------- the rollover gate
    after = await _account_state(symbol)
    after_view = EX.canonical_executions(after.get("orders_seen"),
                                         after.get("trades"), symbol=symbol,
                                         fills=after.get("fills"))
    gate = CR.rollover_safe(symbol, {**after, "executions": after_view})
    rolled_on_shadow = False
    if not gate["safe"]:
        # THE GATE EXISTS TO STOP A NEW WINDOW OPENING WHILE THE LAST ONE MAY
        # STILL HAVE LIVE ORDERS. In SHADOW that hazard cannot exist: this
        # module never constructs a session and never calls Driver.place or
        # Driver.cancel, so there is nothing outstanding to protect. The
        # refusal is RECORDED in full and the loop rolls on.
        #
        # THIS IS NARROWLY SHADOW-ONLY. In LIVE the stop is unchanged, and a
        # test pins that a LIVE runner still stops here. Without this, an
        # unreadable account half ends a continuous collector after one
        # window -- which is what it did on the first BTC15m attempt.
        if runner.mode == CR.SHADOW:
            rolled_on_shadow = True
        else:
            runner.stop(gate["state"], gate["why"])
    if gate["safe"] or rolled_on_shadow:
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
         "cohort": (cohort.name if cohort is not None else None),
         "execution_policy": policy,
         "adapter_vetoes": sum(len(r.get("vetoed_by_adapter") or [])
                               for r in rows),
         "waited_s": sched.get("waited_s"), "polls": sched.get("polls"),
         "cycles": len(rows), "account_readable": account_readable,
         "rollover": gate,
         "rolled_over_despite_gate_in_shadow": rolled_on_shadow,
         "rollover_note": (
             "the rollover gate refused and this is SHADOW: no session exists "
             "and no order could be outstanding, so the refusal is recorded "
             "and the loop continues. In LIVE this stops the run."
             if rolled_on_shadow else None),
         "habitat": HB.window_summary(
             samples, rungs_placed=hyp.placed + hyp.requoted,
             fills=hyp.fills, window_s=len(rows) * interval_s),
         "hypothetical": hyp.report(),
         "inventory": inv.report(),
         "inventory_labels": (
             "moved by HYPOTHETICAL fills only. No order was submitted and "
             "nothing here is filled, settled, realized or a cost basis."),
         "state": (CR.COMPLETED if (gate["safe"] or rolled_on_shadow)
                   else gate["state"]),
         "mutations_sent": 0,
         "labels": ("SHADOW: zero orders were sent and no session exists. "
                    "SUBMITTED/FILLED/SETTLED/REALIZED are all zero and a "
                    "recorded plan is none of them.")}
    runner.windows.append(w)
    runner._persist(w)
    return w


async def main_async(a) -> int:
    from src.gemini_execution import contract_cohort as CC
    co = CC.resolve(a.cohort)
    band = ((a.band_lo, a.band_hi) if a.band_lo is not None
            else co.entry_band)
    runner = CR.ContinuousRunner(mode=CR.SHADOW,
                                 budget=CR.RiskBudget(max_windows=a.windows),
                                 entry_band=band,
                                 evidence_dir=a.evidence_dir)
    print("COHORT %s prefix=%s grid=%dm band=%s policy=%s" % (
        co.name, co.prefix, co.minutes, band, a.policy), flush=True)
    forever = (a.windows == 0)
    while True:
        adm = CR.admits_another_window(runner.budget, runner.state)
        if not forever and not adm["admits"]:
            runner.stop(adm["state"] or CR.COMPLETED, adm["why"] or "done")
            break
        w = await one_window(runner, cycles=a.cycles, interval_s=a.interval,
                             cohort=co, policy=a.policy)
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
    ap.add_argument("--windows", type=int, default=2,
                    help="0 = roll continuously, one contract after another")
    ap.add_argument("--cycles", type=int, default=12)
    ap.add_argument("--interval", type=float, default=8.0)
    ap.add_argument("--cohort", default="btc5m",
                    help="contract family: btc5m or btc15m")
    ap.add_argument("--policy", default="FROZEN_L5_NATIVE",
                    help="FROZEN_L5_NATIVE or GEMINI_RECYCLE")
    ap.add_argument("--band-lo", type=float, default=None)
    ap.add_argument("--band-hi", type=float, default=None)
    ap.add_argument("--evidence-dir", default="analysis/gemini/continuous_v1")
    ap.add_argument("--out",
                    default="analysis/gemini/continuous_shadow_soak.json")
    a = ap.parse_args(argv)
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
