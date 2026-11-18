"""Market-progress completion policy for a stranded leg. OFF by default.

ONE DEFINITION, TWO EXECUTORS. The BTC5m controller and the sports
two-sided bot both hold lone legs and both have to decide, repeatedly,
whether to complete, reprice, reduce, hold, or (where separately
authorized) cross. They currently answer it with different code and
different constants. This module is the shared answer, and it is the
INTERFACE the two lanes agree on: pure, no transport, no venue, no
environment writes, no clock of its own beyond what the caller passes.

WHAT IT DOES AND DOES NOT DECIDE.

  It decides the CEILING a completion may pay at this point in the
  market's life, and which of the caller's options is best on expected
  net dollars. It does NOT place, cancel, size, or authorize anything. A
  returned MAKER_COMPLETE is a recommendation the caller's own gates then
  accept or refuse; every existing economic gate, exposure cap, taker
  authorization and halt remains upstream of this and unaffected by it.

THE CEILING IS A PERMISSION, NEVER A TARGET. This is the rule that makes
the schedule safe, and it is tested:

    reaching a later stage NEVER forces a completion.

A later stage only WIDENS what is permissible. The decision still
requires the chosen action to beat holding on expected value, strictly.
So at the FINAL stage, where the ceiling is above par and any matched
pair priced there is a certain small loss, the policy still holds unless
completing is worth more than carrying the leg to settlement. A schedule
that completed merely because the clock advanced would convert a
probabilistic loss into a certain one on every quiet market.

PROGRESS IS MEASURED FROM MARKET START, not from the fill. Two legs
stranded in the same market at different moments face the same remaining
risk, so they must sit at the same stage; keying off the fill would give
a leg filled late a full patient window it has not earned. Owner's
instruction, 2026-11-18, and it is also the only definition that makes
two lanes comparable.

UNCERTAIN RESOLUTION IS TREATED AS EARLY, NOT LATE. A BTC5m window ends
at a known second. A tennis match does not: it ends when it ends. Where
the end time is unknown, unreadable, or merely estimated, this module
refuses to advance the stage and holds the TIGHTEST ceiling. Guessing
late would unlock above-par completion on a market that may have hours
left, which is the expensive direction to be wrong in.

CANDIDATE NUMBERS, NOT SETTLED ONES. The stage boundaries and ceilings
below are the owner's candidates of 2026-11-18. Two of the three claims
originally offered in support of them did not survive verification:

  * "every stranded leg held to settlement (41 of 41) settled at zero"
    is WITHDRAWN. The sports bot records `held_to_close` as a COUNT with
    no settlement outcome attached, and its observed values are 3
    (legacy) + 4 (recovery) = 7, not 41. 41 is the number of
    ACTIVITY_TYPE_POSITION_RESOLUTION rows in the 23:30:23Z audit (40 at
    23:14Z, 42 at 01:30Z) -- resolution events for ALL positions,
    matched pairs included. Nothing measured says stranded legs settle
    at zero 100% of the time, so the FINAL stage's above-par ceiling
    rests on no verified evidence and is carried here only as a
    candidate, gated by the never-force rule above.
  * "cheap leg first is worth about 1.8c/pair" is SOURCED but NOT
    significant: `pmus_sports_open_policy` documents 81 episodes with
    +1.8c/pair at a bootstrap 90% CI of (-0.5c, +4.1c), P(<=0) ~ 0.10,
    and says so itself -- "suggestive, not proof, and cheap-first alone
    is break-even in this sample" (cheap arm pair cost 1.0001, margin
    -0.0c). Treat it as a reason to avoid letting the DEARER leg fill
    first, not as an edge to spend.

  The sport-level economics DID verify, and correct an earlier claim of
  mine: CFB is the worst sport, not CS2 -- book-settled -6.5582 / venue
  -7.5723 (n=33), against CS2 -5.3490 / -5.9500 (n=18) and table tennis
  (setkameua) -3.0900 / -3.4800 (n=10).

DENOMINATORS, CORRECTED 2026-11-18 -- AND I GOT THIS WRONG FIRST.

I reported that completion was "about 4x cheaper, not 2x" than holding.
That was my own denominator error, of exactly the kind the owner asked me
to check for. I compared 3.8c -- the mean overpay across ALL 34 completed
pairs, derived from their mean pair cost of 1.038 -- against a holding
cost measured on the 25 ABOVE-PAR strands only. Two different
populations.

On matched denominators, the same 25 above-par strands (125 contracts):

    completing cost          7.6c/contract   ($9.50)
    holding would have cost 12.4c/contract  ($15.50)
    completion is cheaper by 4.8c/contract  ($6.00), a ratio of 1.63x

That ties to the independently reported nets: recorded production -11.78
against the bounded -17.77 at 0.99 is a gap of $5.99, and 7.6c vs 12.4c
over 125 contracts predicts $6.00. The earlier 16c figure mixed in 3
legs outside that population.

So the direction stands and the magnitude does not: completing above par
was cheaper than holding on that night, by about 1.6x rather than 4x.

THE LARGEST SPORTS LOSS WAS RE-ENTRY, NOT COMPLETION PRICE. Of the 36
settled strands, the 17 that followed an earlier above-par completion in
the SAME market account for $8.49 of the $11.78 -- 47% of the strands and
72% of the loss. That points at the opening side, not the ceiling: the
expensive mistake is re-opening a market that has already demonstrated it
strands you. The sports lane gates this with a per-market strand-loss
budget (aaf70a7f8, default off). It is NOT ported here: the evidence is
17 markets of in-play tennis and esports, and BTC5m strands carry the
opposite sign (see below).

A LIVE OBSERVATION THAT BEARS ON STAGE 0. In the sports lane's own
measured episodes, completions land at a pair cost of 1.0001 (cheap leg
first) to 1.0177 (dearer first). Both are AT OR ABOVE par, so a 0.99
ceiling would refuse essentially every completion that lane actually
achieves, and in practice the schedule would begin at stage 1. That is
reported rather than silently corrected: it is the owner's number to
move, and `stage_would_refuse_observed_completions` exists so a caller
can see it.
"""
from __future__ import annotations

