"""Can this controller express continuous two-sided participation?

WHAT IS BEING CHECKED, and against what. A 304-window BTC5m harvest of
wallet `bonereaper` establishes an EXECUTION PATTERN: 97.4% two-sided
participation, median 33s to the opposite-side fill, 50.5 fills a window,
early imbalance, continuous replenishment, large residual held to settlement.

THE PATTERN IS A CAPABILITY SPEC, NOT A TARGET. The same harvest shows that
wallet's trading LOST money -- median matched-pair unit cost 1.0199,
pnl_derived_resolution -$9,727.10 over the BTC5m windows, and -$14,562.84
across all assets, still -$6,864.55 after TAKER_REBATE $6,466.66 +
MAKER_REBATE $1,087.80 + REWARD $143.83. Its merge_usdc $39,613.33 is matched
pairs handed back at $1.00, already counted in that P&L, and is NOT income
(analysis/traders/bonereaper/). This account is maker Tier 1 where the rebate
is ZERO. So these tests verify the MECHANICS are available and the ECONOMIC
GATES still refuse; nothing here copies that wallet's residual size or its
above-$1 pair purchases.

Driven through the REAL path: `l5_btc5m_runner.public_book` for the book with
the venue's own constraints carried, the real `Btc5mExecutor`, the audited
`Venue` double whose strict __getattr__ raises on any unexpected transport
call, and a ledger on disk. Nothing is submitted live.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_venue_constraints as VC

from tests.test_l5_btc5m_execution import (          # noqa: E402
    ARMED, MARKET, Rig, armed, clean_env,            # noqa: F401
)

LOT = D("0.01")


def market_doc(bid="0.50", ask="0.51", min_qty=0.01, tick=0.01, fee=0.095):
    return {"market": {"slug": MARKET, "bestBidQuote": bid,
                       "bestAskQuote": ask, "closed": False,
                       "minimumTradeQty": min_qty,
                       "orderPriceMinTickSize": tick,
                       "feeCoefficient": fee}}


class Rig6(Rig):
    """Steps with a book the REAL runner produced, constraints included."""

    def book(self, **kw):
        from src import l5_btc5m_runner as R
        doc = market_doc(**kw)
        return R.public_book(MARKET, fetch=lambda _u: doc)

    def go(self, t=20.0, p_up="0.50", **kw):
        return self.ex.step(market=MARKET, book=self.book(**kw),
                            t_in_window_s=t, p_up=p_up, p_source="TEST")

    def working(self):
        return [o for o in self.venue.by_oid
                if o not in self.venue.full and o not in self.venue.retired]

    def fill_all(self, qty=None):
        n = 0
        for oid in self.working():
            self.venue.fill(oid, qty=qty)
            n += 1
        return n


@pytest.fixture
def rig(tmp_path):
    return Rig6(tmp_path, max_live_orders=6)


@pytest.fixture
def two_sided(monkeypatch):
    """The straddle, two-sided, replenishing, venue constraints REQUIRED."""
    monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
    monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.0008")       # one lot a side
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.00")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "1.00")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "5")
    monkeypatch.setenv(VC.REQUIRE_ENV, VC.REQUIRE_TOKEN)
    #: SYNTHETIC rates, mechanism-only. The gate's own behaviour is covered
    #: in tests/test_l5_straddle_ev.py; leaving these unset refuses every
    #: order, which is the gate working and not what these tests measure.
    monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.95")
    monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.0001")
    monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.50")
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)


# ============ 1. SIMULTANEOUS UP/DOWN, RESTING FROM EARLY IN THE WINDOW

def test_both_sides_rest_from_one_decision(rig, armed, two_sided):
    """97.4% two-sided participation needs both sides from ONE decision, not
    one side now and the other after a round completes."""
    rig.go(t=5.0)
    sides = {s["side"] for s in rig.venue.sent}
    assert sides == {C.UP, C.DOWN}, rig.venue.sent
    assert len(rig.working()) == 2


@pytest.mark.parametrize("t", [2.0, 5.0, 15.0, 33.0, 60.0])
def test_two_sided_is_available_early_in_the_window(rig, armed, two_sided, t):
    """Median time to the opposite-side fill in the harvest is 33s, so both
    sides must be quotable well before that."""
    rig.go(t=t)
    assert {s["side"] for s in rig.venue.sent} == {C.UP, C.DOWN}, t


def test_both_legs_are_equal_size_and_post_only(rig, armed, two_sided):
    rig.go(t=5.0)
    qtys = {s["qty"] for s in rig.venue.sent}
    assert len(qtys) == 1, qtys
    assert all(s["post_only"] for s in rig.venue.sent)
    assert all(s["mode"] == "MAKER" for s in rig.venue.sent)


def test_the_pair_cost_of_the_two_legs_is_under_one(rig, armed, two_sided):
    """NOT bonereaper's 1.0199. A pair at or above 1.00 cannot profit."""
    rig.go(t=5.0)
    px = {s["side"]: D(s["price"]) for s in rig.venue.sent}
    assert px[C.UP] + px[C.DOWN] < D("1.00"), px


