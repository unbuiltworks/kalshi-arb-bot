"""The HOLD_OPEN_EXTEND canary: arming, every rule, every exit path.

No test here touches the network. The fake transport asserts on any path it
was not told about, so an unexpected mutation fails the test rather than
passing silently.
"""
import time
from decimal import Decimal as D

#: A window far enough ahead that the suite never depends on the wall clock.
#: The campaign's deadline is capped by the window close, so a slug from a
#: window that has already ended correctly refuses to trade -- which made
#: every test using a past slug fail once that cap existed.
FUTURE_SLUG = "cpc-btc-updown-15m-2099-01-01-1200z"

import pytest

from src import pmus_q1_canary as C
from src import pmus_q1_executor as X
from src import pmus_ws_live_state as WS


# ----------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv(X.STATE_ENV, str(tmp_path))
    yield


def _arm(monkeypatch, slug=FUTURE_SLUG):
    monkeypatch.setenv(X.LIVE_ENV, "YES")
    monkeypatch.setenv(X.SID_ENV, "sid-1")
    monkeypatch.setenv(X.ARM_ENV, "sid-1")
    monkeypatch.setenv(C.CANARY_ARM_ENV, "sid-1")
    monkeypatch.setenv(X.MARKET_ENV, slug)
    monkeypatch.setenv(X.GATE_ENV, slug)
    monkeypatch.setenv(X.KEY_ENV, "k")
    monkeypatch.setenv(X.SECRET_ENV, "s")
    return slug


def ws_state(bid, ask, *, slug=FUTURE_SLUG,
             bid_qty="100", ask_qty="100", now=None, trade=("0.41", "5")):
    """A LiveState holding one injected authenticated-WS book."""
    st = WS.LiveState()
    st.apply({"marketData": {"marketSlug": slug,
                             "bids": [{"px": {"value": str(bid)},
                                       "qty": str(bid_qty)}],
                             "offers": [{"px": {"value": str(ask)},
                                         "qty": str(ask_qty)}]}}, now=now)
    if trade:
        st.apply({"trade": {"marketSlug": slug,
                            "price": {"value": str(trade[0])},
                            "quantity": str(trade[1]),
                            "tradeTime": "2026-09-30T17:00:01Z",
                            "id": "t1"}}, now=now)
    return st


class StepState(WS.LiveState):
    """A LiveState whose book ADVANCES through a script on each snapshot.

    Needed because a static book makes the reactive-take branch fire before
    any complement can rest, so the cancel race is unreachable with one book.
    """

    def __init__(self, slug, books):
        super().__init__()
        self.slug, self.books, self.i = slug, list(books), 0
        self._push()

    def _push(self):
        bid, ask = self.books[min(self.i, len(self.books) - 1)]
        self.apply({"marketData": {
            "marketSlug": self.slug,
            "bids": [{"px": {"value": str(bid)}, "qty": "100"}],
            "offers": [{"px": {"value": str(ask)}, "qty": "100"}]}},
            now=time.time())

    def snapshot(self, slug, **kw):
        snap = super().snapshot(slug, **kw)
        self.i += 1
        self._push()
        return snap


class Fake:
    """Scripted venue. `books` is a list of (bid, ask) served in order."""

    def __init__(self, books, fills=None, tick="0.01"):
        self.books = list(books)
        self.fills = dict(fills or {})     # order_id -> fill dict or None
        self.tick = tick
        self.placed, self.cancelled, self.sweeps = [], [], 0
        self.bbo_calls = 0
        self.market_reads = 0
        self.swept = False
        #: a venue that ACKs cancels without applying them. The pathological
        #: subclasses set this; the honest default does not.
        self.stubborn = False
        self.n = 0
        self._next = 0

    def _book(self):
        i = min(self.n, len(self.books) - 1)
        return self.books[i]

    def request(self, method, path, body=None, **kw):
        if path.endswith("/bbo"):
            # Allowed ONLY as the pre-submit cross sanity check inside
            # place_limit_buy. Decisions must never reach here, so the
            # count is asserted on in the WS tests below.
            self.bbo_calls += 1
            self.n += 1
            bid, ask = self._book()
            return {"marketData": {"bestBid": {"value": bid},
                                   "bestAsk": {"value": ask}}}
        if path.startswith("/v1/market/slug/"):
            # the canonical grid route; the slug is matched on the row
            self.market_reads += 1
            return {"market": {"slug": path.rsplit("/", 1)[-1],
                               X.TICK_FIELD: self.tick}}
        if path.startswith("/v1/markets/") and path.count("/") == 3:
            self.market_reads += 1
            return {"market": {X.TICK_FIELD: self.tick}}
        if path == "/v1/order/preview":
            return {}
        if path == "/v1/orders":
            self._next += 1
            oid = f"O{self._next}"
            self.placed.append({"id": oid, "body": body})
            return {"id": oid}
        if path.endswith("/cancel") and path.startswith("/v1/order/"):
            self.cancelled.append(path.split("/")[3])
            return {}
        if path == "/v1/orders/open/cancel":
            self.sweeps += 1
            # a venue that HONOURS cancel-all: unfilled orders go terminal.
            # StubbornCancel / StubbornIOC model the venue that does not.
            self.swept = True
            return {}
        if path.startswith("/v1/orders/open"):
            return {"orders": []}
        if path.startswith("/v1/order/"):
            oid = path.split("/")[3]
            f = self.fills.get(oid)
            if f is None:
                if not self.stubborn and (oid in self.cancelled or self.swept):
                    return {"id": oid, "state": "ORDER_STATE_CANCELLED",
                            "cumQuantity": "0"}
                return {"id": oid, "state": "ORDER_STATE_OPEN"}
            return f
        if path == "/v1/account/balances":
            return {"balances": [{"buyingPower": {"value": "50"}}]}
        if path.startswith("/v1/portfolio/positions"):
            return {"positions": []}
        if path.startswith("/v1/orders/open"):
            return {"orders": []}
        raise AssertionError(f"unexpected call {method} {path}")


def _filled(px, qty=1, side="OUTCOME_SIDE_YES"):
    """The venue's real filled-order shape, as `authoritative_fill` parses it.

    `avgPx` is the LONG-side price, so a NO order's fill is served as
    1 - (NO price). Building the fake any other way would test a schema the
    venue does not send.
    """
    wire = X.ONE - D(str(px)) if side.endswith("NO") else D(str(px))
    return {"state": "ORDER_STATE_FILLED", "outcomeSide": side,
            "avgPx": {"value": str(wire)}, "filledQuantity": str(qty)}


def _clock():
    t = {"v": 0.0}

    def c():
        t["v"] += 0.5
        return t["v"]
    return c


# ------------------------------------------------------------------- arming

