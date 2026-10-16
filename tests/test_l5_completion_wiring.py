"""Patient completion through the REAL Runner methods.

The lesson from the queue measurement and the probe deadline is that writing
a decision function is not wiring it, so these call
Runner.ts_completion_inputs and Runner.maybe_ts_completion directly and
assert on what they produce -- including that a refusal leaves the existing
deadline cancellation untouched.
"""
from decimal import Decimal as D

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TSP

SLUG = "cpc-btc-updown-15m-2026-10-15-2000z"
WS = 1_792_094_400.0
CUT = WS + TSP.WINDOW_S - TSP.CANCEL_MARGIN_S          # start + 720


class FakeTransport:
    market_slug = SLUG
    armed = False
    tick_size = D("0.01")
    quantity_increment = D("0.01")


class FakeBook:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self, slug):
        return dict(self._snap) if self._snap is not None else None


class FakeStore:
    """Only what the completion inputs read."""
    def __init__(self, up="5", dn="0", up_cost="2.95", dn_cost="0",
                 attested=("UP", "DOWN"), committed=None, exposure=None):
        self._g = {"up": up, "dn": dn}
        self._lc = {"UP": D(up_cost), "DOWN": D(dn_cost)}
        self._attested = set(attested)
        self._committed = committed or {}
        self._exposure = exposure if exposure is not None else {
            "ok": True, "capacity_usd": "4.00"}

    def gross_inventory(self, market):
        return dict(self._g)

    def leg_cost(self, market):
        return dict(self._lc)

    def cost_attested(self, market, side):
        return side in self._attested

    def committed(self):
        return dict(self._committed)

    def account_wide_risk_by_market(self, **kw):
        return dict(self._exposure)


class Gates:
    max_cash_deployed_usd = D("10")
    max_worst_case_deficit_usd = D("3.60")


class Executor:
    validation_gates = Gates()


_KEEP = object()        # so snap=None means "no book", not "the default"


class Stub:
    """Only what the completion methods touch, so the real code runs."""
    TS_COMPLETION_CROSS_ENV = R.Runner.TS_COMPLETION_CROSS_ENV
    TS_COMPLETION_EVERY_S = R.Runner.TS_COMPLETION_EVERY_S
    ts_completion_cross_authorized = R.Runner.ts_completion_cross_authorized
    ts_completion_inputs = R.Runner.ts_completion_inputs
    maybe_ts_completion = R.Runner.maybe_ts_completion

    def __init__(self, store=None, snap=_KEEP):
        self.transport = FakeTransport()
        self.store = store or FakeStore()
        self.executor = Executor()
        self.book = FakeBook(BOOK if snap is _KEEP else snap)
        self.last_ts_completion_t = 0.0
        self.stats = __import__("collections").Counter()


BOOK = {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
        "ask_shares": "900", "stream_age_s": "0.05"}


@pytest.fixture(autouse=True)
def armed(monkeypatch):
    monkeypatch.setenv(TSP.EXPERIMENT_ENV, "TS-COMPLETION")
    monkeypatch.setenv(TSP.MARKET_ENV, SLUG)
    monkeypatch.delenv(R.Runner.TS_COMPLETION_CROSS_ENV, raising=False)
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start", lambda slug: WS)
    sent = []
    monkeypatch.setattr(R, "emit", lambda k, d: sent.append((k, d)))
    return sent


# ----------------------------------------------------------- the phasing

def test_nothing_is_decided_in_the_maker_phase(armed):
    s = Stub()
    assert s.maybe_ts_completion(WS + 60) is None
    assert armed == []


def test_the_decision_runs_inside_the_completion_window(armed):
    s = Stub()
    r = s.maybe_ts_completion(CUT - 30)
    assert r is not None and r["phase"] == "COMPLETION"
    assert [k for k, _ in armed] == ["L5_TS_COMPLETION"]


def test_nothing_is_decided_once_LATE(armed):
    """Past the cutoff nothing may rest, so there is nothing to complete."""
    s = Stub()
    assert s.maybe_ts_completion(CUT + 1) is None
    assert armed == []