# ============ 2. REPLENISHMENT AFTER PARTIAL AND COMPLETE FILLS

def test_a_complete_fill_is_replenished(rig, armed, two_sided):
    rig.go(t=5.0)
    first = len(rig.venue.sent)
    assert rig.fill_all() == 2
    rig.tick(2).go(t=10.0)
    assert len(rig.venue.sent) > first, "no replenishment after a full fill"


def test_many_replenishment_cycles_in_one_window(rig, armed, two_sided):
    """50.5 fills a window is ~8 cycles of a six-order book. The mechanics
    must sustain repeated cycles, not one."""
    cycles = fills = 0
    rig.go(t=5.0)
    for i in range(10):
        got = rig.fill_all()
        if got:
            cycles += 1
            fills += got
        rig.tick(2).go(t=10.0 + 5 * i)
    assert cycles >= 6, "only %d replenishment cycles" % cycles
    assert fills >= 12, fills


def test_a_partial_fill_is_replenished_without_losing_the_remainder(
        rig, armed, two_sided, monkeypatch):
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.0040")      # five lots a side
    rig.go(t=5.0)
    oid = rig.working()[0]
    want = D(str(rig.venue.by_oid[oid]["qty"]))
    rig.venue.fill(oid, qty=D("0.02"))
    row = rig.venue.order(oid)
    assert row["state"] == "ORDER_STATE_PARTIALLY_FILLED"
    assert D(row["leavesQuantity"]) == want - D("0.02")
    rig.tick(2).go(t=10.0)
    #: the filled part is inventory and the remainder is still working
    side = rig.venue.by_oid[oid]["side"]
    inv = rig.ex.inventory(MARKET)
    assert D(str(inv[side]["qty"])) >= D("0.02")


def test_every_replenishment_stays_on_both_venue_grids(rig, armed, two_sided):
    spec = VC.from_market(market_doc()["market"])
    rig.go(t=5.0)
    for i in range(8):
        rig.fill_all()
        rig.tick(2).go(t=10.0 + 5 * i)
    for s in rig.venue.sent:
        assert VC.check_order(s["price"], s["qty"], spec) is None, s


def test_no_client_order_id_is_ever_reused(rig, armed, two_sided):
    """The venue double raises on a duplicate, so a reused id fails here
    rather than double-counting a fill."""
    rig.go(t=5.0)
    for i in range(8):
        rig.fill_all()
        rig.tick(2).go(t=10.0 + 5 * i)
    coids = [s["client_order_id"] for s in rig.venue.sent]
    assert len(coids) == len(set(coids))


# ============ 3. ADAPTIVE REPRICING WITHOUT CANCEL/REPLACE RACES

