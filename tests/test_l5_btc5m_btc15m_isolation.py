"""BTC 5-minute adapter and BTC 15-minute runner on ONE scripted venue.

WHAT IS REAL. The 15-minute side is the production `Runner` and
`FaithfulExecutor`, built by `test_l5_ladder_runner.harness` exactly as the
existing runner suites build them, on its own `InventoryStore`. The
5-minute side is a real `Btc5mExecutor` on its own ledger file. Both are
handed the SAME venue object, so every order, cancel and read of either
strategy lands in one place and can be attributed.

WHAT IS NOT. The venue and book are scripted doubles. `Runner.roll`,
arming and the real transport are not exercised; the real transport is
bound to one market slug and carries a cancel-ownership oracle, neither of
which this double models. Nothing here is evidence about a real account.

THE VENUE DOUBLE. `SharedVenue` reports each order and position under the
market its own intent named (the stock double files everything under the
15-minute slug). By default its open-order rows carry no side, as the stock
double's do. `rows_state_side = True` adds `outcomeSide`, which real PMUS
open-order rows carry -- and the 15-minute reconciliation behaves
differently in the two cases, so the last class pins both.

SECTIONS A-F ARE PROPERTIES THE ADAPTER MUST HOLD. SECTION G CHARACTERISES
EXISTING 15-MINUTE BEHAVIOUR AND FIXES NOTHING: those tests assert what the
runner does today so a change to it shows up here. Read their docstrings
before running both strategies on one account.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

import tests.test_l5_ladder_runner as LR
import tests.test_l5_runner_pair_and_cancel_state as T
from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests import test_l5_btc5m_execution as T5
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R

M15 = T.SLUG                                        # cpc-btc-updown-15m-...
M5 = "cpc-btc-updown-5m-2026-11-10-0200z"
ENV_5M = {C.CONTROLLER_ENV: "ON", X.LIVE_ENV: X.ARM_TOKEN, X.MARKET_ENV: M5,
          X.NOT_AFTER_ENV: "2026-11-10T02:15:00Z", X.ACCOUNT_CAP_ENV: "10"}
FIRST_5M = ("UP", D("0.58"), D("0.17"))             # on 0.60/0.62, p_up 0.75
FIRST_5M_COST = D("0.0986")


class SharedVenue(T.Venue):
    """The stock scripted venue, with every row under its order's market."""

    rows_state_side = False
    #: False is the REAL venue: the posted order body carries no client
    #: order id (`l5_order_body.build`), so an open-order row cannot echo one
    rows_carry_client_id = True

    def __init__(self):
        super().__init__()
        self.refuse_markets = set()

    def submit(self, intent):
        if intent.get("market") in self.refuse_markets:
            raise ConnectionError("venue did not answer")
        return super().submit(intent)

    def open_orders(self):
        rows = []
        for oid, i in self.by_oid.items():
            if oid in self.full or oid in self.retired:
                continue
            row = {"id": oid, "clientOrderId": i["client_order_id"],
                   "marketSlug": i["market"], "qty": str(i["qty"]),
                   "remainingQuantity": str(D(str(i["qty"])) - D(self.cum[oid]))}
            if self.rows_state_side:
                row["outcomeSide"] = ("OUTCOME_SIDE_YES" if i["side"] == "UP"
                                      else "OUTCOME_SIDE_NO")
            if not self.rows_carry_client_id:
                del row["clientOrderId"]
            rows.append(row)
        return rows

    def positions(self):
        rows = []
        for market in sorted({i["market"] for i in self.by_oid.values()}):
            up = sum(D(self.cum[o]) for o, i in self.by_oid.items()
                     if i["market"] == market and i["side"] == "UP")
            dn = sum(D(self.cum[o]) for o, i in self.by_oid.items()
                     if i["market"] == market and i["side"] == "DOWN")
            if up or dn:
                rows.append({"marketSlug": market, "qtyBought": str(up),
                             "qtySold": str(dn),
                             "netPositionDecimal": str(up - dn)})
        return rows

    # -- reads for the tests
    def ids(self, prefix):
        return [o for o, i in self.by_oid.items()
                if i["client_order_id"].startswith(prefix)]

    def resting(self, prefix):
        return [o for o in self.ids(prefix)
                if o not in self.full and o not in self.retired]


class Both:
    """The real 15m runner harness and a 5m executor on one venue."""

    def __init__(self, tmp_path, monkeypatch):
        self.h = LR.harness(tmp_path, monkeypatch)
        LR.seed(self.h, 30, "0.0300")
        self.venue = self.h.venue = SharedVenue()
        self.h.attach()                 # runner AND executor now use it
        LR.apply_limits(self.h)
        for k, v in ENV_5M.items():
            monkeypatch.setenv(k, v)
        self._mp, self._tmp = monkeypatch, tmp_path
        self.ex = X.Btc5mExecutor(
            self.venue, str(tmp_path / "btc5m" / "ledger.json"),
            now_fn=lambda: dt.datetime.fromtimestamp(self.h.t, dt.timezone.utc))
        assert self.h.r.transport is self.venue
        assert self.h.r.executor.transport is self.venue
        assert self.ex.transport is self.venue

    # -- the 15m side
    def open15(self):
        """The L5 action places leg one; the next timer tick its complement."""
        before = len(self.venue.ids("l5f-"))
        self.h.at(6.5).l5_action()
        self.h.tick(6.8)
        assert len(self.venue.ids("l5f-")) == before + 2, dict(self.h.r.stats)
        return self

    def store15(self):
        return self.h.r.store

    # -- the 5m side
    def step5(self, bid="0.60", ask="0.62", p_up="0.75", at=None):
        """One step of the 5m adapter, UNDER THE 5m SERVICE'S OWN
        ENVIRONMENT. In production the two strategies are two services and
        each reads its own approval document; in this one process they
        share `os.environ`, so the 5-minute service's authorization is
        applied for the length of its own step and removed again. Outside
        this block the 15m harness sees exactly what it always saw -- in
        particular, no 5-minute approval, which would (correctly) refuse
        every 15-minute claim."""
        if at is not None:
            self.h.at(at)
        with self._mp.context() as m:
            T5.authorize_5m(
                m, self._tmp,
                now_fn=lambda: dt.datetime.fromtimestamp(
                    self.h.t, dt.timezone.utc),
                starts=(dt.datetime.fromtimestamp(T.WS, dt.timezone.utc),),
                release_locks=False)
            return self.ex.step(
                market=M5, book={"bid": bid, "ask": ask, "age_s": 0.1},
                t_in_window_s=self.h.t - float(T.WS), p_up=p_up,
                p_source="TEST")

    def open5(self, at=8.0):
        rep = self.step5(at=at)
        assert [(p["side"], D(p["price"]), D(p["qty"]))
                for p in rep["placed"]] == [FIRST_5M], rep
        return rep["placed"][0]["venue_order_id"]


