"""Inventory-aware decision controller for PMUS BTC 5-minute up/down. PURE.

One function, `decide`, maps (YES book, time in window, inventory, resting
orders, optional probability) to ONE action plus the orders that implement
it. It does no I/O, reads no clock, holds no state and never mutates its
inputs; the only environment it reads is the on/off switch in `enabled()`.
It places nothing itself: a caller must act on the returned orders.

MARKET. Binary; UP (YES) wins when the BTC index at window end is at or
above the index at window start. 300 s window, tick 0.01, minimum quantity
0.01. Taker fee 0.095 * p * (1-p) per share, maker fee 0, NO rebate assumed.

OWN-PRICE CONVENTION. Every price here is the price of the side being
bought: UP price = YES price, DOWN price = 1 - YES price. So a DOWN bid is
1 - YES ask and a DOWN ask is 1 - YES bid.

WHY IT MOSTLY HOLDS. A passive buy below the bid has measured as adversely
selected (about 6 cents a share on a proxy). The value of a maker buy at x
on side s is therefore p_s - x - adverse_selection_per_share, and an entry
needs that to clear min_edge_per_share. With the book mid as the only
probability, the estimate is not independent of the price being paid
(`independent_estimate: False`) and a normal tight book fails the test.

FAIL CLOSED. Any unreadable input returns HOLD_OR_SKIP with a named reason
and cancels every resting order, as does an unusable or stale book: nothing
is left working that this decision could not re-justify.

Every PLACE is a post-only maker BUY unless `Params.taker_allowed` is True
(default False), which is the only way EXIT_OR_FLIP can be emitted.

TWO ENTRY RULES, selected by `Params.entry_mode` and described there.
"EDGE_GATED" is the original and the default. "ACCUMULATE" is favorite
accumulation: repeated small maker buys of the current favorite at an
eligible paid price, for the whole window, while the cap has room, with no
edge gate and no pair formation. Nothing about the caps, the halts, the
post-only rule or the authorization path differs between them.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace as _dc_replace
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

D = Decimal

CONTROLLER_ENV = "L5_BTC5M_CONTROLLER"

DISABLED = "DISABLED"
OPEN_FAVORITE = "OPEN_FAVORITE"
ADD_FAVORITE = "ADD_FAVORITE"
HEDGE_UNDERDOG = "HEDGE_UNDERDOG"
REPRICE = "REPRICE"
EXIT_OR_FLIP = "EXIT_OR_FLIP"
HOLD_OR_SKIP = "HOLD_OR_SKIP"

UP = "UP"
DOWN = "DOWN"
SIDES = (UP, DOWN)

#: the two entry rules. See `Params.entry_mode`.
EDGE_GATED = "EDGE_GATED"
ACCUMULATE = "ACCUMULATE"
#: "STRADDLE" rests a FIXED limit on BOTH sides for the whole window and
#: never requotes. It has no favorite, no band and no edge gate, because it
#: is not a view on which side wins -- it is a bet that the window reverses
#: hard enough to fill both legs cheaply. See `Params.straddle_price`.
STRADDLE = "STRADDLE"
ENTRY_MODES = (EDGE_GATED, ACCUMULATE, STRADDLE)

from src import l5_late_favorite as LF          # noqa: E402  (pure, stdlib)
from src import l5_strategy_arbiter as AR       # noqa: E402  (pure, stdlib)
from src import l5_straddle_ev as SEV
from src import l5_venue_constraints as VC     # noqa: E402  (pure, stdlib)

ZERO = D("0")
ONE = D("1")
HALF = D("0.5")
CENT = D("0.01")
PRICE_MIN = D("0.01")
PRICE_MAX = D("0.99")


def enabled() -> bool:
    """True only when the switch is exactly "ON". Anything else is off."""
    return os.environ.get(CONTROLLER_ENV) == "ON"


@dataclass(frozen=True)
class Params:
    band_lo: Decimal = D("0.51")              # favorite bid band, inclusive
    band_hi: Decimal = D("0.90")
    depth_ticks: int = 2                      # entry rests this far below the bid
    tick: Decimal = D("0.01")
    min_qty: Decimal = D("0.01")
    clip_usd: Decimal = D("0.10")             # dollars per entry
    #: SHARES per entry, when the owner means a share count rather than a
    #: dollar amount. None (the default) sizes by `clip_usd`. The owner's
    #: manual orders were 0.10 SHARES (about $0.06-$0.09 each); the pilot
    #: brief says about $0.10 per order. They are different sizes and the
    #: choice has to be stated, so both exist and neither is inferred.
    clip_shares: Optional[Decimal] = None
    max_exposure_usd: Decimal = D("5.00")     # filled cost + resting notional
    hedge_reserve_usd: Decimal = D("1.50")    # part of the cap entries may not use
    min_edge_per_share: Decimal = D("0.01")
    adverse_selection_per_share: Decimal = D("0.06")
    hedge_target_pair: Decimal = D("0.98")
    hard_pair_ceiling: Decimal = D("1.00")
    quote_ttl_s: float = 20.0
    stale_book_s: float = 3.0
    no_new_entry_after_s: float = 270.0
    #: MINIMUM EXECUTABLE DEPTH ON THE COMPLEMENT SIDE BEFORE A FIRST LEG.
    #: Zero is OFF and is the shipped default.
    #:
    #: THE EVIDENCE, from the 290 recorded ladders of 2026-10-25 bucketed
    #: by seconds remaining (analysis/l5/btc5m/):
    #:
    #:   tau 600-900  100% two-sided   bid 1133 / ask  972   50 ask levels
    #:   tau 420-600  100% two-sided   bid  675 / ask  401   48
    #:   tau 300-420  100% two-sided   bid  533 / ask  308   32
    #:   tau 180-300  100% two-sided   bid  619 / ask  834   12
    #:   tau 120-180  100% two-sided   bid  801 / ask  771    8
    #:   tau  60-120   56% two-sided   bid 2102 / ask  768    1
    #:   tau   0- 60    0% two-sided   NO ASK AT ALL          0
    #:
    #: The spread is 1.00c throughout and depth at the touch is 300-1100
    #: contracts against clips of 0.17-2, so depth never binds EARLY. What
    #: happens late is that one side disappears: ask levels fall 50 -> 0
    #: while bid levels climb 40 -> 92. A pair cannot be completed against
    #: a side that has no orders.
    #:
    #: AND THE ENGINE OPENS INTO EXACTLY THAT. `no_new_entry_after_s` is
    #: 270 of a 300-second window, i.e. first legs are admitted down to
    #: tau=30, where two-sided liquidity was observed 0% of the time. The
    #: 0130z window is the recorded case: the first leg filled, the
    #: complement had NO ASK in all eight late frames, the pair never
    #: completed and it settled -$1.1594.
    #:
    #: WHAT THIS IS NOT. It is NOT the price test I proposed and then
    #: withdrew -- requiring the complement to be CHEAP at entry would
    #: have refused 7 of 7 recorded episodes, 6 of which completed below
    #: par at 0.72-0.96, because the complement gets cheaper later. This
    #: requires only that the complement EXISTS with depth. Those are
    #: different claims and the evidence separates them.
    #:
    #: It can only ever REFUSE an opening, never admit one.
    min_complement_shares: Decimal = D("0")
    #: LATE-FAVOURITE KNOBS. Every default is the value that REFUSES, so
    #: the second strategy cannot act until each is set deliberately:
    #: a zero clip buys nothing, a zero edge margin returns
    #: NO_EDGE_MARGIN_SET, and an unset continuation estimate returns
    #: NO_CONTINUATION_ESTIMATE rather than making a handoff look free.
    #: STRADDLE ENTRY-EV GATE. Every rate defaults to None, which
    #: REFUSES: the gate exists so a rung is bought because it is worth
    #: buying, and a guessed rate would manufacture the edge it tests
    #: for. Measured over 503 shadow windows, for the owner to set
    #: deliberately: p_pair_natural 0.070, p_complete 0.131,
    #: p_lone_win 0.0981 -- which price a 0.08/0.08 rung at +9.55c a
    #: share against a 0.83 completion target.
    straddle_p_pair_natural: Optional[Decimal] = None
    straddle_p_complete: Optional[Decimal] = None
    straddle_p_lone_win: Optional[Decimal] = None
    straddle_fee_per_share: Decimal = D("0")
    straddle_min_ev_per_share: Decimal = D("0")
    #: HOW LATE the favourite may be bought, in seconds before close.
    #: 0 -- the default -- cannot fire and refuses, deliberately. The
    #: module's own default of 30 is WORSE than refusing: the session
    #: steps only while `t < window_s - CANCEL_LEAD_S` (270 of 300), and
    #: a 30-second window fires from t=270, so the two never overlap. It
    #: refused TOO_EARLY on every step of every window and looked like a
    #: quiet market. Measured: zero steps at t>=270 across 20 windows.
    late_favorite_window_s: Decimal = D("0")
    late_favorite_clip_usd: Decimal = D("0")
    late_favorite_fee_per_share: Decimal = D("0")
    late_favorite_min_edge_per_share: Decimal = D("0")
    late_favorite_continuation_usd: Optional[Decimal] = None
    late_favorite_handoff_margin_usd: Optional[Decimal] = None
    window_s: float = 300.0
    taker_allowed: bool = False
    taker_fee_coef: Decimal = D("0.095")
    flip_margin: Decimal = D("0.05")
    #: How entries keep room for the completing (opposite-side) order.
    #: "FIXED": entries may not use the last `hedge_reserve_usd` of the cap
    #: (the original rule; meaningless when the cap is below the reserve).
    #: "COMPLETION": an entry is placed only if, after it, the cap still
    #: holds the cost of completing EVERY unmatched share -- held, resting
    #: on the favorite side, and the new entry -- at the target pair price,
    #: plus `completion_fee_per_share`. Shares already resting on the
    #: completing side are counted once, as resting notional, not reserved
    #: again. The executor runs in this mode with its effective ceiling.
    #: "NONE": entries may use the whole cap. Only coherent with
    #: `hedge_enabled` False -- there is no completing order to reserve for.
    reserve_mode: str = "FIXED"
    completion_fee_per_share: Decimal = D("0")

    # ------------------------------------------------- entry mode
    #: WHICH ENTRY RULE APPLIES. Two, and they are different strategies.
    #:
    #: "EDGE_GATED" (the default, unchanged): one entry at a time, resting
    #: `depth_ticks` below the favorite's bid, admitted only when
    #:     p_side - price - adverse_selection_per_share >= min_edge_per_share
    #: On a one-cent book that asks the entry to sit about SEVEN cents under
    #: the bid (0.06 + 0.01), which a 5-minute book does not offer: every
    #: step of the 2026-11-12 06:30z and 08:00z windows refused with
    #: EV_BELOW_MIN while $0.41 of the $0.45 cap sat unused.
    #:
    #: "ACCUMULATE" (favorite accumulation, owner's contract 2026-11-12):
    #: repeated small post-only maker buys of the CURRENT FAVORITE at an
    #: eligible price, throughout the window, while capacity remains. The
    #: edge gate is NOT an entry condition: the eligible price band is. The
    #: band is judged ON THE PRICE THIS ORDER WOULD PAY -- `band_lo` through
    #: `band_hi` inclusive -- not on the touch it is derived from, because
    #: the band is a statement about the price paid. `ev_per_share` is still
    #: computed and reported on every decision, as evidence; it blocks
    #: nothing. Adding to a side STOPS the moment that side is no longer the
    #: favorite, and anything resting on the other side is cancelled.
    entry_mode: str = "EDGE_GATED"
    #: STRADDLE only: the fixed own-price each side rests at, all window.
    #: A matched pair costs exactly 2 x this and pays 1.00, so anything at
    #: or above 0.50 locks a LOSS by construction and is refused as
    #: unreadable rather than treated as a configuration choice. Measured
    #: best on the 189-window tape around 0.05-0.10 (analysis/l5/
    #: btc5m_accumulation/straddle.py); the default is deliberately the
    #: do-nothing value so the mode cannot trade until it is set.
    straddle_price: Decimal = D("0")
    #: STRADDLE only: extra rungs. Empty means the single `straddle_price`.
    #: A LADDER IS LEVERAGE, NOT DIVERSIFICATION -- every rung fills on the
    #: same loser collapse and pays on the same reversal, so they are
    #: perfectly correlated and widening the ladder does NOT improve the
    #: risk-adjusted result. Measured on the 189-window tape: 0.05-0.20
    #: returns 10x the absolute profit of a single rung for 27x the
    #: capital, with t falling 1.67 -> 1.17; 0.05-0.25 and 0.10-0.25 go
    #: NEGATIVE out of sample. Only the narrow cheap end (0.05-0.10) holds
    #: its return on capital. Rungs at or above 0.50 are refused with the
    #: single price, for the same reason.
    straddle_rungs: tuple = ()
    #: =====================================================================
    #: BOUNDED REPEATED ENTRIES, replacing the one-fill-per-side lockout.
    #:
    #: The lockout was `if inv[side][0] > 0: continue` -- "this side is
    #: done". It capped the strategy at two fills a window against the
    #: 106-207 per window measured on the behaviour being replicated, and
    #: it was ALSO the de-facto unmatched-inventory bound: one clip a side
    #: means imbalance can never exceed one clip. So it cannot be removed
    #: on its own, and these three parameters replace both of its jobs.
    #:
    #: DEFAULT IS THE OLD BEHAVIOUR, BYTE FOR BYTE. `False` keeps the
    #: lockout exactly, so no configuration that exists today changes. The
    #: two dollar bounds are REQUIRED when it is enabled and refuse the
    #: whole mode when absent: repeated entry without an unmatched ceiling
    #: is a directional bet wearing a straddle's clothes, which is the one
    #: shape this strategy must never hold.
    straddle_repeat_enabled: bool = False
    #: per SIDE: filled cost + resting notional that side may reach.
    straddle_max_side_usd: Decimal = D("0")
    #: the cost of the UNMATCHED portion of inventory, whichever side is
    #: heavy. This is the real directional exposure and it is bounded in
    #: dollars rather than clips so a repriced rung cannot widen it.
    straddle_max_unmatched_usd: Decimal = D("0")
    #: =====================================================================
    #: ONE-SIDED ACCUMULATION NEEDS ITS OWN REASON.
    #:
    #: OBSERVED LIVE, window 2026-11-20 17:05Z. UP reached its side cap at
    #: t=119 s. From then on only DOWN could be quoted, and it was: eight
    #: more DOWN fills at 0.20 falling to 0.01 while UP ran from 0.84 to
    #: 0.99. Each was a leg whose partner could no longer be bought, so it
    #: was not half of a pair -- it was a directional position on the side
    #: the market was leaving, opened under a pair strategy's permission.
    #: The dollar unmatched ceiling did not see it: cheap contracts are
    #: cheap, so 0.44 unmatched shares cost four cents.
    #:
    #: WHAT IS ASKED OF A LEG. Shares that only bring this side up to what
    #: the other side already HOLDS complete pairs and are not judged here.
    #: Shares beyond that EXTEND the imbalance, and those need one of:
    #:
    #:   - the partner side is still quotable, so this is ordinary
    #:     two-sided quoting and the imbalance is temporary; or
    #:   - the leg stands on its own: an INDEPENDENT, fresh probability
    #:     says  p_side - price - adverse_selection_per_share  clears
    #:     min_edge_per_share, the same test the single-sided path uses.
    #:
    #: Otherwise the leg is refused. It is NOT a ban on imbalance and not a
    #: ban on one-sided buying: it asks that buying alone be justified
    #: alone. It can only add a refusal, and False -- the default -- leaves
    #: every decision byte for byte as it was.
    straddle_one_sided_control: bool = False
    #: =====================================================================
    #: A COMPLETING LEG IS PRICED AGAINST WHAT IS ALREADY HELD.
    #:
    #: The pair ceiling bounds the two prices quoted TOGETHER in one round.
    #: It says nothing about a leg bought now against a leg bought earlier:
    #: UP filled at 0.60 as it fell, then DOWN quoted at its bid of 0.49
    #: after UP reached 0.50, is a matched pair costing 1.09. Replayed over
    #: 187 recorded windows the live configuration's matched pairs averaged
    #: 1.017-1.023, and the matched line LOST money: every such pair was a
    #: completion bought after the market had moved against the first leg.
    #:
    #: With this set, the side that would COMPLETE held inventory is never
    #: quoted above  (this figure - the held side's average cost). Where
    #: the book's bid is higher the quote rests lower instead, at the price
    #: that completes the pair within the figure; where no legal price
    #: does, that side is not quoted (PAIR_COST_ABOVE_CAP) and the held leg
    #: stays unmatched, which the one-sided rule and the unmatched ceiling
    #: then govern. It prices; it adds no order and widens no limit. Zero
    #: -- the default -- changes nothing.
    straddle_max_completed_pair_cost: Decimal = D("0")
    #: =====================================================================
    #: WHICH SIDE MAY BE BID, BY ITS PRICE. A MECHANISM, NOT A FINDING.
    #:
    #: Portfolio settled net is the sum over fills of
    #: shares x (1 if that side won else 0 - price), so the return of a
    #: fill by the price paid IS the economics; pairs do not enter it.
    #:
    #: WHAT THE EVIDENCE SUPPORTS (analysis/l5/btc5m/ENTRY_RESEARCH_RESULT
    #: .txt, ENTRY_BAND_EVIDENCE_RESULT.txt). ROBUST: a bid on a side
    #: priced 0.15-0.45 loses -- in Bonereaper's real fills on both dates
    #: and under every weighting, and on our venue's quotes. NOT
    #: ESTABLISHED: that any band is positive. The favourite band
    #: 0.55-0.85 is positive in Bonereaper's fills only when windows are
    #: weighted equally; per fill and per dollar it is negative on the
    #: larger date. Replayed through this controller on 187 windows, a
    #: 0.55-0.85 band loses MORE per fill than quoting both sides, because
    #: a favourite that changes sides is bought on both at above 0.55.
    #:
    #: SO THIS STAYS OFF. It exists so a band can be tested without a code
    #: change, not because one has been found.
    #:
    #: With both set, a side is quoted only while its own quote price lies
    #: inside [min, max]. It applies to every two-sided leg, completing or
    #: not: it can only add a refusal (ENTRY_BAND). Both zero -- the
    #: default -- changes nothing. An inverted or out-of-range band refuses
    #: every leg rather than being read as no band.
    straddle_entry_bid_min: Decimal = D("0")
    straddle_entry_bid_max: Decimal = D("0")
    #: =====================================================================
    #: DO NOT REPLENISH THE SIDE THAT IS BOTH HEAVIER AND LOSING.
    #:
    #: Two-sided bidding is filled on whichever side is falling, so the
    #: book ends up long the side the market has turned against, and the
    #: next round bids for more of it. In 1,101 recorded windows of the
    #: trader this strategy is modelled on, 63-91% of new spending went to
    #: his heavier side while it was still the favourite and 25-42% once it
    #: had become the underdog (analysis/l5/bonereaper_recent/
    #: INVENTORY_TRAJECTORY_RESULT.txt). That is behaviour, not a proven
    #: edge; this switch lets the existing controller be replayed with it.
    #:
    #: With this set, in book-relative mode, a side is NOT quoted, and its
    #: resting two-sided legs are cancelled by id, while BOTH hold:
    #:   it holds more shares than the other side, and
    #:   its bid is below the other side's bid by more than the margin.
    #: The other side is untouched, so the pair can still be completed and
    #: simultaneous quoting resumes the moment either condition ends. It
    #: only removes orders (HEAVY_SIDE_IS_UNDERDOG): it places nothing,
    #: widens no limit and sells nothing. False -- the default -- leaves
    #: every decision byte for byte as it was.
    #: =====================================================================
    #: JUDGE EVERY ORDER ON THE WHOLE POSITION, NOT ON THE PAIR.
    #:
    #: The entry gate below prices a leg by the chance it becomes half of a
    #: pair, and charges adverse selection "on the lone branch only: a
    #: filled pair is locked and cannot be adversely selected". The replays
    #: contradict that: with every completing leg quoted inside a 0.99 pair
    #: ceiling, the pairs actually built cost 1.04 on average and are 58%
    #: of the loss (analysis/l5/btc5m/REMAINING_FILLS_ANATOMY_RESULT.txt).
    #: A completing bid is filled when ITS side is falling, exactly like any
    #: other bid.
    #:
    #: Settled P&L is linear in shares: a position holding U and D pays
    #: U - cost if UP wins and D - cost if DOWN wins, so one more share of a
    #: side bought at x adds (value of that share when the bid is filled)
    #: - x, whatever else is held. Pairing changes the SPREAD between the
    #: two outcomes, never the expectation. So with basis "POSITION":
    #:   * a leg's expected worth is `straddle_fill_edge_per_share` (the
    #:     MEASURED value of a filled share minus its price) minus the fee,
    #:     times its quantity -- the same for an opening, extending or
    #:     completing leg; no pair probability enters;
    #:   * that figure must be explicitly set from measurement. Unset, the
    #:     leg is refused (FILL_EDGE_NOT_SET): nothing is assumed;
    #:   * the step report carries, per proposed order, the position's
    #:     settled P&L if UP wins and if DOWN wins, before and after, and
    #:     the worst of the two. Risk stays with the existing side,
    #:     unmatched and window-loss ceilings, which are untouched.
    #: "" -- the default -- leaves the pair-based gate exactly as it was.
    straddle_ev_basis: str = ""
    straddle_fill_edge_per_share: Optional[Decimal] = None
    straddle_stop_adding_heavy_underdog: bool = False
    #: =====================================================================
    #: SPEND THE SIDE CAP ACROSS THE WINDOW, NOT IN ITS FIRST MINUTE.
    #:
    #: At a $0.60 window the caps are a dozen clips. Replayed on 1,091
    #: recorded windows the side cap first refused a quote 55 seconds in
    #: (median) and no order was working for 68% of the window
    #: (analysis/l5/btc5m/SMALL_CAPITAL_CONSTRAINTS_RESULT.txt), so the
    #: controller cannot respond to anything that happens after the first
    #: minute -- which is where the trader it is modelled on does most of
    #: his buying. With this set, the side cap in force at time t is
    #:     max(one clip, side cap x t / no_new_entry_after_s)
    #: reaching the full cap only at the entry cutoff. It can only LOWER
    #: the cap in force (SIDE_CAP_PACED); the configured cap, the unmatched
    #: ceiling and the window-loss ceiling are untouched. False -- the
    #: default -- leaves every decision as it was.
    straddle_pace_side_cap: bool = False
    #: how far below the other side's bid counts as the underdog
    straddle_heavy_underdog_margin: Decimal = D("0")
    #: how old the probability may be and still justify a lone leg
    straddle_one_sided_max_p_age_s: float = 5.0
    #: INVENTORY-AWARE REPRICING. Per clip of net imbalance, the HEAVY
    #: side's rung moves this many ticks DOWN (bidding less aggressively
    #: for more of what we already hold) and the light side's moves UP.
    #: Zero disables it and is the default, so a fixed limit still cannot
    #: drift unless an owner asks for it. Prices stay inside the mode's
    #: existing (0, 0.50) bound and on the tick grid; a shift that would
    #: leave either is clamped, never wrapped.
    straddle_reprice_ticks_per_clip: int = 0
    #: ==================================================================
    #: BOOK-RELATIVE TWO-SIDED QUOTING (k9Q2 / early-0x8dxd placement).
    #:
    #: WHY IT HAD TO BE ADDED RATHER THAN CONFIGURED. Two-sided quoting
    #: lived ONLY in this mode, and this mode priced from a constant by
    #: design. Worse, every rung was bounded to (0, 0.50) EXCLUSIVE --
    #: and since UP + DOWN is about 1.00, one side is ALWAYS at or above
    #: 0.50. So quoting both sides near the market was not merely
    #: unconfigured, it was arithmetically impossible.
    #:
    #: THAT BOUND WAS A GENERALISATION ERROR. Its reasoning -- "a pair
    #: costs twice this and pays 1.00, so 0.50 is a locked loss" -- is
    #: true only for a SYMMETRIC pair. UP 0.30 + DOWN 0.69 = 0.99 is a
    #: perfectly good pair that the per-rung bound forbids. The correct
    #: invariant is on the COMBINED cost, not on each leg, and that is
    #: what `straddle_pair_ceiling` enforces below.
    #:
    #: NECESSARY, NOT SUFFICIENT. A combined quote cost under 1.00 is
    #: required for matched-pair economics before costs. It guarantees
    #: NOTHING about fills or realized P&L. The entry-EV gate still
    #: decides whether a rung is worth buying, and
    #: `straddle_fill_model_measured` below refuses outright while the
    #: fill model for these prices is unmeasured.
    straddle_book_relative: bool = False
    #: Where the quote sits relative to OUR OWN side's bid, in ticks.
    #: 0 joins the bid (queue behind it); 1 improves it by a tick, taking
    #: queue priority at a worse price. Never crosses: the price is built
    #: from the bid, and a value that would reach the offer is refused.
    straddle_join_ticks: int = 0
    #: ==================================================================
    #: REQUOTE: FOLLOW THE BOOK INSTEAD OF STACKING BEHIND IT (issue #247).
    #:
    #: WHAT WAS MISSING. Book-relative mode priced a fresh quote from the
    #: live book every step and NEVER withdrew the previous one. Measured
    #: through the real executor on a book walking 0.50 -> 0.60 -> 0.45:
    #: six orders resting at six stale prices after four moves, every
    #: later quote refused MAX_LIVE_ORDERS, and UP 0.60 resting beside
    #: DOWN 0.53 -- a 1.13 pair under a 0.99 ceiling, because the ceiling
    #: was checked on each step's two quotes and never on what was
    #: actually left working. The trader being replicated holds ONE live
    #: opinion a side and moves it; this is that.
    #:
    #: THE RULE, per side, on STRADDLE_LEG orders only (a completion is
    #: managed by the completion block and is never touched here):
    #:   * resting ABOVE this step's quote  -> cancel. It is bidding more
    #:     than we now would, which is the adversely-selected direction,
    #:     and it is what let a resting pair exceed the ceiling. No
    #:     tolerance: with this, resting <= quote on both sides, so the
    #:     resting pair can never cost more than the quoted pair.
    #:   * resting BELOW it by at least this many ticks -> cancel. It has
    #:     been left behind.
    #:   * resting below it by LESS -> keep. QUEUE-AWARE: a one-tick drift
    #:     is not worth surrendering queue position for.
    #:   * no quote can be made this step (pair above ceiling, not
    #:     makeable) -> cancel: nothing justifies the order any more.
    #:
    #: NEVER CANCEL AND REPLACE IN ONE STEP. A side with a cancel issued
    #: gets no new order until a later step has seen the cancel land, so
    #: the old and the new quote are never both live and a fill that beats
    #: the cancel is booked before anything is re-bought. One order a side
    #: at most: a side that already has a kept quote is not quoted again.
    #:
    #: Zero -- the default -- disables all of it, byte for byte.
    straddle_requote_ticks: int = 0
    #: ==================================================================
    #: THE WINDOW'S WORST-CASE SETTLEMENT LOSS, BOUNDED DIRECTLY.
    #:
    #: WHY THE EXISTING BOUNDS DO NOT ADD UP TO ONE. The pair ceiling
    #: bounds the two prices QUOTED TOGETHER in one step. It says nothing
    #: about an UP leg filled at 0.97 at t=20s and a DOWN leg filled at
    #: 0.97 at t=200s after the market reversed: that is a MATCHED pair
    #: costing 1.94 and paying 1.00, the unmatched ceiling does not see it
    #: because nothing is unmatched, and each quote was under the pair
    #: ceiling when it was made. Bounded only by the per-side caps, a
    #: window could lose S * (2 - 1/0.99), about 0.99 of ONE side's cap --
    #: $1.19 at $1.20 a side -- while every existing check passed. An
    #: earlier proposal for this mode stated $0.55 from "unmatched cap
    #: plus a clip". That was wrong for exactly this reason.
    #:
    #: WHAT IS BOUNDED. A window settles UP or DOWN, so what it can lose
    #: is  total cost - min(UP shares, DOWN shares).  Before any leg is
    #: placed, that figure is taken over EVERY subset of the orders that
    #: could still fill -- everything resting, including orders with a
    #: cancel in flight, everything queued this step, and the leg itself
    #: -- and the leg is refused, WINDOW_LOSS_CEILING, if any subset
    #: exceeds this. Cost minus a minimum is convex in the fill
    #: fractions, so the maximum is at full-or-nothing fills and partial
    #: fills cannot exceed it. By induction no reachable inventory does.
    #:
    #: It can only ADD a refusal. Zero -- the default -- disables it and
    #: changes nothing.
    straddle_max_window_loss_usd: Decimal = D("0")
    #: WITHDRAW A LEG THAT HAS RESTED THIS LONG, by exact id, and quote
    #: afresh on a later step. Independent of the requote setting and of
    #: every hold, so no state can keep a leg past it. It exists because the independent
    #: watchdog latches the ACCOUNT-WIDE halt on any order older than
    #: 300s: a quote that sat unmoved on a quiet book from the open would
    #: be about 298s old when the window's end cancels it -- inside the
    #: limit by two seconds, and outside it on one slow step. Zero -- the
    #: default -- disables it.
    straddle_leg_max_age_s: float = 0.0
    #: ==================================================================
    #: THE BEHAVIOUR-PROBE WAIVER. INERT UNLESS IT HOLDS THE EXACT TOKEN.
    #:
    #: Set ONLY by `Btc5mExecutor.step`, per step, when
    #: `l5_behaviour_probe.evaluate` has validated the owner's
    #: one-window document for the market being stepped. It has no env
    #: reader of its own and no default but None.
    #:
    #: WHAT IT DOES. A leg the entry-EV gate refused SOLELY because a rate
    #: is not set (`*_NOT_SET`) is admitted, on the window loss ceiling
    #: instead of on expected value. It cannot turn a NEGATIVE EV into an
    #: admission, cannot admit anything while a rate is set, and is
    #: re-checked HERE against the loss ceiling, book-relative mode and the
    #: requote being in force -- so a waiver that arrived without them, by
    #: whatever route, admits nothing.
    straddle_ev_waiver: Optional[str] = None
    #: THE COMBINED BOUND, replacing the per-leg 0.50. The two quotes
    #: together may not cost more than this; at or above 1.00 a matched
    #: pair cannot profit before costs, so the ceiling must sit below it
    #: with room for fees. Zero disables book-relative quoting, which is
    #: the default.
    straddle_pair_ceiling: Decimal = D("0")
    #: HAS THE FILL MODEL FOR THESE PRICES BEEN MEASURED?
    #:
    #: The entry-EV gate's three rates were measured at 0.08 rungs. They
    #: do not transfer to a quote sitting on the bid: the fill
    #: probability, the natural-pair rate and the lone-leg win rate are
    #: all different there, and feeding 0.08-measured rates into a 0.30
    #: rung would not refuse -- it would return a CONFIDENT WRONG NUMBER.
    #: That is worse than refusing, so this is an explicit, separate
    #: permission. False -- the default -- refuses book-relative quoting
    #: with FILL_MODEL_UNMEASURED.
    #:
    #: It is NOT a bypass of the EV gate. The gate still runs and still
    #: has to admit the rung. This only says whether the rates it is
    #: being given are believed to describe these prices.
    straddle_fill_model_measured: bool = False
    #: RESERVE THE COST OF COMPLETING WHAT WE ARE ABOUT TO BUY.
    #:
    #: Measured 2026-11-17 window 1500z: a 0.08 entry filled twice for
    #: 3.74 shares, and completing it needed about $2.66 of the opposite
    #: side against a $1.60 envelope. Completion cost roughly NINE TIMES
    #: the entry, so entry and completion capital cannot be sized as if
    #: comparable -- the completion was unfundable by construction and
    #: never fired.
    #:
    #: Admitting qty q at price p reserves (ceiling - p) * q on top of
    #: the p * q spent now, i.e. the pair costs at most ceiling * q in
    #: total. The reserve therefore follows the ACTUAL entry price.
    #:
    #: IT GUARANTEES NOTHING. Reserved capital is capital, not a fill:
    #: the completion still has to be available and economic. And it
    #: must not silently stop all accumulation, so when the room is too
    #: small the SIZE IS SHRUNK to what the room can complete, and only a
    #: size below the venue minimum refuses, named COMPLETION_UNFUNDABLE.
    straddle_completion_reserve: bool = False
    #: ==================================================================
    #: ONE DECISION PROCESS OVER EVERY ALTERNATIVE (requirement 5).
    #:
    #: `src/l5_inventory_decision.rank()` already IS this: it prices
    #: HOLD, MAKER_COMPLETE, TAKER_COMPLETE and TAKER_PARTIAL on ONE
    #: arithmetic, ranks them by expected worst-case-after, and chooses
    #: HOLD unless an alternative STRICTLY improves the worse settlement
    #: outcome. It is tested, and the PMUS lane already consults it.
    #:
    #: NO MODULE NAMES FROM THE EXECUTION OR NETWORK LAYERS APPEAR IN
    #: THIS FILE, not even in a comment. An architectural test greps this
    #: source for them, and my first draft of this very comment named one
    #: and tripped it. The grep is crude and it is correct: a pure
    #: decision module should not be reaching into the layer that moves
    #: money. The ranker is pulled in by a LOCAL import inside
    #: `_rank_inventory`, so nothing new is imported at module scope.
    #:
    #: THIS LANE NEVER CALLED IT. It had its own narrower completion
    #: branch, which could only ever rest at the pair-cost ceiling or
    #: cross it -- it could not compare completing against reducing
    #: against holding on one scale. So this is a WIRING change, not a
    #: new strategy and not a second controller.
    #:
    #: IT REPLACES THE CHOICE, NOT THE GUARDS. Everything downstream
    #: still applies in full: the pair-cost target, `_completion_cross`'s
    #: all-in dominance test, the adapter's `completion_taker_fault`
    #: (depth walk with haircut, slippage charged against us, dearest-lot
    #: basis, >=1c locked, notional cap), the envelope's taker_allowed,
    #: post-only enforcement, and every exposure and loss ceiling. A rank
    #: that chooses TAKER_COMPLETE is a REQUEST, and any one of those
    #: refusing keeps the completion resting.
    #:
    #: False -- the default -- keeps the existing branch exactly.
    inventory_ranking_enabled: bool = False
    #: A RANKER THAT COULD PRICE NOTHING HAS EXPRESSED NO PREFERENCE.
    #:
    #: `_rank_inventory` passes `maker_fill_rate=None` on purpose -- the
    #: rate has never been measured at these prices and inventing one is
    #: forbidden -- so MAKER_COMPLETE comes back `available=False`. With
    #: taker completion disabled by policy every taker route is
    #: unavailable too. The ONLY available alternative is then HOLD, in
    #: every book, forever, and the controller reads that as "RANKED_HOLD:
    #: nothing on offer beats waiting" and withdraws the baseline
    #: completion. Enabling the ranker therefore REMOVED the only
    #: risk-reducing completion the controller had. Observed: with
    #: cpc=0.96 and a lopsided UP leg, ranker OFF places DOWN @0.36x0.50
    #: and ranker ON places nothing.
    #:
    #: True distinguishes the two HOLDs. A HOLD taken over at least one
    #: AVAILABLE non-HOLD alternative is a real comparison and still
    #: binds. A HOLD with no available alternative is a vacuous veto, and
    #: the baseline completion proceeds -- through every guard below it,
    #: unchanged: price range, pair target, min qty, already-working,
    #: exposure room, and the side and unmatched caps. It widens no
    #: bound, invents no fill rate and authorizes no taker.
    ranker_vacuous_hold_falls_back: bool = False
    #: THE EXPECTED VALUE WE WILL PAY TO REMOVE A WORST CASE.
    #:
    #: The ranking basis is expected WORST-case, not expected value --
    #: deliberately, because an EV ranking prefers carrying every
    #: unmatched leg (holding has EV zero; completing has EV minus the
    #: fee). So the chosen action can be RISK-REDUCING YET EV-NEGATIVE:
    #: it buys away the bad tail and pays for it.
    #:
    #: That is a real decision and it must be bought deliberately, so it
    #: is bounded in dollars. None -- the default -- means NO budget,
    #: which `rank` treats as refusing any alternative whose EV give-up
    #: cannot be measured, and the step report labels every chosen action
    #: POSITIVE_EV or RISK_REDUCING_EV_NEGATIVE either way.
    max_completion_ev_giveup_usd: Optional[Decimal] = None
    #: ------------------------------------------------------------------
    #: COMPLETION CROSSING. Whether a chosen completion may LIFT the
    #: opposite side's offer instead of resting at the pair-cost ceiling.
    #:
    #: WHY THIS EXISTS. The patient completion below rests at
    #: `straddle_completion_pair_cost - avg_cost_of_the_held_leg` -- a
    #: BUDGET CEILING, not a market price. Nothing ever priced it against
    #: the book, with two consequences:
    #:
    #:   1. When the offer is already BELOW the ceiling the completion
    #:      still rests AT the ceiling, forgoing a strictly cheaper fill
    #:      of the same side in the same size.
    #:   2. A resting BUY only fills when a seller comes DOWN to it, so
    #:      the patient completion fills precisely when that side is
    #:      being marked down -- adverse selection by construction. The
    #:      cheap fills it was supposed to catch are the ones it misses.
    #:
    #: This does not decide WHETHER to complete; the pair-cost gate below
    #: already did that. It decides only HOW the completion it already
    #: chose is executed: rest at the ceiling, or lift a cheaper offer.
    #: That is a DOMINANCE comparison on one side, one size, fees
    #: included -- not a directional bet -- so it needs no settlement
    #: probability and makes none up.
    #:
    #: ZERO/False IS THE DEFAULT AND REFUSES, and crossing needs BOTH
    #: this and the envelope's `taker_allowed`. Two independent switches,
    #: because crossing spends the spread and a single flag flipped by
    #: accident should not be able to do that.
    completion_cross_enabled: bool = False
    #: How far ABOVE the offer the crossing limit may sit, in ticks, so a
    #: book that moves between decision and arrival can still fill. The
    #: limit is capped by the ceiling regardless, so slippage can never
    #: widen the pair beyond its budget. Zero -- the default -- prices the
    #: limit exactly at the offer.
    completion_cross_slippage_ticks: int = 0
    #: The per-share saving, FEES INCLUDED, that crossing must beat the
    #: resting ceiling by before it is worth spending the spread. Zero
    #: permits any strict improvement; it is never permitted to cross for
    #: a worse or equal all-in price than resting.
    completion_cross_min_margin: Decimal = D("0")
    #: STRADDLE only: PATIENT COMPLETION of a lone leg. Zero disables it and
    #: is the default, so the mode's behaviour is byte-for-byte unchanged
    #: until an owner sets it.
    #:
    #: WHAT IT DOES. When one straddle leg has filled and the other has not,
    #: this rests an ADDITIONAL order on the unfilled side at
    #: `straddle_completion_pair_cost - avg_cost_of_the_held_leg`, so the
    #: pair costs exactly this much if it fills. The cheap straddle rung
    #: STAYS where it is -- this is a fallback, never a replacement.
    #:
    #: WHY NOT THE EXISTING HEDGE. `hedge_enabled` does the replacement
    #: version: it cancels anything on the hedge side that is not at the
    #: hedge price, which kills the cheap rung. Measured on 183 windows,
    #: that is WORSE than doing nothing -- -1.33 to -0.35 c/window against
    #: +2.86 today -- because forfeiting fourteen +84c pairs costs more
    #: than rescuing 169 lone legs. Keeping the rung and ADDING the
    #: completion is +4.79 c/window at a 0.96 pair cost, t +2.53.
    #:
    #: WHY IT CANNOT MAKE THINGS WORSE THAN NOT TRYING. A filled completion
    #: returns `1 - pair_cost` and a held losing leg returns `-entry`. At a
    #: 0.96 pair cost that is +4c against -8c. If it never fills we are
    #: exactly where we started, so the arm is weakly dominant in the fill
    #: rate: measured +3.23 c/window even if it fills only 20% of the times
    #: the price reaches it.
    #:
    #: THE COST IT DOES CARRY, stated because it is real: when the held leg
    #: would have WON, completing turns +0.92 into +0.04. Lone legs win
    #: about 3.6% of the time and that loss is already priced into the
    #: figures above.
    straddle_completion_pair_cost: Decimal = D("0")
    #: ACCUMULATE only: ticks below the favorite's bid an entry rests at.
    #: 0 joins the bid, which is where a maker buy has queue priority and
    #: the smallest gap to the price the band speaks about.
    accumulate_depth_ticks: int = 0
    #: ACCUMULATE only: how many of this strategy's own entry orders may
    #: rest on the favorite at once. The executor's `max_live_orders` is a
    #: separate, harder bound and the smaller of the two is what binds.
    #: FOUR, because four $0.10 clips are what a $0.45 ceiling holds, so the
    #: CAP is what stops accumulation rather than a count: measured on the
    #: 142-window tape, a limit of 2 refused 20 entries the cap would have
    #: allowed and a limit of 4 refuses 1. Raising it further changes
    #: nothing at this clip size.
    max_resting_entries: int = 4
    #: Whether an unmatched leg is completed with the opposite side at all.
    #: False is favorite accumulation: a directional inventory with no pair
    #: formation, so no HEDGE_UNDERDOG is ever emitted, nothing is reserved
    #: for a completion, and a resting order on the non-favorite side is
    #: cancelled rather than read as a hedge.
    hedge_enabled: bool = True


# --------------------------------------------------------------- readers

def _dec(x: Any) -> Optional[Decimal]:
    """A finite Decimal, or None. bool is not a number here."""
    if x is None or isinstance(x, bool):
        return None
    try:
        d = x if isinstance(x, Decimal) else D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _num(x: Any) -> Optional[float]:
    """A finite float, or None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (ValueError, TypeError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _other(side: str) -> str:
    return DOWN if side == UP else UP


def _floor_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _floor_qty(x: Decimal, params: Params) -> Decimal:
    """Down to a multiple of min_qty, then to 0.01. Never negative."""
    if x <= 0:
        return D("0.00")
    return _floor_to(_floor_to(x, params.min_qty), CENT).quantize(CENT)


def _read_inventory(inventory: Any) -> Optional[Dict[str, tuple]]:
    """{side: (qty, cost)}; a missing side is flat; anything odd is None."""
    if not isinstance(inventory, dict):
        return None
    out = {}
    for side in SIDES:
        leg = inventory.get(side)
        if leg is None:
            out[side] = (ZERO, ZERO)
            continue
        if not isinstance(leg, dict):
            return None
        qty, cost = _dec(leg.get("qty")), _dec(leg.get("cost"))
        if qty is None or cost is None or qty < 0 or cost < 0:
            return None
        if qty == 0 and cost > 0:           # cost with nothing held: not ours
            return None
        out[side] = (qty, cost)
    return out


def _read_resting(resting: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalised resting BUY orders, or None when any one is unreadable."""
    if not isinstance(resting, (list, tuple)):
        return None
    out = []
    for o in resting:
        if not isinstance(o, dict) or o.get("id") is None:
            return None
        price, qty, age = _dec(o.get("price")), _dec(o.get("qty")), _num(o.get("age_s"))
        if o.get("side") not in SIDES or price is None or qty is None or age is None:
            return None
        if not (ZERO < price < ONE) or qty <= 0 or age < 0:
            return None
        row = {"id": o["id"], "side": o["side"], "price": price,
               "qty": qty, "age_s": age}
        if o.get("reason") is not None:
            #: optional, and only ever read to tell a straddle leg from a
            #: completion when requoting. Absent means "unknown", and an
            #: unknown order is never requoted.
            row["reason"] = str(o["reason"])
        out.append(row)
    return out


def _aged_leg_cancels(resting: Any, params: "Params") -> List[Dict[str, Any]]:
    """A CANCEL for every STRADDLE_LEG at or past `straddle_leg_max_age_s`.

    Read from the RAW resting list, before it is validated, so it works on
    the early holds. An order whose age or reason cannot be read is left to
    the paths that handle unreadable orders; this never guesses.
    """
    try:
        limit = float(params.straddle_leg_max_age_s or 0)
    except (TypeError, ValueError):
        return []
    if not (limit > 0) or not isinstance(resting, (list, tuple)):
        return []
    out, seen = [], set()
    for o in resting:
        if not isinstance(o, dict) or o.get("id") is None \
                or o.get("reason") != "STRADDLE_LEG" or o["id"] in seen:
            continue
        age = _num(o.get("age_s"))
        if age is not None and age >= limit:
            seen.add(o["id"])
            out.append({"op": "CANCEL", "id": o["id"]})
    return out


def _cancel_all(resting: Any) -> List[Dict[str, Any]]:
    """A CANCEL for every resting order that has an id, readable or not."""
    out, seen = [], []
    if isinstance(resting, (list, tuple)):
        for o in resting:
            if isinstance(o, dict) and o.get("id") is not None and o["id"] not in seen:
                seen.append(o["id"])
                out.append({"op": "CANCEL", "id": o["id"]})
    return out


# ----------------------------------------------------------- probability

def p_up_from_spot(spot: Any, strike: Any, t_left_s: Any,
                   vol_per_sqrt_s: Any) -> Optional[Decimal]:
    """P(UP) = Phi((spot - strike) / (vol * sqrt(t_left))). None if unreadable.

    `vol_per_sqrt_s` is in index dollars per square-root second. With no
    time left the outcome is decided: 1 when spot >= strike, else 0.
    """
    s, k, t = _num(spot), _num(strike), _num(t_left_s)
    if s is None or k is None or t is None:
        return None
    if t <= 0:
        return ONE if s >= k else ZERO
    v = _num(vol_per_sqrt_s)
    if v is None or v <= 0:
        return None
    z = (s - k) / (v * math.sqrt(t))
    p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return D(repr(min(1.0, max(0.0, p)))).quantize(D("0.000001"))


# -------------------------------------------------------------- exposure

def _side_committed_usd(side: str, inv: Dict[str, tuple],
                        rest: List[Dict[str, Any]]) -> Decimal:
    """Filled cost plus resting notional ON ONE SIDE.

    Resting notional counts because an order we have placed is money we
    have already committed: a side cap that ignored resting orders could
    be breached by fills we had already asked for.
    """
    filled = inv[side][1]
    resting = sum((o["price"] * o["qty"] for o in rest
                   if o["side"] == side), ZERO)
    return filled + resting


def _prospective_unmatched_usd(side: str, price: Decimal, qty: Decimal,
                               inv: Dict[str, tuple],
                               rest: Optional[List[Dict[str, Any]]] = None
                               ) -> Decimal:
    """Cost of the unmatched portion if THIS clip filled and the opposite
    side did not move.

    Judged before the order is placed, and judged on the assumption that
    the clip fills and its hedge does not -- which is the case the bound
    exists for. The unmatched quantity is valued at the heavy side's
    average cost, because what an unmatched contract loses is what was
    paid for it, not a dollar.

    EVERY OUTSTANDING ORDER COUNTS, not just the one being placed.
    Reasoning from inventory plus this clip alone left a real hole: once
    repricing moves the quote, two same-side orders can rest at the same
    time, and each was admitted against an inventory that did not include
    the other. The reachable unmatched cost was therefore the ceiling
    PLUS another clip, which is why a 3-window campaign at $0.30 clips
    could not be proved inside a $1.00 ceiling and had to be cut to
    $0.15. Counting the resting book closes that: the bound becomes the
    ceiling itself.
    #
    #: It is DELIBERATELY PESSIMISTIC ON ONE SIDE ONLY -- our own
    #: same-side orders are assumed to fill, while the opposite side's are
    #: not. Crediting the opposite side's resting orders would net away
    #: exactly the exposure this bound exists to measure.
    #
    #: `rest` is optional so the pure arithmetic stays callable without a
    #: book; omitting it reproduces the old, looser answer.
    """
    other = _other(side)
    q_side, c_side = inv[side]
    q_side = q_side + qty
    c_side = c_side + price * qty
    if rest:
        for o in rest:
            if o["side"] == side:
                q_side = q_side + o["qty"]
                c_side = c_side + o["price"] * o["qty"]
    q_other, c_other = inv[other]
    if q_side > q_other:
        heavy_q, heavy_c, unmatched = q_side, c_side, q_side - q_other
    elif q_other > q_side:
        heavy_q, heavy_c, unmatched = q_other, c_other, q_other - q_side
    else:
        return ZERO
    if heavy_q <= 0:
        return ZERO
    return (heavy_c / heavy_q) * unmatched


#: the one value of `Params.straddle_ev_waiver` that means anything. Spelled
#: here and in `l5_behaviour_probe.CLASS`; a test holds them equal.
def one_sided_verdict(*, side: str, price: Decimal, qty: Decimal,
                      inv: Dict[str, tuple],
                      rest: Optional[List[Dict[str, Any]]],
                      partner_open: bool, p_up: Any, p_source: Any,
                      p_age_s: Any, params: "Params") -> Dict[str, Any]:
    """May this leg be placed, given what it does to the imbalance?

    See `Params.straddle_one_sided_control`. Pure: it reads inventory, the
    resting book and the probability it is handed, and decides nothing but
    this one leg.

    `extends` is how many of the leg's shares would sit beyond what the
    OTHER side already holds, counting everything already working on this
    side as if it fills -- the case a bound exists for. `completes` is the
    rest. A leg that only completes is always allowed here; every other
    limit still applies to it afterwards.

    The comparison a completion rests on is reported, not enforced: buying
    the missing side at x turns a position worth p_held into a certain 1,
    so it is worth p_side - x against holding, and `pair_cost` says what
    the pair then cost.
    """
    other = _other(side)
    working = sum((o["qty"] for o in (rest or ()) if o["side"] == side),
                  ZERO)
    after = inv[side][0] + working + qty
    extends = min(qty, max(ZERO, after - inv[other][0]))
    completes = qty - extends
    out: Dict[str, Any] = {"side": side, "price": str(price),
                           "qty": str(qty), "extends": str(extends),
                           "completes": str(completes),
                           "partner_open": bool(partner_open),
                           "p_side": None, "lone_edge": None,
                           "pair_cost": None, "value_vs_holding": None,
                           "allowed": True, "why": None}
    p = _dec(p_up)
    independent = (p is not None and ZERO <= p <= ONE
                   and p_source is not None
                   and str(p_source).upper() != "BOOK")
    age = _num(p_age_s)
    fresh = (independent and age is not None and 0 <= age
             <= float(params.straddle_one_sided_max_p_age_s))
    if independent:
        p_side = p if side == UP else ONE - p
        out["p_side"] = str(p_side)
        out["value_vs_holding"] = str(p_side - price)
    if completes > 0 and inv[other][0] > 0:
        out["pair_cost"] = str(inv[other][1] / inv[other][0] + price)
    if extends <= 0:
        out["why"] = "COMPLETES_PAIR"
        return out
    if partner_open:
        out["why"] = "PARTNER_QUOTABLE"
        return out
    if not fresh:
        out.update(allowed=False, why="ONE_SIDED_P_UNKNOWN")
        return out
    edge = p_side - price - params.adverse_selection_per_share
    out["lone_edge"] = str(edge)
    if edge >= params.min_edge_per_share:
        out["why"] = "LONE_EDGE_OK"
        return out
    out.update(allowed=False, why="ONE_SIDED_NO_EDGE")
    return out


EV_WAIVER_TOKEN = "BEHAVIOUR_PROBE_EV_UNPROVEN"


EV_BASIS_POSITION = "POSITION"


def position_order_economics(*, side: str, price: Decimal, qty: Decimal,
                             inv: Dict[str, tuple], fill_edge: Any,
                             fee_per_share: Any) -> Dict[str, Any]:
    """What one more filled order does to the WHOLE position.

    See `Params.straddle_ev_basis`. Pure arithmetic on what is held and
    what is proposed. `fill_edge` is the measured value of a filled share
    minus its price; None means unmeasured and is refused, never read as
    zero. The verdict has the shape `SEV.admits` returns.
    """
    cost = inv[UP][1] + inv[DOWN][1]
    before = {UP: inv[UP][0] - cost, DOWN: inv[DOWN][0] - cost}
    fee = _dec(fee_per_share)
    fee = ZERO if fee is None else fee
    spend = qty * price + qty * fee
    after = {s_: before[s_] - spend + (qty if s_ == side else ZERO)
             for s_ in SIDES}
    out = {"basis": EV_BASIS_POSITION, "side": side,
           "pnl_if_up_before": str(before[UP]),
           "pnl_if_down_before": str(before[DOWN]),
           "pnl_if_up_after": str(after[UP]),
           "pnl_if_down_after": str(after[DOWN]),
           "worst_before": str(min(before.values())),
           "worst_after": str(min(after.values())),
           "completes_shares": str(max(ZERO, min(
               qty, inv[_other(side)][0] - inv[side][0]))),
           "fill_edge_per_share": None, "expected_change_usd": None}
    edge = _dec(fill_edge) if fill_edge is not None else None
    if edge is None:
        out.update(ok=False, reason="FILL_EDGE_NOT_SET")
        return out
    ev = edge - fee
    out.update(fill_edge_per_share=str(edge), ev_per_share=str(ev),
               expected_change_usd=str(ev * qty))
    if ev <= ZERO:
        out.update(ok=False, reason="EV_NOT_POSITIVE")
    else:
        out.update(ok=True, reason=None)
    return out


def with_ev_waiver(params: "Params") -> "Params":
    """A copy of `params` carrying the behaviour-probe token. The ONLY way
    the token is ever attached, and it is called from exactly one place:
    `Btc5mExecutor.step`, after the injected probe function said yes."""
    return _dc_replace(params, straddle_ev_waiver=EV_WAIVER_TOKEN)


def _ev_waived(params: "Params", verdict: Dict[str, Any], bookrel: bool,
               requote: int, loss_cap: Decimal) -> bool:
    """May a leg the EV gate refused be admitted under the behaviour probe?

    ONLY when the refusal is "no rate has been set" and nothing else. Every
    structural condition is re-checked here rather than trusted from the
    caller: the exact token, book-relative quoting, the requote, and above
    all a window loss ceiling in force.
    """
    if params.straddle_ev_waiver != EV_WAIVER_TOKEN:
        return False
    if not str(verdict.get("reason") or "").endswith("_NOT_SET"):
        #: EV_NOT_POSITIVE, EV_BELOW_FLOOR, unreadable inputs: the gate was
        #: able to answer, or something is broken. Neither is waived.
        return False
    if not (bookrel and requote > 0 and loss_cap > ZERO):
        return False
    if (params.straddle_p_pair_natural is not None
            or params.straddle_p_complete is not None
            or params.straddle_p_lone_win is not None):
        return False
    return True


def worst_case_loss_usd(inv: Dict[str, tuple],
                        could_fill: List[tuple]) -> Decimal:
    """The most this window can lose at settlement: cost minus the smaller
    side's shares, maximised over every subset of `could_fill`.

    `inv` is {side: (qty, cost)}; `could_fill` is [(side, price, qty)].
    Exhaustive over subsets -- at most the live-order limit plus what one
    step queues, so a few hundred cases -- because the worst subset is not
    monotone: adding a fill on the LIGHT side can lower the loss.
    """
    best = None
    n = len(could_fill)
    for mask in range(1 << n):
        q = {UP: inv[UP][0], DOWN: inv[DOWN][0]}
        cost = inv[UP][1] + inv[DOWN][1]
        for i in range(n):
            if mask >> i & 1:
                s_, p_, q_ = could_fill[i]
                q[s_] += q_
                cost += p_ * q_
        loss = cost - min(q[UP], q[DOWN])
        if best is None or loss > best:
            best = loss
    return best if best is not None else ZERO


def _repriced_rung(r: Decimal, side: str, inv: Dict[str, tuple],
                   qty: Decimal, params: "Params",
                   hi: Optional[Decimal] = None) -> Optional[Decimal]:
    """The rung, shaded by how lopsided inventory already is.

    The heavy side bids LOWER and the light side HIGHER, by
    `straddle_reprice_ticks_per_clip` ticks for each whole clip of net
    imbalance. Returns None when the shift would leave the mode's
    (0, 0.50) bound -- a clamped-to-the-edge rung would quietly become a
    different trade from the one the owner configured, so it is refused
    instead.
    """
    per = int(params.straddle_reprice_ticks_per_clip or 0)
    if per <= 0 or qty <= 0:
        return r
    other = _other(side)
    skew = (inv[side][0] - inv[other][0]) / qty
    #: whole clips only: a partial fill should not nudge the quote
    clips = int(skew.to_integral_value(rounding=ROUND_FLOOR)) if skew >= 0 \
        else -int((-skew).to_integral_value(rounding=ROUND_FLOOR))
    if clips == 0:
        return r
    tick = _dec(params.tick) or D("0.01")
    #: THE UPPER BOUND IS THE CALLER'S. HALF is the fixed symmetric-pair
    #: rule; a book-relative leg is bounded by the COMBINED pair ceiling
    #: instead, which the caller checks, so it passes ONE here. Keeping
    #: HALF hardcoded would have made every repriced DOWN quote above
    #: 0.50 refuse, i.e. silently disabled repricing in exactly the mode
    #: that needs it.
    top = HALF if hi is None else hi
    out = r - (tick * per * clips)
    if not (ZERO < out < top):
        return None
    #: back onto the grid the venue will accept
    out = (out / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if not (ZERO < out < top):
        return None
    return out


def _exposure(inv: Dict[str, tuple], rest: List[Dict[str, Any]]) -> Dict[str, Any]:
    (up_qty, up_cost), (down_qty, down_cost) = inv[UP], inv[DOWN]
    filled = up_cost + down_cost
    resting_notional = sum((o["price"] * o["qty"] for o in rest), ZERO)
    matched = min(up_qty, down_qty)
    if up_qty > down_qty:
        unmatched_side, unmatched = UP, up_qty - down_qty
    elif down_qty > up_qty:
        unmatched_side, unmatched = DOWN, down_qty - up_qty
    else:
        unmatched_side, unmatched = None, ZERO
    pair = None
    if up_qty > 0 and down_qty > 0:
        pair = up_cost / up_qty + down_cost / down_qty
    # Filled inventory only: the losing side pays nothing, the winner $1 each.
    # Negative means the filled book is ahead on either outcome.
    worst_filled = max(filled - up_qty, filled - down_qty)
    return {
        "filled_cost_usd": filled,
        "resting_notional_usd": resting_notional,
        "total_exposure_usd": filled + resting_notional,
        "up_qty": up_qty,
        "down_qty": down_qty,
        "unmatched_side": unmatched_side,
        "unmatched_qty": unmatched,
        "matched_qty": matched,
        "matched_cost_per_pair": pair,
        "worst_case_settlement_loss_usd": worst_filled,
        # Every resting buy fills and then loses: all cost, no payoff. So a
        # resting hedge can only raise this, never lower either figure.
        "worst_case_if_resting_fill_usd": worst_filled + resting_notional,
    }


def exposure(inventory: Any, resting: Any) -> Optional[Dict[str, Any]]:
    """Dollar exposure and worst cases; None when either input is unreadable."""
    inv, rest = _read_inventory(inventory), _read_resting(resting)
    if inv is None or rest is None:
        return None
    return _exposure(inv, rest)


# ------------------------------------------------------ completion reserve

def completion_reserve(inv: Dict[str, tuple], rest: List[Dict[str, Any]],
                       params: "Params", add_side: Optional[str] = None,
                       add_price: Decimal = ZERO, add_qty: Decimal = ZERO
                       ) -> Dict[str, Any]:
    """Dollars the cap must still hold to complete every unmatched share.

    Worst case for the reserve: every resting BUY fills (and the optional
    new order too). The long side is the one with more shares after that;
    each uncovered share needs one share of the other side at a price no
    higher than `hedge_target_pair - avg long cost` -- what an ordinary
    completion may pay -- plus the fee allowance. A flipped-favorite hedge
    may pay up to `hard_pair_ceiling`; for that case the reserve can fall
    short by (ceiling - target) a share and the hedge is sized to the room
    left. Pricing the reserve at the ceiling instead was measured on the 42
    recorded windows (analysis/l5/btc5m_cap_policy_replay.py): it blocked
    entries that later completed cheaply and replayed worse. Shares resting on the short side are
    already in `total_exposure_usd` as resting notional, so they cover their
    part of the gap and are not reserved a second time.
    """
    qty = {s: inv[s][0] for s in SIDES}
    cost = {s: inv[s][1] for s in SIDES}
    for o in rest:
        qty[o["side"]] += o["qty"]
        cost[o["side"]] += o["qty"] * o["price"]
    if add_side in SIDES and add_qty > 0:
        qty[add_side] += add_qty
        cost[add_side] += add_qty * add_price
    if qty[UP] == qty[DOWN]:
        return {"usd": ZERO, "uncovered_qty": ZERO, "long_side": None,
                "price_bound": None}
    long_ = UP if qty[UP] > qty[DOWN] else DOWN
    gap = qty[long_] - qty[_other(long_)]
    avg = cost[long_] / qty[long_]
    bound = max(ZERO, params.hedge_target_pair - avg)
    per = bound + params.completion_fee_per_share
    return {"usd": gap * per, "uncovered_qty": gap, "long_side": long_,
            "price_bound": bound}


# --------------------------------------------------------------- decide

def _result(action, why, orders, evaluation, exp, favorite=None, p_up=None,
            p_source=None) -> Dict[str, Any]:
    return {"action": action, "why": why, "orders": orders,
            "evaluation": evaluation, "exposure": exp, "favorite": favorite,
            "p_up": p_up, "p_source": p_source}


def _place(side, price, qty, reason, *, post_only=True, intent="BUY", slot=0):
    """One order. `slot` is which of the concurrent entries this is: 0
    everywhere except an ACCUMULATE entry beyond the first, where the
    caller needs to tell two identical clips apart without reading either
    as a replay of the other."""
    return {"op": "PLACE", "side": side, "price": price, "qty": qty,
            "post_only": post_only, "intent": intent, "reason": reason,
            "slot": int(slot)}


def _book_rung(side, bid, ask, params, tick):
    """This side's book-relative maker price, or None if it cannot be made.

    Built from OUR OWN side's bid so it rests rather than crosses: UP's
    bid is the book's bid, DOWN's is one minus the book's offer. Joining
    means resting AT that bid, behind whatever is already queued there;
    `straddle_join_ticks` improves on it instead, buying queue priority
    at a worse price.

    Returns None when the result is off-grid, outside (0, 1), or would
    reach our own side's offer -- a maker order that would cross is not a
    maker order, and this mode never crosses.
    """
    own_bid = bid if side == UP else ONE - ask
    own_ask = ask if side == UP else ONE - bid
    n = int(params.straddle_join_ticks or 0)
    if n < 0:
        return None
    px = _floor_to(own_bid + n * tick, tick)
    if not (PRICE_MIN <= px <= PRICE_MAX) or px <= ZERO:
        return None
    if px >= own_ask:
        #: it would initiate rather than rest
        return None
    return px


def _rank_inventory(*, inv, rest, deficient, deficit, bid, ask, age,
                    ceiling, params, tick, room, t):
    """ONE comparison over every executable alternative, via the existing
    `l5_inventory_decision.rank`.

    Returns `(chosen, record)`. `chosen` is one of HOLD,
    MAKER_COMPLETE, TAKER_COMPLETE, TAKER_PARTIAL -- or HOLD whenever the
    module could not price the question, because "we could not tell" is
    not a reason to act.

    THE RECORD CLASSIFIES THE CHOICE. `rank` ranks on expected WORST-case
    after, so a chosen action can be risk-reducing and still EV-negative:
    it removes the bad tail and pays expected value for the privilege.
    `ev_class` says which it is -- POSITIVE_EV or
    RISK_REDUCING_EV_NEGATIVE -- so a loss-realizing risk reduction can
    never be reported as a profitable completion.
    """
    out = {"on": True, "chosen": None, "ev_class": None,
           "ev_giveup_usd": None, "reason": None, "considered": [],
           "ok": False}
    try:
        from src import l5_inventory_decision as ID
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return "HOLD", dict(out, reason="RANKER_UNAVAILABLE")

    def _px(side):
        q, c = inv[side]
        return (c / q) if q > 0 else ZERO

    #: the deficient side's own offer, one level, as a single rung. UP's
    #: offer is the book's; DOWN's is one minus the book's bid.
    own_ask = ask if deficient == UP else ONE - bid
    rungs = ((own_ask, deficit),) if (own_ask is not None
                                      and ZERO < own_ask < ONE) else None
    try:
        rec = ID.rank(
            up_qty=inv[UP][0], dn_qty=inv[DOWN][0],
            up_cost_px=_px(UP), dn_cost_px=_px(DOWN),
            deficient_side=deficient, deficit=deficit,
            min_qty=_dec(params.min_qty), tick=tick,
            taker_rungs=rungs, taker_own_px=ceiling,
            #: THE RESTING COMPLETION, priced at the same budget ceiling
            #: the patient path uses. Its fill rate is NOT supplied,
            #: because it has not been measured at these prices -- `rank`
            #: then reports MAKER_COMPLETE unavailable with
            #: MAKER_FILL_RATE_UNKNOWN rather than inventing one.
            maker_own_px=ceiling, maker_fill_rate=None,
            seconds_to_settlement=(_dec(params.window_s) - _dec(str(t))
                                   if t is not None else None),
            up_mid_wire=((bid + ask) / 2),
            cash_headroom=room,
            worst_case_ceiling=_dec(params.straddle_max_unmatched_usd),
            max_ev_giveup_usd=params.max_completion_ev_giveup_usd,
            book_age_s=age, max_book_age_s=_dec(str(params.stale_book_s)))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return "HOLD", dict(out, reason="RANKER_RAISED:%s"
                            % type(exc).__name__)

    chosen = str(rec.get("chosen") or "HOLD")
    #: `rank` reports its priced routes under `alternatives`. Reading
    #: `considered` alone returned [] on every call -- the wrapper was
    #: reporting an empty comparison while a real one had happened, and
    #: it nearly cost me the diagnosis above. Prefer the real key and
    #: keep the old one as a fallback.
    _alts = list(rec.get("alternatives") or rec.get("considered") or [])
    #: WAS THERE ANYTHING TO COMPARE AGAINST? Only an AVAILABLE non-HOLD
    #: route makes a HOLD a choice rather than the absence of one.
    _priceable = [a for a in _alts
                  if str(a.get("alternative") or "") != "HOLD"
                  and bool(a.get("available"))]
    out.update(ok=bool(rec.get("ok")), chosen=chosen,
               reason=rec.get("reason"),
               considered=_alts,
               priceable_alternatives=len(_priceable),
               priceable_names=[str(a.get("alternative")) for a in _priceable],
               #: the distinction the controller acts on
               hold_is_vacuous=bool(chosen == "HOLD" and not _priceable),
               book_refused=rec.get("book_refused"),
               why_hold=rec.get("why_hold"))
    for alt in (rec.get("alternatives") or []):
        if alt.get("alternative") == chosen:
            g = alt.get("ev_giveup_usd")
            out["ev_giveup_usd"] = g
            if g is None:
                out["ev_class"] = "EV_UNKNOWN"
            else:
                out["ev_class"] = ("RISK_REDUCING_EV_NEGATIVE"
                                   if _dec(g) is not None and _dec(g) > ZERO
                                   else "POSITIVE_EV")
            break
    return chosen, out


def _completion_cross(*, ceiling, ask, held_avg, pair_target, params, tick):
    """Should a chosen completion LIFT the offer instead of resting at the
    pair-cost ceiling?

    One side, one size, fees included: this is a DOMINANCE test, not a
    forecast. Crossing is permitted only when the all-in cost of buying
    the offer outright is STRICTLY cheaper than the ceiling the completion
    would otherwise rest at -- and only when the resulting pair, fees
    included, is still inside its budget. It needs no settlement
    probability and invents none.

    Returns a record whose `cross` is False unless every condition held,
    with `why` naming the condition that refused. `limit_dec` is the
    crossing limit as a Decimal for the caller to price the order at;
    every other number is a string, for the step report.

    THE FEE IS TAKEN AT ITS WORST POINT over the whole range the order
    could fill in, not at the limit. `theta*p*(1-p)` peaks at p=0.50, so
    for a completion priced well above the half the fee RISES as the fill
    price falls, and charging it at the limit alone would understate the
    cost of the cheaper fill this path exists to get.
    """
    out = {"cross": False, "why": "CROSS_DISABLED", "ask": None,
           "limit": None, "limit_dec": None, "fee_per_share": None,
           "saving_per_share": None}
    if not params.completion_cross_enabled:
        return out
    #: BOTH SWITCHES, CHECKED HERE. `done()` refuses any non-post-only
    #: order without `taker_allowed` as a last line of defence, but a
    #: refusal there discards the whole decision; refusing here keeps the
    #: resting completion and says why it was kept.
    if not params.taker_allowed:
        out["why"] = "TAKER_NOT_ALLOWED"
        return out
    a = _dec(ask)
    if a is None or a <= ZERO or a >= ONE:
        out["why"] = "NO_OFFER"
        return out
    out["ask"] = str(a)
    ticks = int(params.completion_cross_slippage_ticks or 0)
    if ticks < 0:
        out["why"] = "SLIPPAGE_NEGATIVE"
        return out
    #: THE CEILING CAPS THE LIMIT, so slippage can never widen the pair
    #: past its budget however many ticks were asked for.
    limit = _floor_to(min(ceiling, a + ticks * tick), tick)
    out["limit"] = str(limit)
    if limit < a:
        #: The offer is dearer than the whole completion budget. Resting
        #: at the ceiling is the only thing left, which is what happens.
        out["why"] = "OFFER_ABOVE_CEILING"
        return out
    if not (PRICE_MIN <= limit <= PRICE_MAX) or limit <= ZERO:
        out["why"] = "LIMIT_OUT_OF_RANGE"
        return out
    pts = [a, limit]
    if a <= HALF <= limit:
        pts.append(HALF)
    fee = max(params.taker_fee_coef * q * (ONE - q) for q in pts)
    out["fee_per_share"] = str(fee)
    allin = limit + fee
    saving = ceiling - allin
    out["saving_per_share"] = str(saving)
    margin = _dec(params.completion_cross_min_margin) or ZERO
    if saving <= ZERO or saving < margin:
        #: Paying the spread buys nothing here: resting at the ceiling is
        #: at least as cheap all-in, so the patient order keeps its place.
        out["why"] = "NO_MARGIN_OVER_RESTING"
        return out
    if held_avg + allin > pair_target:
        #: The pair-cost gate above priced the RESTING completion. The
        #: crossing one is re-checked on its own all-in cost so a fee can
        #: never carry the pair past the target it was admitted under.
        out["why"] = "PAIR_ABOVE_TARGET_ALL_IN"
        return out
    out.update(cross=True, why=None, limit_dec=limit)
    return out


def decide(*, book, t_in_window_s, inventory, resting, params=Params(),
           p_up=None, p_source=None, p_age_s=None,
           loss_room_usd=None) -> Dict[str, Any]:
    """One decision. See the module docstring; the steps are numbered below."""
    # 0. Off unless explicitly switched on.
    if not enabled():
        return _result(DISABLED, "CONTROLLER_OFF", [], {}, exposure(inventory, resting))

    exp = exposure(inventory, resting)
    cancel_all = _cancel_all(resting)

    #: THE LEG AGE LIMIT HOLDS THROUGH A NON-CANCELLING HOLD. Two holds
    #: below deliberately leave orders resting (the venue's grid could not
    #: be verified this step). Left at that, a leg could sit through such a
    #: hold past the age the limit exists to enforce. Empty unless
    #: `straddle_leg_max_age_s` is set, so nothing changes by default.
    _aged = _aged_leg_cancels(resting, params)

    def hold(why, *, cancel=False, evaluation=None):
        return _result(HOLD_OR_SKIP, why,
                       list(cancel_all) if cancel else list(_aged),
                       evaluation or {}, exp)

    tick, min_qty = _dec(params.tick), _dec(params.min_qty)
    if tick is None or tick <= 0 or min_qty is None or min_qty <= 0:
        return hold("PARAMS_UNREADABLE", cancel=True)
    #: THE VENUE'S OWN GRID, when we are required to have verified it.
    #:
    #: `params.tick` and `params.min_qty` are DEFAULTS and nothing checked
    #: them against the venue, while `l5_btc5m_runner.book()` was already
    #: fetching the market object that carries `orderPriceMinTickSize` and
    #: `minimumTradeQty` and discarding both. Verified live 2026-11-19: tick
    #: 0.01, minimumTradeQty 0.01, feeCoefficient 0.095 -- so the defaults
    #: are currently right, which is why a silent change to either would
    #: otherwise be invisible.
    #:
    #: OFF unless the environment says REQUIRE, so an unset variable leaves
    #: every existing decision byte-identical. When required, an unverified
    #: or implausible constraint HOLDS rather than falling back to the
    #: default, because a default standing in for the venue is the defect.
    _vspec = None
    if VC.required():
        _vspec = VC.from_book(book)
        if not _vspec["ok"]:
            return hold(_vspec["reason"], cancel=False)
        if _vspec["tick"] != tick or _vspec["min_qty"] != min_qty:
            return hold("VENUE_CONSTRAINTS_DISAGREE:tick=%s/%s,minq=%s/%s"
                        % (_vspec["tick"], tick,
                           _vspec["min_qty"], min_qty), cancel=False)
    if params.reserve_mode not in ("FIXED", "COMPLETION", "NONE"):
        return hold("PARAMS_UNREADABLE", cancel=True)
    if params.entry_mode not in ENTRY_MODES:
        return hold("PARAMS_UNREADABLE", cancel=True)
    straddle = params.entry_mode == STRADDLE
    _sx = _dec(params.straddle_price)
    if straddle and (_sx is None or not (ZERO < _sx < HALF)):
        # at or above 0.50 the pair costs >= 1.00 and cannot profit, so it
        # is a contradiction rather than an aggressive setting.
        return hold("PARAMS_UNREADABLE", cancel=True)
    _cpc = _dec(params.straddle_completion_pair_cost)
    if straddle and _cpc is not None and _cpc != ZERO and not (HALF < _cpc <= ONE):
        # a completion is only meaningful between 0.50 and 1.00: at or below
        # 0.50 the cheap rung already covers it, above 1.00 the pair is a
        # guaranteed loss. Out of range is a contradiction, not a setting.
        return hold("PARAMS_UNREADABLE", cancel=True)
    #: BOOK-RELATIVE CONFIGURATION, validated before anything is priced.
    #: Every contradiction refuses the mode rather than being clamped.
    _bookrel = bool(straddle and params.straddle_book_relative)
    _pceil = _dec(params.straddle_pair_ceiling)
    if _bookrel:
        if _pceil is None or not (ZERO < _pceil < ONE):
            #: at or above 1.00 a matched pair cannot profit before
            #: costs; zero means the mode was switched on without a
            #: budget. Both are contradictions, not settings.
            return hold("PARAMS_UNREADABLE", cancel=True)
        if int(params.straddle_join_ticks or 0) < 0:
            return hold("PARAMS_UNREADABLE", cancel=True)
    _rungs = ()
    if straddle:
        raw = params.straddle_rungs or (params.straddle_price,)
        try:
            _rungs = tuple(sorted({_dec(r) for r in raw}))
        except TypeError:
            return hold("PARAMS_UNREADABLE", cancel=True)
        #: THE PER-LEG 0.50 BOUND IS A FIXED-MODE RULE ONLY. It is the
        #: symmetric-pair bound and it is correct there: with both legs
        #: at the same price, 0.50 each is a guaranteed loss. Applied to
        #: independently priced legs it forbids valid pairs, so
        #: book-relative mode is bounded on the COMBINED cost instead,
        #: checked against `_pceil` once both prices are known.
        if not _rungs or any(r is None for r in _rungs):
            return hold("PARAMS_UNREADABLE", cancel=True)
        if not _bookrel and any(not (ZERO < r < HALF) for r in _rungs):
            return hold("PARAMS_UNREADABLE", cancel=True)
    # "NONE" says nothing is kept back for a completing order, which is only
    # true when no completing order is ever placed. Asking for one without
    # the other is a contradiction, not a default.
    if (params.reserve_mode == "NONE") != (not params.hedge_enabled):
        return hold("PARAMS_UNREADABLE", cancel=True)
    accumulate = params.entry_mode == ACCUMULATE
    if accumulate and int(params.max_resting_entries) < 1:
        return hold("PARAMS_UNREADABLE", cancel=True)
    _cap, _fee = _dec(params.max_exposure_usd), _dec(params.completion_fee_per_share)
    if _cap is None or _cap < 0 or _fee is None or _fee < 0:
        return hold("PARAMS_UNREADABLE", cancel=True)

    # 1. The book. Without a fresh two-sided YES quote nothing may rest.
    if not isinstance(book, dict):
        return hold("BOOK_UNUSABLE", cancel=True)
    bid, ask, age = _dec(book.get("bid")), _dec(book.get("ask")), _num(book.get("age_s"))
    if bid is None or ask is None or age is None or age < 0:
        return hold("BOOK_UNUSABLE", cancel=True)
    if age > params.stale_book_s:
        return hold("BOOK_STALE", cancel=True)
    if bid <= 0 or ask <= bid or ask >= 1:
        return hold("BOOK_UNUSABLE", cancel=True)

    t = _num(t_in_window_s)
    if t is None or t < 0:
        return hold("TIME_UNREADABLE", cancel=True)
    if t >= params.window_s:
        return hold("WINDOW_OVER", cancel=True)

    inv, rest = _read_inventory(inventory), _read_resting(resting)
    if inv is None:
        return hold("INVENTORY_UNREADABLE", cancel=True)
    if rest is None:
        return hold("RESTING_UNREADABLE", cancel=True)

    # 1b. STRADDLE. Handled in full here, before any favorite, band or
    #     edge machinery is touched, so the two older modes cannot be
    #     perturbed by it: nothing below this block runs in this mode.
    #
    #     The rule is deliberately dumb. Leave ONE resting buy per side at
    #     `straddle_price` and never move it. A fixed limit cannot drift,
    #     so if both fill the pair cost is exactly 2x that price -- no
    #     adverse selection on the PRICE at all, only on which legs fill.
    #     A side that already holds inventory is finished: the straddle
    #     wants one fill a side, not accumulation.
    if straddle:
        exp_s = _exposure(inv, rest)
        room = _cap - exp_s["total_exposure_usd"]
        #: SIZED SO ONE SIDE'S WHOLE LADDER COSTS `clip_usd`, which keeps
        #: the single-rung case identical (sum == the one price) and makes
        #: the worst case exactly 2 x clip whatever the ladder's width.
        #: ---------------- BOOK-RELATIVE PRICING, ONCE PER STEP, AND
        #: BEFORE SIZING, because the clip is sized from the prices we
        #: will actually quote. Sizing from the fixed ladder while
        #: quoting from the book produced a 25-share clip at 0.30 -- a
        #: $7.50 order from a $2.00 clip -- which the side cap then
        #: refused, so the mode looked configured and quoted nothing.
        #:
        #: Both legs are priced before either is considered, because the
        #: bound that matters is on the two of them TOGETHER: one leg at
        #: a time cannot see a pair that costs 1.02.
        #: WHY EACH SIDE DID NOT GET AN ORDER, kept per side.
        #:
        #: `why_s` is one string and the loop overwrites it, so the LAST
        #: side evaluated wins. Live window 1930z: the UP leg -- the
        #: risk-reducing one that would have completed the pair -- was
        #: refused COMPLETE_BUDGET_EXHAUSTED, while the reported why was
        #: the DOWN side's UNMATCHED_CEILING. The decisive refusal sat
        #: only in `refused`, and diagnosing it took far longer than it
        #: should have.
        #:
        #: BOUND HERE, not inside the loop's branch. The refusal branches
        #: (CLIP_TOO_SMALL, ENTRY_WINDOW_CLOSED, REPEAT_WITHOUT_BOUNDS,
        #: FILL_MODEL_UNMEASURED) skip that branch and still build the
        #: step report, so a definition in there is a NameError on every
        #: one of those paths -- the identical mistake I made with `_bk`
        #: a few lines below, in this same function.
        _side_why: Dict[str, Any] = {s_: None for s_ in SIDES}
        _one_sided: Dict[str, Any] = {s_: None for s_ in SIDES}
        _position: Dict[str, Any] = {s_: None for s_ in SIDES}
        _pacing: Dict[str, Any] = {"on": False}
        _pair_block: Dict[str, bool] = {s_: False for s_ in SIDES}
        _pair_plan: Dict[str, Any] = {"action": None}
        _bk, _bk_why = {}, None
        if _bookrel:
            if not params.straddle_fill_model_measured:
                #: THE HONEST REFUSAL. The gate's three rates were
                #: measured at 0.08 rungs. Fed a 0.30 rung they do not
                #: refuse -- they return a confident WRONG number, which
                #: is worse than refusing. So this stops here until the
                #: fill model for these prices is measured and that is
                #: stated explicitly. The EV gate still runs below.
                _bk_why = "FILL_MODEL_UNMEASURED"
            else:
                for s_ in SIDES:
                    _bk[s_] = _book_rung(s_, bid, ask, params, tick)
                _pc_cap = _dec(params.straddle_max_completed_pair_cost) \
                    or ZERO
                if _pc_cap > ZERO and inv[UP][0] != inv[DOWN][0] \
                        and all(v is not None for v in _bk.values()):
                    _hv = UP if inv[UP][0] > inv[DOWN][0] else DOWN
                    _lt = _other(_hv)
                    _havg = inv[_hv][1] / inv[_hv][0]
                    _cpx = _floor_to(_pc_cap - _havg, tick)
                    _pair_plan.update(
                        held_side=_hv, completing_side=_lt,
                        held_avg=str(_havg), book_bid=str(_bk[_lt]),
                        cap=str(_pc_cap), cap_price=str(_cpx),
                        action="AT_BOOK")
                    if _cpx < PRICE_MIN:
                        _pair_block[_lt] = True
                        _pair_plan["action"] = "NOT_QUOTED"
                    elif _cpx < _bk[_lt]:
                        _bk[_lt] = _cpx
                        _pair_plan["action"] = "RESTS_AT_CAP"
                if any(v is None for v in _bk.values()):
                    _bk_why = "QUOTE_NOT_MAKEABLE"
                elif sum(_bk.values()) > _pceil:
                    #: NECESSARY CONDITION ON THE PAIR. Under 1.00 is
                    #: required for matched-pair economics before costs;
                    #: it promises nothing about fills, which is what the
                    #: EV gate and the fill model are for.
                    _bk_why = "PAIR_ABOVE_CEILING"
        #: SIZED ON THE DEARER LEG so neither side's notional exceeds the
        #: clip, and ONE quantity for both so a filled pair matches
        #: exactly 1:1 -- unequal legs would manufacture residual on
        #: purpose.
        if _bookrel and not _bk_why:
            ladder = max(_bk.values())
        else:
            ladder = sum(_rungs, ZERO)
        qty = _floor_qty(_dec(params.clip_usd) / ladder, params)
        #: ---------------- RESERVE WHAT COMPLETING THIS WOULD COST.
        #:
        #: MEASURED, window 1500z, 2026-11-17: a 0.08 entry filled twice
        #: for 3.74 shares and completing it needed about $2.66 of the
        #: opposite side against a $1.60 envelope. Completion cost about
        #: NINE TIMES the entry, the reserve did not exist, and the
        #: completion was unfundable by construction -- it never fired.
        #:
        #: Buying q at p now and completing at up to (ceiling - p) later
        #: costs at most `ceiling * q` in total, whatever p is. So the
        #: room required follows the ACTUAL entry price without having to
        #: guess the completion price.
        #:
        #: IT SHRINKS, IT DOES NOT BLOCK. Refusing outright whenever the
        #: full clip cannot be completed would stop all accumulation on a
        #: small envelope, which is the opposite of the intent. Only a
        #: size below the venue minimum refuses, and it says so.
        #:
        #: IT GUARANTEES NOTHING. Reserved capital is capital, not a
        #: fill: the completion still has to exist on the book and still
        #: has to be economic when it is considered.
        _reserve = {"applied": False, "ceiling": None, "clip_qty": str(qty),
                    "qty_after": None, "why": None}
        if _bookrel and params.straddle_completion_reserve and _pceil > ZERO:
            _reserve.update(applied=True, ceiling=str(_pceil))
            _afford = _floor_qty(room / _pceil, params)
            if _afford < qty:
                qty = _afford
            _reserve["qty_after"] = str(qty)
            if qty < min_qty:
                _reserve["why"] = "COMPLETION_UNFUNDABLE"
        #: ---------------- HEAVY AND LOSING: which side, if any.
        _hu = {s_: False for s_ in SIDES}
        if params.straddle_stop_adding_heavy_underdog and _bookrel \
                and bid is not None and ask is not None:
            _sb = {UP: bid, DOWN: ONE - ask}
            _hm = _dec(params.straddle_heavy_underdog_margin) or ZERO
            for s_ in SIDES:
                _o_ = _other(s_)
                _hu[s_] = bool(inv[s_][0] > inv[_o_][0]
                               and _sb[s_] < _sb[_o_] - _hm)
        want = []
        ev_rungs = []
        #: ---------------- REQUOTE, decided before anything is placed.
        _rq = int(params.straddle_requote_ticks or 0) if _bookrel else 0
        try:
            _max_age = float(params.straddle_leg_max_age_s or 0)
        except (TypeError, ValueError):
            _max_age = 0.0
        _rq_cancel = {s: [] for s in SIDES}
        _rq_kept = {s: False for s in SIDES}
        #: the price rule needs a requote setting and a state in which a
        #: target exists or is known not to; the AGE rule needs neither and
        #: applies in every state, so no hold can keep a leg past its limit.
        _rq_live = _rq > 0 and _bk_why in (None, "PAIR_ABOVE_CEILING",
                                            "QUOTE_NOT_MAKEABLE")
        if _rq_live or _max_age > 0:
            for s_ in SIDES:
                _tgt = None
                if _rq_live and _bk_why is None:
                    _tgt = _repriced_rung(_bk[s_], s_, inv, qty, params,
                                          hi=ONE)
                for o in rest:
                    if o["side"] != s_ \
                            or o.get("reason") != "STRADDLE_LEG":
                        continue
                    _old = _max_age > 0 and o["age_s"] >= _max_age
                    _moved = _rq_live and (
                        _tgt is None or o["price"] > _tgt
                        or _tgt - o["price"] >= _rq * tick)
                    if _old or _moved:
                        _rq_cancel[s_].append(o)
                    elif _rq_live:
                        _rq_kept[s_] = True
        for s_ in SIDES:
            if not _hu[s_]:
                continue
            _have = {o["id"] for o in _rq_cancel[s_]}
            for o in rest:
                if o["side"] == s_ and o.get("reason") == "STRADDLE_LEG" \
                        and o["id"] not in _have:
                    _rq_cancel[s_].append(o)
        for s_ in SIDES:
            for o in _rq_cancel[s_]:
                want.append({"op": "CANCEL", "id": o["id"]})
        why_s = "STRADDLE_RESTING"
        if _reserve.get("why"):
            why_s = _reserve["why"]
        elif qty <= 0:
            why_s = "CLIP_TOO_SMALL"
        elif t >= params.no_new_entry_after_s:
            why_s = "ENTRY_WINDOW_CLOSED"
        elif (params.straddle_repeat_enabled
              and not ((_dec(params.straddle_max_side_usd) or ZERO) > ZERO
                       and (_dec(params.straddle_max_unmatched_usd)
                            or ZERO) > ZERO)):
            #: repeated entry without an unmatched ceiling is a
            #: directional bet wearing a straddle's clothes. Refuse the
            #: mode rather than inventing a bound.
            why_s = "REPEAT_WITHOUT_BOUNDS"
        else:
            #: RUNGS OUTER, SIDES INNER -- so that any truncation falls
            #: EVENLY on both sides. Draining one side first (the original
            #: order) meant an external cap left the ladder lopsided: with
            #: 12 rungs wanted and 10 orders allowed, UP took all 6 and
            #: DOWN got 4, which is a directional bet wearing a straddle's
            #: clothes. Observed live 2026-11-12 23:12Z as MAX_LIVE_ORDERS
            #: refusing ten placements after UP had been filled first.
            #: A half-built ladder is the one shape this strategy must
            #: never hold.
            live = {s: {o["price"] for o in rest if o["side"] == s}
                    for s in SIDES}
            _repeat = bool(params.straddle_repeat_enabled)
            if _bk_why is not None:
                why_s = _bk_why
                _rungs = ()
            _side_cap = _dec(params.straddle_max_side_usd) or ZERO
            _side_cap_full = _side_cap
            if params.straddle_pace_side_cap and _side_cap > ZERO:
                try:
                    _hz = float(params.no_new_entry_after_s)
                    _fr = min(1.0, max(float(t), 0.0) / _hz) if _hz > 0 \
                        else 1.0
                except (TypeError, ValueError):
                    _fr = 0.0        # unreadable clock: the smallest cap
                _paced = _side_cap * D(str(round(_fr, 6)))
                _floor = _dec(params.clip_usd) or ZERO
                _side_cap = min(_side_cap, max(_paced, _floor))
            _pacing.update(on=bool(params.straddle_pace_side_cap),
                           side_cap_now=str(_side_cap),
                           side_cap_full=str(_side_cap_full))
            _wl_cap = _dec(params.straddle_max_window_loss_usd) or ZERO
            _unm_cap = _dec(params.straddle_max_unmatched_usd) or ZERO
            _committed = {s: _side_committed_usd(s, inv, rest)
                          for s in SIDES}
            #: ---------------- BOOK-RELATIVE PRICING, ONCE PER STEP.
            #:
            #: Both legs are priced from the live book BEFORE either is
            #: considered, because the bound that matters is on the two
            #: of them TOGETHER. Checking one leg at a time cannot see a
            #: pair that costs 1.02.
            #: LIGHT SIDE FIRST. The executor's live-order limit refuses
            #: whatever is asked for LAST, and repricing never cancels the
            #: light side's older rungs, so they hold the slots. In fixed
            #: UP-then-DOWN order an UP-heavy book therefore had its
            #: balancing DOWN order refused step after step while the UP
            #: order that deepens the imbalance was placed (UP 0.14 against
            #: DOWN 0 in the test that found this); a DOWN-heavy book
            #: stopped correctly, by accident of the order. Equal inventory
            #: keeps the original order, so a balanced book is unchanged.
            _order = tuple(sorted(SIDES, key=lambda s: inv[s][0]))
            for i, r in enumerate(_rungs):
                for side in _order:
                    if _hu[side]:
                        why_s = "HEAVY_SIDE_IS_UNDERDOG"
                        _side_why[side] = "HEAVY_SIDE_IS_UNDERDOG"
                        continue
                    if _rq_cancel[side] or _rq_kept[side]:
                        #: a cancel is in flight, or a quote within
                        #: tolerance already works this side
                        if _rq_cancel[side]:
                            _side_why[side] = "REQUOTE_CANCEL_PENDING"
                        continue
                    #: REPRICING. Shades the rung by existing imbalance;
                    #: returns the rung unchanged when disabled. In
                    #: book-relative mode the BASE is this side's live
                    #: quote rather than a constant, and the leg is
                    #: bounded by the pair ceiling rather than by 0.50.
                    if _bookrel:
                        r_side = _repriced_rung(_bk[side], side, inv, qty,
                                                params, hi=ONE)
                    else:
                        r_side = _repriced_rung(r, side, inv, qty, params)
                    if r_side is None:
                        why_s = "REPRICE_OUT_OF_BAND"
                        _side_why[side] = "REPRICE_OUT_OF_BAND"
                        continue
                    _eb_lo = _dec(params.straddle_entry_bid_min) or ZERO
                    _eb_hi = _dec(params.straddle_entry_bid_max) or ZERO
                    if _eb_lo != ZERO or _eb_hi != ZERO:
                        if not (ZERO < _eb_lo <= _eb_hi < ONE):
                            why_s = "ENTRY_BAND_UNREADABLE"
                            _side_why[side] = "ENTRY_BAND_UNREADABLE"
                            continue
                        if not (_eb_lo <= r_side <= _eb_hi):
                            why_s = "ENTRY_BAND"
                            _side_why[side] = "ENTRY_BAND"
                            continue
                    if not _repeat:
                        if inv[side][0] > 0:
                            continue              # this side is done
                    else:
                        #: BOUNDED REPETITION. Two dollar bounds replace
                        #: the lockout's two jobs: how much one side may
                        #: hold, and how lopsided the book may get.
                        if _committed[side] + r_side * qty > _side_cap:
                            _sc = ("SIDE_CAP_PACED"
                                   if _committed[side] + r_side * qty
                                   <= _side_cap_full else "SIDE_CAP_REACHED")
                            why_s = _sc
                            _side_why[side] = _sc
                            continue
                        if _prospective_unmatched_usd(
                                side, r_side, qty, inv, rest) > _unm_cap:
                            why_s = "UNMATCHED_CEILING"
                            _side_why[side] = "UNMATCHED_CEILING"
                            continue
                    if _pair_block[side]:
                        why_s = "PAIR_COST_ABOVE_CAP"
                        _side_why[side] = "PAIR_COST_ABOVE_CAP"
                        continue
                    if params.straddle_one_sided_control:
                        #: IS THE PARTNER STILL BUYABLE? A partner quote
                        #: already working counts. Otherwise the partner
                        #: must be priceable now and inside the same side
                        #: and unmatched bounds its own leg would face.
                        _o = _other(side)
                        _p_open = any(x["side"] == _o for x in rest)
                        if not _p_open and _bk_why is None \
                                and not _pair_block[_o]:
                            _r_o = (_repriced_rung(_bk[_o], _o, inv, qty,
                                                   params, hi=ONE)
                                    if _bookrel else
                                    _repriced_rung(r, _o, inv, qty, params))
                            if _r_o is None:
                                _p_open = False
                            elif not _repeat:
                                _p_open = inv[_o][0] <= 0
                            else:
                                _p_open = (
                                    _committed[_o] + _r_o * qty <= _side_cap
                                    and _prospective_unmatched_usd(
                                        _o, _r_o, qty, inv, rest)
                                    <= _unm_cap)
                        _osv = one_sided_verdict(
                            side=side, price=r_side, qty=qty, inv=inv,
                            rest=rest, partner_open=_p_open, p_up=p_up,
                            p_source=p_source, p_age_s=p_age_s,
                            params=params)
                        _one_sided[side] = _osv
                        if not _osv["allowed"]:
                            why_s = _osv["why"]
                            _side_why[side] = _osv["why"]
                            continue
                    if r_side in live[side]:
                        continue                  # that rung already rests
                    if _wl_cap > ZERO:
                        _could = ([(o["side"], o["price"], o["qty"])
                                   for o in rest]
                                  + [(o["side"], o["price"], o["qty"])
                                     for o in want if o.get("op") == "PLACE"]
                                  + [(side, r_side, qty)])
                        #: bounded so a pathological resting book cannot
                        #: turn this into 2**n work; over the bound it
                        #: refuses rather than guesses.
                        if len(_could) > 12 or worst_case_loss_usd(
                                inv, _could) > _wl_cap:
                            why_s = "WINDOW_LOSS_CEILING"
                            _side_why[side] = "WINDOW_LOSS_CEILING"
                            continue
                    if r_side * qty > room:
                        why_s = "NO_EXPOSURE_ROOM"
                        _side_why[side] = "NO_EXPOSURE_ROOM"
                        continue
                    #: IS THIS RUNG WORTH BUYING? Priced before any room
                    #: is committed. "The cap allows it" is not a reason
                    #: to buy something -- the reflex that opened a rung
                    #: whenever the clock and the cap permitted produced
                    #: 15 consecutive lone legs live, each settling
                    #: -0.16. A nonpositive OR UNKNOWN value refuses:
                    #: "we could not tell" is not a reason either.
                    #: the opposite side's rung as it would actually be
                    #: priced, so an asymmetric pair is not valued as a
                    #: symmetric one. NB `_other` is a module-level
                    #: function here; shadowing it with a local broke 44
                    #: tests on the first attempt.
                    #: THE OPPOSITE LEG AS IT WOULD ACTUALLY BE
                    #: PRICED. In book-relative mode that is the other
                    #: side's BOOK quote, not the fixed ladder's rung.
                    #:
                    #: Taking it from the fixed rung priced the pair at
                    #: 0.30 + 0.08 = 0.38 while the real quoted pair was
                    #: 0.99, which inflated EV from +0.0007 to +0.0434 a
                    #: share -- about sixty times -- and admitted rungs
                    #: on a pair that did not exist. Exactly the
                    #: "confident wrong number" this gate is supposed to
                    #: prevent, arriving through the gate itself.
                    _pos_basis = (params.straddle_ev_basis
                                  == EV_BASIS_POSITION)
                    if _bookrel:
                        _r_other = _repriced_rung(_bk[_other(side)],
                                                  _other(side), inv, qty,
                                                  params, hi=ONE)
                    else:
                        _r_other = _repriced_rung(r, _other(side), inv, qty,
                                                  params)
                    _ev = SEV.entry_ev(
                        price=r_side,
                        other_price=(r_side if _r_other is None
                                     else _r_other),
                        pair_target=params.straddle_completion_pair_cost,
                        p_pair_natural=params.straddle_p_pair_natural,
                        #: ZERO WHEN COMPLETION IS OFF. A completion
                        #: probability beside a disabled completion is a
                        #: contradiction, and crediting one would price
                        #: a payoff this configuration cannot collect.
                        #: With it off the rung is worth its natural
                        #: pair plus its lone leg, and nothing else.
                        p_complete=(params.straddle_p_complete
                                    if (_cpc is not None and _cpc > ZERO)
                                    else ZERO),
                        #: THE LONE LEG'S WIN RATE IS THE PRICE, in
                        #: book-relative mode.
                        #:
                        #: WHY THE CONSTANT WAS WRONG. 0.0981 was
                        #: measured at 0.08 rungs. A leg bought at 0.69
                        #: does not win 9.8% of the time -- it wins about
                        #: 69%, because in a prediction market THE PRICE
                        #: IS THE IMPLIED PROBABILITY. Feeding the 0.08
                        #: constant to a 0.69 rung priced it at
                        #: -0.534367 a share and the gate refused every
                        #: book-relative quote, which is why the
                        #: authorized 1910z campaign would have submitted
                        #: nothing. That was a wrong input, not a missing
                        #: permission.
                        #:
                        #: NOTHING IS INVENTED: this is the venue's own
                        #: price. And it is the CONSERVATIVE reading of
                        #: it -- `lone_pay` is
                        #: pw*(1-px) - (1-pw)*px, which at pw == px is
                        #: exactly ZERO. So the lone leg contributes
                        #: nothing either way: we neither credit
                        #: ourselves the half-spread we actually buy at
                        #: the bid, nor charge ourselves a loss the
                        #: market does not imply. The decision is then
                        #: carried entirely by the PAIR economics, which
                        #: is the strategy, with `p_pair_natural` left as
                        #: the genuinely unmeasured input the discovery
                        #: campaign exists to measure.
                        p_lone_win=(r_side if _bookrel
                                    else params.straddle_p_lone_win),
                        fee_per_share=params.straddle_fee_per_share,
                        #: THE COST THIS GATE WAS MISSING. This module's own
                        #: header states a passive maker buy at x is worth
                        #: `p_s - x - adverse_selection_per_share`, and
                        #: `ev_at` charges exactly that on the EDGE_GATED
                        #: path. The straddle path did not, and with the
                        #: venue's maker fee at 0 and `p_lone_win` pinned to
                        #: the price in book-relative mode -- which makes the
                        #: lone leg a FAIR BET, worth exactly zero -- EV
                        #: collapsed to `p_pair_natural * (1 - pair_cost)`.
                        #: That is positive for any p_pair_natural above
                        #: zero: MEASURED on a 0.52/0.46 quote, 0.001
                        #: admitted two legs.
                        #:
                        #: But a resting buy is filled PRECISELY when the
                        #: price moves against it, so an unmatched leg is not
                        #: a fair bet, and the unmatched leg is the owner's
                        #: whole stated failure mode. Charged on the lone
                        #: branch only: a filled pair is locked and cannot be
                        #: adversely selected.
                        adverse_per_share=params.adverse_selection_per_share)
                    _ok = SEV.admits(
                        _ev,
                        min_ev_per_share=params.straddle_min_ev_per_share)
                    if _pos_basis:
                        #: the pair-based figure above is computed and
                        #: DISCARDED: under this basis no pair probability
                        #: may admit a leg.
                        _poe = position_order_economics(
                            side=side, price=r_side, qty=qty, inv=inv,
                            fill_edge=params.straddle_fill_edge_per_share,
                            fee_per_share=params.straddle_fee_per_share)
                        _position[side] = _poe
                        _ev = {"ok": _poe["ok"],
                               "ev_per_share": _poe.get("ev_per_share")}
                        _ok = {"ok": _poe["ok"], "reason": _poe["reason"]}
                    ev_rungs.append({"price": str(r_side),
                                     "ev_per_share": (
                                         None if _ev.get("ev_per_share") is None
                                         else str(_ev["ev_per_share"])),
                                     "admitted": bool(_ok["ok"]),
                                     "reason": _ok.get("reason")})
                    if not _ok["ok"] and _ev_waived(params, _ok, _bookrel,
                                                    _rq, _wl_cap):
                        #: admitted on the loss ceiling, which has
                        #: ALREADY refused this leg above if it did not
                        #: fit. Said out loud in the evaluation.
                        ev_rungs[-1].update(
                            admitted=True, reason=EV_WAIVER_TOKEN,
                            gate_said=_ok.get("reason"))
                    elif not _ok["ok"]:
                        why_s = "ENTRY_EV_%s" % _ok["reason"]
                        continue
                    room -= r_side * qty
                    _committed[side] += r_side * qty
                    want.append(_place(side, r_side, qty, "STRADDLE_LEG",
                                       slot=i))
        #: PATIENT COMPLETION, AND WHY IT IS NOT ADDITIVE.
        #:
        #: It was written as additive -- "no cancel is issued here and the
        #: cheap rung above is untouched, so this can only ever add a
        #: second way for the window to end matched". That reasoning is
        #: wrong, and it cost money on its first live day.
        #:
        #: MEASURED 2026-11-15, window cpc-btc-updown-5m-2026-11-15-1925z.
        #: UP filled at 0.08 (t=234s). The completion bought DOWN at 0.88.
        #: The cheap DOWN rung was still resting, and it filled too. The
        #: window ended holding 2 UP @0.08, 2 DOWN @0.08 and 2 DOWN @0.88:
        #:   the cheap pair alone would have paid  +1.68
        #:   the extra 0.88 leg settled worthless  -1.76
        #:   realised                              -0.08
        #: Completing destroyed 1.76 in one window. The two routes are not
        #: independent chances at a pair; they are two purchases of the
        #: SAME side, and when both fill the expensive one is pure loss.
        #:
        #: So they are now mutually exclusive: completing CANCELS the cheap
        #: opposite rung in the same decision. Exactly one way to end
        #: matched, never two.
        #:
        #: WHICH OF THE TWO IS BETTER is close to a coin flip and is NOT
        #: what this fixes. Over 257 shadow windows the opposite side
        #: reached 0.08 after the first fill in 19 of 245 (q = 7.76%), and
        #: waiting beats completing only when q > 7.35%. Within that
        #: margin either policy is defensible; doing BOTH is strictly worse
        #: than either, which is the defect.
        comp = {"why": "COMPLETION_DISABLED", "side": None, "price": None,
                "qty": None, "pair_cost": None, "cancelled_cheap_rungs": [],
                "withdrew_pending_rungs": [],
                #: HOW the completion would be executed. `cross` stays
                #: False and `cross_why` carries the reason it was not
                #: taken, so a window that rested at the ceiling says
                #: WHY it rested rather than reading like a window with
                #: no offer to lift.
                "cross": False, "cross_why": "CROSS_DISABLED",
                "cross_limit": None, "cross_ask": None,
                "cross_fee_per_share": None, "cross_saving_per_share": None,
                #: the one comparison over every alternative, when wired
                "ranking": {"on": False}}
        if _cpc is not None and _cpc > ZERO:
            exp_c = _exposure(inv, rest)
            cheld, cunm = exp_c["unmatched_side"], exp_c["unmatched_qty"]
            if not cheld or cunm <= ZERO:
                comp["why"] = "NOTHING_UNMATCHED"
            else:
                cside = _other(cheld)
                cavg = inv[cheld][1] / inv[cheld][0]
                cprice = _floor_to(_cpc - cavg, tick)
                cqty = _floor_qty(cunm, params)
                #: THE CROSSING DECISION IS TAKEN HERE, before anything
                #: reads the resting book, because WHICH PRICE the
                #: completion would use decides which resting orders are
                #: completions and which are cheap rungs.
                #:
                #: THE OFFER IS DERIVED FROM `bid`/`ask`, NOT `quote`.
                #: That dict is built further down, AFTER this branch
                #: returns -- the identical trap the `priced()` note
                #: below records, and reading it here is an
                #: UnboundLocalError the moment a completion is enabled.
                #: DOWN's offer is one minus UP's bid, which is what
                #: `quote` would have said.
                #: ---------------- ONE DECISION OVER EVERY ALTERNATIVE.
                #: Consulted BEFORE the completion is priced into the
                #: plan, because its answer decides WHETHER to complete
                #: at all and by which route. It replaces the choice; the
                #: guards below are untouched and all still apply.
                _rank_choice = None
                if params.inventory_ranking_enabled:
                    _rank_choice, comp["ranking"] = _rank_inventory(
                        inv=inv, rest=rest, deficient=cside, deficit=cunm,
                        bid=bid, ask=ask, age=_dec(str(age)),
                        ceiling=cprice, params=params, tick=tick,
                        room=room, t=t)
                c_ask = ask if cside == UP else ONE - bid
                #: A CEILING IS NOT A RESTING PRICE.
                #:
                #: `cprice` is `pair_target - our_average_cost` and reads
                #: NOTHING from the book. When the completion side's own
                #: offer sits below it, resting at the ceiling is an order
                #: THROUGH the offer, which `post_only` rejects outright --
                #: so the completion never rests, never fills, and the
                #: residual it exists to convert stays residual.
                #:
                #: MEASURED on the production defaults: holding UP at 0.30
                #: with DOWN's own ask at 0.40 and a 0.96 target, it posted
                #: DOWN at 0.66. That order cannot rest. Worse, the pair it
                #: was reaching for was available to a MAKER at DOWN's own
                #: bid of 0.38 -- a pair cost of 0.68 against the 0.96 it
                #: asked for, so 28 cents a share of locked pair economics
                #: was left on the table by a price that could not fill.
                #:
                #: Capping one tick inside the offer fixes both at once: the
                #: order rests, and it pays STRICTLY LESS than the ceiling,
                #: so the achieved pair cost can only improve. It is not a
                #: taker route: it never reaches the offer.
                c_cap_px = None
                _cap = _floor_to(c_ask - tick, tick)
                if cprice >= c_ask and _cap > ZERO and _cap < cprice:
                    c_cap_px = _cap
                cx = _completion_cross(
                    ceiling=cprice, ask=c_ask, held_avg=cavg,
                    pair_target=_cpc, params=params, tick=tick)
                comp.update(cross_why=cx["why"], cross_ask=cx["ask"],
                            cross_limit=cx["limit"],
                            cross_fee_per_share=cx["fee_per_share"],
                            cross_saving_per_share=cx["saving_per_share"])

                def _is_completion_rung(px):
                    """Is this resting order a COMPLETION, or the cheap
                    straddle rung?

                    The ceiling alone was the discriminator while every
                    completion rested AT the ceiling. A crossing
                    completion rests at the OFFER if it fills only
                    partly, and judging that by the ceiling would read
                    the remainder as a cheap rung -- cancelling and
                    re-placing our own working completion every step,
                    losing its queue place and crossing again for the
                    same shares. Both prices count.
                    """
                    if abs(px - cprice) <= tick:
                        return True
                    #: the MAKER-CAPPED price is a third price this order
                    #: can legitimately rest at. Omitting it would make the
                    #: controller fail to recognise its OWN working
                    #: completion, cancelling and re-placing it every step
                    #: and losing its queue place each time -- the same
                    #: failure this closure was widened to prevent.
                    if c_cap_px is not None and abs(px - c_cap_px) <= tick:
                        return True
                    lim = cx.get("limit_dec")
                    return lim is not None and abs(px - lim) <= tick

                already = any(o["side"] == cside
                              and _is_completion_rung(o["price"])
                              for o in rest)
                comp.update(side=cside, price=str(cprice), qty=str(cqty),
                            pair_cost=str(cavg + cprice))
                # NOT `priced()` -- that closure is defined further down,
                # after this branch returns, so calling it here is a
                # NameError the moment a completion is enabled. The bound
                # is inlined instead.
                #: A VACUOUS HOLD IS NOT A DECISION. When every non-HOLD
                #: route came back `available=False` -- MAKER_COMPLETE
                #: because its fill rate is deliberately unmeasured,
                #: the taker routes because policy disables them -- the
                #: ranker compared nothing and prefers nothing. Honouring
                #: it then strands the leg forever, which is the one
                #: outcome the unmatched bound exists to prevent.
                #: Carrying naked inventory is not the fail-closed
                #: direction; reducing it is.
                _vacuous = bool(params.ranker_vacuous_hold_falls_back
                                and isinstance(comp.get("ranking"), dict)
                                and comp["ranking"].get("hold_is_vacuous"))
                if _vacuous:
                    comp["ranking_vacuous"] = True
                    comp["ranker_note"] = (
                        "RANKER_NO_PRICEABLE_ALTERNATIVE: every non-HOLD "
                        "route was unavailable, so the baseline completion "
                        "proceeds through the unchanged guards below")
                if _rank_choice == "HOLD" and not _vacuous:
                    #: THE RANKER CHOSE TO CARRY THE LEG, over at least
                    #: one alternative it could actually price. That is
                    #: "nothing on offer is better than waiting", not an
                    #: error. Acting here anyway would be taking a
                    #: decision the one comparison declined to take.
                    comp["why"] = "RANKED_HOLD"
                elif not (PRICE_MIN <= cprice <= PRICE_MAX) or cprice <= ZERO:
                    comp["why"] = "PRICE_OUT_OF_RANGE"
                elif cavg + cprice > _cpc:
                    comp["why"] = "PAIR_ABOVE_TARGET"
                elif cqty <= ZERO:
                    comp["why"] = "QTY_BELOW_MIN"
                elif already:
                    #: A completion from an earlier step is still working,
                    #: so the cheap rung on that side must STILL not be
                    #: queued beside it -- the double-buy does not care
                    #: which step placed the expensive leg.
                    pending = [o for o in want
                               if o.get("op") == "PLACE"
                               and o.get("side") == cside
                               and not _is_completion_rung(o["price"])]
                    for o in pending:
                        want.remove(o)
                        room += o["price"] * o["qty"]
                    comp["withdrew_pending_rungs"] = [str(o["price"])
                                                      for o in pending]
                    comp["why"] = "COMPLETION_ALREADY_WORKING"
                elif cprice * cqty > room:
                    comp["why"] = "NO_EXPOSURE_ROOM"
                else:
                    #: CANCEL THE CHEAP OPPOSITE RUNG FIRST. Every resting
                    #: order on the side we are about to buy expensively is
                    #: withdrawn by exact id, so the window cannot buy that
                    #: side twice. A rung with no readable id cannot be
                    #: withdrawn, and completing beside it would re-create
                    #: the 1925z defect, so that refuses instead.
                    #: `_read_resting` refuses the whole decision when any
                    #: resting order lacks an id, so every entry in `rest`
                    #: is cancellable by exact id here and no
                    #: un-withdrawable rung can reach this point.
                    stale = [o for o in rest if o["side"] == cside
                             and not _is_completion_rung(o["price"])]
                    if True:
                        #: A rung queued THIS step is withdrawn before it is
                        #: ever sent -- the straddle loop above runs first,
                        #: so without this the same decision would place the
                        #: cheap rung and the completion together, which is
                        #: the 1925z defect created in one step instead of
                        #: two. Cancelling an id we have not sent is not
                        #: possible, so it is dropped rather than cancelled.
                        pending = [o for o in want
                                   if o.get("op") == "PLACE"
                                   and o.get("side") == cside
                                   and not _is_completion_rung(o["price"])]
                        for o in pending:
                            want.remove(o)
                            room += o["price"] * o["qty"]
                        comp["withdrew_pending_rungs"] = [str(o["price"])
                                                          for o in pending]
                        for o in stale:
                            want.append({"op": "CANCEL", "id": o["id"]})
                        comp["cancelled_cheap_rungs"] = [o["id"] for o in stale]
                        #: LIFT A CHEAPER OFFER, OR REST AT THE CEILING.
                        #: `cprice` is the budget ceiling. If the offer on
                        #: this side is already cheaper than the ceiling by
                        #: more than the taker fee, buying it outright is
                        #: the same side in the same size at a strictly
                        #: lower ALL-IN price with certainty, instead of a
                        #: resting bid that can only be hit on the way
                        #: down. Every refusal keeps today's resting
                        #: completion exactly as it was.
                        c_px, c_only = cprice, True
                        #: A RANKED TAKER CHOICE IS A REQUEST, NOT A
                        #: PERMISSION. `_completion_cross` still has to
                        #: find the offer strictly cheaper all-in than
                        #: resting, the envelope still has to grant
                        #: taker_allowed, and the adapter's
                        #: `completion_taker_fault` still re-verifies
                        #: against the live book with its own depth
                        #: haircut, slippage charge and notional cap.
                        #: Conversely a ranked MAKER_COMPLETE is NOT
                        #: allowed to cross, whatever the dominance test
                        #: says, because crossing was not the action that
                        #: was ranked.
                        _wants_taker = (_rank_choice is None
                                        or str(_rank_choice).startswith(
                                            "TAKER"))
                        if cx["cross"] and _wants_taker:
                            comp["cross"] = True
                            c_px, c_only = cx["limit_dec"], False
                        elif cx["cross"] and not _wants_taker:
                            comp["cross_why"] = "RANKED_MAKER_COMPLETE"
                        if c_only and c_cap_px is not None:
                            #: maker path only. A crossing taker limit is
                            #: already bounded by the ceiling and was
                            #: separately authorized; this touches neither.
                            comp["maker_capped_from"] = str(c_px)
                            comp["maker_cap_why"] = "CEILING_CROSSED_THE_OFFER"
                            c_px = c_cap_px
                            comp["price"] = str(c_px)
                            comp["pair_cost"] = str(cavg + c_px)
                        #: ROOM IS CHARGED AT THE CEILING EITHER WAY. A
                        #: crossing limit is bounded by the ceiling by
                        #: construction, so the ceiling remains a valid
                        #: upper bound on what this order can spend and
                        #: the exposure check above does not change
                        #: meaning when crossing is on.
                        room -= cprice * cqty
                        want.append(_place(cside, c_px, cqty,
                                           "STRADDLE_COMPLETION",
                                           post_only=c_only))
                        comp["price"] = str(c_px)
                        comp["pair_cost"] = str(cavg + c_px)
                        comp["why"] = None
                        why_s = ("STRADDLE_COMPLETING_CROSS" if cx["cross"]
                                 else "STRADDLE_COMPLETING")
        #: 1b. THE SECOND STRATEGY, AND THE ONE CHOICE BETWEEN THEM.
        #:
        #: `want` and `room` already carry everything the straddle
        #: decided THIS step, including a completion and the cheap rungs
        #: it cancelled or withdrew. So the late favourite is offered the
        #: room that is genuinely left, and the arbiter compares two
        #: fully-formed plans rather than two wishes.
        #:
        #: A COMPLETION IN FLIGHT STANDS THE LATE FAVOURITE DOWN
        #: ENTIRELY. The completion buys the side OPPOSITE our unmatched
        #: leg; the favourite may be that same side. Letting both run
        #: could buy one side twice in one step -- the 1925z defect in a
        #: new costume -- and a pair is worth more than this candidate
        #: anyway. So the straddle's plan is returned untouched and the
        #: reason is recorded.
        late = {"ok": False, "reason": "NOT_EVALUATED"}
        arb = None
        #: PREFIX, NOT EQUALITY. A crossing completion reports
        #: STRADDLE_COMPLETING_CROSS, and matching the resting spelling
        #: alone would have let the late favourite run beside it -- the
        #: 1925z double-buy this very branch exists to prevent, re-created
        #: by a string comparison. Both spellings stand it down.
        completing = (str(why_s or "").startswith("STRADDLE_COMPLETING")
                      or comp.get("why") == "COMPLETION_ALREADY_WORKING")
        if completing:
            late = {"ok": False, "reason": "COMPLETION_IN_FLIGHT"}
        else:
            late = LF.evaluate(
                p_up=p_up, p_age_s=p_age_s,
                t_in_window_s=t, window_s=params.window_s,
                book={"bid": bid, "ask": ask,
                      "bid_shares": (book or {}).get("bid_shares"),
                      "ask_shares": (book or {}).get("ask_shares")},
                inventory={s_: {"qty": inv[s_][0], "cost": inv[s_][1]}
                           for s_ in SIDES},
                resting=rest,
                room_usd=max(ZERO, room),
                loss_room_usd=loss_room_usd,
                clip_usd=params.late_favorite_clip_usd,
                fee_per_share=params.late_favorite_fee_per_share,
                min_edge_per_share=params.late_favorite_min_edge_per_share,
                late_window_s=params.late_favorite_window_s)
            arb = AR.choose(
                straddle_orders=want, completion_order=None,
                completion_ev_usd=None, late=late,
                continuation_usd=params.late_favorite_continuation_usd,
                handoff_margin_usd=params.late_favorite_handoff_margin_usd)
            #: THE ARBITER'S CHOICE IS WHAT GETS EMITTED. On a HANDOFF
            #: that is the cancel ALONE: `deferred_order` is deliberately
            #: left unsent, and a later step places it once `rest` no
            #: longer shows the cancelled id -- the executor's
            #: venue-confirmed view, not a hope.
            want = list(arb["orders"]) + list(arb["cancels"])
            if arb["action"] == AR.HANDOFF:
                why_s = "LATE_FAVORITE_HANDOFF"
            elif arb["why"] == "STRADDLE_AND_LATE_FAVORITE":
                why_s = "STRADDLE_AND_LATE_FAVORITE"
        #: NAME THE CONSTRAINT ON THE ACTION THAT MATTERED MOST.
        #:
        #: When inventory is lopsided the order worth having is the one
        #: on the LIGHT side: it reduces unmatched exposure and completes
        #: the pair. If that side was refused, its reason is the binding
        #: one, whatever the heavy side happened to say last. In live
        #: window 1930z the heavy side's UNMATCHED_CEILING was reported
        #: while the light side's COMPLETE_BUDGET_EXHAUSTED -- the
        #: refusal that actually stopped the pair completing -- sat only
        #: in `refused`.
        #: one CANCEL per id: the requote pass and the completion block can
        #: both decide to withdraw the same leg.
        _seen_c, _dedup = set(), []
        for o in want:
            if o.get("op") == "CANCEL":
                if o.get("id") in _seen_c:
                    continue
                _seen_c.add(o.get("id"))
            _dedup.append(o)
        want = _dedup
        if not [o for o in want if o.get("op") == "PLACE"]:
            _light = None
            if inv[UP][0] != inv[DOWN][0]:
                _light = UP if inv[UP][0] < inv[DOWN][0] else DOWN
            if _light is not None and _side_why.get(_light):
                why_s = _side_why[_light]
        ev_s = {"completion": comp,
                "side_refusals": dict(_side_why),
                "one_sided": dict(_one_sided),
                "heavy_underdog": dict(_hu),
                "position_economics": dict(_position),
                "pacing": dict(_pacing),
                "pair_completion": dict(_pair_plan),
                "completion_reserve": _reserve,
                "book_relative": {
                    "on": _bookrel,
                    "pair_ceiling": None if _pceil is None else str(_pceil),
                    "join_ticks": int(params.straddle_join_ticks or 0),
                    "fill_model_measured": bool(
                        params.straddle_fill_model_measured),
                    "quotes": {k: str(v) for k, v in _bk.items()
                               if v is not None},
                    "pair_quote_cost": (
                        None if (not _bk or any(v is None
                                                for v in _bk.values()))
                        else str(sum(_bk.values()))),
                    "why": _bk_why},
                "entry_ev": {"rungs": ev_rungs,
                             "rates": {
                                 "p_pair_natural": (
                                     None if params.straddle_p_pair_natural
                                     is None else
                                     str(params.straddle_p_pair_natural)),
                                 "p_complete": (
                                     None if params.straddle_p_complete is None
                                     else str(params.straddle_p_complete)),
                                 "p_lone_win": (
                                     None if params.straddle_p_lone_win is None
                                     else str(params.straddle_p_lone_win)),
                                 "fee_per_share":
                                     str(params.straddle_fee_per_share),
                                 "min_ev_per_share":
                                     str(params.straddle_min_ev_per_share)}},
                "late_favorite": {k: v for k, v in late.items()
                                  if k != "order"},
                "arbiter": None if arb is None else {
                    "action": arb["action"], "why": arb["why"],
                    "evaluation": arb["evaluation"],
                    "deferred_order": (
                        None if not arb.get("deferred_order") else
                        {"side": arb["deferred_order"]["side"],
                         "price": str(arb["deferred_order"]["price"]),
                         "qty": str(arb["deferred_order"]["qty"])})},
                "entry": {"why": why_s, "price": str(_sx), "qty": str(qty),
                          "room_usd": str(max(ZERO, room)),
                          "cap_usd": str(_cap),
                          "rungs": [str(r) for r in _rungs],
                          "ladder_cost_one_side": str(ladder * qty),
                          "pair_cost_if_both_fill": str(2 * _sx)}}
        return _result(OPEN_FAVORITE if want else HOLD_OR_SKIP,
                       why_s, want, ev_s, exp_s)

    # 2. Probability and favorite.
    if p_up is None:
        p, source = (bid + ask) / 2, "BOOK"
    else:
        p = _dec(p_up)
        if p is None:
            return hold("P_UP_UNREADABLE", cancel=True)
        p, source = min(ONE, max(ZERO, p)), (p_source if p_source is not None else "CALLER")
    independent = source != "BOOK"
    fav = UP if p >= HALF else DOWN
    prob = {UP: p, DOWN: ONE - p}
    quote = {UP: {"bid": bid, "ask": ask}, DOWN: {"bid": ONE - ask, "ask": ONE - bid}}
    fav_bid = quote[fav]["bid"]

    def priced(x):
        return PRICE_MIN <= x <= PRICE_MAX

    # 3. Entry price and its value after the adverse-selection allowance.
    #    ACCUMULATE rests `accumulate_depth_ticks` below the bid (0 = join
    #    it); EDGE_GATED keeps its own `depth_ticks`.
    _ticks = (params.accumulate_depth_ticks if accumulate
              else params.depth_ticks)
    target = _floor_to(fav_bid - _ticks * tick, tick)

    def ev_at(side, price):
        return prob[side] - price - params.adverse_selection_per_share

    late = t >= params.no_new_entry_after_s

    def complement_depth_block(side):
        """Why a FIRST LEG on `side` is refused for want of a complement
        book, or None. Off when `min_complement_shares` is 0.

        The complement of UP is bought from the DOWN book, whose depth is
        the UP side's BID depth, and vice versa -- the same identity the
        rest of this module uses for prices. Unreadable depth refuses:
        a book we cannot measure is not a book we can rely on completing
        against.
        """
        need = _dec(params.min_complement_shares)
        if need is None or need <= ZERO:
            return None
        avail = (book or {}).get("bid_shares") if side == UP \
            else (book or {}).get("ask_shares")
        got = _dec(avail)
        if got is None:
            return "COMPLEMENT_DEPTH_UNREADABLE"
        if got < need:
            return "COMPLEMENT_DEPTH_SHORT"
        return None

    def entry_block(price, side=None):
        """Why a favorite entry at `price` is refused, or None.

        ACCUMULATE judges the band on `price` -- what this order pays --
        and applies NO edge gate. EDGE_GATED is unchanged: the band on the
        touch, and the adverse-selection edge test.
        """
        if late:
            return "ENTRY_WINDOW_CLOSED"
        cd = complement_depth_block(side if side is not None else fav)
        if cd:
            return cd
        banded = price if accumulate else fav_bid
        if not (params.band_lo <= banded <= params.band_hi):
            return "OUT_OF_BAND"
        if not priced(price):
            return "PRICE_OUT_OF_RANGE"
        if not accumulate and ev_at(fav, price) < params.min_edge_per_share:
            return "EV_BELOW_MIN"
        return None

    # Hedge geometry: the unmatched side is `held`, the hedge buys the other.
    # With `hedge_enabled` False there is no hedge at all: no leg is read as
    # one, none is priced, and none is ever chosen.
    held, unmatched = exp["unmatched_side"], exp["unmatched_qty"]
    if not params.hedge_enabled:
        held, unmatched = None, ZERO
    hedge_side = _other(held) if held else None
    avg_held = inv[held][1] / inv[held][0] if held else None
    hedge_price = _floor_to(quote[hedge_side]["bid"], tick) if held else None
    flipped = bool(held) and prob[held] < HALF - params.flip_margin

    def hedge_block(price):
        """Why a hedge at `price` is refused, or None."""
        pair = avg_held + price
        if pair > params.hard_pair_ceiling:
            return "PAIR_ABOVE_CEILING"
        if pair <= params.hedge_target_pair or flipped:
            return None
        return "PAIR_ABOVE_TARGET"

    # 4./5. Review what is resting: keep only what would be placed again now.
    cancels, review, kept = [], [], []
    kept_hedge_qty = ZERO
    kept_entries = 0
    #: an ACCUMULATE entry tracks the touch exactly: one tick of drift means
    #: paying over the bid (filled by a seller on the way down) or sitting
    #: behind it (never reached). EDGE_GATED keeps its one-tick tolerance.
    drift_tol = ZERO if accumulate else tick
    for o in rest:
        purpose, why = None, None
        on_hedge, on_fav = bool(held) and o["side"] == hedge_side, o["side"] == fav
        if o["age_s"] > params.quote_ttl_s:
            why = "TTL_EXPIRED"
        elif not on_hedge and not on_fav:
            why = "WRONG_SIDE"
        else:
            if on_hedge:
                if abs(o["price"] - hedge_price) > tick:
                    why = "PRICE_DRIFT"
                elif hedge_block(o["price"]) is not None:
                    why = hedge_block(o["price"])
                elif kept_hedge_qty + o["qty"] > unmatched:
                    why = "HEDGE_EXCEEDS_UNMATCHED"
                else:
                    purpose = "HEDGE"
                    kept_hedge_qty += o["qty"]
            if purpose is None and on_fav:
                if abs(o["price"] - target) > drift_tol:
                    why = "PRICE_DRIFT"
                elif entry_block(o["price"], o["side"]) is not None:
                    why = entry_block(o["price"], o["side"])
                elif (accumulate and
                      kept_entries >= int(params.max_resting_entries)):
                    why = "MAX_RESTING_ENTRIES"
                else:
                    purpose, why = "ENTRY", None
                    kept_entries += 1
        if purpose is None:
            cancels.append({"op": "CANCEL", "id": o["id"]})
        else:
            kept.append(dict(o, purpose=purpose))
        review.append({"id": o["id"], "side": o["side"], "price": o["price"],
                       "purpose": purpose, "verdict": "KEEP" if purpose else "CANCEL",
                       "why": why})

    total = exp["total_exposure_usd"]   # counts orders being cancelled: a cancel can lose the race

    # 6. Hedge the unmatched leg. May use the hedge reserve.
    hedge = {"chosen": False,
             "why": ("HEDGING_DISABLED" if not params.hedge_enabled
                     else "NO_UNMATCHED_INVENTORY")}
    if held:
        pair = avg_held + hedge_price
        # Everything already working on the hedge side counts, cancelled or not.
        working = sum((o["qty"] for o in rest if o["side"] == hedge_side), ZERO)
        need = unmatched - working
        room = params.max_exposure_usd - total
        can_afford = room / hedge_price if hedge_price > 0 and room > 0 else ZERO
        h_qty = _floor_qty(min(need, can_afford), params)
        hedge = {"held_side": held, "side": hedge_side, "price": hedge_price,
                 "qty": h_qty, "pair_cost": pair, "avg_held_cost": avg_held,
                 "unmatched_qty": unmatched, "already_working_qty": working,
                 "p_held": prob[held], "flipped": flipped,
                 "ev_per_share": ev_at(hedge_side, hedge_price),
                 "room_usd": room, "chosen": False}
        if not priced(hedge_price):
            hedge["why"] = "PRICE_OUT_OF_RANGE"
        elif hedge_block(hedge_price) is not None:
            hedge["why"] = hedge_block(hedge_price)
        elif need <= 0:
            hedge["why"] = "HEDGE_ALREADY_WORKING"
        elif _floor_qty(need, params) < min_qty:
            hedge["why"] = "QTY_BELOW_MIN"
        elif h_qty < min_qty:
            hedge["why"] = "NO_EXPOSURE_ROOM"
        else:
            hedge["chosen"] = True
            hedge["why"] = ("PAIR_AT_OR_BELOW_TARGET" if pair <= params.hedge_target_pair
                            else "FAVORITE_FLIPPED_PAIR_WITHIN_CEILING")

    # 7. Exit: sell the unmatched leg when the bid pays more than it is worth.
    if not params.taker_allowed:
        exit_ = {"blocked": "TAKER_DISABLED", "chosen": False, "why": "TAKER_DISABLED"}
    elif not held:
        exit_ = {"chosen": False, "why": "NO_UNMATCHED_INVENTORY"}
    else:
        x_price = _floor_to(quote[held]["bid"], tick)
        fee = params.taker_fee_coef * x_price * (ONE - x_price)
        x_qty = _floor_qty(unmatched, params)
        exit_ = {"side": held, "price": x_price, "qty": x_qty,
                 "taker_fee_per_share": fee, "net_per_share": x_price - fee,
                 "value_per_share": prob[held],
                 "ev_per_share": x_price - fee - prob[held], "chosen": False}
        if hedge["chosen"]:
            exit_["why"] = "HEDGE_TAKES_PRIORITY"
        elif not priced(x_price):
            exit_["why"] = "PRICE_OUT_OF_RANGE"
        elif x_qty < min_qty:
            exit_["why"] = "QTY_BELOW_MIN"
        elif x_price - fee > prob[held] + params.min_edge_per_share:
            exit_["chosen"], exit_["why"] = True, "BID_NET_OF_FEE_ABOVE_VALUE"
        else:
            exit_["why"] = "BID_NOT_ABOVE_VALUE"

    # 8. Open or add to the favorite. Entries may not touch the hedge reserve.
    kind = ADD_FAVORITE if inv[fav][0] > 0 else OPEN_FAVORITE
    if params.clip_shares is not None:
        _cs = _dec(params.clip_shares)
        e_qty = (_floor_qty(_cs, params)
                 if _cs is not None and _cs > 0 else D("0.00"))
    else:
        e_qty = (_floor_qty(params.clip_usd / target, params)
                 if target > 0 else D("0.00"))
    notional = target * e_qty
    if params.reserve_mode == "COMPLETION":
        res = completion_reserve(inv, rest, params, add_side=fav,
                                 add_price=target, add_qty=e_qty)
        entry_cap = params.max_exposure_usd - res["usd"]
    elif params.reserve_mode == "NONE":
        res = None
        entry_cap = params.max_exposure_usd
    elif params.reserve_mode == "FIXED":
        res = None
        entry_cap = params.max_exposure_usd - params.hedge_reserve_usd
    else:                                   # unreachable: checked above
        return hold("PARAMS_UNREADABLE", cancel=True)
    entry = {"kind": kind, "side": fav, "fav_bid": fav_bid, "price": target,
             "qty": e_qty, "p_side": prob[fav], "ev_per_share": ev_at(fav, target),
             "min_edge_per_share": params.min_edge_per_share,
             "notional_usd": notional, "room_usd": entry_cap - total,
             "reserve_mode": params.reserve_mode,
             "completion_reserve_usd": None if res is None else res["usd"],
             "cap_usd": params.max_exposure_usd,
             "entry_mode": params.entry_mode,
             "resting_entries": kept_entries,
             "max_resting_entries": (int(params.max_resting_entries)
                                     if accumulate else 1),
             "chosen": False}
    block = entry_block(target)
    if block is None:
        # EDGE_GATED allows one entry at a time. ACCUMULATE allows
        # `max_resting_entries`, which is what "repeated small maker orders
        # while capacity remains" needs: the cap, not a count of one, is
        # what should stop it.
        if accumulate:
            if kept_entries >= int(params.max_resting_entries):
                block = "MAX_RESTING_ENTRIES"
        elif any(o["side"] == fav for o in kept):
            block = "ENTRY_ALREADY_RESTING"
    if block is None and e_qty < min_qty:
        block = "QTY_BELOW_MIN"
    if block is None and total + notional > entry_cap:
        block = "NO_EXPOSURE_ROOM"
    if block is None and (hedge["chosen"] or exit_["chosen"]):
        block = "HEDGE_TAKES_PRIORITY" if hedge["chosen"] else "EXIT_TAKES_PRIORITY"
    entry["chosen"], entry["why"] = block is None, block or "EV_AND_ROOM_OK"

    evaluation = {"independent_estimate": independent, "t_in_window_s": t,
                  "entry_window_open": not late, "quotes": quote,
                  "entry": entry, "hedge": hedge, "exit": exit_, "resting": review}

    def done(action, why, orders):
        # Last line of defence: nothing non-post-only or off-grid leaves here.
        for o in orders:
            if o["op"] == "PLACE":
                if not o["post_only"] and not params.taker_allowed:
                    return _result(HOLD_OR_SKIP, "TAKER_ORDER_SUPPRESSED", list(cancel_all),
                                   evaluation, exp, fav, p, source)
                if not priced(o["price"]) or o["qty"] < min_qty:
                    return _result(HOLD_OR_SKIP, "ORDER_OUT_OF_RANGE", list(cancel_all),
                                   evaluation, exp, fav, p, source)
                #: AND AGAINST THE VENUE'S OWN GRID, when we were required
                #: to verify it. The checks above use `params`; this one
                #: uses what the venue said. It REFUSES the decision rather
                #: than resizing the order: rounding a sub-minimum quantity
                #: up to the minimum is how a penny intent becomes a
                #: ten-penny order, so nothing here rounds at all.
                if _vspec is not None:
                    _vbad = VC.check_order(o["price"], o["qty"], _vspec)
                    if _vbad:
                        return _result(HOLD_OR_SKIP, "VENUE_%s" % _vbad,
                                       list(cancel_all), evaluation, exp,
                                       fav, p, source)
        return _result(action, why, orders, evaluation, exp, fav, p, source)

    if hedge["chosen"]:
        order = _place(hedge_side, hedge_price.quantize(tick), h_qty, hedge["why"])
        return done(HEDGE_UNDERDOG, hedge["why"], cancels + [order])
    if exit_["chosen"]:
        # Selling the leg leaves nothing to hedge or add to: clear the book too.
        order = _place(held, exit_["price"].quantize(tick), exit_["qty"], exit_["why"],
                       post_only=False, intent="SELL")
        return done(EXIT_OR_FLIP, exit_["why"], list(cancel_all) + [order])
    if entry["chosen"]:
        # the lowest free slot, which is simply how many entries are
        # already resting: the same state decides the same slot, so a
        # replayed decision is still recognised as the same intent.
        order = _place(fav, target.quantize(tick), e_qty, kind,
                       slot=(kept_entries if accumulate else 0))
        return done(kind, entry["why"], cancels + [order])
    if cancels:
        reasons = sorted({r["why"] for r in review if r["verdict"] == "CANCEL"})
        return done(REPRICE, "+".join(reasons), cancels)
    # 9. Nothing to do: the entry's binding reason is the reason.
    return done(HOLD_OR_SKIP, entry["why"], [])
