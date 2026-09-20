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

#: EXECUTION ADAPTATIONS, run as challengers beside the control on every
#: cohort. These change NO decision Model A makes -- not the side, not the
#: size, not whether to quote -- only how the order carrying that decision is
#: placed and whether it is replaced:
#:
#:   A  one tick better, refused whenever that would reach the ask
#:   B  keep a rung already holding its place in line when the desired touch
#:      has moved by at most a tick
#:
#: Offline over 59 contracts under symmetric queue accounting, A+B took the
#: control's 3 out-of-sample fills to 17, its 0 matched windows to 4 and its 0
#: fee-positive pairs to 2. A is the mechanism: 39 of 40 fills rest on a rung
#: with nothing in front of it. B contributes 3 and is carried to be measured
#: prospectively rather than because the offline corpus demanded it.
#: The economic challenger runs beside the control by default and can be
#: switched off without editing code. It never mutates anything either way,
#: and switching it off cannot change a single decision the control makes.
ECON_ARM_ENABLED = os.environ.get("GEMINI_ECON_ARM", "1") not in ("0", "false")

#: the flow-evidence ladder (PP_F0 / PP_F1 / PP_F2 beside PASSIVE_PAIR_V1).
#: Observation only; it can be switched off without touching any other arm.
LADDER_ENABLED = os.environ.get("GEMINI_ECON_LADDER", "1") not in ("0", "false")

