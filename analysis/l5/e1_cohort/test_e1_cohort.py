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
                     "book_unix": 1000.0,
                     "first_leg": {"best_bid": first_leg_price,
                                   "best_ask": "0.52"},
                     "complement": {"best_bid": complement_bid,
                                    "best_ask": "0.54"}},
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
            # the venue answers the preview with the order under `order`,
            # which is what `src.l5_preview_gate.require_echo` compares
            return {"order": dict(body["request"])}
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
    # the CREDITED rebate is cent-rounded, and at 0.13 shares the raw
    # rebate 0.0125 x 0.13 x p(1-p) <= $0.000406 rounds to $0.00 at EVERY
    # price. That is structural, not a weak signal.
    for p in ("0.01", "0.10", "0.30", "0.50", "0.60", "0.99"):
        assert FEE.maker_rebate(D(p), D("0.13"), cents=True) == D("0.00")
        assert FEE.maker_rebate(D(p), D("0.13")) <= D("0.0005")


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
    assert v["verdict"] == ST.VERDICT_CONTINUE
    assert v["truncation_cause"] == ST.TRUNCATION_AMBIGUOUS
    assert any("AMBIGUOUS_SUBMISSION_NEVER_RETRIED" in s
               for s in v["stop_reasons"])
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
    tr.record_confirmed_cash(amount_usd="-4.90")
    assert tr.hard_stops() == []
    # 4.90 + 0.078 = 4.978 <= 5.00, so one more attempt is still permitted
    assert tr.may_place(window=0, notional_usd="0.078")["ok"] is True
    tr.record_confirmed_cash(amount_usd="-0.10")
    assert tr.realized_loss_usd == D("5.00")
    stops = tr.hard_stops()
    assert any("COHORT_REALIZED_LOSS_AT_OR_ABOVE_5.00" in s for s in stops)
    assert tr.may_place(window=0, notional_usd="0.078")["ok"] is False
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_CONTINUE
    assert v["truncation_cause"] == ST.TRUNCATION_RISK_LIMIT
    assert v["truncated_by_the_risk_limit"] is True
    assert v["truncated_by_a_low_fill_rate"] is False


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
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_CONTINUE
    assert v["truncation_cause"] == ST.TRUNCATION_SAFETY


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


def test_a_fill_racing_a_cancel_is_counted_and_is_a_FAIL():
    tr = ST.CohortTracker()
    tr.record_post_cancel_ack_fill()
    assert tr.post_cancel_ack_fills == 1
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAIL
    assert any("AFTER a cancel acknowledgement" in r for r in v["reasons"])
    assert any("invalidates the design" in r for r in v["reasons"])


def test_a_cashflow_without_source_identity_is_a_fail():
    tr = ST.CohortTracker()
    tr.record_unattributed_cashflow()
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_FAIL
    assert any("source identity" in r for r in v["reasons"])


def test_there_is_no_PASS_outcome_at_all():
    assert not hasattr(ST, "VERDICT_PASS")
    assert ST.AUTHORIZED_VERDICTS == (
        "FAIL", "COMPLETION_SPRT_POSITIVE_CROSSING",
        "CONTINUE_INSUFFICIENT_EVIDENCE")
    assert SP.SPEC["outcome_taxonomy"]["formal_PASS_exists_as_an_outcome"] \
        is False
    assert SP.SPEC["outcome_taxonomy"][
        "no_code_path_can_label_a_result_PASS"] is True