def test_disarmed_without_the_canary_specific_key(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.delenv(C.CANARY_ARM_ENV)
    out = C.run_canary(t=Fake([("0.40", "0.60")]), clock=_clock(),
                       sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60"), max_book_age_s=1e9)
    assert out["status"] == "DISARMED"


def test_the_executors_own_dual_key_is_not_sufficient(monkeypatch):
    """The canary behaviour is separately authorized, so it needs its own key."""
    _arm(monkeypatch)
    monkeypatch.setenv(C.CANARY_ARM_ENV, "a-different-id")
    assert C.canary_armed() is False


def test_gate_must_equal_the_market(monkeypatch):
    slug = _arm(monkeypatch)
    monkeypatch.setenv(X.GATE_ENV, slug + "-other")
    out = C.run_canary(t=Fake([("0.40", "0.60")]), clock=_clock(),
                       sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60"), max_book_age_s=1e9)
    assert out["status"] == "GATE_NOT_CLEARED"


def test_a_disarmed_run_makes_no_network_call(monkeypatch):
    class Explode:
        def request(self, *a, **k):
            raise AssertionError("network touched while disarmed")
    out = C.run_canary(t=Explode(), clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60"), max_book_age_s=1e9)
    assert out["status"] == "DISARMED"


# -------------------------------------------------------- rule 7: no EXTEND

def test_extend_is_structurally_impossible_at_the_residual_cap():
    inv = C.Inventory()
    assert C.extend_permitted(inv) is True          # flat: an OPEN, not extend
    inv.add("yes", D(1), D("0.40"))
    assert C.extend_permitted(inv) is False         # residual 1 == cap


# -------------------------------------------------- rule 2: hold, not chase

def test_a_better_touch_is_not_a_reason_to_move():
    bbo = {"bid": "0.45", "ask": "0.60"}
    assert C.open_still_valid(D("0.40"), bbo) is True


def test_an_out_of_band_rest_is_given_up():
    assert C.open_still_valid(D("0.90"), {"bid": "0.90", "ask": "0.95"}) is False


def test_a_rest_that_would_now_cross_is_given_up():
    assert C.open_still_valid(D("0.60"), {"bid": "0.55", "ask": "0.60"}) is False


def test_the_canary_holds_across_touch_moves_without_requoting(monkeypatch):
    _arm(monkeypatch)
    # touch walks 0.40 -> 0.45; the rest at 0.40 stays valid the whole time
    f = Fake([("0.40", "0.60"), ("0.42", "0.60"), ("0.44", "0.60"),
              ("0.45", "0.60")])
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state(*f.books[0]), max_book_age_s=1e9)
    assert len(f.placed) == 1, "the canary requoted a still-valid order"
    assert out["holds"] >= 1
    assert out["requotes"] == 0


# ------------------------------------- rules 3/4: complement worked at once

def test_a_fill_switches_the_objective_to_reducing_residual(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state(*f.books[0]), max_book_age_s=1e9)
    bodies = [p["body"] for p in f.placed]
    assert bodies[0]["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert any(b["outcomeSide"] == "OUTCOME_SIDE_NO" for b in bodies), \
        "no complement was worked after the opening fill"
    assert out["status"] in ("ONE_SIDED_HOLD", "MATCHED")


def test_the_complement_price_locks_the_required_edge(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state(*f.books[0]), max_book_age_s=1e9)
    no = [p["body"] for p in f.placed
          if p["body"]["outcomeSide"] == "OUTCOME_SIDE_NO"][0]
    # the wire price is the LONG-side price, so the NO price is 1 - wire
    no_px = X.ONE - D(no["price"]["value"])
    assert D("0.40") + no_px <= X.ONE - X.MIN_EDGE


def test_a_matched_pair_ends_the_campaign_and_reports_pair_cost(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state(*f.books[0]), max_book_age_s=1e9)
    assert out["status"] == "MATCHED"
    assert out["matched_contracts"] == "1"
    assert out["residual"] == "0"
    assert D(out["pair_cost"]) == D("0.99")


# ---------------------------------------------- rule 5: partial remainder

def test_a_partial_complement_fill_reduces_only_the_remainder():
    inv = C.Inventory()
    inv.add("yes", D(3), D("0.40"))
    inv.add("no", D(1), D("0.58"))
    assert inv.residual() == D(2)
    assert inv.matched() == D(1)


# -------------------------------------------- rule 6: reactive completion

def test_an_acceptable_fee_adjusted_ask_completes_immediately(monkeypatch):
    """A real crossing order must be SUBMITTED, with the right side/price/qty."""
    _arm(monkeypatch)
    slug = _arm(monkeypatch)
    # WS bid 0.45 makes the NO ask 1-0.45 = 0.55; all-in with the taker fee
    # that still locks >= 1c after a YES fill at 0.40, so it must be taken.
    st = ws_state("0.45", "0.60", slug=slug)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.55", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    assert out["takes"] == 1, "a fee-safe contemporaneous completion was missed"
    takers = [p for p in f.placed
              if p["body"]["participateDontInitiate"] is False]
    assert len(takers) == 1, "TAKE incremented a counter but submitted nothing"
    body = takers[0]["body"]
    assert body["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert body["action"] == "ORDER_ACTION_BUY"
    assert body["quantity"] == 1
    assert X.ONE - D(body["price"]["value"]) == D("0.55")


def test_a_taker_fill_updates_inventory_exactly_once(monkeypatch):
    slug = _arm(monkeypatch)
    st = ws_state("0.45", "0.60", slug=slug)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.55", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    assert out["matched_contracts"] == "1"
    assert out["residual"] == "0"
    assert D(out["pair_cost"]) == D("0.95")


def test_a_rejected_taker_does_not_create_matched_inventory(monkeypatch):
    """An unfilled crossing order must leave inventory untouched."""
    slug = _arm(monkeypatch)
    st = ws_state("0.45", "0.60", slug=slug)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = {"state": "ORDER_STATE_REJECTED", "cumQuantity": "0"}
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    assert out["takes"] == 1
    assert out["matched_contracts"] == "0"
    assert out["residual"] == "1"
    assert out["pair_cost"] is None


def test_a_maker_fill_during_the_cancel_race_aborts_the_take(monkeypatch):
    """Cancelled maker + TAKE must not both fill. The race is resolved first."""
    slug = _arm(monkeypatch)
    # bid 0.20 first (NO ask 0.80 -> take unattractive) so the complement
    # RESTS; then bid 0.45 (NO ask 0.55 -> attractive) so the take branch
    # fires while that complement is still working.
    # NOTE: wait_ready() polls snapshot() too, so the unattractive prefix
    # must outlast those polls as well as the first loop iterations.
    st = StepState(slug, [("0.20", "0.60")] * 12 + [("0.45", "0.60")] * 30)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    # the resting complement O2 fills during the pre-take cancel
    f.fills["O2"] = _filled("0.58", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    takers = [p for p in f.placed
              if p["body"]["participateDontInitiate"] is False]
    assert out["matched_contracts"] == "1"
    assert D(out["residual"]) == 0
    assert len(takers) == 0 or out["takes"] == 0, \
        "crossed after the maker had already completed the pair"


def test_an_unacceptable_ask_is_not_taken(monkeypatch):
    slug = _arm(monkeypatch)
    # WS bid 0.20 -> NO ask 0.80; pairing at 0.40+0.80 = 1.20 is past $1.
    # REST stays in-band: preflight's band check is a legitimate REST role.
    st = ws_state("0.20", "0.60", slug=slug)
    f = Fake([("0.40", "0.60")] * 6)
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    assert out["takes"] == 0
    assert not [p for p in f.placed
                if p["body"]["participateDontInitiate"] is False]


# ------------------------------------------- DEFECT 1: the decision feed

def test_decisions_consume_injected_ws_state_not_the_rest_bbo(monkeypatch):
    """The WS book says 0.40; the REST fake says 0.70. The order must be 0.40."""
    slug = _arm(monkeypatch)
    st = ws_state("0.40", "0.60", slug=slug)
    f = Fake([("0.70", "0.75")] * 6)          # REST disagrees on purpose
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    assert f.placed, out["status"]
    assert D(f.placed[0]["body"]["price"]["value"]) == D("0.40"), \
        "the opening price came from the REST BBO, not the WS book"


def test_changing_the_ws_book_changes_the_decision_without_rest_polling(
        monkeypatch):
    """Same REST fake, two different WS books, two different decisions."""
    slug = _arm(monkeypatch)
    f1, f2 = Fake([("0.55", "0.60")] * 6), Fake([("0.55", "0.60")] * 6)
    C.run_canary(t=f1, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.30", "0.60", slug=slug), max_book_age_s=1e9)
    C.run_canary(t=f2, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.50", "0.60", slug=slug), max_book_age_s=1e9)
    assert D(f1.placed[0]["body"]["price"]["value"]) == D("0.30")
    assert D(f2.placed[0]["body"]["price"]["value"]) == D("0.50")
    # the market-state read is the one-off tick lookup, never a decision poll
    assert f1.market_reads <= 1


def test_a_stale_ws_book_refuses_and_never_falls_back_to_rest(monkeypatch):
    slug = _arm(monkeypatch)
    st = ws_state("0.40", "0.60", slug=slug, now=1.0)      # ancient
    f = Fake([("0.40", "0.60")] * 6)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=3.0)
    assert out["status"] == "WS_STALE_REFUSED"
    assert not f.placed, "placed an order from a stale feed"
    assert f.sweeps >= 1


def test_a_feed_that_goes_stale_mid_campaign_stops_the_controller(monkeypatch):
    slug = _arm(monkeypatch)
    st = WS.LiveState()
    st.apply({"marketData": {"marketSlug": slug,
                             "bids": [{"px": {"value": "0.40"}, "qty": "100"}],
                             "offers": [{"px": {"value": "0.60"},
                                         "qty": "100"}]}}, now=time.time())
    f = Fake([("0.40", "0.60")] * 6)
    # freeze the book, then let real time pass beyond the age limit
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=0.0001)
    assert out["status"] == "WS_STALE_REFUSED"


def test_every_decision_row_carries_the_ws_evidence(monkeypatch, tmp_path):
    slug = _arm(monkeypatch)
    st = ws_state("0.40", "0.60", slug=slug)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=st, max_book_age_s=1e9)
    import json
    rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").open()]
    rests = [r for r in rows if r["kind"] == "resting"]
    assert rests, "nothing was recorded"
    for r in rests:
        for field in ("ws_recv_t", "book_age_s", "bid", "ask", "bid_qty",
                      "ask_qty", "depth", "last_trade_px", "last_trade_qty",
                      "last_trade_venue_t", "residual", "feed"):
            assert field in r, f"decision row is missing {field}"
        assert r["feed"] == "AUTHENTICATED_WS"


def test_taker_completion_rule_accounts_for_the_current_fee_schedule():
    assert X.taker_completion_ok(D("0.40"), D("0.55")) is True
    assert X.taker_completion_ok(D("0.40"), D("0.60")) is False


# ------------------------------------------------ rule 8: sweep every exit

@pytest.mark.parametrize("scenario", ["matched", "no_fill", "halt", "stale"])
def test_every_exit_path_sweeps(monkeypatch, scenario):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    if scenario == "matched":
        f.fills["O1"] = _filled("0.40")
        f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    elif scenario == "halt":
        def boom(*a, **k):
            raise X.Halt("INJECTED")
        monkeypatch.setattr(X, "preflight", boom)
    elif scenario == "stale":
        def cross(*a, **k):
            raise X.StaleRefuse("INJECTED")
        monkeypatch.setattr(X, "place_limit_buy", cross)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state(*f.books[0]), max_book_age_s=1e9)
    assert f.sweeps >= 1, f"no sweep on exit path {scenario} ({out['status']})"


def test_a_halt_is_reported_not_swallowed(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(X, "preflight",
                        lambda *a, **k: (_ for _ in ()).throw(X.Halt("BOOM")))
    out = C.run_canary(t=Fake([("0.40", "0.60")]), clock=_clock(),
                       sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60"), max_book_age_s=1e9)
    assert out["status"] == "HALT" and "BOOM" in out["reason"]


# ----------------------------------------------------------- accounting

def test_realized_is_never_asserted_by_this_process(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state(*f.books[0]), max_book_age_s=1e9)
    assert out["realized_pnl"] is None
    assert "PENDING" in out["settlement"]


def test_rebates_are_reported_separately_and_never_net_against_cost(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state(*f.books[0]), max_book_age_s=1e9)
    assert D(out["pair_cost"]) == D("0.99")      # gross of any rebate
    assert D(out["rebates_separate"]) > 0


def test_every_decision_reaches_the_durable_ledger(monkeypatch, tmp_path):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state(*f.books[0]), max_book_age_s=1e9)
    import json
    kinds = [json.loads(l)["kind"]
             for l in (tmp_path / "ledger.jsonl").open()]
    for required in ("canary_start", "submitted", "resting", "filled",
                     "canary_end", "sweep"):
        assert required in kinds, f"{required} was never recorded"


def test_the_daily_loss_stop_blocks_the_run(monkeypatch, tmp_path):
    _arm(monkeypatch)
    X.ledger_append({"kind": "realized", "realized_usd": "-1.50"})
    out = C.run_canary(t=Fake([("0.40", "0.60")]), clock=_clock(),
                       sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60"), max_book_age_s=1e9)
    assert out["status"] == "DAILY_LOSS_STOP"


# ============ BLOCKERS from independent review of b26f3db7 ============
# 1. an ACK'd-but-ineffective cancel left a live maker the controller had
#    forgotten, and the next iteration placed a SECOND order: two live YES
#    orders, $1.59 committed against an authorized $0.80 cap, residual 2
#    against a cap of 1, reported as residual=0.
# 2. the same mechanism bypassed the pre-take double-fill guard, because by
#    the time the take fired `resting` was already None.
# 3. no test ever executed that guard.

class StubbornCancel(Fake):
    """A venue that ACKs a cancel and leaves the order WORKING.

    This is the eventual-consistency case the executor itself documents, and
    it is the exact shape that produced two live orders.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.stubborn = True

    def request(self, method, path, body=None, **kw):
        if path.endswith("/cancel") and path.startswith("/v1/order/"):
            self.cancelled.append(path.split("/")[3])
            return {}                      # ACK, but nothing changes
        return super().request(method, path, body, **kw)


def test_an_ackd_but_ineffective_cancel_never_places_a_second_order(monkeypatch):
    slug = _arm(monkeypatch)
    # touch walks away so the resting OPEN becomes invalid and a requote is
    # attempted: 0.80 -> 0.79 with the ask collapsing onto our price
    st = StepState(slug, [("0.80", "0.95")] * 12 + [("0.79", "0.795")] * 30)
    f = StubbornCancel([("0.80", "0.95")] * 20)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    assert out["status"] == "CANCEL_UNCONFIRMED", out["status"]
    assert len(f.placed) == 1, (
        "placed a second order while the first may still be live: %s"
        % [(p["id"], p["body"]["price"]["value"]) for p in f.placed])
    assert f.sweeps >= 1


def test_a_stubborn_cancel_blocks_the_take_instead_of_crossing(monkeypatch):
    """Blocker 2: never cross while any placed order is unresolved."""
    slug = _arm(monkeypatch)
    st = StepState(slug, [("0.20", "0.60")] * 12 + [("0.45", "0.60")] * 30)
    f = StubbornCancel([("0.40", "0.60")] * 20)
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    takers = [p for p in f.placed
              if p["body"]["participateDontInitiate"] is False]
    assert not takers, "crossed while a maker may still be live on the venue"
    assert out["status"] == "CANCEL_UNCONFIRMED"


class FillDuringCancel(Fake):
    """An order that reads OPEN until its cancel is requested, then FILLED.

    This IS the cancel race, stated exactly: the fill becomes visible only
    after the cancel goes out. A static `fills` dict cannot express it -- the
    ordinary poll at the top of the loop would see the fill first and the
    pre-take guard would never be entered, which is why the previous version
    of this test was vacuous.
    """

    def __init__(self, books, oid, px, side, **kw):
        super().__init__(books, **kw)
        self.target, self.px, self.side = oid, px, side
        self.cancel_seen = False

    def request(self, method, path, body=None, **kw):
        if path.endswith("/cancel") and path.startswith("/v1/order/"):
            if path.split("/")[3] == self.target:
                self.cancel_seen = True
            self.cancelled.append(path.split("/")[3])
            return {}
        if (path.startswith("/v1/order/") and not path.endswith("/cancel")
                and path.split("/")[3] == self.target):
            if self.cancel_seen:
                return _filled(self.px, side=self.side)
            return {"id": self.target, "state": "ORDER_STATE_OPEN"}
        return super().request(method, path, body, **kw)


def test_the_take_and_a_resting_complement_are_mutually_exclusive_at_q1():
    """Why the PRE-TAKE guard branch is unreachable while a complement rests.

    The reviewer asked for a test that enters the pre-take guard. It cannot be
    entered from the loop in this design, and the reason is arithmetic rather
    than an accident, so it is pinned here instead of faked:

    with `first_cost` c the complement rests at the fee-safe bound
    b = floor(1 - c - 0.01), and the crossing price for the same side is
    (1 - bid). The take requires 1 - c - (1 - bid) - fee >= 0.01, i.e.
    bid >= c + 0.01 + fee; our resting b crosses the live quote as soon as
    b >= 1 - bid, i.e. bid >= 1 - b = c + 0.01. The take window is therefore a
    STRICT SUBSET of the region where our own complement is already invalid,
    so `complement_still_valid` is False first and the hold path cancels and
    resolves it before any take is considered.

    The pre-take loop stays as defence in depth (tested directly below), and
    the protection that actually fires on this path is tested by
    `test_a_race_fill_during_the_requote_cancel_is_booked_and_never_crossed`.
    """
    for c in (D("0.30"), D("0.40"), D("0.55")):
        bound = X.complement_bound(c, tick=D("0.01"))
        assert bound is not None
        for bid_cents in range(20, 81):
            bid = D(bid_cents) / D(100)
            take_ok = X.taker_completion_ok(c, X.ONE - bid)
            ours_crosses = bound >= X.ONE - bid
            if take_ok:
                assert ours_crosses, (
                    f"c={c} bid={bid}: take is available while our complement "
                    f"at {bound} is still valid -- the pre-take guard IS "
                    f"reachable and needs a loop-level test")


def test_a_race_fill_during_the_requote_cancel_is_booked_and_never_crossed(
        monkeypatch, tmp_path):
    """The reachable race: the fill appears only once the cancel is sent.

    This is the path that actually protects against a double fill here, and a
    static `fills` dict cannot express it -- the ordinary poll would see the
    fill first. `FillDuringCancel` makes the fill visible only after the
    cancel goes out.
    """
    import json
    slug = _arm(monkeypatch)
    st = StepState(slug, [("0.20", "0.60")] * 12 + [("0.45", "0.60")] * 40)
    f = FillDuringCancel([("0.40", "0.60")] * 20, "O2", "0.58",
                         "OUTCOME_SIDE_NO")
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=st, max_book_age_s=1e9)
    rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").open()]
    assert any(r["kind"] == "filled" and r.get("stage") == "complement"
               for r in rows), \
        "the race fill was not booked: %s" % [
            (r["kind"], r.get("stage")) for r in rows]
    assert not [p for p in f.placed
                if p["body"]["participateDontInitiate"] is False], \
        "crossed after the maker had already completed the pair"
    assert out["matched_contracts"] == "1"
    assert out["residual"] == "0"
    # exactly two orders, both makers, one per leg
    assert len(f.placed) == 2


def test_the_pre_take_loop_resolves_every_unresolved_order(monkeypatch):
    """Defence in depth, tested directly since the loop cannot reach it.

    A forgotten-but-live order must be cancelled AND positively resolved, and
    an unconfirmable one must stop the campaign rather than be crossed over.
    """
    slug = _arm(monkeypatch)
    ok = Fake([("0.40", "0.60")] * 4)
    ok.fills["OX"] = {"id": "OX", "state": "ORDER_STATE_CANCELLED",
                      "cumQuantity": "0"}
    assert C.cancel_and_resolve(ok, "OX", slug,
                                sleep=lambda *_: None)["terminal"] is True
    stubborn = StubbornCancel([("0.40", "0.60")] * 4)
    stubborn.fills["OY"] = None
    with pytest.raises(C.CancelUnconfirmed):
        C.cancel_and_resolve(stubborn, "OY", slug, sleep=lambda *_: None)


def test_the_worst_case_loss_cap_is_measured_not_assumed():
    inv = C.Inventory()
    assert C.worst_case_loss(inv, None) == D(0)
    # one unpaired long at 0.40 plus an opening order at 0.45 = 0.85 > cap
    inv.add("yes", D(1), D("0.40"))
    assert C.worst_case_loss(inv, None) == D("0.40")
    projected = C.worst_case_loss(inv, {"stage": "open", "px": D("0.45"),
                                        "qty": D(1)})
    assert projected == D("0.85")
    assert projected > C.CANARY_MAX_LOSS
    # a complement REDUCES residual, so it adds no worst-case loss
    assert C.worst_case_loss(inv, {"stage": "complement", "px": D("0.59"),
                                   "qty": D(1)}) == D("0.40")


def test_a_matched_pair_carries_no_worst_case_loss():
    inv = C.Inventory()
    inv.add("yes", D(1), D("0.40"))
    inv.add("no", D(1), D("0.59"))
    assert C.worst_case_loss(inv, None) == D(0)


def test_cancel_and_resolve_requires_proof(monkeypatch):
    slug = _arm(monkeypatch)
    f = StubbornCancel([("0.40", "0.60")] * 4)
    f.fills["O9"] = None
    with pytest.raises(C.CancelUnconfirmed, match="CANCEL_UNCONFIRMED"):
        C.cancel_and_resolve(f, "O9", slug, sleep=lambda *_: None)


def test_cancel_and_resolve_accepts_a_terminal_cancel(monkeypatch):
    slug = _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 4)
    f.fills["O9"] = {"id": "O9", "state": "ORDER_STATE_CANCELLED",
                     "cumQuantity": "0"}
    res = C.cancel_and_resolve(f, "O9", slug, sleep=lambda *_: None)
    assert res["terminal"] is True


def test_cancel_and_resolve_books_a_race_fill(monkeypatch):
    """The fill is booked into inventory by reconcile, not returned raw."""
    slug = _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 4)
    f.fills["O9"] = _filled("0.58", side="OUTCOME_SIDE_NO")
    inv = C.Inventory()
    info = {"stage": "complement", "inv_side": "no", "qty": D(1)}
    res = C.cancel_and_resolve(f, "O9", slug, info, inv,
                               sleep=lambda *_: None)
    assert res["terminal"] is True
    assert inv.qty["no"] == D(1)
    assert inv.basis("no") == D("0.58")
    assert info["booked_qty"] == D(1)


def test_the_venue_grid_must_be_known_before_any_order_exists(monkeypatch):
    """Reviewer NON-BLOCKER 12, promoted: a wrong grid strands the first leg.

    GET /v1/markets/{slug} answers with an error object, so the old _tick
    returned None and complement_bound fell back to TICK=0.005 -- off-grid on
    this 0.01 market, so the complement would be refused at preview after the
    opening leg had already filled. Now an unknown grid refuses up front.
    """
    slug = _arm(monkeypatch)
    monkeypatch.setattr(C, "_tick", lambda *a, **k: None)
    f = Fake([("0.40", "0.60")] * 6)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["status"] == "TICK_UNAVAILABLE"
    assert not f.placed, "placed an order without knowing the price grid"
    assert f.sweeps >= 1


def test_the_grid_comes_from_the_canonical_row_route():
    """The route that actually serves it, with the slug matched on the row."""
    from src import pmus_market_row as MR
    assert MR.MARKET_ROW_PATH == "/v1/market/slug/%s"
    calls = []

    def fake_get(path):
        calls.append(path)
        return {"market": {"slug": "S", MR.TICK_FIELD: "0.01"}}

    row = MR.market_row("S", get=fake_get)
    assert row[MR.TICK_FIELD] == "0.01"
    assert calls == ["/v1/market/slug/S"]


# ---------------- the campaign must end before the window closes

def test_the_window_close_is_parsed_from_the_slug():
    import datetime as dt
    end = C.window_end_epoch("cpc-btc-updown-15m-2026-09-30-1800z")
    assert end == dt.datetime(2026, 9, 30, 18, 15,
                              tzinfo=dt.timezone.utc).timestamp()
    assert C.window_end_epoch("not-a-window-slug") is None


def test_the_deadline_is_capped_by_the_window_close_with_a_sweep_buffer():
    slug = "cpc-btc-updown-15m-2026-09-30-1800z"
    end = C.window_end_epoch(slug)
    # 10 minutes of runway left, 600s requested -> capped by the buffer
    now = end - 600
    got = C.effective_deadline_s(slug, 600, now=now, buffer_s=90)
    assert got == 510
    # plenty of runway -> the request stands
    assert C.effective_deadline_s(slug, 300, now=end - 900,
                                  buffer_s=90) == 300


def test_no_usable_runway_refuses_the_campaign():
    slug = "cpc-btc-updown-15m-2026-09-30-1800z"
    end = C.window_end_epoch(slug)
    assert C.effective_deadline_s(slug, 600, now=end - 60, buffer_s=90) is None
    assert C.effective_deadline_s(slug, 600, now=end + 10, buffer_s=90) is None


def test_a_campaign_with_no_runway_places_nothing(monkeypatch):
    slug = "cpc-btc-updown-15m-2026-09-30-1800z"
    _arm(monkeypatch, slug)
    monkeypatch.setattr(C, "effective_deadline_s", lambda *a, **k: None)
    f = Fake([("0.40", "0.60")] * 6)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["status"] == "NO_RUNWAY"
    assert not f.placed
    assert f.sweeps >= 1


def test_an_unparseable_slug_refuses_rather_than_guessing_a_close(monkeypatch):
    _arm(monkeypatch, "some-other-market")
    f = Fake([("0.40", "0.60")] * 6)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug="some-other-market"),
                       max_book_age_s=1e9)
    assert out["status"] in ("NO_RUNWAY", "TICK_UNAVAILABLE")
    assert not f.placed


# ========== SERIALIZER DRIFT: the live 400 at 2026-09-30T18:09:16Z ==========
# The canary hand-built order bodies that omitted price.currency and POSTed
# the bare body to /v1/order/preview. src/l5_order_body is the body that has
# actually traded; pmus_micro_executor and pmus_q1_executor_v4 both send
# preview as {"request": body}.

def _bodies(fake):
    """(preview payloads, submit payloads) seen by a fake venue."""
    return ([c["body"] for c in fake.calls if c["path"] == "/v1/order/preview"],
            [c["body"] for c in fake.calls if c["path"] == "/v1/orders"])


class RecordingFake(Fake):
    """Records every call path+body so wire shape can be asserted."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = []

    def request(self, method, path, body=None, **kw):
        self.calls.append({"method": method, "path": path, "body": body})
        return super().request(method, path, body, **kw)


def test_maker_preview_body_is_the_certified_envelope(monkeypatch):
    from src import l5_order_body as OB
    slug = _arm(monkeypatch)
    f = RecordingFake([("0.40", "0.60")] * 6)
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug), max_book_age_s=1e9)
    previews, _ = _bodies(f)
    assert previews, "no preview was sent"
    expected = OB.preview_envelope(OB.build(
        slug=slug, side="OUTCOME_SIDE_YES", price=D("0.40"), qty=1,
        post_only=True, tif=OB.TIF_GTC))
    assert previews[0] == expected


def test_maker_submit_body_is_the_same_body_unwrapped(monkeypatch):
    from src import l5_order_body as OB
    slug = _arm(monkeypatch)
    f = RecordingFake([("0.40", "0.60")] * 6)
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug), max_book_age_s=1e9)
    previews, submits = _bodies(f)
    assert submits, "nothing was submitted"
    assert submits[0] == previews[0]["request"]
    assert "request" not in submits[0]


def test_taker_uses_the_same_certified_serializer(monkeypatch):
    from src import l5_order_body as OB
    slug = _arm(monkeypatch)
    st = ws_state("0.45", "0.60", slug=slug)
    f = RecordingFake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.55", side="OUTCOME_SIDE_NO")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None, state=st,
                 max_book_age_s=1e9)
    previews, submits = _bodies(f)
    takers = [b for b in submits
              if b["participateDontInitiate"] is False]
    assert len(takers) == 1
    t_body = takers[0]
    assert t_body == OB.build(slug=slug, side="OUTCOME_SIDE_NO",
                              price=D("0.55"), qty=1, post_only=False,
                              tif=OB.TIF_IOC)
    assert t_body["tif"] == OB.TIF_IOC
    # its preview carried the same envelope
    assert {"request": t_body} in previews


def test_the_no_inversion_happens_exactly_once(monkeypatch):
    """A caller that pre-inverts inverts twice and sends a wildly wrong price."""
    slug = _arm(monkeypatch)
    f = RecordingFake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug), max_book_age_s=1e9)
    _, submits = _bodies(f)
    no_bodies = [b for b in submits
                 if b["outcomeSide"] == "OUTCOME_SIDE_NO"]
    assert no_bodies, "no complement was sent"
    # complement bound off a 0.40 basis is 0.59 in NO terms -> wire 0.41
    assert D(no_bodies[0]["price"]["value"]) == D("0.41")