EXECUTION_ARMS = ({"label": "EXEC_A_PLUS_B", "improve": True,
                   "preserve": True},)


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

    __slots__ = ("label", "sp", "guard", "inv", "hyp", "error", "improve",
                 "preserve")

    def __init__(self, label, sp, *, symbol, cohort_prefix, policy,
                 improve=False, preserve=False):
        self.label = label
        self.sp = sp
        self.improve = improve
        self.preserve = preserve
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
    # THE GEMINI_ECON_V1 CHALLENGER DRIVES ITSELF. It is the only arm whose
    # decision is not "Model A, optionally re-priced": it asks Model A the same
    # question and then applies venue economics the frozen policy cannot know.
    # Delegating here keeps that logic out of this function entirely, so the
    # control's path below is unchanged.
    if getattr(arm, "IS_ECON_ARM", False):
        return arm.step_compat(symbol=symbol, tte=tte, book=book, depth=depth,
                               flow=flow, policy=policy)
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
    # EXECUTION ADAPTATION A, applied to the price only. The action, leg and
    # size are Model A's and are not touched; an improvement that would reach
    # the ask is refused outright.
    if getattr(arm, "improve", False):
        from src.gemini_execution import ab_arms as AB
        acted = AB.apply_A(acted, book or {})
    arm.hyp.retarget(acted, now_ms=int(time.time() * 1000), depth=depth,
                     preserve=getattr(arm, "preserve", False))
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
            "hypothetical_rungs_rested_cumulative": (arm.hyp.placed
                                                     + arm.hyp.requoted),
            "hypothetical_cumulative": {
                k: arm.hyp.report()[k]
                for k in ("fills", "first_fills", "open_fills",
                          "extend_fills", "complete_fills")},
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
        for spec in EXECUTION_ARMS:
            arms.append(_Arm(spec["label"], None, symbol=symbol,
                             cohort_prefix=(("GEMI-%s" % cohort.prefix)
                                            if cohort else None),
                             policy=policy, improve=spec["improve"],
                             preserve=spec["preserve"]))
        # THE ECONOMIC CHALLENGER, strictly additive like every other arm: its
        # own guard, inventory and hypothetical book, sharing only the tape.
        # It is built LAST so that a failure to construct it cannot prevent the
        # correction and execution arms from running.
        if ECON_ARM_ENABLED and cohort is not None:
            from src.gemini_execution.econ import arm as ECON
            from src.gemini_execution.econ import registry as REG
            # PER-COHORT STATE FROM THE REGISTRY, not built here: the arm is
            # rebuilt every window and its census, hazard, fork ledger, socket
            # and pre-warmer must all outlive that. A socket dropped at window
            # end is exactly what made every next window open blind.
            from src.gemini_execution.econ import policy as ECPOL
            sdir = os.environ.get("GEMINI_ECON_STATE_DIR")
            # TWO ECONOMIC ARMS, on the SAME tape, the SAME cadence and the
            # SAME hypothetical fill model, differing in the spread treatment
            # and in nothing else:
            #   GEMINI_ECON_V1  boolean max_spread 0.08   (unchanged)
            #   PASSIVE_PAIR_V1 no fixed maximum spread; admission is
            #                   yes_bid + no_bid + maker fees <= 1 - min_edge
            # They share the market (one socket, one census) and keep their
            # own coverage, attribution, labels, forks and fill sequences, so
            # neither can contaminate the other's evidence.
            # THE FLOW-EVIDENCE LADDER, observation only. Four rungs sharing
            # the identical passive-pair economic rule, price/TTE policy,
            # inventory limits, cadence and fill tape, differing ONLY in how
            # many WS prints the tape must carry. F5 IS PASSIVE_PAIR_V1 and is
            # not duplicated. The point is not to pick the loosest rung: it is
            # to measure whether the extra flow evidence buys better fills.
            _spec = [(ECON.LABEL, ECPOL.BOOLEAN_VARIANT),
                     (ECON.PASSIVE_PAIR_LABEL, ECPOL.PASSIVE_PAIR_VARIANT)]
            if LADDER_ENABLED:
                _spec += [("PP_%s" % r, r) for r in ("F0", "F1", "F2")]
            for _lbl, _var in _spec:
                arms.append(ECON.GeminiEconArm(
                    symbol=symbol,
                    cohort_prefix="GEMI-%s" % cohort.prefix,
                    policy=policy, cohort_name=cohort.name,
                    state=REG.state_for(cohort.name, arm=_lbl,
                                        state_dir=sdir),
                    variant=_var, label=_lbl,
                    cohort_prefix_grid=cohort.prefix,
                    cohort_minutes=cohort.minutes))
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
        row["hypothetical_rungs_rested_cumulative"] = hyp.placed + hyp.requoted
        row["hypothetical_cumulative"] = {
            k: hyp.report()[k] for k in ("fills", "first_fills", "open_fills",
                                         "extend_fills", "complete_fills")}
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
                     book_why=row["habitat"].get("book_why"), arms=arms)
        runner._persist(row)
        await asyncio.sleep(interval_s)

    # ---- WINDOW END: resolve the economic challenger's open counterfactual
    # forks on the SAME final book both branches are scored against. Contained,
    # like every other arm operation, so an observation module can never end a
    # window.
    econ_finish = {}
    for arm in arms:
        if not getattr(arm, "IS_ECON_ARM", False):
            continue
        try:
            econ_finish[arm.label] = arm.finish(book=book)
        except Exception as exc:                                 # noqa: BLE001
            econ_finish[arm.label] = {"error": "%s: %s"
                                      % (type(exc).__name__, exc)}

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
                         # the economic challenger carries a scoreboard the
                         # other arms have no equivalent of. WITHOUT IT a
                         # starved arm is indistinguishable from a broken one:
                         # both read "rested=0" and neither says which gate
                         # refused.
                         **({"econ": arm.report(),
                             "econ_finish": econ_finish.get(arm.label)}
                            if getattr(arm, "IS_ECON_ARM", False) else {}),
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
         "evidence_cohort": EVIDENCE_COHORT,
         "queue_model": getattr(hyp, "queue_mode", None),
         "evidence_cohort_note": EVIDENCE_COHORT_NOTE,
         "labels": ("SHADOW: zero orders were sent and no session exists. "
                    "SUBMITTED/FILLED/SETTLED/REALIZED are all zero and a "
                    "recorded plan is none of them.")}
    runner.windows.append(w)
    runner._persist(w)
    # A COMPACT CYCLE VIEW FOR THE LIVE BANDED PRINT, attached AFTER the
    # record is persisted and dropped by the caller once printed. The full
    # rows are already on disk one per cycle; carrying them on the window
    # record too would double the evidence and grow `runner.windows`
    # without bound on a lane that runs for ever.
    w["cycles_detail"] = [{
        "cycle": r.get("cycle"), "tte_s": r.get("tte_s"),
        "habitat": {"book_readable": (r.get("habitat") or {}
                                      ).get("book_readable"),
                    "flow": (r.get("habitat") or {}).get("flow")},
        "hypothetical_rungs_rested_cumulative":
            r.get("hypothetical_rungs_rested_cumulative"),
        "correction_arms": {
            k: {"hypothetical_rungs_rested_cumulative":
                v.get("hypothetical_rungs_rested_cumulative"),
                "decision": {"plan": {"place": ((v.get("decision") or {})
                                                .get("plan") or {}
                                                ).get("place") or []}}}
            for k, v in (r.get("correction_arms") or {}).items()},
    } for r in rows]
    return w