def test_an_unauthorized_market_is_inert(armed, monkeypatch):
    monkeypatch.setenv(TSP.MARKET_ENV, "cpc-btc-updown-15m-2026-01-01-0000z")
    s = Stub()
    assert s.maybe_ts_completion(CUT - 30) is None


def test_the_decision_is_throttled_not_run_per_tick(armed):
    s = Stub()
    assert s.maybe_ts_completion(CUT - 60) is not None
    assert s.maybe_ts_completion(CUT - 59) is None      # inside the throttle
    assert s.maybe_ts_completion(
        CUT - 60 + Stub.TS_COMPLETION_EVERY_S + 1) is not None


# ----------------------------------------------- the own-outcome crossing

def test_a_DOWN_completion_crosses_at_one_minus_the_UP_BID(armed):
    """THE ARITHMETIC TRAP. Buying DOWN crosses the DOWN ask, which is
    1 - the UP bid, NOT the UP ask."""
    s = Stub()
    src = s.ts_completion_inputs(SLUG)
    assert src["ok"] and src["side"] == "DOWN"
    assert src["taker_price"] == D(1) - D("0.60")
    assert src["available_depth"] == "900"              # the BID side's depth


def test_an_UP_completion_crosses_at_the_UP_ASK(armed):
    s = Stub(store=FakeStore(up="0", dn="5", up_cost="0", dn_cost="1.90"))
    src = s.ts_completion_inputs(SLUG)
    assert src["ok"] and src["side"] == "UP"
    assert src["taker_price"] == D("0.61")
    assert src["available_depth"] == "900"


def test_the_blended_cost_per_share_is_used_not_the_total(armed):
    s = Stub(store=FakeStore(up="5", up_cost="2.95"))
    src = s.ts_completion_inputs(SLUG)
    assert src["up_cost_px"] == D("0.59")              # 2.95 / 5, not 2.95


# -------------------------------------------------- fail-closed, in the runner

def test_an_unattested_leg_refuses_rather_than_pricing_at_zero(armed):
    """A quantity adopted from the venue's net has no cost basis of ours."""
    s = Stub(store=FakeStore(up="5", up_cost="2.95", attested=()))
    src = s.ts_completion_inputs(SLUG)
    assert src["ok"] is False
    assert src["reason"] == "UNKNOWN_COST_BASIS"
    r = s.maybe_ts_completion(CUT - 30)
    assert r["reason"] == "UNKNOWN_COST_BASIS" and r["crossed"] is False


def test_unreadable_exposure_refuses(armed):
    s = Stub(store=FakeStore(exposure={"ok": False, "reason": "BOOM"}))
    src = s.ts_completion_inputs(SLUG)
    assert src["ok"] is False and src["reason"] == "EXPOSURE_UNREADABLE"


def test_a_missing_book_refuses_rather_than_assuming_a_price(armed):
    s = Stub(snap=None)
    src = s.ts_completion_inputs(SLUG)
    assert src["taker_price"] is None
    r = s.maybe_ts_completion(CUT - 30)
    assert r["ok"] is False
    assert r["reason"] in ("COMPLETION_PRICE_UNREADABLE", "BOOK_STALE")


def test_a_stale_book_refuses_in_the_runner_path(armed):
    s = Stub(snap=dict(BOOK, stream_age_s="9"))
    r = s.maybe_ts_completion(CUT - 30)
    assert r["ok"] is False and r["reason"] == "BOOK_STALE"


def test_insufficient_depth_is_reported_as_a_partial(armed):
    s = Stub(snap=dict(BOOK, bid_shares="2"))
    r = s.maybe_ts_completion(CUT - 30)
    assert r["ok"] is True and r["partial"] is True
    assert D(r["qty"]) == D("2")


def test_the_cash_headroom_is_the_SMALLER_of_the_two_bounds(armed):
    """The run-scoped cash bound and the account-wide exposure capacity are
    different quantities; the tighter one binds, recomputed now."""
    s = Stub(store=FakeStore(exposure={"ok": True, "capacity_usd": "0.50"}))
    src = s.ts_completion_inputs(SLUG)
    assert src["cash_headroom"] == D("0.50")           # not the gates' 10
    s2 = Stub(store=FakeStore(committed={SLUG: "9.80"}))
    assert s2.ts_completion_inputs(SLUG)["cash_headroom"] == D("0.20")


