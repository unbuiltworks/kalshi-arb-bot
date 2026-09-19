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
from typing import Any, List, Optional

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


#: which cohorts carry a corrected-timing arm alongside the frozen lane.
#: BTC5m is deliberately absent: its window is 300s, so [0, 300] and [0, 900]
#: are the same band there and a second arm would be a duplicate of the first
#: dressed up as a comparison.
CORRECTION_ARMS = {"BTC15m": "L5_ADAPTED_GEMINI_BTC15M_V2"}


class _Arm:
    """One decision record: its own guard, inventory and hypothetical book.

    ARMS SHARE THE TAPE AND NOTHING ELSE. Both see the same prints, the same
    book and the same depth on the same cycle, which is the only way the
    difference between them can be attributed to the one field that differs.
    Sharing an inventory or a book would make the second arm a function of
    the first's fills and the comparison would be measuring itself.

    `sp is None` means the frozen Model A exactly as `plan` defaults to it --
    the control's code path is unchanged, not re-implemented.
    """

    __slots__ = ("label", "sp", "guard", "inv", "hyp", "error")

    def __init__(self, label, sp, *, symbol, cohort_prefix, policy):
        self.label = label
        self.sp = sp
        self.error = None
        self.guard = CP.WindowGuard(symbol, auth_id="SHADOW",
                                    cohort_prefix=cohort_prefix)
        self.inv = CP.Inventory()
        self.hyp = HY.HypotheticalBook(symbol=symbol, policy=policy)


def correction_spec(cohort) -> Optional[Any]:
    """The corrected spec for this cohort, or None if it has no correction.

    Probe-scoped size and residual are applied exactly as they are to the
    control, so the two arms differ in the corrected field and in nothing
    else -- including nothing about size.
    """
    label = CORRECTION_ARMS.get(getattr(cohort, "name", None))
    if not label:
        return None
    from src.l5_surface import params as PR
    probe = CP.probe_params(PR.load_frozen()[label])
    # probe_params re-labels whatever it is given to the probe's own name,
    # which for the control is exactly right and for an arm would erase the
    # one thing its evidence has to say: WHICH spec produced this decision.
    return PR.SurfaceParams(
        label=label, kind="ADAPTED", params=probe.params,
        notes=("%s under the probe's own size and residual; %s"
               % (label, probe.notes or "")))


def _corrects(label: str) -> Optional[str]:
    """Which lane this arm corrects, named in every record it writes."""
    from src.l5_surface import lanes as LN
    c = LN.CORRECTIONS.get(label) or {}
    return c.get("corrects")


def _band_of(arm: _Arm):
    """The one field that differs, restated wherever the arm is reported."""
    try:
        return arm.sp.get("TTE_policy")["quote_between_tte_s"]
    except Exception:                                            # noqa: BLE001
        return None


def _step_arm(arm: _Arm, *, symbol, tte, book, depth, flow, policy):
    """One cycle for one arm. No venue call, no session, no mutation."""
    from src.gemini_execution import tape_replay as TR
    # THE PRINTS CAME FIRST -- see the control path below; the ordering
    # discipline is the same for every arm and for the same reason.
    fills = arm.hyp.consume(flow.get("prints") or [], inv=arm.inv, book=book,
                            depth=depth, tte_s=tte)
    ms = CP.state_from_venue(symbol, tte, arm.inv, book=book)
    kw = {"now": time.time()}
    if arm.sp is not None:
        kw["sp"] = arm.sp
    turn = arm.guard.step(ms, arm.inv, **kw)
    wanted = list(turn.get("actions") or [])
    vetoed = []
    if policy == TR.POLICY_B and arm.inv.residual() != 0:
        keep = [a for a in wanted if a.get("action") == CP.COMPLETE]
        vetoed = [a for a in wanted if a.get("action") != CP.COMPLETE]
        turn = {**turn, "actions": keep}
    acted = list(turn.get("actions") or [])
    arm.hyp.retarget(acted, now_ms=int(time.time() * 1000), depth=depth)
    return {
        "market_state": ms, "turn": turn, "wanted": wanted, "vetoed": vetoed,
        "acted": acted, "fills": fills,
        "decision": {"turn": turn, "policy": policy,
                     "model_a_wanted": wanted, "vetoed_by_adapter": vetoed,
                     "plan": {"keep": [],
                              "place": [{"rung": a} for a in acted],
                              "cancel": [], "mutations": len(acted)}},
    }