def test_PASS_never_appears_as_a_terminal_classification():
    """Not in the taxonomy, not in the spec's authorized outcomes, and not
    producible by any reachable tracker state."""
    assert "PASS" not in ST.AUTHORIZED_VERDICTS
    for v in ST.AUTHORIZED_VERDICTS:
        assert v != "PASS"
    assert "PASS" not in SP.SPEC["outcome_taxonomy"]["authorized_outcomes"]
    # no source line in stopping.py assigns a verdict of "PASS"
    src = open(os.path.join(HERE, "stopping.py"), encoding="utf8").read()
    assert 'VERDICT_PASS' not in src
    assert '"verdict": "PASS"' not in src
    # and over a wide sweep of tracker states, no verdict is ever PASS
    seen = set()
    for nf in (0, 10, 35):
        for comp in (0, 20, 200):
            for noncomp in (0, 8, 50):
                for exp in (None, D("-0.02"), D("0.05")):
                    tr = ST.CohortTracker()
                    tr.consecutive_no_fill = nf
                    tr.completions, tr.non_completions = comp, noncomp
                    tr.per_share_expectancy_upper_usd = exp
                    tr.placements, tr.fills = 500, 500
                    v = tr.verdict()["verdict"]
                    assert v in ST.AUTHORIZED_VERDICTS, v
                    seen.add(v)
    assert "PASS" not in seen


def test_sprt_positive_crossing_carries_all_four_disclaimers_as_fields():
    tr = ST.CohortTracker()
    for _ in range(40):
        tr.record_completion(complete=True)
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_SPRT_POSITIVE
    # FIELDS, not prose, so a summary cannot drop them
    assert v["evidence_specifically_for"] == \
        "the maker->maker completion question, and nothing else"
    assert v["is_formal_E1_PASS"] is False
    assert v["is_proof_of_positive_expectancy"] is False
    assert v["is_authorization_for_E2"] is False
    assert v["disclaimers"] == ST.SPRT_POSITIVE_DISCLAIMERS
    assert v["is_an_authorization"] is False
    assert v["sprt_walk"] >= ST.sprt_boundaries()["accept_h1_at_or_above"]


