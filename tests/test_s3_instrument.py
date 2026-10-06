"""S3 tests: frozen fingerprint, safety gates, and behaviour on synthetic
and recorded event sequences.

These tests must fail if S3 drifts from frozen spec 28c619df / 8b20a259.
"""
from __future__ import annotations

import json
import sys

import pytest

from analysis.l5.s3 import instrument as I
from analysis.l5.s3 import cohort as C
from analysis.l5.s3.spec import SPEC, S3_FINGERPRINT, fingerprint, assert_frozen

FP = "8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df"


# ----------------------------------------------------- frozen spec
def test_fingerprint_matches_the_frozen_literal():
    assert S3_FINGERPRINT == FP
    assert fingerprint() == FP
    assert_frozen()


def test_guard_is_not_vacuous():
    import copy
    s = copy.deepcopy(SPEC)
    s["reservation"]["max_combined_pair_cost"] = 0.99
    assert fingerprint(s) != FP


@pytest.mark.parametrize("path,expected", [
    (("placement", "target_offset_ticks_below_touch"), 0),
    (("placement", "intended_quantity_shares"), 20.0),
    (("placement", "clip_ceiling_shares"), 80.0),
    (("reservation", "max_combined_pair_cost"), 0.96),
    (("maintenance", "reprice_on_displacement_price_distance"), 0.06),
    (("maintenance", "reprice_on_staleness_s"), 69),
    (("withdrawal", "stop_opening_new_interest_tte_s"), 120),
    (("bounds", "residual_cap_shares"), 80.0),
    (("bounds", "max_notional_per_market_usdc"), 50.0),
    (("cohort", "n_markets_required"), 25),
])
def test_frozen_values_unchanged(path, expected):
    """The brief forbids altering any of these from observed history."""
    node = SPEC
    for k in path:
        node = node[k]
    assert node == expected


def test_s3_is_not_a_predictor():
    assert SPEC["is_predictor"] is False
    assert SPEC["constraints"]["no_next_action_prediction"] is True
    assert SPEC["constraints"]["no_conditional_inventory_forecast"] is True
    assert SPEC["constraints"]["not_optimized_against_settlement_pnl"] is True


def test_par_pair_is_forbidden_and_taker_disallowed():
    assert SPEC["placement"]["atomic_par_pair"].startswith("FORBIDDEN")
    assert SPEC["placement"]["taker_permitted"] is False


def test_rebates_and_rewards_not_counted():
    assert SPEC["accounting"]["rebates_counted"] is False
    assert SPEC["accounting"]["rewards_counted"] is False


# ----------------------------------------------------- safety gates
def test_no_execution_surface_passes_when_clean():
    I.assert_no_execution_surface()


def test_no_execution_surface_aborts_if_signing_module_loaded():
    sys.modules["pmus_q1_executor"] = object()
    try:
        with pytest.raises(SystemExit):
            I.assert_no_execution_surface()
    finally:
        del sys.modules["pmus_q1_executor"]


def test_instrument_imports_no_execution_module():
    src = open("analysis/l5/s3/instrument.py", encoding="utf-8").read()
    for bad in ("import pmus_q1_executor", "import pmus_micro_executor",
                "place_limit_buy", "sign(", "private_key"):
        assert bad not in src, bad


def test_market_reports_zero_submitted_and_signed():
    m = I.S3Market("t", 0.0)
    assert m.metrics()["orders_submitted"] == 0
    assert m.metrics()["orders_signed"] == 0


# ----------------------------------------------------- synthetic sequences
def snap(t, bid, ask=None, depth=100.0):
    bids = [[str(bid), str(depth)]] if bid is not None else []
    offers = [[str(ask), "100"]] if ask is not None else []
    return I.Snap(t, bids, offers)


def test_opens_at_the_touch_not_behind_it():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40, 0.41)})
    o = m.live["up"]
    assert o is not None and o.price == 0.40
    assert o.intended_quantity == 20.0


def test_quantity_is_not_the_80_clip_ceiling():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40)})
    assert m.live["up"].intended_quantity == 20.0 < 80.0


def test_reservation_blocks_open_when_pair_would_exceed_0_96():
    m = I.S3Market("t", 0.0)
    # complement bid 0.60 -> reservation 0.96-0.60 = 0.36; touch 0.40 > 0.36
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.60)})
    assert m.live["up"] is None
    assert any("RESERVATION" in s["reason"] for s in m.stand_downs)


