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
import importlib.util as _ilu
import pathlib as _pl

# the adapter is a PROTOTYPE outside src/ (see prototypes/README.md)
_spec = _ilu.spec_from_file_location(
    "l5_btc5m_execution_prototype",
    _pl.Path(__file__).resolve().parents[1] / "prototypes"
    / "l5_btc5m_execution.py")
X = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(X)
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
        if at is not None:
            self.h.at(at)
        return self.ex.step(
            market=M5, book={"bid": bid, "ask": ask, "age_s": 0.1},
            t_in_window_s=self.h.t - float(T.WS), p_up=p_up, p_source="TEST")

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

    def test_a_resting_5m_order_whose_row_states_no_side_stops_15m(self, both):
        """OBSERVED with the stock double's row shape (no side on the row).
        The 15m reconciliation tries to adopt the b5m order, cannot read a
        side, raises `UnknownSide`, and the 15m book stays uncertain: every
        15m submission is refused for as long as a 5m order is resting. The
        5m order itself is neither adopted nor cancelled."""
        h, st = both.h, both.store15()
        v5 = both.open5(at=5.0)
        h.at(6.0)
        h.r.reconcile_if_needed(h.t)
        assert st.orders_uncertain() == "reconcile raised UnknownSide"
        assert h.r.reconciled is False
        assert st.live_orders() == {}
        h.at(6.5).l5_action()
        assert both.venue.ids("l5f-") == []                 # 15m is stopped
        h.at(6.0 + R.RECONCILE_RETRY_S + 1.0)
        h.r.reconcile_if_needed(h.t)
        assert st.orders_uncertain() == "reconcile raised UnknownSide"
        assert both.venue.cancelled == []
        assert both.venue.order(v5)["state"] == "ORDER_STATE_NEW"

    def test_a_resting_5m_order_whose_row_states_its_side_is_adopted_and_swept(
            self, both):
        """OBSERVED with rows that state the side, as real PMUS rows do.
        A 15m reconciliation that runs while a b5m order rests ADOPTS it
        into the 15m `live_orders` (`adopted: True`). From then on the 15m
        store answers `owns_venue_order_id` True for it, counts it against
        its own live-order ceiling, and the unscoped terminal sweep
        (`maybe_ts_deadline` -> `deadman`) CANCELS it. So the property in
        section C does not survive a 15m reconciliation: the 15m runner can
        cancel a 5m order."""
        both.venue.rows_state_side = True
        h, st = both.h, both.store15()
        both.open15()
        v5 = both.open5()
        (coid5,) = both.ex.orders()
        h.r.reconciled = False                    # e.g. a 15m process restart
        h.tick(30.0)
        live = st.live_orders()
        assert coid5 in live and live[coid5]["adopted"] is True
        assert live[coid5]["market"] == M5
        assert st.owns_venue_order_id(v5) is True
        assert st.still_fillable_order_count() == 3      # 2 of its own + ours
        assert both.venue.cancelled == []
        # the per-order deadline is scoped to the 15m market: it spares it
        h.tick(134.3)
        assert both.venue.cancelled == ["v1", "v2"]
        # the terminal sweep is not scoped: it takes the 5m order
        for off in (300.0, 500.0, 584.0, 586.0, 590.0):
            h.tick(off)
        assert v5 in both.venue.cancelled
        swept = [rec for label, rec in h.emitted if label == "L5_TS_DEADLINE"]
        assert [c["client_order_id"] for c in swept[-1]["result"]["cancelled"]
                ] == [coid5]
        # the 5m ledger finds out only by reading its own order
        both.ex.poll()
        assert both.ex.orders()[coid5]["state"] == X.CANCELLED

    def test_an_adopted_5m_order_that_fills_is_booked_by_both_ledgers(self, both):
        """OBSERVED with rows that state the side. Once adopted, the 15m
        poll reads the b5m order like one of its own and books its fill at
        the venue's price into the 15m store, under the 5m market. The 5m
        ledger books the same fill. One 0.17 UP fill, two ledgers."""
        both.venue.rows_state_side = True
        h, st = both.h, both.store15()
        both.open15()
        v5 = both.open5()
        h.r.reconciled = False
        h.tick(30.0)
        both.venue.fill(v5)
        LR.book_fills(h, 33.5)
        key = "%s|UP" % M5
        assert st.position(M5, "UP") == D("0.17")
        assert D(str(st.state["leg_cost"][key])) == FIRST_5M_COST
        both.ex.poll()
        assert both.ex.inventory(M5)["UP"] == {"qty": D("0.17"),
                                              "cost": FIRST_5M_COST}


# ======================================================================
# H. THE FIX FOR WHAT SECTION G RECORDS: another strategy's orders are
#    named foreign and are never adopted. Default off.
# ======================================================================
from src import l5_live_execution as _E15


class TestForeignOrdersAreNotAdoptedWhenNamed:

    @pytest.mark.parametrize("raw,want", [
        (None, ()), ("", ()), ("b5m-", ("b5m-",)),
        (" b5m- , x9- ", ("b5m-", "x9-")),
        ("l5f-", ()),                      # our own prefix is never foreign
        ("b5m-,l5f-,b5m-", ("b5m-",)),
        ("B5M-", ()), ("b5m", ()), ("-", ()), ("a-", ()),
        ("muchtoolongprefix-", ()), ("b5m-*", ()),
    ])
    def test_only_well_formed_prefixes_other_than_our_own_count(
            self, monkeypatch, raw, want):
        if raw is None:
            monkeypatch.delenv(_E15.FOREIGN_COID_PREFIXES_ENV, raising=False)
        else:
            monkeypatch.setenv(_E15.FOREIGN_COID_PREFIXES_ENV, raw)
        assert _E15.foreign_coid_prefixes() == want
        assert _E15.is_foreign_coid("l5f-" + "0" * 24) is False
        assert _E15.is_foreign_coid("") is False

    def test_named_foreign_the_5m_order_is_listed_not_adopted_and_not_swept(
            self, both, monkeypatch):
        """The scenario of section G, with the 5m prefix named."""
        monkeypatch.setenv(_E15.FOREIGN_COID_PREFIXES_ENV, "b5m-")
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
        assert coid5 in (st.state.get("foreign_orders") or {})
        # its own deadline and its terminal sweep both leave the 5m order
        for off in (134.3, 300.0, 500.0, 584.0, 586.0, 590.0):
            h.tick(off)
        assert v5 not in both.venue.cancelled
        assert both.venue.cancelled            # it did cancel its own
        assert all(str(both.venue.by_oid[o]["client_order_id"]
                       ).startswith("l5f-") for o in both.venue.cancelled)
        both.ex.poll()
        assert both.ex.orders()[coid5]["state"] != X.CANCELLED

    def test_named_foreign_a_side_less_5m_row_no_longer_stops_15m(
            self, both, monkeypatch):
        monkeypatch.setenv(_E15.FOREIGN_COID_PREFIXES_ENV, "b5m-")
        both.venue.rows_state_side = False
        h, st = both.h, both.store15()
        both.open15()
        both.open5()
        h.r.reconciled = False
        h.tick(30.0)                               # must not raise or halt
        assert all(c.startswith("l5f-") for c in st.live_orders())
        assert st.still_fillable_order_count() == 2

    def test_an_order_of_our_own_is_still_adopted(self, both, monkeypatch):
        """The rule is by name: a 15m order the file lost is recovered as
        before."""
        monkeypatch.setenv(_E15.FOREIGN_COID_PREFIXES_ENV, "b5m-")
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
