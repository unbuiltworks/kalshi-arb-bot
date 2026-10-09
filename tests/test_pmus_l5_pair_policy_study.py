"""The replay's honesty is the result. These tests pin it.

If any of these fail, the ranked comparison is worthless regardless of what
numbers it prints: a quote that fills because the market merely touched it,
a price chosen after the fact, or a residual scored as free would all make a
losing policy look profitable.
"""
from decimal import Decimal as D

import pytest

from src import pmus_l5_pair_policy_study as S


def row(t, bid, ask, traded, last=None, bid_qty=10.0, ask_qty=10.0):
    return {"_poll_t": float(t),
            "t": "2026-10-09T00:00:%02dZ" % (int(t) % 60),
            "slug": "cpc-btc-updown-15m-2026-10-09-1200z",
            "bid": bid, "ask": ask,
            "shares_traded_cum": traded, "last_trade_px": last,
            "book_levels": {"bids": [{"px": bid, "qty": bid_qty}],
                            "offers": [{"px": ask, "qty": ask_qty}]}}


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def tape(spec):
    """spec: list of (t, bid, ask, traded_cum, last_px)"""
    out = []
    for t, b, a, v, lp in spec:
        r = row(t, b, a, v, lp)
        r["t"] = iso(t)
        out.append(r)
    return out


# ------------------------------------------------- the three guarantees ---
def test_a_touch_without_volume_never_fills():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (20, 0.40, 0.40, 0, None),      # ask came to our bid
                (40, 0.40, 0.60, 0, None)])
    f = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None)
    assert f["filled"] is False and f["why"] == "NEVER_CLEARED_QUEUE"


def test_volume_inside_the_queue_never_fills():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (20, 0.39, 0.41, 5, 0.39)])     # 5 printed as a SELL, 10 ahead
    f = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None)
    assert f["filled"] is False


def test_only_the_excess_over_the_queue_is_ours():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (20, 0.39, 0.41, 10.4, 0.39)])  # 10 ahead, 0.4 excess
    f = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None)
    assert f["filled"] is True and f["qty"] == pytest.approx(0.4)


def test_a_missing_ladder_is_never_an_empty_queue():
    obs = tape([(0, 0.40, 0.60, 0, None), (20, 0.39, 0.41, 999, 0.40)])
    obs[1]["last_trade_px"] = 0.39
    obs[0]["book_levels"] = {"bids": [], "offers": []}
    f = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None)
    assert f["filled"] is False
    assert f["why"] == "QUEUE_UNKNOWN_AT_PLACEMENT"


def test_the_deadline_is_respected():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (200, 0.39, 0.41, 999, 0.39)])
    f = S.resting_fill(obs, 0, True, D("0.40"), D("1"), deadline=120.0)
    assert f["filled"] is False and f["why"] == "DEADLINE_PASSED"


def test_pre_entry_drift_uses_only_earlier_rows():
    obs = tape([(0, 0.50, 0.52, 0, None),
                (70, 0.40, 0.42, 0, None),      # mid fell 0.10
                (90, 0.90, 0.92, 0, None)])     # LATER spike: must not leak
    d = S.pre_entry_drift(obs, 1, True)
    assert d == pytest.approx(-0.10, abs=1e-6)


def test_own_side_frames():
    q = S.own_side(row(0, 0.40, 0.60, 0), False)
    assert q["maker"] == D("1") - D("0.60")
    assert q["taker"] == D("1") - D("0.40")


# ------------------------------------------------------- policy behaviour --
def _two_sided_tape():
    # a book that trades through BOTH touches, so every policy gets a
    # chance and the differences are the policies, not the tape
    return tape([(0, 0.40, 0.60, 0, None),
                 (20, 0.40, 0.60, 11, 0.40),     # sells through 0.40
                 (40, 0.40, 0.60, 22, 0.60),     # buys through 0.60
                 (60, 0.41, 0.59, 33, 0.41),
                 (80, 0.41, 0.59, 44, 0.59),
                 (120, 0.41, 0.59, 55, 0.41)])


def test_immediate_taker_pays_the_fee_and_always_completes():
    r = S.replay("s", _two_sided_tape(), "P1_IMMEDIATE_TAKER")
    assert r["complete"] is True
    assert float(r["fees_usd"]) > 0
    assert r["fill_evidence"][1]["maker"] is False
    # YES at 0.40 + NO taker at 1-0.40 = 0.60 -> pair 1.00 before fees
    assert r["matched_pair_cost"] == pytest.approx(1.0, abs=1e-6)
    assert r["pair_under_one"] is False