def test_reservation_allows_open_when_pair_is_cheap_enough():
    m = I.S3Market("t", 0.0)
    # complement 0.50 -> reservation 0.46; touch 0.40 <= 0.46
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.50)})
    assert m.live["up"] is not None


def test_reservation_stand_down_is_labelled_separately_from_L5_reasons():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.30), "down": snap(10.0, 0.50)})
    o = m.live["up"]
    # touch runs away past the reservation
    m.step(20.0, {"up": snap(20.0, 0.45), "down": snap(20.0, 0.60)})
    assert o.withdrawal_reason == "RESERVATION_STAND_DOWN"
    assert o.reservation_binding is True
    assert SPEC["withdrawal"]["no_book_triggered_withdrawal"] is True


def test_displacement_reprice_threshold_is_price_not_ticks():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.30), "down": snap(10.0, 0.10)})
    o = m.live["up"]
    # +0.05 is inside the 0.06 price threshold -> no reprice
    m.step(11.0, {"up": snap(11.0, 0.35), "down": snap(11.0, 0.10)})
    assert m.live["up"] is o
    # +0.07 exceeds it -> reprice
    m.step(12.0, {"up": snap(12.0, 0.37), "down": snap(12.0, 0.10)})
    assert m.live["up"] is not o
    assert m.live["up"].predecessor_order_id == o.order_id
    assert o.maintenance_reason == "REPRICE_ON_DISPLACEMENT"


def test_quiet_book_at_the_touch_never_reprices_however_old():
    m = I.S3Market("t", 0.0)
    m.step(1.0, {"up": snap(1.0, 0.40), "down": snap(1.0, 0.10)})
    o = m.live["up"]
    for t in range(2, 400):
        m.step(float(t), {"up": snap(float(t), 0.40),
                          "down": snap(float(t), 0.10)})
    assert m.live["up"] is o, "a quiet book must not trigger maintenance"
    assert o.maintenance_reason is None


def test_staleness_reprice_fires_only_past_69s_behind_the_touch():
    m = I.S3Market("t", 0.0)
    m.step(1.0, {"up": snap(1.0, 0.30), "down": snap(1.0, 0.10)})
    o = m.live["up"]
    # 0.33 is above us but within displacement; at 60s no staleness reprice
    m.step(60.0, {"up": snap(60.0, 0.33), "down": snap(60.0, 0.10)})
    assert m.live["up"] is o
    m.step(75.0, {"up": snap(75.0, 0.33), "down": snap(75.0, 0.10)})
    assert m.live["up"] is not o
    assert o.maintenance_reason == "REPRICE_ON_STALENESS"


def test_no_new_interest_inside_the_120s_tte_stop():
    m = I.S3Market("t", 0.0)
    t = I.WINDOW_S - 100.0           # tte = 100 < 120
    m.step(t, {"up": snap(t, 0.40), "down": snap(t, 0.10)})
    assert m.live["up"] is None
    assert any("TTE_STOP" in s["reason"] for s in m.stand_downs)


def test_touch_alone_is_never_a_fill():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.10)})
    o = m.live["up"]
    # the touch sits exactly at our price, depth unchanged, for a long time
    for t in range(11, 60):
        m.step(float(t), {"up": snap(float(t), 0.40),
                          "down": snap(float(t), 0.10)})
    assert o.fill_evidence_class == "NO_FILL"
    assert SPEC["evidence"]["touch_equals_fill"] is False


def test_depth_leaving_our_level_while_touch_holds_is_UNCERTAIN_not_a_fill():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40, depth=100.0),
                  "down": snap(10.0, 0.10)})
    o = m.live["up"]
    m.step(11.0, {"up": snap(11.0, 0.40, depth=40.0),
                  "down": snap(11.0, 0.10)})
    assert o.fill_evidence_class == "UNCERTAIN"
    assert o.partial_fill_estimate is None
    assert o.partial_fill_defensible is False
    assert m.inv["up"] == 0.0, "an UNCERTAIN entry must not move inventory"


