"""The HOLD_OPEN_EXTEND canary: arming, every rule, every exit path.

No test here touches the network. The fake transport asserts on any path it
was not told about, so an unexpected mutation fails the test rather than
passing silently.
"""
from decimal import Decimal as D

import pytest

from src import pmus_q1_canary as C
from src import pmus_q1_executor as X


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


class Fake:
    """Scripted venue. `books` is a list of (bid, ask) served in order."""

    def __init__(self, books, fills=None, tick="0.01"):
        self.books = list(books)
        self.fills = dict(fills or {})     # order_id -> fill dict or None
        self.tick = tick
        self.placed, self.cancelled, self.sweeps = [], [], 0
        self.n = 0
        self._next = 0

    def _book(self):
        i = min(self.n, len(self.books) - 1)
        return self.books[i]

    def request(self, method, path, body=None, **kw):
        if path.endswith("/bbo"):
            self.n += 1
            bid, ask = self._book()
            return {"marketData": {"bestBid": {"value": bid},
                                   "bestAsk": {"value": ask}}}
        if path.startswith("/v1/markets/") and path.count("/") == 3:
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
                       sleep=lambda *_: None)
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
                       sleep=lambda *_: None)
    assert out["status"] == "GATE_NOT_CLEARED"


def test_a_disarmed_run_makes_no_network_call(monkeypatch):
    class Explode:
        def request(self, *a, **k):
            raise AssertionError("network touched while disarmed")
    out = C.run_canary(t=Explode(), clock=_clock(), sleep=lambda *_: None)
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
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    assert len(f.placed) == 1, "the canary requoted a still-valid order"
    assert out["holds"] >= 1
    assert out["requotes"] == 0


# ------------------------------------- rules 3/4: complement worked at once

def test_a_fill_switches_the_objective_to_reducing_residual(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    bodies = [p["body"] for p in f.placed]
    assert bodies[0]["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert any(b["outcomeSide"] == "OUTCOME_SIDE_NO" for b in bodies), \
        "no complement was worked after the opening fill"
    assert out["status"] in ("ONE_SIDED_HOLD", "MATCHED")


def test_the_complement_price_locks_the_required_edge(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
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
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
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
    _arm(monkeypatch)
    # after the YES fill at 0.40 the NO ask is 1-0.45=0.55; all-in with the
    # taker fee that still locks >= 1c, so the canary must take it
    f = Fake([("0.40", "0.60")] * 3 + [("0.45", "0.60")] * 8)
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    assert out["takes"] == 1, "a fee-safe contemporaneous completion was missed"


def test_an_unacceptable_ask_is_not_taken(monkeypatch):
    _arm(monkeypatch)
    # NO ask = 1-0.05 = 0.95; pairing at 0.40+0.95 is far past $1
    f = Fake([("0.40", "0.60")] * 3 + [("0.05", "0.60")] * 6)
    f.fills["O1"] = _filled("0.40")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    assert out["takes"] == 0


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
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    assert f.sweeps >= 1, f"no sweep on exit path {scenario} ({out['status']})"


def test_a_halt_is_reported_not_swallowed(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(X, "preflight",
                        lambda *a, **k: (_ for _ in ()).throw(X.Halt("BOOM")))
    out = C.run_canary(t=Fake([("0.40", "0.60")]), clock=_clock(),
                       sleep=lambda *_: None)
    assert out["status"] == "HALT" and "BOOM" in out["reason"]


# ----------------------------------------------------------- accounting

def test_realized_is_never_asserted_by_this_process(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    assert out["realized_pnl"] is None
    assert "PENDING" in out["settlement"]


def test_rebates_are_reported_separately_and_never_net_against_cost(monkeypatch):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    out = C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
    assert D(out["pair_cost"]) == D("0.99")      # gross of any rebate
    assert D(out["rebates_separate"]) > 0


def test_every_decision_reaches_the_durable_ledger(monkeypatch, tmp_path):
    _arm(monkeypatch)
    f = Fake([("0.40", "0.60")] * 12)
    f.fills["O1"] = _filled("0.40")
    f.fills["O2"] = _filled("0.59", side="OUTCOME_SIDE_NO")
    C.run_canary(t=f, clock=_clock(), sleep=lambda *_: None)
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
                       sleep=lambda *_: None)
    assert out["status"] == "DAILY_LOSS_STOP"