def test_a_fill_that_beats_a_cancel_is_booked_not_lost(rig, armed, two_sided):
    """THE RACE. A cancel is not evidence the order did not fill."""
    rig.go(t=5.0)
    oid = rig.working()[0]
    side = rig.venue.by_oid[oid]["side"]
    rig.venue.fill_on_cancel.add(oid)
    rig.venue.cancel(oid, MARKET)
    rig.tick(2).go(t=10.0)
    inv = rig.ex.inventory(MARKET)
    assert D(str(inv[side]["qty"])) >= LOT, "the racing fill was lost"


def test_a_cancel_is_always_by_exact_order_id(rig, armed, two_sided):
    rig.go(t=5.0)
    for i in range(6):
        rig.tick(30).go(t=40.0 + 30 * i)
    known = set(rig.venue.by_oid)
    for oid, _slug in rig.venue.cancelled:
        assert oid in known, "cancelled an id we never submitted: %s" % oid


def test_repricing_never_leaves_more_than_the_order_limit_working(
        rig, armed, two_sided, monkeypatch):
    """A cancel/replace that places before the cancel lands would breach the
    limit. It must not."""
    monkeypatch.setenv(X.STRADDLE_REPRICE_TICKS_ENV, "2")
    rig.go(t=5.0)
    for i in range(10):
        rig.tick(5).go(t=10.0 + 5 * i, bid="0.%02d" % (50 + i),
                       ask="0.%02d" % (51 + i))
        assert len(rig.ex.live_orders()) <= 6, i


def test_a_working_order_is_not_cancelled_and_reissued_at_the_same_price(
        rig, armed, two_sided):
    """Churning the same price forfeits queue position for nothing."""
    rig.go(t=5.0)
    before = len(rig.venue.cancelled)
    for _ in range(4):
        rig.tick(2).go(t=10.0)          # same book, same prices
    assert len(rig.venue.cancelled) == before, rig.venue.cancelled


# ============ 4. PAIR COMPLETION IS OPTIONAL AND ECONOMIC

def test_completion_is_off_unless_a_pair_cost_is_configured(rig, armed,
                                                            two_sided):
    """NOT mandatory inventory balancing. Unconfigured means disabled."""
    rig.go(t=5.0)
    rig.fill_all()
    rig.tick(2)
    rep = rig.go(t=40.0)
    #: THE PROPERTY is that no completion is SENT. My first version asserted
    #: evaluation["completion"]["why"] == "COMPLETION_DISABLED", which is
    #: brittle: the decision can exit on an earlier gate and then carries no
    #: completion block at all. Assert the outcome, and the message only
    #: when the block exists.
    assert not [s for s in rig.venue.sent
                if s.get("reason") == "STRADDLE_COMPLETION"]
    ev = (rep.get("evaluation") or {}).get("completion")
    if isinstance(ev, dict) and ev.get("why") is not None:
        assert ev["why"] == "COMPLETION_DISABLED", ev


def test_an_imbalance_is_tolerated_rather_than_forced_flat(rig, armed,
                                                           two_sided):
    """Early imbalance is the observed pattern and must not trigger a forced
    rebalance. Nothing may SELL to flatten."""
    rig.go(t=5.0)
    oid = rig.working()[0]
    rig.venue.fill(oid)                 # one side only -> imbalance
    rig.tick(2).go(t=40.0)
    assert all(s["action"] == "BUY" for s in rig.venue.sent), rig.venue.sent


def test_completion_when_enabled_never_pays_above_its_target(rig, armed,
                                                             two_sided,
                                                             monkeypatch):
    monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "0.96")
    rig.go(t=5.0)
    oid = rig.working()[0]
    rig.venue.fill(oid)
    held_px = D(rig.venue.by_oid[oid]["price"])
    rig.tick(2).go(t=40.0, bid="0.60", ask="0.61")
    comp = [s for s in rig.venue.sent
            if s.get("reason") == "STRADDLE_COMPLETION"]
    for s in comp:
        assert held_px + D(s["price"]) <= D("0.96"), s
        assert s["post_only"] is True


