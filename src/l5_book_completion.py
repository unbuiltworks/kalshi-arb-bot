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
from typing import Any, Dict, Mapping, Optional, Sequence

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
        return _no("DECIDE_COOLDOWN", **rec)

    # ---- 1. IS THERE ANYTHING TO COMPLETE? Only an UNMATCHED remainder.
    #         After a completed round BOTH sides hold stock, and treating
    #         "the other side holds something" as a completion is what once
    #         routed a new first leg down this path and stopped accumulation
    #         dead at one pair.
    hq, dq = _d(held_qty), _d(deficient_qty) or D(0)
    if hq is None or hq <= 0:
        return _no("NO_INVENTORY", **rec)
    need = hq - dq
    if need <= 0:
        return _no("ALREADY_MATCHED", matched=str(dq), **rec)
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
    if age > D(str(TS.MAX_BOOK_AGE_S)):
        return _no("STALE_BOOK", stream_age_s=str(age), **rec)
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
        best = min((_d(r.get("price")) or D(9)) for r in mine)
        if best is not None:
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