@pytest.fixture
def both(tmp_path, monkeypatch):
    return Both(tmp_path, monkeypatch)


# ======================================================================
# A. BOTH PLACE; THE NAMESPACES AND MARKETS ARE DISJOINT
# ======================================================================
class TestBothPlaceOnTheirOwnMarkets:

    def test_both_place_and_no_id_or_market_is_shared(self, both):
        both.open15()
        v5 = both.open5()
        sent = both.venue.sent
        assert len(sent) == 3
        by_prefix = {s["client_order_id"][:4] for s in sent}
        assert by_prefix == {"l5f-", "b5m-"}
        for s in sent:
            coid = s["client_order_id"]
            want = M15 if coid.startswith("l5f-") else M5
            assert s["market"] == want, s
            assert coid.startswith("l5f-") != coid.startswith(X.COID_PREFIX)
        assert len({s["client_order_id"] for s in sent}) == 3
        # each ledger holds only its own
        assert set(both.ex.orders()) == {s["client_order_id"] for s in sent
                                         if s["market"] == M5}
        assert all(c.startswith("l5f-") for c in both.store15().live_orders())
        assert not any(both.ex.owns(c) for c in both.store15().live_orders())
        # and the venue lists each under its own market
        listed = {r["id"]: r["marketSlug"] for r in both.venue.open_orders()}
        assert listed == {"v1": M15, "v2": M15, v5: M5}

    def test_the_5m_adapter_refuses_the_15m_market_outright(self, both,
                                                            monkeypatch):
        monkeypatch.setenv(X.MARKET_ENV, M15)
        rep = both.ex.step(market=M15, t_in_window_s=8.0, p_up="0.75",
                           book={"bid": "0.60", "ask": "0.62", "age_s": 0.1})
        assert rep["arming_refusal"] == "NOT_A_BTC5M_MARKET"
        assert both.venue.sent == []


# ======================================================================
# B. THE 5M SIDE'S CANCELS LEAVE EVERY 15M ORDER RESTING
# ======================================================================
class TestThe5mCancelsTouchOnlyItsOwn:

    def assert_15m_untouched(self, both):
        assert both.venue.resting("l5f-") == ["v1", "v2"]
        for oid in ("v1", "v2"):
            assert both.venue.order(oid)["state"] == "ORDER_STATE_NEW"
        assert both.store15().still_fillable_order_count() == 2
        assert sorted(both.h.live()) == ["DOWN", "UP"]

    def test_end_of_window_cancels_only_the_5m_order(self, both):
        both.open15()
        v5 = both.open5()
        rep = both.ex.end_of_window(M5)
        assert both.venue.cancelled == [v5]
        assert [c["venue_order_id"] for c in rep["cancelled"]] == [v5]
        assert both.ex.live_orders() == {}
        self.assert_15m_untouched(both)

    def test_a_favorite_flip_cancels_only_the_5m_order(self, both):
        both.open15()
        v5 = both.open5()
        rep = both.step5(bid="0.38", ask="0.40", p_up="0.30", at=9.0)
        assert both.venue.cancelled == [v5]
        assert [(p["side"], D(p["price"])) for p in rep["placed"]] == [
            ("DOWN", D("0.58"))]
        self.assert_15m_untouched(both)
        # and the replacement is again a b5m order on the 5m market
        assert both.venue.sent[-1]["client_order_id"].startswith("b5m-")
        assert both.venue.sent[-1]["market"] == M5

    def test_end_of_window_with_only_15m_orders_cancels_nothing(self, both):
        both.open15()
        rep = both.ex.end_of_window(M5)
        assert rep["cancelled"] == [] and both.venue.cancelled == []
        # not even when asked about the 15m market by name
        assert both.ex.end_of_window(M15)["cancelled"] == []
        assert both.venue.cancelled == []
        self.assert_15m_untouched(both)


# ======================================================================
# C. THE 15M RUNNER'S OWN CANCELS LEAVE THE 5M ORDER RESTING
# ======================================================================
class TestThe15mCancelsTouchOnlyItsOwn:
    """True while the 15m store has NOT adopted the 5m order. See section G
    for what happens when a 15m reconciliation runs with one resting."""

    def assert_5m_untouched(self, both, v5):
        assert v5 not in both.venue.cancelled
        assert not set(both.venue.cancelled) & set(both.venue.ids("b5m-"))
        assert both.venue.order(v5)["state"] == "ORDER_STATE_NEW"
        got = both.ex.poll()
        assert got["uncertain"] == [] and got["fills"] == []
        assert [r["state"] for r in both.ex.orders().values()] == [X.LIVE]

    def test_the_per_order_deadline_cancels_only_15m_orders(self, both):
        both.open15()
        v5 = both.open5()
        for off in (40.0, 70.0, 100.0):
            both.h.tick(off)
        assert both.venue.cancelled == []            # inside the 120 s TTL
        out = both.h.tick(134.3)
        assert out["maybe_acc_deadline"] is not None
        assert both.venue.cancelled == ["v1", "v2"]
        self.assert_5m_untouched(both, v5)

    def test_a_whole_15m_window_of_ticks_never_cancels_the_5m_order(self, both):
        both.open15()
        v5 = both.open5()
        for off in (40.0, 100.0, 134.3, 138.3, 170.0, 300.0, 400.0, 500.0,
                    584.0, 586.0, 590.0, 600.0):
            both.h.tick(off)
        assert both.venue.cancelled, "the 15m side did cancel its own orders"
        assert set(both.venue.cancelled) <= set(both.venue.ids("l5f-"))
        assert both.venue.resting("l5f-") == []
        self.assert_5m_untouched(both, v5)

    def test_the_completion_requote_cancels_only_its_15m_leg(self, both):
        h = both.h
        both.open15()
        v5 = both.open5()
        h.venue.fill(h.venue.oid("DOWN"))
        h.tick(13.0)
        assert h.pos("DOWN") == D("0.6") and h.pos("UP") == D(0)
        h.book.set("0.56", "0.57")
        up = "v2"
        assert h.venue.by_oid[up]["side"] == "UP"
        h.r.reconciled, h.r.last_reconcile_t = True, float(T.WS) + 31.0
        out = h.tick(35.9, poll=False)
        rq = out["maybe_completion_requote"]
        assert rq and rq.get("act") is True, (rq, dict(h.r.stats))
        assert both.venue.cancelled == [up]
        self.assert_5m_untouched(both, v5)