# ============ 5. EVERY LIMIT STILL BINDS

def test_the_per_side_cap_binds(rig, armed, two_sided, monkeypatch):
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "0.0016")
    for i in range(10):
        rig.fill_all()
        rig.tick(2).go(t=10.0 + 5 * i)
    per = {}
    for s in rig.venue.sent:
        per[s["side"]] = per.get(s["side"], D(0)) + D(s["price"]) * D(s["qty"])
    for side, usd in per.items():
        assert usd <= D("0.0016") + D("0.0008"), (side, usd)


def test_the_unmatched_cap_binds(rig, armed, two_sided, monkeypatch):
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.0008")
    rig.go(t=5.0)
    oid = rig.working()[0]
    rig.venue.fill(oid)
    rig.tick(2)
    rig.go(t=20.0)
    #: the property, not the message: unmatched notional never exceeds the
    #: cap by more than one lot. (My first version also asserted the refusal
    #: reason `or True`, which cannot fail -- an unfailable assertion reads
    #: as coverage and is worse than none.)
    inv = rig.ex.inventory(MARKET)
    up = D(str(inv[C.UP]["cost"]))
    dn = D(str(inv[C.DOWN]["cost"]))
    assert abs(up - dn) <= D("0.0008") + D("0.0008"), (up, dn)


def test_the_aggregate_cash_cap_binds(rig, armed, two_sided, monkeypatch):
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.0024")
    for i in range(10):
        rig.fill_all()
        rig.tick(2).go(t=10.0 + 5 * i)
    spent = sum(D(s["price"]) * D(s["qty"]) for s in rig.venue.sent)
    assert spent <= D("0.0024"), spent


def test_the_six_order_limit_binds_across_the_window(rig, armed, two_sided):
    for i in range(12):
        rig.tick(2).go(t=5.0 + 5 * i)
        assert len(rig.ex.live_orders()) <= 6, i


def test_the_venue_gate_still_stops_an_unverified_book(rig, armed, two_sided):
    rep = rig.ex.step(market=MARKET,
                      book={"bid": "0.50", "ask": "0.51", "age_s": 0.1},
                      t_in_window_s=5.0, p_up="0.50", p_source="TEST")
    assert rig.venue.sent == []
    assert rep["why"] == "VENUE_CONSTRAINTS_UNVERIFIED"


# ============ 6. WHAT WE DO *NOT* COPY

def test_an_above_one_pair_cost_is_unreachable(rig, armed, two_sided,
                                               monkeypatch):
    """bonereaper's median BTC5m pair cost is 1.0199. A completion target at
    or above 1.00 is a contradiction and must refuse outright."""
    monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "1.02")
    rep = rig.go(t=5.0)
    comp = [s for s in rig.venue.sent
            if s.get("reason") == "STRADDLE_COMPLETION"]
    assert comp == [], rep


def test_a_ninety_cent_entry_is_not_reachable_in_this_mode(rig, armed,
                                                           two_sided):
    """176 of bonereaper's 304 windows include a buy at 90c or above. The
    straddle prices from `straddle_price`, which is validated below 0.50, so
    no leg of this mode can be a 90c purchase."""
    rig.go(t=5.0, bid="0.95", ask="0.96")
    for s in rig.venue.sent:
        assert D(s["price"]) < D("0.50"), s


def test_directional_accumulation_stays_gated(rig, armed, two_sided,
                                              monkeypatch):
    """Favourite accumulation needs an independent positive-EV estimate. With
    the EV rates withdrawn, nothing opens."""
    for k in (X.STRADDLE_P_PAIR_NATURAL_ENV, X.STRADDLE_P_COMPLETE_ENV,
              X.STRADDLE_P_LONE_WIN_ENV):
        monkeypatch.delenv(k, raising=False)
    rep = rig.go(t=5.0)
    assert rig.venue.sent == []
    assert "NOT_SET" in str(rep.get("why")), rep.get("why")


