"""Offline tests for the E1 cohort. NO NETWORK, NO CREDENTIAL, NO ORDER.

Every venue interaction is a fake transport. The real
`analysis.l5.live_exec2.orders.submit_one` is exercised against it, so the
preview/echo/latch/POST sequence under test is the proven one and not a
re-implementation of it.

Run:
    PYTHONPATH=. python3 -m pytest analysis/l5/e1_cohort/test_e1_cohort.py -q
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import recorder as R
from analysis.l5.e1_cohort import runner as RUN
from analysis.l5.e1_cohort import spec as SP
from analysis.l5.e1_cohort import stopping as ST
from analysis.l5.live_exec2 import orders as O

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _isolated_latch(monkeypatch, tmp_path):
    """Every test gets its own latch directory and session id. Nothing in
    these tests can touch a real latch."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "latch"))
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "offline-test-session")
    yield


def snapshot(*, first_leg_price="0.50", complement_bid="0.46",
             window=0, qty=None, **over):
    s = {
        "market": "btc-updown-15m-TEST",
        "window": window,
        "window_open": True,
        "within_handover_tte": True,
        "our_outstanding_orders": 0,
        "our_open_residual": None,
        "qty": qty if qty is not None else SP.FIRST_LEG_QTY,
        "first_leg_price": first_leg_price,
        "first_leg": {"side": "UP", "best_bid": first_leg_price,
                      "best_ask": "0.52"},
        "complement": {"side": "DOWN", "best_bid": complement_bid,
                       "best_ask": "0.54"},
        "fingerprint_pinned": {"matches_this_build": True},
        "ws_state": {"timing_authority": "websocket_strict",
                     "both_tokens_streaming": True,
                     "book_unix": 1000.0},
    }
    s.update(over)
    return s


NOW = 1000.5


class FakeTransport:
    """Stands in for `BoundedOrderTransport`. Echoes the preview, accepts
    one submission, and refuses a second exactly as the real one does."""

    def __init__(self, *, preview_raises=None, submit_raises=None):
        self.preview_raises = preview_raises
        self.submit_raises = submit_raises
        self.calls = []
        self._submits = 0

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path, json.loads(json.dumps(body,
                                                               default=str))))
        if path == O.PREVIEW_PATH:
            if self.preview_raises:
                raise self.preview_raises
            return dict(body["request"])
        if path == O.SUBMIT_PATH:
            self._submits += 1
            if self._submits > 1:
                raise O.MutationRefused("second POST refused")
            if self.submit_raises:
                raise self.submit_raises
            return {"id": "order-%d" % self._submits}
        return {}


@pytest.fixture
def armed(monkeypatch):
    """Pretends the owner armed it, INSIDE THE TEST PROCESS ONLY, so the
    eligibility and submission paths can be exercised. No real key is set
    and nothing leaves the process."""
    monkeypatch.setattr(O, "armed_env_summary",
                        lambda: {"armed": True, "keys_present": {}})
    monkeypatch.setattr(O, "assert_frozen", lambda *a, **k: None)
    yield


# ------------------------------------------------------- the frozen spec

def test_fingerprint_self_check_is_not_vacuous():
    SP.assert_frozen()
    assert SP.fingerprint() == SP.E1_FINGERPRINT
    assert re.fullmatch(r"[0-9a-f]{64}", SP.E1_FINGERPRINT)
    # the literal appears in the SOURCE as a literal, not as a computation
    src = open(os.path.join(HERE, "spec.py"), encoding="utf8").read()
    assert '"%s"' % SP.E1_FINGERPRINT in src


def test_fingerprint_perturbation_is_detected():
    """Every single INVARIANT path, perturbed one at a time, must make
    assert_frozen raise. A freeze check that passes under perturbation is
    vacuous."""
    import copy as _c
    for dotted, _want in SP.INVARIANTS:
        bad = _c.deepcopy(SP.SPEC)
        node = bad
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = "PERTURBED-%s" % dotted
        with pytest.raises(SystemExit):
            SP.assert_frozen(bad)
    # and a perturbation at a path NOT in INVARIANTS is still caught by the
    # fingerprint alone
    bad = _c.deepcopy(SP.SPEC)
    bad["question"] = "PERTURBED"
    with pytest.raises(SystemExit):
        SP.assert_frozen(bad)