# ======================================================================
# D. ONE SIDE FAILING DOES NOT STOP THE OTHER
# ======================================================================
class TestOneFailingDoesNotStopTheOther:

    def test_a_5m_submit_failure_halts_5m_and_15m_trades_on(self, both):
        both.venue.refuse_markets.add(M5)
        rep = both.step5(at=5.0)
        assert [r["reason"] for r in rep["refused"]] == ["SUBMIT_UNCERTAIN"]
        assert both.ex.halted_reason.startswith("ORDER_UNCERTAIN")
        both.open15()                                   # asserts two placed
        both.venue.fill("v1")
        both.h.tick(13.0)
        assert both.h.pos("DOWN") == D("0.6")
        assert both.store15().orders_uncertain() is None
        # 5m stays halted and has nothing at the venue
        assert both.step5(at=14.0)["placed"] == []
        assert both.venue.ids("b5m-") == []

    def test_a_5m_read_error_halts_5m_and_15m_trades_on(self, both):
        both.open15()
        v5 = both.open5()
        both.venue.read_error[v5] = 1
        rep = both.step5(at=9.0)
        assert rep["halted_reason"].startswith("ORDER_UNCERTAIN")
        both.venue.fill("v1")
        both.h.tick(13.0)
        assert both.h.pos("DOWN") == D("0.6")
        out = both.h.tick(134.3)
        assert out["maybe_acc_deadline"] is not None
        assert both.venue.cancelled == ["v2"]           # its own stale leg
        assert both.store15().orders_uncertain() is None
        assert both.ex.halted_reason is not None        # 5m is still halted

    def test_a_15m_exception_does_not_stop_a_5m_step(self, both, monkeypatch):
        def boom(now):
            raise RuntimeError("15m stage failed")
        monkeypatch.setattr(both.h.r, "maybe_controller_open", boom)
        with pytest.raises(RuntimeError):
            both.h.tick(6.8)
        v5 = both.open5()
        assert both.venue.by_oid[v5]["market"] == M5
        assert both.ex.halted_reason is None

    def test_an_uncertain_15m_book_does_not_halt_5m(self, both):
        both.store15().mark_uncertain("15m cannot read one of its orders")
        both.h.at(6.5).l5_action()
        assert both.venue.sent == []                    # 15m is failed closed
        v5 = both.open5()
        both.venue.fill(v5)
        rep = both.step5(bid="0.55", ask="0.57", at=280.0)
        assert rep["inventory"]["UP"] == {"qty": D("0.17"),
                                          "cost": FIRST_5M_COST}
        assert both.ex.halted_reason is None

    def test_a_15m_venue_read_error_does_not_halt_5m(self, both):
        both.open15()
        v5 = both.open5()
        both.venue.read_error["v1"] = 3
        both.h.tick(13.0)
        assert both.ex.poll()["uncertain"] == []
        assert both.ex.halted_reason is None
        assert both.venue.order(v5)["state"] == "ORDER_STATE_NEW"

    def test_the_shared_operator_halt_stops_both_on_purpose(self, both,
                                                            monkeypatch):
        monkeypatch.setenv(X.HALT_ENV, "1")
        assert R.operator_halted() is True
        rep = both.step5(at=8.0)
        assert rep["arming_refusal"] == "L5_LIVE_HALT"
        assert both.venue.sent == []
        # and the 15m service's own spelling of "halted" halts 5m too
        monkeypatch.setenv(X.HALT_ENV, "true")
        assert R.operator_halted() is True
        assert both.ex.arming_refusal(M5, both.ex._now()) == "L5_LIVE_HALT"

    def test_the_shared_shadow_lock_is_read_the_same_way_by_both(
            self, both, monkeypatch):
        for value in ("0", "1", "", None):
            if value is None:
                monkeypatch.delenv(X.SHADOW_ONLY_ENV, raising=False)
            else:
                monkeypatch.setenv(X.SHADOW_ONLY_ENV, value)
            assert X.shadow_only_engaged() is R.shadow_only(), value


# ======================================================================
# E. ONE ACCOUNT CEILING, AND THE 5M SIDE SEES THE 15M EXPOSURE
# ======================================================================
class TestTheSharedAccountCeiling:

    def test_15m_orders_count_against_the_5m_account_check(self, both,
                                                           monkeypatch):
        both.open15()
        acct = both.ex.account_exposure()
        # two resting 0.6-contract legs, at $1 a contract: not ours to price
        assert acct["other_open_order_contracts"] == D("1.2")
        assert acct["other_usd"] == D("1.20") and acct["own_usd"] == 0
        assert acct["unknown"] is False
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "1.25")    # 1.20 + 0.0986 > 1.25
        rep = both.step5(at=8.0)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_EXPOSURE_CAP"]
        assert both.venue.ids("b5m-") == [] and both.ex.orders() == {}
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "1.30")
        both.open5(at=9.0)

    def test_a_15m_fill_is_still_seen_once_it_is_a_position(self, both,
                                                            monkeypatch):
        both.open15()
        both.venue.fill("v1")                           # 0.6 DOWN, 15m
        both.h.tick(13.0)
        acct = both.ex.account_exposure()
        assert acct["other_open_order_contracts"] == D("0.6")
        assert acct["other_position_contracts"] == D("0.6")
        assert acct["other_usd"] == D("1.20")
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "1.25")
        rep = both.step5(at=14.0)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_EXPOSURE_CAP"]
        assert both.venue.ids("b5m-") == []

    def test_the_5m_sides_own_fills_are_not_charged_twice(self, both):
        both.open15()
        v5 = both.open5()
        both.venue.fill(v5)
        both.ex.poll()
        acct = both.ex.account_exposure()
        assert acct["own_usd"] == FIRST_5M_COST          # exact, from the ledger
        assert acct["other_usd"] == D("1.20")            # the 15m legs only
        assert acct["total_usd"] == D("1.2986")