#: WHICH EVIDENCE COHORT THIS PROCESS WRITES.
#:
#: Every row written before 2026-09-20 came from a fill simulator that gave a
#: NO rung no queue to clear while a YES rung cleared the real ladder, so its
#: fills, matched pairs and pair bases are upper bounds of unknown size. They
#: are not comparable with rows written after the correction and must never be
#: summed with them. The cohort is stamped on every window and heartbeat so
#: the separation is a property of the evidence rather than of remembering
#: which day it was.
EVIDENCE_COHORT = "CORRECTED_QUEUE_V1"
EVIDENCE_COHORT_NOTE = (
    "rows stamped CORRECTED_QUEUE_V1 use symmetric queue accounting: both "
    "legs queue behind the ladder an aggressor actually consumes. Unstamped "
    "rows predate the 2026-09-20 correction, carry a one-legged queue and do "
    "not count toward this cohort's scoreboard.")

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
        # ---- THREE DIFFERENT CLOCKS, NAMED SO THEY CANNOT BE COMPARED BY
        # ACCIDENT. `first_fills` and `complete_fills` are ASSIGNED from the
        # CURRENT window's HypotheticalBook every cycle, so a rollover resets
        # them to 0 with the fresh book. `matched_pairs` is INCREMENTED once
        # per window and never reset. Printed side by side they read as one
        # ratio, and they are not one: "first_fills 0, matched_pairs 1" means
        # "no first fill in the window running NOW; one EARLIER window ended
        # matched". These originals are preserved byte-for-byte so rows stay
        # comparable with the control service's, and unambiguous fields are
        # added beside them rather than replacing them.
        self.first_fills = 0
        self.complete_fills = 0
        self.matched_pairs = 0
        #: true cumulative totals across every window this process has run
        self.first_fills_cumulative = 0
        self.complete_fills_cumulative = 0
        #: what `matched_pairs` actually counts: WINDOWS that ended with
        #: matched inventory, not the number of pairs. A window holding three
        #: matched pairs adds ONE.
        self.windows_ending_matched = 0
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
              book_why, arms=None) -> None:
        self.current_contract = symbol
        if arms:
            self.note_arms(arms)
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

    def note_arms(self, arms) -> None:
        """Live per-arm counters, read from each arm's OWN ledger.

        The control's counters come from its live HypotheticalBook every
        cycle; the arms' must too. Reading them from the persisted row
        instead reported first_fills 0 on a cycle whose residual had
        plainly moved, because the row carries that cycle's fills and no
        running total.
        """
        for arm in arms or ():
            a = self.arms.setdefault(arm.label, {"first_fills": 0,
                                                 "COMPLETE_fills": 0,
                                                 "matched_pairs": 0,
                                                 "residual": "0",
                                                 "rungs_resting": 0,
                                                 "rungs_rested": 0,
                                                 "error": None})
            if arm.error:
                a["error"] = arm.error
                continue
            rep = arm.hyp.report()
            inv = arm.inv.report()
            a["rungs_resting"] = len(arm.hyp.live)
            a["rungs_rested"] = rep["rungs_rested"]
            a["zero_ahead_rungs"] = rep.get("zero_ahead_rungs")
            a["preserved_rungs"] = rep.get("preserved_rungs")
            a["first_fills"] = rep["first_fills"]
            a["COMPLETE_fills"] = rep["complete_fills"]
            a["matched_qty"] = str(inv.get("matched_qty"))
            a["pair_basis"] = inv.get("effective_matched_pair_cost")
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
            # the arm's counters run on the same three clocks as the control's
            # -- current-window gauges assigned per cycle, plus a per-window
            # increment -- so they get the same unambiguous companions.
            try:
                a["first_fills_cumulative"] = (
                    a.get("first_fills_cumulative", 0)
                    + int(h.get("first_fills") or 0))
                a["COMPLETE_fills_cumulative"] = (
                    a.get("COMPLETE_fills_cumulative", 0)
                    + int(h.get("complete_fills") or 0))
            except (TypeError, ValueError):
                pass
            a["rungs_rested"] = h.get("rungs_rested")
            a["band"] = rec.get("quote_between_tte_s")
            a["corrects"] = rec.get("corrects")
            a["error"] = rec.get("error") or a.get("error")
            inv = rec.get("inventory") or {}
            a["residual"] = str(inv.get("directional_residual"))
            try:
                if float(inv.get("matched_qty") or 0) > 0:
                    a["matched_pairs"] += 1
                    a["windows_ending_matched"] = (
                        a.get("windows_ending_matched", 0) + 1)
            except (TypeError, ValueError):
                pass
        self.last_rollover = {"symbol": w.get("symbol"),
                              "state": w.get("state"),
                              "finished_utc": w.get("finished_utc")}
        inv = w.get("inventory") or {}
        try:
            if float(inv.get("matched_qty") or 0) > 0:
                self.matched_pairs += 1
                self.windows_ending_matched += 1
        except (TypeError, ValueError):
            pass
        # the window's OWN totals, banked before the next window's fresh
        # HypotheticalBook resets the current-window gauges to zero
        h = w.get("hypothetical") or {}
        try:
            self.first_fills_cumulative += int(h.get("first_fills") or 0)
            self.complete_fills_cumulative += int(h.get("complete_fills") or 0)
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
            "evidence_cohort": EVIDENCE_COHORT,
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
            # ---- the same facts, named so the clock is part of the name
            "first_fills_current_window": self.first_fills,
            "COMPLETE_fills_current_window": self.complete_fills,
            "first_fills_cumulative": self.first_fills_cumulative,
            "COMPLETE_fills_cumulative": self.complete_fills_cumulative,
            "windows_ending_matched": self.windows_ending_matched,
            "counter_semantics": {
                "first_fills": ("CURRENT WINDOW ONLY. Assigned from the live "
                                "HypotheticalBook each cycle; a rollover "
                                "resets it to 0 with the fresh book."),
                "COMPLETE_fills": "CURRENT WINDOW ONLY, same as first_fills.",
                "matched_pairs": ("CUMULATIVE COUNT OF WINDOWS that ended with "
                                  "matched inventory -- NOT a number of pairs. "
                                  "A window holding three matched pairs adds "
                                  "ONE. Never reset."),
                "residual": "CURRENT WINDOW's inventory.",
                "why_this_matters": ("first_fills and matched_pairs run on "
                                     "DIFFERENT CLOCKS and are not a ratio. "
                                     "'first_fills 0, matched_pairs 1' means "
                                     "no first fill in the window running now "
                                     "and one earlier window ended matched. "
                                     "Use the _cumulative fields for any rate."),
            },
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
                _print_arms(w)
                _print_econ(w)
                _print_ladder(w)
                _print_bands(w)
                w.pop("cycles_detail", None)
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