def test_a_risk_truncation_is_distinguishable_from_a_low_fill_truncation():
    risk = ST.CohortTracker()
    risk.record_confirmed_cash(amount_usd="-5.00")
    rv = risk.verdict()
    assert rv["verdict"] == ST.VERDICT_CONTINUE
    assert rv["truncation_cause"] == ST.TRUNCATION_RISK_LIMIT
    assert rv["truncated_by_the_risk_limit"] is True
    assert rv["truncated_by_a_low_fill_rate"] is False

    low = ST.CohortTracker()
    # a fill every 30 placements: 8 fills in 240, and the longest no-fill
    # run is 29, which stays BELOW the 35-consecutive FAIL boundary. So this
    # is a genuine low-fill truncation and not a disguised FAIL.
    for i in range(240):
        low.record_placement(window=i // 20, notional_usd=D("0.0001"))
        low.record_first_leg_result(
            filled_qty="0.13" if i % 30 == 0 else "0")
    assert low.fails_so_far() == [] if hasattr(low, "fails_so_far") else True
    assert low.consecutive_no_fill < ST.FAIL_CONSECUTIVE_NO_FILL
    lv = low.verdict()
    assert lv["verdict"] == ST.VERDICT_CONTINUE
    assert lv["truncation_cause"] == ST.TRUNCATION_LOW_FILL_RATE
    assert lv["truncated_by_a_low_fill_rate"] is True
    assert lv["truncated_by_the_risk_limit"] is False
    assert rv["truncation_cause"] != lv["truncation_cause"]


def test_continue_terminates_explicitly_when_the_fill_rate_is_too_low():
    tr = ST.CohortTracker()
    for i in range(160):
        tr.record_placement(window=i // 20, notional_usd=D("0.0001"))
        tr.record_first_leg_result(filled_qty="0.13" if i % 16 == 0 else "0")
    v0 = tr.verdict()
    assert v0["verdict"] == ST.VERDICT_CONTINUE
    assert v0["truncation_cause"] is None and v0["stopped"] is False
    for i in range(160, 200):
        tr.record_placement(window=i // 20, notional_usd=D("0.0001"))
        # one fill at 180 keeps the no-fill run under 35, so this is a
        # low-fill truncation and not the consecutive-no-fill FAIL
        tr.record_first_leg_result(filled_qty="0.13" if i == 180 else "0")
    assert tr.consecutive_no_fill < ST.FAIL_CONSECUTIVE_NO_FILL
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_CONTINUE
    assert v["truncation_cause"] == ST.TRUNCATION_LOW_FILL_RATE
    assert v["stopped"] is True
    assert v["EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION"]
    assert v["anything_widened_automatically"] is False
    assert v["raw_tape_must_still_yield_the_estimation_chain"] is True


def test_parameters_do_not_move_on_interim_results():
    tr = ST.CohortTracker()
    for i in range(240):
        tr.record_placement(window=i // 20, notional_usd=D("0.0001"))
        tr.record_first_leg_result(filled_qty="0")
        assert SP.MAX_PLACEMENTS == 240
        assert SP.WINDOWS == 12
        assert SP.FIRST_LEG_QTY == D("0.13")
        assert SP.MAX_COHORT_REALIZED_LOSS_USD == D("5.00")
        assert SP.MAX_EXPOSURE_PER_WINDOW_USD == D("1.56")
    assert tr.verdict()["parameters_moved_on_interim_results"] is False


def test_a_hard_stop_is_not_a_FAIL():
    """Only a PREREGISTERED boundary yields FAIL. A risk stop is
    CONTINUE_INSUFFICIENT_EVIDENCE with the cause named."""
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="-5.00")
    v = tr.verdict()
    assert v["verdict"] != ST.VERDICT_FAIL
    assert v["verdict"] == ST.VERDICT_CONTINUE


# ------------------------------- the FORWARD-LOOKING $5.00 risk invariant

def test_the_invariant_counts_the_attempt_about_to_be_submitted():
    tr = ST.CohortTracker()
    r = tr.forward_looking_report(prospective_attempts=1)
    assert r["realized_cohort_loss_usd"] == "0"
    assert r["unresolved_residual_max_loss_usd"] == "0"
    assert r["prospective_attempt_max_loss_usd"] == "0.0780"
    assert r["forward_looking_total_usd"] == "0.0780"
    assert r["within_ceiling"] is True
    assert ST.PER_ATTEMPT_MAX_LOSS_USD == D("0.13") * D("0.60") == D("0.0780")


def test_the_bound_blocks_a_new_first_leg_when_residuals_total_near_five():
    tr = ST.CohortTracker()
    # 63 residuals of 0.13 x 0.60 = 0.078 each -> $4.914
    for i in range(63):
        tr.open_residual(window=0, slot=i, filled_qty="0.13",
                         fill_price_usd="0.60")
    assert tr.unresolved_residual_max_loss_usd() == D("4.9140")
    # + one more attempt at 0.078 = 4.992 <= 5.00, so still permitted
    assert tr.forward_looking_report(
        prospective_attempts=1)["within_ceiling"] is True
    tr.open_residual(window=0, slot=99, filled_qty="0.13",
                     fill_price_usd="0.60")
    assert tr.unresolved_residual_max_loss_usd() == D("4.9920")
    # 4.992 + 0.078 = 5.070 > 5.00 -> the NEW FIRST LEG IS REFUSED
    r = tr.forward_looking_report(prospective_attempts=1)
    assert r["forward_looking_total_usd"] == "5.0700"
    assert r["within_ceiling"] is False
    g = tr.may_place(window=1, notional_usd=D("0.078"))
    assert g["ok"] is False
    assert any("FORWARD_LOOKING_LOSS_BOUND" in x for x in g["refusals"])


def test_five_dollars_is_a_true_ceiling_not_a_ceiling_plus_one_attempt():
    """Walk a tracker right up to the boundary and assert the committed
    total NEVER exceeds $5.00, attempt by attempt."""
    tr = ST.CohortTracker()
    n = 0
    while tr.may_place(window=0, notional_usd=D("0.078"))["ok"]:
        tr.open_residual(window=0, slot=n, filled_qty="0.13",
                         fill_price_usd="0.60")
        n += 1
        committed = tr.forward_looking_loss_usd(prospective_attempts=0)
        assert committed <= D("5.00"), committed
        if n > 100:
            break
    assert tr.forward_looking_loss_usd(prospective_attempts=0) <= D("5.00")
    assert tr.forward_looking_loss_usd(prospective_attempts=1) > D("5.00")


def test_a_confirmed_settled_win_reduces_the_realized_component():
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="-1.00")
    assert tr.realized_loss_usd == D("1.00")
    tr.record_confirmed_cash(amount_usd="0.40")
    assert tr.realized_loss_usd == D("0.60")
    # and resolving a residual by confirmed cash frees its headroom
    tr.open_residual(window=0, slot=0, filled_qty="0.13",
                     fill_price_usd="0.60")
    assert tr.unresolved_residual_max_loss_usd() == D("0.0780")
    tr.record_confirmed_cash(amount_usd="0.13", key=(0, 0))
    assert tr.unresolved_residual_max_loss_usd() == D("0")


FORBIDDEN_REDUCERS = (
    ("MARK_TO_MARKET", lambda t: t.offer_mark_to_market("2.00")),
    ("EXPECTED_OR_PROJECTED_SETTLEMENT",
     lambda t: t.offer_expected_settlement("2.00")),
    ("UNFILLED_OR_RESTING_COMPLEMENT",
     lambda t: t.offer_resting_complement(window=0, slot=0,
                                          price_usd="0.46")),
    ("PENDING_ACCRUED_OR_ESTIMATED_REBATE",
     lambda t: t.offer_pending_rebate("2.00")),
)


@pytest.mark.parametrize("kind,offer", FORBIDDEN_REDUCERS,
                         ids=[k for k, _ in FORBIDDEN_REDUCERS])
def test_a_forbidden_reducer_does_not_move_the_bound(kind, offer):
    """Each of the four, its own test. The bound is identical before and
    after, and the attempt is RECORDED rather than silently ignored."""
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="-2.00")
    for i in range(10):
        tr.open_residual(window=0, slot=i, filled_qty="0.13",
                         fill_price_usd="0.60")
    before = tr.forward_looking_loss_usd(prospective_attempts=1)
    before_realized = tr.realized_loss_usd
    before_resid = tr.unresolved_residual_max_loss_usd()
    rec = offer(tr)
    assert rec["kind"] == kind
    assert rec["moved_the_bound"] is False
    assert rec["bound_before_usd"] == rec["bound_after_usd"]
    assert tr.forward_looking_loss_usd(prospective_attempts=1) == before
    assert tr.realized_loss_usd == before_realized
    assert tr.unresolved_residual_max_loss_usd() == before_resid
    assert any(r["kind"] == kind for r in tr.refused_reducers)


def test_the_window_bound_binds_independently_of_the_cohort_bound():
    """A tracker with ample forward-looking headroom is still stopped by the
    window bound, and vice versa. Neither substitutes for the other."""
    tr = ST.CohortTracker()
    for _ in range(19):
        tr.record_placement(window=0, notional_usd=D("0.078"))
    fwd = tr.forward_looking_report(prospective_attempts=1)
    assert fwd["within_ceiling"] is True            # cohort bound is fine
    g = tr.may_place(window=0, notional_usd=D("0.078"))
    assert g["ok"] is False                          # window bound is not
    assert any("PROJECTED_WINDOW_EXPOSURE" in r for r in g["refusals"])
    # the other direction: window 1 is empty, but the cohort bound bites
    tr2 = ST.CohortTracker()
    tr2.record_confirmed_cash(amount_usd="-4.95")
    g2 = tr2.may_place(window=1, notional_usd=D("0.078"))
    assert g2["ok"] is False
    assert any("FORWARD_LOOKING" in r for r in g2["refusals"])
    assert g2["window_bound_binds_independently"] is True


def test_the_bound_blocks_submission_21_in_a_window():
    tr = ST.CohortTracker()
    for i in range(20):
        g = tr.may_place(window=0, notional_usd=D("0.078"))
        if i < 19:
            assert g["ok"] is True, (i, g["refusals"])
        tr.record_placement(window=0, notional_usd=D("0.078"))
    g = tr.may_place(window=0, notional_usd=D("0.078"))
    assert g["ok"] is False
    assert any("ATTEMPT_21_REFUSED_MAX_20" in r for r in g["refusals"])


def test_eighteen_seventy_two_is_unreachable_by_construction():
    w = ST.effective_worst_case()
    assert w["unrestricted_cohort_worst_case_usd"] == "18.72"
    assert w["unrestricted_is_REACHABLE"] is False
    assert w["effective_cohort_worst_case_usd"] == "5.00"
    assert w["independent_per_window_bound_usd"] == "1.56"
    assert w["both_bind_simultaneously"] is True
    assert w["neither_substitutes_for_the_other"] is True
    assert w["reduced_only_by"] == "a CONFIRMED settled win"
    # drive the cohort as hard as the gates allow and assert the committed
    # worst case never passes $5.00
    tr = ST.CohortTracker()
    slot = 0
    for win in range(SP.WINDOWS):
        for _ in range(SP.MAX_ATTEMPTS_PER_WINDOW):
            if not tr.may_place(window=win, notional_usd=D("0.078"))["ok"]:
                continue
            tr.record_placement(window=win, notional_usd=D("0.078"))
            tr.open_residual(window=win, slot=slot, filled_qty="0.13",
                             fill_price_usd="0.60")
            slot += 1
            assert tr.forward_looking_loss_usd(
                prospective_attempts=0) <= D("5.00")
    total = tr.forward_looking_loss_usd(prospective_attempts=0)
    assert total <= D("5.00") < D("18.72")
    assert SP.SPEC["forward_looking_loss_bound"][
        "unrestricted_worst_case_is_reachable"] is False


def test_the_spec_records_the_forward_looking_bound():
    b = SP.SPEC["forward_looking_loss_bound"]
    assert b["realized_only"] is False
    assert b["prospective_attempt_is_counted_before_submitting"] is True
    assert b["is_a_true_ceiling_not_a_ceiling_plus_one_attempt"] is True
    assert b["ceiling_usd"] == "5.00"
    assert b["per_attempt_max_loss_usd"] == "0.078"
    assert b["per_window_bound_binds_independently"] is True
    assert b["residual_is_unresolved_until"] == \
        "CONFIRMED_SETTLEMENT_CASHFLOW"
    assert b["may_never_reduce_the_bound"] == [
        "mark-to-market value", "expected or projected settlement",
        "an unfilled or resting complement",
        "pending, accrued or estimated rebates"]


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
            "ws_state": dict(snapshot()["ws_state"],
                             both_tokens_streaming=False)},
        "E7_TIMING_AUTHORITY_NOT_WEBSOCKET_STRICT": {
            "ws_state": dict(snapshot()["ws_state"],
                             timing_authority="rest_same_second_"
                                              "reachability")},
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