# ------------------------------------ crossing needs its own authorization

def test_an_economically_positive_cross_is_NOT_submitted_unarmed(armed):
    s = Stub()
    r = s.maybe_ts_completion(CUT - 30)
    assert r["ok"] is True                       # the economics say yes
    assert r["crossed"] is False                 # and nothing is placed
    assert r["authorized_to_cross"] is False
    assert r["armed"] is False


def test_authorization_alone_does_not_place_an_order_while_unarmed(armed,
                                                                  monkeypatch):
    monkeypatch.setenv(R.Runner.TS_COMPLETION_CROSS_ENV, "YES")
    s = Stub()
    r = s.maybe_ts_completion(CUT - 30)
    assert r["authorized_to_cross"] is True
    assert r["crossed"] is False                 # armed is still False
    assert s.transport.armed is False


def test_the_cross_flag_is_strict(armed, monkeypatch):
    s = Stub()
    for raw in ("", "no", "1", "true", "yes ", "Y"):
        monkeypatch.setenv(R.Runner.TS_COMPLETION_CROSS_ENV, raw)
        assert s.ts_completion_cross_authorized() is (raw.strip().upper()
                                                      == "YES")


# ---------------------------------------------------- restart and races

def test_a_restart_rereads_inventory_from_the_store_not_memory(armed):
    """Nothing about the decision is cached across a process: a fresh Stub
    on the same store reaches the same verdict from disk."""
    store = FakeStore(up="5", dn="0", up_cost="2.95")
    a = Stub(store=store).maybe_ts_completion(CUT - 30)
    b = Stub(store=store).maybe_ts_completion(CUT - 30)
    assert a["ok"] is b["ok"] is True
    assert a["qty"] == b["qty"] and a["price"] == b["price"]


def test_a_fill_arriving_between_evaluations_changes_the_verdict(armed):
    """The cancellation race in the shape that matters: the opposite leg
    fills while we were deciding to cross, so the next evaluation must find
    nothing left to complete rather than crossing on a stale deficit."""
    store = FakeStore(up="5", dn="0", up_cost="2.95")
    s = Stub(store=store)
    assert s.maybe_ts_completion(CUT - 90)["ok"] is True
    store._g = {"up": "5", "dn": "5"}            # the DOWN leg filled
    store._lc = {"UP": D("2.95"), "DOWN": D("1.90")}
    s.last_ts_completion_t = 0.0
    assert s.maybe_ts_completion(CUT - 30)["reason"] == "ALREADY_MATCHED"


def test_a_partial_fill_between_evaluations_shrinks_the_deficit(armed):
    store = FakeStore(up="5", dn="0", up_cost="2.95")
    s = Stub(store=store)
    assert D(s.maybe_ts_completion(CUT - 90)["qty"]) == D("5")
    store._g = {"up": "5", "dn": "3"}
    store._lc = {"UP": D("2.95"), "DOWN": D("1.14")}
    s.last_ts_completion_t = 0.0
    assert D(s.maybe_ts_completion(CUT - 30)["qty"]) == D("2")


# -------------------------------------- the deadline still owns the cancel

def test_completion_runs_BEFORE_the_deadline_sweep_in_tick():
    """Order matters: a completion decided after the cutoff has nothing left
    to complete against, so tick must evaluate it first."""
    import inspect
    src = inspect.getsource(R.Runner.tick)
    assert src.index("maybe_ts_completion") < src.index("maybe_ts_deadline")


def test_the_deadline_sweep_is_not_weakened_by_the_completion_path():
    """A refusal to cross must leave the terminal cancellation exactly as it
    was -- it is the only thing that stops a leg resting into expiry."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_ts_deadline)
    assert "ts_terminal_deadline" in src
    assert "TS_TERMINAL_DEADLINE" in src
    assert "completion" not in src.lower()
