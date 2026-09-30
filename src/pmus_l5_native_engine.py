"""The frozen n=25 q50 PROFIT_LOCK_5 policy, as a LIVE decision module.

WHAT THIS IS. A port -- not a reinterpretation -- of the decision rules in
`pmus_completion_shadow.run_window`, the function that produced the frozen q50
PROFIT_LOCK_5 result of +6.1167/window SHADOW TRADING NET. Every rule below
names the frozen line it comes from, and the parity suite replays this module
against the same 25-window tape and reconciles to the frozen ledger.

WHAT IT IS NOT. It is not `new_l5_actions -> pmus_translate`. That executor
copies published L5 actions one at a time and rests at L5's translated price,
and its taker branch crosses at OUR touch with no basis, no theta and no
per-level test. It is preserved as the action-by-action behavioural comparison
lane and is PROHIBITED from this money path; `assert_no_translate_fallback()`
states that structurally.

THE ONE STRUCTURAL DIFFERENCE, stated up front. The frozen scorer decides and
fills in the same breath: `maker_fill` returns `(qty, px)` and the book is
updated immediately. Live, we can only ASK. So this module emits INTENTS and
its book moves only in `on_fill`, from authenticated venue fills. The frozen
`queue_aware` fill model is therefore NOT ported into the engine: it is a FILL
MODEL, not a decision rule, and live the venue answers that question. In replay
the simulated adapter supplies exactly `maker_fill(..., queue_aware)` so the
two are comparable rule-for-rule.

FROZEN PARAMETERS, fixed here so a caller cannot tune them:
    clip=50  theta=0.96  variant=B  hold_s=60  residual_cap_k=2
    profit_lock_usd=5  floor_guard=False  complement_bias=None
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_authenticated_observation as OBS
from src import pmus_completion_shadow as S

#: THE FROZEN ARM. `p1_profit_lock.py` calls run_window with exactly these.
#:
#: CLIP IS THE ONE CAPACITY CONTROL. It sets HOW MUCH each passive quote is
#: for and nothing else: no threshold, no eligibility test, no timing, no side
#: selection and no completion rule reads it. theta, variant, hold_s,
#: residual_cap_k and profit_lock_usd stay fixed here and are not settable.
#: Default 50, which is the frozen arm; unset means exactly clip-50 behaviour.
CLIP = D(50)
THETA = D("0.96")

#: venue price tick. The venue's own name for it is `orderPriceMinTickSize`
#: (read per market by the transport); every price in the recorded tape is on
#: a 2-decimal grid, so 0.01 is the observed grid and not a chosen parameter.
#: A derived complement price is floored to this grid, which can only make the
#: quote CHEAPER than the economics admit, never richer.
PRICE_TICK = D("0.01")
VARIANT = "B"
HOLD_S = 60.0
RESIDUAL_CAP_K = D(2)
PROFIT_LOCK_USD = D(5)
FLOOR_GUARD = False
COMPLEMENT_BIAS_C = None

REST, CANCEL, COMPLETE, SECURE = "REST", "CANCEL", "COMPLETE", "SECURE"
LOCK_CANDIDATE = "LOCK_CANDIDATE"


class Intent(dict):
    """One decision. Carries the state it was decided against, so the fill can
    be applied on the SAME basis the decision used."""

    def __init__(self, kind: str, **kw: Any) -> None:
        super().__init__(kind=kind, **kw)

    @property
    def kind(self) -> str:
        return str(self["kind"])


def usable_book(row: Mapping[str, Any]) -> bool:
    """Is this book state one a decision may be taken against?

    Accepts the tape's `state` and the live feed's `status`, which are two
    spellings of the same fact. Requires one of them to say so affirmatively:
    a row carrying neither is not usable, so a new row shape fails closed
    instead of being waved through.
    """
    st = str(row.get("state") or "").strip()
    if st:
        return st == "MARKET_STATE_OPEN"
    status = str(row.get("status") or "").strip()
    if status:
        # USABLE and ONE_SIDED both proceed; the per-side price check below
        # declines the side that is not quoted. Refusing ONE_SIDED outright
        # would re-break the per-side property parity already paid for --
        # 0045z lost a real 50-clip of DOWN at 0.99 to exactly that mistake.
        # NO_BOOK, and anything unrecognised, fail closed.
        return status in (str(OBS.USABLE), "ONE_SIDED")
    return False


class BadClip(ValueError):
    """A size we cannot stand behind. There is no default to fall back to --
    a probe that cannot read its own size does not trade."""


def parse_clip(raw, *, default: D = CLIP) -> D:
    """`L5_NATIVE_CLIP` -> a usable clip, or FAIL CLOSED.

    Unset or empty is the frozen default. Anything present must be a finite,
    strictly positive number; zero, negative, NaN, Infinity and junk all raise
    rather than silently becoming 50, because quietly trading a different size
    than the operator asked for is the failure this seat could most easily
    introduce.
    """
    if raw is None:
        return D(str(default))
    txt = str(raw).strip()
    if txt == "":
        return D(str(default))
    try:
        val = D(txt)
    except (InvalidOperation, ValueError, TypeError):
        raise BadClip("clip is not a number: %r" % (txt,))
    if not val.is_finite():
        raise BadClip("clip is not finite: %r" % (txt,))
    if val <= 0:
        raise BadClip("clip must be strictly positive: %r" % (txt,))
    return val


class StaleBook(RuntimeError):
    """Fail closed. A decision was asked for against a book we cannot stand
    behind, so no ACQUISITION intent is produced.

    `cancels` carries any cancel the engine still wants executed -- a stale
    complement whose cap the current inventory refuses. Cancelling needs no
    readable book and reduces risk, so it must not be lost when the tick is
    refused. A caller that ignores the attribute behaves exactly as before.
    """

    def __init__(self, *args) -> None:
        self.cancels = list(args[1]) if len(args) > 1 else []
        super().__init__(*args[:1])


class NativeEngine:
    """The frozen policy, driven by authenticated book state."""

    def __init__(self, *, clip: D = CLIP, theta: D = THETA,
                 variant: str = VARIANT, hold_s: float = HOLD_S,
                 residual_cap_k: D = RESIDUAL_CAP_K,
                 profit_lock_usd: D = PROFIT_LOCK_USD,
                 charge_resting: bool = False,
                 prospective_cap: bool = True,
                 complement_controller: bool = False,
                 residual_backstop: bool = False,
                 extend_requires_balance: bool = False,
                 lock_gate=None) -> None:
        self.clip, self.theta = D(str(clip)), D(str(theta))
        self.variant, self.hold_s = variant, float(hold_s)
        self.residual_cap_k = D(str(residual_cap_k))
        self.profit_lock_usd = D(str(profit_lock_usd))
        #: FROZEN BEHAVIOUR, and it is a no-op there. run_window passes
        #: `resting=qstate`, whose entries are {"px":..., "ahead":...} with NO
        #: "qty" key, so `settlement_independent_floor`'s resting charge never
        #: fires in the frozen arm. Defaulting False ports what the frozen code
        #: DOES; True applies the charge the docstring describes. Live this is
        #: a real decision and is escalated, never chosen silently.
        self.charge_resting = bool(charge_resting)
        #: frozen applies RESIDUAL_CAP_k2 to the qty that FILLED, because there
        #: decision and fill are one step. Live must decide first, so the cap
        #: also runs against the clip we intend to rest -- strictly tighter.
        #: Parity replay sets this False and applies the cap at the frozen point.
        self.prospective_cap = bool(prospective_cap)
        #: INVENTORY-AWARE COMPLEMENT CONTROLLER. Default False, which is the
        #: frozen policy byte-for-byte: step 4 rests both sides at OUR touch
        #: with no reference to the inventory being completed. The trace of the
        #: ten corrected-OOS tail windows showed what that costs: holding 20
        #: DOWN at basis 0.5700, the engine posted UP at 0.57, 0.40, 0.49,
        #: 0.37, ... 0.96, 0.97, 0.98, 0.99 -- following its own bid up as the
        #: market left, and completing pairs at 1.34 while the theta-gated
        #: TAKER walk was attempted 47 times and admitted nothing.
        #:
        #: True derives the maximum economically acceptable complement price
        #: from the held side's own all-in basis and posts `min(touch, cap)`.
        #:
        #: SAY THAT PRECISELY, because an earlier wording overclaimed it. The
        #: quote still FOLLOWS the touch while the touch is below the cap -- on
        #: the training tape that is most of the time -- so "it rests below the
        #: touch and waits" is false. What is true, and is what the tests pin,
        #: is that the quote can never become more aggressive than the cap: the
        #: cap is a pure function of present inventory, so an adverse touch move
        #: cannot raise it, and a complement fill can therefore never form a
        #: pair costing more than theta.
        #:
        #: It is NOT a global ceiling: when a side is not the deficient one --
        #: flat inventory, or the side we are already long -- the quote is the
        #: touch, exactly as frozen. The cap exists only where unmatched
        #: inventory exists to complete, which also means the FIRST pair formed
        #: from flat is uncapped; see tests/test_complement_controller.py's
        #: scope-limitation test, which quantifies that on the real tape.
        self.complement_controller = bool(complement_controller)
        #: repair the residual at market once it reaches the frozen bound.
        self.residual_backstop = bool(residual_backstop)
        #: refuse to EXTEND the long side while its complement is outstanding.
        self.extend_requires_balance = bool(extend_requires_balance)
        #: diagnostics for the controller. Counts only; no economics.
        self.complement_capped = 0
        self.complement_no_price = 0
        self.extend_refused_by_bound = 0
        self.residual_repairs = 0
        self.extend_refused_while_imbalanced = 0
        self.stale_complement_cancels = 0
        #: LIVE SAFETY OVERLAY SEAT. None -- the default, and what the parity
        #: replay uses -- latches immediately, exactly as frozen. Live, the
        #: overlay installs a gate that must run the cancel/reconcile
        #: transaction and re-derive the floor from AUTHENTICATED state before
        #: the latch is real. The gate can only DELAY or REFUSE a latch; it can
        #: never create one, so live can do LESS than the simulated arm when
        #: venue state is uncertain and never silently more.
        self.lock_gate = lock_gate
        #: true between a lock CANDIDATE and its confirmation or abort. No new
        #: acquisition risk is placed while this is open.
        self.lock_pending = False

        self.bk = S.Book()
        self.locked_at = None
        self.lock_floor = None
        self.lock_via = None
        self.lock_cancels = 0
        self.floor_trace: List[dict] = []
        self.blocked_until = 0.0
        #: False until an execution layer calls `sync_resting`. While False
        #: the engine keeps its own (optimistic) resting model, which is what
        #: the frozen replay harness -- which has no money path -- requires.
        self._resting_owned_by_execution = False
        self.need_flat = False
        self.max_clip_seen = D(0)
        #: our OWN resting orders, by side: {"px":..., "qty":...}. Live this is
        #: venue truth, not a queue model.
        self.resting: Dict[str, Optional[dict]] = {"up": None, "down": None}
        self.ladder: Optional[Mapping[str, Any]] = None

    # ------------------------------------------------- inventory, from FILLS

    def on_fill(self, side: str, qty: D, cost: D, *, maker: bool,
                fee: Optional[D] = None) -> None:
        """An AUTHENTICATED fill. The only thing that moves inventory."""
        self.bk.add(side, D(str(qty)), D(str(cost)), maker=maker, fee=fee)
        if maker and D(str(qty)) > self.max_clip_seen:
            self.max_clip_seen = D(str(qty))

    def on_completion_fill(self, intent: Intent, n: D, cost: D, fee: D) -> None:
        """A completion filled. Retires the matched pair EXACTLY as frozen.

        Ported line-for-line from run_window's completion block, including that
        `locked` is GROSS of the completion fee -- `settle()` subtracts
        `self.fees` once and that already carries this fee.
        """
        n, cost, fee = D(str(n)), D(str(cost)), D(str(fee))
        if n <= 0:
            return
        held, comp = intent["held"], intent["comp"]
        basis = D(str(intent["basis"]))
        bk = self.bk
        bk.executable_states += 1
        bk.add(comp, n, cost, maker=False, fee=fee)
        bk.locked += n * D(1) - n * basis - cost
        bk.gross_margin += n * D(1) - n * basis - cost
        bk.edges.append((n * D(1) - n * basis - cost - fee) / n)
        _qh, _qc = bk.q[held], bk.q[comp]
        bk.q[held] -= n
        bk.c[held] -= n * basis
        bk.q[comp] -= n
        bk.c[comp] -= cost
        # the attributed taker fee follows the shares off the book
        bk.release_fees(held, _qh, bk.q[held])
        bk.release_fees(comp, _qc, bk.q[comp])
        bk.completions += 1
        bk.completed_qty += n
        bk.completion_cost += cost + fee
        if self.variant == "B":
            self.blocked_until = intent["now"] + self.hold_s
        elif self.variant == "C":
            self.need_flat = True

    # ----------------------------------------------------------- the decision

    def floor(self) -> D:
        resting = self.resting if self.charge_resting else {
            s: ({"px": (v or {}).get("px")} if v else None)
            for s, v in self.resting.items()}
        return S.settlement_independent_floor(self.bk, resting=resting)

    def on_book(self, row: Mapping[str, Any],
                ladder: Optional[Mapping[str, Any]] = None,
                execute_taker=None) -> List[Intent]:
        """One decision pass over one authenticated book state.

        Rule order is the frozen order and is not rearranged:
          1 theta-marginal ECONOMIC_COMPLETE on the residual held right now
          2 PROFIT_LOCK: hold, or the floor-securing completion (no theta)
          3 once locked: cancel resting, no OPEN/ACQUIRE/EXTEND
          4 two-sided passive acquisition at OUR touch, one clip per side

        `execute_taker(intent) -> (n, cost, fee) | None` confirms a taker leg
        INLINE, which is where frozen applies it: the lock test and the
        acquisition that follow in this same row read the POST-completion book.
        Without it the pass stops after emitting the completion, because a
        decision that depends on a fill we have not confirmed is exactly the
        thing this engine must not guess -- fail closed, resume next book state.
        """
        if ladder is not None:
            self.ladder = ladder
        # TWO ROW SHAPES, ONE CONTRACT. The frozen tape rows carry
        # `state: MARKET_STATE_OPEN`; the LIVE feed's rows come from
        # `l5_window_actions.book_row`, which carries `status` with the
        # vocabulary USABLE / ONE_SIDED / NO_BOOK and no `state` key at all.
        #
        # Requiring `state` meant every live tick raised StaleBook: the 1830z
        # probe submitted nothing, refused nothing and proved nothing. The gate
        # was not too strict, it was reading a field the feed never sets. Both
        # shapes are accepted here and neither is weakened -- a row that states
        # neither is still refused.
        # CANCELS NEED NO READABLE BOOK. Revalidation of what is already
        # resting runs before both StaleBook gates and is attached to the
        # exception when the tick is refused, because an unreadable book is
        # exactly when a stale too-aggressive quote is most dangerous: the
        # engine will take no decision at all, and the old order would sit at
        # the venue until the book comes back.
        stale_cancels = self._revalidate_resting()
        if not usable_book(row):
            raise StaleBook("book is not usable: state=%r status=%r"
                            % (row.get("state"), row.get("status")),
                            stale_cancels)
        bid, ask = S._d(row.get("bid")), S._d(row.get("ask"))
        if bid is None and ask is None:
            raise StaleBook("unreadable book: neither side quoted",
                            stale_cancels)
        # NOT a two-sided requirement. Frozen decides PER SIDE -- `maker_fill`
        # reads `bid` for UP and `ask` for DOWN and independently declines the
        # side it cannot price -- so a one-sided book still quotes the side that
        # IS quoted. Requiring both here silently dropped whole observations
        # (measured: 0045z 00:59:07, one clip of DOWN at 0.99) and that is a
        # policy change, not a safety margin. Freshness and disconnect remain
        # the FEED's job, upstream, where MAX_AGE_S already fails closed.
        now = S._epoch(row.get("t"))
        out: List[Intent] = list(stale_cancels)

        # ---- 1. completion decision FIRST, on the residual held right now
        r = self.bk.residual()
        if r != 0:
            held = "up" if r > 0 else "down"
            comp = S.complement(held)
            want = abs(r)
            self.bk.eligible_states += 1
            lv = S.buy_levels(self.ladder, comp) if self.ladder else []
            if lv:
                self.bk.attempted_states += 1
            basis = self.bk.avg(held)
            n, cost, fee, depth_limited = S.marginal_complete(
                lv, want, basis, self.theta)
            if depth_limited:
                self.bk.missed_qualifying += 1
                self.bk.unknown_events += 1
            if n > 0:
                it = Intent(COMPLETE, side=comp, held=held, comp=comp,
                            qty=n, cost=cost, fee=fee, basis=basis,
                            now=now, taker=True,
                            limit_price=worst_admitted_price(
                                lv, want, basis, self.theta),
                            reason="THETA_MARGINAL_%s" % self.theta)
                out.append(it)
                if execute_taker is None:
                    return out              # unconfirmed: decide nothing more
                got = execute_taker(it)
                if got is None:
                    return out
                self.on_completion_fill(it, *got)

        # ---- 1b. RESIDUAL REPAIR BACKSTOP. Only with the controller on.
        #
        # THE MISSING HALF. `residual_cap_k` is already frozen in the config at
        # 2, and the engine already enforces it -- but only by REFUSING to add
        # more. It never repairs, so a residual that reaches the bound rides to
        # settlement and the whole basis is lost: 1415z is 20 contracts and
        # -$18.80 with no matched inventory at all. With the complement
        # controller waiting at a basis-derived price, that happens more often,
        # not less, which is exactly what the training cohort showed.
        #
        # L5's mature-era evidence is "much larger peak imbalance but FASTER
        # REPAIR". Repair is the half we never implemented. This closes it with
        # no new parameter: the trigger is the bound the config already
        # declares, and the action is the same no-theta walk the floor-securing
        # completion already uses.
        #
        # Theta is deliberately NOT applied here, and that is not a loophole:
        # residual-on-winner is established as approximately a coin flip, so
        # completing an unhedged residual at the market's own price is
        # expectation-neutral and variance-reducing. This is a risk action, not
        # an edge action, and it is the only place in the engine allowed to pay
        # above theta for a reason other than securing a locked floor.
        if self.residual_backstop and self.locked_at is None:
            rb = self.bk.residual()
            if rb != 0:
                bound = self.residual_cap_k * (
                    self.max_clip_seen if self.max_clip_seen > 0 else self.clip)
                if abs(rb) >= bound and self.ladder:
                    heldb = "up" if rb > 0 else "down"
                    compb = S.complement(heldb)
                    lvb = S.buy_levels(self.ladder, compb)
                    nb, costb, _shortb = S.walk_ladder(lvb, abs(rb))
                    if nb > 0:
                        feeb = S.taker_fee(costb / nb, nb)
                        itb = Intent(SECURE, side=compb, qty=nb, cost=costb,
                                     fee=feeb, now=now, taker=True,
                                     limit_price=worst_walked_price(lvb,
                                                                   abs(rb)),
                                     residual_before=rb, bound=bound,
                                     reason="RESIDUAL_BOUND_REPAIR_NO_THETA")
                        out.append(itb)
                        if execute_taker is None:
                            return out      # unconfirmed: change nothing
                        gotb = execute_taker(itb)
                        if gotb is None:
                            return out
                        nbf, costbf, feebf = gotb
                        self.bk.add(compb, nbf, costbf, maker=False,
                                    fee=feebf)
                        self.residual_repairs += 1

        # ---- 2. PROFIT_LOCK. Causal, settlement-independent.
        if self.locked_at is None:
            fh = self.floor()
            secured_by, new_floor = None, fh
            if fh >= self.profit_lock_usd:
                secured_by = "HOLD"
            else:
                r2 = self.bk.residual()
                n2 = cost2 = fee2 = D(0)
                if r2 != 0 and self.ladder:
                    held2 = "up" if r2 > 0 else "down"
                    comp2 = S.complement(held2)
                    n2, cost2, _short = S.walk_ladder(
                        S.buy_levels(self.ladder, comp2), abs(r2))
                    if n2 > 0:
                        fee2 = S.taker_fee(cost2 / n2, n2)
                        new_floor = fh + n2 - cost2 - fee2
                if new_floor >= self.profit_lock_usd and n2 > 0:
                    # NO THETA. This is a floor-SECURING action and is taken
                    # only because it actually secures the floor.
                    it2 = Intent(SECURE, side=comp2, qty=n2, cost=cost2,
                                 fee=fee2, now=now, taker=True,
                                 limit_price=worst_walked_price(
                                     S.buy_levels(self.ladder, comp2),
                                     abs(r2)),
                                 floor_before=fh, floor_after=new_floor,
                                 reason="FLOOR_SECURING_NO_THETA")
                    out.append(it2)
                    if execute_taker is None:
                        return out          # unconfirmed: latch nothing
                    got2 = execute_taker(it2)
                    if got2 is None:
                        return out
                    n2f, cost2f, fee2f = got2
                    self.bk.add(comp2, n2f, cost2f, maker=False, fee=fee2f)
                    secured_by = "COMPLETE"
            self.floor_trace.append({
                "t": row.get("t"), "floor": fh,
                "floor_after_securing_action": new_floor,
                "locked": secured_by is not None, "via": secured_by})
            if secured_by is not None:
                if self.lock_gate is not None:
                    # A CANDIDATE, not a latch. The overlay must cancel, confirm,
                    # reconcile any race fill and re-derive the floor from
                    # authenticated state before this becomes real.
                    self.lock_pending = True
                    out.append(Intent(LOCK_CANDIDATE, via=secured_by,
                                      floor_modelled=new_floor, now=now,
                                      t=row.get("t"),
                                      reason="AWAITING_CONFIRMED_CANCELLATION"))
                    return out
                self.locked_at = row.get("t")
                self.lock_via = secured_by
                self.lock_floor = S.settlement_independent_floor(self.bk)
                for side in ("up", "down"):
                    if self.resting.get(side):
                        self.lock_cancels += 1
                        out.append(Intent(CANCEL, side=side,
                                          reason="LOCKED_NO_NEW_RISK"))
                        self.resting[side] = None
                return out

        # ---- 3. locked: no OPEN, no ACQUIRE, no EXTEND for the rest
        if self.locked_at is not None:
            return out
        if self.lock_pending:
            # mid-transaction. Exposure may still be resting at the venue and
            # a race fill may still be in flight, so no new risk goes out.
            return out

        # ---- 4. acquisition: two-sided resting, one clip per side, OUR touch
        if self.need_flat and self.bk.residual() != 0:
            return out
        self.need_flat = False
        for side in ("up", "down"):
            if self.variant == "B" and now < self.blocked_until:
                rr = self.bk.residual()
                if rr != 0 and ((rr > 0) == (side == "up")):
                    # HOLD MEANS NOT RESTING. Frozen `continue`s before
                    # `maker_fill`, so during the hold that side cannot fill at
                    # all. Leaving our order in the book would keep filling it
                    # and is a different policy -- measured on 1900z and 2345z.
                    if self.resting.get(side):
                        out.append(Intent(CANCEL, side=side,
                                          reason="VARIANT_B_HOLD"))
                        self.resting[side] = None
                    continue          # same-side rebuild held for hold_s
            own = bid if side == "up" else ask
            if own is None:
                continue              # this side is not quoted; the other may be
            px = own if side == "up" else (D(1) - own)
            if px <= 0 or px >= 1:
                continue
            if self.complement_controller:
                cap = self.complement_cap(side)
                if cap is None:
                    # NOT a complement. This is an OPEN (flat) or an EXTEND of
                    # the side we are already long. The price is the touch,
                    # exactly as frozen -- the controller adds no ceiling here.
                    # The only discipline that applies to an EXTEND is the
                    # residual bound already frozen in the config
                    # (residual_cap_k), evaluated BEFORE the quote goes out
                    # rather than after the venue fills it. L5 evidence makes
                    # same-side EXTEND first-class (P(EXTEND|EXTEND) ~ 0.695 in
                    # the mature era), so nothing here suppresses it; the bound
                    # is the "explicit risk bounds" the operating model already
                    # names, applied at the point a live account must apply it.
                    _rr = self.bk.residual()
                    _is_extend = _rr != 0 and ((_rr > 0) == (side == "up"))
                    if self.extend_requires_balance and _is_extend:
                        # WAIT MEANS WAIT FOR THE COMPLEMENT, not "keep buying
                        # the side we already hold". The sequence L5 exhibits is
                        # OPEN -> EXTEND -> WAIT -> COMPLETE; the wait state is
                        # a wait for the other leg. Adding to the long side
                        # while its complement is unfilled raises the very
                        # basis the complement has to clear, so each extend
                        # makes its own completion harder. Nothing here
                        # suppresses EXTEND in general -- from flat, and after
                        # a completion returns us to balance, EXTEND is
                        # untouched and first-class.
                        self.extend_refused_while_imbalanced += 1
                        if self.resting.get(side):
                            out.append(Intent(CANCEL, side=side,
                                              reason="AWAITING_COMPLEMENT"))
                            self.resting[side] = None
                        continue
                    if (_rr != 0
                            and not self._cap_permits(side, self.clip)):
                        self.extend_refused_by_bound += 1
                        if self.resting.get(side):
                            out.append(Intent(CANCEL, side=side,
                                              reason="RESIDUAL_BOUND"))
                            self.resting[side] = None
                        continue
                elif cap < PRICE_TICK:
                    # the inventory is already so expensive that NO price on
                    # the grid completes it acceptably. Do not quote. Waiting
                    # is the action; the theta-gated taker walk in step 1 keeps
                    # testing the real ladder every tick and will take the
                    # completion the moment the book offers one.
                    self.complement_no_price += 1
                    if self.resting.get(side):
                        out.append(Intent(CANCEL, side=side,
                                          reason="NO_ACCEPTABLE_COMPLEMENT"))
                        self.resting[side] = None
                    continue
                elif px > cap:
                    # THE CORRECTION. The touch is richer than this inventory
                    # can afford. Rest at the acceptable price and WAIT for the
                    # market to come to us, instead of chasing it upward into
                    # progressively worse pair economics.
                    px = cap
                    self.complement_capped += 1
            if self.prospective_cap and not self._cap_permits(side, self.clip):
                continue
            cur = self.resting.get(side)
            if cur and D(str(cur["px"])) == px and D(str(cur["qty"])) == self.clip:
                continue              # already resting exactly there
            if cur:
                out.append(Intent(CANCEL, side=side, reason="REQUOTE"))
            _reason = "TWO_SIDED_OUR_TOUCH"
            if self.complement_controller:
                _c = self.complement_cap(side)
                if _c is not None and px == _c and (
                        (own if side == "up" else (D(1) - own)) > _c):
                    _reason = "COMPLEMENT_AT_BASIS_CAP"
            out.append(Intent(REST, side=side, price=px, qty=self.clip,
                              maker=True, reason=_reason))
            if not self._resting_owned_by_execution:
                # NO EXECUTION LAYER IS PRESENT. The frozen replay harness
                # drives this engine directly with no money path and no
                # `live_orders` to adopt, so the engine must keep its own
                # model there or frozen parity changes -- and frozen parity
                # is the reference, not a thing to renegotiate. Ownership
                # transfers only when something actually takes it.
                self.resting[side] = {"px": px, "qty": self.clip}
            # OTHERWISE NOT HERE. Creating an Intent is a
            # REQUEST, not an order. This line used to record the quote as
            # resting before the money path had established that anything
            # existed, so a blocked, rejected or never-submitted intent left
            # the engine believing in a quote that was never placed. R5
            # measured the cost: of 416 ticks after its last fill, 271
            # (65.1%) emitted NO INTENT because `cur["px"] == px` matched a
            # phantom. `resting` is now owned by the execution layer and
            # adopted through `sync_resting`.
        return out

    def sync_resting(self, live: Mapping[str, Any]) -> None:   # noqa: D401
        """Adopt the EXECUTION layer's authoritative view of what rests.

        `live` is the money path's `live_orders`: populated only from a
        successful submit that returned a venue order id, and cleared when
        the venue says that order is terminal. An order that was blocked
        before submit, refused by preview, rejected by the venue, cancelled
        or expired is absent from it, and is therefore absent here.

        This does NOT weaken duplicate protection. A genuinely live order
        still appears, still suppresses a same-price re-quote, and the money
        path's own `_ensure_side_clear` remains the authoritative gate that
        refuses a second same-side order. UNKNOWN ownership never reaches
        this point: `on_book_state` halts the tick before the engine is
        consulted, so an uncertain order is never treated as absent.
        """
        # THE TRANSFER. From the first call on, this engine no longer
        # records its own intents as resting: the caller owns that state.
        self._resting_owned_by_execution = True
        for side in ("up", "down"):
            v = (live or {}).get(side)
            self.resting[side] = (None if not v else
                                  {"px": v["px"], "qty": v["qty"]})

    def _revalidate_resting(self) -> List[Intent]:
        """Cancel any resting complement whose price the current cap refuses.

        THE HOLE THIS CLOSES. The cap used to be applied only where a quote was
        (re)priced, and step 4 has several `continue` paths that skip a side
        without cancelling it -- `own is None`, when the venue shows no touch on
        that side, being the reachable one: the training tape has 228 bbo rows
        with no bid and 276 with no ask. So a fill that RAISED the held basis
        could tighten the cap while an order rested at the old, looser one, and
        the next tick would skip the side and leave it there. Replay could not
        fill it, but live the order is at the venue: hold 10 DOWN at basis 0.30,
        rest UP at 0.66, take a DOWN fill that moves the basis to 0.60, and the
        stale 0.66 forms a pair at 1.26 -- precisely what the controller exists
        to prevent.

        Consults NO market data. A cancel is always available even when a
        replacement price is not, which is why this runs ahead of the StaleBook
        gates rather than inside the quoting loop.
        """
        out: List[Intent] = []
        if not self.complement_controller:
            return out
        for side in ("up", "down"):
            cur = self.resting.get(side)
            if not cur:
                continue
            cap = self.complement_cap(side)
            if cap is None:
                continue                  # not a complement: nothing to check
            if D(str(cur["px"])) > cap:
                self.stale_complement_cancels += 1
                out.append(Intent(CANCEL, side=side,
                                  reason="CAP_TIGHTENED_STALE_QUOTE"))
                self.resting[side] = None
        return out

    def complement_cap(self, side: str) -> Optional[D]:
        """The richest own-terms price at which resting `side` still completes
        a pair at acceptable economics -- or None when `side` is not completing
        anything.

        `side` is a COMPLEMENT only when we hold more of the other side than of
        this one. Then every clip filled here matches against inventory already
        carried at `avg(other)`, and the pair costs `avg(other) + px`. A maker
        fill pays no taker fee at this venue (maker volume earns a rebate; the
        0.0695 * p * (1-p) charge is the taker's), so the acceptable price is

            cap = theta - avg(other)

        floored to the venue tick. Two consequences worth stating because they
        are the whole point:

        * the cap depends ONLY on inventory, never on the touch, so a market
          moving away can never raise it. That is the defect this closes.
        * a pair bought at the cap costs at most theta, whatever the book does,
          because the basis used here is the ALL-IN basis: price paid plus any
          taker fee already paid on those shares. `avg()` alone excludes that
          fee (a taker fee never enters `c[side]`) and made this bound
          optimistic by up to ~$0.0174/share -- the review demonstrated a pair
          at 0.9686875 passing a bound of 0.96. `economic_basis` closes it.
    * the invariant is on the PAIR, not on the price, which is why this is
          not a global 0.96 price ceiling: an OPEN with no inventory behind it
          is not capped at all, and an EXTEND is not capped either. The quote
          itself is `min(touch, cap)` and does still track an improving touch;
          what it cannot do is exceed the cap.

        Returns None when the side is not deficient (no cap applies) and a
        non-positive Decimal when no acceptable price exists at all, which the
        caller must treat as "do not quote", not as "quote at zero".
        """
        held = "down" if side == "up" else "up"
        if self.bk.q[held] <= self.bk.q[side]:
            return None               # not a complement: OPEN or EXTEND
        cap = self.theta - self.bk.economic_basis(held)
        # floor to the venue grid; quantize(ROUND_FLOOR) keeps it no richer
        # than the economics admit even when the basis is a repeating decimal.
        return (cap / PRICE_TICK).to_integral_value(rounding=ROUND_FLOOR) \
            * PRICE_TICK

    def _cap_permits(self, side: str, qty: D) -> bool:
        """B' RESIDUAL_CAP_k2, applied PROSPECTIVELY.

        Frozen applies this to the qty that DID fill, because there decision and
        fill are one step. Live we must decide before the venue answers, so the
        test runs against the clip we intend to rest. That can only refuse more
        often than frozen, never less, and the parity suite pins the difference
        by applying it at the frozen point in replay.
        """
        rr = self.bk.residual()
        signed = qty if side == "up" else -qty
        after = rr + signed
        if rr != 0 and abs(after) > abs(rr):
            mc = self.max_clip_seen if self.max_clip_seen > qty else qty
            if abs(after) > self.residual_cap_k * mc:
                return False
        return True

    def confirm_lock(self, *, via: str, t: Any, floor: D) -> None:
        """The overlay proved it: cancellations confirmed, race fills booked,
        floor re-derived from authenticated state and still >= the threshold."""
        self.locked_at = t
        self.lock_via = via
        self.lock_floor = D(str(floor))
        self.lock_pending = False
        for side in ("up", "down"):
            if self.resting.get(side):
                self.lock_cancels += 1
                self.resting[side] = None

    def abort_lock(self, reason: str) -> None:
        """The transaction could not be established. NOT locked, and the
        resting book is whatever the venue says it is -- not what we hoped."""
        self.lock_pending = False
        self.lock_abort_reason = reason

    def settle(self, winner: Optional[str]) -> Dict[str, Any]:
        out = self.bk.settle(winner)
        out["locked_at"] = self.locked_at
        out["lock_floor"] = self.lock_floor
        out["lock_via"] = self.lock_via
        out["floor_trace"] = self.floor_trace
        out["peak_floor_seen"] = (max(f["floor"] for f in self.floor_trace)
                                  if self.floor_trace else None)
        return out


def worst_admitted_price(levels, want: D, basis: D, theta: D) -> Optional[D]:
    """The WORST own-terms price the frozen theta walk actually admitted.

    `marginal_complete` returns a cost, and cost/qty is a VWAP. A limit set at
    the VWAP cannot reach the deepest level the decision admitted: 5 @ 0.30 plus
    5 @ 0.40 averages 0.35, and a 0.35 limit buys five contracts, not ten. The
    executable limit must be the worst ADMITTED level and no worse, so the order
    can take exactly what the frozen rule said yes to and nothing it said no to.

    This mirrors `marginal_complete`'s walk exactly -- same level order, same
    marginal test, same break -- and returns None when nothing was admitted.
    """
    qty, worst = D(0), None
    for lvl in levels or []:
        if qty >= want:
            break
        px = S._d(lvl[0] if len(lvl) > 0 else None)
        avail = S._d(lvl[1] if len(lvl) > 1 else None)
        if px is None or avail is None or avail <= 0:
            continue
        if not (D(0) < px < D(1)):
            continue
        unit_fee = S.THETA_TAKER * px * (D(1) - px)
        if basis + px + unit_fee > theta:
            break
        take = want - qty
        if take > avail:
            take = avail
        qty += take
        worst = px if worst is None or px > worst else worst
    return worst


def worst_walked_price(levels, want: D) -> Optional[D]:
    """The worst own-terms price `walk_ladder` consumes for the floor-securing
    completion, which has NO theta gate. Mirrors that walk exactly."""
    filled, worst = D(0), None
    for lvl in levels or []:
        if filled >= want:
            break
        px = S._d(lvl[0] if len(lvl) > 0 else None)
        qty = S._d(lvl[1] if len(lvl) > 1 else None)
        if px is None or qty is None or qty <= 0:
            continue
        take = want - filled
        if take > qty:
            take = qty
        filled += take
        worst = px if worst is None or px > worst else worst
    return worst


def assert_no_translate_fallback() -> None:
    """The generic touch-crossing taker fallback is PROHIBITED here.

    AST-BASED, not text. A text scan matched this module's own prose -- the
    same false positive this repo already fixed once in the forbidden-capability
    guard -- so the check reads the IMPORT GRAPH and the NAMES the code actually
    evaluates, and a docstring naming the banned module cannot trip or satisfy it.
    """
    import ast
    import pathlib

    banned = {"l5_faithful_translation", "pmus_translate", "new_l5_actions",
              "l5_window_actions"}
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    reached = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                reached.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                reached.update(node.module.split("."))
            for a in node.names:
                reached.add(a.name)
        elif isinstance(node, ast.Name):
            reached.add(node.id)
        elif isinstance(node, ast.Attribute):
            reached.add(node.attr)
    hit = sorted(banned & reached)
    if hit:
        raise AssertionError(
            "the money path reaches the L5-action translator: %s" % (hit,))