def _step_arms(arms, *, symbol, tte, book, depth, flow, policy):
    """Every arm's record for one cycle, with each arm's failure contained.

    An arm that raises is RECORDED and the remaining arms still step. A
    research arm must not be able to take down the 24/7 collector it rides
    on, and "must not" has to be a property of this function rather than of
    an intention.
    """
    out = {}
    for arm in arms:
        try:
            st = _step_arm(arm, symbol=symbol, tte=tte, book=book,
                           depth=depth, flow=flow, policy=policy)
        except Exception as exc:                                 # noqa: BLE001
            arm.error = "%s: %s" % (type(exc).__name__, exc)
            out[arm.label] = {"error": arm.error}
            continue
        out[arm.label] = {
            "corrects": _corrects(arm.label),
            "quote_between_tte_s": _band_of(arm),
            "decision": st["decision"],
            "model_a_wanted": st["wanted"],
            "vetoed_by_adapter": st["vetoed"],
            "hypothetical_fills": st["fills"],
            "hypothetical_rungs_live": sorted(arm.hyp.live),
            "inventory": arm.inv.report(),
        }
    return out


async def one_window(runner: CR.ContinuousRunner, *, cycles: int,
                     interval_s: float, cohort=None,
                     policy: str = "FROZEN_L5_NATIVE",
                     on_cycle=None) -> dict:
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

    # THE CORRECTED-TIMING ARM, if this cohort has one. It is strictly
    # additive: it has its own guard, inventory and book, it is never
    # consulted by the control, and it cannot stop the window -- a cohort
    # that cannot build it records why and runs the control alone, because a
    # research arm must never be able to take down a collector.
    arms: List[_Arm] = []
    arms_error = None
    try:
        csp = correction_spec(cohort)
        if csp is not None:
            arms.append(_Arm(csp.label, csp, symbol=symbol,
                             cohort_prefix=(("GEMI-%s" % cohort.prefix)
                                            if cohort else None),
                             policy=policy))
    except Exception as exc:                                     # noqa: BLE001
        arms_error = "%s: %s" % (type(exc).__name__, exc)

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
        # ---- the corrected arm(s), on THIS cycle's tape and book. Recorded
        # beside the control's decision, never merged into it.
        if arms:
            row["correction_arms"] = _step_arms(
                arms, symbol=symbol, tte=tte, book=book, depth=depth,
                flow=flow, policy=policy)
        if arms_error:
            row["correction_arms_error"] = arms_error
        rows.append(row)
        if on_cycle is not None:
            # LIVE, not end-of-window. rungs_resting only means anything while
            # the window is still running; by the time the record is written
            # the surface has been withdrawn and the answer is always zero.
            on_cycle(symbol=symbol, cycle=n, tte_s=tte, inv=inv, hyp=hyp,
                     book_readable=bool(row["habitat"].get("book_readable")),
                     book_why=row["habitat"].get("book_why"), row=row)
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
         "correction_arms": {
             arm.label: {"corrects": _corrects(arm.label),
                         "quote_between_tte_s": _band_of(arm),
                         "hypothetical": arm.hyp.report(),
                         "inventory": arm.inv.report(),
                         "error": arm.error}
             for arm in arms} or None,
         "correction_arms_error": arms_error,
         "correction_arms_labels": (
             "A SECOND DECISION RECORD ON THE SAME TAPE, not a second lane. "
             "It differs from the control in quote_between_tte_s and in "
             "nothing else, it is shadow-only and unauthorized for any live "
             "path, and mutations_sent is zero for every arm."
             if arms else None),
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


#: how often a cohort prints its compact heartbeat, in cycles
HEARTBEAT_EVERY = 5

#: a cohort that raises backs off before trying again. It never gives up:
#: this is a read-only collector and its job is to be running tomorrow.
COHORT_BACKOFF_S = (5.0, 15.0, 30.0, 60.0, 120.0)

#: THE FLOOR, and it is not decorative. A cohort whose window raises
#: immediately retries immediately, and an immediate retry inside a shared
#: event loop is a hot loop that STARVES THE SIBLING COHORT -- the one thing
#: putting both families in one process is not allowed to do. A test drove
#: the backoff to zero and watched a broken BTC15m spin 353,506 times while
#: BTC5m made no progress at all. The floor makes that unreachable whatever
#: the table above says.
MIN_BACKOFF_S = 1.0

