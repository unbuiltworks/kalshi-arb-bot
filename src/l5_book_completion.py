"""BOOK-DRIVEN COMPLETION for inventory we already own.

THE DEFECT THIS CLOSES. A maker completion is priced by
`TS.complement_order`, called from `l5_live_execution.handle(row, ...)`,
whose only hot-path caller is the ingest loop at
`l5_pmus_live_runner.py:2450` -- one call per admitted L5 action. So a
completion refused at the instant an action arrives is not reconsidered
until the next action, a measured ~34 s later and longer when L5 is quiet.
Neither timer path substitutes: `maybe_ts_completion` decides whether to
CROSS and is a logged proposal under MAKER_ONLY, and
`maybe_completion_requote` can only reprice a completion that already
rests. Window 1945z is the recorded cost: twelve COMPLEMENT_PAIR_ABOVE_CAP
refusals, nothing ever posted, an exit worth -$0.0144 at 204 s taken at
600 s for -$0.5348.

WHAT THIS MODULE IS, AND IS NOT. It is a DECISION FUNCTION and nothing
else: pure, no I/O, no clock of its own, no order. It holds no limit and no
latch -- every number it reasons about is passed in from the object that
already enforces it, because a module that keeps its own copy of a rule is
a module that can disagree with the one that actually refuses.

It cannot submit. A decision reaches the executor only as a
`CONTROLLER_GENERATED` intent through `l5_intent_gate`, and that boundary
requires a `ControllerLiveAuthorization` -- a capability object, not an
environment variable. `Runner` passes none, so in production this path is
not disabled, it is ABSENT. That is the feature gate, and it is stronger
than a flag because no deploy template or copied variable can flip it.

IT NEVER CROSSES. Every quote it produces is post-only maker, and
`_would_cross` refuses any price that would take the touch. Reducing
exposure by crossing is deliberately NOT in this module: that is
`l5_defensive_completion`'s decision, on its own flag, and duplicating it
here would be a second author of the same risk.

PRICE SEMANTICS. The wire is YES/UP. `own(UP) = wire`, `own(DOWN) =
1 - wire`. A matched UP/DOWN pair redeems exactly $1.00, so a pair whose
own costs sum below $1.00 is locked economics and one above $1.00 is a
locked loss.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_two_sided_probe as TS

#: EVALUATION flag. ON makes this module run and RECORD; it does not make
#: anything submittable -- that needs the capability object above. OFF is
#: the default and leaves the engine byte-identical.
ENABLED_ENV = "L5_BOOK_COMPLETION"
_TRUE = ("1", "yes", "true", "on")

#: how often this may re-decide. The book feed updates far faster, but a
#: decision per tick would mean a requote per tick, and every requote
#: resets queue priority -- which the dwell evidence already says buys
#: nothing (settled break-even completion pair cost 0.9532 against an
#: achieved 0.9530). 5 s matches the residual sampler that measured the
#: blind interval, so the cadence and the evidence for it agree.
DECIDE_EVERY_S = 5.0

#: the settled break-even completion pair cost: the pair at which a
#: completion stops being PROFITABLE -- measured, not chosen: 0.9532 against
#: an achieved quantity-weighted 0.9530. It is a PREFERRED TARGET and never
#: a ceiling. A pair between this and $1.00 is still a non-negative matched
#: outcome and is still worth taking over a stranded leg, which is what
#: 1445z cost us when this number was used as a gate (see step 7). It is a
#: DEFAULT, overridable by the caller, never a second copy of a gate.
#: ONE SOURCE, in `l5_two_sided_probe`, so the two completion routes
#: cannot drift apart. See the note there for why it is a TARGET and
#: not a ceiling, and for the BTC15m cohort behind it.
BREAKEVEN_PAIR = TS.BREAKEVEN_PAIR

#: A MATCHED PAIR REDEEMS EXACTLY $1.00 A SHARE. This is arithmetic, not
#: policy, which is why it is the one hard line this module draws: above it
#: the matched book is a certain loss that no residual argument can justify.
#: It is deliberately the SAME number the submission boundary enforces in
#: `l5_live_execution.completion_pair_rescue` (`eff > D(1)` refuses), so this
#: path cannot approve a pair that the gate downstream would reject.
REDEMPTION_PAIR = D("1.00")

#: HOW FAR BEHIND THE TOUCH A COMPLETION RESTS, which is NOT the opening
#: probe's depth. An opening quote rests behind the touch to earn the spread;
#: a completion is removing risk from inventory already held, and queue
#: position is the whole of its fill probability. 1445z posted its complement
#: at own 0.6000 into a 0.6200 bid -- two ticks behind the entire visible
#: queue, unfillable by construction -- because the opening depth
#: (`L5_TS_DEPTH_TICKS`, default 2) was reused here. Default 0 rests AT the
#: touch, which `_would_cross` still proves is post-only. Resting at the bid
#: is a FILLABLE price and nothing more: it is not an assumption of a fill,
#: and nothing in this module treats a resting completion as a hedge.
COMPLETION_DEPTH_TICKS_ENV = "L5_COMPLETION_DEPTH_TICKS"
COMPLETION_DEPTH_TICKS = 0


#: how far outward the adaptive search may walk, in ticks. A bound, not a
#: target: the walk stops at the FIRST admissible depth.
ADAPTIVE_MAX_DEPTH_TICKS = 5
ADAPTIVE_ENV = "L5_COMPLETION_ADAPTIVE_DEPTH"
_TRUE_ADAPT = ("1", "yes", "true", "on")
#: the ONLY spellings that turn the adaptive walk off, now that it is the
#: default. Anything else -- including a typo -- keeps it on.
_FALSE_ADAPT = ("0", "no", "false", "off")


def adaptive_depth_enabled() -> bool:
    """DEFAULT ON since the 2026-11-09 15:30Z live window. Set it to a
    false value to pin the completion depth again.

    THE DEFAULT FLIPPED BECAUSE THE PREDICTION CAME TRUE WITH MONEY ON IT.
    Everything below was measured in replay and said a completion fixed at
    the touch can never complete. The owner-authorized live window
    `cpc-btc-updown-15m-2026-11-09-1530z` then ran with this OFF and
    produced exactly the predicted shape: ONE filled first leg (0.7 DOWN,
    venue-reconciled `ours 0.7 / venue 0.7000`), ZERO completions, and
    -$0.3080 settled. The arithmetic of that window, from its own decision
    lines: DOWN own 0.44, so a complement must rest at own <= 0.52 to keep
    the pair inside the 0.96 cap, and the bid never fell below 0.53 after
    the fill. At depth 0 the complement could only rest at own 0.53, a pair
    of 0.97 the cap refuses. ONE tick behind -- own 0.52, pair exactly 0.96
    -- already clears it. The miss was a single tick wide.

    "DEFAULT OFF because it changes a money-path decision while #206's
    deployment freeze stands" was right when written. That freeze has since
    been lifted for an executed live window, and leaving the validated
    shape behind an unset variable is how the window was spent proving a
    configuration nobody intended to trade.

    IT STILL WIDENS NOTHING. The fee-safe ceiling, the entry band, the
    exposure and loss limits, the maker-only restriction and every other
    gate are untouched. This chooses only WHERE to rest a complement whose
    economics those gates have already permitted, and the walk stops at the
    FIRST admissible depth rather than seeking the deepest.

    WHY IT EXISTS, measured. Replaying 13 out-of-sample windows, a
    completion fixed AT THE TOUCH completes ZERO pairs in 652 entries:
    all 311 filled first legs refuse with the pair above the cap, because
    a touch pair is `1 - spread` -- 0.99 on a one-tick book -- and the
    fee-safe ceiling is 0.96. The depth is not a preference there; at 0 it
    makes completion arithmetically impossible.

    Walking OUTWARD to the shallowest depth whose pair clears the cap
    converted 12 of 66 stranded legs into pairs on the queue-independent
    PROVEN arm -- an 18% reduction in strands, +0.0012 net per entry --
    and a bounded taker completion recovered NONE of the remaining 54,
    every one of which fails the cap again after fees.

    """
    raw = (os.getenv(ADAPTIVE_ENV) or "").strip().lower()
    if not raw:
        return True
    # AN UNREADABLE VALUE IS NOT AN OFF SWITCH. Only the explicit false
    # spellings disable it; a typo leaves the validated shape in force
    # rather than silently restoring the one that completes nothing.
    return raw not in _FALSE_ADAPT


def adaptive_depth_candidates() -> List[int]:
    """The depths to try, shallowest first. Just the fixed depth when off."""
    base = completion_depth_ticks()
    if not adaptive_depth_enabled():
        return [base]
    out = [base]
    for d in range(0, ADAPTIVE_MAX_DEPTH_TICKS + 1):
        if d not in out:
            out.append(d)
    return out


def completion_depth_ticks() -> int:
    """The completion depth, from its OWN env and never the opening probe's.

    Unreadable or negative falls back to the default rather than to the
    opening depth, because inheriting the opening depth is the defect.
    """
    raw = (os.getenv(COMPLETION_DEPTH_TICKS_ENV) or "").strip()
    if not raw:
        return COMPLETION_DEPTH_TICKS
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return COMPLETION_DEPTH_TICKS
    return v if v >= 0 else COMPLETION_DEPTH_TICKS


ACTIONS = ("COMPLETE", "REPRICE", "HOLD")


def enabled() -> bool:
    return (os.getenv(ENABLED_ENV) or "").strip().lower() in _TRUE


def _d(v: Any) -> Optional[D]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return D(str(v))
    except BaseException:                                      # noqa: BLE001
        return None


def _no(reason: str, **kw: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"action": "HOLD", "ok": False, "reason": reason}
    out.update(kw)
    return out


def _would_cross(side: str, own_px: D, bid: D, ask: D) -> bool:
    """True when this maker quote would take the touch instead of resting.

    Buying UP rests a YES bid, so it crosses at or above the ask. Buying
    DOWN rests a YES offer at `1 - own`, so it crosses at or below the bid.
    """
    if str(side).upper() == "UP":
        return own_px >= ask
    return (D(1) - own_px) <= bid


def cooldown_observation(*, held_side: str, held_qty: Any,
                         held_cost_usd: Any, deficient_qty: Any,
                         book: Mapping[str, Any], tick: Any,
                         depth_ticks: int, now_t: float,
                         last_decide_t: float) -> Dict[str, Any]:
    """What the book looked like when the cadence declined to re-decide.

    WHY THIS EXISTS. `decide` returns DECIDE_COOLDOWN at step 0, BEFORE the
    book is read, so every cooldown record carried `pair: None` and
    `executable_evidence: false`. On the 2000z window of
    camp1.5b2fa83e916c04d7e2871bca that was 98 of 201 refusal records, and
    it made the one question worth asking -- did the cadence decline a
    completion that was actually available -- unanswerable from the logs in
    either direction. The 101 OPENING refusals in the same window carried
    the band, the pair and the evidence basis; the DECISION refusals that
    covered real held inventory carried none of it.

    REPORTING ONLY, AND STRUCTURALLY INCAPABLE OF BEING ANYTHING ELSE. It
    takes no decision, prices no order, consults no gate and returns a dict
    the caller merges into a refusal that has already been chosen. The
    cadence condition above it is untouched, so the 5-second interval, the
    order behaviour, every risk limit and the HALT state are exactly what
    they were. Any failure inside here returns {} rather than propagating:
    a diagnostic that can change a refusal is not a diagnostic.

    A PRICE IS NOT A FILL. `at_touch`, `behind_touch` and `marketable` are
    the prices at which a complement COULD be quoted or crossed against the
    displayed book. The venue publishes no queue position, so none of them
    is a promise of execution, and `pair_*` is what the pair WOULD have cost
    had the corresponding leg filled in full. Nothing here may be read as a
    completion that was available, only as one that was priceable.
    """
    try:
        out: Dict[str, Any] = {}
        out["cooldown_remaining_s"] = round(
            float(DECIDE_EVERY_S) - (float(now_t) - float(last_decide_t)), 3)
        out["decide_every_s"] = float(DECIDE_EVERY_S)
        want = "DOWN" if str(held_side).upper() == "UP" else "UP"
        out["complement_side"] = want

        bid, ask = _d(book.get("bid")), _d(book.get("ask"))
        age = _d(book.get("stream_age_s"))
        gap = bool(book.get("gap_suspected"))
        usable = bool(bid is not None and ask is not None
                      and bid > 0 and ask > bid)
        # reporting only, but a future-dated age must not be REPORTED
        # fresh either: the telemetry is what the next audit reads.
        fresh = bool(age is not None and age >= 0
                     and age <= D(str(TS.MAX_BOOK_AGE_S)) and not gap)
        out["book"] = {
            "bid": None if bid is None else str(bid),
            "ask": None if ask is None else str(ask),
            "bid_shares": book.get("bid_shares"),
            "ask_shares": book.get("ask_shares"),
            "stream_age_s": None if age is None else str(age),
            # THE SOURCE, because the consumer grades evidence on it. Without
            # it `l5_missed_pair_blocker.evidence_basis` falls to
            # EVIDENCE_BASIS_UNKNOWN with "book source not stated" -- which
            # is what every DECISION-stage record said while this block was
            # pricing a perfectly good authenticated book.
            "source": book.get("source"),
            "gap_suspected": gap, "usable": usable}
        # the SAME freshness test step 3 applies, reported rather than enforced
        out["evidence"] = {
            "executable_evidence": bool(usable and fresh),
            "basis": ("AUTHENTICATED_WS" if (usable and fresh)
                      else "NOT_ESTABLISHED"),
            "why": ("book usable and within MAX_BOOK_AGE_S"
                    if (usable and fresh)
                    else "book unusable, stale or gapped at this observation")}
        if not usable:
            return out

        tk = _d(tick) or D("0.01")
        d = D(int(depth_ticks)) * tk
        # own-price convention: the wire is YES/UP, so a DOWN leg's own cost
        # at wire p is 1 - p. Joining means resting on the side that leaves
        # us long `want`; crossing means taking the other one.
        if want == "UP":
            at_touch, behind, marketable = bid, bid - d, ask
        else:
            at_touch = D(1) - ask
            behind = D(1) - (ask + d)
            marketable = D(1) - bid
        out["complement_own"] = {
            "at_touch": str(at_touch), "behind_touch": str(behind),
            "marketable": str(marketable), "depth_ticks": int(depth_ticks),
            "labels": ("prices, not fills. The venue publishes no queue "
                       "position, so a touch at one of these is not an "
                       "execution and must never be counted as one")}

        hq, dqv = _d(held_qty), (_d(deficient_qty) or D(0))
        hc = _d(held_cost_usd)
        # AN UNATTESTED BASIS IS NOT A FREE POSITION. Cost zero against
        # positive quantity means the basis was never attested, which is the
        # same reading `l5_continuous_inventory.decide` and
        # `adopt_venue_position` already take. Reporting a pair priced from
        # a zero basis would make every completion look free.
        basis = ((hc / hq) if (hc is not None and hq is not None
                               and hq > 0 and hc > 0) else None)
        out["held_avg_basis"] = None if basis is None else str(basis)
        out["need"] = (None if (hq is None) else str(hq - dqv))
        if basis is not None:
            out["pair_if"] = {
                "at_touch": str(basis + at_touch),
                "behind_touch": str(basis + behind),
                "marketable": str(basis + marketable),
                "breakeven_pair": str(BREAKEVEN_PAIR),
                "redemption": str(REDEMPTION_PAIR),
                "labels": ("what the pair WOULD have cost had that leg "
                           "filled in full. Conditional, never realized")}
        return out
    except BaseException:                                      # noqa: BLE001
        return {}


def decide(*, market: str, held_side: str,
           held_qty: Any, held_cost_usd: Any,
           deficient_qty: Any = None, deficient_cost_usd: Any = None,
           book: Mapping[str, Any],
           now_t: float, window_start: float,
           resting: Sequence[Mapping[str, Any]] = (),
           tick: Any = "0.01", min_qty: Any = "0.01",
           depth_ticks: int = 2,
           residual_budget_usd: Any = None,
           breakeven_pair: Any = None,
           submissions_left: Optional[int] = None,
           outstanding_qty: Any = None,
           last_decide_t: Optional[float] = None) -> Dict[str, Any]:
    """One decision for inventory already held. Submits nothing.

    Returns `action` in COMPLETE / REPRICE / HOLD with `ok` and `reason`,
    and on a COMPLETE or REPRICE a `leg` the caller may turn into an
    intent. HOLD is the default on every uncertainty: a decision this
    cannot make safely is one it declines to make.
    """
    rec: Dict[str, Any] = {"market": market, "held_side": held_side,
                           "policy": "BOOK_COMPLETION"}

    # ---- 0. CADENCE. Bounded re-decision, so this cannot become a
    #         requote per tick.
    if last_decide_t is not None and (now_t - last_decide_t) < DECIDE_EVERY_S:
        # THE CONDITION IS UNCHANGED. Only the RECORD gains the book that
        # was already in hand, so a cooldown refusal can be told apart from
        # one that declined a priceable completion. See
        # `cooldown_observation`: reporting only, {} on any failure.
        rec.update(cooldown_observation(
            held_side=held_side, held_qty=held_qty,
            held_cost_usd=held_cost_usd, deficient_qty=deficient_qty,
            book=book, tick=tick, depth_ticks=depth_ticks,
            now_t=now_t, last_decide_t=last_decide_t))
        return _no("DECIDE_COOLDOWN", **rec)

    # ---- 1. IS THERE ANYTHING TO COMPLETE? Only an UNMATCHED remainder.
    #         After a completed round BOTH sides hold stock, and treating
    #         "the other side holds something" as a completion is what once
    #         routed a new first leg down this path and stopped accumulation
    #         dead at one pair.
    hq, dq = _d(held_qty), _d(deficient_qty) or D(0)
    if hq is None or hq <= 0:
        return _no("NO_INVENTORY", **rec)

    # ---- 1a. WHAT IS ALREADY WORKING ON THE SIDE WE WOULD BUY IS ALREADY
    #          OURS, and subtracting it is the whole of the reservation.
    #
    # WHAT THIS CLOSES. `need` used to be `held - deficient`, read from
    # POSITIONS only. A resting order on the deficient side is not a
    # position yet, so it counted for nothing: holding 3 UP with a DOWN
    # order for 3 still working computed `need = 3` and decided a SECOND
    # DOWN order for 3. Nothing downstream merges them -- this policy
    # cancels nothing, as its own sibling test asserts of the caller -- so
    # both could fill and leave 6 DOWN against 3 UP. The excess is not a
    # hedge; it is a fresh directional position on the side we were trying
    # to flatten, and it arrives at exactly the moment the engine believed
    # it was reducing risk.
    #
    # WHY IT ALSO SHRINKS A PARTIAL. A partially filled DOWN order books
    # its filled part into `dq` and keeps the rest working. Both parts are
    # spoken for, and only the sum of them is.
    #
    # UNREADABLE IS NOT ZERO. The caller derives this from
    # `worst_case_remaining`, which charges an unreadable row at the
    # absolute residual ceiling rather than at nothing. So an unreadable
    # ledger makes the reservation LARGER and this policy more reluctant --
    # the same fail-closed direction every other bound in the engine takes.
    #
    # THIS CANNOT CREATE A COMPLETION. It only ever makes `need` smaller,
    # so every path below either refuses where it used to act, or sizes
    # smaller. There is no input for which the reservation enlarges an
    # order.
    # AN UNREADABLE RESERVATION REFUSES, rather than reserving nothing. The
    # `or D(0)` idiom used everywhere else in this module is right for an
    # optional INPUT and wrong for a BOUND: it maps "we could not read how
    # much is working" onto "nothing is working", which is the one reading
    # that can place the duplicate order. `None` still means the caller
    # passed no reservation at all, which is how every existing caller
    # behaves and why parity holds.
    if outstanding_qty is None:
        res = D(0)
    else:
        res = _d(outstanding_qty)
        # NaN AND INFINITY ARE UNREADABLE, NOT LARGE. `_d` parses both
        # happily -- `Decimal("NaN")` is a Decimal -- and then every
        # comparison against one RAISES InvalidOperation, which would take
        # the whole completion path down rather than refuse it. The finite
        # test belongs with the parse, not after the first comparison.
        if res is None or not res.is_finite():
            return _no("OUTSTANDING_UNREADABLE",
                       outstanding_raw=str(outstanding_qty)[:40], **rec)
        if res < 0:
            res = D(0)
    rec["reserved"] = str(res)
    need = hq - dq - res
    if need <= 0:
        # NAMED APART FROM `ALREADY_MATCHED`, because the two mean opposite
        # things operationally: matched inventory is a finished pair, while
        # a reserved remainder is a pair still in flight at the venue. A
        # reader who cannot tell them apart cannot tell a completed round
        # from one waiting on a fill.
        return _no("ALREADY_MATCHED" if res <= 0 else "ALREADY_RESERVED",
                   matched=str(dq), **rec)
    rec["need"] = str(need)

    want = "DOWN" if str(held_side).upper() == "UP" else "UP"
    rec["side"] = want

    # ---- 2. TIME. Two clocks, because `complete_cutoff_s()` alone is 720
    #         while the cancel margin binds from 600 -- a guard built on the
    #         later one would arm entirely inside dead time.
    t_in = now_t - float(window_start)
    try:
        cancel_at = (TS.cancel_cutoff(float(window_start) + TS.WINDOW_S)
                     - float(window_start))
    except BaseException:                                      # noqa: BLE001
        return _no("CUTOFF_UNREADABLE", **rec)
    horizon = min(float(TS.complete_cutoff_s()), float(cancel_at))
    rec["t_in_window_s"] = round(t_in, 2)
    rec["horizon_s"] = round(horizon, 2)
    if t_in >= horizon:
        return _no("PAST_TERMINAL", **rec)

    # ---- 3. BOOK FRESHNESS. A stale book is not a cheap book, and the one
    #         direction that fails closed is declining to act on it.
    age = _d(book.get("stream_age_s"))
    if age is None:
        return _no("BOOK_AGE_UNKNOWN", **rec)
    # REPORTING ONLY, and deliberately before the freshness test: the age
    # used to be recorded only when it FAILED, so a passing evaluation
    # carried no evidence of how fresh the book it acted on was. That made
    # the 2-second requirement unauditable in exactly the case that
    # matters -- the one where we traded. Carried on every outcome now.
    rec["stream_age_s"] = str(age)
    # NEGATIVE IS STALE, not fresh. A future-dated stream age means the
    # snapshot is dated ahead of us, which is the one reading that must
    # never pass a freshness test. Same guard as l5_two_sided_probe.
    if age < 0 or age > D(str(TS.MAX_BOOK_AGE_S)):
        return _no("STALE_BOOK", **rec)
    if book.get("gap_suspected"):
        return _no("BOOK_GAP_SUSPECTED", **rec)
    bid, ask = _d(book.get("bid")), _d(book.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return _no("BOOK_UNUSABLE", **rec)

    # ---- 4. SUBMISSION BUDGET, as the caller reports it. Never a second
    #         copy of the counter.
    if submissions_left is not None and submissions_left <= 0:
        return _no("NO_SUBMISSIONS_LEFT", **rec)

    # ---- 5. ECONOMICS, from the SAME function the L5-action path uses, so
    #         this path cannot price a completion differently from the one
    #         it is meant to replace. `complement_order` judges against the
    #         completion ceiling -- does completing beat carrying the
    #         residual -- rather than the 0.96 opening cap.
    avg = None
    hc = _d(held_cost_usd)
    if hc is not None and hq > 0:
        avg = hc / hq
    comp = TS.complement_order(
        side=want, qty=need, cost_px=avg,
        held_qty=hq, held_cost_usd=hc,
        deficient_qty=dq, deficient_cost_usd=_d(deficient_cost_usd),
        residual_budget_usd=_d(residual_budget_usd),
        bid=bid, ask=ask, tick=_d(tick), min_qty=_d(min_qty),
        depth_ticks=depth_ticks,
        book_age_s=age, gap_suspected=bool(book.get("gap_suspected")),
        market=market, held_side=held_side,
        bid_shares=book.get("bid_shares"), ask_shares=book.get("ask_shares"))
    if not comp.get("ok"):
        return _no("ECONOMICS:%s" % str(comp.get("reason"))[:60], **rec)
    leg = dict(comp.get("leg") or {})
    px = _d(leg.get("price"))
    if px is None or str(leg.get("side", "")).upper() != want:
        return _no("LEG_UNUSABLE", **rec)

    # ---- 6. MAKER ONLY, ASSERTED ON THE PRICE. Never inferred from a mode
    #         string: a quote that would take the touch is refused here even
    #         if something upstream labelled it MAKER.
    # `leg["price"]` IS ALREADY THE OWN PRICE, and converting it again was a
    # defect that refused every DOWN completion resting at the touch.
    #
    # `complement_order` returns both: `price` in own terms and `wire_price`
    # beside it, and they sum to 1.00 (verified: own 0.09 / wire 0.91 on a
    # 0.90/0.91 book). `_would_cross` takes the OWN price and does the wire
    # conversion itself. The previous form passed `1 - px` -- the WIRE price
    # -- into that own-price parameter, so for DOWN the helper converted a
    # second time and tested `own <= bid` instead of `wire <= bid`.
    #
    # WHAT IT COST. A DOWN complement resting at the touch on a 0.90/0.91
    # book is a YES offer AT the ask: a maker order that cannot cross. The
    # correct test returns False; the double conversion returned True, so
    # the quote was refused WOULD_CROSS. With COMPLETION_DEPTH_TICKS = 0 the
    # completion always prices at the touch, so EVERY DOWN completion on
    # this route was refused -- including 2130z of the Stage A campaign,
    # which held UP 2.05 and needed a DOWN complement, and settled -$0.9430
    # stranded. UP was unaffected, because for UP the conversion is the
    # identity, which is why the defect survived the window that completed.
    #
    # AND IT WAS WORSE THAN THE CROSS CHECK. The same converted value fed
    # step 7's `pair = avg + own`, so a DOWN complement's PAIR COST was
    # computed as `avg + (1 - px)` instead of `avg + px`: on the 2130z state
    # that is 0.46 + 0.91 = 1.37 rather than 0.46 + 0.09 = 0.55, which trips
    # PAIR_ABOVE_REDEMPTION all on its own. Every DOWN completion therefore
    # had TWO independent spurious refusals in front of it, and
    # `complement_order` -- which computes `pair = paid + px` correctly --
    # had already said yes.
    own = px
    if _would_cross(want, own, bid, ask):
        return _no("WOULD_CROSS", price=str(px), bid=str(bid), ask=str(ask),
                   **rec)

    # ---- 7. THE PAIR MUST NOT BUY A CERTAIN LOSS. A matched pair redeems
    #         exactly $1.00 a share, so `pair <= 1.00` is the line between a
    #         dear completion and a guaranteed loss. That is the SAME test
    #         the submission boundary already applies -- `eff > D(1)` in
    #         `completion_pair_rescue` -- and this module's contract is to
    #         hold no limit of its own, so it is the only hard line here.
    #
    #         1445z, 2026-10-30, IS WHY THIS CHANGED. The previous form
    #         refused whenever `pair > BREAKEVEN_PAIR`, treating the 0.9532
    #         target as a ceiling. Held 1.91 DOWN at a 0.37 basis on a book
    #         1 tick wide (bid 0.6200 / ask 0.6300), EVERY maker completion
    #         price yields a pair >= 0.97, so this gate refused on every tick
    #         for the final 12 minutes while `complement_order` -- which owns
    #         completion economics and had already cleared the leg at 0.6200
    #         against `completion_ceiling` -- said ok. The leg settled
    #         unmatched for -$0.7067 where completing at 0.99 would have
    #         redeemed +$0.0191 on 1.91 contracts. A pair at or below $1.00
    #         is never a loss, so refusing one to protect a preference for a
    #         cheaper pair strands the very inventory most in need of
    #         completion.
    #
    #         0.9532 SURVIVES AS A PREFERENCE, NOT A PROHIBITION. It is
    #         recorded on every decision so the tape still distinguishes a
    #         preferred completion from a dearer risk-reducing one, and
    #         `l5_two_sided_probe` remains its one source. The opening cap
    #         (0.96) is untouched and still governs opening a new pair: this
    #         path only ever completes inventory the ledger already holds.
    #
    #         CARRY NEEDS NO PROBABILITY ESTIMATE. The operating model holds
    #         that residual directional exposure has no demonstrated
    #         predictive skill, so carrying an unmatched leg is not a
    #         positive-expectancy alternative to a certain non-negative
    #         pair. Completing at <= $1.00 removes directional risk at
    #         non-negative locked P&L, which is why no mark-implied
    #         probability is read here.
    be = _d(breakeven_pair) or BREAKEVEN_PAIR
    pair = None if avg is None else (avg + own)
    rec["pair_own_cost"] = None if pair is None else str(pair)
    rec["breakeven_pair"] = str(be)
    if pair is None:
        return _no("NO_FIRST_LEG_PRICE", **rec)
    # The preference, recorded either way and never a refusal on its own.
    rec["within_preferred_target"] = bool(pair <= be)
    rec["locked_pnl_usd"] = str((D(1) - pair) * need)
    #
    #         `pair` IS AN AVERAGE OVER THE WHOLE HELD SIDE, which is the
    #         defect this now has an answer for. `avg = hc / hq` blends an
    #         already-MATCHED pair's cost into the economics of a cheap
    #         STRANDED leg, so a completion that locks a profit can read as
    #         a pair above redemption, and the whole-book test is the one
    #         that answers whether it actually does.
    #
    #         THIS IS THE PATH THE RECORDED REFUSALS CAME FROM, which is
    #         why the bound is wired here and not only in
    #         `completion_requote`. The 1315z-1400z campaign raised
    #         `book_completion:PAIR_ABOVE_REDEMPTION` 182 times at this
    #         PLACEMENT decision -- its largest economic refusal. The
    #         requote path governs REPRICING an order already resting, so
    #         the bound there could not have reached any of them.
    #
    #         IT WOULD HAVE ADMITTED NONE OF THOSE 182, and that is the
    #         measured answer rather than the hoped-for one. On the 1400z
    #         book from its own `L5_TS_COMPLETION` records -- DOWN 4.00 at
    #         avg 0.4730, UP 2.23 at avg 0.3400, need 1.77, `worst_before
    #         -0.530200`, which `net_settlement` reproduces to the cent --
    #         the whole-book cap is 0.68 while UP was executable at own
    #         0.72 to 0.85 all window. The engine already reports this
    #         question directly as `both_positive_after`, and across
    #         13:15z-14:31z not one record carries it true. The averaged
    #         basis and the whole-book test agreed: those completions
    #         really would have locked a loss.
    #
    #         SO THIS IS A CORRECTNESS FIX TO THE BASIS, NOT A SOURCE OF
    #         FILLS. It stays default-off, and the evidence for enabling it
    #         is a book where `both_positive_after` is true -- which this
    #         campaign never produced.
    #
    #         THE BOUND IS STRICTLY STRONGER THAN THE TEST IT REPLACES.
    #         `pair <= 1.00` promises the matched portion never settles at
    #         a loss; `portfolio_completion_cap` asks the engine's own
    #         `net_settlement` whether the WHOLE BOOK settles >= 0 on BOTH
    #         outcomes, fees included. Where the two disagree on an
    #         averaged basis it is the average that is wrong.
    #
    #         DEFAULT OFF, and it only ever ADMITS. Unset leaves this
    #         refusal exactly as it was, and a `None` cap -- unreadable
    #         inputs, or no qualifying price -- also refuses. It cannot
    #         tighten anything either, because every gate above has already
    #         run and `complement_order` has already cleared the leg
    #         against the owner's deficit budget.
    if pair > REDEMPTION_PAIR:
        _pcap = (TS.portfolio_completion_cap(
            side=want, held_qty=hq, held_cost_usd=hc,
            deficient_qty=dq, deficient_cost_usd=_d(deficient_cost_usd),
            buy_qty=need, tick=_d(tick), ceiling=own)
            if TS.portfolio_completion_enabled() else None)
        rec["portfolio_cap"] = None if _pcap is None else str(_pcap)
        if _pcap is None or _pcap < own:
            return _no("PAIR_ABOVE_REDEMPTION",
                       redemption=str(REDEMPTION_PAIR), **rec)
        rec["admitted_by"] = "PORTFOLIO_PAYOFF"

    # ---- 8. IDEMPOTENCE AND REPRICING, against what is ALREADY RESTING.
    #         The same decision taken twice must not place two orders, and a
    #         resting completion is replaced only to go CHEAPER -- chasing a
    #         quote upward is the thing the downward-only requote exists to
    #         forbid, and repricing also forfeits queue position.
    mine = [r for r in resting
            if str(r.get("side", "")).upper() == want
            and str(r.get("market") or market) == str(market)]
    if mine:
        # AN UNREADABLE RESTING PRICE FAILS CLOSED, and it did not used to.
        #
        # This read was `min((_d(r.get("price")) or D(9)) for r in mine)`.
        # The `or D(9)` sentinel was meant to stand for "so dear that ours
        # is better", but the comparison happens in OWN terms and the
        # conversion is side-dependent, so 9 is only dear on one side:
        #
        #   want UP    rest_own = 9       own >= 9 is False  -> REPRICE
        #   want DOWN  rest_own = 1 - 9   own >= -8 is True   -> HOLD
        #
        # So a row whose price could not be read refused safely when we
        # wanted DOWN and, when we wanted UP, CANCELLED AND REPLACED an
        # order whose price was unknown -- forfeiting its queue position,
        # possibly chasing upward, which is the one thing the downward-only
        # rule exists to forbid, and recording `CHEAPER_THAN_RESTING` with
        # nothing to support it. The asymmetry was an accident of the
        # sentinel, and the safe branch was the accidental one.
        #
        # Declining is the safe direction here because the order ALREADY
        # EXISTS at the venue: leaving it resting may fill, while replacing
        # it destroys a position we cannot evaluate. It is the same
        # fail-closed reasoning as the unreadable book age above.
        #
        # (`if best is not None` was unreachable under the sentinel. Had it
        # ever been reached it would have fallen through to COMPLETE, which
        # is a DUPLICATE order against a row we knew was resting.)
        prices = [_d(r.get("price")) for r in mine]
        if any(p is None for p in prices):
            return _no("RESTING_PRICE_UNREADABLE",
                       resting_rows=len(mine),
                       unreadable_rows=sum(1 for p in prices if p is None),
                       **rec)
        best = min(prices)
        rest_own = best if want == "UP" else (D(1) - best)
        if own >= rest_own:
            return _no("ALREADY_RESTING_AT_OR_BETTER",
                       resting_own=str(rest_own), quote_own=str(own),
                       **rec)
        rec["replaces_resting_own"] = str(rest_own)
        return dict(rec, action="REPRICE", ok=True,
                    reason="CHEAPER_THAN_RESTING", leg=leg,
                    key=decision_key(market, want, need, px))
    return dict(rec, action="COMPLETE", ok=True,
                reason=("PAIR_BEATS_CARRY" if rec["within_preferred_target"]
                        else "RISK_REDUCING_COMPLETION"),
                leg=leg, key=decision_key(market, want, need, px))


def decision_key(market: str, side: str, qty: Any, price: Any) -> str:
    """The idempotence key. The SAME decision yields the SAME key, so a
    retry is recognisable as a duplicate rather than placed twice. It is
    deliberately price- and quantity-bearing: a decision at a different
    price is a different decision."""
    return "bc|%s|%s|%s|%s" % (market, str(side).upper(),
                               str(qty), str(price))
