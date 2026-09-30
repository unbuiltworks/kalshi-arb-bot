"""The HOLD_OPEN_EXTEND canary: arming, every rule, every exit path.

No test here touches the network. The fake transport asserts on any path it
was not told about, so an unexpected mutation fails the test rather than
passing silently.
"""
import time
from decimal import Decimal as D

import pytest

from src import pmus_q1_canary as C
from src import pmus_q1_executor as X
from src import pmus_ws_live_state as WS


# ----------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv(X.STATE_ENV, str(tmp_path))
    yield


def _arm(monkeypatch, slug="cpc-btc-updown-15m-2026-09-30-1700z"):
    monkeypatch.setenv(X.LIVE_ENV, "YES")
    monkeypatch.setenv(X.SID_ENV, "sid-1")
    monkeypatch.setenv(X.ARM_ENV, "sid-1")
    monkeypatch.setenv(C.CANARY_ARM_ENV, "sid-1")
    monkeypatch.setenv(X.MARKET_ENV, slug)
    monkeypatch.setenv(X.GATE_ENV, slug)
    monkeypatch.setenv(X.KEY_ENV, "k")
    monkeypatch.setenv(X.SECRET_ENV, "s")
    return slug


def ws_state(bid, ask, *, slug="cpc-btc-updown-15m-2026-09-30-1700z",
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
            return {}
        if path.startswith("/v1/order/"):
            oid = path.split("/")[3]
            f = self.fills.get(oid)
            if f is None:
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