def _print_bands(w) -> None:
    """The banded table for THIS window, emitted where it can be read.

    The evidence stream lives on the service's volume, so a report that can
    only be run against those files is a report nobody reads while the lane
    is running. Computing it per window from the window's own record puts
    >600 / 300-600 / <=300 in the log beside the window that produced it.

    Fails soft and entirely: a reporting error must never end a collector.
    """
    try:
        from src.gemini_execution import arm_bands as AB
        labels = [AB.CONTROL] + list(w.get("correction_arms") or {})
        for lbl in labels:
            b = AB.bands([w], lbl)
            if not b["windows"]:
                continue
            for name, _lo, _hi in AB.BANDS:
                r = b["bands"][name]
                if not (r["fills"] or r["readable_cycles"]):
                    continue
                print("ARM_BAND %s %s tte=%s readC=%d rested=%d first=%d "
                      "EXT=%d COMP=%d pairs=%d basis=%s fee+=%d resGen=%s "
                      "m/g=%s" % (
                          w.get("symbol"), lbl[:28], name,
                          r["readable_cycles"], r["rungs_rested"],
                          r["first_fills"], r["extend_fills"],
                          r["complete_fills"], r["matched_pairs"],
                          r["actual_pair_basis_median"],
                          r["fee_positive_pairs"], r["residual_generated"],
                          r["matched_gross"]), flush=True)
    except Exception as exc:                                     # noqa: BLE001
        print("ARM_BAND_ERROR %s: %s" % (type(exc).__name__, exc), flush=True)