# ===========================================================================
# THE MOST IMPORTANT TEST: the raw tape must yield the whole estimation
# chain, afterwards and from the files alone. Asserted BEFORE money is
# spent rather than discovered after.
# ===========================================================================

def _ws(ok=True):
    return {"timing_authority": "websocket_strict",
            "both_tokens_streaming": True, "book_unix": 1000.0,
            "first_leg": {"best_bid": "0.50", "best_ask": "0.52",
                          "size_at_bid": "4.0"},
            "complement": {"best_bid": "0.46", "best_ask": "0.48"},
            "recv_monotonic_ns": 12345}


def _record_fixture(rec):
    """Six attempts covering every branch the chain has to survive:
    full fill + complete, partial fill + complete, full fill + residual,
    partial fill + residual, and two no-fills."""
    plan = [
        # (filled, placed, state, comp_px, pair_cost, ttf, ttc, resid, dur)
        ("0.13", "0.13", "COMPLETE", "0.46", "0.96", 3.0, 11.0, None, None),
        ("0.07", "0.13", "COMPLETE", "0.44", "0.94", 5.0, 40.0, None, None),
        ("0.13", "0.13", "RESIDUAL", "0.46", None, 2.0, None, "0.13", 420.0),
        ("0.05", "0.13", "RESIDUAL", "0.46", None, 9.0, None, "0.05", 300.0),
        ("0", "0.13", "NO_FILL", None, None, None, None, None, None),
        ("0", "0.13", "NO_FILL", None, None, None, None, None, None),
    ]
    for i, (fq, pq, state, cpx, pc, ttf, ttc, resid, dur) in enumerate(plan):
        rec.record_attempt(
            window=0, slot=i, market="btc-updown-15m-TEST",
            ws_state=_ws(),
            first_leg={"side": "UP", "price": "0.50", "qty": pq},
            queue={"size_at_our_price": "4.00", "size_ahead_of_us": "3.87"},
            timestamps={"submitted_unix": 1000.0 + i,
                        "acknowledged_unix": 1000.1 + i,
                        "resting_unix": 1000.1 + i,
                        "time_to_first_fill_s": ttf,
                        "time_to_completion_s": ttc},
            fill={"filled_qty": fq,
                  "partial": D(fq) > 0 and D(fq) < D(pq)},
            complement=None if cpx is None else {
                "side": "DOWN", "price": cpx, "qty": fq,
                "submitted_unix": 1000.5 + i,
                "filled_qty": fq if state == "COMPLETE" else "0",
                "no_fill": state != "COMPLETE"},
            outcome={"state": state,
                     "realized_pair_cost_usd_per_share": pc,
                     "residual_shares": resid,
                     "residual_duration_s": dur,
                     "residual_censored": False if dur else None},
            cancellations=([] if state == "COMPLETE" else
                           [{"event": "DEADMAN_CANCEL_ALL",
                             "fill_raced_it": False}]),
            markouts={"book_moved": "DOWN", "h_s": 30,
                      "signed_move_usd": "-0.01",
                      "source": "websocket_tape"})
        rec.record_event(window=0, slot=i, event="SUBMITTED",
                         venue_ts="2026-10-07T00:00:0%dZ" % i,
                         monotonic_ns=1000 + i)
    # venue-reported money: at 0.13 shares the rebate is structurally $0.00
    for i in (0, 1):
        rec.record_venue_money(window=0, slot=i, kind="maker_rebate",
                               amount_usd="0.00",
                               source="venue_rebate_credit",
                               source_identity={"sufficient": True,
                                                "statement_id": "st-%d" % i})
    # settlement: one confirmed, one settled-but-unconfirmed
    rec.record_settlement(window=0, slot=0, market="btc-updown-15m-TEST",
                          settled=True, cash_confirmed=True, cash_usd="0.01",
                          source_identity={"sufficient": True,
                                           "statement_id": "s0"})
    rec.record_settlement(window=0, slot=2, market="btc-updown-15m-TEST",
                          settled=True, cash_confirmed=False, cash_usd=None,
                          source_identity={"sufficient": False})