def test_every_price_carries_the_currency_field(monkeypatch):
    slug = _arm(monkeypatch)
    f = RecordingFake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug), max_book_age_s=1e9)
    previews, submits = _bodies(f)
    for b in submits + [p["request"] for p in previews]:
        assert b["price"]["currency"] == "USD", b["price"]
        assert isinstance(b["quantity"], float), type(b["quantity"])


class Http400Fake(RecordingFake):
    """The venue answering /v1/order/preview with a real HTTPError."""

    def request(self, method, path, body=None, **kw):
        if path == "/v1/order/preview":
            self.calls.append({"method": method, "path": path, "body": body})
            import urllib.error
            exc = urllib.error.HTTPError(
                "https://api.polymarket.us" + path, 400, "Bad Request", {},
                None)
            exc.venue_body = '{"code":3,"message":"invalid price scale"}'
            raise exc
        return super().request(method, path, body, **kw)


def test_a_preview_400_halts_cleanly_and_keeps_the_venue_reason(monkeypatch):
    slug = _arm(monkeypatch)
    f = Http400Fake([("0.40", "0.60")] * 6)
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["status"] == "HALT"
    assert "PREVIEW_REJECTED" in out["reason"]
    assert "invalid price scale" in out["reason"]


def test_a_preview_400_submits_nothing(monkeypatch):
    slug = _arm(monkeypatch)
    f = Http400Fake([("0.40", "0.60")] * 6)
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug),
                 max_book_age_s=1e9)
    _, submits = _bodies(f)
    assert submits == [], "an order was submitted after a rejected preview"
    assert f.sweeps >= 1