#: a failing cohort reports, it does not flood. After this many consecutive
#: failures with the same error the heartbeat goes quiet until the error
#: changes or the cohort recovers.
ERROR_QUIET_AFTER = 3


class CohortHeartbeat:
    """One cohort's live state, compact enough to read in a log tail.

    WHY PER COHORT AND NOT PER PROCESS. Two cohorts now share a process, and
    a single merged counter would hide exactly the thing the comparison is
    for: whether BTC5m and BTC15m behave differently. Every field here
    belongs to one contract family and is never summed across them.
    """

    def __init__(self, name: str) -> None:
        self.cohort = name
        self.current_contract = None
        self.cycles = 0
        self.readable = 0
        self.refused = 0
        self.rungs_resting = 0
        self.rungs_rested = 0
        self.first_fills = 0
        self.complete_fills = 0
        self.matched_pairs = 0
        self.residual = "0"
        self.windows = 0
        self.last_rollover = None
        #: per corrected arm, never summed into the control's counters. The
        #: whole point is that the two numbers can differ.
        self.arms = {}
        self.last_error = None
        self.errors = 0
        self.restarts = 0
        self._repeats = 0

    # -------------------------------------------------------- live updates
    def cycle(self, *, symbol, cycle, tte_s, inv, hyp, book_readable,
              book_why, row=None) -> None:
        self.current_contract = symbol
        if row is not None:
            self.note_arms(row)
        self.cycles += 1
        if book_readable:
            self.readable += 1
        else:
            self.refused += 1
        self.rungs_resting = len(hyp.live)
        self.rungs_rested = hyp.placed + hyp.requoted
        rep = hyp.report()
        self.first_fills = rep["first_fills"]
        self.complete_fills = rep["complete_fills"]
        r = inv.report()
        self.residual = str(r.get("directional_residual"))
        if cycle % HEARTBEAT_EVERY == 0:
            self.emit(tte_s=tte_s)

    def note_arms(self, row) -> None:
        """Live per-arm counters, read from the arm's OWN books."""
        for label, rec in (row.get("correction_arms") or {}).items():
            a = self.arms.setdefault(label, {"first_fills": 0,
                                             "COMPLETE_fills": 0,
                                             "matched_pairs": 0,
                                             "residual": "0",
                                             "rungs_resting": 0,
                                             "error": None})
            if rec.get("error"):
                a["error"] = rec["error"]
                continue
            a["rungs_resting"] = len(rec.get("hypothetical_rungs_live") or [])
            inv = rec.get("inventory") or {}
            a["residual"] = str(inv.get("directional_residual"))

    def window_done(self, w) -> None:
        self.windows += 1
        for label, rec in (w.get("correction_arms") or {}).items():
            a = self.arms.setdefault(label, {"first_fills": 0,
                                             "COMPLETE_fills": 0,
                                             "matched_pairs": 0,
                                             "residual": "0",
                                             "rungs_resting": 0,
                                             "error": None})
            h = rec.get("hypothetical") or {}
            a["first_fills"] = h.get("first_fills", a["first_fills"])
            a["COMPLETE_fills"] = h.get("complete_fills", a["COMPLETE_fills"])
            a["rungs_rested"] = h.get("rungs_rested")
            a["band"] = rec.get("quote_between_tte_s")
            a["corrects"] = rec.get("corrects")
            a["error"] = rec.get("error") or a.get("error")
            inv = rec.get("inventory") or {}
            a["residual"] = str(inv.get("directional_residual"))
            try:
                if float(inv.get("matched_qty") or 0) > 0:
                    a["matched_pairs"] += 1
            except (TypeError, ValueError):
                pass
        self.last_rollover = {"symbol": w.get("symbol"),
                              "state": w.get("state"),
                              "finished_utc": w.get("finished_utc")}
        inv = w.get("inventory") or {}
        try:
            if float(inv.get("matched_qty") or 0) > 0:
                self.matched_pairs += 1
        except (TypeError, ValueError):
            pass
        self.emit()

    def fail(self, exc) -> None:
        self.errors += 1
        err = "%s: %s" % (type(exc).__name__, exc)
        same = (self.last_error or {}).get("err") == err
        self._repeats = (self._repeats + 1) if same else 0
        self.last_error = {"err": err,
                           "utc": datetime.now(timezone.utc).isoformat(),
                           "consecutive": self._repeats + 1}
        # REPORT, DO NOT FLOOD. The same error every backoff for an hour is
        # one fact, not seven hundred lines of it.
        if self._repeats < ERROR_QUIET_AFTER:
            self.emit()

    def emit(self, *, tte_s=None) -> None:
        print("L5_COHORT_HB " + json.dumps({
            "cohort": self.cohort,
            "current_contract": self.current_contract,
            "tte_s": (round(tte_s, 1) if isinstance(tte_s, (int, float))
                      else None),
            "windows": self.windows,
            "cycles": self.cycles,
            "readable": self.readable,
            "refused": self.refused,
            "rungs_resting": self.rungs_resting,
            "rungs_rested": self.rungs_rested,
            "first_fills": self.first_fills,
            "COMPLETE_fills": self.complete_fills,
            "matched_pairs": self.matched_pairs,
            "residual": self.residual,
            "arms": self.arms or None,
            "last_rollover": self.last_rollover,
            "last_error": self.last_error,
            "errors": self.errors,
            "restarts": self.restarts,
            "mutations_sent": 0,
            "labels": ("SHADOW/HYPOTHETICAL. No order exists. first_fills, "
                       "COMPLETE_fills and matched_pairs count rungs that "
                       "were never placed; none is submitted, filled, "
                       "settled or realized."),
            "arms_labels": (("`arms` is the corrected-timing shadow arm, "
                             "reported SEPARATELY and never summed into the "
                             "counters above. mutations_sent is zero for it "
                             "too.") if self.arms else None),
        }, default=str), flush=True)


