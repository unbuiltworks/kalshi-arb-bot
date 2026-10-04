"""FAVORITE_DCA Variants A and B on the authenticated PMUS BTC15m tape.

Frozen by `analysis/l5/favorite_dca/PREREGISTRATION.md`. Historical replay
only: nothing here submits, fills, settles or realizes anything.

The venue has ONE book per window, the long (UP) side, so every order --
UP or DOWN, taker or resting -- is expressed in that book:

    taker UP    pays `ask`,      consumes `ask_qty`
    taker DOWN  pays `1 - bid`,  consumes `bid_qty`   (DOWN ask = 1 - UP bid)
    resting UP  bid at p         is an UP bid  at p
    resting DOWN bid at p        is an UP ask  at 1 - p

No touch = fill. A resting order fills only once its price IS the touch, then
only as trades on our side deplete the queue ahead of it, and a print
strictly through our price clears that queue.
"""
from __future__ import annotations
import argparse, collections, json, os, sys
from decimal import Decimal as D, getcontext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import pmus_fees as FEES                                 # noqa: E402

getcontext().prec = 28
TAPE = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl"
ZERO, ONE, TICK = D(0), D(1), D("0.01")
CADENCE_S = 60
ALLOC_USD = D(1)        # the frozen default; only the ENVELOPE study varies it
MIN_SPAN_S = 600
END_TOL_S = 30
MARK_TOL_S = 5
TERMINAL = {(D("0.9900"), D("0.9900")): "UP", (D("0.0100"), D("0.0100")): "DOWN"}


# ----------------------------------------------------------------- loading

def window_start(slug: str) -> int:
    """`cpc-btc-updown-15m-2026-10-02-1730z` -> epoch seconds."""
    import datetime as dt
    parts = slug.split("-")
    y, m, d, hhmmz = int(parts[4]), int(parts[5]), int(parts[6]), parts[7]
    t = dt.datetime(y, m, d, int(hhmmz[:2]), int(hhmmz[2:4]),
                    tzinfo=dt.timezone.utc)
    return int(t.timestamp())


def load(path: str):
    by = collections.defaultdict(lambda: {"book": [], "trade": []})
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            by[r["slug"]][r["kind"]].append(r)
    for w in by.values():
        w["book"].sort(key=lambda r: r["t"])
        w["trade"].sort(key=lambda r: r["t"])
    return by


def eligible(slug: str, w) -> tuple:
    """(ok, settlement_outcome, reason). Rule fixed in the preregistration."""
    b = w["book"]
    if not b:
        return False, None, "no_book"
    span = b[-1]["t"] - b[0]["t"]
    if span < MIN_SPAN_S:
        return False, None, "span_%.0fs" % span
    end = window_start(slug) + 900
    if abs(b[-1]["t"] - end) > END_TOL_S:
        return False, None, "ends_%.0fs_from_window_end" % (b[-1]["t"] - end)
    key = (D(b[-1]["bid"]), D(b[-1]["ask"]))
    if key not in TERMINAL:
        return False, None, "terminal_%s/%s" % key
    return True, TERMINAL[key], "ok"


# ----------------------------------------------------------------- state