import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

ZERO, ONE = D("0"), D("1")

#: the switch. Anything but exactly "ON" is off, so a typo is off.
POLICY_ENV = "L5_COMPLETION_POLICY"

#: THE AUTHORIZED PATIENT CEILING. Owner 2026-11-18: "The $0.97 decision
#: stands. Use it as the initial patient-stage ceiling, with $0.99
#: retained as a replay comparator." 0.97 locks about 3c, which is the
#: rationale his first message gave; 0.99 locks 1c, which is what his
#: formal instruction said. He has resolved it to 0.97.
PATIENT_CEILING = D("0.97")

#: the comparator, for replay only. Callers pass it explicitly; nothing
#: defaults to it. The replay decides which produces better NET outcomes
#: after unmatched losses -- that question is open.
COMPARATOR_PATIENT_CEILING = D("0.99")

#: ABOVE-PAR COMPLETION IS NOT AUTHORIZED. Owner 2026-11-18: "There is no
#: authorization to restore the $1.03 allowance." Its only stated
#: justification was the 41-of-41 claim, which did not survive
#: verification, so FINAL is par like the two stages before it and
#: nothing in this module can pay above 1.00.
#:
#: READ CONSERVATIVELY AND ON PURPOSE. That sentence could have meant
#: only "not for uncertain sports markets", which was already true via
#: UNCERTAIN_MAX_STAGE. Taking the narrower reading would have left a
#: certain-clock market able to pay 1.03 on no authorization at all, so
#: the wider reading is the one implemented. Restoring it is one explicit
#: argument -- `final_ceiling=` -- and it needs the owner's word.
ABOVE_PAR_FINAL_CEILING = D("1.03")
FINAL_CEILING = D("1.00")


def stages(patient_ceiling: Optional[D] = None,
           final_ceiling: Optional[D] = None
           ) -> Tuple[Tuple[D, D, str], ...]:
    """(progress_upper_bound, pair_ceiling, name). First match wins.

    Both overrides exist for REPLAY and for an explicitly authorized
    change; the defaults are the authorized numbers.
    """
    pc = PATIENT_CEILING if patient_ceiling is None else patient_ceiling
    fc = FINAL_CEILING if final_ceiling is None else final_ceiling
    return ((D("0.30"), pc,         "PATIENT"),
            (D("0.60"), D("1.00"),  "STEP"),
            (D("0.85"), D("1.00"),  "BREAKEVEN"),
            (ONE,       fc,         "FINAL"))


