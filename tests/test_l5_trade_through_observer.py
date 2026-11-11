"""The additive trade-through observer: observations, never fills."""
from decimal import Decimal as D

from src import l5_maker_fill_model as FM
from src import l5_two_sided_shadow as S

SLUG = "cpc-btc-updown-15m-2026-10-15-1730z"
T0 = 1_792_085_400.0


def obs(depth=2, qty=D(5), bid=D("0.45"), ask=D("0.46"),
        bid_sh=D("800"), ask_sh=D("5000"), t=None):
    o = S.TradeThroughObserver(SLUG, depth, qty, window_start=T0)
    o.place(bid, ask, bid_sh, ask_sh, t if t is not None else T0 + 300)
    return o


def pr(px, qty, d, t):
    return {"px": str(px), "qty": str(qty), "dir": d, "t": t}


def test_the_two_prices_are_two_ticks_behind_each_touch():
    o = obs()
    legs = {l.side: l for l in o.legs.values()}
    assert legs[FM.UP].own_px == D("0.43")
    assert legs[FM.DOWN].own_px == D("0.52")
    assert legs[FM.DOWN].yes_px == D("0.48")      # the YES ask it rests at
    assert o.pair_cost == D("0.95")


def test_the_touch_baseline_is_kept_but_does_not_clear_the_fee_floor():
    z = obs(depth=0)
    assert z.pair_cost == D("0.99")
    assert z.settlement(True)["clears_fee_floor"] is False
    assert obs(depth=2).settlement(True)["clears_fee_floor"] is True


def test_a_print_AT_our_price_is_touched_and_never_through():
    o = obs()
    o.consume([pr("0.43", 100, "sell", T0 + 400)], T0 + 500)
    up = {l.side: l for l in o.legs.values()}[FM.UP]
    assert up.state == S.TOUCHED
    assert up.first_through_t is None
    assert up.through_vol == D(0)
    assert o.settlement(True)["both_through"] is False


def test_a_print_strictly_through_marks_through_with_its_timestamp():
    o = obs()
    o.consume([pr("0.42", 50, "sell", T0 + 410)], T0 + 500)
    up = {l.side: l for l in o.legs.values()}[FM.UP]
    assert up.state == S.THROUGH
    assert up.first_through_t == T0 + 410
    assert up.through_vol == D(50)
    assert up.row()["seconds_to_through"] == 110.0


def test_the_wrong_aggressor_direction_never_counts():
    o = obs()
    # a BUY cannot hit our resting UP bid
    o.consume([pr("0.42", 50, "buy", T0 + 410)], T0 + 500)
    assert {l.side: l for l in o.legs.values()}[FM.UP].state == S.NEVER


def test_the_down_leg_is_lifted_by_a_buy_above_its_yes_price():
    o = obs()
    dn = {l.side: l for l in o.legs.values()}[FM.DOWN]
    assert dn.yes_px == D("0.48")
    o.consume([pr("0.49", 30, "buy", T0 + 420)], T0 + 500)
    assert dn.state == S.THROUGH


def test_both_legs_through_gives_positive_pnl_on_both_outcomes():
    o = obs()
    o.consume([pr("0.42", 50, "sell", T0 + 410),
               pr("0.49", 50, "buy", T0 + 430)], T0 + 500)
    s = o.settlement(True)
    assert s["both_through"] is True
    assert s["both_outcomes_positive"] is True
    assert s["pnl_if_up_settles"] == s["pnl_if_dn_settles"]
    assert o.report(True)["seconds_between_legs"] == 20.0
    assert o.report(True)["first_leg_through"] == FM.UP


def test_one_sided_is_SETTLED_not_marked():
    o = obs()
    o.consume([pr("0.42", 50, "sell", T0 + 410)], T0 + 500)
    won = o.settlement(True)          # UP settles, our UP leg wins
    lost = o.settlement(False)
    assert won["one_through"] is True and won["one_sided_side"] == FM.UP
    assert D(won["one_sided_pnl"]) > 0
    assert D(lost["one_sided_pnl"]) < 0
    assert D(lost["one_sided_pnl"]) == -D(won["cost_if_one_filled"])


def test_an_unscored_one_sided_window_reports_no_pnl_rather_than_zero():
    o = obs()
    o.consume([pr("0.42", 50, "sell", T0 + 410)], T0 + 500)
    s = o.settlement(None)
    assert s["one_sided_pnl"] is None
    assert "needs the venue's own binary resolution" in s["one_sided_pnl_note"]