def test_diff_values_are_not_aliased_into_the_spec():
    """The inherited `_apply` bug (FROZEN_SPEC_MUTATION_AUDIT.md) is NOT
    inherited: no mutable DIFF value is the same object as the spec's."""
    aliased = [k for k, v in SP.DIFF.items()
               if isinstance(v, (dict, list)) and SP.SPEC.get(k) is v]
    assert aliased == []
    # and mutating the spec's copy cannot reach DIFF
    before = json.dumps(SP.DIFF, sort_keys=True, default=str)
    SP.SPEC["cohort_bounds"]["__probe__"] = 1
    try:
        assert json.dumps(SP.DIFF, sort_keys=True,
                          default=str) == before
    finally:
        SP.SPEC["cohort_bounds"].pop("__probe__", None)


def test_diff_report_hands_out_copies():
    rep = SP.diff_report()
    before = json.dumps(SP.DIFF, sort_keys=True, default=str)
    for v in list(rep["keys_added"].values()):
        if isinstance(v, dict):
            v["__probe__"] = 1
    assert json.dumps(SP.DIFF, sort_keys=True, default=str) == before


def test_the_frozen_parameters_are_exactly_what_was_handed_down():
    assert SP.FIRST_LEG_QTY == D("0.13")
    assert SP.MAX_FIRST_LEG_PRICE == D("0.60")
    assert SP.PAIR_COST_CEILING == D("0.96")
    assert SP.MAX_ATTEMPTS_PER_WINDOW == 20
    assert SP.MAX_EXPOSURE_PER_WINDOW_USD == D("1.56")
    assert SP.AUTHORIZED_WINDOW_CEILING_USD == D("5.00")
    assert SP.WINDOWS == 12
    assert SP.ABSOLUTE_WORST_CASE_USD == D("18.72")
    assert SP.MAX_COHORT_REALIZED_LOSS_USD == D("5.00")
    # and the arithmetic actually holds
    assert 20 * D("0.13") * D("0.60") == D("1.56")
    assert 12 * 20 * D("0.13") * D("0.60") == D("18.72")
    assert SP.MAX_EXPOSURE_PER_WINDOW_USD < SP.AUTHORIZED_WINDOW_CEILING_USD


def test_spec_says_it_is_not_authorized():
    assert SP.SPEC["authorization"]["state"] == "NOT_AUTHORIZED"
    assert SP.SPEC["authorization"]["armed"] is False
    assert SP.SPEC["authorization"]["owner_values_set"] is False
    assert SP.SPEC["authorization"]["deployed"] is False


# ------------------------------------------------ NO DUPLICATE SERIALIZER

FORBIDDEN = ("marketSlug", "ORDER_TYPE_LIMIT", "ORDER_ACTION_BUY",
             "TIME_IN_FORCE_", "participateDontInitiate",
             "manualOrderIndicator", "OUTCOME_SIDE_")


def test_no_file_in_this_package_contains_a_duplicate_serializer():
    """A duplicate serializer caused the A5 HTTP 400, and the repository
    records the same bug happening twice. No file here may build a body."""
    offenders = {}
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py"):
            continue
        if name == os.path.basename(__file__):
            # this test file names the tokens in order to forbid them
            continue
        text = open(os.path.join(HERE, name), encoding="utf8").read()
        hits = [tok for tok in FORBIDDEN if tok in text]
        if re.search(r'["\']request["\']\s*:', text):
            hits.append('bare "request": envelope key')
        if hits:
            offenders[name] = hits
    assert offenders == {}, offenders


def test_the_serializer_is_the_proven_one():
    assert SP.SPEC["serializer"]["module"] == "src.l5_order_body"
    assert SP.SPEC["serializer"]["duplicated_here"] is False
    assert SP.SPEC["mutation_path"]["module"] == "analysis.l5.live_exec2.orders"


# ------------------------------------------------------------ pair economics

def test_pair_cost_above_the_ceiling_is_refused():
    # a first leg at 0.60 with a complement bid of 0.50 must be priced down
    plan = RUN.complement_price(first_leg_price="0.60", qty="0.13",
                                complement_best_bid="0.50")
    assert D(plan["price_usd"]) <= D("0.36")
    assert plan["pair_cost"]["within_ceiling"]
    # and a first leg so expensive that no tick price works is REFUSED
    with pytest.raises(RUN.ComplementInfeasible):
        RUN.complement_price(first_leg_price="0.96", qty="0.13",
                             complement_best_bid="0.50")
    # the ceiling is never relaxed to make a pair fit
    for a in ("0.40", "0.50", "0.59", "0.60"):
        p = RUN.complement_price(first_leg_price=a, qty="0.13",
                                 complement_best_bid="0.99")
        assert (D(a) + D(p["price_usd"])) <= SP.PAIR_COST_CEILING


