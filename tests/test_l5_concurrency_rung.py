"""OWNER live decision 2026-09-06 (uptime): a rung may hold more than one
market at a time. What must NOT change: one session at a time, one
outstanding order, maker-only, fee-safe 0.98, HOLD-to-settlement, the
per-market cap, the residual cap and the daily stop. What does change:
a sibling position the campaign OWNS no longer blocks the next market."""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ladder
from src import pmus_micro_executor as micro
from src import pmus_spring_l5_session as base


class T:
    """Venue with two open positions and no open orders."""

    def __init__(self, positions=(("mkt-a", "0.02"), ("mkt-b", "-0.04")), orders=()):
        self.positions = list(positions)
        self.orders = list(orders)

    def request(self, method, path, body=None, base=None):
        if path.startswith("/v1/orders/open"):
            return {"orders": list(self.orders)}
        if path.startswith("/v1/portfolio/positions"):
            return {"positions": [{"marketSlug": s, "netPositionDecimal": q}
                                  for s, q in self.positions]}
        raise RuntimeError(f"HTTP 404 {path}")


def test_r2c2_is_r2c_caps_with_concurrency_two():
    r = ladder.rung("R2C2")
    assert r["per_market_cap_usd"] == "0.15" and r["residual_units"] == 6
    assert r["markets_per_campaign"] == 3 and r["concurrency"] == 2
    assert r["max_actions_per_session"] == 6 and r["promote"] is None
    assert ladder.shell_version(r) == (
        "R2C2/cap0.15/residual6/conc2/maker-only/fee-safe0.98/rest30s")
    wc = ladder.worst_case(r)
    # the per-market cap is R2C's; shortening to 3 markets (OWNER live
    # 2026-09-07) HALVES the campaign worst case rather than raising it
    assert wc["per_market_usd"] == ladder.worst_case(ladder.rung("R2C"))["per_market_usd"]
    assert wc["per_campaign_usd"] == "0.45"
    assert D(wc["per_campaign_usd"]) < D(ladder.worst_case(ladder.rung("R2C"))["per_campaign_usd"])
    # concurrent exposure is the ONE figure that rises versus R2C
    assert wc["max_concurrent_open_exposure_usd"] == "0.30"
    assert wc["daily_loss_stop_usd"] == "-1.00"
    # OWNER live 2026-09-07: the sized rung runs concurrency 2 as well
    assert ladder.rung("R3L")["concurrency"] == 2


def test_every_rung_label_states_its_own_concurrency():
    """LIVE 23:32Z: the campaign's rung report carried a hardcoded
    concurrency 1 while running R2C2/conc2. The label, the rung and the
    worst case must all quote the SAME number for every rung."""
    import re
    for r in ladder.ladder():
        label_conc = int(re.search(r"/conc(\d+)/", ladder.shell_version(r)).group(1))
        assert label_conc == r["concurrency"], r["id"]
        cap, conc = D(str(r["per_market_cap_usd"])), r["concurrency"]
        assert r["worst_case"]["max_concurrent_open_exposure_usd"] == str(cap * conc)


def test_owned_siblings_stop_blocking_only_when_the_campaign_declares_them():
    t = T()
    try:
        # the default is the original account-wide lock
        assert base.set_concurrent_owned(()) == frozenset()
        assert micro.account_block_reason(t).startswith("nonzero_positions:")
        # declaring BOTH held markets clears the position block for a sibling
        base.set_concurrent_owned({"mkt-a", "mkt-b"})
        assert micro.account_block_reason(t, allow_slugs=base.concurrent_owned()) is None
        # a market the campaign does NOT own still blocks
        base.set_concurrent_owned({"mkt-a"})
        assert micro.account_block_reason(
            t, allow_slugs=base.concurrent_owned()) == "nonzero_positions:mkt-b=-0.04"
        # an open order ANYWHERE still blocks, owned or not
        assert micro.account_block_reason(
            T(orders=[{"id": "o1"}]), allow_slugs={"mkt-a", "mkt-b"}) == "open_orders:1"
    finally:
        base.set_concurrent_owned(())


def test_resume_preflight_allows_owned_siblings_and_nothing_else():
    t = T()
    try:
        base.set_concurrent_owned(())
        # concurrency 1: a position elsewhere blocks resuming mkt-a
        assert base.resume_block_reason(t, "mkt-a") == \
            "nonzero_positions_elsewhere:mkt-b=-0.04"
        base.set_concurrent_owned({"mkt-a", "mkt-b"})
        assert base.resume_block_reason(t, "mkt-a") is None
        assert base.resume_block_reason(t, "mkt-b") is None
        # a foreign position still blocks the resume
        t2 = T(positions=(("mkt-a", "0.02"), ("foreign", "0.5")))
        assert base.resume_block_reason(t2, "mkt-a") == \
            "nonzero_positions_elsewhere:foreign=0.5"
    finally:
        base.set_concurrent_owned(())


def test_held_markets_lists_every_position_and_keeps_single_market_semantics(monkeypatch):
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    assert cont.held_markets(object()) == []
    monkeypatch.setattr(cont, "held_market", lambda t: "mkt-a")
    assert cont.held_markets(object()) == ["mkt-a"]
    # only the genuine multi-position case reads the authoritative list
    monkeypatch.setattr(cont, "held_market", lambda t: cont.MULTI_HELD)
    assert cont.held_markets(T()) == ["mkt-a", "mkt-b"]


def test_the_caps_that_bound_money_do_not_move_with_concurrency():
    try:
        for rid, cap, residual, stop in (("R2C", "0.15", 6, "-1.00"),
                                         ("R2C2", "0.15", 6, "-1.00")):
            ladder.set_active(rid)
            assert base.session_notional_cap() == D(cap)
            assert base.max_residual_units() == residual
            assert base.daily_loss_stop() == D(stop)
            assert ladder.target_notional() == D("0.01")   # size unchanged
    finally:
        ladder.set_active(None)
