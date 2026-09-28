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

from decimal import Decimal as D, InvalidOperation
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
    behind, so no intent is produced at all."""


class NativeEngine:
    """The frozen policy, driven by authenticated book state."""

    def __init__(self, *, clip: D = CLIP, theta: D = THETA,
                 variant: str = VARIANT, hold_s: float = HOLD_S,
                 residual_cap_k: D = RESIDUAL_CAP_K,
                 profit_lock_usd: D = PROFIT_LOCK_USD,
                 charge_resting: bool = False,
                 prospective_cap: bool = True,
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
        bk.q[held] -= n
        bk.c[held] -= n * basis
        bk.q[comp] -= n
        bk.c[comp] -= cost
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
        if not usable_book(row):
            raise StaleBook("book is not usable: state=%r status=%r"
                            % (row.get("state"), row.get("status")))
        bid, ask = S._d(row.get("bid")), S._d(row.get("ask"))
        if bid is None and ask is None:
            raise StaleBook("unreadable book: neither side quoted")
        # NOT a two-sided requirement. Frozen decides PER SIDE -- `maker_fill`
        # reads `bid` for UP and `ask` for DOWN and independently declines the
        # side it cannot price -- so a one-sided book still quotes the side that
        # IS quoted. Requiring both here silently dropped whole observations
        # (measured: 0045z 00:59:07, one clip of DOWN at 0.99) and that is a
        # policy change, not a safety margin. Freshness and disconnect remain
        # the FEED's job, upstream, where MAX_AGE_S already fails closed.
        now = S._epoch(row.get("t"))
        out: List[Intent] = []

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
            if self.prospective_cap and not self._cap_permits(side, self.clip):
                continue
            cur = self.resting.get(side)
            if cur and D(str(cur["px"])) == px and D(str(cur["qty"])) == self.clip:
                continue              # already resting exactly there
            if cur:
                out.append(Intent(CANCEL, side=side, reason="REQUOTE"))
            out.append(Intent(REST, side=side, price=px, qty=self.clip,
                              maker=True, reason="TWO_SIDED_OUR_TOUCH"))
            self.resting[side] = {"px": px, "qty": self.clip}
        return out

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