def test_a_pair_at_the_ceiling_is_cent_observable_at_013():
    """The reason 0.13 is the quantity: $0.04/share x 0.13 = $0.0052 -> $0.01
    under banker's rounding, and 0.12 would vanish."""
    gross = D("1.00") - SP.PAIR_COST_CEILING
    assert gross == D("0.04")
    assert (D("0.13") * gross).quantize(D("0.01")) == D("0.01")
    assert (D("0.12") * gross).quantize(D("0.01")) == D("0.00")


def test_rebate_at_this_size_is_structurally_zero():
    from src import pmus_fee_schedule as FEE
    for p in ("0.10", "0.30", "0.50", "0.60"):
        assert FEE.maker_rebate(D(p), D("0.13")) == D("0")


# -------------------------------------------------- complement sizing rules

def test_partial_first_leg_fill_sizes_the_complement_to_the_actual_fill(armed):
    t = FakeTransport()
    snap = snapshot()
    out = RUN.submit_complement(window=0, slot=0, snapshot=snap,
                                first_leg_filled_qty="0.07",
                                tracker=ST.CohortTracker(),
                                transport_factory=lambda: t)
    assert out["e1_complement_qty"] == "0.07"
    assert out["sized_to_actual_filled_qty"] is True
    submitted = [c for c in t.calls if c[1] == O.SUBMIT_PATH]
    assert len(submitted) == 1
    assert submitted[0][2]["quantity"] == 0.07
    # NOT the placed 0.13
    assert submitted[0][2]["quantity"] != 0.13


def test_a_fill_below_the_venue_minimum_is_residual_not_a_complement():
    with pytest.raises(RUN.ComplementInfeasible):
        RUN.complement_qty(first_leg_filled_qty="0.009")
    with pytest.raises(RUN.ComplementInfeasible):
        RUN.complement_qty(first_leg_filled_qty="0")


def test_no_requote_the_second_complement_is_refused_at_the_latch(armed):
    snap = snapshot()
    tr = ST.CohortTracker()
    RUN.submit_complement(window=0, slot=3, snapshot=snap,
                          first_leg_filled_qty="0.13", tracker=tr,
                          transport_factory=FakeTransport)
    with pytest.raises(L.LatchSpent):
        RUN.submit_complement(window=0, slot=3, snapshot=snap,
                              first_leg_filled_qty="0.13", tracker=tr,
                              transport_factory=FakeTransport)


def test_spec_forbids_taker_rescue_chasing_and_requote():
    c = SP.SPEC["complement"]
    assert c["taker_rescue"] is False
    assert c["chasing"] is False
    assert c["requote"] is False


# ------------------------------------------------------- complement no-fill

def test_complement_never_fills_bounded_residual_then_deadman(armed, tmp_path):
    """The residual is bounded by the first leg's filled quantity, and the
    handover is a deadman cancel -- never a taker rescue."""
    rec = R.Recorder(str(tmp_path / "led"))
    tr = ST.CohortTracker()
    tr.record_placement(window=0, notional_usd=D("0.13") * D("0.50"))
    tr.record_first_leg_result(filled_qty="0.13")
    tr.record_completion(complete=False)
    row = rec.record_attempt(
        window=0, slot=0, market="m",
        ws_state={"timing_authority": "websocket_strict",
                  "both_tokens_streaming": True},
        first_leg={"side": "UP", "price": "0.50", "qty": "0.13"},
        queue={"size_at_our_price": "4.0", "size_ahead_of_us": "4.0"},
        timestamps={"submitted": 1, "acknowledged": 2, "resting": 2,
                    "time_to_first_fill_s": 3.0,
                    "time_to_completion_s": None},
        fill={"filled_qty": "0.13", "partial": False},
        complement={"price": "0.46", "submitted": 5, "filled_qty": "0",
                    "no_fill": True},
        outcome={"state": "RESIDUAL", "residual_shares": "0.13",
                 "residual_duration_s": 420.0,
                 "realized_pair_cost_usd_per_share": None},
        cancellations=[{"event": "DEADMAN_CANCEL_ALL", "at_handover": True,
                        "fill_raced_it": False}],
        markouts={"book_moved": "DOWN", "source": "websocket_tape"})
    assert row["outcome"]["state"] == "RESIDUAL"
    assert D(row["outcome"]["residual_shares"]) <= SP.FIRST_LEG_QTY
    assert row["cancellations"][0]["event"] == "DEADMAN_CANCEL_ALL"
    assert row["REALIZED_CLAIM"] is False
    # the residual never becomes a taker rescue
    assert SP.SPEC["complement"]["taker_rescue"] is False