def test_the_raw_tape_yields_the_whole_estimation_chain(tmp_path):
    rec = R.Recorder(str(tmp_path / "tape"))
    _record_fixture(rec)

    # reconstruct from a FRESH Recorder on the same directory: nothing
    # in-memory is carried over, so this is the files alone
    chain = R.reconstruct_chain(R.Recorder(str(tmp_path / "tape")))
    assert chain["chain_is_computable"] is True, chain["missing_for_the_chain"]

    s1 = chain["step_1_first_leg_maker_fill_rate"]
    assert s1["placements"] == 6 and s1["fills"] == 4
    assert D(s1["rate"]) == D(4) / D(6)
    assert s1["partial_fills"] == 2

    s2 = chain["step_2_conditional_maker_to_maker_completion_rate"]
    assert s2["denominator_is_first_leg_fills"] == 4
    assert s2["completions"] == 2 and s2["non_completions"] == 2
    assert D(s2["rate"]) == D("0.5")
    assert s2["conditioning_on_the_fill_is_part_of_the_definition"] is True

    s3 = chain["step_3_residual"]
    assert D(s3["rate"]) == D("0.5") and s3["count"] == 2
    assert D(s3["total_shares"]) == D("0.18")
    assert sorted(s3["durations_s"]) == [300.0, 420.0]
    # worst-case residual loss = (0.13 + 0.05) x 0.50
    assert D(s3["worst_case_loss_usd"]) == D("0.0900")

    s4 = chain["step_4_pair_cost"]
    assert s4["n"] == 2
    assert sorted(s4["values_usd_per_share"]) == ["0.94", "0.96"]
    assert s4["all_within_ceiling_096"] is True

    s5 = chain["step_5_actual_cashflows"]
    assert s5["all_venue_money_rows_are_unmodelled"] is True
    assert s5["confirmed_realized_rows"] == 1
    assert D(s5["confirmed_cash_usd"]) == D("0.01")
    assert s5["unconfirmed_rows"] == 1

    assert sorted(chain["timings"]["time_to_first_fill_s"]) == \
        [2.0, 3.0, 5.0, 9.0]
    assert sorted(chain["timings"]["time_to_completion_s"]) == [11.0, 40.0]