# ======================================================================
# F. CONCURRENT FILLS GO TO THE RIGHT LEDGER
# ======================================================================
class TestFillsAreBookedToTheirOwnLedger:

    def test_fills_on_both_markets_in_one_interval(self, both):
        both.open15()
        v5 = both.open5()
        both.venue.fill("v1")                # 15m DOWN 0.6 @ own 0.44
        both.venue.fill(v5)                  # 5m   UP   0.17 @ 0.58
        both.h.tick(13.0)
        rep = both.step5(bid="0.55", ask="0.57", at=280.0)
        # the 15m store: its own fill and nothing from the 5m market
        st = both.store15()
        assert st.position(M15, "DOWN") == D("0.6")
        assert st.position(M15, "UP") == D(0)
        assert st.position(M5, "UP") == D(0) and st.position(M5, "DOWN") == D(0)
        assert not any(k.startswith(M5) for k in st.state["positions"])
        assert D(str(st.state["leg_cost"]["%s|DOWN" % M15])) == D("0.264")
        # the 5m ledger: its own fill and nothing from the 15m market
        assert rep["inventory"]["UP"] == {"qty": D("0.17"), "cost": FIRST_5M_COST}
        assert rep["inventory"]["DOWN"]["qty"] == 0
        assert both.ex.inventory(M15) == {
            "UP": {"qty": D(0), "cost": D(0)},
            "DOWN": {"qty": D(0), "cost": D(0)}}
        assert set(both.ex._ledger["inventory"]) == {M5}
        # and the venue agrees with each, market by market
        pos = {r["marketSlug"]: r for r in both.venue.positions()}
        assert D(pos[M15]["qtySold"]) == D("0.6")
        assert D(pos[M5]["qtyBought"]) == D("0.17")

    def test_partial_fills_on_both_are_booked_separately(self, both):
        both.open15()
        v5 = both.open5()
        both.venue.fill("v2", "0.2")         # 15m UP, partly
        both.venue.fill(v5, "0.05")          # 5m UP, partly
        both.h.tick(13.0)
        got = both.ex.poll()
        assert [(f["qty"], f["cost"]) for f in got["fills"]] == [
            (D("0.05"), D("0.0290"))]
        assert both.store15().position(M15, "UP") == D("0.2")
        assert both.ex.inventory(M5)["UP"]["qty"] == D("0.05")
        assert both.store15().position(M5, "UP") == D(0)