#: the authorized schedule, for callers that want it without a call.
STAGES: Tuple[Tuple[D, D, str], ...] = stages()

#: candidate cap on concurrent stranded legs. A caller's own stricter
#: limit always wins; this never raises anyone's allowance.
MAX_STRANDED_LEGS = 3

#: observed completion pair costs in the sports lane, for the advisory
#: in `stage_would_refuse_observed_completions`. Provenance is in the
#: module docstring.
OBSERVED_COMPLETION_PAIR_COST = (D("1.0001"), D("1.0177"))

ACTIONS = ("HOLD", "MAKER_COMPLETE", "REPRICE", "REDUCE", "TAKER_COMPLETE")


def enabled() -> bool:
    """True only when the switch is exactly "ON"."""
    return os.environ.get(POLICY_ENV) == "ON"


def _d(v: Any) -> Optional[D]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


# ---------------------------------------------------------------- progress
def progress(*, now: Any, start: Any, end: Any,
             resolution_certain: bool = True) -> Dict[str, Any]:
    """Fraction of the market's life elapsed, FROM START, or unknown.

    Returns `{"progress": D|None, "certain": bool, "why": str|None}`.
    `progress` is clamped to [0, 1]. Anything unreadable, inverted, or
    merely estimated comes back with `certain` False, which
    `stage_for` turns into the tightest ceiling rather than a guess.
    """
    n, s, e = _d(now), _d(start), _d(end)
    if n is None or s is None:
        return {"progress": None, "certain": False, "why": "TIME_UNREADABLE"}
    if e is None:
        return {"progress": None, "certain": False,
                "why": "RESOLUTION_TIME_UNKNOWN"}
    if e <= s:
        return {"progress": None, "certain": False,
                "why": "RESOLUTION_NOT_AFTER_START"}
    p = (n - s) / (e - s)
    if p < ZERO:
        p = ZERO
    if p > ONE:
        p = ONE
    return {"progress": p, "certain": bool(resolution_certain),
            "why": None if resolution_certain else "RESOLUTION_ESTIMATED"}


#: the furthest an UNCERTAIN market may advance, even on a strong
#: event-state signal. BREAKEVEN, never FINAL: the above-par rung's only
#: stated justification was the 41-of-41 claim, which did not survive
#: verification, so nothing may reach it without a certain clock.
UNCERTAIN_MAX_STAGE = 2


def stage_for(prog: Mapping[str, Any], *,
              event_late: bool = False,
              patient_ceiling: Optional[Any] = None,
              final_ceiling: Optional[Any] = None) -> Dict[str, Any]:
    """The stage and its ceiling. Unknown or estimated -> stage 0.

    THE CONSERVATIVE DIRECTION IS EARLY. An unknown end time could mean
    the market closes in a minute or in three hours; only one of those
    mistakes unlocks paying above par on a market with hours to run.

    `event_late` IS AN OBSERVED FACT, NOT A CLOCK ESTIMATE, and that is
    why it is allowed to move the stage at all. A sports lane has a venue
    `gameStartTime` but no certain end, so under the rule above every
    live match would sit at 0.99 for ever and FINAL would be unreachable
    -- which makes the policy a no-op there, and worse than the lane's
    status quo. A caller that can observe the match STATE (final period,
    late set, final frame) is supplying evidence of lateness rather than
    a guess about it, so it may advance -- but only as far as
    UNCERTAIN_MAX_STAGE. An uncertain market can therefore reach
    breakeven and never above par.

    Passing `event_late` on a CERTAIN market changes nothing: a real
    clock is better evidence than a flag, and is used instead.
    """
    sched = stages(_d(patient_ceiling), _d(final_ceiling))
    p = prog.get("progress")
    certain = prog.get("certain") is True
    if p is None or not certain:
        if event_late:
            idx = UNCERTAIN_MAX_STAGE
            _u, ceil, name = sched[idx]
            return {"stage": idx, "stage_name": name, "pair_ceiling": ceil,
                    "progress": p, "event_late": True,
                    "capped_at_uncertain_max": True,
                    "why": ("advanced on an OBSERVED late-event signal and "
                            "capped at %s; an uncertain market never "
                            "reaches the above-par stage" % name)}
        idx, ceil, name = 0, sched[0][1], sched[0][2]
        return {"stage": idx, "stage_name": name, "pair_ceiling": ceil,
                "progress": p, "event_late": False,
                "why": ("held at the tightest stage: %s"
                        % (prog.get("why") or "RESOLUTION_UNCERTAIN"))}
    for idx, (upper, ceil, name) in enumerate(sched):
        if p <= upper:
            return {"stage": idx, "stage_name": name, "pair_ceiling": ceil,
                    "progress": p, "why": None}
    upper, ceil, name = sched[-1]
    return {"stage": len(sched) - 1, "stage_name": name,
            "pair_ceiling": ceil, "progress": p, "why": None}


