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

    def residual(self) -> D:
        return self.q["up"] - self.q["down"]

    def avg(self, side: str) -> D:
        return (self.c[side] / self.q[side]) if self.q[side] > 0 else D(0)

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
            self.fees += taker_fee(cost / qty, qty) if fee is None else fee
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
        }


def maker_fill(row: Dict[str, Any], side: str, clip: D, *,
               queue_aware: bool = False,
               fill_model: Optional[str] = None,
               ladder: Optional[Dict[str, Any]] = None,
               qstate: Optional[Dict[str, Any]] = None
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
    if own_price(side, last) > px:
        return D(0), None          # nothing printed at or through our price

    if model == "optimistic":
        avail = dlt
    elif model == "pessimistic":
        ahead = _d(row.get("bid_shares") if side == "up"
                   else row.get("ask_shares")) or D(0)
        avail = dlt - ahead
    else:
        venue_px = resting_venue_price(row, side)
        shown = level_displayed_qty(ladder, side, venue_px)
        if shown is None:
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
    return qty, px


def run_window(rows: Sequence[Dict[str, Any]], *, clip: D, theta: Optional[D],
               variant: str = "A", queue_aware: bool = False,
               fill_model: Optional[str] = None,
               winner: Optional[str] = None,
               residual_cap_k: D = D(2), hold_s: float = 60.0,
               ladder_max_age_s: Optional[float] = None,
               respect_live_bbo: bool = False,
               fill_prob: Optional[float] = None,
               seed: int = 0
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
    """
    bk = Book()
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
                if variant == "B":
                    blocked_until = now + hold_s
                elif variant == "C":
                    need_flat = True

        # ---- acquisition: two-sided resting, one clip per side
        if need_flat and bk.residual() != 0:
            continue
        need_flat = False
        for side in ("up", "down"):
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