def test_delayed_maker_is_cheaper_when_it_completes():
    r = S.replay("s", _two_sided_tape(), "P2_DELAYED_MAKER")
    if r.get("complete"):
        # YES 0.40 + NO maker (1-0.60)=0.40 -> 0.80
        assert r["matched_pair_cost"] < 1.0
        assert float(r["fees_usd"]) == 0.0


def test_both_sides_resting_can_end_one_sided_and_says_so():
    one_sided = tape([(0, 0.40, 0.60, 0, None),
                      (20, 0.40, 0.60, 11, 0.40),
                      (40, 0.40, 0.60, 12, 0.40)])
    r = S.replay("s", one_sided, "P3_BOTH_SIDES_RESTING")
    assert r["complete"] is False and r["why"] == "ONE_SIDED_FILL"
    assert abs(r["residual_shares_signed"]) > 0


def test_an_open_residual_is_scored_to_the_end_of_the_tape():
    one_sided = tape([(0, 0.40, 0.60, 0, None),
                      (20, 0.40, 0.60, 11, 0.40),
                      (300, 0.40, 0.60, 12, 0.40)])
    r = S.replay("s", one_sided, "P2_DELAYED_MAKER")
    assert r["complete"] is False
    # time at risk runs to the tape end, not to the last fill
    assert r["time_at_risk_s"] >= 280


def test_summary_excludes_residual_pnl_and_says_why():
    rows = [S.replay("s", _two_sided_tape(), p) for p in S.POLICIES]
    out = S.summarize(rows, "P1_IMMEDIATE_TAKER")
    assert out["entries"] == 1
    assert "settlement feed" in out["UNRESOLVED_RESIDUAL"]
    assert out["LABEL"] == "REPLAY_NOT_REALIZED"


def test_study_splits_out_of_sample_by_time():
    t1 = _two_sided_tape()
    t2 = [dict(r, _poll_t=r["_poll_t"] + 10000, t=iso(r["_poll_t"] + 10000))
          for r in _two_sided_tape()]
    out = S.study({"early_market": t1, "later_market": t2})
    assert out["splits"]["in_sample"]["markets"] == 1
    assert out["splits"]["out_of_sample"]["markets"] == 1
    assert out["splits"]["in_sample"]["ranking"]


def test_a_print_between_the_touches_is_not_attributed_to_us():
    """An unknown-direction print is not evidence that our resting buy was
    hit: 0.40 between a 0.39 bid and 0.41 ask could be either side."""
    obs = tape([(0, 0.40, 0.60, 0, None),
                (20, 0.39, 0.41, 999, 0.40)])
    f = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None)
    assert f["filled"] is False


# ---------------------------------------------- inferred settlement -------
def test_settlement_is_inferred_only_from_a_decisive_terminal_book():
    up = tape([(0, 0.40, 0.60, 0, None), (60, 0.98, 1.00, 5, 0.99)])
    dn = tape([(0, 0.40, 0.60, 0, None), (60, 0.00, 0.02, 5, 0.01)])
    mid = tape([(0, 0.40, 0.60, 0, None), (60, 0.55, 0.65, 5, 0.60)])
    assert S.infer_settlement(up)["yes_won"] is True
    assert S.infer_settlement(dn)["yes_won"] is False
    u = S.infer_settlement(mid)
    assert u["resolved"] is False and u["why"] == "TERMINAL_STATE_NOT_DECISIVE"
    assert u["LABEL"] == "SETTLEMENT_UNDECIDABLE"
    assert S.infer_settlement(up)["LABEL"] == \
        "SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK"


def test_full_inventory_counts_every_losing_residual():
    # one YES share at 0.30, no complement, market resolves DOWN
    row = {"policy": "P", "slug": "s", "fees_usd": "0",
           "fill_evidence": [{"side": "YES", "px": "0.30", "qty": 1.0}]}
    lost = S.settle_row(row, {"resolved": True, "yes_won": False})
    won = S.settle_row(row, {"resolved": True, "yes_won": True})
    assert lost["residual_pnl_usd"] == pytest.approx(-0.30)
    assert lost["net_usd"] == pytest.approx(-0.30)
    assert won["residual_pnl_usd"] == pytest.approx(0.70)


def test_full_inventory_pairs_pay_one_either_way_minus_fees():
    row = {"policy": "P", "slug": "s", "fees_usd": "0.01",
           "fill_evidence": [{"side": "YES", "px": "0.40", "qty": 1.0},
                             {"side": "NO", "px": "0.55", "qty": 1.0}]}
    for yw in (True, False):
        out = S.settle_row(row, {"resolved": True, "yes_won": yw})
        assert out["matched_pair_pnl_usd"] == pytest.approx(0.05)
        assert out["residual_pnl_usd"] == pytest.approx(0.0)
        assert out["net_usd"] == pytest.approx(0.04)      # fee charged