class Port:
    """Aggregate UP/DOWN inventory. Never a list of independent pairs."""

    #: the tape shows TOP OF BOOK only, so "defensible liquidity" is the
    #: displayed touch depth and nothing deeper. The fraction of it a single
    #: order consumes is tracked, because a size that routinely eats most of
    #: the touch is no longer a price-taker at the displayed price and the
    #: replay would be flattering itself.
    

    def __init__(self):
        self.up_sh = self.dn_sh = ZERO
        self.up_cash = self.dn_cash = ZERO      # price paid, fees excluded
        self.fees = ZERO
        self.rebate_modelled = ZERO             # UNVERIFIED, never netted
        self.deployed = ZERO                    # cash + fees, cumulative
        self.max_deployed = ZERO
        self.max_residual = ZERO
        self.cash_in = ZERO                     # exit proceeds, net of fee
        self.depth_frac = []                    # per order, consumed/displayed
        self.realized_gross = ZERO              # exit proceeds BEFORE fee
        self.realized_basis = ZERO              # cost basis of exited shares
        self.max_net_capital = ZERO             # deployed - cash_in, peak

    # --- aggregates the preregistration requires after every purchase
    @property
    def matched(self):
        return min(self.up_sh, self.dn_sh)

    @property
    def residual(self):
        return self.up_sh - self.dn_sh          # +long UP, -long DOWN

    @property
    def acq_cost(self):
        return self.up_cash + self.dn_cash

    @property
    def eff_matched_cost(self):
        """Per matched share, the cost of the two legs that formed it, taken
        at each side's average cost. Undefined with no matched inventory."""
        m = self.matched
        if m <= 0:
            return None
        return (self.up_cash / self.up_sh) + (self.dn_cash / self.dn_sh)

    def add(self, side: str, sh: D, px: D, fee: D, rebate: D = ZERO):
        cost = sh * px
        if side == "UP":
            self.up_sh += sh; self.up_cash += cost
        else:
            self.dn_sh += sh; self.dn_cash += cost
        self.fees += fee
        self.rebate_modelled += rebate
        self.deployed += cost + fee
        self.max_deployed = max(self.max_deployed, self.deployed)
        self.max_residual = max(self.max_residual, abs(self.residual))
        self.max_net_capital = max(self.max_net_capital,
                                   self.deployed - self.cash_in)

    def sell(self, side: str, sh: D, px: D, fee: D):
        """Reduce one side at an executable price. The cost basis leaves at
        that side's AVERAGE cost, so the matched/directional split below is
        computed on the same basis throughout and cannot drift with order."""
        if side == "UP":
            avg = self.up_cash / self.up_sh if self.up_sh > 0 else ZERO
            self.up_sh -= sh
            self.up_cash -= sh * avg
        else:
            avg = self.dn_cash / self.dn_sh if self.dn_sh > 0 else ZERO
            self.dn_sh -= sh
            self.dn_cash -= sh * avg
        self.fees += fee
        self.cash_in += sh * px - fee
        self.realized_gross += sh * px
        self.realized_basis += sh * avg
        self.max_residual = max(self.max_residual, abs(self.residual))

    # --- valuation
    def exit_residual(self, bid: D, ask: D):
        """Executable proceeds from flattening the residual, net of fee.

        Long UP residual sells at `bid`. Long DOWN residual exits at the DOWN
        bid, `1 - ask`. Selling is a taker action and pays the taker fee.
        """
        r = self.residual
        if r == 0:
            return ZERO
        if r > 0:
            px, sh = bid, r
        else:
            px, sh = ONE - ask, -r
        return sh * px - FEES.taker_fee(px, sh)

    def mark(self, bid: D, ask: D):
        """Matched at 1.00 (riskless to hold) + residual at executable exit."""
        return self.matched * ONE + self.exit_residual(bid, ask)

    def full_liquidation(self, bid: D, ask: D):
        """Strict: unwind the matched leg too, at `1 - spread` per pair."""
        m = self.matched
        v = self.exit_residual(bid, ask)
        if m > 0:
            v += m * (bid + (ONE - ask))
            v -= FEES.taker_fee(bid, m) + FEES.taker_fee(ONE - ask, m)
        return v

    def settlement_value(self, outcome: str):
        return self.up_sh if outcome == "UP" else self.dn_sh


# ----------------------------------------------------------------- resting

class Resting:
    """One passive bid, modelled in UP-book terms. No touch = fill."""

    def __init__(self):
        self.side = None          # "UP" | "DOWN"
        self.px = None            # in the side's OWN terms
        self.sh = ZERO
        self.up_px = None         # the book level it occupies
        self.is_ask = False       # a DOWN bid rests on the UP ask side
        self.queue = None         # None until our level becomes the touch
        self.placed_t = None
        self.replacements = 0
        self.queue_at_arm = None

    def quote(self, side: str, px: D, sh: D, t: float):
        """Place or REPLACE. A changed price loses queue position."""
        if (self.side == side and self.px == px and self.sh > 0):
            return
        if self.side is not None:
            self.replacements += 1
        self.side, self.px, self.sh, self.placed_t = side, px, sh, t
        self.is_ask = (side == "DOWN")
        self.up_px = (ONE - px) if self.is_ask else px
        self.queue = None

    def cancel(self):
        self.side = self.px = self.up_px = None
        self.sh = ZERO
        self.queue = None
        self.queue_at_arm = None

    def on_book(self, bid: D, ask: D, bid_qty: D, ask_qty: D):
        """Queue-ahead is set the moment our level becomes the touch, from
        the depth observed there. It is never guessed for a level below."""
        if self.side is None or self.queue is not None:
            return
        if self.is_ask and ask == self.up_px:
            self.queue = ask_qty
            self.queue_at_arm = ask_qty
        elif (not self.is_ask) and bid == self.up_px:
            self.queue = bid_qty
            self.queue_at_arm = bid_qty

    def on_trade(self, px: D, qty: D, direction: str):
        """Returns shares filled. Only trades consuming OUR side count."""
        if self.side is None or self.queue is None:
            return ZERO
        ours = "buy" if self.is_ask else "sell"       # aggressor that hits us
        if direction not in ("buy", "sell") or direction != ours:
            return ZERO
        through = (px > self.up_px) if self.is_ask else (px < self.up_px)
        if through:
            self.queue = ZERO                          # the level was cleared
            return ZERO
        if px != self.up_px:
            return ZERO                                # away from our level
        if self.queue > 0:
            eaten = min(self.queue, qty)
            self.queue -= eaten
            qty -= eaten
        if qty <= 0:
            return ZERO
        fill = min(self.sh, qty)
        self.sh -= fill
        return fill