def test_queue_ahead_bands_are_reported_and_neither_is_a_fill():
    o = obs(qty=D(5), bid_sh=D("40"))
    o.consume([pr("0.42", 30, "sell", T0 + 410)], T0 + 500)
    row = {l.side: l for l in o.legs.values()}[FM.UP].row()
    assert row["filled_optimistic"] == "5"     # 30 through, we want 5
    assert row["filled_conservative"] == "0"   # 40 displayed ahead of us
    assert row["queue_ahead_at_placement"] == "40"
    assert "Neither bound is a confirmed fill" in row["labels"]


def test_an_unknown_queue_never_fills_conservatively():
    o = obs(bid_sh=None)
    o.consume([pr("0.42", 999, "sell", T0 + 410)], T0 + 500)
    row = {l.side: l for l in o.legs.values()}[FM.UP].row()
    assert row["filled_conservative"] == "0"
    assert row["queue_ahead_at_placement"] is None


def test_prints_after_the_cancellation_deadline_are_ignored():
    o = obs()
    dl = o.deadline()
    assert dl == T0 + 900 - 180
    o.consume([pr("0.42", 50, "sell", dl + 1)], dl + 2)
    assert {l.side: l for l in o.legs.values()}[FM.UP].state == S.NEVER


def test_prints_before_placement_are_ignored_and_duplicates_once():
    o = obs(t=T0 + 300)
    p = pr("0.42", 50, "sell", T0 + 200)
    o.consume([p], T0 + 400)
    assert {l.side: l for l in o.legs.values()}[FM.UP].state == S.NEVER
    q = pr("0.42", 50, "sell", T0 + 410)
    o.consume([q, q], T0 + 500)
    assert {l.side: l for l in o.legs.values()}[FM.UP].through_vol == D(50)


def test_prices_are_frozen_at_placement_and_never_requoted():
    o = obs()
    before = [l.own_px for l in o.legs.values()]
    assert o.place(D("0.60"), D("0.61"), D(1), D(1), T0 + 400) is None
    assert [l.own_px for l in o.legs.values()] == before


def test_a_one_sided_book_refuses_and_stays_unevaluable():
    o = S.TradeThroughObserver(SLUG, 2, D(5), window_start=T0)
    assert o.place(None, D("0.46"), D(1), D(1), T0 + 300) == \
        "BOOK_NOT_TWO_SIDED"
    assert o.settlement(True)["evaluable"] is False


def test_the_aggregate_preserves_market_rows_and_reports_intervals():
    rs = []
    for i, (u, d) in enumerate([(True, True), (True, False), (True, True)]):
        o = obs()
        ps = [pr("0.42", 50, "sell", T0 + 410)]
        if d:
            ps.append(pr("0.49", 50, "buy", T0 + 430))
        o.consume(ps, T0 + 500)
        r = o.report(True); r["slug"] = "w%d" % i
        rs.append(r)
    agg = S.aggregate(rs)
    cell = agg["depths"]["2"]
    assert cell["n_windows"] == 3
    assert cell["both_through"]["k"] == 2
    assert cell["one_through"]["k"] == 1
    assert len(cell["markets"]) == 3           # auditable, not just a rate
    assert cell["markets"][0]["slug"] == "w0"
    assert cell["spread_regimes"] == {"0.01": 3}
    assert "no threshold, depth or regime is selected" in agg["labels"]


def test_wilson_intervals_separate_small_from_large_samples():
    a = S.wilson_interval(5, 8)
    b = S.wilson_interval(500, 800)
    assert a["p"] == b["p"] == "0.6250"
    assert float(a["hi"]) - float(a["lo"]) > 0.5
    assert float(b["hi"]) - float(b["lo"]) < 0.08   # 0.067 at n=800
    assert S.wilson_interval(0, 0)["p"] is None


def test_the_observer_cannot_submit_or_cancel():
    import inspect
    src = inspect.getsource(S.TradeThroughObserver)
    for bad in ("transport", "submit", "cancel(", "urllib", "requests"):
        assert bad not in src, bad


def test_the_existing_collector_is_untouched():
    """The additive classes must not have displaced the shadow API."""
    for name in ("TwoSidedShadow", "Config", "Leg", "ShadowLedger",
                 "score_report", "pending_report"):
        assert hasattr(S, name), name