def test_the_chain_survives_a_truncated_cohort(tmp_path):
    """A risk-truncated run must still yield the chain from its tape."""
    rec = R.Recorder(str(tmp_path / "tape"))
    _record_fixture(rec)
    tr = ST.CohortTracker()
    tr.record_confirmed_cash(amount_usd="-5.00")
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_CONTINUE
    assert v["truncation_cause"] == ST.TRUNCATION_RISK_LIMIT
    assert v["raw_tape_must_still_yield_the_estimation_chain"] is True
    chain = R.reconstruct_chain(R.Recorder(str(tmp_path / "tape")))
    assert chain["chain_is_computable"] is True
    assert chain["step_1_first_leg_maker_fill_rate"]["placements"] == 6


def test_the_chain_reports_what_is_missing_rather_than_guessing(tmp_path):
    rec = R.Recorder(str(tmp_path / "tape"))
    rec.record_attempt(
        window=0, slot=0, market="m", ws_state=_ws(),
        first_leg={"side": "UP", "price": "0.50", "qty": "0.13"},
        queue={}, timestamps={},
        fill={"filled_qty": "0.13"}, complement=None,
        outcome={"state": "RESIDUAL"}, cancellations=[], markouts={})
    chain = R.reconstruct_chain(rec)
    assert chain["chain_is_computable"] is False
    assert chain["missing_for_the_chain"]