def _cohort_paths(root: str, cohort_name: str):
    """Independent evidence paths, one directory per family.

    BTC15m keeps the exact path it has been writing to on the volume since
    2026-09-19, so this change adds a lane rather than orphaning a lane's
    history: root=/state gives /state/gemini-btc15m/continuous_v1 as before.
    """
    base = os.path.join(root, "gemini-%s" % cohort_name.lower())
    return (os.path.join(base, "continuous_v1"),
            os.path.join(base, "%s_continuous.json" % cohort_name.lower()))


async def cohort_loop(a, cohort_name: str) -> None:
    """ONE cohort, for ever, isolated from every other cohort.

    FAILURE ISOLATION IS THE POINT. Each cohort owns its runner, its entry
    band, its evidence directory, its guard, its inventory and its
    hypothetical book -- nothing is shared but the event loop and the
    process. A cohort that raises records the error on its own heartbeat,
    backs off and builds a FRESH runner; it cannot stop, reset or even slow
    the other one, and a test asserts that by making one cohort raise every
    time while the other completes its windows.
    """
    from src.gemini_execution import contract_cohort as CC
    co = CC.resolve(cohort_name)
    band = ((a.band_lo, a.band_hi) if a.band_lo is not None
            else co.entry_band)
    ev, out = _cohort_paths(a.state_root, co.name)
    hb = CohortHeartbeat(co.name)
    print("COHORT %s prefix=%s grid=%dm band=%s policy=%s evidence=%s" % (
        co.name, co.prefix, co.minutes, band, a.policy, ev), flush=True)
    forever = (a.windows == 0)
    attempt = 0
    while True:
        runner = CR.ContinuousRunner(
            mode=CR.SHADOW, budget=CR.RiskBudget(max_windows=a.windows),
            entry_band=band, evidence_dir=ev)
        try:
            while True:
                adm = CR.admits_another_window(runner.budget, runner.state)
                if not forever and not adm["admits"]:
                    runner.stop(adm["state"] or CR.COMPLETED,
                                adm["why"] or "done")
                    break
                w = await one_window(runner, cycles=a.cycles,
                                     interval_s=a.interval, cohort=co,
                                     policy=a.policy, on_cycle=hb.cycle)
                _print_window(w)
                hb.window_done(w)
                attempt = 0
                if runner.state.stopped:
                    # A STOP IS NOT AN EXIT for a read-only collector. It is
                    # recorded, this cohort gets a fresh runner, and the
                    # other cohort never notices.
                    hb.restarts += 1
                    hb.last_error = {"err": "runner stopped: %s"
                                            % (runner.state.why,),
                                     "utc": datetime.now(
                                         timezone.utc).isoformat()}
                    hb.emit()
                    break
            if not forever:
                break
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                 # noqa: BLE001
            hb.fail(exc)
            wait = max(MIN_BACKOFF_S,
                       COHORT_BACKOFF_S[min(attempt,
                                            len(COHORT_BACKOFF_S) - 1)])
            attempt += 1
            await asyncio.sleep(wait)
            continue
        try:
            rep = runner.report()
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=2, default=str)
        except OSError as exc:                                   # noqa: BLE001
            hb.fail(exc)
        if not forever:
            return
        await asyncio.sleep(1.0)