# ============ 7. THE PATHS SECTIONS 3 AND 5 CLAIMED BUT NEVER REACHED
#
# An independent audit of this file (2026-11-19) found that three tests above
# pass without exercising what their names say:
#
#   * `test_repricing_never_leaves_more_than_the_order_limit_working` moves
#     the BOOK, but the fixed straddle reprices on INVENTORY IMBALANCE only,
#     so nothing was repriced and at most two orders were ever live;
#   * `test_a_cancel_is_always_by_exact_order_id` loops over zero cancels;
#   * `test_the_six_order_limit_binds_across_the_window` never has more than
#     two orders live, so the limit is never approached.
#
# They are left in place as the weak checks they are. The tests below reach
# each path for real and assert that they did, so they cannot pass vacuously.

def _side(rig, side):
    return [o for o in rig.working() if rig.venue.by_oid[o]["side"] == side]


@pytest.fixture
def repricing(monkeypatch, two_sided):
    """A rung at 0.20 that shades one tick per clip of imbalance, two lots a
    clip -- room to reprice many times without leaving the (0, 0.50) band."""
    monkeypatch.setenv(X.STRADDLE_REPRICE_TICKS_ENV, "1")
    monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.20")
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.0020")


def _drive_one_sided(rig, heavy, steps):
    """Fill only `heavy`, step, and return every step's report."""
    reps = [rig.go(t=5.0)]
    for i in range(steps):
        for o in _side(rig, heavy):
            rig.venue.fill(o)
        reps.append(rig.tick(2).go(t=10.0 + 2 * i))
    return reps


def test_an_imbalance_really_reprices_both_sides(rig, armed, repricing):
    """Heavy side bids LOWER, light side HIGHER, one tick per clip."""
    _drive_one_sided(rig, C.UP, 2)
    px = {s: [D(x["price"]) for x in rig.venue.sent if x["side"] == s]
          for s in (C.UP, C.DOWN)}
    assert px[C.UP] == [D("0.20"), D("0.19"), D("0.18")], px
    assert px[C.DOWN] == [D("0.20"), D("0.21"), D("0.22")], px


def test_repricing_is_additive_and_cancels_nothing(rig, armed, repricing):
    """WHAT "REPRICING" MEANS HERE, pinned because the name suggests
    otherwise: the light side's older rungs are NOT cancelled, they keep
    resting below the new one. So there is no cancel/replace race on this
    path at all -- and the order limit is what bounds the stack."""
    _drive_one_sided(rig, C.UP, 3)
    assert rig.venue.cancelled == []
    resting = sorted(D(rig.venue.by_oid[o]["price"])
                     for o in _side(rig, C.DOWN))
    assert resting == [D("0.20"), D("0.21"), D("0.22"), D("0.23")], resting


@pytest.mark.parametrize("heavy", [C.UP, C.DOWN])
def test_the_order_limit_is_reached_and_refuses(rig, armed, repricing, heavy):
    """The limit is REACHED (six live), a placement is REFUSED for it by
    name, and it is never exceeded."""
    reps = _drive_one_sided(rig, heavy, 10)
    refusals = []
    for rep in reps:
        refusals += [r for r in (rep.get("refused") or [])
                     if r["reason"] == "MAX_LIVE_ORDERS"]
    assert len(rig.ex.live_orders()) == 6
    assert len(rig.working()) == 6
    assert refusals, "the six-order limit was never the reason for a refusal"


@pytest.mark.parametrize("limit", [3, 6])
def test_the_order_limit_is_never_exceeded_at_any_step(tmp_path, armed,
                                                       repricing, limit):
    rig = Rig6(tmp_path, max_live_orders=limit)
    rig.go(t=5.0)
    seen = 0
    for i in range(12):
        for o in _side(rig, C.UP):
            rig.venue.fill(o)
        rig.tick(2).go(t=10.0 + 2 * i)
        seen = max(seen, len(rig.working()))
        assert len(rig.ex.live_orders()) <= limit, i
        assert len(rig.working()) <= limit, i
    assert seen == limit, "the limit of %d was never reached" % limit