# ======================================================================
# G. CHARACTERISATION: WHAT THE 15M RECONCILIATION DOES WITH 5M STATE
# ======================================================================
class TestCharacterise15mReconciliationOfForeign5mState:
    """NOT A FIX AND NOT AN ENDORSEMENT. `InventoryStore.reconcile_from_venue`
    treats the whole account as its own: it was written for one strategy on
    one account. These tests record what it does today with state the 5m
    adapter created, because that decides whether the two can share an
    account. `Runner.reconcile_if_needed` runs it at process start and again
    whenever the 15m book is uncertain or stalled."""

    def test_g_a_filled_5m_position_is_adopted_at_a_dollar_a_share(self, both):
        """OBSERVED: YES, IT IS ADOPTED.

        The 5m adapter fills 0.17 UP at 0.58 (cost 0.0986, in its own
        ledger). The next 15m reconciliation finds a market it never booked
        and calls `adopt_venue_position`: the 15m store takes the 0.17 UP as
        its own position in the 5m market, at $1.00 a share (0.17, not
        0.0986), lists the basis as unknown, pins a 0.17 risk floor, and
        marks its book uncertain -- which refuses 15m submissions until the
        following reconciliation (RECONCILE_RETRY_S later) agrees with the
        venue. The position then STAYS in the 15m store, counted in its
        account-wide risk against `max_total_exposure_usd`, and the same
        fill is now held by both ledgers.
        """
        h, st = both.h, both.store15()
        v5 = both.open5(at=5.0)
        both.venue.fill(v5)
        assert both.ex.poll()["fills"][0]["cost"] == FIRST_5M_COST
        assert st.state["positions"] == {}
        h.at(6.0)
        h.r.reconcile_if_needed(h.t)
        key = "%s|UP" % M5
        assert st.state["positions"] == {key: "0.17"}                # adopted
        assert st.state["leg_cost"] == {key: "0.17"}                 # $1/share
        assert D(st.state["leg_cost"][key]) == D("0.17") * E.UNKNOWN_PRICE_WORST_CASE
        assert st.state["leg_cost_basis_unknown"] == [key]
        # labelled from evidence only. The 5m order filled before this
        # reconciliation ever saw it, so nothing here proves whose it was:
        # the position is UNKNOWN, at the bound, and still counted.
        leg = st.position_origins()["legs"][key]
        assert leg["origin"] == "UNKNOWN"
        assert leg["cost_basis"] == "UPPER_BOUND_1USD_PER_CONTRACT"
        assert st.state["risk_floor_usd"] == {M5: "0.17"}
        assert st.position(M5, "UP") == D("0.17")
        assert st.risk_exposure_usd(M5) == D("0.17")
        assert M5 in st.markets_with_exposure()
        why = st.orders_uncertain()
        assert why.startswith("gross/net divergence ['%s']" % M5), why
        # ... which fails the 15m side closed for that interval
        h.at(6.5).l5_action()
        assert both.venue.ids("l5f-") == []
        assert any("BOOK_UNCERTAIN" in k for k, n in h.r.stats.items() if n)
        # the next reconciliation agrees with the venue and clears the mark,
        # but the adopted position is still there
        h.at(6.0 + R.RECONCILE_RETRY_S + 1.0)
        h.r.reconcile_if_needed(h.t)
        assert st.orders_uncertain() is None
        assert st.state["positions"] == {key: "0.17"}
        assert st.state["leg_cost"] == {key: "0.17"}
        risk = st.account_wide_risk_by_market(
            extra_market=M15, extra_side="UP", extra_qty=D("0.6"),
            extra_price=D("0.50"))
        assert risk["ok"] and risk["risk_by_market"][M5] == D("0.17")
        # 15m trades again, carrying the 5m position in its own book
        h.at(26.0).l5_action()
        assert len(both.venue.ids("l5f-")) == 1
        # while the 5m ledger holds the same fill at its real cost
        assert both.ex.inventory(M5)["UP"] == {"qty": D("0.17"),
                                              "cost": FIRST_5M_COST}

    def test_a_resting_5m_order_whose_row_states_no_side_no_longer_stops_15m(
            self, both):
        """WAS: with the stock double's row shape (no side on the row) the
        15m reconciliation tried to adopt the b5m order, raised
        `UnknownSide`, and every 15m submission was refused for as long as a
        5m order was resting. NOW, with nothing configured: the order is
        foreign by its id, is never read as one of ours, and 15m carries
        on. The 5m order is neither adopted nor cancelled."""
        h, st = both.h, both.store15()
        v5 = both.open5(at=5.0)
        (coid5,) = both.ex.orders()
        h.at(6.0)
        h.r.reconcile_if_needed(h.t)
        assert st.orders_uncertain() is None
        assert h.r.reconciled is True
        assert st.live_orders() == {}
        assert list(st.state["foreign_orders"]) == [coid5]
        assert st.state["foreign_orders"][coid5]["origin"] == "BTC5M"
        h.at(6.5).l5_action()
        assert len(both.venue.ids("l5f-")) == 1            # 15m is NOT stopped
        assert both.venue.cancelled == []
        assert both.venue.order(v5)["state"] == "ORDER_STATE_NEW"

    def test_a_resting_5m_order_is_never_adopted_and_never_swept(self, both):
        """WAS: with rows that state the side, as real PMUS rows do, a 15m
        reconciliation ADOPTED the b5m order, counted it against its own
        live-order ceiling, and the unscoped terminal sweep CANCELLED it.
        NOW, with nothing configured: listed as foreign, never adopted,
        never counted as ours, never cancelled -- through the per-order
        deadline and the terminal sweep alike."""
        both.venue.rows_state_side = True
        h, st = both.h, both.store15()
        both.open15()
        v5 = both.open5()
        (coid5,) = both.ex.orders()
        h.r.reconciled = False                    # e.g. a 15m process restart
        h.tick(30.0)
        live = st.live_orders()
        assert coid5 not in live
        assert all(c.startswith("l5f-") for c in live)
        assert st.owns_venue_order_id(v5) is False
        assert st.still_fillable_order_count() == 2          # its own two
        assert st.state["foreign_orders"][coid5]["origin"] == "BTC5M"
        for off in (134.3, 300.0, 500.0, 584.0, 586.0, 590.0):
            h.tick(off)
        assert v5 not in both.venue.cancelled
        assert both.venue.cancelled                 # it did cancel its own
        assert all(str(both.venue.by_oid[o]["client_order_id"]
                       ).startswith("l5f-") for o in both.venue.cancelled)
        both.ex.poll()
        assert both.ex.orders()[coid5]["state"] != X.CANCELLED

    def test_a_5m_fill_is_booked_at_its_price_by_the_5m_ledger_only(self, both):
        """WAS: once adopted, the 15m poll read the b5m order like one of
        its own and booked its fill into the 15m store too -- one fill, two
        ledgers at the fill price. NOW: the 15m poll never reads the order.
        The 5m ledger books the fill at its price. The 15m ledger, which is
        also the account-wide one, sees only the venue's POSITION on its
        next reconciliation and holds it at the $1 bound, labelled BTC5M
        from the order the venue had listed there."""
        both.venue.rows_state_side = True
        h, st = both.h, both.store15()
        both.open15()
        v5 = both.open5()
        h.r.reconciled = False
        h.tick(30.0)
        assert st.state["foreign_orders_seen"] == {M5: "BTC5M"}
        both.venue.fill(v5)
        LR.book_fills(h, 33.5)
        assert st.position(M5, "UP") == D(0)        # not booked by the 15m poll
        both.ex.poll()
        assert both.ex.inventory(M5)["UP"] == {"qty": D("0.17"),
                                              "cost": FIRST_5M_COST}
        # the account-wide view: adopted from the venue's position, at $1
        h.r.reconciled = False
        h.at(40.0 + R.RECONCILE_RETRY_S + 1.0)
        h.r.reconcile_if_needed(h.t)
        key = "%s|UP" % M5
        assert st.state["positions"][key] == "0.17"
        assert D(st.state["leg_cost"][key]) == D("0.17")
        leg = st.position_origins()["legs"][key]
        assert leg["origin"] == "BTC5M"
        assert leg["cost_basis"] == "UPPER_BOUND_1USD_PER_CONTRACT"
        assert leg["evidence"] == "THAT_STRATEGYS_ORDER_LISTED_BY_VENUE_IN_MARKET"


# ======================================================================
# H. THE RULE IS MANDATORY: another registered strategy's orders are foreign
#    by a code registry. No variable has to be set for it to hold, and none
#    can turn it off (owner decision, 2026-11-11).
# ======================================================================
from src import l5_live_execution as _E15
from src import l5_origin as _OG
from src import l5_series_registry as _REG