def _print_window(w) -> None:
    """The per-window line. Unchanged in content; moved so both the single
    and the multi-cohort paths emit exactly the same thing."""
    h = w.get("hypothetical") or {}
    iv = w.get("inventory") or {}
    hb = w.get("habitat") or {}
    why = (hb.get("refusal_reasons") or ["-"])[0]
    print("WINDOW %s tte=%s cycles=%s -> %s | HYP rested=%s fills=%s "
          "first=%s complete=%s | matched=%s residual=%s basis=%s "
          "| books=%s/%s refused=%s" % (
              w.get("symbol"), w.get("entry_tte_s"), w.get("cycles"),
              w.get("state"), h.get("rungs_rested"), h.get("fills"),
              h.get("first_fills"), h.get("complete_fills"),
              iv.get("matched_qty"), iv.get("directional_residual"),
              iv.get("effective_matched_pair_cost"),
              hb.get("book_readable_cycles"), hb.get("cycles"), why),
          flush=True)


async def multi_async(a) -> int:
    """Every cohort concurrently, each in its own task.

    `return_exceptions=True` is deliberate: a task that somehow escapes its
    own handler must not cancel its sibling through gather.
    """
    names = [n.strip() for n in a.cohorts.split(",") if n.strip()]
    print("DUAL_COHORT_SHADOW cohorts=%s policy=%s state_root=%s" % (
        names, a.policy, a.state_root), flush=True)
    await asyncio.gather(*[cohort_loop(a, n) for n in names],
                         return_exceptions=True)
    return 0


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
        # THE LEDGER GOES TO STDOUT, not only to the evidence file. A lane
        # running on Railway persists to a volume no analysis container can
        # mount, so without this its recycling evidence is unreachable until
        # someone shells into it. HYPOTHETICAL throughout -- see
        # hypothetical.py; nothing here is filled, settled or realized.
        h = w.get("hypothetical") or {}
        iv = w.get("inventory") or {}
        hb = w.get("habitat") or {}
        # WHY THERE WERE NO RUNGS, not just that there were none. A window
        # that rested nothing because the venue's book was CROSSED on every
        # cycle and one that rested nothing because the policy declined a
        # readable book are different findings, and on a lane whose evidence
        # file cannot be reached this line is the only place they differ.
        why = (hb.get("refusal_reasons") or ["-"])[0]
        print("WINDOW %s tte=%s cycles=%s -> %s | HYP rested=%s fills=%s "
              "first=%s complete=%s | matched=%s residual=%s basis=%s "
              "| books=%s/%s refused=%s" % (
                  w.get("symbol"), w.get("entry_tte_s"), w.get("cycles"),
                  w.get("state"), h.get("rungs_rested"), h.get("fills"),
                  h.get("first_fills"), h.get("complete_fills"),
                  iv.get("matched_qty"), iv.get("directional_residual"),
                  iv.get("effective_matched_pair_cost"),
                  hb.get("book_readable_cycles"), hb.get("cycles"), why),
              flush=True)
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
    # ---- MULTI-COHORT. Supplying --cohorts takes the dual path; leaving it
    # unset keeps the single-cohort path byte-for-byte, which is what every
    # existing invocation and every existing test uses.
    ap.add_argument("--cohorts", default=None,
                    help="comma-separated families to run CONCURRENTLY, each "
                         "with its own runner, band, guard, inventory and "
                         "evidence directory (e.g. btc5m,btc15m)")
    ap.add_argument("--state-root", default="analysis/gemini",
                    help="parent of the per-cohort evidence directories; "
                         "each family gets <root>/gemini-<cohort>/")
    a = ap.parse_args(argv)
    if a.cohorts:
        return asyncio.run(multi_async(a))
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