def test_trade_through_is_evidenced_and_flagged_selection_biased():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.10)})
    o = m.live["up"]
    m.step(11.0, {"up": snap(11.0, 0.38), "down": snap(11.0, 0.10)})
    assert o.fill_evidence_class == "EVIDENCED_FILL"
    assert "SELECTION-BIASED" in o.fill_evidence_detail
    assert m.inv["up"] == 20.0


def test_queue_ahead_is_bounded_and_never_invented():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40, depth=55.0)})
    o = m.live["up"]
    assert o.queue_ahead_estimate == 55.0
    assert o.queue_ahead_bounds == [0.0, 55.0]
    assert o.queue_ahead_observable is True


def test_queue_ahead_unobservable_is_declared_not_guessed():
    m = I.S3Market("t", 0.0)
    s = I.Snap(10.0, [], [])             # no bids at all
    m.step(10.0, {"up": s})
    assert m.live["up"] is None          # cannot place without a touch


def test_residual_cap_blocks_new_interest_at_80():
    m = I.S3Market("t", 0.0)
    m.inv["up"] = 80.0
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.10)})
    assert m.live["down"] is None or m.live["up"] is None
    assert any("RESIDUAL_CAP" in s["reason"] for s in m.stand_downs)


def test_crossed_book_is_flagged_on_the_record():
    s = I.Snap(1.0, [["0.76", "10"]], [["0.56", "10"]])
    assert s.crossed is True
    m = I.S3Market("t", 0.0)
    m.step(1.0, {"up": s})
    assert m.live["up"].bbo_at_placement["crossed"] is True


# ----------------------------------------------------- markouts
def test_markouts_cover_every_preregistered_horizon():
    assert I.MARKOUT_HORIZONS == (1, 5, 10, 30, 60)
    series = [snap(float(t), 0.40 - 0.001 * t) for t in range(0, 120)]
    mk = I.markouts(series, 0.0, 0.40)
    for k in I.MARKOUT_HORIZONS:
        assert "t_plus_%d" % k in mk


def test_markout_uses_the_bid_not_the_mid():
    assert I.markouts([snap(0.0, 0.40, 0.90)], 0.0, 0.40)["mark_source"] \
        == "best_bid"


def test_markout_sign_detects_adverse_selection():
    # bid falls after our fill -> negative markout
    series = [snap(0.0, 0.40)] + [snap(float(t), 0.30) for t in range(1, 120)]
    mk = I.markouts(series, 0.0, 0.40)
    assert mk["t_plus_10"]["markout"] == pytest.approx(-0.10)


def test_missing_horizon_is_None_with_a_reason_never_carried_forward():
    series = [snap(0.0, 0.40), snap(2.0, 0.39)]      # nothing past t+2
    mk = I.markouts(series, 0.0, 0.40)
    assert mk["t_plus_1"]["markout"] is not None
    far = mk["t_plus_60"]
    assert far["horizon_fully_elapsed"] is False
    assert far["reason"] == "WINDOW_ENDED_BEFORE_HORIZON"


def test_markout_records_staleness_and_crossed_flag():
    series = [snap(0.0, 0.40), I.Snap(5.0, [["0.42", "1"]], [["0.30", "1"]]),
              snap(80.0, 0.41)]
    mk = I.markouts(series, 0.0, 0.40)
    assert mk["t_plus_5"]["staleness_s"] == 0.0
    assert mk["t_plus_5"]["crossed_at_mark"] is True


def test_markouts_are_labelled_by_ledger_and_not_pooled():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40, depth=100.0),
                  "down": snap(10.0, 0.10)})
    o = m.live["up"]
    m.step(11.0, {"up": snap(11.0, 0.40, depth=10.0),
                  "down": snap(11.0, 0.10)})
    m.finish(12.0, {"up": [snap(10.0, 0.40), snap(11.0, 0.40)]})
    assert o.markouts.get("ledger") == "UNCERTAIN"


def test_metrics_report_markout_unavailability():
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.10)})
    m.step(11.0, {"up": snap(11.0, 0.38), "down": snap(11.0, 0.10)})
    m.finish(12.0, {"up": [snap(10.0, 0.40), snap(11.0, 0.38)]})
    met = m.metrics()
    assert met["M1_evidenced_fills"] == 1
    assert "n_unavailable" in met["M7_markout_t_plus_60"]