def stage_would_refuse_observed_completions(ceiling: Any) -> Dict[str, Any]:
    """Advisory: does this ceiling sit below where we actually complete?

    Reporting only. It changes no decision; it exists so that a 0.99
    stage-0 ceiling being unreachable in practice is visible rather than
    discovered after a campaign.
    """
    c = _d(ceiling)
    if c is None:
        return {"refuses_all_observed": None, "why": "CEILING_UNREADABLE"}
    worse = [str(x) for x in OBSERVED_COMPLETION_PAIR_COST if x > c]
    return {"refuses_all_observed":
            len(worse) == len(OBSERVED_COMPLETION_PAIR_COST),
            "observed_above_ceiling": worse,
            "observed": [str(x) for x in OBSERVED_COMPLETION_PAIR_COST]}


# -------------------------------------------------------------- stranding
def stranded_room(*, stranded_now: Any, caller_max: Any = None
                  ) -> Dict[str, Any]:
    """May another leg be stranded? The STRICTER of the two limits wins.

    This can only ever narrow a caller's allowance: `caller_max` lower
    than MAX_STRANDED_LEGS is honoured, higher is ignored.
    """
    n = _d(stranded_now)
    if n is None or n < ZERO:
        return {"room": False, "why": "STRANDED_COUNT_UNREADABLE"}
    cm = _d(caller_max)
    limit = D(MAX_STRANDED_LEGS)
    if cm is not None and cm >= ZERO and cm < limit:
        limit = cm
    return {"room": n < limit, "stranded_now": int(n), "limit": int(limit),
            "why": None if n < limit else "MAX_STRANDED_LEGS"}


def opening_room(*, market_strand_loss_usd: Any,
                 budget_usd: Any = None) -> Dict[str, Any]:
    """May this market take a NEW opening leg? Per-market strand budget.

    THE LARGEST MEASURED LOSS IS HERE, NOT IN THE COMPLETION PRICE. Of
    the 36 settled sports strands of 2026-11-17, the 17 that followed an
    earlier above-par completion in the SAME market carry $8.49 of the
    $11.78 -- 47% of the strands and 72% of the loss. Every pair-cost
    ceiling tried on that night made the result WORSE (recorded -11.78,
    bounded -17.77 at 0.99, -20.17 at 0.97), because the ceiling
    tightens the completion while the loss is being made by RE-OPENING a
    market that has already demonstrated it strands you. This gates the
    valve the evidence actually points at.

    WHAT IT GATES, AND WHAT IT MUST NOT. Opening legs only. Completions,
    repricing, reduction and every risk-reducing action continue
    untouched -- a market that has hurt us still needs its existing
    inventory worked, and a guard that froze that would strand the
    inventory it was meant to protect.

    `market_strand_loss_usd` is the market's own realized cost from its
    above-par completions, i.e. the running sum of qty * (pair_cost - 1).
    It is the CALLER'S ledger: this module keeps no state and remembers
    nothing between calls.

    OFF UNLESS BUDGETED, and unreadable FAILS CLOSED. No budget means no
    gating, so adopting this module cannot silently start refusing
    openings. But a budget that is configured and a ledger that cannot
    be read is the one combination that must refuse: the whole point is
    that the market has a history, and "I cannot see the history" is not
    "there is no history".

    NO THRESHOLD IS BAKED IN. The sports figures above are evidence for
    the MECHANISM, not a default for anyone. Each lane supplies its own
    budget, because the two lanes' strands behave oppositely: BTC5m's
    1,170 resolvable strands won 67.9% at a 0.5987 basis, so a budget
    calibrated on in-play tennis would be meaningless there.
    """
    b = _d(budget_usd)
    if b is None or b <= ZERO:
        return {"room": True, "why": "NO_BUDGET_CONFIGURED",
                "budget_usd": None}
    loss = _d(market_strand_loss_usd)
    if loss is None or loss < ZERO:
        return {"room": False, "why": "STRAND_LEDGER_UNREADABLE",
                "budget_usd": str(b),
                "labels": ("a configured budget with an unreadable ledger "
                           "refuses: unseen history is not absent history")}
    return {"room": loss < b, "market_strand_loss_usd": str(loss),
            "budget_usd": str(b),
            "why": None if loss < b else "MARKET_STRAND_LOSS_BUDGET"}


