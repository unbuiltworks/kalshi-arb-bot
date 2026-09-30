"""Read-only clip-capacity completion shadow over the PMUS BTC15m book tape.

NOTHING HERE CAN TRADE. It replays a recorded tape. No transport, no order
verb, no credential, no arm token. It moves no money.

Governed by analysis/pmus/CLIP_CAPACITY_PREREG.md, which fixed every rule,
threshold and clip size before any row was scored.

THE TWO LOOKAHEAD GUARDS, structural rather than remembered:
  1. completion is sized to the residual held AT THE TRIGGER; the end-of-window
     residual is never visible to a decision;
  2. the residual SIDE is whatever the forward walk holds at that instant, never
     the side that turns out to be residual at settlement.
Settlement is read exactly once, by the scorer, after the walk has finished.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

THETA_TAKER = D("0.0695")
THETA_MAKER_MODELED = D("0.0125")   # MODELED only. Credited rebate is $0.


def _d(v) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def taker_fee(price: D, qty: D) -> D:
    return THETA_TAKER * qty * price * (D(1) - price)


def modeled_maker_rebate(price: D, qty: D) -> D:
    """NEVER added into net. Reported on its own line, always."""
    return THETA_MAKER_MODELED * qty * price * (D(1) - price)


def walk_ladder(levels: Sequence[Sequence[str]], want: D
                ) -> Tuple[D, D, bool]:
    """Consume `want` contracts across ACTUAL recorded levels.

    Returns (filled_qty, total_cost, short). `short` is True when displayed
    depth could not cover `want` -- the shortfall is NEVER filled at the touch
    price and never at a guessed one. That remainder is UNKNOWN.
    """
    filled = D(0)
    cost = D(0)
    for lvl in levels or []:
        if filled >= want:
            break
        px = _d(lvl[0] if len(lvl) > 0 else None)
        qty = _d(lvl[1] if len(lvl) > 1 else None)
        if px is None or qty is None or qty <= 0:
            continue
        take = want - filled
        if take > qty:
            take = qty
        filled += take
        cost += take * px
    return filled, cost, filled < want


def own_price(side: str, px: D) -> D:
    """Venue prices are always the LONG (UP/YES) side. DOWN's own price is 1-p."""
    return px if side == "up" else (D(1) - px)


def live_touch_own(row: Dict[str, Any], side: str) -> Optional[D]:
    """Contemporaneous cost of BUYING `side` as a taker, in that side's terms."""
    if side == "up":
        return _d(row.get("ask"))
    b = _d(row.get("bid"))
    return (D(1) - b) if b is not None else None


def complement(side: str) -> str:
    return "down" if side == "up" else "up"


#: the three preregistered maker-fill treatments. All three share the causal
#: precondition (observed trade volume AND a print at or through our price in
#: our own terms). They differ ONLY in what stands ahead of us in the queue.
FILL_MODELS = ("optimistic", "queue_aware", "pessimistic")


def resting_venue_price(row: Dict[str, Any], side: str) -> Optional[D]:
    """The venue-quoted (YES) price our passive clip would rest at.

    Buying UP rests a BID at the bid. Buying DOWN is resting a SELL of YES at
    the ask, so the venue price is the ask even though our own-terms cost is
    1 - ask.
    """
    return _d(row.get("bid")) if side == "up" else _d(row.get("ask"))


def level_displayed_qty(ladder: Optional[Dict[str, Any]], side: str,
                        venue_px: Optional[D]) -> Optional[D]:
    """Quantity displayed AT OUR EXACT PRICE LEVEL, in venue terms.

    This is the only defensible queue-ahead measure. `bid_shares`/`ask_shares`
    are whole-ladder sums (~20,000 against a touch of ~100-1,600 on this tape)
    and using them as queue-ahead is simply wrong.

    None means our price is not displayed at all, so queue position cannot be
    established from the public tape -- which is treated as NO FILL, never as
    an empty queue.
    """
    if ladder is None or venue_px is None:
        return None
    rows = (ladder.get("bids") if side == "up" else ladder.get("offers")) or []
    for lvl in rows:
        p = _d(lvl[0] if len(lvl) > 0 else None)
        q = _d(lvl[1] if len(lvl) > 1 else None)
        if p is not None and q is not None and p == venue_px:
            return q
    return None