# ------------------------------------------- zero-fill classification
def test_zero_fill_with_no_orders_is_instrumentation_failure():
    head = [{"n_orders": 0, "n_snapshots": 0, "M1_evidenced_fills": 0,
             "reservation_stand_downs": 0}] * 5
    c = C.classify_zero_fill(head)
    assert c["classification"] == "INSTRUMENTATION_FAILURE"
    assert "NO_ORDERS_EVER_PLACED_AND_NO_RESERVATION_BLOCK" in c["reasons"]
    assert c["cohort_restarted_or_extended"] is False


def test_zero_fill_with_resting_observable_orders_is_valid_evidence():
    head = [{"n_orders": 8, "n_snapshots": 200, "M1_evidenced_fills": 0,
             "M6_queue_ahead": {"n": 8}, "ledger_counts": {"UNCERTAIN": 3},
             "M6_resting_time_s": {"mean": 42.0},
             "complement_never_observed": False}] * 5
    c = C.classify_zero_fill(head)
    assert c["classification"] == "VALID_ZERO_FILL_EVIDENCE"
    assert c["cohort_restarted_or_extended"] is False


def test_gate_fires_on_first_five_markets_with_no_evidenced_fills():
    done = [{"n_orders": 5, "n_snapshots": 100, "M1_evidenced_fills": 0,
             "M6_queue_ahead": {"n": 5}, "M6_resting_time_s": {"mean": 30.0},
             "complement_never_observed": False,
             "M2_two_sided_evidenced_acquisition": False,
             "ledger_counts": {}} for _ in range(5)]
    g = C.gate_check(done)
    assert g["halt"] is True
    conds = [b["condition"] for b in g["breaches"]]
    assert any("orders_never_evidenced" in c for c in conds)


def test_gate_does_not_fire_before_five_markets():
    done = [{"n_orders": 5, "n_snapshots": 100, "M1_evidenced_fills": 0,
             "M6_queue_ahead": {"n": 5}, "M6_resting_time_s": {"mean": 30.0},
             "M2_two_sided_evidenced_acquisition": False,
             "ledger_counts": {}} for _ in range(4)]
    assert C.gate_check(done)["halt"] is False


def test_gate_treats_submission_as_absolute():
    done = [{"n_orders": 1, "orders_submitted": 1, "M1_evidenced_fills": 1,
             "M6_queue_ahead": {"n": 1}, "M6_resting_time_s": {"mean": 1.0},
             "M2_two_sided_evidenced_acquisition": True,
             "ledger_counts": {}}]
    g = C.gate_check(done)
    assert any(b.get("SEVERITY") == "ABSOLUTE" for b in g["breaches"])


# ------------------------------------------- recorded tape end to end
def test_runs_on_the_recorded_pmus_tape():
    path = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
    by = C.load_tape(path)
    assert by, "recorded tape should contain book rows"
    slug = sorted(by)[0]
    out = C.run_market(slug, by[slug])
    assert out["orders_submitted"] == 0
    assert out["n_snapshots"] > 0
    # the tape carries one outcome only; the instrument must SAY so rather
    # than silently treating a one-sided feed as two-sided
    assert out["complement_never_observed"] is True
    assert out["M2_two_sided_evidenced_acquisition"] is False


def test_non_elapsed_horizons_are_excluded_from_aggregates():
    """Regression: a pre-move mark must never be pooled into a markout mean,
    which would understate adverse selection."""
    m = I.S3Market("t", 0.0)
    m.step(10.0, {"up": snap(10.0, 0.40), "down": snap(10.0, 0.10)})
    m.step(11.0, {"up": snap(11.0, 0.38), "down": snap(11.0, 0.10)})
    # series stops right after the fill: no horizon past t+1 can elapse
    m.finish(12.0, {"up": [snap(10.0, 0.40), snap(11.0, 0.38)]})
    met = m.metrics()
    assert met["M1_evidenced_fills"] == 1
    assert met["M7_markout_t_plus_60"]["n"] == 0
    assert met["M7_markout_t_plus_60"]["n_unavailable"] == 1
    assert met["M7_markout_t_plus_60"]["mean"] is None