class TestForeignOrdersAreNeverAdopted:

    #: EVERY registered strategy other than our own. Grew from ("b5m-",)
    #: on 2026-11-15 when ETH/SOL/XRP 5m were registered, and the growth is
    #: a SAFETY GAIN, not a widening of what we may do: a prefix in this
    #: set is one we must NEVER adopt or cancel. l5-pmus-live spent hours
    #: today adopting and cancelling orders it did not recognise, because
    #: its foreign filter knew only one other series.
    REG = ("b5m-", "e5m-", "s5m-", "x5m-")

    @pytest.mark.parametrize("raw,want", [
        (None, REG), ("", REG), ("b5m-", REG),
        (" b5m- , x9- ", REG + ("x9-",)),          # the variable can only ADD
        ("x9-", REG + ("x9-",)),
        ("l5f-", REG),                     # our own prefix is never foreign
        ("b5m-,l5f-,b5m-", REG),
        ("B5M-", REG), ("b5m", REG), ("-", REG),
        ("a-", REG), ("muchtoolongprefix-", REG),
        ("b5m-*", REG), ("0", REG), ("off", REG),
    ])
    def test_the_registry_always_applies_and_the_variable_only_adds(
            self, monkeypatch, raw, want):
        if raw is None:
            monkeypatch.delenv(_E15.FOREIGN_COID_PREFIXES_ENV, raising=False)
        else:
            monkeypatch.setenv(_E15.FOREIGN_COID_PREFIXES_ENV, raw)
        assert _E15.foreign_coid_prefixes() == want
        assert _E15.is_foreign_coid("b5m-" + "0" * 24) is True
        assert _E15.is_foreign_coid("l5f-" + "0" * 24) is False
        assert _E15.is_foreign_coid("") is False
        assert _E15.is_foreign_coid("manual-1") is False

    def test_the_registry_is_the_one_in_l5_origin(self):
        assert _E15.OWN_STRATEGY == _OG.BTC15M
        assert _E15.OWN_COID_PREFIX == _OG.STRATEGY_COID_PREFIX[_OG.BTC15M] \
            == "l5f-"
        assert X.COID_PREFIX == _OG.STRATEGY_COID_PREFIX[_OG.BTC5M] == "b5m-"
        assert _E15.OWN_COID_PREFIX not in _E15.foreign_coid_prefixes()
        assert X.COID_PREFIX in _E15.foreign_coid_prefixes()
        # every OTHER registered strategy is foreign to this one, so a new
        # series cannot be adopted by an older lane simply by existing
        for r in _REG.REGISTRY:
            if r.strategy != _E15.OWN_STRATEGY:
                assert r.coid_prefix in _E15.foreign_coid_prefixes()

    def test_a_side_less_5m_row_does_not_stop_15m(self, both):
        both.venue.rows_state_side = False
        h, st = both.h, both.store15()
        both.open15()
        both.open5()
        h.r.reconciled = False
        h.tick(30.0)                               # must not raise or halt
        assert all(c.startswith("l5f-") for c in st.live_orders())
        assert st.still_fillable_order_count() == 2

    def test_an_order_of_our_own_is_still_adopted(self, both):
        """The rule is by name: a 15m order the file lost is recovered as
        before."""
        both.venue.rows_state_side = True
        h, st = both.h, both.store15()
        both.open15()
        mine = [str(o["venue_order_id"]) for o in st.live_orders().values()]
        assert len(mine) == 2
        st.state["live_orders"] = {}               # the file lost them
        h.r.reconciled = False
        h.tick(30.0)
        assert all(st.owns_venue_order_id(v) for v in mine)
        assert all(c.startswith("l5f-") for c in st.live_orders())

    def test_a_foreign_resting_order_is_counted_in_account_exposure(
            self, both):
        """Not adopted is not the same as not counted. The 5m order's 0.17
        remaining contracts are charged at $1 each in the 15m service's
        account-wide figure, under their own market."""
        both.venue.rows_state_side = True
        h, st = both.h, both.store15()
        both.open5()
        h.r.reconciled = False
        h.tick(30.0)
        risk = st.account_wide_risk_by_market()
        assert risk["ok"] is True
        assert risk["foreign_orders_usd"] == "0.17"
        assert risk["foreign_orders_usd_by_origin"] == {"BTC5M": "0.17"}
        assert risk["risk_by_market"][M5] == D("0.17")
        assert D(risk["total_usd"]) == D("0.17")
        # and a 15m order priced on top of it sees less room, not the same
        more = st.account_wide_risk_by_market(
            extra_market=M15, extra_side="UP", extra_qty=D("0.6"),
            extra_price=D("0.50"))
        assert more["risk_by_market"][M15] > 0
        assert D(more["total_usd"]) == D("0.17") + more["risk_by_market"][M15]

    @pytest.mark.parametrize("remaining", [None, "", "x", "-1", "NaN"])
    def test_a_foreign_order_we_cannot_size_makes_exposure_unknown(
            self, both, remaining):
        st = both.store15()
        st.state["foreign_orders"] = {
            "b5m-" + "9" * 24: {"origin": "BTC5M", "market": M5,
                                "remaining": remaining}}
        risk = st.account_wide_risk_by_market()
        assert risk["ok"] is False
        assert risk["reason"].startswith("FOREIGN_ORDER_UNREADABLE:")