def buy_levels(row: Dict[str, Any], side: str) -> List[List[str]]:
    """Levels a TAKER buying `side` would consume, in that side's own terms.

    Buying UP lifts the offers at their quoted price. Buying DOWN is selling
    YES, which hits the bids, and each bid at p costs (1-p) in DOWN's terms.
    """
    if side == "up":
        return [[str(p), str(q)] for p, q in
                ((lvl[0], lvl[1]) for lvl in (row.get("offers") or []))]
    out = []
    for lvl in (row.get("bids") or []):
        p = _d(lvl[0])
        if p is None:
            continue
        out.append([str(D(1) - p), str(lvl[1])])
    return out


# --------------------------------------------------------------- the walk

class Book:
    """Our simulated inventory over one window. Own-outcome terms."""

    def __init__(self) -> None:
        self.q = {"up": D(0), "down": D(0)}
        self.c = {"up": D(0), "down": D(0)}
        self.maker_qty = D(0)
        self.taker_qty = D(0)
        self.fees = D(0)
        #: the same fees, ATTRIBUTED TO THE SIDE that paid them. `fees` stays
        #: the single authoritative total and no economics read this split; it
        #: exists so a complement cap can include the fee the held leg already
        #: paid, which `avg()` cannot see because a taker fee never enters
        #: `c[side]`. Adding it changed no P&L: the sum is unchanged and
        #: `settle` still reads `self.fees`.
        self.fees_side = {"up": D(0), "down": D(0)}
        self.modeled_rebate = D(0)
        self.locked = D(0)          # realized P&L of completed pairs
        self.completions = 0
        self.completed_qty = D(0)
        self.completion_cost = D(0)
        self.unknown_events = 0     # depth shortfalls
        self.peak_resid = D(0)
        self.eligible_states = 0
        self.attempted_states = 0
        self.executable_states = 0
        self.missed_qualifying = 0
        self.gross_margin = D(0)
        self.edges: List[D] = []
        self.cum_matched = D(0)
        self.recycle_cancels = 0

    def residual(self) -> D:
        return self.q["up"] - self.q["down"]

    def avg(self, side: str) -> D:
        return (self.c[side] / self.q[side]) if self.q[side] > 0 else D(0)

    def fee_per_share(self, side: str) -> D:
        """Taker fee already paid per share still held on `side`.

        Zero for maker-only inventory, which is the common case. `avg()` cannot
        see this: a taker fee is added to `fees` and never to `c[side]`.
        """
        return (self.fees_side[side] / self.q[side]) if self.q[side] > 0 \
            else D(0)

    def economic_basis(self, side: str) -> D:
        """All-in cost per share held on `side`: price paid plus fee paid.

        This is the basis a pair-cost bound must use. Using `avg()` alone makes
        the bound optimistic by up to `THETA_TAKER * p * (1-p)` per share
        (<= ~$0.0174), which the review of the complement controller
        demonstrated: held UP = 10 taker @0.50 + 10 maker @0.40 gives
        avg 0.45, so a 0.51 complement reads as "exactly theta" while the true
        pair costs 0.9686875.
        """
        return self.avg(side) + self.fee_per_share(side)

    def release_fees(self, side: str, q_before: D, q_after: D) -> None:
        """Keep attributed fee per share constant when shares leave the book.

        A completion decrements `q` and `c`; without this the remaining shares
        would inherit the departed shares' fees and the basis would drift up.
        No P&L reads `fees_side`, so this cannot move a reported number -- it
        keeps the CAP honest, nothing else.
        """
        if q_before <= 0:
            return
        if q_after <= 0:
            self.fees_side[side] = D(0)
            return
        self.fees_side[side] = self.fees_side[side] * q_after / q_before

    def add(self, side: str, qty: D, cost: D, *, maker: bool,
            fee: Optional[D] = None) -> None:
        if qty <= 0:
            return
        self.q[side] += qty
        self.c[side] += cost
        if maker:
            self.maker_qty += qty
            self.modeled_rebate += modeled_maker_rebate(cost / qty, qty)
        else:
            self.taker_qty += qty
            # `fee` is the PER-LEVEL fee actually incurred. Recomputing it from
            # the blended average price understates/overstates it, because the
            # fee is convex in price -- so the caller's per-level sum wins
            # whenever it is supplied.
            _f = taker_fee(cost / qty, qty) if fee is None else fee
            self.fees += _f
            self.fees_side[side] += _f
        a = abs(self.residual())
        if a > self.peak_resid:
            self.peak_resid = a

    def settle(self, winner: Optional[str]) -> Dict[str, Any]:
        m = min(self.q["up"], self.q["down"])
        matched_cost = m * (self.avg("up") + self.avg("down"))
        matched_pnl = m - matched_cost
        r = self.residual()
        if r > 0:
            rq, ravg, rwin = r, self.avg("up"), (winner == "up")
        elif r < 0:
            rq, ravg, rwin = -r, self.avg("down"), (winner == "down")
        else:
            rq, ravg, rwin = D(0), D(0), False
        resid_cost = rq * ravg
        resid_pnl = ((rq if rwin else D(0)) - resid_cost) if winner else None
        net = None
        if resid_pnl is not None:
            net = matched_pnl + resid_pnl + self.locked - self.fees
        return {
            "matched_qty": m + self.completed_qty,
            "matched_pnl": matched_pnl + self.locked,
            "resid_qty": rq, "resid_pnl": resid_pnl,
            "fees": self.fees, "modeled_rebate": self.modeled_rebate,
            "credited_rebate": D(0),
            "maker_qty": self.maker_qty, "taker_qty": self.taker_qty,
            "completions": self.completions,
            "completed_qty": self.completed_qty,
            "completion_cost": self.completion_cost,
            "unknown_events": self.unknown_events,
            "peak_resid": self.peak_resid,
            "max_simultaneous_residual": self.peak_resid,
            "cumulative_matched": m + self.completed_qty,
            "eligible_states": self.eligible_states,
            "attempted_states": self.attempted_states,
            "executable_states": self.executable_states,
            "missed_qualifying": self.missed_qualifying,
            "gross_margin": self.gross_margin,
            "median_edge_per_contract": (
                sorted(self.edges)[len(self.edges) // 2]
                if self.edges else None),
            "net": net,
            "recycle_cancels": self.recycle_cancels,
            "lock_via": getattr(self, "lock_via", None),
            "lock_inventory": getattr(self, "lock_inventory", None),
            "lock_cancels": getattr(self, "lock_cancels", 0),
            "floor_guard_blocks": getattr(self, "floor_guard_blocks", 0),
            "floor_guard_blocked_usd": getattr(
                self, "floor_guard_blocked_usd", D(0)),
        }


def maker_fill(row: Dict[str, Any], side: str, clip: D, *,
               queue_aware: bool = False,
               fill_model: Optional[str] = None,
               ladder: Optional[Dict[str, Any]] = None,
               qstate: Optional[Dict[str, Any]] = None,
               limit_px: Optional[D] = None
               ) -> Tuple[D, Optional[D]]:
    """Would a clip resting at `side`'s touch have filled on THIS observation?

    CAUSAL PRECONDITION, shared by all three treatments and never relaxed: the
    tape must show trade volume in this interval AND `last` must be at or
    through our resting price in our own terms. A price merely TOUCHING our
    level is not a fill -- without an attributable print there is no evidence
    anyone traded against us.

    The treatments differ only in the queue ahead of us:

    optimistic  -- no queue. Our clip is at the front. The existing rule,
                   retained unchanged so the audited result stays comparable.
    queue_aware -- ahead is initialised from the displayed quantity AT OUR
                   EXACT PRICE LEVEL when we join, and decremented only by
                   volume that is attributable to our side and price. Re-quoting
                   to a new level sends us to the back of that level's queue.
                   If our price is not displayed, queue position is UNKNOWN and
                   there is no fill.
    pessimistic -- the pre-existing conservative bound: the whole displayed
                   book must clear first. Known to be mis-specified as a queue
                   measure; kept only as a floor.

    `qstate` carries the queue across observations for queue_aware; without it
    the model cannot persist and degrades to requiring one interval to clear
    the level, which is reported rather than hidden.
    """
    model = fill_model
    if model is None:
        model = "pessimistic" if queue_aware else "optimistic"
    if model not in FILL_MODELS:
        raise ValueError("unknown fill_model %r" % (model,))

    dlt = _d(row.get("shares_traded_delta_lower_bound"))
    if dlt is None or dlt <= 0:
        return D(0), None
    if side == "up":
        px = _d(row.get("bid"))
    else:
        b = _d(row.get("ask"))
        px = (D(1) - b) if b is not None else None
    if px is None or px <= 0 or px >= 1:
        return D(0), None
    last = _d(row.get("last"))
    if last is None:
        return D(0), None
    # the price the causal test is about is OURS when the caller supplied it.
    # Against the touch it asked the wrong question for an order resting away
    # from the touch: a print through our limit fills us even when it did not
    # reach the touch, and a print at the touch says nothing about an order
    # sitting behind it.
    test_px = px if limit_px is None else D(str(limit_px))
    if own_price(side, last) > test_px:
        return D(0), None          # nothing printed at or through our price

    if model == "optimistic":
        avail = dlt
    elif model == "pessimistic":
        ahead = _d(row.get("bid_shares") if side == "up"
                   else row.get("ask_shares")) or D(0)
        avail = dlt - ahead
    else:
        # OUR PRICE, NOT THE TOUCH. `resting_venue_price(row, side)` is the
        # touch, and keying the queue on it was harmless only while the engine
        # requoted to the touch every tick. Once a complement rests BELOW the
        # touch that keying asserts something false -- it measures the queue at
        # a level we are not on, and it resets our queue position to "just
        # joined" whenever the TOUCH moves, although our order never moved and
        # should be GAINING priority. Measured on the training cohort with the
        # controller on: 434 of 1162 fill queries had our price != the touch,
        # with 28 spurious resets.
        #
        # So the queue is keyed on the price WE are resting at, in venue terms,
        # whenever the caller tells us what that is. `limit_px` is our own-terms
        # resting price; `resting_venue_price` converts the touch, so the same
        # conversion is applied here: UP rests at its own price, DOWN rests as a
        # YES sell at 1 - own.
        if limit_px is None:
            venue_px = resting_venue_price(row, side)
        else:
            venue_px = (D(str(limit_px)) if side == "up"
                        else D(1) - D(str(limit_px)))
        shown = level_displayed_qty(ladder, side, venue_px)
        if shown is None:
            # FAIL CLOSED. Our level is not displayed, so our position in its
            # queue is unknown and cannot be represented. No fill is granted --
            # never a guess, and never a silent fallback to the touch level.
            return D(0), None      # queue position not establishable
        st = None if qstate is None else qstate.get(side)
        if st is None or st.get("px") != venue_px:
            ahead = shown          # we JOIN here, behind everything displayed
        else:
            ahead = st["ahead"]
            if shown < ahead:
                ahead = shown      # the level shrank; never invent queue
        ahead = ahead - dlt        # attributable volume eats the queue
        if ahead > 0:
            if qstate is not None:
                qstate[side] = {"px": venue_px, "ahead": ahead}
            return D(0), None      # still queued behind displayed size
        avail = -ahead             # the surplus reaches us
        if qstate is not None:
            qstate[side] = {"px": None, "ahead": D(0)}
    if avail <= 0:
        return D(0), None
    qty = clip if clip < avail else avail
    return qty, (px if limit_px is None else D(str(limit_px)))


def settlement_independent_floor(bk: "Book", *, resting=None) -> D:
    """The P&L this portfolio is GUARANTEED, whichever way the market settles.

    If UP settles, the up leg pays $1/contract and the down leg pays nothing; if
    DOWN settles, the reverse. So across both outcomes the payout is at least
    min(q_up, q_down), and

        floor = min(q_up, q_down) - cash_paid - fees_already_incurred

    is attainable by DOING NOTHING and holding to settlement. That is what makes
    it settlement-independent: it is not a mark, and it needs no future fill, no
    future price and no knowledge of the outcome. `locked` is already realized
    completion margin and is carried in the same terms.

    OUTSTANDING ORDERS ARE CHARGED CONSERVATIVELY. A resting acquisition may
    still fill, and a fill can only add cost on one side, so every resting order
    is assumed to fill at its own resting price and its cost is subtracted while
    its contribution to `min` is ignored. That can only understate the floor,
    which is the direction a floor must err in.
    """
    m = min(bk.q["up"], bk.q["down"])
    floor = m - (bk.c["up"] + bk.c["down"]) - bk.fees + bk.locked
    for side in ("up", "down"):
        st = (resting or {}).get(side) or {}
        px, qty = st.get("px"), st.get("qty")
        if px is not None and qty:
            floor -= D(str(px)) * D(str(qty))
    return floor


def run_window(rows: Sequence[Dict[str, Any]], *, clip: D, theta: Optional[D],
               variant: str = "A", queue_aware: bool = False,
               fill_model: Optional[str] = None,
               winner: Optional[str] = None,
               residual_cap_k: D = D(2), hold_s: float = 60.0,
               ladder_max_age_s: Optional[float] = None,
               respect_live_bbo: bool = False,
               fill_prob: Optional[float] = None,
               seed: int = 0,
               atomic_recycle: bool = False,
               complement_bias_c: Optional[D] = None,
               profit_lock_usd: Optional[D] = None,
               floor_guard: bool = False
               ) -> Dict[str, Any]:
    """Forward walk of ONE window. No settlement is consulted until settle().

    variant A: complete, then continue normally.
    variant B: complete, then block same-side rebuild for `hold_s`.
    variant C: complete, then resume only from a genuinely flat book.
    theta None means B' baseline: acquire only, never complete.

    ANALYSIS-ONLY ladder hygiene, both default OFF so the authoritative result
    is unchanged:
      ladder_max_age_s -- a completion may use only a ladder at least this
        fresh. Beyond it the ladder is treated as absent. Nothing is ever
        interpolated.
      respect_live_bbo -- drop ladder levels priced BETTER than the
        contemporaneous BBO touch, so a stale favourable ladder cannot be used
        when a newer BBO contradicts it.

    CHALLENGERS, both default OFF so the frozen arm is byte-identical:

      complement_bias_c -- COMPLEMENT_BIAS. Suppress SAME-SIDE acquisition while
        the held side's own price has risen at least this many cents above its
        basis. Measured independently from L5's raw corpus (66,949 BTC15m states,
        `src/l5_sequence_corpus.py`): once the held side has risen, his next fill
        is the COMPLEMENT 0.613 / 0.623 / 0.591 of the time in the +2..+5c,
        +5..+10c and >=+10c buckets, while after a FALL he adds to the same side
        ~0.59-0.62. This reproduces the canonical RESPONSE_CURVE from raw data.
        It is inventory-and-move conditioned and symmetric -- NOT a directional
        forecast, and not a stop.

      floor_guard -- CHANNEL_A. A passive add that LOWERS the
        settlement-independent floor is placed only when its price is BELOW the
        current average basis on that side. The window's first add is exempt,
        since no basis exists yet.

        Why those two clauses and no others. The floor is
        min(q_up,q_down) - cost - fees, so a clip of n at p on the DEFICIENT side
        moves it by +n*(1-p) and the same clip on the SURPLUS or BALANCED side
        moves it by -n*p. "Floor-decreasing" is therefore an identity of the book
        state, not a judgement. Two independent measurements over L5's 66,949
        BTC15m states say he does not take the version of that trade our clone
        takes: among his 33,805 floor-decreasing adds, 64.3% are BELOW his own
        held-side basis (median -15.1c) against 34.9% above (median +10.6c); and
        he acts from a BALANCED book in only 627 states, 0.94%, while every one
        of our largest floor-destroying adds comes from a balanced book.

        No pair-cost cap, no theta, no directional forecast, no settlement.

      profit_lock_usd -- PROFIT_LOCK_5. Latch once the portfolio's WORST-CASE
        executable terminal P&L across BOTH settlement outcomes reaches this
        figure, then stop opening/extending. See `settlement_independent_floor`:
        the floor is min(q_up, q_down) - cost - fees, which is exactly the P&L
        guaranteed by holding to settlement whichever way it resolves. No
        settlement outcome, future price or future fill is consulted.
    """
    bk = Book()
    locked_at = None                  # (t, floor, inventory) once latched
    lock_floor = None
    floor_trace = []
    # ANALYSIS-ONLY. fill_prob thins the modelled passive acquisitions to answer
    # "what fill rate does the result actually need?". Default None = no thinning.
    _rnd = None
    if fill_prob is not None:
        import random as _random
        _rnd = _random.Random(seed)
    qstate: Dict[str, Any] = {}
    blocked_until = 0.0
    need_flat = False
    max_clip_seen = D(0)
    ladder = {}                       # last seen ladder per slug
    t0 = None
    for row in rows:
        kind = row.get("kind")
        if kind == "book":
            ladder = row
            continue
        if kind != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        ts = row.get("t")
        now = _epoch(ts)
        if t0 is None:
            t0 = now

        # ---- completion decision FIRST, on the residual held right now
        r = bk.residual()
        if theta is not None and r != 0:
            held = "up" if r > 0 else "down"
            comp = complement(held)
            want = abs(r)
            bk.eligible_states += 1
            use_ladder = ladder or None
            if use_ladder is not None and ladder_max_age_s is not None:
                age = now - _epoch(use_ladder.get("t"))
                if age < 0 or age > ladder_max_age_s:
                    use_ladder = None          # too stale to stand behind
            lv = buy_levels(use_ladder, comp) if use_ladder else []
            if lv and respect_live_bbo:
                live = live_touch_own(row, comp)
                if live is not None:
                    # a newer BBO contradicts any better-priced stale level
                    lv = [l for l in lv if _d(l[0]) is not None
                          and _d(l[0]) >= live]
            if lv:
                bk.attempted_states += 1
            basis = bk.avg(held)
            # MARGINAL, level by level, on the CONTEMPORANEOUS ladder only.
            n, cost, fee, depth_limited = marginal_complete(
                lv, want, basis, theta)
            if depth_limited:
                bk.missed_qualifying += 1
                bk.unknown_events += 1
            if n > 0:
                bk.executable_states += 1
                bk.add(comp, n, cost, maker=False, fee=fee)
                # `locked` is GROSS of the completion fee. net subtracts
                # self.fees once, and self.fees already carries this fee, so
                # netting it here too would charge the taker fee TWICE.
                bk.locked += n * D(1) - n * basis - cost
                bk.gross_margin += n * D(1) - n * basis - cost
                # the reported edge stays the NET marginal edge per contract
                bk.edges.append((n * D(1) - n * basis - cost - fee) / n)
                bk.q[held] -= n
                bk.c[held] -= n * basis
                bk.q[comp] -= n
                bk.c[comp] -= cost
                bk.completions += 1
                bk.completed_qty += n
                bk.completion_cost += cost + fee
                # ATOMIC_RECYCLE DIAGNOSTIC (analysis only, default OFF).
                # State consistency, NOT a new rule and NOT a predictor: a
                # completion that retires aged inventory also invalidates the
                # passive acquisition orders that were resting on the strength
                # of the pre-completion inventory state. Clearing the queue
                # state cancels them, so the next decision must re-join at the
                # back of whatever level it then chooses.
                if atomic_recycle and qstate:
                    bk.recycle_cancels += len(
                        [k for k, v in qstate.items()
                         if v and v.get("px") is not None])
                    qstate.clear()
                if variant == "B":
                    blocked_until = now + hold_s
                elif variant == "C":
                    need_flat = True

        # ---- PROFIT_LOCK (challenger, default OFF) -----------------------
        # Causal and settlement-independent. Evaluated at every action
        # timestamp on contemporaneous state only.
        if profit_lock_usd is not None and locked_at is None:
            fh = settlement_independent_floor(bk, resting=qstate)
            secured_by = None
            if fh >= profit_lock_usd:
                # already guaranteed by holding: no trade is needed, so the
                # cheapest way to preserve the floor is to stop adding risk.
                secured_by = "HOLD"
                new_floor = fh
            else:
                # A completion can RAISE the floor, because buying n of the
                # deficient side for `cost` adds n to min() and costs cost+fee.
                # Priced on the CONTEMPORANEOUS ladder, depth-limited, with NO
                # theta gate -- theta governs opportunistic completion, this is
                # a floor-securing action and is only taken when it actually
                # secures the floor.
                r2 = bk.residual()
                new_floor, n2, cost2, fee2 = fh, D(0), D(0), D(0)
                if r2 != 0 and ladder:
                    held2 = "up" if r2 > 0 else "down"
                    comp2 = complement(held2)
                    n2, cost2, _short = walk_ladder(
                        buy_levels(ladder, comp2), abs(r2))
                    if n2 > 0:
                        fee2 = taker_fee(cost2 / n2, n2)
                        new_floor = fh + n2 - cost2 - fee2
                if new_floor >= profit_lock_usd and n2 > 0:
                    bk.add(comp2, n2, cost2, maker=False, fee=fee2)
                    secured_by = "COMPLETE"
            # CHANNEL A vs B. The floor above is EXECUTABLE. This is the
            # ordinary mark-to-market at the contemporaneous mid, which is what
            # a naive reading would call "+$5 in the window" -- it is a MARK,
            # it is not settlement-independent, and it can evaporate.
            bid, ask = _d(row.get("bid")), _d(row.get("ask"))
            mid = ((bid + ask) / 2) if (bid is not None and ask is not None) \
                else (bid if bid is not None else ask)
            mtm = None
            if mid is not None:
                mtm = (bk.q["up"] * mid + bk.q["down"] * (D(1) - mid)
                       - bk.c["up"] - bk.c["down"] - bk.fees + bk.locked)
            floor_trace.append({
                "t": ts, "floor": fh, "mark_to_market": mtm,
                "floor_after_securing_action": new_floor,
                "locked": secured_by is not None, "via": secured_by,
            })
            if secured_by is not None:
                locked_at = ts
                lock_floor = settlement_independent_floor(bk)
                bk.lock_via = secured_by
                bk.lock_inventory = {"up": bk.q["up"], "down": bk.q["down"]}
                bk.lock_tte_s = None
                # cancel every resting acquisition: they are new risk
                bk.lock_cancels = len([k for k, v in qstate.items()
                                       if v and v.get("px") is not None])
                qstate.clear()
        if locked_at is not None:
            # no OPEN, no ACQUIRE, no EXTEND for the rest of the window
            continue

        # ---- acquisition: two-sided resting, one clip per side
        if need_flat and bk.residual() != 0:
            continue
        need_flat = False
        for side in ("up", "down"):
            # ---- COMPLEMENT_BIAS (challenger, default OFF) ----------------
            # While the held side's own price has risen at least
            # complement_bias_c above its basis, do not add to that side. L5's
            # raw corpus says the complement is his majority action in exactly
            # that state; our unconditional two-sided quoting has no such
            # asymmetry, which is how 2245z bought UP at 0.44, 0.77, 0.76, 0.89
            # and 0.98 while UP climbed. Symmetric and inventory-conditioned:
            # after a FALL the same side stays allowed, as his corpus also says.
            if complement_bias_c is not None:
                rb = bk.residual()
                held_b = "up" if rb > 0 else ("down" if rb < 0 else None)
                if held_b is not None and side == held_b:
                    own_now = live_touch_own(row, held_b)
                    basis_b = bk.avg(held_b)
                    if (own_now is not None and basis_b > 0
                            and own_now - basis_b >= complement_bias_c):
                        continue
            if variant == "B" and now < blocked_until:
                rr = bk.residual()
                if rr != 0 and ((rr > 0) == (side == "up")):
                    continue
            qty, px = maker_fill(row, side, clip, queue_aware=queue_aware,
                                 fill_model=fill_model, ladder=ladder or None,
                                 qstate=qstate)
            if qty <= 0 or px is None:
                continue
            if _rnd is not None and _rnd.random() > fill_prob:
                continue           # this modelled fill did not happen
            # ---- CHANNEL_A floor guard (challenger, default OFF) ---------
            if floor_guard:
                rr0 = bk.residual()
                held0 = "up" if rr0 > 0 else ("down" if rr0 < 0 else None)
                # deficient side => floor-increasing => always allowed
                deficient0 = held0 is not None and side != held0
                if not deficient0:
                    basis0 = bk.avg(side)
                    if basis0 > 0 and px >= basis0:
                        bk.floor_guard_blocks = getattr(
                            bk, "floor_guard_blocks", 0) + 1
                        bk.floor_guard_blocked_usd = getattr(
                            bk, "floor_guard_blocked_usd", D(0)) + qty * px
                        continue
            # B' RESIDUAL_CAP_k2 / PROSPECTIVE_NET, unchanged
            rr = bk.residual()
            signed = qty if side == "up" else -qty
            after = rr + signed
            if rr != 0 and abs(after) > abs(rr):
                mc = max_clip_seen if max_clip_seen > qty else qty
                if abs(after) > residual_cap_k * mc:
                    continue
            bk.add(side, qty, qty * px, maker=True)
            if qty > max_clip_seen:
                max_clip_seen = qty
    out = bk.settle(winner)
    out["window_s"] = None
    out["locked_at"] = locked_at
    out["lock_floor"] = lock_floor
    out["floor_trace"] = floor_trace
    out["peak_floor_seen"] = (max((f["floor"] for f in floor_trace))
                              if floor_trace else None)
    _m = [f["mark_to_market"] for f in floor_trace
          if f.get("mark_to_market") is not None]
    out["peak_mark_to_market_seen"] = max(_m) if _m else None
    return out


def _epoch(ts) -> float:
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def marginal_complete(levels: Sequence[Sequence[str]], want: D, basis: D,
                      theta: D) -> Tuple[D, D, D, bool]:
    """Take ONLY the contracts whose MARGINAL economics clear theta.

    Holding `want` contracts at `basis`, walk the complement ladder in price
    order. At each level price p the marginal pair cost of one more contract is
    basis + p + per-contract taker fee at p. Take from the level while that is
    <= theta; stop at the first level that fails, since levels ascend in price
    and every later one is worse.

    Returns (qty, cost, fee, depth_limited). `qty` may be a strict fraction of
    `want`: completing only what the book actually pays for is the point, and a
    blended average would hide the levels that do not.

    `depth_limited` marks the case where every affordable level was exhausted
    and residual remains -- economics said yes, depth said no. That is a MISSED
    QUALIFYING STATE, not a rejection on economics, and is counted separately.
    """
    qty = D(0)
    cost = D(0)
    fee = D(0)
    affordable_exhausted = False
    for lvl in levels or []:
        if qty >= want:
            break
        px = _d(lvl[0] if len(lvl) > 0 else None)
        avail = _d(lvl[1] if len(lvl) > 1 else None)
        if px is None or avail is None or avail <= 0:
            continue
        if not (D(0) < px < D(1)):
            continue
        unit_fee = THETA_TAKER * px * (D(1) - px)
        if basis + px + unit_fee > theta:
            break                      # this level and all worse ones fail
        take = want - qty
        if take > avail:
            take = avail
            affordable_exhausted = True
        else:
            affordable_exhausted = False
        qty += take
        cost += take * px
        fee += take * unit_fee
    return qty, cost, fee, (qty < want and affordable_exhausted)