# ---------------------------------------------------- lifecycle classification

def test_preview_rejection_leaves_the_latch_untouched(armed):
    snap = snapshot()
    tr = ST.CohortTracker()
    err = O.VenueHTTPError(method="POST", path=O.PREVIEW_PATH, status=400,
                           reason="Bad Request", body="{}", headers={})
    t = FakeTransport(preview_raises=err)
    before = L.window_state(window=0)["attempts_spent"]
    with pytest.raises(O.PreviewRejected) as ei:
        RUN.submit_complement(window=0, slot=0, snapshot=snap,
                              first_leg_filled_qty="0.13", tracker=tr,
                              transport_factory=lambda: t)
    assert ei.value.crossed_order_boundary is False
    assert ei.value.latch_consumed is False
    # the ATTEMPT slots are untouched: a preview creates nothing
    assert L.window_state(window=0)["attempts_spent"] == before
    assert tr.ambiguous_submissions == 0
    assert [c for c in t.calls if c[1] == O.SUBMIT_PATH] == []


def test_ambiguous_submission_is_one_attempt_and_is_never_retried(armed):
    snap = snapshot()
    tr = ST.CohortTracker()
    err = O.VenueHTTPError(method="POST", path=O.SUBMIT_PATH, status=503,
                           reason="Unavailable", body="{}", headers={})
    t = FakeTransport(submit_raises=err)
    with pytest.raises(O.SubmissionAmbiguous) as ei:
        RUN.submit_complement(window=0, slot=0, snapshot=snap,
                              first_leg_filled_qty="0.13", tracker=tr,
                              transport_factory=lambda: t)
    assert ei.value.crossed_order_boundary is True
    assert tr.ambiguous_submissions == 1
    # exactly ONE submission attempt was made
    assert len([c for c in t.calls if c[1] == O.SUBMIT_PATH]) == 1
    # and the cohort is stopped, not retrying
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAILURE_TO_MEASURE
    assert any("AMBIGUOUS_SUBMISSION_NEVER_RETRIED" in s
               for s in v["stop_reasons"])
    assert v["is_a_FAIL"] is False
    # the slot is NOT released
    assert os.path.exists(L.slot_path(window=0, slot=0,
                                      role=L.ROLE_COMPLEMENT))


# ----------------------------------------------------------------- the latch

def test_latch_is_named_slots_and_bounds_the_window_at_twenty():
    for i in range(SP.MAX_ATTEMPTS_PER_WINDOW):
        rec = L.consume_attempt(window=0, slug="m", side="UP", price="0.50",
                                qty="0.13")
        assert rec["slot"] == i
        assert rec["role"] == L.ROLE_FIRST_LEG
    st = L.window_state(window=0)
    assert st["attempts_spent"] == 20
    assert st["exhausted"] is True
    with pytest.raises(L.WindowExhausted):
        L.consume_attempt(window=0, slug="m", side="UP", price="0.50",
                          qty="0.13")
    # twenty distinct FILES, not a counter
    assert len({L.slot_path(window=0, slot=i) for i in range(20)}) == 20
    assert st["is_a_bare_counter"] is False
    # a different window is unaffected
    assert L.window_state(window=1)["attempts_spent"] == 0


def test_attempt_21_in_a_window_is_refused_by_the_tracker_too():
    tr = ST.CohortTracker()
    for _ in range(20):
        tr.record_placement(window=0, notional_usd=D("0.001"))
    g = tr.may_place(window=0, notional_usd=D("0.001"))
    assert g["ok"] is False
    assert any("ATTEMPT_21_REFUSED_MAX_20" in r for r in g["refusals"])