def test_the_terminal_summary_reaches_stdout_even_on_rejection(
        monkeypatch, capsys):
    slug = _arm(monkeypatch)
    f = Http400Fake([("0.40", "0.60")] * 6)
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug),
                 max_book_age_s=1e9)
    printed = capsys.readouterr().out
    assert "CANARY preview_rejected" in printed
    assert "CANARY halt" in printed
    assert "CANARY sweep" in printed


def test_the_transport_sanitizes_and_bounds_a_venue_error_body():
    san = C.sanitize_venue_error
    out = san(b'{"code":3,"message":"nope","signature":"deadbeef",'
              b'"authorization":"Bearer x"}')
    assert "deadbeef" not in out and "Bearer" not in out
    assert "<redacted>" in out and "nope" in out
    assert len(san(b"x" * 99999)) <= 2048


# ===== IOC lifecycle: an ACK'd taker that never proves terminality =====
# The taker path previously released the order with X.cancel_exact -- the
# legacy predicate that returns True for ORDER_STATE_OPEN -- and then popped
# `unresolved` unconditionally. IOC normally terminates at once, but the
# safety design does not assume venue lifecycle behaviour.

class StubbornIOC(RecordingFake):
    """Accepts the IOC, ACKs its cancel, and keeps reporting OPEN forever."""

    def __init__(self, books, taker_oid="O2", **kw):
        super().__init__(books, **kw)
        self.taker_oid = taker_oid
        self.stubborn = True

    def request(self, method, path, body=None, **kw):
        if path.endswith("/cancel") and path.startswith("/v1/order/"):
            self.calls.append({"method": method, "path": path, "body": body})
            self.cancelled.append(path.split("/")[3])
            return {}                      # ACK, but nothing changes
        if (path.startswith("/v1/order/") and not path.endswith("/cancel")
                and path.split("/")[3] == self.taker_oid):
            self.calls.append({"method": method, "path": path, "body": body})
            return {"id": self.taker_oid, "state": "ORDER_STATE_OPEN",
                    "cumQuantity": "0"}
        return super().request(method, path, body, **kw)