# ----------------------------------------------------------------- replay

def reservation(p: Port, fav: str) -> D:
    """1.00 - average cost of the favored side - taker fee already paid per
    favored share. Frozen in the preregistration; the modelled maker rebate
    is deliberately excluded."""
    sh = p.up_sh if fav == "UP" else p.dn_sh
    if sh <= 0:
        return D("-1")
    cash = p.up_cash if fav == "UP" else p.dn_cash
    fee_per = p.fees / (p.up_sh + p.dn_sh) if (p.up_sh + p.dn_sh) > 0 else ZERO
    return ONE - (cash / sh) - fee_per


def replay(slug: str, w, outcome: str, variant: str,
           alloc: D = ALLOC_USD) -> dict:
    """variant: "A" taker control, "B" taker + opposite resting,
    "Am" the maker variant of A -- the SAME $1 favored allocation, rested at
    the favored outcome's own best bid instead of taken, replaced each period,
    unfilled remainder cancelled at the next evaluation. Its fills obey the
    same queue/trade-through discipline as B's, so it is reported only where
    that evidence carries it."""
    books, trades = w["book"], w["trade"]
    start = window_start(slug)
    port = Port()
    rest = Resting()
    traj, allocs, fills_opp = [], [], []
    flips = skips = 0
    fav = None
    bt = [b["t"] for b in books]
    peak = {"pnl": None, "t": None}
    trough = ZERO
    max_dd = ZERO
    ti = 0

    import bisect
    marks = [start + k * CADENCE_S for k in range(1, 15)]
    for mark in marks:
        i = bisect.bisect_left(bt, mark)
        cand = [j for j in (i - 1, i) if 0 <= j < len(books)]
        best = min(cand, key=lambda j: abs(books[j]["t"] - mark), default=None)
        if best is None or abs(books[best]["t"] - mark) > MARK_TOL_S:
            skips += 1
            continue
        b = books[best]
        bid, ask = D(b["bid"]), D(b["ask"])
        bq, aq = D(b["bid_qty"]), D(b["ask_qty"])
        mid = (bid + ask) / 2
        new_fav = "UP" if mid > D("0.50") else ("DOWN" if mid < D("0.50")
                                                else None)
        if new_fav is None:
            skips += 1
        else:
            if fav is not None and new_fav != fav:
                flips += 1
            fav = new_fav
            if variant == "Am":
                # join the touch on the favored side: a resting UP bid at
                # `bid`, or a resting DOWN bid at `1 - ask` (the DOWN bid).
                mpx = bid if fav == "UP" else ONE - ask
                if D("0.01") <= mpx <= D("0.99"):
                    rest.quote(fav, mpx, alloc / mpx, b["t"])
                else:
                    rest.cancel()
                px = None
            else:
                px = ask if fav == "UP" else ONE - bid
                depth = aq if fav == "UP" else bq
                sh = (alloc / px)
            if px is not None and depth > 0:
                port.depth_frac.append(float(min(sh, depth) / depth))
            if px is not None and depth < sh:
                sh = depth                      # executable depth is the cap
            if px is not None and sh > 0 and D("0.01") <= px <= D("0.99"):
                fee = FEES.taker_fee(px, sh)
                port.add(fav, sh, px, fee)
                allocs.append({"t": b["t"], "side": fav, "px": str(px),
                               "sh": str(round(sh, 4)), "fee": str(fee),
                               "mode": "taker",
                               "up_sh": str(round(port.up_sh, 4)),
                               "dn_sh": str(round(port.dn_sh, 4)),
                               "up_cash": str(round(port.up_cash, 4)),
                               "dn_cash": str(round(port.dn_cash, 4)),
                               "matched": str(round(port.matched, 4)),
                               "residual": str(round(port.residual, 4)),
                               "deployed": str(round(port.deployed, 4)),
                               "eff_matched_cost": (
                                   None if port.eff_matched_cost is None
                                   else str(round(port.eff_matched_cost, 4))),
                               "mark": str(round(port.mark(bid, ask), 4))})
        if variant == "B" and fav is not None:
            opp = "DOWN" if fav == "UP" else "UP"
            res = reservation(port, fav)
            opp_ask = (ONE - bid) if opp == "DOWN" else ask
            cap = opp_ask - TICK
            q = min(res, cap)
            if q >= D("0.01") and res > 0:
                q = q.quantize(TICK)
                rest.quote(opp, q, alloc / q, b["t"])
            else:
                rest.cancel()

        # advance the tape to the next mark, letting the resting order live
        nxt = mark + CADENCE_S
        while ti < len(trades) and trades[ti]["t"] < nxt:
            tr = trades[ti]; ti += 1
            if variant == "A" or rest.side is None:
                continue
            # the book as of this trade, for the queue-arming test
            j = bisect.bisect_left(bt, tr["t"]) - 1
            if j >= 0:
                bb = books[j]
                rest.on_book(D(bb["bid"]), D(bb["ask"]), D(bb["bid_qty"]),
                             D(bb["ask_qty"]))
            got = rest.on_trade(D(tr["px"]), D(tr["qty"]), tr.get("dir"))
            if got > 0:
                side, fpx = rest.side, rest.px
                reb = FEES.maker_rebate(fpx, got)
                port.add(side, got, fpx, ZERO, rebate=reb)
                fills_opp.append({"t": tr["t"], "side": side, "px": str(fpx),
                                  "sh": str(round(got, 4)),
                                  "rebate_modelled": str(reb),
                                  "matched": str(round(port.matched, 4)),
                                  "residual": str(round(port.residual, 4)),
                                  "guaranteed_matched_value": str(
                                      round(port.matched, 4)),
                                  "acq_cost": str(round(port.acq_cost, 4)),
                                  "eff_matched_cost": (
                                      None if port.eff_matched_cost is None
                                      else str(round(port.eff_matched_cost,
                                                     4))),
                                  "deployed": str(round(port.deployed, 4)),
                                  # audit fields: a fill must be at OUR price
                                  # with the market's own touch there, behind
                                  # the depth that was displayed when our
                                  # level became the touch.
                                  "queue_at_arm": str(rest.queue_at_arm),
                                  "queue_remaining": str(rest.queue),
                                  "book_bid_at_fill": bb["bid"],
                                  "book_ask_at_fill": bb["ask"],
                                  "opposite_ask_at_fill": str(
                                      (ONE - D(bb["bid"])) if side == "DOWN"
                                      else D(bb["ask"])),
                                  "seconds_resting": round(
                                      tr["t"] - rest.placed_t, 2)})
                if rest.sh <= 0:
                    rest.cancel()

        # trajectory, sampled on every book between marks
        for j in range(len(books)):
            if not (mark <= books[j]["t"] < nxt):
                continue
            bb = books[j]
            bid2, ask2 = D(bb["bid"]), D(bb["ask"])
            mk = port.mark(bid2, ask2)
            pnl = mk - port.deployed
            traj.append({"t": bb["t"], "bid": bb["bid"], "ask": bb["ask"],
                         "mark": str(round(mk, 4)),
                         "pnl": str(round(pnl, 4)),
                         "matched": str(round(port.matched, 4)),
                         "residual": str(round(port.residual, 4)),
                         "deployed": str(round(port.deployed, 4))})
            if peak["pnl"] is None or pnl > D(peak["pnl"]):
                peak = {"pnl": str(round(pnl, 4)), "t": bb["t"],
                        "s_into_window": round(bb["t"] - start, 1)}
            trough = min(trough, pnl)
            if peak["pnl"] is not None:
                max_dd = max(max_dd, D(peak["pnl"]) - pnl)

    last = books[-1]
    bidL, askL = D(last["bid"]), D(last["ask"])
    settle = port.settlement_value(outcome)
    gross = settle - port.acq_cost
    net = settle - port.deployed
    return {
        "slug": slug, "variant": variant, "settlement_outcome": outcome,
        "allocations": len(allocs), "skipped_periods": skips,
        "leadership_flips": flips,
        "opposite_fills": len(fills_opp),
        "touch_depth_consumed_max": (str(round(max(port.depth_frac), 4))
                                     if port.depth_frac else None),
        "touch_depth_consumed_over_10pct": sum(
            1 for x in port.depth_frac if x > 0.10),
        "taker_orders": len(port.depth_frac),
        "opposite_replacements": rest.replacements,
        "up_shares": str(round(port.up_sh, 4)),
        "dn_shares": str(round(port.dn_sh, 4)),
        "up_cash": str(round(port.up_cash, 4)),
        "dn_cash": str(round(port.dn_cash, 4)),
        "matched_shares": str(round(port.matched, 4)),
        "residual_terminal": str(round(port.residual, 4)),
        "max_residual": str(round(port.max_residual, 4)),
        "acquisition_cost": str(round(port.acq_cost, 4)),
        "avg_cost_per_share": (str(round(port.acq_cost /
                                         (port.up_sh + port.dn_sh), 6))
                               if (port.up_sh + port.dn_sh) > 0 else None),
        "eff_matched_cost": (None if port.eff_matched_cost is None
                             else str(round(port.eff_matched_cost, 4))),
        "fees": str(round(port.fees, 4)),
        "maker_rebate_modelled_UNVERIFIED": str(round(port.rebate_modelled,
                                                      4)),
        "maker_rebate_verified": "0.0000",
        "max_capital": str(round(port.max_deployed, 4)),
        "capital_deployed_terminal": str(round(port.deployed, 4)),
        "settlement_value": str(round(settle, 4)),
        "gross_settlement_pnl": str(round(gross, 4)),
        "net_settlement_pnl": str(round(net, 4)),
        "peak_executable_pnl": peak["pnl"],
        "peak_t_s_into_window": peak.get("s_into_window"),
        "terminal_executable_pnl": str(round(port.mark(bidL, askL)
                                             - port.deployed, 4)),
        "full_liquidation_pnl_terminal": str(round(
            port.full_liquidation(bidL, askL) - port.deployed, 4)),
        "min_executable_pnl": str(round(trough, 4)),
        "max_drawdown_from_peak": str(round(max_dd, 4)),
        "trajectory": traj, "allocation_log": allocs,
        "opposite_fill_log": fills_opp,
    }