def test_latch_slots_are_never_released_on_failure():
    rec = L.consume_attempt(window=2, slug="m", side="UP", price="0.50",
                            qty="0.13")
    assert os.path.exists(rec["path"])
    assert rec["NOT_RELEASED_ON_FAILURE"] is True


def test_latch_fails_closed_without_a_session_id(monkeypatch):
    monkeypatch.delenv("PMUS_EXEC1_SESSION_ID", raising=False)
    with pytest.raises(L.LatchUnavailable):
        L.consume_attempt(window=0, slug="m", side="UP", price="0.50",
                          qty="0.13")


def test_latch_refuses_a_window_outside_the_authorized_twelve():
    with pytest.raises(L.LatchUnavailable):
        L.consume_attempt(window=12, slug="m", side="UP", price="0.50",
                          qty="0.13")


# ------------------------------------------------------------- hard stops

def test_cohort_loss_stop_at_five_dollars():
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="-4.99")
    assert tr.hard_stops() == []
    assert tr.may_place(window=0, notional_usd="0.078")["ok"] is True
    tr.record_confirmed_cash(amount_usd="-0.01")
    assert tr.realized_loss_usd == D("5.00")
    stops = tr.hard_stops()
    assert any("COHORT_REALIZED_LOSS_AT_OR_ABOVE_5.00" in s for s in stops)
    assert tr.may_place(window=0, notional_usd="0.078")["ok"] is False
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAILURE_TO_MEASURE
    assert v["is_a_FAIL"] is False and v["is_a_result"] is False


def test_only_confirmed_cash_moves_the_loss_counter():
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="+1.00")
    assert tr.realized_loss_usd == D("0")


def test_window_exposure_stop_at_156():
    tr = ST.CohortTracker()
    for _ in range(19):
        tr.record_placement(window=0, notional_usd=D("0.13") * D("0.60"))
    assert tr.window_exposure_usd[0] == D("1.4820")
    # the twentieth attempt would reach 1.56 exactly, so it is refused
    g = tr.may_place(window=0, notional_usd=D("0.13") * D("0.60"))
    assert g["ok"] is False
    assert any("PROJECTED_WINDOW_EXPOSURE" in r for r in g["refusals"])


def test_safety_violation_is_a_hard_stop():
    tr = ST.CohortTracker()
    tr.violate("the book went backwards")
    assert any("SAFETY_INVARIANT_VIOLATION" in s for s in tr.hard_stops())
    assert tr.verdict()["verdict"] == ST.VERDICT_FAILURE_TO_MEASURE


def test_venue_error_storm_is_a_hard_stop():
    tr = ST.CohortTracker()
    for _ in range(5):
        tr.record_venue_error()
    assert any("VENUE_ERROR_STORM" in s for s in tr.hard_stops())
    tr2 = ST.CohortTracker()
    for _ in range(4):
        tr2.record_venue_error()
    tr2.record_venue_ok()
    tr2.record_venue_error()
    assert tr2.hard_stops() == []


# ------------------------------------------------------ PASS / FAIL / CONTINUE

def test_fail_on_35_consecutive_no_fills():
    tr = ST.CohortTracker()
    for _ in range(34):
        tr.record_first_leg_result(filled_qty="0")
    assert tr.verdict()["verdict"] == ST.VERDICT_CONTINUE
    tr.record_first_leg_result(filled_qty="0")
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAIL
    assert any("35 consecutive no-fill" in r for r in v["reasons"])
    assert v["is_an_authorization"] is False


def test_fail_boundary_is_the_designs_and_not_a_new_number():
    from analysis.l5.measurement import cohort_design as CD
    assert ST.FAIL_CONSECUTIVE_NO_FILL == \
        CD.e1_fail_boundary_consecutive_misses() == 35
    assert ST.HARD_TRUNCATION == CD.e1_hard_truncation_placements() == 4310
    assert ST.BINDING_FILL_GATE == CD.e1_binding_gates()["fill_gate"] == 431
    assert ST.ECONOMICS_FORECLOSURE_USD_PER_SHARE == \
        CD.e1_economics_foreclosure_usd_per_share()