# ======================================================================
# I. THE SAME, WITH ROWS SHAPED LIKE THE REAL VENUE'S: no client order id.
#    The posted order body carries none, so the venue cannot echo one. A
#    rule that needs the prefix on the row would pass every test above and
#    fail on the venue. These rows carry only what a real row carries: the
#    venue's id, the market, the side and the quantities.
# ======================================================================
class TestIsolationOnRowsWithoutClientIds:

    @pytest.fixture(autouse=True)
    def real_rows(self, both):
        both.venue.rows_carry_client_id = False
        both.venue.rows_state_side = True

    def test_the_posted_order_body_carries_no_client_order_id(self):
        """Why this section exists, pinned: if the body ever gains one, the
        prefix becomes available on the venue and this can be revisited."""
        from src import l5_order_body as OB
        body = OB.from_intent({"client_order_id": "l5f-" + "0" * 24,
                               "market": M15, "side": "UP", "price": "0.50",
                               "qty": "0.6", "mode": "MAKER"}, M15)
        assert not any("client" in k.lower() for k in body)
        assert "l5f-" not in str(body)

    def test_a_5m_order_is_foreign_by_its_market_and_is_never_swept(self, both):
        h, st = both.h, both.store15()
        both.open15()
        v5 = both.open5()
        h.r.reconciled = False                    # e.g. a 15m process restart
        h.tick(30.0)
        live = st.live_orders()
        assert all(c.startswith("l5f-") for c in live) and len(live) == 2
        assert all(o["market"] == M15 for o in live.values())
        assert st.owns_venue_order_id(v5) is False
        assert st.still_fillable_order_count() == 2
        (frow,) = st.state["foreign_orders"].values()
        assert frow["venue_order_id"] == v5 and frow["market"] == M5
        assert frow["foreign_because"] == "MARKET_SERIES"
        assert frow["series_strategy"] == "BTC5M"
        # whose it is cannot be read off a row with no id: only "not ours"
        assert frow["origin"] == "UNKNOWN"
        for off in (134.3, 300.0, 500.0, 584.0, 586.0, 590.0):
            h.tick(off)
        assert v5 not in both.venue.cancelled
        assert both.venue.cancelled and all(
            both.venue.by_oid[o]["market"] == M15
            for o in both.venue.cancelled)
        both.ex.poll()
        (row5,) = both.ex.orders().values()
        assert row5["state"] != X.CANCELLED

    def test_it_is_still_counted_in_account_exposure(self, both):
        h, st = both.h, both.store15()
        both.open5()
        h.r.reconciled = False
        h.tick(30.0)
        risk = st.account_wide_risk_by_market()
        assert risk["ok"] and risk["foreign_orders_usd"] == "0.17"
        assert risk["foreign_orders_usd_by_origin"] == {"UNKNOWN": "0.17"}
        assert risk["risk_by_market"][M5] == D("0.17")

    def test_our_own_orders_are_still_ours_by_their_venue_ids(self, both):
        h, st = both.h, both.store15()
        both.open15()
        mine = sorted(str(o["venue_order_id"])
                      for o in st.live_orders().values())
        h.r.reconciled = False
        h.tick(30.0)
        live = st.live_orders()
        assert sorted(str(o["venue_order_id"]) for o in live.values()) == mine
        assert all(c.startswith("l5f-") for c in live)
        assert not any(o.get("adopted") for o in live.values())
        assert st.state["foreign_orders"] == {}

    def test_an_untracked_order_in_our_own_series_is_adopted_as_before(
            self, both):
        """UNCHANGED, and stated so nobody reads more into the rule than it
        does: a row in a 15-minute market that this ledger does not track
        -- one of its own that the file lost, or one placed by hand -- is
        still adopted. The ledger cannot tell those two apart, and losing
        track of its own order is the worse error."""
        h, st = both.h, both.store15()
        both.open15()
        mine = sorted(str(o["venue_order_id"])
                      for o in st.live_orders().values())
        st.state["live_orders"] = {}               # the file lost them
        st.state.pop(E.VENUE_ORDER_LINK, None)
        st.state["submitted"] = {}
        h.r.reconciled = False
        h.tick(30.0)
        got = {str(o["venue_order_id"]): o for o in st.live_orders().values()}
        assert set(mine) <= set(got)
        assert all(got[v]["adopted"] for v in mine)
        assert st.state["foreign_orders"] == {}

    def test_a_5m_fill_reaches_the_15m_ledger_as_an_unknown_position(
            self, both):
        """What the 15-minute service's report will actually show in
        production: with no id on the row there is no evidence of whose the
        position is, so it is UNKNOWN at the $1 bound -- counted, never
        attributed. The 5-minute result is the 5-minute ledger's to state."""
        h, st = both.h, both.store15()
        v5 = both.open5()
        h.r.reconciled = False
        h.tick(30.0)
        assert st.state.get("foreign_orders_seen") in (None, {})
        both.venue.fill(v5)
        both.ex.poll()
        h.r.reconciled = False
        h.at(40.0 + R.RECONCILE_RETRY_S + 1.0)
        h.r.reconcile_if_needed(h.t)
        key = "%s|UP" % M5
        assert st.state["positions"][key] == "0.17"
        leg = st.position_origins()["legs"][key]
        assert leg["origin"] == "UNKNOWN"
        assert leg["cost_basis"] == "UPPER_BOUND_1USD_PER_CONTRACT"
        assert both.ex.inventory(M5)["UP"] == {"qty": D("0.17"),
                                              "cost": FIRST_5M_COST}

    def test_the_5m_adapter_never_touches_a_15m_order_either(self, both):
        h = both.h
        both.open15()
        both.open5()
        for _ in range(3):
            both.ex.poll()
        rep = both.ex.end_of_window(M5)
        assert [c["market"] for c in rep["cancelled"]] == [M5]
        assert all(both.venue.by_oid[o]["market"] == M5
                   for o in both.venue.cancelled)
        acct = both.ex.account_exposure()
        assert acct["unknown"] is False