# --------------------------------------------------------------- decision
def _net_if_completed(*, held_qty: D, held_avg: D, complete_px: D,
                      qty: D, fee_per_share: D) -> D:
    """Net dollars of completing `qty` of the pair at `complete_px`.

    A matched pair pays exactly 1.0 at settlement whichever side wins,
    so its net is qty * (1 - pair_cost), with the pair cost taken on the
    HELD side's average cost plus the completion price plus fees.
    """
    pair_cost = held_avg + complete_px + fee_per_share
    return qty * (ONE - pair_cost)


def _net_if_partial(*, held_qty: D, held_avg: D, complete_px: D,
                    qty: D, fee_per_share: D, p_win: D) -> D:
    """Net of completing only `qty` of `held_qty`, carrying the rest.

    THE PAIR DOES NOT HAVE TO BE ALL OR NOTHING. Depth is finite, and a
    completion sized to the depth that actually exists resolves part of
    the strand at a known cost instead of leaving all of it exposed. The
    remainder keeps its lone-leg expectation, so a partial is scored
    against HOLD on exactly the same basis as a full completion.
    """
    pair_cost = held_avg + complete_px + fee_per_share
    matched = qty * (ONE - pair_cost)
    lone = (held_qty - qty) * (p_win - held_avg)
    return matched + lone


def _net_if_held(*, held_qty: D, held_avg: D, p_win: D) -> D:
    """Expected dollars of carrying the lone leg to settlement.

    `p_win` is the probability the held side settles at 1. It is the
    caller's, and it should come from an executable price rather than a
    midpoint; this module does not invent it.
    """
    return held_qty * (p_win - held_avg)