def test_fail_on_sprt_crossing_h0_with_economics_foreclosed():
    tr = ST.CohortTracker()
    for _ in range(8):
        tr.record_completion(complete=False)
    # the SPRT alone is not enough; the economics condition is conjunctive
    assert tr.verdict()["verdict"] == ST.VERDICT_CONTINUE
    tr.per_share_expectancy_upper_usd = D("-0.02")
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAIL
    assert any("SPRT walk" in r for r in v["reasons"])
    assert ST.sprt_walk(0, 8) <= ST.sprt_boundaries()["accept_h0_at_or_below"]


def test_a_fill_racing_a_cancel_is_counted_and_is_a_hard_fail():
    tr = ST.CohortTracker()
    tr.record_post_cancel_ack_fill()
    assert tr.post_cancel_ack_fills == 1
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAIL
    assert any("after a cancel acknowledgement" in r for r in v["reasons"])
    assert any("invalidates the design" in r for r in v["reasons"])


def test_a_cashflow_without_source_identity_is_a_fail():
    tr = ST.CohortTracker()
    tr.record_unattributed_cashflow()
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAIL
    assert any("source identity" in r for r in v["reasons"])


def test_pass_is_unreachable_in_this_authorization_and_says_so():
    f = ST.project_feasibility()
    assert f["PASS_reachable_in_this_authorization"] is False
    assert f["placement_gate_reachable"] is False
    assert f["binding_fill_gate_reachable"] is False
    assert f["EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION"]
    assert f["anything_widened_here"] is False
    rows = {r["p_fill"]: r for r in f["rows"]}
    assert rows[0.35]["expected_fills_in_240_placements"] == 84
    assert rows[0.20]["expected_fills_in_240_placements"] == 48
    assert rows[0.20]["reaches_sprt_expected_81_fills"] is False