def test_a_nonterminal_ioc_is_never_forgotten(monkeypatch, capsys):
    slug = _arm(monkeypatch)
    st = ws_state("0.45", "0.60", slug=slug)
    f = StubbornIOC([("0.40", "0.60")] * 12, taker_oid="O2")
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None, state=st,
                       max_book_age_s=1e9)
    # fail-closed, not a silent continue
    assert out["status"] == "CANCEL_UNCONFIRMED", out
    assert "O2" in out["reason"]
    # the IOC was placed exactly once and nothing further was placed after it
    submits = [c["body"] for c in f.calls if c["path"] == "/v1/orders"]
    takers = [b for b in submits if b["participateDontInitiate"] is False]
    assert len(takers) == 1
    assert submits[-1] is takers[-1], "placed again after an unresolved IOC"
    # inventory unchanged by the unproven taker: still one unpaired YES
    assert out["matched_contracts"] == "0"
    assert out["residual"] == "1"
    # and the sweep still ran
    assert f.sweeps >= 1
    printed = capsys.readouterr().out
    assert "CANARY cancel_unconfirmed" in printed
    assert "CANARY sweep" in printed


def test_an_expired_ioc_counts_as_proven_terminal():
    """An IOC that finds no liquidity EXPIRES; that is proof, unlike OPEN."""
    assert C.terminal_no_fill({"state": "ORDER_STATE_EXPIRED",
                               "cumQuantity": "0"}) is True
    assert C.terminal_no_fill({"state": "ORDER_STATE_CANCELLED",
                               "cumQuantity": "0"}) is True
    assert C.terminal_no_fill({"state": "ORDER_STATE_REJECTED",
                               "cumQuantity": "0"}) is True
    assert C.terminal_no_fill({"state": "ORDER_STATE_OPEN",
                               "cumQuantity": "0"}) is False
    # a filled order is not "terminal with no fill"
    assert C.terminal_no_fill({"state": "ORDER_STATE_EXPIRED",
                               "cumQuantity": "1"}) is False