def test_an_undecidable_market_is_not_scored_as_zero():
    row = {"policy": "P", "slug": "s", "fees_usd": "0",
           "fill_evidence": [{"side": "YES", "px": "0.30", "qty": 1.0}]}
    out = S.settle_row(row, {"resolved": False, "why": "X"})
    assert out["settled"] is False and "net_usd" not in out


def test_bootstrap_interval_reports_n_and_both_tails():
    ci = S._boot_ci([0.1, -0.2, 0.05, 0.0])
    assert ci["n"] == 4 and ci["p05"] <= ci["mean"] <= ci["p95"]
    assert S._boot_ci([]) is None


# ------------------------------------------- placement policies (P7-P10) ---
def test_placement_requotes_and_can_fill_more_than_once():
    """The point of P7-P10 is that fill COUNT is an output, not a cap. Two
    separate clearing events, 200 s apart, must produce two legs."""
    obs = tape([(0, 0.40, 0.60, 0, None),
                (30, 0.39, 0.41, 11.0, 0.39),     # clears the queue once
                (90, 0.40, 0.60, 11.0, None),     # fresh quote, fresh queue
                (120, 0.40, 0.60, 11.0, None),
                (150, 0.39, 0.41, 22.0, 0.39),    # clears it again
                (210, 0.40, 0.60, 22.0, None),
                (240, 0.40, 0.60, 22.0, None)])
    r = S.replay_placement("m", obs, "P7_BEST_BID_RESTING")
    assert r["legs"] >= 2, r
    assert r["requotes"] >= 2


def test_deeper_resting_quotes_behind_the_bid_and_improve_in_front():
    obs = tape([(0, 0.40, 0.60, 0, None)])
    assert S._place_px(obs[0], True, -1) == D("0.39")
    assert S._place_px(obs[0], True, 0) == D("0.40")
    assert S._place_px(obs[0], True, 1) == D("0.41")


def test_a_resting_quote_may_never_cross_its_own_ask():
    obs = tape([(0, 0.40, 0.41, 0, None)])
    assert S._place_px(obs[0], True, 1) is None


def test_complement_is_not_rested_above_what_the_ceiling_allows():
    legs = [{"side_yes": True, "px": D("0.60"), "qty": 1.0}]
    # long YES at 0.60; ceiling 0.96 leaves at most 0.36 for the NO leg
    assert S._budget(legs, False) == S.CEILING - D("0.60")


def test_budget_is_none_with_no_long_side():
    assert S._budget([], False) is None


def test_placement_never_fills_on_a_touch_either():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (30, 0.40, 0.40, 0, None),
                (90, 0.40, 0.60, 0, None),
                (150, 0.40, 0.60, 0, None)])
    r = S.replay_placement("m", obs, "P7_BEST_BID_RESTING")
    assert r.get("skipped") == "NO_FILL" and r["requotes"] >= 1


def test_one_sided_placement_result_is_not_marked_complete():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (30, 0.39, 0.41, 11.0, 0.39),
                (90, 0.40, 0.60, 11.0, None),
                (150, 0.40, 0.60, 11.0, None)])
    r = S.replay_placement("m", obs, "P7_BEST_BID_RESTING")
    assert r["complete"] is False and r["why"] == "ONE_SIDED_ONLY"
    assert r["matched_shares"] == 0


def test_same_side_extension_is_bounded():
    """EXTEND is first-class, but not unbounded: once the imbalance reaches
    MAX_EXTEND the long side is no longer quoted."""
    spec = [(0, 0.40, 0.60, 0.0, None)]
    v = 0.0
    for k in range(1, 12):                 # repeated sell prints at the bid
        v += 11.0
        spec.append((60 * k, 0.39, 0.41, v, 0.39))
        spec.append((60 * k + 30, 0.40, 0.60, v, None))
    spec.append((60 * 12, 0.40, 0.60, v, None))
    r = S.replay_placement("m", tape(spec), "P7_BEST_BID_RESTING")
    assert abs(r["residual_shares_signed"]) <= float(S.MAX_EXTEND), r


def test_passing_trades_in_changes_no_result():
    """The per-quote recomputation was made optional for speed. If the two
    paths ever disagree, every placement number is suspect."""
    from src import pmus_status_report as ST
    obs = tape([(0, 0.40, 0.60, 0, None),
                (20, 0.39, 0.41, 10.4, 0.39),
                (40, 0.39, 0.41, 10.4, 0.39)])
    a = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None)
    b = S.resting_fill(obs, 0, True, D("0.40"), D("1"), None,
                       trades=ST.infer_trades(list(obs)))
    assert a == b