def _print_ladder(w) -> None:
    """The flow-evidence ladder, compared on DEDUPLICATED observations.

    A state admitted by F0, F1 and F2 is ONE market observation seen three
    times. Counting it three times would inflate the looser rungs' samples by
    construction and make them look better simply for being looser, which is
    the opposite of what this ladder is for.
    """
    rungs = {}
    for lbl, rec in (w.get("correction_arms") or {}).items():
        e = rec.get("econ")
        if not e or not str(lbl).startswith(("PP_", "PASSIVE_PAIR")):
            continue
        fs = e.get("fill_sequence") or {}
        if fs.get("candidate_states") is None:
            continue
        rungs[lbl] = fs
    if len(rungs) < 2:
        return
    base = rungs.get("PASSIVE_PAIR_V1") or {}
    for lbl in sorted(rungs):
        fs = rungs[lbl]
        print("ECON_LADDER %s %s | uniqObs=%s cand=%s rested=%s first=%s "
              "ffRate=%s | compPrice=%s compFill=%s m2m=%s | matched=%s "
              "basis=%s net=%s | residRate=%s | mo60=%s adv60=%s | "
              "perDay first=%s complete=%s" % (
                  w.get("symbol"), lbl,
                  fs.get("unique_observations"), fs.get("candidate_states"),
                  fs.get("orders_hypothetically_rested"),
                  fs.get("first_fills"), fs.get("first_fill_rate_of_rested"),
                  fs.get("complement_price_appearances"),
                  fs.get("complement_fills"),
                  fs.get("maker_to_maker_completion_rate_of_first_fills"),
                  fs.get("matched_pairs"), fs.get("matched_basis_p50"),
                  fs.get("matched_net_total_excl_subsidy"),
                  fs.get("residual_rate_of_first_fills"),
                  fs.get("post_first_fill_markout_p50_60s"),
                  fs.get("post_first_fill_adverse_share_60s"),
                  fs.get("first_fills_per_day"),
                  fs.get("completions_per_day")), flush=True)
    # THE INCREMENTAL VALUE OF THE FILTER. If F5 does not beat the looser
    # rungs on these, >=5 prints is spending capacity and buying nothing.
    if base:
        for lbl in sorted(k for k in rungs if k != "PASSIVE_PAIR_V1"):
            fs = rungs[lbl]

            def _d(k):
                a, b = base.get(k), fs.get(k)
                return (None if (a is None or b is None) else round(a - b, 4))
            print("ECON_LADDER_DELTA %s F5_minus_%s | mo60=%s m2m=%s "
                  "ffRate=%s residRate=%s net=%s" % (
                      w.get("symbol"), lbl,
                      _d("post_first_fill_markout_p50_60s"),
                      _d("maker_to_maker_completion_rate_of_first_fills"),
                      _d("first_fill_rate_of_rested"),
                      _d("residual_rate_of_first_fills"),
                      _d("matched_net_total_excl_subsidy")), flush=True)
    # WHY BOTH LEGS MOVED. Does a wide passive book compress after we post?
    for lbl in sorted(rungs):
        cl = rungs[lbl].get("book_move_classes_by_horizon_s") or {}
        if any(cl.values()):
            print("ECON_BOOKMOVE %s %s | %s" % (
                w.get("symbol"), lbl,
                " ; ".join("%ss %s" % (h, v) for h, v in sorted(cl.items())
                           if v)), flush=True)