def test_a_filled_ioc_books_exactly_once(monkeypatch):
    slug = _arm(monkeypatch)
    st = ws_state("0.45", "0.60", slug=slug)
    f = RecordingFake([("0.40", "0.60")] * 10)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.55", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None, state=st,
                       max_book_age_s=1e9)
    assert out["matched_contracts"] == "1"
    assert out["residual"] == "0"
    assert D(out["pair_cost"]) == D("0.95")


def test_an_expired_ioc_leaves_inventory_untouched(monkeypatch):
    slug = _arm(monkeypatch)
    st = ws_state("0.45", "0.60", slug=slug)
    f = RecordingFake([("0.40", "0.60")] * 10)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = {"id": "O2", "state": "ORDER_STATE_EXPIRED",
                     "cumQuantity": "0"}
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None, state=st,
                       max_book_age_s=1e9)
    assert out["takes"] == 1
    assert out["matched_contracts"] == "0"
    assert out["residual"] == "1"
    assert f.sweeps >= 1


# ============ EXIT SWEEP: verified, and the report is POST-sweep ============
# The old exit logged `sweep` because the cancel request had been SENT, and
# built the report BEFORE the sweep ran. An order filling during the shutdown
# race therefore produced a report claiming residual 1 / matched 0 while the
# venue had completed the pair.