# ======================================================================
# E2. FOREIGN EXPOSURE: REPORTED
# ======================================================================
class TestForeignExposureReported:
    """L5_BTC5M_FOREIGN_EXPOSURE=REPORTED stops charging another lane's
    inventory against OUR ceiling, and replaces that charge with the
    venue's own buying power as the bound on the shared account.

    Section E above pins CHARGED, which stays the default. These tests
    pin what REPORTED changes and -- more importantly -- what it does
    NOT: our own exposure still meets the owner's ceiling, foreign
    inventory is still read and still reported, an unreadable foreign
    book still makes the account UNKNOWN, and cash that cannot be read
    refuses rather than reading as unlimited."""

    @pytest.fixture(autouse=True)
    def _parser(self, monkeypatch):
        """The REAL balances parser, as a class attribute: the adapter
        reads it off `type(transport)` and may not import the live
        transport itself, so the schema under test here is the one the
        live venue uses and not a second implementation."""
        from src.l5_pmus_transport import PmusTransport
        monkeypatch.setattr(SharedVenue, "buying_power",
                            staticmethod(PmusTransport.buying_power),
                            raising=False)

    @staticmethod
    def _cash(both, payload):
        """Teach the shared venue to answer /v1/account/balances."""
        both.venue.account = lambda: payload

    def test_default_is_charged_and_unchanged(self, both, monkeypatch):
        monkeypatch.delenv(X.FOREIGN_EXPOSURE_ENV, raising=False)
        assert X.foreign_exposure_mode() == X.FOREIGN_CHARGED
        both.open15()
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "1.25")
        rep = both.step5(at=8.0)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_EXPOSURE_CAP"]
        assert rep["exposure"]["foreign_mode"] == X.FOREIGN_CHARGED

    def test_junk_and_lowercase_values(self, both, monkeypatch):
        for raw, want in (("", X.FOREIGN_CHARGED), ("charged", X.FOREIGN_CHARGED),
                          ("yes", X.FOREIGN_CHARGED), ("1", X.FOREIGN_CHARGED),
                          ("REPORT", X.FOREIGN_CHARGED),
                          ("reported", X.FOREIGN_REPORTED),
                          (" Reported ", X.FOREIGN_REPORTED),
                          ("REPORTED", X.FOREIGN_REPORTED)):
            monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, raw)
            assert X.foreign_exposure_mode() == want, raw

    def test_reported_lets_the_order_through_the_same_block(self, both,
                                                            monkeypatch):
        """The exact live block: foreign inventory over the ceiling, $0 of
        our own risk. CHARGED refuses; REPORTED places."""
        both.open15()
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "1.25")       # 1.20 foreign
        assert [r["reason"] for r in both.step5(at=8.0)["refused"]] == \
            ["ACCOUNT_EXPOSURE_CAP"]
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        self._cash(both, {"balances": [{"buyingPower": "500.00"}]})
        rep = both.step5(at=9.0)
        assert rep["refused"] == [], rep
        assert [(p["side"], D(p["price"]), D(p["qty"]))
                for p in rep["placed"]] == [FIRST_5M]
        assert rep["exposure"]["foreign_mode"] == X.FOREIGN_REPORTED
        assert rep["exposure"]["buying_power_usd"] == "500.00"

    def test_foreign_inventory_is_still_read_and_reported(self, both,
                                                          monkeypatch):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        self._cash(both, {"balances": [{"buyingPower": "500.00"}]})
        both.open15()
        both.venue.fill("v1")
        both.h.tick(13.0)
        rep = both.step5(at=14.0)
        acct = rep["exposure"]["account"]
        assert acct["other_open_order_contracts"] == D("0.6")
        assert acct["other_position_contracts"] == D("0.6")
        assert acct["other_usd"] == D("1.20")               # priced, not charged
        assert rep["refused"] == [], rep

    def test_the_ceiling_still_bounds_us_under_reported(self, both,
                                                        monkeypatch):
        """A ceiling under our own first leg stops us even with foreign
        inventory no longer charged and cash to spare.

        The guard that fires here is the CONTROLLER's exposure-room check,
        which is strictly earlier than the money path and reads the same
        ceiling; REPORTED does not widen it. The money path's own
        ACCOUNT_EXPOSURE_CAP under REPORTED is pinned in
        test_l5_btc5m_execution, where the decision can be scripted past
        this gate."""
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        self._cash(both, {"balances": [{"buyingPower": "500.00"}]})
        both.open15()
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.05")       # under one 5m leg
        rep = both.step5(at=8.0)
        assert rep["refused"] == [] and rep["placed"] == []
        assert (rep["action"], rep["why"]) == ("HOLD_OR_SKIP",
                                              "NO_EXPOSURE_ROOM")
        assert D(str(rep["entry_eval"]["cap_usd"])) == D("0.05")
        assert both.venue.ids("b5m-") == [] and both.ex.orders() == {}

    def test_unreadable_cash_refuses(self, both, monkeypatch):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        both.open15()
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "1.25")

        def boom():
            raise ConnectionError("venue did not answer")

        both.venue.account = boom
        rep = both.step5(at=8.0)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_CASH_UNREADABLE"]
        assert both.venue.ids("b5m-") == []
        # a shape the parser does not know is equally unreadable
        both.venue.account = lambda: {"somethingElse": 1}
        rep = both.step5(at=8.5)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_CASH_UNREADABLE"]
        assert rep["exposure"]["buying_power_usd"] is None
        assert both.venue.ids("b5m-") == []

    def test_cash_short_refuses(self, both, monkeypatch):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        both.open15()
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "10")
        self._cash(both, {"balances": [{"buyingPower": "0.05"}]})
        rep = both.step5(at=8.0)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_CASH_SHORT"]
        assert D(str(rep["refused"][0]["buying_power_usd"])) == D("0.05")
        assert both.venue.ids("b5m-") == []
        self._cash(both, {"balances": [{"buyingPower": "500.00"}]})
        both.open5(at=9.0)

    def test_an_unreadable_foreign_book_still_halts_us(self, both,
                                                       monkeypatch):
        """REPORTED weakens the CHARGE, never the READ. If we cannot see
        the account we do not place, whatever our own cash says."""
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        self._cash(both, {"balances": [{"buyingPower": "500.00"}]})

        def boom():
            raise ConnectionError("venue did not answer")

        both.venue.open_orders = boom
        rep = both.step5(at=8.0)
        assert [r["reason"] for r in rep["refused"]] == ["ACCOUNT_STATE_UNKNOWN"]
        assert both.venue.ids("b5m-") == []

    def test_an_unset_ceiling_still_refuses_under_reported(self, both,
                                                           monkeypatch):
        monkeypatch.setenv(X.FOREIGN_EXPOSURE_ENV, "REPORTED")
        self._cash(both, {"balances": [{"buyingPower": "500.00"}]})
        monkeypatch.delenv(X.ACCOUNT_CAP_ENV, raising=False)
        rep = both.step5(at=8.0)
        assert [r["reason"] for r in rep["refused"]] == \
            ["ACCOUNT_CAP_UNSET_OR_UNREADABLE"]
        assert both.venue.ids("b5m-") == []
