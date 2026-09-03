from decimal import Decimal as D

import pytest

from src import pmus_q1_executor_v4 as e


class BBOFake:
    def __init__(self, bid="0.710", ask="0.715"):
        self.bid = bid
        self.ask = ask
        self.posts = 0

    def request(self, method, path, body=None, base=e.API):
        if path.endswith("/bbo"):
            return {"marketData": {
                "bestBid": {"value": self.bid},
                "bestAsk": {"value": self.ask},
            }}
        if path == "/v1/order/preview":
            return {"order": {"marketSlug": "m"}}
        if path == "/v1/orders":
            self.posts += 1
            return {"id": "oid"}
        raise AssertionError((method, path, body, base))


def _no_state(monkeypatch, tmp_path):
    monkeypatch.setattr(e, "persist_intent", lambda row: None)
    monkeypatch.setattr(e, "ledger_append", lambda row: None)


def test_outcome_bbo_is_side_aware():
    f = BBOFake("0.710", "0.715")
    yes = e.outcome_bbo(f, "m", "OUTCOME_SIDE_YES")
    no = e.outcome_bbo(f, "m", "OUTCOME_SIDE_NO")
    assert yes["bid"] == D("0.710")
    assert yes["ask"] == D("0.715")
    assert no["bid"] == D("0.285")
    assert no["ask"] == D("0.290")


def test_no_post_only_guard_uses_no_ask_not_yes_ask(monkeypatch, tmp_path):
    _no_state(monkeypatch, tmp_path)
    f = BBOFake("0.720", "0.725")  # NO ask = .280
    with pytest.raises(e.Halt, match="POST_ONLY_STALE_REFUSE"):
        e.place_limit_buy(f, "m", "OUTCOME_SIDE_NO", D("0.280"),
                          "complement", post_only=True)
    assert f.posts == 0


def test_maker_complement_price_stays_below_ask_and_bound():
    assert e._maker_complement_price(D("0.280"), D("0.290")) == D("0.280")
    assert e._maker_complement_price(D("0.280"), D("0.280")) == D("0.275")
    assert e._maker_complement_price(D("0.280"), D("0.275")) == D("0.270")


def test_taker_completion_is_never_flattered_by_rebate():
    first = D("0.710")
    assert not e.taker_completion_ok(first, D("0.280"))
    assert e.taker_completion_ok(first, D("0.260"))


def test_complement_rejection_retries_new_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        e, "outcome_bbo",
        lambda t, slug, side: {"bid": D("0.285"), "ask": D("0.290"),
                               "yes_bid": D("0.710"), "yes_ask": D("0.715")},
    )
    monkeypatch.setattr(e, "taker_completion_ok", lambda first, ask: False)

    def place(t, slug, side, price, stage, post_only=True):
        calls.append((stage, price, post_only))
        if len(calls) == 1:
            raise e.RejectedNoFill("first rejected")
        return "oid2"

    monkeypatch.setattr(e, "place_limit_buy", place)
    monkeypatch.setattr(
        e, "wait_for_fill",
        lambda *a, **k: {"price": D("0.275"), "qty": D("1")},
    )
    result = e.manage_complement(
        object(), "m", D("0.710"), 999.0,
        clock=lambda: 1.0, sleep=lambda _: None,
    )
    assert result["status"] == "MATCHED"
    assert result["attempt"] == 2
    assert len(calls) == 2
    assert all(c[2] is True for c in calls)


def test_first_fill_plus_failed_complement_is_residual_not_no_fill(monkeypatch, tmp_path):
    monkeypatch.setattr(e, "armed", lambda: True)
    monkeypatch.setattr(e, "has_credentials", lambda: True)
    monkeypatch.setattr(e, "gate_asserted", lambda slug: True)
    monkeypatch.setattr(e, "realized_today", lambda: D("0"))
    monkeypatch.setattr(e, "preflight", lambda t, slug: None)
    monkeypatch.setattr(
        e, "outcome_bbo",
        lambda t, slug, side: {"bid": D("0.710"), "ask": D("0.715"),
                               "yes_bid": D("0.710"), "yes_ask": D("0.715")},
    )
    monkeypatch.setattr(e, "place_limit_buy", lambda *a, **k: "first")
    monkeypatch.setattr(
        e, "wait_for_fill",
        lambda *a, **k: {"price": D("0.710"), "qty": D("1")},
    )
    monkeypatch.setattr(
        e, "manage_complement",
        lambda *a, **k: {"status": "ONE_SIDED_HOLD",
                          "reason": "maker_rejected_zero_fill", "attempts": 6},
    )
    monkeypatch.setattr(e, "_position_for_slug", lambda *a, **k: D("1"))
    monkeypatch.setattr(e, "ledger_append", lambda row: None)
    monkeypatch.setattr(e, "cancel_all_deadman", lambda *a, **k: None)
    monkeypatch.setenv(e.MARKET_ENV, "m")
    monkeypatch.setenv(e.RESIDUAL_ACTION_ENV, "hold")

    result = e.run_campaign(object(), clock=lambda: 0.0, sleep=lambda _: None)
    assert result["status"] == "COMPLEMENT_UNFILLED_RESIDUAL_HOLD"
    assert result["first_cost"] == "0.710"
    assert result["residual"] == 1
    assert result["realized_pnl"] is None