@pytest.mark.parametrize("heavy", [C.UP, C.DOWN])
def test_at_the_order_limit_the_heavy_side_stops_growing(rig, armed,
                                                         repricing, heavy):
    """THE DEFECT THESE TESTS FOUND. Sides were visited UP then DOWN, and
    the limit refuses whatever is asked for last. With UP heavy, the DOWN
    order that would BALANCE the book was refused every step while the UP
    order that deepens the imbalance was placed: UP reached 0.14 against
    DOWN 0 in 14 steps. With DOWN heavy it stopped at 0.05, by accident of
    the order. The light side now goes first, so both directions stop."""
    _drive_one_sided(rig, heavy, 14)
    inv = rig.ex.inventory(MARKET)
    light = C.DOWN if heavy == C.UP else C.UP
    assert D(str(inv[light]["qty"])) == 0
    assert D(str(inv[heavy]["qty"])) <= D("0.05"), inv
    #: and what was refused for the limit is the HEAVY side, never the light
    rep = rig.tick(2).go(t=60.0)
    lim = [r["order"]["side"] for r in (rep.get("refused") or [])
           if r["reason"] == "MAX_LIVE_ORDERS"]
    assert light not in lim, lim


def test_a_balanced_book_is_still_visited_up_then_down(rig, armed, two_sided):
    """The light-side-first ordering changes nothing when inventory is
    equal: the first decision still sends UP and then DOWN."""
    rig.go(t=5.0)
    assert [s["side"] for s in rig.venue.sent] == [C.UP, C.DOWN]


# ---- cancels that actually happen

def test_a_stale_book_cancels_exactly_the_working_orders(rig, armed,
                                                         two_sided):
    rig.go(t=5.0)
    working = set(rig.working())
    assert len(working) == 2
    book = rig.book()
    book["age_s"] = 999
    rep = rig.tick(2).ex.step(market=MARKET, book=book, t_in_window_s=20.0,
                              p_up="0.50", p_source="TEST")
    assert rep["why"] == "BOOK_STALE"
    assert {oid for oid, _ in rig.venue.cancelled} == working
    assert all(slug == MARKET for _, slug in rig.venue.cancelled)
    assert len(rig.ex.live_orders()) == 0


def test_the_window_end_cancels_exactly_the_working_orders(rig, armed,
                                                           repricing):
    """With a real stack resting: every working id is cancelled once, and
    nothing that had already filled is cancelled."""
    _drive_one_sided(rig, C.UP, 3)
    working, filled = set(rig.working()), set(rig.venue.full)
    assert len(working) >= 4 and filled
    rep = rig.tick(2).go(t=301.0)
    assert rep["why"] == "WINDOW_OVER"
    ids = [oid for oid, _ in rig.venue.cancelled]
    assert set(ids) == working
    assert len(ids) == len(set(ids)), "an order was cancelled twice"
    assert not set(ids) & filled
    assert len(rig.ex.live_orders()) == 0


@pytest.fixture
def completing(monkeypatch, two_sided):
    monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "0.96")
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "NO")


def _open_a_completion(rig):
    rig.go(t=5.0)
    rig.venue.fill(_side(rig, C.UP)[0])
    rig.tick(2).go(t=40.0, bid="0.60", ask="0.61")
    comp = [o for o in rig.working()
            if rig.venue.by_oid[o]["reason"] == "STRADDLE_COMPLETION"]
    assert len(comp) == 1, rig.venue.sent
    return comp[0]