def decide(*, held_side: str, held_qty: Any, held_avg_cost: Any,
           complete_ask: Any = None, complete_bid: Any = None,
           reduce_bid: Any = None, p_win: Any = None,
           fee_per_share: Any = "0", taker_authorized: bool = False,
           prog: Optional[Mapping[str, Any]] = None,
           stranded_now: Any = None, caller_max_stranded: Any = None,
           event_late: bool = False, p_win_source: Optional[str] = None,
           patient_ceiling: Optional[Any] = None,
           final_ceiling: Optional[Any] = None,
           complete_depth: Any = None,
           ) -> Dict[str, Any]:
    """Rank the options on expected net dollars. HOLD unless beaten.

    Prices are EXECUTABLE, and the caller must supply them that way:
    `complete_ask` is what a taker would pay now for the complement,
    `complete_bid` is where a maker completion would rest, `reduce_bid`
    is where the held side could be sold. A midpoint is not a price and
    this module cannot tell the difference, so the caller owns that.

    Returns the chosen action, every option's net, the stage, and a
    refusal reason for each option that was not permissible. HOLD is
    always permissible and is the default, so an unreadable input can
    only ever produce inaction.
    """
    if not enabled():
        return {"action": "HOLD", "why": "POLICY_OFF", "stage": None,
                "options": {}}

    q, avg = _d(held_qty), _d(held_avg_cost)
    fee = _d(fee_per_share) or ZERO
    if q is None or avg is None or q <= ZERO or avg < ZERO:
        return {"action": "HOLD", "why": "INVENTORY_UNREADABLE",
                "stage": None, "options": {}}
    if held_side not in ("UP", "DOWN", "YES", "NO"):
        return {"action": "HOLD", "why": "SIDE_UNREADABLE", "stage": None,
                "options": {}}

    st = stage_for(prog or {"progress": None, "certain": False,
                            "why": "NO_PROGRESS_SUPPLIED"},
                   event_late=event_late, patient_ceiling=patient_ceiling,
                   final_ceiling=final_ceiling)
    ceiling = st["pair_ceiling"]
    room = stranded_room(stranded_now=stranded_now if stranded_now is not None
                         else 0, caller_max=caller_max_stranded)

    pw = _d(p_win)
    if pw is None or not (ZERO <= pw <= ONE):
        #: WITHOUT A PROBABILITY THERE IS NO BASELINE. Holding cannot be
        #: valued, so nothing can be shown to beat it, so we hold. This
        #: is the fail-closed direction: it never completes on a guess.
        return {"action": "HOLD", "why": "P_WIN_UNREADABLE",
                "stage": st, "options": {}, "stranded": room}

    base = _net_if_held(held_qty=q, held_avg=avg, p_win=pw)
    opts: Dict[str, Any] = {
        "HOLD": {"net": str(base), "permissible": True, "why": None}}

    def consider(name: str, px: Optional[D], *, taker: bool,
                 qty: Optional[D] = None) -> None:
        if px is None:
            opts[name] = {"net": None, "permissible": False,
                          "why": "NO_PRICE"}
            return
        if taker and not taker_authorized:
            opts[name] = {"net": None, "permissible": False,
                          "why": "TAKER_NOT_AUTHORIZED"}
            return
        n_qty = q if qty is None else qty
        if n_qty <= ZERO:
            opts[name] = {"net": None, "permissible": False,
                          "why": "NO_DEPTH"}
            return
        pair_cost = avg + px + fee
        if n_qty >= q:
            net = _net_if_completed(held_qty=q, held_avg=avg,
                                    complete_px=px, qty=q,
                                    fee_per_share=fee)
        else:
            net = _net_if_partial(held_qty=q, held_avg=avg, complete_px=px,
                                  qty=n_qty, fee_per_share=fee, p_win=pw)
        if pair_cost > ceiling:
            #: REFUSED, BUT STILL SCORED. The old code returned here with
            #: net None, so a completion the schedule forbade carried no
            #: economics at all -- and the number needed to judge whether
            #: the ceiling was worth enforcing was destroyed by the
            #: refusal itself. On the sports night of 2026-11-17 that
            #: refusal is what separated a recorded -11.78 from a bounded
            #: -17.77 at 0.99 and -20.17 at 0.97. The option stays
            #: REFUSED and cannot be chosen; `net_if_permitted` records
            #: what it would have been worth, so the cost of the ceiling
            #: is measurable instead of invisible.
            opts[name] = {"net": None, "permissible": False,
                          "why": "PAIR_ABOVE_STAGE_CEILING",
                          "pair_cost": str(pair_cost),
                          "ceiling": str(ceiling),
                          "qty": str(n_qty),
                          "net_if_permitted": str(net)}
            return
        opts[name] = {"net": str(net), "permissible": True, "why": None,
                      "pair_cost": str(pair_cost), "price": str(px),
                      "qty": str(n_qty)}

    consider("MAKER_COMPLETE", _d(complete_bid), taker=False)
    consider("REPRICE", _d(complete_bid), taker=False)
    consider("TAKER_COMPLETE", _d(complete_ask), taker=True)

    #: PARTIAL, SIZED TO THE DEPTH THAT ACTUALLY EXISTS. Only offered when
    #: depth is readable AND short of the held quantity; with depth at or
    #: above the strand a full completion is the same thing and is already
    #: scored. Depth unreadable is NOT depth zero and is not assumed
    #: either way: no partial is offered and nothing changes.
    dep = _d(complete_depth)
    if dep is not None and ZERO < dep < q:
        consider("PARTIAL_COMPLETE", _d(complete_bid), taker=False, qty=dep)
    else:
        opts["PARTIAL_COMPLETE"] = {
            "net": None, "permissible": False,
            "why": ("DEPTH_UNREADABLE" if dep is None
                    else "DEPTH_COVERS_FULL_STRAND" if dep >= q
                    else "NO_DEPTH")}

    rb = _d(reduce_bid)
    if rb is None:
        opts["REDUCE"] = {"net": None, "permissible": False,
                          "why": "NO_PRICE"}
    else:
        #: selling the held side realises (bid - avg) per share and ends
        #: the strand. No pair ceiling applies: nothing is being paired.
        opts["REDUCE"] = {"net": str(q * (rb - avg - fee)),
                          "permissible": True, "why": None,
                          "price": str(rb)}

    #: THE NEVER-FORCE RULE. Only a STRICT improvement over holding can
    #: displace it, so arriving at a later stage cannot by itself cause a
    #: completion. REPRICE is excluded from winning outright because it
    #: is the same price as MAKER_COMPLETE; it exists so a caller can see
    #: that moving an existing resting order is permissible.
    best, best_net = "HOLD", base
    for name in ("MAKER_COMPLETE", "PARTIAL_COMPLETE", "REDUCE",
                 "TAKER_COMPLETE"):
        o = opts.get(name) or {}
        if o.get("permissible") is not True:
            continue
        n = _d(o.get("net"))
        if n is None:
            continue
        if n > best_net:
            best, best_net = name, n

    #: WHAT THE CEILING COST, THIS DECISION. For every option the stage
    #: ceiling refused, how much better it would have been than the action
    #: actually chosen. This is the artefact the owner asked for: a
    #: relaxation that is "separately identified, quantified, and
    #: submitted for approval" rather than argued for in prose. It
    #: ENABLES NOTHING -- the refused option is still refused and still
    #: cannot be chosen -- and a caller that ignores this field behaves
    #: exactly as before.
    relax = None
    best_refused, best_refused_net, best_refused_cost = None, None, None
    for name, o in opts.items():
        if o.get("permissible") is True:
            continue
        nip = _d(o.get("net_if_permitted"))
        if nip is None:
            continue
        gain = nip - best_net
        if gain > ZERO and (best_refused_net is None or nip > best_refused_net):
            best_refused, best_refused_net, best_refused_cost = \
                name, nip, gain
    if best_refused is not None:
        relax = {
            "refused_action": best_refused,
            "pair_cost": opts[best_refused].get("pair_cost"),
            "ceiling": str(ceiling),
            "stage": st.get("stage_name"),
            "net_if_permitted": str(best_refused_net),
            "chosen_net": str(best_net),
            "ceiling_opportunity_cost_usd": str(best_refused_cost),
            "status": "NOT_ENABLED_REQUIRES_OWNER_APPROVAL",
            "labels": ("MODELLED opportunity cost of enforcing the stage "
                       "ceiling on this decision. It is not a realized "
                       "loss, not a fill, and not an authorization; the "
                       "refused action remains refused.")}

    return {"action": best,
            "why": None if best != "HOLD" else "HOLD_NOT_BEATEN",
            "ceiling_relaxation_would_gain": relax,
            "stage": st, "options": opts, "stranded": room,
            #: WHERE p_win CAME FROM, recorded rather than assumed. A
            #: caller with no model may pass an executable mid, but the
            #: decision record then says so, because a probability and a
            #: price are not the same object and the difference must not
            #: vanish into a number.
            "p_win": str(pw), "p_win_source": p_win_source or "UNDECLARED",
            "expected_net_usd": str(best_net),
            "hold_net_usd": str(base),
            #: THE CEILING IS NOT AN ECONOMIC GATE. Owner 2026-11-18:
            #: "$1.00 is only breakeven before fees and other execution
            #: costs. The actual economic gate must remain
            #: authoritative." `fee_per_share` is charged into every pair
            #: cost here, but this module cannot see the caller's other
            #: execution costs, rebates, slippage or capital charges. A
            #: permissible completion is one the SCHEDULE allows, never
            #: one the economics have cleared; the caller's own gate
            #: decides that and this never substitutes for it.
            "ceiling_is_not_an_economic_gate": True,
            "labels": ("expected values are MODELLED from the caller's "
                       "executable prices and p_win; they are not realized "
                       "P&L and a completion here is a recommendation, not "
                       "an authorization. Every upstream gate still applies.")}