def test_continue_terminates_explicitly_when_the_fill_rate_is_too_low():
    """240 placements with a low fill rate must say INCONCLUSIVE_FILL_RATE_
    TOO_LOW out loud, not run out silently."""
    tr = ST.CohortTracker()
    # 160 placements, 10 fills: at most 10 + 80 = 90 >= 81, still CONTINUE
    for i in range(160):
        tr.record_placement(window=i // 20, notional_usd=D("0.001"))
        tr.record_first_leg_result(filled_qty="0.13" if i % 16 == 0 else "0")
    assert tr.verdict()["verdict"] == ST.VERDICT_CONTINUE
    # push to 200 placements with no further fills: 10 + 40 = 50 < 81
    for i in range(160, 200):
        tr.record_placement(window=i // 20, notional_usd=D("0.001"))
        tr.record_first_leg_result(filled_qty="0")
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_UNDECIDABLE
    assert v["is_a_FAIL"] is False and v["is_a_PASS"] is False
    assert v["EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION"]
    assert v["anything_widened_automatically"] is False


def test_a_hard_stop_is_never_reported_as_a_fail():
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="-5.00")
    v = tr.verdict()
    assert v["verdict"] != ST.VERDICT_FAIL
    assert v["verdict"] == ST.VERDICT_FAILURE_TO_MEASURE
    assert v["report_partial_posteriors"] is True


# ------------------------------------------------------------- eligibility

def test_eligible_refuses_while_disarmed():
    el = RUN.eligible(snapshot(), ST.CohortTracker(), now=NOW)
    assert el["ok"] is False
    assert "E1_DISARMED" in el["refusals"]
    assert el["ARMED"] is False


def test_eligible_passes_on_a_clean_armed_snapshot(armed):
    el = RUN.eligible(snapshot(), ST.CohortTracker(), now=NOW)
    assert el["ok"] is True, el["refusals"]
    assert el["complement_plan"]["price_usd"] == "0.46"


def test_eligibility_refusals_one_at_a_time(armed):
    tr = ST.CohortTracker()
    cases = {
        "E4_WINDOW_NOT_OPEN": {"window_open": False},
        "E4_PAST_HANDOVER_TTE": {"within_handover_tte": False},
        "E3_OUR_ORDER_IS_OUTSTANDING": {"our_outstanding_orders": 1},
        "E3_OUR_RESIDUAL_IS_OPEN": {"our_open_residual": "0.13"},
        "E1_FINGERPRINT_NOT_PINNED_TO_THIS_BUILD": {
            "fingerprint_pinned": {"matches_this_build": False}},
        "E7_BOTH_TOKENS_NOT_STREAMING": {
            "ws_state": {"timing_authority": "websocket_strict",
                         "both_tokens_streaming": False,
                         "book_unix": 1000.0}},
        "E7_TIMING_AUTHORITY_NOT_WEBSOCKET_STRICT": {
            "ws_state": {"timing_authority": "rest_same_second_reachability",
                         "both_tokens_streaming": True,
                         "book_unix": 1000.0}},
    }
    for want, over in cases.items():
        el = RUN.eligible(snapshot(**over), tr, now=NOW)
        assert el["ok"] is False
        assert want in el["refusals"], (want, el["refusals"])


def test_a_stale_book_is_refused(armed):
    el = RUN.eligible(snapshot(), ST.CohortTracker(), now=1000.0 + 2.5)
    assert any(r.startswith("E7_BOOK_STALE") for r in el["refusals"])


def test_price_above_the_frozen_ceiling_is_refused(armed):
    el = RUN.eligible(snapshot(first_leg_price="0.61",
                               first_leg={"side": "UP", "best_bid": "0.61",
                                          "best_ask": "0.63"}),
                      ST.CohortTracker(), now=NOW)
    assert "E9_PRICE_ABOVE_CEILING:0.61" in el["refusals"]


def test_a_crossing_price_is_refused_locally(armed):
    el = RUN.eligible(snapshot(first_leg_price="0.52",
                               first_leg={"side": "UP", "best_bid": "0.52",
                                          "best_ask": "0.52"}),
                      ST.CohortTracker(), now=NOW)
    assert any(r.startswith("E9_WOULD_CROSS") for r in el["refusals"])


def test_quantity_other_than_013_is_refused(armed):
    el = RUN.eligible(snapshot(qty="1.00"), ST.CohortTracker(), now=NOW)
    assert "QTY_NOT_THE_FROZEN_0.13:1.00" in el["refusals"]


def test_an_infeasible_complement_refuses_before_the_first_leg(armed):
    el = RUN.eligible(snapshot(first_leg_price="0.60", complement_bid="0.00",
                               first_leg={"side": "UP", "best_bid": "0.60",
                                          "best_ask": "0.62"}),
                      ST.CohortTracker(), now=NOW)
    assert any(r.startswith("E10_COMPLEMENT_INFEASIBLE")
               for r in el["refusals"])


def test_submit_first_leg_refuses_when_ineligible():
    with pytest.raises(RUN.Ineligible):
        RUN.submit_first_leg(snapshot(), ST.CohortTracker(), now=NOW,
                             transport_factory=FakeTransport)


def test_a_clean_first_leg_consumes_exactly_one_slot(armed):
    tr = ST.CohortTracker()
    t = FakeTransport()
    out = RUN.submit_first_leg(snapshot(), tr, now=NOW,
                               transport_factory=lambda: t)
    assert out["e1_latch_slot"]["slot"] == 0
    assert L.window_state(window=0)["attempts_spent"] == 1
    assert tr.placements == 1
    assert len([c for c in t.calls if c[1] == O.SUBMIT_PATH]) == 1
    body = [c for c in t.calls if c[1] == O.SUBMIT_PATH][0][2]
    assert body["quantity"] == 0.13
    assert body["price"]["currency"] == "USD"
    assert body["participateDontInitiate"] is True


# -------------------------------------------------------------- the recorder

def test_recorder_is_append_only_with_provenance_on_every_row(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    for i in range(3):
        rec.record_event(window=0, slot=i, event="SUBMITTED")
    rows = rec.read(R.EVENTS)
    assert len(rows) == 3
    for row in rows:
        assert row["probe"] == SP.PROBE
        assert row["spec_fingerprint"] == SP.E1_FINGERPRINT
        assert row["timing_authority"] == "websocket_strict"
        assert row["rest_may_order_the_tape"] is False
        assert row["append_only"] is True
    rec.record_event(window=0, slot=9, event="CANCEL_ACK")
    assert len(rec.read(R.EVENTS)) == 4          # nothing was overwritten


def test_an_attempt_row_may_never_be_labelled_realized(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    with pytest.raises(R.RecorderRefused):
        rec.record_attempt(
            window=0, slot=0, market="m",
            ws_state={"timing_authority": "websocket_strict",
                      "both_tokens_streaming": True},
            first_leg={}, queue={}, timestamps={}, fill={},
            complement=None, outcome={}, cancellations=[], markouts={},
            label=R.REALIZED)


def test_an_attempt_row_requires_the_websocket_as_timing_authority(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    for ws in ({"timing_authority": "rest_same_second_reachability",
                "both_tokens_streaming": True},
               {"timing_authority": "websocket_strict",
                "both_tokens_streaming": False}):
        with pytest.raises(R.RecorderRefused):
            rec.record_attempt(window=0, slot=0, market="m", ws_state=ws,
                               first_leg={}, queue={}, timestamps={},
                               fill={}, complement=None, outcome={},
                               cancellations=[], markouts={})


def test_venue_money_refuses_a_modelled_figure(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    with pytest.raises(R.RecorderRefused):
        rec.record_venue_money(window=0, slot=0, kind="maker_rebate",
                               amount_usd="0.01", source="modelled",
                               source_identity={"sufficient": True})
    with pytest.raises(R.RecorderRefused):
        rec.record_venue_money(window=0, slot=0, kind="maker_rebate",
                               amount_usd="0.01",
                               source="venue_rebate_credit",
                               source_identity={"sufficient": False})
    row = rec.record_venue_money(window=0, slot=0, kind="maker_rebate",
                                 amount_usd="0.00",
                                 source="venue_rebate_credit",
                                 source_identity={"sufficient": True,
                                                  "statement_id": "x"})
    assert row["modelled"] is False
    assert row["kept_separate_from_trading_pnl"] is True


def test_realized_requires_settlement_and_confirmed_cash(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    a = rec.record_settlement(window=0, slot=0, market="m", settled=True,
                              cash_confirmed=False, cash_usd=None,
                              source_identity={"sufficient": False})
    assert a["label"] == "SETTLED" and a["REALIZED_CLAIM"] is False
    b = rec.record_settlement(window=0, slot=1, market="m", settled=False,
                              cash_confirmed=False, cash_usd=None,
                              source_identity={})
    assert b["label"] == "FILLED"
    c = rec.record_settlement(window=0, slot=2, market="m", settled=True,
                              cash_confirmed=True, cash_usd="0.01",
                              source_identity={"sufficient": True,
                                               "statement_id": "s"})
    assert c["label"] == R.REALIZED and c["REALIZED_CLAIM"] is True
    with pytest.raises(R.RecorderRefused):
        rec.record_settlement(window=0, slot=3, market="m", settled=True,
                              cash_confirmed=True, cash_usd="0.05",
                              source_identity={"sufficient": False})


def test_a_fill_racing_a_cancel_is_recorded_as_such(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    row = rec.record_event(window=0, slot=0, event="FILL_AFTER_CANCEL_ACK",
                           venue_ts="2026-10-07T00:00:02Z",
                           monotonic_ns=123,
                           detail={"cancel_ack_venue_ts":
                                   "2026-10-07T00:00:01Z",
                                   "fill_raced_the_cancel": True})
    assert row["detail"]["fill_raced_the_cancel"] is True
    assert row["event"] == "FILL_AFTER_CANCEL_ACK"


# -------------------------------------------------------------------- boot

def test_boot_asserts_and_reports_disarmed():
    rep = RUN.boot()
    assert rep["fingerprint"] == SP.E1_FINGERPRINT
    assert rep["armed"] is False and rep["authorized"] is False
    assert rep["NO_ORDER_WILL_BE_PLACED"] is True
    assert rep["AUTHORIZATION"].startswith("NOT AUTHORIZED")
    assert rep["feasibility"]["PASS_reachable_in_this_authorization"] is False
    assert rep["diff_report"]["to_fingerprint"] == SP.E1_FINGERPRINT


def test_no_network_is_reachable_from_these_tests():
    """The tests never construct a real transport, so nothing can dial out.
    Asserted structurally: every submission in this file goes through a
    FakeTransport."""
    src = open(os.path.abspath(__file__), encoding="utf8").read()
    assert "O.BoundedOrderTransport()" not in src
    assert "urllib" not in src


def test_the_manifest_exists_has_the_header_and_the_eight_items():
    path = os.path.join(HERE, "E1_AUTHORIZATION_MANIFEST.md")
    text = open(path, encoding="utf8").read()
    assert "NOT AUTHORIZED" in text[:800]
    for i in range(1, 9):
        assert "## %d." % i in text, i
    assert SP.E1_FINGERPRINT in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