class OpenAfterSweep(Fake):
    """Cancel-all is ACK'd but /v1/orders/open still lists our order."""

    def __init__(self, books, stuck="O1", **kw):
        super().__init__(books, **kw)
        self.stuck, self.stubborn = stuck, True

    def request(self, method, path, body=None, **kw):
        # only AFTER cancel-all, so preflight still sees a clean account --
        # otherwise this fake fails the run before it starts.
        if (path.startswith("/v1/orders/open")
                and path != "/v1/orders/open/cancel" and self.sweeps > 0):
            return {"orders": [{"id": self.stuck,
                                "marketSlug": FUTURE_SLUG}]}
        return super().request(method, path, body, **kw)


def test_an_order_still_open_after_cancel_all_is_sweep_unverified(monkeypatch):
    slug = _arm(monkeypatch)
    f = OpenAfterSweep([("0.40", "0.60")] * 8, stuck="O1")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["sweep_verified"] is False
    assert out["open_orders_after_sweep"] == 1
    assert "O1" in out["open_order_ids"]
    assert out["status"] in ("SWEEP_UNVERIFIED", "CANCEL_UNCONFIRMED")


class FillDuringShutdown(Fake):
    """The resting complement fills only once the campaign is shutting down.

    `reveal_after` status reads of that order return OPEN; from then on it
    reports FILLED -- i.e. the fill lands in the shutdown reconciliation.
    """

    def __init__(self, books, oid="O2", px="0.58", **kw):
        super().__init__(books, **kw)
        self.oid, self.px = oid, px

    def request(self, method, path, body=None, **kw):
        if (path.startswith("/v1/order/") and not path.endswith("/cancel")
                and path.split("/")[3] == self.oid):
            # OPEN until ITS OWN cancel goes out, then FILLED. That is the
            # shutdown cancel race exactly: during the campaign the order is
            # valid and never cancelled, so the fill can only surface inside
            # finalization.
            if self.oid in self.cancelled:
                return _filled(self.px, side="OUTCOME_SIDE_NO")
            return {"id": self.oid, "state": "ORDER_STATE_OPEN",
                    "cumQuantity": "0"}
        return super().request(method, path, body, **kw)


def test_a_fill_during_shutdown_is_booked_and_changes_the_final_report(
        monkeypatch, capsys):
    slug = _arm(monkeypatch)
    f = FillDuringShutdown([("0.40", "0.60")] * 40, oid="O2", px="0.58")
    f.stubborn = True          # its state is driven by the cancel, not the base
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    # the pair was completed during the shutdown race and the report says so
    assert out["booked_during_shutdown"], out
    assert out["booked_during_shutdown"][0]["order_id"] == "O2"
    assert out["matched_contracts"] == "1"
    assert out["residual"] == "0"
    assert D(out["pair_cost"]) == D("0.98")
    assert out["status"] == "MATCHED"
    assert out["sweep_verified"] is True
    # booked exactly once
    printed = capsys.readouterr().out
    assert printed.count('"stage":"complement_shutdown"') == 1


def test_zero_open_orders_after_the_sweep_is_a_verified_exit(monkeypatch):
    slug = _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["status"] == "MATCHED"
    assert out["sweep_verified"] is True
    assert out["open_orders_after_sweep"] == 0
    assert out["unresolved_orders"] == []
    assert out["sweep_errors"] == []


def test_sweep_verification_failure_never_permits_another_placement(monkeypatch):
    """Finalization is terminal: it runs after the loop, so nothing follows."""
    slug = _arm(monkeypatch)
    f = OpenAfterSweep([("0.40", "0.60")] * 8, stuck="O1")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["sweep_verified"] is False
    # exactly one order was ever placed; the unverified sweep added none
    assert len(f.placed) == 1, [p["id"] for p in f.placed]