def replay_c(slug: str, w, outcome: str,
             alloc: D = ALLOC_USD, *, seed: bool = True,
             opposite_rest: bool = False, name: str = "C") -> dict:
    """TWO_SIDED_FAVORITE_TRAIL, frozen by PREREGISTRATION_C.md.

    Seed $1 on BOTH sides once, extend the favored side $1 per 60 s only
    while it is strengthening, and on one derived reversal condition flatten
    the directional residual at executable prices while never unwinding
    matched inventory.
    """
    import bisect
    books, trades = w["book"], w["trade"]
    start = window_start(slug)
    port = Port()
    rest = Resting()
    opp_fills = []
    bt = [b["t"] for b in books]
    marks = [start + k * CADENCE_S for k in range(1, 15)]
    fav = None
    prev_fav_mid = None
    peak = None                  # running max of the favored mid, this episode
    spread_at_peak = None
    episode_open = False
    flips = skips = withheld = 0
    allocs, exits, traj = [], [], []
    cleanup_shares = ZERO
    peak_pnl = {"pnl": None, "t": None}
    trough = ZERO
    max_dd = ZERO

    def book_at(t):
        j = bisect.bisect_left(bt, t) - 1
        return books[j] if j >= 0 else None

    def fav_mid(side, bid, ask):
        m = (bid + ask) / 2
        return m if side == "UP" else ONE - m

    def taker_buy(side, bid, ask, bq, aq, t, tag):
        px = ask if side == "UP" else ONE - bid
        depth = aq if side == "UP" else bq
        sh = alloc / px
        if depth > 0:
            port.depth_frac.append(float(min(sh, depth) / depth))
        if depth < sh:
            sh = depth
        if sh <= 0 or not (D("0.01") <= px <= D("0.99")):
            return False
        fee = FEES.taker_fee(px, sh)
        port.add(side, sh, px, fee)
        allocs.append({"t": t, "kind": tag, "side": side, "px": str(px),
                       "sh": str(round(sh, 4)), "fee": str(fee),
                       "up_sh": str(round(port.up_sh, 4)),
                       "dn_sh": str(round(port.dn_sh, 4)),
                       "matched": str(round(port.matched, 4)),
                       "residual": str(round(port.residual, 4)),
                       "deployed": str(round(port.deployed, 4)),
                       "net_capital": str(round(port.deployed - port.cash_in,
                                                4))})
        return True

    def flatten(t, reason):
        """Reduce the residual to zero at executable prices, across as many
        books as depth requires. Matched inventory is untouched."""
        nonlocal cleanup_shares
        b = book_at(t)
        if b is None:
            return
        before = port.mark(D(b["bid"]), D(b["ask"])) - (port.deployed
                                                        - port.cash_in)
        want = port.residual
        if want == 0:
            return
        side = "UP" if want > 0 else "DOWN"
        need = abs(want)
        basis_per = ((port.up_cash / port.up_sh) if side == "UP"
                     and port.up_sh > 0 else
                     ((port.dn_cash / port.dn_sh) if port.dn_sh > 0 else ZERO))
        legs = []
        j = bisect.bisect_left(bt, t)
        j = max(0, j - 1)
        while need > 0 and j < len(books):
            bb = books[j]
            bid, ask = D(bb["bid"]), D(bb["ask"])
            bq, aq = D(bb["bid_qty"]), D(bb["ask_qty"])
            # selling UP hits the UP bid; exiting DOWN sells at the DOWN bid,
            # 1 - ask, which consumes the UP ask side's depth
            px = bid if side == "UP" else ONE - ask
            depth = bq if side == "UP" else aq
            take = min(need, depth)
            if take > 0 and D("0.01") <= px <= D("0.99"):
                fee = FEES.taker_fee(px, take)
                port.sell(side, take, px, fee)
                need -= take
                legs.append({"t": bb["t"], "px": str(px),
                             "sh": str(round(take, 4)), "fee": str(fee)})
            j += 1
            if len(legs) > 200:
                break
        if need > 0:
            cleanup_shares += need
        bb = book_at(legs[-1]["t"]) if legs else b
        after = (port.mark(D(bb["bid"]), D(bb["ask"]))
                 - (port.deployed - port.cash_in))
        exits.append({
            "t": t, "reason": reason, "side_exited": side,
            "shares_wanted": str(round(abs(want), 4)),
            "shares_exited": str(round(abs(want) - need, 4)),
            "unfilled_cleanup": str(round(need, 4)),
            "legs": legs,
            "cost_basis_per_share": str(round(basis_per, 6)),
            "proceeds_net_of_fee": str(round(
                sum(D(l["px"]) * D(l["sh"]) - D(l["fee"]) for l in legs), 4)),
            "basis_of_exited_shares": str(round(
                basis_per * (abs(want) - need), 4)),
            "profitable_exit": bool(
                sum(D(l["px"]) * D(l["sh"]) - D(l["fee"]) for l in legs)
                > basis_per * (abs(want) - need)),
            "executable_pnl_before": str(round(before, 4)),
            "executable_pnl_after": str(round(after, 4)),
            "delta": str(round(after - before, 4)),
            "matched_after": str(round(port.matched, 4)),
            "residual_after": str(round(port.residual, 4))})

    for mi, mark in enumerate(marks):
        i = bisect.bisect_left(bt, mark)
        cand = [j for j in (i - 1, i) if 0 <= j < len(books)]
        best = min(cand, key=lambda j: abs(books[j]["t"] - mark), default=None)
        if best is None or abs(books[best]["t"] - mark) > MARK_TOL_S:
            skips += 1
        else:
            b = books[best]
            bid, ask = D(b["bid"]), D(b["ask"])
            bq, aq = D(b["bid_qty"]), D(b["ask_qty"])
            mid = (bid + ask) / 2
            new_fav = ("UP" if mid > D("0.50")
                       else ("DOWN" if mid < D("0.50") else None))
            if mi == 0 and seed:
                # the seed: bounded inventory on BOTH sides, once
                taker_buy("UP", bid, ask, bq, aq, b["t"], "seed")
                taker_buy("DOWN", bid, ask, bq, aq, b["t"], "seed")
            if new_fav is None:
                skips += 1
            else:
                if fav is not None and new_fav != fav:
                    flips += 1
                    peak = None
                    spread_at_peak = None
                    episode_open = False
                    prev_fav_mid = None
                fav = new_fav
                fm = fav_mid(fav, bid, ask)
                strengthening = (prev_fav_mid is not None
                                 and fm > prev_fav_mid)
                if mi == 0 and seed:
                    episode_open = True            # the seed opens an episode
                    peak, spread_at_peak = fm, (ask - bid)
                elif strengthening and episode_open:
                    taker_buy(fav, bid, ask, bq, aq, b["t"], "extend")
                elif episode_open:
                    withheld += 1
                elif strengthening:
                    # a new episode begins on the first strengthening read
                    # after a flip or an exit
                    episode_open = True
                    peak, spread_at_peak = fm, (ask - bid)
                    taker_buy(fav, bid, ask, bq, aq, b["t"], "extend")
                prev_fav_mid = fm
                if opposite_rest:
                    # Variant B's frozen reservation rule, carried over
                    # unmodified: pay at most what leaves the matched pair
                    # non-negative after the fees already paid.
                    opp = "DOWN" if fav == "UP" else "UP"
                    res = reservation(port, fav)
                    opp_ask = (ONE - bid) if opp == "DOWN" else ask
                    q = min(res, opp_ask - TICK)
                    if q >= D("0.01") and res > 0:
                        rest.quote(opp, q.quantize(TICK), alloc / q.quantize(TICK),
                                   b["t"])
                    else:
                        rest.cancel()

        # between marks: books and trades are walked in TRUE TIME ORDER, so a
        # passive fill and a reversal in the same interval resolve in the
        # order the tape puts them, not in the order the code is written.
        nxt = mark + CADENCE_S
        events = [(bb["t"], 0, bb) for bb in books if mark <= bb["t"] < nxt]
        if opposite_rest:
            events += [(tr["t"], 1, tr) for tr in trades
                       if mark <= tr["t"] < nxt]
        events.sort(key=lambda e: (e[0], e[1]))
        for _t, kind, ev in events:
            if kind == 1:
                if rest.side is None:
                    continue
                j0 = bisect.bisect_left(bt, ev["t"]) - 1
                if j0 >= 0:
                    b0 = books[j0]
                    rest.on_book(D(b0["bid"]), D(b0["ask"]),
                                 D(b0["bid_qty"]), D(b0["ask_qty"]))
                got = rest.on_trade(D(ev["px"]), D(ev["qty"]), ev.get("dir"))
                if got > 0:
                    side0, fpx = rest.side, rest.px
                    port.add(side0, got, fpx, ZERO,
                             rebate=FEES.maker_rebate(fpx, got))
                    opp_fills.append({
                        "t": ev["t"], "side": side0, "px": str(fpx),
                        "sh": str(round(got, 4)),
                        "rebate_modelled": str(FEES.maker_rebate(fpx, got)),
                        "queue_at_arm": str(rest.queue_at_arm),
                        "matched": str(round(port.matched, 4)),
                        "residual": str(round(port.residual, 4)),
                        "deployed": str(round(port.deployed, 4))})
                    if rest.sh <= 0:
                        rest.cancel()
                continue
            bb = ev
            bid2, ask2 = D(bb["bid"]), D(bb["ask"])
            if fav is not None and episode_open:
                fm2 = fav_mid(fav, bid2, ask2)
                if peak is None or fm2 > peak:
                    peak, spread_at_peak = fm2, (ask2 - bid2)
                else:
                    trail = (spread_at_peak
                             + FEES.taker_fee(peak, D(1)) * 2)
                    if fm2 < peak - trail and port.residual != 0:
                        flatten(bb["t"], "trail_%s" % str(round(trail, 4)))
                        episode_open = False
                        peak = spread_at_peak = None
            mk = port.mark(bid2, ask2)
            net_cap = port.deployed - port.cash_in
            pnl = mk - net_cap
            traj.append({"t": bb["t"], "bid": bb["bid"], "ask": bb["ask"],
                         "mark": str(round(mk, 4)),
                         "pnl": str(round(pnl, 4)),
                         "matched": str(round(port.matched, 4)),
                         "residual": str(round(port.residual, 4)),
                         "net_capital": str(round(net_cap, 4))})
            if peak_pnl["pnl"] is None or pnl > D(peak_pnl["pnl"]):
                peak_pnl = {"pnl": str(round(pnl, 4)), "t": bb["t"],
                            "s_into_window": round(bb["t"] - start, 1)}
            trough = min(trough, pnl)
            if peak_pnl["pnl"] is not None:
                max_dd = max(max_dd, D(peak_pnl["pnl"]) - pnl)

    last = books[-1]
    bidL, askL = D(last["bid"]), D(last["ask"])
    settle = port.settlement_value(outcome)
    net = port.cash_in + settle - port.deployed
    # The preregistered identity, with every term defined from tracked
    # quantities rather than one of them left as a plug:
    #   directional + matched + settlement_residual + compensation - fees = net
    m = port.matched
    cu = port.up_cash / port.up_sh if port.up_sh > 0 else ZERO
    cd = port.dn_cash / port.dn_sh if port.dn_sh > 0 else ZERO
    matched_econ = m * (ONE - (cu + cd)) if m > 0 else ZERO
    r = port.residual
    res_basis = (abs(r) * (cu if r > 0 else cd)) if r != 0 else ZERO
    res_settle = (abs(r) if ((r > 0 and outcome == "UP")
                             or (r < 0 and outcome == "DOWN")) else ZERO)
    settle_resid = res_settle - res_basis
    directional = port.realized_gross - port.realized_basis
    identity = (directional + matched_econ + settle_resid - port.fees) - net
    return {
        "slug": slug, "variant": name, "settlement_outcome": outcome,
        "allocations": len(allocs),
        "seed_legs": sum(1 for a in allocs if a["kind"] == "seed"),
        "extends": sum(1 for a in allocs if a["kind"] == "extend"),
        "extensions_withheld_not_strengthening": withheld,
        "skipped_periods": skips, "leadership_flips": flips,
        "exits": len(exits),
        "cleanup_shares_unexitable": str(round(cleanup_shares, 4)),
        "up_shares": str(round(port.up_sh, 4)),
        "dn_shares": str(round(port.dn_sh, 4)),
        "up_cash": str(round(port.up_cash, 4)),
        "dn_cash": str(round(port.dn_cash, 4)),
        "matched_shares": str(round(m, 4)),
        "residual_terminal": str(round(port.residual, 4)),
        "max_residual": str(round(port.max_residual, 4)),
        "acquisition_cost": str(round(port.acq_cost, 4)),
        "fees": str(round(port.fees, 4)),
        "maker_compensation": "0.0000",
        "max_capital": str(round(port.max_net_capital, 4)),
        "gross_cash_out": str(round(port.deployed, 4)),
        "exit_proceeds": str(round(port.cash_in, 4)),
        "settlement_value": str(round(settle, 4)),
        "matched_pair_economics": str(round(matched_econ, 4)),
        "directional_realized_pnl": str(round(directional, 4)),
        "settlement_residual_pnl": str(round(settle_resid, 4)),
        "verified_compensation": "0.0000",
        "decomposition_identity_residual": str(round(identity, 6)),
        "gross_settlement_pnl": str(round(port.cash_in + settle
                                          - port.acq_cost, 4)),
        "net_settlement_pnl": str(round(net, 4)),
        "peak_executable_pnl": peak_pnl["pnl"],
        "peak_t_s_into_window": peak_pnl.get("s_into_window"),
        "terminal_executable_pnl": str(round(
            port.mark(bidL, askL) - (port.deployed - port.cash_in), 4)),
        "min_executable_pnl": str(round(trough, 4)),
        "max_drawdown_from_peak": str(round(max_dd, 4)),
        "opposite_fills": len(opp_fills),
        "opposite_fill_log_n": len(opp_fills),
        "profitable_exits": sum(1 for e in exits if e["profitable_exit"]),
        "touch_depth_consumed_max": (str(round(max(port.depth_frac), 4))
                                     if port.depth_frac else None),
        "touch_depth_consumed_over_10pct": sum(
            1 for x in port.depth_frac if x > 0.10),
        "taker_orders": len(port.depth_frac),
        "capital_turnover": str(round(
            port.deployed / port.max_net_capital, 3)
            if port.max_net_capital > 0 else 0),
        "maker_rebate_modelled_UNVERIFIED": str(round(port.rebate_modelled,
                                                      4)),
        "maker_rebate_verified": "0.0000",
        "trajectory": traj, "allocation_log": allocs, "exit_log": exits,
        "opposite_fill_log": opp_fills,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default=TAPE)
    ap.add_argument("--out", default="analysis/l5/favorite_dca")
    ap.add_argument("--alloc", default="1")
    a = ap.parse_args()
    by = load(a.tape)
    elig, rejected = {}, {}
    for slug, w in by.items():
        ok, oc, why = eligible(slug, w)
        (elig if ok else rejected)[slug] = (w, oc, why)
    res = {"alloc_usd": a.alloc,
           "eligible_windows": len(elig), "rejected_windows":
           {s: v[2] for s, v in rejected.items()}, "windows": {}}
    for slug in sorted(elig):
        w, oc, _ = elig[slug]
        al = D(a.alloc)
        res["windows"][slug] = {v: replay(slug, w, oc, v, al)
                                for v in ("A", "B", "Am")}
        res["windows"][slug]["C"] = replay_c(slug, w, oc, al)
        res["windows"][slug]["D0"] = replay_c(slug, w, oc, al, seed=False,
                                              name="D0")
        res["windows"][slug]["D1"] = replay_c(slug, w, oc, al, seed=False,
                                              opposite_rest=True, name="D1")
    os.makedirs(a.out, exist_ok=True)
    # the trajectories are ~99MB as one JSON document, so they go out as a
    # gzipped row stream and the summary keeps everything else. The split is
    # mechanical, not a filter: no row is dropped.
    import gzip as _gz
    with _gz.open(os.path.join(a.out, "trajectories.jsonl.gz"), "wt") as tf:
        for slug, vs in res["windows"].items():
            for v, r in vs.items():
                for row in r.pop("trajectory"):
                    row["slug"] = slug
                    row["variant"] = v
                    tf.write(json.dumps(row) + "\n")
    with open(os.path.join(a.out, "replay_summary.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({"eligible": len(elig),
                      "rejected": len(rejected)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