def _print_econ(w) -> None:
    """The economic challenger's own scoreboard, which no other arm has.

    "rested=0" on the WINDOW_ARM line is ambiguous by itself -- an arm that was
    refused by its own gates and an arm that crashed look identical. This line
    makes the refusal auditable: which gate refused, how many completes the
    risk overlay forbade, and at what basis.
    """
    for lbl, rec in (w.get("correction_arms") or {}).items():
        e = rec.get("econ")
        if not e:
            continue
        sb = e.get("scoreboard") or {}
        ov = sb.get("RISK_OVERLAY_cost") or {}
        print("ECON %s %s spec=%s | entries %s/%s refused=%s | firstFills=%s "
              "compOpp=%s acceptable=%s | veto=%s fbBasis=%s fbFeePos=%s | "
              "defect_above_ceiling=%s | primary=%s | div=%s" % (
                  w.get("symbol"), lbl[:20], e.get("spec_label"),
                  sb.get("entries_admitted"), sb.get("entries_considered"),
                  sb.get("entry_refusals_by_gate"), sb.get("first_fills"),
                  sb.get("complement_opportunities"),
                  sb.get("acceptable_complement_opportunities"),
                  ov.get("completes_forbidden"),
                  ov.get("forbidden_basis_median"),
                  ov.get("forbidden_that_would_have_been_fee_positive"),
                  sb.get("PAIRS_ABOVE_CEILING_defect_counter"),
                  sb.get("PRIMARY_net_cents_per_capital_dollar_minute"),
                  e.get("divergences")), flush=True)
        # ---- THE FILL SEQUENCE FUNNEL. Price availability is not profit:
        # every step is counted separately, and the gap between a complement
        # PRICE appearing and a complement FILLING is the thesis under test.
        fs = e.get("fill_sequence") or {}
        if fs.get("candidate_states"):
            print("ECON_FUNNEL %s %s variant=%s | cand=%s rested=%s "
                  "first=%s ffRate=%s | compPrice=%s compFill=%s "
                  "m2mRate=%s medComplete=%ss | matched=%s basis=%s "
                  "net=%s | residRate=%s residSettle=%s exitNet=%s | "
                  "perDay cand=%s first=%s complete=%s" % (
                      w.get("symbol"), lbl[:20], e.get("variant"),
                      fs.get("candidate_states"),
                      fs.get("orders_hypothetically_rested"),
                      fs.get("first_fills"),
                      fs.get("first_fill_rate_of_rested"),
                      fs.get("complement_price_appearances"),
                      fs.get("complement_fills"),
                      fs.get("maker_to_maker_completion_rate_of_first_fills"),
                      fs.get("median_completion_time_s"),
                      fs.get("matched_pairs"), fs.get("matched_basis_p50"),
                      fs.get("matched_net_total_excl_subsidy"),
                      fs.get("residual_rate_of_first_fills"),
                      fs.get("residual_settlement_p50"),
                      fs.get("residual_best_exit_net_p50"),
                      fs.get("candidates_per_day"),
                      fs.get("first_fills_per_day"),
                      fs.get("completions_per_day")), flush=True)
            print("ECON_FUNNEL_MO %s %s mo8=%s mo30=%s mo60=%s mo120=%s | "
                  "adv60=%s | byLeg=%s" % (
                      w.get("symbol"), lbl[:20],
                      fs.get("post_first_fill_markout_p50_8s"),
                      fs.get("post_first_fill_markout_p50_30s"),
                      fs.get("post_first_fill_markout_p50_60s"),
                      fs.get("post_first_fill_markout_p50_120s"),
                      fs.get("post_first_fill_adverse_share_60s"),
                      fs.get("by_first_fill_leg")), flush=True)
        # THE DENOMINATOR. A cycle the socket could not vouch for is NOT an
        # entry refusal, and an admission rate over total cycles would report a
        # coverage gap as a strategy verdict.
        cov = e.get("coverage") or {}
        if cov.get("total_cycles"):
            print("ECON_COV %s %s cohort=%s | total=%s vouched=%s "
                  "warmup_excl=%s not_quoting=%s eligible=%s | admitted=%s "
                  "refusals=%s rate_of_eligible=%s | warmup_share=%s "
                  "prewarmed=%s notPrewarmed=%s medTimeToVouched=%ss "
                  "exclusive=%s" % (
                      w.get("symbol"), lbl[:20], e.get("cohort"),
                      cov.get("total_cycles"),
                      cov.get("coverage_vouched_cycles"),
                      cov.get("warmup_excluded_cycles"),
                      cov.get("not_quoting_cycles"),
                      cov.get("strategy_eligible_cycles"),
                      cov.get("entries_admitted"),
                      cov.get("strategy_refusals"),
                      cov.get("admission_rate_of_eligible"),
                      cov.get("warmup_share_of_total"),
                      cov.get("warmup_prewarmed"),
                      cov.get("warmup_not_prewarmed"),
                      cov.get("median_time_to_vouched_s"),
                      cov.get("buckets_are_exclusive")), flush=True)
            if not cov.get("accounting_consistent", True):
                print("ECON_COV_INCONSISTENT %s %s admitted+refusals != "
                      "counted<=eligible | %s" % (
                          w.get("symbol"), lbl[:20],
                          {k: cov.get(k) for k in (
                              "entries_admitted", "strategy_refusals",
                              "strategy_eligible_cycles",
                              "decisions_refused_wrong_bucket",
                              "decisions_refused_over_count")}), flush=True)
        pw = e.get("prewarm") or {}
        if pw:
            print("ECON_PREWARM %s %s watching=%s opened=%s dropped=%s "
                  "errors=%s" % (
                      w.get("symbol"), lbl[:20], pw.get("watching"),
                      pw.get("opened_total"), pw.get("dropped_total"),
                      pw.get("errors")), flush=True)
        # ---- PROFIT SPRINT. Which gate is BINDING (sole blocker) versus a
        # PASSENGER (blocks cycles something else blocked anyway), and what the
        # ablations would have admitted. Nothing here is deployed.
        at = e.get("attribution") or {}
        if at.get("cycles"):
            pg = at.get("per_gate") or {}
            print("ECON_GATE %s %s cycles=%s admitted=%s | %s" % (
                w.get("symbol"), lbl[:20], at.get("cycles"),
                at.get("admitted_n"),
                " ; ".join("%s %s blk=%.0f%% marg=%.0f%%"
                           % (g, v["verdict"], v["blocked_pct"],
                              v["marginal_pct"])
                           for g, v in sorted(pg.items()))), flush=True)
            print("ECON_ABLATE %s %s | %s" % (
                w.get("symbol"), lbl[:20],
                " ; ".join("%s=%s(%+d)" % (k, v["admitted_n"],
                                           v["delta_vs_current"])
                           for k, v in (at.get("ablations") or {}).items())),
                  flush=True)
            combos = at.get("blocker_combinations") or []
            if combos:
                print("ECON_COMBO %s %s | %s" % (
                    w.get("symbol"), lbl[:20],
                    " ; ".join("%s n=%d (%.0f%%)"
                               % ("+".join(c["gates"]) or "NONE", c["n"],
                                  c["pct"]) for c in combos[:5])), flush=True)
        # ---- WAS BEING BLOCKED GOOD FOR US? Forward labels on candidate first
        # fills, segmented by cohort/TTE/LEG -- YES and NO never pooled.
        bl = e.get("blocked_labels") or {}
        if bl.get("labelled"):
            def _fmt(d):
                if not d or d.get("empty"):
                    return "n=0"
                return ("n=%s mo8=%s mo30=%s mo60=%s mo120=%s adv60=%s "
                        "opp=%s t2opp=%s basis=%s b<=1=%s maxAdv=%s" % (
                            d.get("n"), d.get("markout_p50_8s"),
                            d.get("markout_p50_30s"), d.get("markout_p50_60s"),
                            d.get("markout_p50_120s"),
                            d.get("adverse_share_60s"),
                            d.get("opposite_available_share"),
                            d.get("median_time_to_opposite_s"),
                            d.get("best_pair_basis_p50"),
                            d.get("best_pair_below_one_share"),
                            d.get("max_adverse_markout_p50_ticks")))
            print("ECON_LABEL %s %s labelled=%s open=%s | BLOCKED %s | "
                  "ADMITTED %s" % (
                      w.get("symbol"), lbl[:20], bl.get("labelled"),
                      bl.get("open"), _fmt(bl.get("blocked")),
                      _fmt(bl.get("admitted"))), flush=True)
            for k, v in sorted((bl.get("by_cohort_tte_leg") or {}).items()):
                if v.get("n"):
                    print("ECON_SEG %s %s %s | %s" % (
                        w.get("symbol"), lbl[:20], k, _fmt(v)), flush=True)
            for k, v in sorted((bl.get("by_marginal_blocker") or {}).items()):
                if v.get("n"):
                    print("ECON_BYBLOCK %s %s %s | %s" % (
                        w.get("symbol"), lbl[:20], k, _fmt(v)), flush=True)
        why = sb.get("entry_refusal_example_reasons") or {}
        if why:
            print("ECON_WHY %s %s %s" % (w.get("symbol"), lbl[:20], why),
                  flush=True)
        # BOTH PATHS AT EVERY VETO. allow_minus_veto > 0 means COMPLETING
        # would have produced better TOTAL INVENTORY economics than refusing,
        # i.e. the risk overlay cost us on that fork.
        cf = e.get("counterfactual") or {}
        if cf.get("forks_opened"):
            print("ECON_CF %s %s forks=%s resolved=%s | allow-veto med=%s "
                  "mean=%s | ALLOW_better=%s VETO_better=%s | settled=%s "
                  "marked=%s" % (
                      w.get("symbol"), lbl[:20], cf.get("forks_opened"),
                      cf.get("forks_resolved"),
                      cf.get("allow_minus_veto_median"),
                      cf.get("allow_minus_veto_mean"),
                      cf.get("windows_where_ALLOW_would_have_been_better"),
                      cf.get("windows_where_VETO_was_better"),
                      cf.get("settled_resolutions"),
                      cf.get("marked_resolutions")), flush=True)
        # SPREAD AS A DISTRIBUTION. No threshold is moved from this; it is
        # collected so a treatment can be preregistered against evidence.
        sp = e.get("spread_census") or {}
        if sp.get("observations"):
            top = sorted(((k, v) for k, v in (sp.get("cells") or {}).items()),
                         key=lambda kv: -kv[1]["n"])[:3]
            print("ECON_SPREAD %s %s obs=%s cellsOK=%s uniq=%s persp=%s | %s"
                  % (w.get("symbol"), lbl[:20], sp.get("observations"),
                     sp.get("cells_sufficient"), sp.get("keys_unique"),
                     sp.get("markout_perspective"),
                     " ; ".join(
                         "%s n=%d med=%.3f p90=%.3f signed=%s abs=%s adv=%s"
                         % (k, v["n"], v["median"], v["p90"],
                            v.get("signed_markout_ticks_by_horizon_s"),
                            v.get("abs_touch_move_ticks_by_horizon_s"),
                            v.get("adverse_share_by_horizon_s"))
                         for k, v in top)), flush=True)


def _print_arms(w) -> None:
    """The arm's window totals beside the control's, in one line each."""
    for lbl, rec in (w.get("correction_arms") or {}).items():
        h = rec.get("hypothetical") or {}
        iv = rec.get("inventory") or {}
        print("WINDOW_ARM %s %s band=%s | rested=%s fills=%s first=%s "
              "complete=%s | matched=%s residual=%s basis=%s | err=%s" % (
                  w.get("symbol"), lbl[:28], rec.get("quote_between_tte_s"),
                  h.get("rungs_rested"), h.get("fills"), h.get("first_fills"),
                  h.get("complete_fills"), iv.get("matched_qty"),
                  iv.get("directional_residual"),
                  iv.get("effective_matched_pair_cost"), rec.get("error")),
              flush=True)


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