def test_every_recorded_row_carries_enough_provenance_to_be_reread(tmp_path):
    rec = R.Recorder(str(tmp_path / "tape"))
    _record_fixture(rec)
    for name in (R.ATTEMPTS, R.EVENTS, R.VENUE_MONEY, R.SETTLEMENT):
        rows = R.Recorder(str(tmp_path / "tape")).read(name)
        assert rows
        for row in rows:
            assert row["schema_version"] == R.SCHEMA_VERSION
            assert row["spec_fingerprint"] == SP.E1_FINGERPRINT
            assert row["fee_schedule_fingerprint"] == \
                SP.FEE_SCHEDULE_FINGERPRINT
            assert row["venue"] == "polymarket_us"
            assert row["timing_authority"] == "websocket_strict"
            assert row["written_utc"] and row["written_unix"]


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
    # no network client is imported, and every transport_factory in this
    # file is the FakeTransport
    assert "url" + "lib" not in src
    assert "import " + "socket" not in src
    assert "import " + "requests" not in src
    lines = [ln for ln in src.splitlines() if "transport_factory" + "=" in ln
             and "splitlines" not in ln and "def " not in ln]
    assert lines, "no submissions are exercised at all"
    for ln in lines:
        assert "Fake" in ln or "lambda: t" in ln, ln


def test_the_manifest_exists_has_the_header_and_the_eight_items():
    path = os.path.join(HERE, "E1_AUTHORIZATION_MANIFEST.md")
    text = open(path, encoding="utf8").read()
    assert "NOT AUTHORIZED" in text[:800]
    for i in range(1, 9):
        assert "## %d." % i in text, i
    assert SP.E1_FINGERPRINT in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