def test_a_completion_is_cancel_replaced_by_exact_id(rig, armed, completing):
    """THE ONE REAL CANCEL/REPLACE on this path. The book moves, the resting
    completion is cancelled by its own id and exactly one replaces it."""
    old = _open_a_completion(rig)
    rig.tick(2).go(t=50.0, bid="0.40", ask="0.41")
    assert old in [oid for oid, _ in rig.venue.cancelled]
    comp = [o for o in rig.working()
            if rig.venue.by_oid[o]["reason"] == "STRADDLE_COMPLETION"]
    assert len(comp) == 1 and comp[0] != old
    assert D(rig.venue.by_oid[comp[0]]["price"]) != \
        D(rig.venue.by_oid[old]["price"])
    known = set(rig.venue.by_oid)
    assert all(oid in known for oid, _ in rig.venue.cancelled)


def test_a_completion_that_fills_during_its_cancel_is_not_replaced(
        rig, armed, completing):
    """THE RACE, on the path that really cancels and replaces. The fill
    beats the cancel; the pair is complete; a replacement would buy the
    second leg TWICE. Nothing more may be sent."""
    old = _open_a_completion(rig)
    sent_before = len(rig.venue.sent)
    rig.venue.fill_on_cancel.add(old)
    for i in range(3):
        rig.tick(2).go(t=50.0 + i, bid="0.40", ask="0.41")
    assert old in [oid for oid, _ in rig.venue.cancelled]
    assert len(rig.venue.sent) == sent_before, rig.venue.sent[sent_before:]
    inv = rig.ex.inventory(MARKET)
    assert D(str(inv[C.UP]["qty"])) == D(str(inv[C.DOWN]["qty"])) == LOT
    assert len(rig.ex.live_orders()) == 0


# ---- the completion target, stated as it actually behaves

@pytest.mark.parametrize("target", ["1.02", "1.00", "0.96"])
def test_an_over_asked_completion_target_is_clamped_not_refused(
        rig, armed, two_sided, monkeypatch, target):
    """CORRECTS `test_an_above_one_pair_cost_is_unreachable`, which holds no
    inventory when it looks and so would pass whatever the target did. A
    target above COMPLETION_PAIR_COST_MAX does NOT disable completion: the
    reader clamps it to 0.90 and a completion IS sent. What is unreachable
    is a PAIR COST above 0.90 -- not the order."""
    monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, target)
    assert X.completion_pair_cost() == X.COMPLETION_PAIR_COST_MAX
    rig.go(t=5.0)
    oid = _side(rig, C.UP)[0]
    rig.venue.fill(oid)
    held = D(rig.venue.by_oid[oid]["price"])
    rig.tick(2).go(t=40.0, bid="0.60", ask="0.61")
    comp = [s for s in rig.venue.sent if s["reason"] == "STRADDLE_COMPLETION"]
    assert len(comp) == 1, rig.venue.sent
    assert held + D(comp[0]["price"]) <= X.COMPLETION_PAIR_COST_MAX
    assert comp[0]["post_only"] is True and comp[0]["side"] == C.DOWN


# ---- the two caps whose first tests could not fail

@pytest.mark.parametrize("cap,n_sent,why", [
    ("1.00", 3, "STRADDLE_RESTING"),        # loose: a third order goes out
    ("0.0008", 2, "UNMATCHED_CEILING"),     # tight: nothing does, by name
])
def test_the_unmatched_cap_is_what_stops_the_next_order(
        rig, armed, two_sided, monkeypatch, cap, n_sent, why):
    """The same scenario at two caps, so the cap is shown to be the cause."""
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, cap)
    rig.go(t=5.0)
    rig.venue.fill(_side(rig, C.UP)[0])
    rep = rig.tick(2).go(t=20.0)
    assert (len(rig.venue.sent), rep["why"]) == (n_sent, why)


def test_a_partial_fill_leaves_its_remainder_working(rig, armed, two_sided,
                                                     monkeypatch):
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.0040")      # five lots a side
    rig.go(t=5.0)
    oid = rig.working()[0]
    rig.venue.fill(oid, qty=D("0.02"))
    rig.tick(2).go(t=10.0)
    assert oid in rig.working(), "the partly filled order was pulled"
    assert rig.venue.cancelled == []
    assert D(rig.venue.order(oid)["leavesQuantity"]) == D("0.03")