def test_the_sweep_row_records_verification_not_mere_submission(
        monkeypatch, tmp_path):
    import json as _json
    slug = _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                 state=ws_state("0.40", "0.60", slug=slug),
                 max_book_age_s=1e9)
    rows = [_json.loads(l) for l in (tmp_path / "ledger.jsonl").open()]
    sweeps = [r for r in rows if r["kind"] == "sweep"]
    assert len(sweeps) == 1
    assert sweeps[0]["sweep_verified"] is True
    assert "open_orders" in sweeps[0]


# ================= PARTIAL FILLS: the last accounting gap =================
# X.authoritative_fill returns None while cumQuantity < QTY unless the state
# says FILLED, and terminal_no_fill required cumulative zero. A q1 maker that
# filled 0.40 and then cancelled matched NEITHER: not a fill, not a no-fill.
# The account owned 0.40 contracts while the report said residual 0.

class Staged(Fake):
    """Serves a scripted sequence of status records for one order id."""

    def __init__(self, books, oid, records, **kw):
        super().__init__(books, **kw)
        self.oid, self.records, self.i, self.stubborn = oid, records, 0, True

    def request(self, method, path, body=None, **kw):
        if (path.startswith("/v1/order/") and not path.endswith("/cancel")
                and path.split("/")[3] == self.oid):
            rec = self.records[min(self.i, len(self.records) - 1)]
            self.i += 1
            return dict(rec, id=self.oid)
        return super().request(method, path, body, **kw)


def _rec(state, cum, avg="0.40"):
    return {"state": state, "cumQuantity": str(cum),
            "avgPx": {"value": str(avg)}}


def test_a_partial_fill_on_a_working_order_books_the_fraction():
    """1. cum 0.40 on an OPEN q1 maker -> 0.40 booked, order stays working."""
    inv = C.Inventory()
    info = {"stage": "open", "inv_side": "yes", "qty": D(1)}
    f = Staged([("0.40", "0.60")], "O1", [_rec("ORDER_STATE_OPEN", "0.40")])
    v = C.reconcile(f, "O1", info, inv, sleep=lambda *_: None)
    assert v["classification"] == "PARTIALLY_FILLED"
    assert v["terminal"] is False
    assert inv.qty["yes"] == D("0.40")
    assert inv.residual() == D("0.40")
    assert info["booked_qty"] == D("0.40")
    assert info["remaining"] == D("0.60")


def test_a_growing_partial_books_only_the_delta():
    """2. cum 0.40 then 0.70 -> only 0.30 more is added."""
    inv = C.Inventory()
    info = {"stage": "open", "inv_side": "yes", "qty": D(1)}
    f = Staged([("0.40", "0.60")], "O1",
               [_rec("ORDER_STATE_OPEN", "0.40"),
                _rec("ORDER_STATE_OPEN", "0.70")])
    C.reconcile(f, "O1", info, inv, sleep=lambda *_: None)
    C.reconcile(f, "O1", info, inv, sleep=lambda *_: None)
    assert inv.qty["yes"] == D("0.70")
    assert info["booked_qty"] == D("0.70")


def test_a_terminal_partial_books_no_duplicate_and_clears():
    """3. CANCELLED at cum 0.70 -> nothing re-booked, and it is terminal."""
    inv = C.Inventory()
    info = {"stage": "open", "inv_side": "yes", "qty": D(1)}
    f = Staged([("0.40", "0.60")], "O1",
               [_rec("ORDER_STATE_OPEN", "0.40"),
                _rec("ORDER_STATE_OPEN", "0.70"),
                _rec("ORDER_STATE_CANCELLED", "0.70")])
    for _ in range(3):
        v = C.reconcile(f, "O1", info, inv, sleep=lambda *_: None)
    assert v["terminal"] is True
    assert inv.qty["yes"] == D("0.70"), "cumulative was counted twice"
    assert info["booked_qty"] == D("0.70")


def test_a_terminal_partial_found_only_in_shutdown_reaches_the_report(
        monkeypatch):
    """4. The post-sweep report must show the real fractional position."""
    slug = _arm(monkeypatch)

    class PartialOnCancel(Fake):
        """OPEN with nothing filled until ITS OWN cancel goes out, then
        CANCELLED carrying 0.40 -- a terminal partial that can only surface
        inside finalization."""

        def __init__(self, books, oid="O1", **kw):
            super().__init__(books, **kw)
            self.oid, self.stubborn = oid, True

        def request(self, method, path, body=None, **kw):
            if (path.startswith("/v1/order/") and not path.endswith("/cancel")
                    and path.split("/")[3] == self.oid):
                if self.oid in self.cancelled:
                    return _rec("ORDER_STATE_CANCELLED", "0.40")
                return _rec("ORDER_STATE_OPEN", "0")
            return super().request(method, path, body, **kw)

    f = PartialOnCancel([("0.40", "0.60")] * 40, oid="O1")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None,
                       state=ws_state("0.40", "0.60", slug=slug),
                       max_book_age_s=1e9)
    assert out["residual"] == "0.40", out
    assert out["matched_contracts"] == "0"
    assert out["booked_during_shutdown"], out
    assert out["booked_during_shutdown"][0]["qty"] == "0.40"
    assert out["sweep_verified"] is True
    assert out["open_orders_after_sweep"] == 0


def test_a_partial_no_fill_uses_own_terms_price():
    """5. The venue serves the LONG price; a NO fill's own price is 1 - avgPx."""
    inv = C.Inventory()
    info = {"stage": "complement", "inv_side": "no", "qty": D(1)}
    # wire avgPx 0.41 == NO at 0.59
    f = Staged([("0.40", "0.60")], "O2",
               [_rec("ORDER_STATE_OPEN", "0.50", avg="0.41")])
    C.reconcile(f, "O2", info, inv, sleep=lambda *_: None)
    assert inv.qty["no"] == D("0.50")
    assert inv.basis("no") == D("0.59")
    assert inv.residual() == D("-0.50")


def test_an_unknown_order_state_halts_rather_than_reading_as_zero():
    inv = C.Inventory()
    info = {"stage": "open", "inv_side": "yes", "qty": D(1)}

    class NoRecord(Fake):
        def request(self, method, path, body=None, **kw):
            if path.startswith("/v1/order/") and not path.endswith("/cancel"):
                return {}
            return super().request(method, path, body, **kw)

    with pytest.raises(X.Halt, match="UNKNOWN_ORDER_STATE"):
        C.reconcile(NoRecord([("0.40", "0.60")]), "O1", info, inv,
                    sleep=lambda *_: None)