# ------------------------------------------- synthetic two-sided cohort
def _tape(tmp_path, kind, markets=1):
    from analysis.l5.s3 import synth
    p = tmp_path / ("%s.jsonl" % kind)
    rows = []
    for i in range(markets):
        rows.extend(synth.GENERATORS[kind](slug="synth-%s-%02d" % (kind, i)))
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(p)


def test_two_sided_cheap_pair_places_and_evidences(tmp_path):
    by = C.load_tape(_tape(tmp_path, "cheap_pair"))
    out = C.run_market(sorted(by)[0], by[sorted(by)[0]])
    assert out["complement_never_observed"] is False
    assert out["n_orders"] > 0
    assert out["M1_evidenced_fills"] >= 1
    assert out["orders_submitted"] == 0


def test_expensive_pair_never_places_an_order(tmp_path):
    by = C.load_tape(_tape(tmp_path, "expensive_pair"))
    out = C.run_market(sorted(by)[0], by[sorted(by)[0]])
    assert out["M1_evidenced_fills"] == 0
    assert out["reservation_stand_downs"] > 0
    assert out["n_orders"] == 0, "the 0.96 reservation must block placement"


def test_no_execution_tape_is_valid_zero_fill_not_a_failure(tmp_path):
    by = C.load_tape(_tape(tmp_path, "no_execution", markets=5))
    done = [C.run_market(s, by[s]) for s in sorted(by)]
    g = C.gate_check(done)
    assert g["halt"] is True
    b = [x for x in g["breaches"] if "orders_never_evidenced" in x["condition"]]
    assert b, "the first-five no-fill gate must fire"
    cls = b[0]["classification"]
    assert cls["classification"] == "VALID_ZERO_FILL_EVIDENCE"
    assert cls["orders_placed"] > 0
    assert cls["cohort_restarted_or_extended"] is False


def test_synthetic_rows_are_labelled_as_verification_only():
    from analysis.l5.s3 import synth
    assert all(r["labels"] == synth.LABEL for r in synth.cheap_pair(n=3))


def test_stale_complement_disables_the_reservation_rather_than_using_it():
    """A carried-forward complement past the freshness bound must not be
    used to justify placing; it is counted as a staleness breach."""
    m = I.S3Market("t", 0.0)
    old = snap(0.0, 0.50)
    m.step(10.0, {"up": snap(10.0, 0.40), "down": old})   # complement 10s old
    assert m.bbo_staleness_breaches == 1
    assert m.metrics()["bbo_freshness_bound_s"] == I.MAX_BBO_AGE_S


def test_staleness_gate_condition_is_evaluated():
    done = [{"n_orders": 5, "n_snapshots": 100, "M1_evidenced_fills": 2,
             "M6_queue_ahead": {"n": 5}, "M6_resting_time_s": {"mean": 9.0},
             "M2_two_sided_evidenced_acquisition": True, "ledger_counts": {},
             "bbo_staleness_breach_share": 0.95} for _ in range(5)]
    g = C.gate_check(done)
    assert any(b["condition"] == "bbo_staleness_breaches_share_above"
               for b in g["breaches"])


def test_zero_orders_due_to_reservation_is_valid_evidence_not_failure(tmp_path):
    """On PMUS pair cost at both touches is 1-spread, so the frozen 0.96
    reservation can block everything. That is an economic finding, not a
    broken instrument, and must not prompt changing the reservation."""
    by = C.load_tape(_tape(tmp_path, "expensive_pair", markets=5))
    done = [C.run_market(s, by[s]) for s in sorted(by)]
    g = C.gate_check(done)
    b = [x for x in g["breaches"] if "orders_never_evidenced" in x["condition"]]
    assert b
    cls = b[0]["classification"]
    assert cls["classification"] == "VALID_ZERO_FILL_EVIDENCE"
    assert cls["zero_orders_because_reservation_blocked"] is True
    assert cls["reservation_stand_downs"] > 0
    assert cls["cohort_restarted_or_extended"] is False


def test_true_instrumentation_failure_still_detected():
    head = [{"n_orders": 0, "n_snapshots": 0, "M1_evidenced_fills": 0,
             "reservation_stand_downs": 0}] * 5
    c = C.classify_zero_fill(head)
    assert c["classification"] == "INSTRUMENTATION_FAILURE"
    assert c["zero_orders_because_reservation_blocked"] is False
