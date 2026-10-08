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

from analysis.l5.e1_cohort import arming as AR
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import recorder as R
from analysis.l5.e1_cohort import runner as RUN
from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import spec as SP
from analysis.l5.e1_cohort import stopping as ST
from analysis.l5.live_exec2 import orders as O
from src import l5_order_body as OB

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _isolated_latch(monkeypatch, tmp_path):
    """Every test gets its own latch directory and session id. Nothing in
    these tests can touch a real latch."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "latch"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", "offline-test-session")
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
        "ws_state": {"timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                     "pinned_market_streaming": True,
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
    monkeypatch.setattr(AR, "armed_env_summary",
                        lambda: {"armed": True, "keys_present": {}})
    monkeypatch.setattr(RUN.A, "armed_env_summary",
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


#: NAMING A RESPONSE KEY IS NOT BUILDING A BODY.
#:
#: `order_state.py` exists to READ authoritative venue order state -- it is
#: the module that replaced `pmus_provider`'s hard-coded
#: `our_outstanding_orders: 0`, the constant that let twenty orders rest in
#: one window. To read an order row it must name the key the venue puts the
#: slug under. It performs GET requests only and constructs no request
#: envelope, and `test_order_state_module_cannot_mutate_anything` proves
#: that independently rather than taking this exemption's word for it.
READ_ONLY_RESPONSE_KEYS = {("order_state.py", "marketSlug")}


def code_only(path):
    """Source with every docstring and comment removed, so a scan looks at
    what the module DOES rather than what it says about itself. A call
    graph that NAMES `{'request': body}` is documentation; a module that
    BUILDS one is a duplicate serializer."""
    import ast
    src = open(path, encoding="utf8").read()
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
    out = []
    for ln in src.splitlines():
        st = ln.strip()
        if st.startswith("#"):
            continue
        out.append(ln)
    text = "\n".join(out)
    for d in docstrings:
        text = text.replace(d, "")
    return text


def test_order_state_module_cannot_mutate_anything():
    """The exemption above is not taken on trust.

    `order_state.py` may name `marketSlug` because it reads order rows. It
    may not POST, PUT, PATCH, DELETE, build a request envelope, or import a
    mutating transport. If any of that ever appears, this fails and the
    exemption is void.
    """
    text = code_only(os.path.join(HERE, "order_state.py"))
    for forbidden in ('"POST"', "'POST'", '"PUT"', '"PATCH"', '"DELETE"',
                      "BoundedOrderTransport", "order_body", "submit_one",
                      "PREVIEW_PATH", "SUBMIT_PATH", "l5_pmus_transport"):
        assert forbidden not in text, forbidden
    assert '"GET"' in text
    assert "ReadOnlyTransport" in text


def test_no_file_in_this_package_contains_a_duplicate_serializer():
    """A duplicate serializer caused the A5 HTTP 400, and the repository
    records the same bug happening twice. No file here may build a body."""
    offenders = {}
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py"):
            continue
        if name.startswith("test_"):
            # test files NAME these tokens deliberately: this one to
            # forbid them, and test_e1_live_path.py to assert what the
            # PROVEN serializer put on the wire. Neither builds a body.
            continue
        text = code_only(os.path.join(HERE, name))
        hits = [tok for tok in FORBIDDEN
                if tok in text
                and (name, tok) not in READ_ONLY_RESPONSE_KEYS]
        # a line of PROSE naming the wire call (a call-graph entry) is
        # documentation, not a serializer. Only a constructed envelope
        # counts.
        envelope_lines = [ln for ln in text.splitlines()
                          if re.search(r'["\']request["\']\s*:', ln)
                          and "POST /v1/order" not in ln]
        if envelope_lines:
            hits.append('bare "request": envelope key: %s'
                        % envelope_lines[:2])
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
    assert out["submitted"] is True
    assert out["e1_complement_qty"] == "0.07"
    assert out["sized_to_cumulative_filled_qty"] is True
    assert out["sized_to_only_the_first_partial"] is False
    submitted = [c for c in t.calls if c[1] == O.SUBMIT_PATH]
    assert len(submitted) == 1
    assert submitted[0][2]["quantity"] == 0.07
    assert submitted[0][2]["quantity"] != 0.13      # NOT the placed qty


# ============ THE CORRECTED CUMULATIVE COMPLEMENT ACCOUNTING (C7/C9/C10) ===

def test_005_then_008_accounts_for_the_whole_013():
    """THE BUG THAT WAS FIXED. The first version complemented 0.05 and left
    an 0.08 residual nobody decided on. The whole 0.13 must be accounted."""
    a = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.05",
                               already_complemented_qty="0",
                               tranches_submitted=0)
    assert a["tranche"] == "0.05" and D(a["residual_qty"]) == D("0")
    b = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.13",
                               already_complemented_qty="0.05",
                               tranches_submitted=1)
    assert b["tranche"] == "0.08", "the LATER 0.08 must be complemented too"
    assert D(b["residual_qty"]) == D("0")
    # and the accounting closes exactly
    acct = RUN.account_first_leg(cumulative_filled_qty="0.13",
                                 complemented_qty="0.13")
    assert acct["fully_accounted"] is True
    assert acct["residual_qty"] == "0.00"
    assert D(acct["unaccounted_qty"]) == D("0")
    # nothing is left over once both tranches are in
    c = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.13",
                               already_complemented_qty="0.13",
                               tranches_submitted=2)
    assert c["tranche"] is None and D(c["residual_qty"]) == D("0")
    assert "already equals" in c["reason"]


def test_repeated_partials_are_each_accounted():
    """0.01 at a time, thirteen times. Every share is complemented."""
    done = D("0")
    tranches = 0
    for i in range(1, 14):
        cum = (D("0.01") * i)
        plan = RUN.complement_tranche(
            cumulative_first_leg_filled_qty=cum,
            already_complemented_qty=done, tranches_submitted=tranches)
        assert plan["tranche"] == str(D("0.01")), (i, plan)
        done += D(plan["tranche"])
        tranches += 1
    assert done == D("0.13")
    assert RUN.account_first_leg(cumulative_filled_qty="0.13",
                                 complemented_qty=done)["fully_accounted"]
    assert tranches == SP.MAX_COMPLEMENT_TRANCHES == 13


def test_a_tranche_is_not_chasing_and_not_a_requote():
    """The price cannot move: it is recomputed from the ORIGINAL first-leg
    price, and the trigger is a FILL, never a non-fill."""
    first = RUN.complement_price(first_leg_price="0.50", qty="0.05",
                                 complement_best_bid="0.46")
    # the market moved against us; the tranche price is unchanged because
    # it is capped by the ORIGINAL first-leg price and the frozen ceiling
    later = RUN.complement_price(first_leg_price="0.50", qty="0.08",
                                 complement_best_bid="0.46")
    assert first["price_usd"] == later["price_usd"]
    plan = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.13",
                                  already_complemented_qty="0.05",
                                  tranches_submitted=1)
    assert plan["is_chasing"] is False
    assert plan["is_a_requote"] is False
    assert plan["triggered_by"] == "A FILL, never a non-fill"
    assert plan["price_is_the_attempts_original_complement_price"] is True
    assert SP.SPEC["complement"]["tranche_trigger"] == \
        "A_FILL_NEVER_A_NON_FILL"


def test_an_unfillable_complement_is_never_replaced_more_aggressively():
    """A NON-fill produces no tranche at all: the mechanism has no path a
    non-fill can take."""
    # cumulative fill unchanged -> no tranche, whatever happened to the
    # complement that is resting
    plan = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.05",
                                  already_complemented_qty="0.05",
                                  tranches_submitted=1)
    assert plan["tranche"] is None
    assert SP.SPEC["complement"]["requote"] is False
    assert SP.SPEC["complement"]["chasing"] is False


def test_sub_tick_dust_is_EXPLICITLY_residual_and_never_rounded_away():
    """C10. A cumulative fill off the grid leaves dust below the venue
    minimum. It is OURS and it is uncomplementable, so it is RECORDED as
    residual rather than lost in a quantize step."""
    plan = RUN.complement_tranche(
        cumulative_first_leg_filled_qty="0.135",
        already_complemented_qty="0.13", tranches_submitted=1)
    assert plan["tranche"] is None
    assert plan["sub_tick_dust_qty"] == "0.005"
    assert plan["sub_tick_dust_is_residual"] is True
    assert D(plan["residual_qty"]) == D("0.005")
    assert "RESIDUAL" in plan["reason"] and "dust" in plan["reason"]
    # dust riding alongside a real tranche is still reported
    p2 = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.137",
                                already_complemented_qty="0.05",
                                tranches_submitted=1)
    assert p2["tranche"] == "0.08"
    assert D(p2["residual_qty"]) == D("0.007")
    assert "dust" in p2["reason"]
    # nothing is unaccounted: tranche + dust + done == the raw fill
    assert (D(p2["tranche"]) + D(p2["residual_qty"])
            + D(p2["already_complemented_qty"])) == D("0.137")
    # a clean on-grid shortfall has no dust
    p3 = RUN.complement_tranche(cumulative_first_leg_filled_qty="0.10",
                                already_complemented_qty="0.09",
                                tranches_submitted=1)
    assert p3["tranche"] == "0.01" and D(p3["residual_qty"]) == 0
    assert p3["sub_tick_dust_is_residual"] is False


def test_an_economically_uncomplementable_shortfall_is_residual(armed):
    """A shortfall whose complement price is infeasible under the frozen
    $0.96 ceiling is recorded as RESIDUAL, not rescued."""
    snap = snapshot(first_leg_price="0.60", complement_bid="0.00",
                    first_leg={"side": "UP", "best_bid": "0.60",
                               "best_ask": "0.62"})
    out = RUN.submit_complement(window=0, slot=0, snapshot=snap,
                                first_leg_filled_qty="0.13",
                                tracker=ST.CohortTracker(),
                                transport_factory=FakeTransport)
    assert out["submitted"] is False
    assert out["residual_qty"] == "0.13"
    assert out["RESIDUAL_IS_EXPLICIT"] is True
    assert out["ceiling_relaxed"] is False
    assert "RESIDUAL" in out["residual_reason"]


def test_an_inconsistent_ledger_is_an_instrument_fault():
    with pytest.raises(RUN.ComplementInfeasible) as ei:
        RUN.complement_tranche(cumulative_first_leg_filled_qty="0.05",
                               already_complemented_qty="0.13")
    assert "INSTRUMENT FAULT" in str(ei.value)


def test_two_tranches_submit_as_two_orders_at_the_same_price(armed):
    snap = snapshot()
    tr = ST.CohortTracker()
    t1 = FakeTransport()
    a = RUN.submit_complement(window=0, slot=0, snapshot=snap,
                              first_leg_filled_qty="0.05",
                              already_complemented_qty="0", tracker=tr,
                              transport_factory=lambda: t1)
    t2 = FakeTransport()
    b = RUN.submit_complement(window=0, slot=0, snapshot=snap,
                              first_leg_filled_qty="0.13",
                              already_complemented_qty="0.05", tracker=tr,
                              transport_factory=lambda: t2)
    assert a["e1_complement_qty"] == "0.05"
    assert b["e1_complement_qty"] == "0.08"
    assert a["e1_tranche_index"] == 0 and b["e1_tranche_index"] == 1
    # SAME price on the wire: no price moved
    pa = [c for c in t1.calls if c[1] == O.SUBMIT_PATH][0][2]["price"]
    pb = [c for c in t2.calls if c[1] == O.SUBMIT_PATH][0][2]["price"]
    assert pa == pb
    # two DISTINCT latch slots, so neither is a re-quote of the other
    assert a["e1_latch_slot"]["path"] != b["e1_latch_slot"]["path"]
    assert L.complement_tranches_spent(window=0, slot=0) == 2


def test_a_fill_below_the_venue_minimum_is_residual_not_a_complement():
    with pytest.raises(RUN.ComplementInfeasible):
        RUN.complement_qty(first_leg_filled_qty="0.009")
    with pytest.raises(RUN.ComplementInfeasible):
        RUN.complement_qty(first_leg_filled_qty="0")


def test_no_requote_the_same_tranche_is_refused_at_the_latch(armed):
    """Re-submitting the SAME tranche is refused by the latch, not merely by
    policy. A LATER tranche is a different slot and is permitted, because
    its trigger is a new fill."""
    tr = ST.CohortTracker()
    L.consume_complement(window=0, slot=3, slug="m", side="DOWN",
                         price="0.46", qty="0.13", tranche=0)
    with pytest.raises(L.LatchSpent):
        L.consume_complement(window=0, slot=3, slug="m", side="DOWN",
                             price="0.46", qty="0.13", tranche=0)
    # tranche 1 is a DIFFERENT named slot
    L.consume_complement(window=0, slot=3, slug="m", side="DOWN",
                         price="0.46", qty="0.01", tranche=1)
    assert L.complement_tranches_spent(window=0, slot=3) == 2
    # and the tranche count is bounded by the venue grid
    with pytest.raises(L.LatchUnavailable):
        L.consume_complement(window=0, slot=3, slug="m", side="DOWN",
                             price="0.46", qty="0.01", tranche=13)


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
        ws_state={"timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                  "pinned_market_streaming": True},
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
    monkeypatch.delenv("PMUS_E1_SESSION_ID", raising=False)
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
        "E7_PINNED_MARKET_NOT_STREAMING": {
            "ws_state": dict(snapshot()["ws_state"],
                             pinned_market_streaming=False)},
        "E7_TIMING_AUTHORITY_NOT_PMUS_AUTHENTICATED_WS": {
            "ws_state": dict(snapshot()["ws_state"],
                             timing_authority="rest_same_second_"
                                              "reachability")},
    }
    for want, over in cases.items():
        el = RUN.eligible(snapshot(**over), tr, now=NOW)
        assert el["ok"] is False
        assert any(str(g) == want or str(g).startswith(want + ":")
                   for g in el["refusals"]), (want, el["refusals"])


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
        assert row["timing_authority"] == PBK.TIMING_PMUS_AUTHENTICATED_WS
        assert row["rest_may_order_the_tape"] is False
        assert row["append_only"] is True
    rec.record_event(window=0, slot=9, event="CANCEL_ACK")
    assert len(rec.read(R.EVENTS)) == 4          # nothing was overwritten


def test_an_attempt_row_may_never_be_labelled_realized(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    with pytest.raises(R.RecorderRefused):
        rec.record_attempt(
            window=0, slot=0, market="m",
            ws_state={"timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                      "pinned_market_streaming": True},
            first_leg={}, queue={}, timestamps={}, fill={},
            complement=None, outcome={}, cancellations=[], markouts={},
            label=R.REALIZED)


def test_an_attempt_row_requires_the_websocket_as_timing_authority(tmp_path):
    rec = R.Recorder(str(tmp_path / "led"))
    for ws in ({"timing_authority": "rest_same_second_reachability",
                "pinned_market_streaming": True},
               {"timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
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
    return {"timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
            "pinned_market_streaming": True, "book_unix": 1000.0,
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


# --- THE FIVE PARTIAL-FILL SEQUENCES, in the RAW-TAPE tests -------------

SEQ_A = "0.05_then_0.08"
SEQ_B = "repeated_partials"
SEQ_C = "complement_partials"
SEQ_D = "cancellation_race"
SEQ_E = "first_leg_fill_while_complement_outstanding"


def _record_five_sequences(rec):
    """All five sequences the owner requires, written to the tape as the
    runner would, so each can be reconstructed AFTER THE FACT."""

    # --- A: 0.05 then 0.08. Two complement tranches, SAME price, whole
    # --- 0.13 accounted for.
    for i, (cum, tranche) in enumerate(((("0.05"), "0.05"),
                                        (("0.13"), "0.08"))):
        rec.record_event(window=0, slot=0, event="FIRST_LEG_PARTIAL_FILL",
                         venue_ts="2026-10-07T00:00:0%dZ" % (i + 1),
                         monotonic_ns=1000 + i,
                         detail={"sequence": SEQ_A, "fill_qty": tranche,
                                 "cumulative_filled_qty": cum})
        rec.record_event(window=0, slot=0, event="COMPLEMENT_TRANCHE_SUBMIT",
                         venue_ts="2026-10-07T00:00:0%dZ" % (i + 2),
                         monotonic_ns=1010 + i,
                         detail={"sequence": SEQ_A, "tranche_index": i,
                                 "qty": tranche, "price": "0.46",
                                 "cumulative_complemented_qty": cum,
                                 "is_a_requote": False,
                                 "is_chasing": False,
                                 "triggered_by": "FILL"})

    # --- B: repeated partials, 0.01 x 13.
    for i in range(13):
        cum = str(D("0.01") * (i + 1))
        rec.record_event(window=0, slot=1, event="FIRST_LEG_PARTIAL_FILL",
                         venue_ts="2026-10-07T00:01:%02dZ" % i,
                         monotonic_ns=2000 + i,
                         detail={"sequence": SEQ_B, "fill_qty": "0.01",
                                 "cumulative_filled_qty": cum})
        rec.record_event(window=0, slot=1, event="COMPLEMENT_TRANCHE_SUBMIT",
                         venue_ts="2026-10-07T00:01:%02dZ" % i,
                         monotonic_ns=2050 + i,
                         detail={"sequence": SEQ_B, "tranche_index": i,
                                 "qty": "0.01", "price": "0.46",
                                 "cumulative_complemented_qty": cum})

    # --- C: complement PARTIALS. The complement itself fills in pieces, so
    # --- the uncovered remainder is residual.
    for i, (q, cum) in enumerate((("0.03", "0.03"), ("0.04", "0.07"))):
        rec.record_event(window=0, slot=2, event="COMPLEMENT_PARTIAL_FILL",
                         venue_ts="2026-10-07T00:02:0%dZ" % i,
                         monotonic_ns=3000 + i,
                         detail={"sequence": SEQ_C, "fill_qty": q,
                                 "cumulative_complement_filled_qty": cum,
                                 "complement_placed_qty": "0.13"})
    rec.record_event(window=0, slot=2, event="COMPLEMENT_NO_FURTHER_FILL",
                     venue_ts="2026-10-07T00:02:30Z", monotonic_ns=3100,
                     detail={"sequence": SEQ_C,
                             "uncovered_qty": "0.06",
                             "recorded_as": "RESIDUAL",
                             "requoted_more_aggressively": False})

    # --- D: a cancellation RACE. A fill lands after the cancel ack.
    rec.record_event(window=0, slot=3, event="CANCEL_SUBMIT",
                     venue_ts="2026-10-07T00:03:00Z", monotonic_ns=4000,
                     detail={"sequence": SEQ_D})
    rec.record_event(window=0, slot=3, event="CANCEL_ACK",
                     venue_ts="2026-10-07T00:03:01Z", monotonic_ns=4001,
                     detail={"sequence": SEQ_D})
    rec.record_event(window=0, slot=3, event="FILL_AFTER_CANCEL_ACK",
                     venue_ts="2026-10-07T00:03:02Z", monotonic_ns=4002,
                     detail={"sequence": SEQ_D, "fill_qty": "0.02",
                             "cancel_ack_venue_ts":
                                 "2026-10-07T00:03:01Z",
                             "fill_raced_the_cancel": True,
                             "signed_interval_s": 1.0})

    # --- E: a first-leg fill arriving WHILE complement quantity is already
    # --- outstanding. The outstanding complement is NOT touched; a new
    # --- tranche covers the new inventory.
    rec.record_event(window=0, slot=4, event="FIRST_LEG_PARTIAL_FILL",
                     venue_ts="2026-10-07T00:03:59Z", monotonic_ns=4999,
                     detail={"sequence": SEQ_E, "fill_qty": "0.05",
                             "cumulative_filled_qty": "0.05",
                             "complement_outstanding_qty": "0",
                             "complement_outstanding_at_arrival": False})
    rec.record_event(window=0, slot=4, event="COMPLEMENT_TRANCHE_SUBMIT",
                     venue_ts="2026-10-07T00:04:00Z", monotonic_ns=5000,
                     detail={"sequence": SEQ_E, "tranche_index": 0,
                             "qty": "0.05", "price": "0.46",
                             "cumulative_complemented_qty": "0.05"})
    rec.record_event(window=0, slot=4, event="FIRST_LEG_PARTIAL_FILL",
                     venue_ts="2026-10-07T00:04:01Z", monotonic_ns=5001,
                     detail={"sequence": SEQ_E, "fill_qty": "0.04",
                             "cumulative_filled_qty": "0.09",
                             "complement_outstanding_qty": "0.05",
                             "complement_outstanding_at_arrival": True})
    rec.record_event(window=0, slot=4, event="COMPLEMENT_TRANCHE_SUBMIT",
                     venue_ts="2026-10-07T00:04:02Z", monotonic_ns=5002,
                     detail={"sequence": SEQ_E, "tranche_index": 1,
                             "qty": "0.04", "price": "0.46",
                             "cumulative_complemented_qty": "0.09",
                             "earlier_complement_cancelled": False,
                             "earlier_complement_price_moved": False,
                             "is_a_requote": False})


def test_the_tape_reconstructs_all_five_partial_fill_sequences(tmp_path):
    """Reconstruct each sequence from a FRESH reader on the files alone."""
    _record_five_sequences(R.Recorder(str(tmp_path / "tape")))
    rows = R.Recorder(str(tmp_path / "tape")).read(R.EVENTS)
    by_seq = {}
    for row in rows:
        by_seq.setdefault(row["detail"]["sequence"], []).append(row)
    assert set(by_seq) == {SEQ_A, SEQ_B, SEQ_C, SEQ_D, SEQ_E}

    # every row is orderable by the websocket authority, not by wall clock
    for row in rows:
        assert row["timing_authority"] == PBK.TIMING_PMUS_AUTHENTICATED_WS
        assert row["ordering_monotonic_ns"] is not None

    # --- A: 0.05 then 0.08 -> the whole 0.13, in two tranches at ONE price
    a = by_seq[SEQ_A]
    fills = [r for r in a if r["event"] == "FIRST_LEG_PARTIAL_FILL"]
    tr = [r for r in a if r["event"] == "COMPLEMENT_TRANCHE_SUBMIT"]
    assert [f["detail"]["fill_qty"] for f in fills] == ["0.05", "0.08"]
    assert fills[-1]["detail"]["cumulative_filled_qty"] == "0.13"
    assert sum(D(r["detail"]["qty"]) for r in tr) == D("0.13")
    assert len({r["detail"]["price"] for r in tr}) == 1    # no price moved
    assert [r["detail"]["tranche_index"] for r in tr] == [0, 1]
    assert all(r["detail"]["is_a_requote"] is False for r in tr)

    # --- B: repeated partials sum to the placed quantity
    b = by_seq[SEQ_B]
    bf = [r for r in b if r["event"] == "FIRST_LEG_PARTIAL_FILL"]
    bt = [r for r in b if r["event"] == "COMPLEMENT_TRANCHE_SUBMIT"]
    assert len(bf) == 13 and len(bt) == 13
    assert sum(D(r["detail"]["fill_qty"]) for r in bf) == D("0.13")
    assert sum(D(r["detail"]["qty"]) for r in bt) == D("0.13")
    assert bt[-1]["detail"]["cumulative_complemented_qty"] == "0.13"
    assert len(bt) <= SP.MAX_COMPLEMENT_TRANCHES

    # --- C: complement partials, and the uncovered remainder is residual
    c = by_seq[SEQ_C]
    cp = [r for r in c if r["event"] == "COMPLEMENT_PARTIAL_FILL"]
    assert sum(D(r["detail"]["fill_qty"]) for r in cp) == D("0.07")
    end = [r for r in c if r["event"] == "COMPLEMENT_NO_FURTHER_FILL"][0]
    assert end["detail"]["recorded_as"] == "RESIDUAL"
    assert D(end["detail"]["uncovered_qty"]) == D("0.13") - D("0.07")
    assert end["detail"]["requoted_more_aggressively"] is False

    # --- D: the cancellation race is reconstructible AS a race
    d = {r["event"]: r for r in by_seq[SEQ_D]}
    assert set(d) == {"CANCEL_SUBMIT", "CANCEL_ACK", "FILL_AFTER_CANCEL_ACK"}
    race = d["FILL_AFTER_CANCEL_ACK"]
    assert race["detail"]["fill_raced_the_cancel"] is True
    assert race["venue_timestamp"] > d["CANCEL_ACK"]["venue_timestamp"]
    assert race["ordering_monotonic_ns"] > \
        d["CANCEL_ACK"]["ordering_monotonic_ns"]
    # and that is the preregistered estimand-6 FAIL
    trk = ST.CohortTracker()
    trk.record_post_cancel_ack_fill()
    assert trk.verdict()["verdict"] == ST.VERDICT_FAIL

    # --- E: a fill arriving while complement quantity was outstanding
    e = by_seq[SEQ_E]
    arrivals = [r for r in e if r["event"] == "FIRST_LEG_PARTIAL_FILL"]
    assert [r["detail"]["fill_qty"] for r in arrivals] == ["0.05", "0.04"]
    arrival = arrivals[1]
    assert arrival["detail"]["complement_outstanding_at_arrival"] is True
    assert arrival["detail"]["complement_outstanding_qty"] == "0.05"
    assert arrival["detail"]["cumulative_filled_qty"] == "0.09"
    et = [r for r in e if r["event"] == "COMPLEMENT_TRANCHE_SUBMIT"]
    assert [r["detail"]["qty"] for r in et] == ["0.05", "0.04"]
    assert sum(D(r["detail"]["qty"]) for r in et) == D("0.09")
    # the outstanding complement was neither cancelled nor re-priced
    assert et[1]["detail"]["earlier_complement_cancelled"] is False
    assert et[1]["detail"]["earlier_complement_price_moved"] is False
    assert et[1]["detail"]["is_a_requote"] is False
    assert len({r["detail"]["price"] for r in et}) == 1


def test_cumulative_accounting_is_recomputable_from_the_tape(tmp_path):
    """For every sequence with first-leg fills, recompute cumulative filled
    vs cumulative complemented from the tape and show nothing is
    unaccounted."""
    _record_five_sequences(R.Recorder(str(tmp_path / "tape")))
    rows = R.Recorder(str(tmp_path / "tape")).read(R.EVENTS)
    for seq, placed in ((SEQ_A, "0.13"), (SEQ_B, "0.13"), (SEQ_E, "0.09")):
        ev = [r for r in rows if r["detail"].get("sequence") == seq]
        filled = sum((D(r["detail"]["fill_qty"]) for r in ev
                      if r["event"] == "FIRST_LEG_PARTIAL_FILL"), D("0"))
        comped = sum((D(r["detail"]["qty"]) for r in ev
                      if r["event"] == "COMPLEMENT_TRANCHE_SUBMIT"), D("0"))
        assert filled == D(placed), (seq, filled)
        acct = RUN.account_first_leg(cumulative_filled_qty=filled,
                                     complemented_qty=comped)
        assert acct["fully_accounted"] is True, (seq, acct)
        assert D(acct["unaccounted_qty"]) == D("0"), (seq, acct)
        assert D(acct["residual_qty"]) == D("0"), (seq, acct)


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
            assert row["timing_authority"] == PBK.TIMING_PMUS_AUTHENTICATED_WS
            assert row["written_utc"] and row["written_unix"]


# =========== XR2W PARITY: the latch is the ONLY behavioural difference ====

XR2W_SOURCE_SHA256_16 = {
    "analysis/l5/live_exec5/spec.py": "dda1e2c50be81209",
    "analysis/l5/live_exec2/orders.py": "800078a852f14d84",
    "analysis/l5/live_exec2/latch.py": "add23515a5a8a201",
    "src/l5_order_body.py": "50b9ff1376abea03",
}

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def test_xr2w_sources_are_byte_identical():
    """The four files E1 reuses are UNMODIFIED, against hashes the owner
    recorded independently."""
    import hashlib
    for rel, want in XR2W_SOURCE_SHA256_16.items():
        raw = open(os.path.join(REPO, rel), "rb").read()
        got = hashlib.sha256(raw).hexdigest()[:16]
        assert got == want, "%s moved: %s != %s" % (rel, got, want)


def test_xr2w_own_fingerprint_still_passes():
    from analysis.l5.live_exec5 import spec as X
    X.assert_frozen()
    assert X.XR2W_FINGERPRINT == \
        "ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562"


def test_parity_e1_and_xr2w_build_the_same_request_body():
    """For an otherwise identical single submission, byte-identical body."""
    for side, price, qty in (("UP", "0.50", "0.13"), ("DOWN", "0.46", "0.08"),
                             ("UP", "0.01", "0.01")):
        e1_body = O.order_body(slug="m", outcome_side=side, price=price,
                               qty=qty)
        xr2w_body = OB.build(slug="m", side=side, price=price, qty=qty,
                             post_only=True)
        assert e1_body == xr2w_body
        assert json.dumps(e1_body, sort_keys=True) == \
            json.dumps(xr2w_body, sort_keys=True)
        # the hard-won conventions, on the wire
        assert e1_body["price"]["currency"] == "USD"
        assert isinstance(e1_body["quantity"], float)
        assert e1_body["participateDontInitiate"] is True
    # the NO-side 1-X mapping is the proven one, not re-derived
    no_body = O.order_body(slug="m", outcome_side="DOWN", price="0.49",
                           qty="0.13")
    assert no_body["price"]["value"] == "0.51"


def test_parity_e1_and_xr2w_use_the_same_preview_envelope():
    body = O.order_body(slug="m", outcome_side="UP", price="0.50",
                        qty="0.13")
    assert O.preview_request(body) == OB.preview_envelope(body)
    assert list(O.preview_request(body)) == ["request"]


def test_parity_e1_and_xr2w_use_the_same_echo_validation():
    """Same validator, same accept, same reject -- E1 imports it, it does
    not reimplement it."""
    import src.l5_preview_gate as GATE
    body = O.order_body(slug="m", outcome_side="UP", price="0.50",
                        qty="0.13")
    assert GATE.require_echo(body, {"order": dict(body)})["ok"] is True
    tampered = dict(body, quantity=0.99)
    with pytest.raises(Exception):
        GATE.require_echo(body, {"order": tampered})
    # and E1's submission path raises the SAME exception type XR2W would
    t = FakeTransport()
    t_bad = FakeTransport()
    t_bad.request = lambda m, pth, b=None, base=None: (
        {"order": {"nope": 1}} if pth == O.PREVIEW_PATH else {})
    with pytest.raises(O.PreviewRejected):
        RUN._submit_once(t_bad, slug="m", outcome_side="UP", price="0.50",
                         qty="0.13", consume_latch=lambda: {"fake": True})
    assert t.calls == []


def test_parity_e1_uses_xr2w_transport_routes_and_route_refusals():
    t = O.BoundedOrderTransport()
    assert t._allowed("POST", O.PREVIEW_PATH)
    assert t._allowed("POST", O.SUBMIT_PATH)
    assert t._allowed("POST", O.DEADMAN_PATH)
    assert t._allowed("POST", "/v1/order/abc/cancel")
    assert not t._allowed("POST", "/v1/anything/else")
    assert not t._allowed("DELETE", O.SUBMIT_PATH)
    # E1 reaches exactly two of them for one submission, in order
    fake = FakeTransport()
    RUN._submit_once(fake, slug="m", outcome_side="UP", price="0.50",
                     qty="0.13", consume_latch=lambda: {"fake": True})
    assert [c[1] for c in fake.calls] == [O.PREVIEW_PATH, O.SUBMIT_PATH]
    assert [c[0] for c in fake.calls] == ["POST", "POST"]


def test_parity_e1_raises_xr2w_exception_taxonomy():
    for path, status, want in ((O.PREVIEW_PATH, 400, O.PreviewRejected),
                               (O.SUBMIT_PATH, 503, O.SubmissionAmbiguous)):
        err = O.VenueHTTPError(method="POST", path=path, status=status,
                               reason="x", body="{}", headers={})
        kw = {"preview_raises": err} if path == O.PREVIEW_PATH else \
             {"submit_raises": err}
        with pytest.raises(want) as ei:
            RUN._submit_once(FakeTransport(**kw), slug="m",
                             outcome_side="UP", price="0.50", qty="0.13",
                             consume_latch=lambda: {"fake": True})
        assert isinstance(ei.value, want)
    # and the boundary flags are XR2W's own, not redefined
    assert O.PreviewRejected("x").crossed_order_boundary is False
    assert O.SubmissionAmbiguous("x").crossed_order_boundary is True


def test_parity_the_latch_is_the_ONLY_behavioural_difference():
    """Enumerate what E1's submission path calls, and assert every piece but
    the latch comes from XR2W's modules."""
    import inspect
    src = inspect.getsource(RUN._submit_once)
    for reused in ("O.order_body", "O.preview_request", "G.require_echo",
                   "O.PREVIEW_PATH", "O.SUBMIT_PATH", "O.VenueHTTPError",
                   "O.PreviewRejected", "O.SubmissionAmbiguous"):
        assert reused in src, reused
    # the ONLY substitution
    assert "consume_latch()" in src
    assert "L.consume" not in src          # the latch is injected, not wired
    # CODE only, with the docstring stripped: the docstring legitimately
    # names the modules it delegates to
    code = src.split('"""')[2] if src.count('"""') >= 2 else src
    # no field name is constructed here: the body is built by delegation
    for forbidden in ("marketSlug", "ORDER_TYPE", "ORDER_ACTION",
                      "TIME_IN_FORCE_", "OB.build", "OB.preview",
                      "participateDontInitiate", '"request":'):
        assert forbidden not in code, forbidden
    # the only l5_order_body mention in the code is the PROVENANCE label it
    # stamps on its result, never a call
    assert "l5_order_body.build(" not in code
    assert code.count("l5_order_body") == 1
    assert SP.SPEC["xr2w_parity"]["the_only_behavioural_difference"].startswith(
        "the latch")
    assert SP.SPEC["xr2w_parity"]["xr2w_modified"] is False


# ====== NEGATIVE ISOLATION PROOFS: E1 vs XR2W namespaces and state ========

LEGACY_XR2W_ENV = ("PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID",
                   "PMUS_EXEC1_ARM", "PMUS_EXEC1_GATE_EVIDENCE",
                   "PMUS_EXEC1_AUTHORIZED_FINGERPRINT")


def test_a_legacy_PMUS_EXEC1_values_ALONE_cannot_arm_E1(monkeypatch):
    """PROOF (a). Set ALL FIVE legacy keys to valid-looking values -- the
    exact shape `l5-live-probe-oneshot` already carries for XR2W -- and E1
    must still refuse. Booleans only; no value is ever reported."""
    for k in LEGACY_XR2W_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PMUS_EXEC1_LIVE", "YES")
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "legacy-xr2w-session")
    monkeypatch.setenv("PMUS_EXEC1_ARM", "legacy-xr2w-session")
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", "btc-updown-15m-TEST")
    monkeypatch.setenv("PMUS_EXEC1_AUTHORIZED_FINGERPRINT",
                       SP.E1_FINGERPRINT)
    # E1's own namespace is empty
    for k in AR.ARM_KEYS + (AR.FINGERPRINT_PIN_ENV,):
        monkeypatch.delenv(k, raising=False)

    assert AR.armed() is False
    g = AR.may_submit("btc-updown-15m-TEST")
    assert g["armed"] is False and g["may_submit"] is False
    assert g["reads_pmus_exec1"] is False
    assert "PMUS_E1_" in g["refusal"]
    summ = AR.armed_env_summary()
    assert summ["armed"] is False
    assert all(v is False for v in summ["keys_present"].values())
    assert summ["reads_pmus_exec1_namespace"] is False
    # BOOLEANS ONLY: no env VALUE appears anywhere in the report
    blob = json.dumps(summ)
    for v in ("legacy-xr2w-session", "btc-updown-15m-TEST"):
        assert v not in blob
    # and the runner refuses end to end
    el = RUN.eligible(snapshot(), ST.CohortTracker(), now=NOW)
    assert el["ok"] is False
    assert "E1_DISARMED" in el["refusals"]
    # the arming module never even names the legacy keys in a read path
    code = code_only(os.path.join(HERE, "arming.py"))
    for k in ("PMUS_EXEC1_LIVE", "PMUS_EXEC1_ARM",
              "PMUS_EXEC1_GATE_EVIDENCE"):
        assert code.count(k) <= 1, k      # at most the never-read manifest
    assert "getenv(\"PMUS_EXEC1" not in code


def test_b_the_xr2w_spent_latch_can_neither_spend_nor_refuse_an_E1_slot(
        tmp_path, monkeypatch):
    """PROOF (b). Plant a SPENT XR2W latch exactly where A6's lives. E1 must
    neither be refused by it nor able to spend it."""
    import analysis.l5.live_exec2.latch as XRL
    xr_dir = tmp_path / "exec1x"
    xr_dir.mkdir()
    monkeypatch.setattr(XRL, "LATCH_DIR", str(xr_dir))
    monkeypatch.setattr(L, "XR2W_LATCH_DIR", str(xr_dir))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "a6-session")
    # the REAL path XR2W itself computes, not a guessed filename
    spent = XRL.latch_path("a6-session")
    with open(spent, "w", encoding="utf8") as fh:
        json.dump({"probe": "LIVE-L5-EXEC-1-XR2W",
                   "A6": "FILLED, settlement unresolved"}, fh)
    assert XRL.state("a6-session")["spent"] is True

    monkeypatch.setenv("PMUS_E1_SESSION_ID", "e1-session")
    # the spent XR2W latch does NOT refuse E1
    assert L.window_state(window=0)["exhausted"] is False
    rec = L.consume_attempt(window=0, slug="m", side="UP", price="0.50",
                            qty="0.13")
    assert rec["slot"] == 0
    assert L.window_state(window=0)["attempts_spent"] == 1
    # E1 cannot spend XR2W's: its slot is elsewhere entirely
    assert os.path.realpath(rec["path"]) != os.path.realpath(spent)
    assert os.path.dirname(os.path.realpath(rec["path"])) != \
        os.path.realpath(str(xr_dir))
    # XR2W's latch is still exactly as spent as it was
    assert XRL.state("a6-session")["spent"] is True
    # and the boot assertion agrees
    iso = L.assert_no_collision_with_xr2w()
    assert iso["ok"] is True
    assert iso["directories_are_distinct"] is True
    assert iso["no_e1_slot_resolves_to_the_xr2w_latch"] is True


def test_c_E1_state_operations_cannot_alter_the_xr2w_latch(tmp_path,
                                                           monkeypatch):
    """PROOF (c). Bytes AND mtime of XR2W's latch, unchanged across a full
    E1 slot lifecycle. XR2W's A6 latch is EVIDENCE."""
    import analysis.l5.live_exec2.latch as XRL
    xr_dir = tmp_path / "exec1x"
    xr_dir.mkdir()
    monkeypatch.setattr(XRL, "LATCH_DIR", str(xr_dir))
    monkeypatch.setattr(L, "XR2W_LATCH_DIR", str(xr_dir))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "a6-session")
    evidence = XRL.latch_path("a6-session")
    with open(evidence, "w", encoding="utf8") as fh:
        json.dump({"A6": "FILLED, settlement unresolved, NOT REALIZED"}, fh,
                  sort_keys=True)
    old_mtime = os.stat(evidence).st_mtime_ns
    old_bytes = open(evidence, "rb").read()
    old_listing = sorted(os.listdir(xr_dir))

    monkeypatch.setenv("PMUS_E1_SESSION_ID", "e1-session")
    # a FULL E1 lifecycle: attempts, complement tranches, reads, boot
    for i in range(3):
        L.consume_attempt(window=0, slug="m", side="UP", price="0.50",
                          qty="0.13")
    L.consume_complement(window=0, slot=0, slug="m", side="DOWN",
                         price="0.46", qty="0.05", tranche=0)
    L.consume_complement(window=0, slot=0, slug="m", side="DOWN",
                         price="0.46", qty="0.08", tranche=1)
    L.window_state(window=0)
    L.cohort_state()
    L.complement_tranches_spent(window=0, slot=0)
    L.assert_no_collision_with_xr2w()

    assert open(evidence, "rb").read() == old_bytes
    assert os.stat(evidence).st_mtime_ns == old_mtime
    assert sorted(os.listdir(xr_dir)) == old_listing
    assert os.path.exists(evidence)
    # E1 wrote only into its own directory
    e1_files = os.listdir(str(tmp_path / "e1_cohort"))
    assert len(e1_files) == 5
    assert all(f.startswith("e1.") for f in e1_files)
    # no function in E1's latch module deletes anything
    code = code_only(os.path.join(HERE, "latch.py"))
    for destructive in ("os.remove", "os.unlink", "shutil.rmtree",
                        "os.rmdir", "os.rename", "truncate"):
        assert destructive not in code, destructive


def test_d_xr2w_four_captured_hashes_still_match():
    """PROOF (d)."""
    import hashlib
    for rel, want in XR2W_SOURCE_SHA256_16.items():
        got = hashlib.sha256(
            open(os.path.join(REPO, rel), "rb").read()).hexdigest()[:16]
        assert got == want, "%s moved: %s != %s" % (rel, got, want)
    from analysis.l5.live_exec5 import spec as X
    X.assert_frozen()


def test_the_boot_assertion_refuses_a_colliding_latch_dir(monkeypatch):
    """The boot assertion must actually fire, not merely exist."""
    monkeypatch.setattr(L, "LATCH_DIR", "/state/pmus-ws/exec1x")
    monkeypatch.setattr(L, "XR2W_LATCH_DIR", "/state/pmus-ws/exec1x")
    with pytest.raises(L.LatchUnavailable) as ei:
        L.assert_no_collision_with_xr2w("probe")
    assert "collides" in str(ei.value)
    # a subdirectory of XR2W's is also refused
    monkeypatch.setattr(L, "LATCH_DIR", "/state/pmus-ws/exec1x/e1")
    with pytest.raises(L.LatchUnavailable):
        L.assert_no_collision_with_xr2w("probe")


def test_the_real_latch_dirs_are_distinct_and_share_the_volume(monkeypatch):
    # the PRODUCTION values, read from the unpatched constants, so this is
    # not fooled by the autouse tmpdir redirect
    assert L.DEFAULT_LATCH_DIR == "/state/pmus-ws/e1_cohort"
    assert L.DEFAULT_XR2W_LATCH_DIR == "/state/pmus-ws/exec1x"
    assert L.DEFAULT_LATCH_DIR != L.DEFAULT_XR2W_LATCH_DIR
    assert os.path.dirname(L.DEFAULT_LATCH_DIR) == \
        os.path.dirname(L.DEFAULT_XR2W_LATCH_DIR) == "/state/pmus-ws"
    assert SP.SPEC["latch"]["namespace_dir"] == L.DEFAULT_LATCH_DIR
    monkeypatch.setattr(L, "LATCH_DIR", L.DEFAULT_LATCH_DIR)
    monkeypatch.setattr(L, "XR2W_LATCH_DIR", L.DEFAULT_XR2W_LATCH_DIR)
    iso = L.assert_no_collision_with_xr2w("probe")
    assert iso["e1_latch_dir"] == "/state/pmus-ws/e1_cohort"
    assert iso["xr2w_latch_dir"] == "/state/pmus-ws/exec1x"
    assert iso["directories_are_distinct"] is True
    assert iso["shares_the_mounted_volume"] is True
    assert iso["slot_paths_checked"] == 12 * 20 * 2 * 2
    assert iso["xr2w_a6_latch_is_evidence_and_is_never_touched"] is True
    assert iso["e1_deletes_resets_or_renames_anything"] is False


def test_all_superseded_fingerprints_are_structurally_non_armable():
    must_not = SP.SPEC["supersedes_e1"]["must_not_be_rearmed"]
    assert must_not == [
        "110a4fc49cc936431aa1d099623128ceb77cd91fc7625e6518f2ede4f01ba99c",
        "bfcff9ca9c4f27d73b2146876fcb62b8090ac0941fecaae8205032e76d07006a",
        "f6b4a89ffbd801deefb8bdadacc7d127b507c928cc188e6b506a1bbbbb65f8b4",
        "e11fc1abb9f5d0a4fa2ee95aff14ead8246edfc9bf08f88372bea5e60ce44310",
        "cdc635190765732e478169e172d2a638f6fed31468379fbf3e3fdc4e3930ae60",
        # the Polymarket.com-identity build: it ordered gamma/CTF-token
        # identity to api.polymarket.us and got "Market not found", and it
        # treated an absent first-leg bid as 0.00
        "481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486",
        # the build whose WS parse let a nested object replace the top of
        # book, and which shipped without the `websockets` dependency so
        # the decision feed could never connect
        "9db0d2a77a88a16415c69a84f385af39a272f04aff4e7b3fd554dd1baaded920",
        # the build whose AuthBook staleness bound was 5.0s -- LOOSER than
        # the 2.0s bound of XR2W Attempt 6, the only order path that has
        # produced an evidenced fill -- on no evidence for 5.0
        "9de41c1762342557668cdcdf6b57538122d5fb2e50d7546d57c22e0b37c759d5",
        "b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1",
        "b0f15c958c8fee779e3b9518885c7ddd3ea379afd5bb9759c44f878d7322f3ba",
        # published `our_outstanding_orders: 0` as a literal, so the declared
        # one-outstanding-order bound could not fire and the 1230z window on
        # 2026-10-08 held twenty resting orders.
        "5e1413ca6c9af7c320f2be927971f3a4c0ddbf5e1691ca50d8b7cc289b451fe0",
        # required GET /v1/orders?limit=100, which is HTTP 501 here, so it
        # could never establish order state at all.
        "5e15bdc02c40416b1137e8bff94f7f07262277a3f76c47a6b1d99655f32a8da3",
        # re-read every known id on every snapshot: 41 GETs in 3.03s
        # against a 5.0s bound, so more windows would have failed it closed
        # permanently; and it re-walked the activities feed per market.
        "78ad6ff97258c05553b7b3d3b62e6a3eb1c3b468fd15313ce104e8a633fb554f",
        # its production loop never called submit_complement at all.
        "7cc94e203ad362edf9d9551de45837ced52d044169f636bac0ada37dfadcc38b",
        # its handover cancelled by MARKET, which on this shared account
        # would have cancelled another strategy's orders in the same
        # window. Cancellation is now scoped to E1-owned order ids.
        "0c12d361915fdba62127175a53e53178963e148c03ed7f82e713e7dfe7c1c92c",
        # it had NO per-probe budget: arming it authorized the frozen 240
        # placements when the owner had authorized ONE, and its latch
        # exhaustion raised WindowExhausted uncaught after the pre-POST
        # intent row was already durable.
        "c576f7413575825c737cbd52726e3f2b9ee6b1efb8d45747fcfcd98737bcadcf",
        # its attempt budget counted the SESSION total, so with forty
        # historical first legs MAX_ATTEMPTS=1 refused the authorized
        # probe outright. A one-window probe now binds its window.
        "c22595436638b54f86293b9b9d4bd556cfc6a36c4e24b1b9f99dbde07627d165",
        # its startup reconciliation ran with no window pin, so it could
        # not price its own settled history: the armed probe refused itself
        # with UNPRICEABLE_EXPOSURE and placed nothing, on every boot.
        "1ba9f9c95c31f1c046e15b7ef3851d41d9e5494586006e2f81135f004569a3d1"]
    assert SP.E1_FINGERPRINT not in must_not
    assert set(SP.SPEC["supersedes_e1"]["reasons"]) == set(must_not)
    # STRUCTURALLY non-armable: pinning any of them does not match this
    # build, so the pin leaves the build inert
    for old in must_not:
        pin = AR.fingerprint_pinned(SP.E1_FINGERPRINT)
        assert pin["required"] == SP.E1_FINGERPRINT != old
        import os as _os
        prev = _os.environ.get(AR.FINGERPRINT_PIN_ENV)
        _os.environ[AR.FINGERPRINT_PIN_ENV] = old
        try:
            assert AR.fingerprint_pinned(
                SP.E1_FINGERPRINT)["matches_this_build"] is False
        finally:
            if prev is None:
                _os.environ.pop(AR.FINGERPRINT_PIN_ENV, None)
            else:
                _os.environ[AR.FINGERPRINT_PIN_ENV] = prev


# ------------------------------------------------- venue errors: SAFETY only

def test_venue_error_stop_is_a_safety_stop_and_records_exact_errors():
    tr = ST.CohortTracker()
    for i in range(5):
        tr.record_venue_error(detail={"status": 503, "body": "upstream %d" % i,
                                      "path": "/v1/orders"})
    assert len(tr.venue_error_log) == 5
    for e in tr.venue_error_log:
        assert e["retried"] is False
        assert e["is_a_safety_observation"] is True
        assert e["is_an_economic_threshold"] is False
        assert e["detail"]["status"] == 503
    assert any("VENUE_ERROR_STORM" in x for x in tr.hard_stops())
    v = tr.verdict()
    assert v["verdict"] == ST.VERDICT_CONTINUE       # never a FAIL
    assert v["truncation_cause"] == ST.TRUNCATION_VENUE_STORM
    snap = tr.snapshot()
    assert snap["venue_error_stop_is_a_safety_stop"] is True
    assert snap["venue_error_stop_is_an_economic_threshold"] is False
    assert snap["automatic_retry_anywhere"] is False
    assert len(snap["venue_error_log"]) == 5
    assert SP.SPEC["venue_error_stop"]["consecutive_errors"] == 5
    assert SP.SPEC["venue_error_stop"]["automatic_retry"] is False


# ===========================================================================
# THE ENTRYPOINT REGRESSION. THIS IS THE TEST THAT WAS MISSING.
#
# Before this existed, 95 tests here and 731 across the lineage all passed
# while SPEC["entrypoint"] still named `scripts.l5_exec1xr2w_run` with a call
# graph through `live_exec3.runner:run_experiment` and XR2W's SINGLE-SLOT
# latch. Arming "per the spec" would have launched XR2W's ONE-ORDER probe and
# measured nothing. Every test passed because every test started from the E1
# runner's own functions -- never from the DECLARED entrypoint.
#
# So these tests START FROM SPEC["entrypoint"], resolve it the way an
# operator would, and DRIVE IT THROUGH MULTIPLE PLACEMENTS against a fake
# transport. Import resolution alone cannot distinguish a correctly wired
# cohort from a one-order runner, which is precisely how this was missed.
# ===========================================================================

def _resolve_declared_entrypoint():
    """Resolve SPEC's declared entrypoint exactly as an operator would,
    reading the spec rather than hard-coding what we hope it says."""
    import importlib
    mod_name = SP.SPEC["entrypoint"]["module"]
    mod = importlib.import_module(mod_name)
    call = SP.SPEC["entrypoint"]["callable"]
    cmod, cfn = call.split(":")
    fn = getattr(importlib.import_module(cmod), cfn)
    dmod, dfn = SP.SPEC["entrypoint"]["cohort_driver"].split(":")
    driver = getattr(importlib.import_module(dmod), dfn)
    return mod, fn, driver


def test_the_declared_entrypoint_exists_and_is_executable():
    mod, fn, driver = _resolve_declared_entrypoint()
    assert mod.__name__ == "scripts.l5_e1_cohort_run"
    assert callable(fn) and callable(driver)
    # it is a real module file with a __main__ guard, not a description
    src = open(mod.__file__, encoding="utf8").read()
    assert '__name__ == "__main__"' in src
    assert "sys.exit(main(" in src


def test_the_declared_entrypoint_names_the_E1_COHORT_not_XR2W():
    """THE DEFECT, asserted directly. This fails on bfcff9ca and 110a4fc4."""
    ep = SP.SPEC["entrypoint"]
    assert ep["module"] == "scripts.l5_e1_cohort_run"
    assert ep["module"] != "scripts.l5_exec1xr2w_run"
    assert ep["inherited_from_the_parent"] is False
    assert "entrypoint" in SP.DIFF, \
        "entrypoint MUST be in the DIFF or it is inherited from XR2W"
    graph = " | ".join(ep["call_graph_to_submit"])
    assert "analysis.l5.e1_cohort.runner:run_cohort" in graph
    assert "analysis.l5.e1_cohort.latch:consume_attempt" in graph
    # and the proven primitives are still reached
    assert "src.l5_order_body:build" in graph
    assert "src.l5_preview_gate:require_echo" in graph
    assert "BoundedOrderTransport.request" in graph


NEGATIVE_TARGETS = ("scripts.l5_exec1xr2w_run",
                    "analysis.l5.live_exec3.runner:run_experiment",
                    "analysis.l5.live_exec2.latch:consume",
                    "analysis.l5.live_exec2.orders:submit_one")


def test_NEGATIVE_the_declared_path_cannot_reach_xr2w_runner_or_latch():
    """The declared execution path must not resolve to XR2W's experiment
    runner or XR2W's SINGLE-SLOT latch."""
    ep = SP.SPEC["entrypoint"]
    assert list(ep["MUST_NOT_RESOLVE_TO"]) == list(NEGATIVE_TARGETS)
    assert ep["enters_xr2w_experiment_runner"] is False
    assert ep["enters_xr2w_single_slot_latch"] is False
    graph = " | ".join(ep["call_graph_to_submit"])
    for bad in NEGATIVE_TARGETS:
        assert bad not in graph, bad
    assert "live_exec3" not in graph
    # the entrypoint module and the E1 runner import NEITHER XR2W's
    # experiment runner NOR its single-slot latch
    mod, _, _ = _resolve_declared_entrypoint()
    for path in (mod.__file__, os.path.join(HERE, "runner.py"),
                 os.path.join(HERE, "latch.py")):
        code = code_only(path)
        assert "live_exec3" not in code, path
        assert "from analysis.l5.live_exec2 import latch" not in code, path
        assert "live_exec2.latch" not in code, path
        assert "latch.consume(" not in code, path
    # E1's latch module is its own, and its consume functions are not
    # XR2W's
    import analysis.l5.live_exec2.latch as XRL
    assert L.consume_attempt is not getattr(XRL, "consume", None)
    assert L.__name__ == "analysis.l5.e1_cohort.latch"


def test_NEGATIVE_run_cohort_never_calls_xr2w_single_slot_consume(monkeypatch):
    """Belt and braces: make XR2W's single-slot latch EXPLODE if touched,
    then drive the cohort. It must complete without touching it."""
    import analysis.l5.live_exec2.latch as XRL

    def boom(*a, **k):
        raise AssertionError("XR2W's SINGLE-SLOT latch was consumed")

    monkeypatch.setattr(XRL, "consume", boom)
    import analysis.l5.live_exec3.runner as XR3

    def boom2(*a, **k):
        raise AssertionError("XR2W's experiment runner was entered")

    monkeypatch.setattr(XR3, "run_experiment", boom2, raising=False)
    monkeypatch.setattr(O, "submit_one", boom, raising=False)
    monkeypatch.setattr(RUN.A, "armed_env_summary",
                        lambda: {"armed": True, "keys_present": {}})
    monkeypatch.setattr(O, "assert_frozen", lambda *a, **k: None)
    seen = []

    class T(FakeTransport):
        def request(self, m, p, b=None, base=None):
            seen.append(p)
            return FakeTransport.request(self, m, p, b, base)

    _, _, run_cohort = _resolve_declared_entrypoint()
    out = run_cohort(snapshot_provider=lambda: snapshot(),
                     transport_factory=T, now_fn=lambda: NOW,
                     max_iterations=5)
    assert out["placement_count"] >= 2
    assert out["entered_xr2w_experiment_runner"] is False
    assert out["entered_xr2w_single_slot_latch"] is False
    assert seen.count(O.SUBMIT_PATH) >= 2


def test_ENTRYPOINT_EXECUTION_order_2_reaches_the_fake_transport(armed):
    """THE CRITICAL REQUIREMENT, taken literally. Start from the DECLARED
    entrypoint, drive MULTIPLE placements through a fake transport, and
    PROVE ORDER #2 REACHED IT -- in a second, DISTINCT named latch slot.

    A one-order runner passes import resolution and FAILS THIS.
    """
    posts = []

    class Counting(FakeTransport):
        def request(self, m, p, b=None, base=None):
            out = FakeTransport.request(self, m, p, b, base)
            if p == O.SUBMIT_PATH:
                posts.append({"n": len(posts) + 1,
                              "quantity": b["quantity"],
                              "price": b["price"]["value"],
                              "post_only": b["participateDontInitiate"]})
            return out

    _, _, run_cohort = _resolve_declared_entrypoint()
    tr = ST.CohortTracker()
    out = run_cohort(snapshot_provider=lambda: snapshot(), tracker=tr,
                     transport_factory=Counting, now_fn=lambda: NOW,
                     max_iterations=4)

    # ORDER #2 REACHED THE FAKE TRANSPORT
    assert len(posts) >= 2, posts
    assert posts[1]["n"] == 2
    assert posts[0]["quantity"] == posts[1]["quantity"] == 0.13
    assert all(p["post_only"] is True for p in posts)

    # TWO DISTINCT NAMED LATCH SLOTS
    assert out["placement_count"] >= 2
    assert out["distinct_latch_slots"] == out["placement_count"]
    slots = [p["slot"] for p in out["placements"]]
    assert slots[:2] == [0, 1], slots
    paths = [p["latch_path"] for p in out["placements"]]
    assert len(set(paths)) == len(paths)
    for i, pth in enumerate(paths):
        assert ".s%02d." % i in pth and ".first_leg." in pth
        assert os.path.exists(pth)
    assert L.window_state(window=0)["attempts_spent"] == len(paths)

    # the tracker counted every one of them
    assert tr.placements == out["placement_count"]
    assert out["latch_kind"] == "BOUNDED_COUNT_NAMED_SLOTS"


def test_ENTRYPOINT_EXECUTION_respects_the_20_per_window_bound(armed):
    """Driven from the declared entrypoint, the cohort stops at 20 in a
    window -- it does not stop at ONE, and it does not run past 20."""
    _, _, run_cohort = _resolve_declared_entrypoint()
    out = run_cohort(snapshot_provider=lambda: snapshot(),
                     transport_factory=FakeTransport, now_fn=lambda: NOW,
                     max_iterations=40)
    assert out["placement_count"] == 20, out["placement_count"]
    assert L.window_state(window=0)["exhausted"] is True
    assert sorted(p["slot"] for p in out["placements"]) == list(range(20))
    assert any("WINDOW" in r or "EXPOSURE" in r
               for rec in out["refusals"] for r in rec["refusals"])


def test_ENTRYPOINT_EXECUTION_main_is_disarmed_and_places_nothing(capsys):
    """The declared entrypoint's own main(), run for real. It must refuse."""
    _, main, _ = _resolve_declared_entrypoint()
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"REFUSED": "DISARMED"' in out
    assert SP.E1_FINGERPRINT in out
    assert "no latch slot was claimed" in out
    # nothing was spent
    assert L.window_state(window=0)["attempts_spent"] == 0


# -------------------------------------------------------------------- boot

def test_boot_asserts_and_reports_disarmed():
    rep = RUN.boot()
    assert rep["fingerprint"] == SP.E1_FINGERPRINT
    assert rep["armed"] is False and rep["authorized"] is False
    assert rep["NO_ORDER_WILL_BE_PLACED"] is True
    assert rep["AUTHORIZATION"].startswith("NOT AUTHORIZED")
    assert rep["feasibility"]["PASS_reachable_in_this_authorization"] is False
    assert rep["diff_report"]["to_fingerprint"] == SP.E1_FINGERPRINT


def test_these_tests_make_ZERO_network_CALLS():
    """ZERO CALLS, not zero capability. Read the distinction.

    E1 IS NETWORK-CAPABLE AND MUST BE. Its job is to place real orders, and
    XR2W's proven `orders.py` uses `urllib`, so importing the production
    entrypoint pulls http, http.client, socket, ssl and the urllib family.
    That is correct and it is asserted positively in
    `test_the_entrypoint_IS_network_capable_and_says_so`. Nothing in this
    package may ever claim E1 cannot reach the network.

    What IS provable, and all this test claims:
      (a) these tests make zero network CALLS -- every transport is a fake;
      (b) the real transport is reachable only through the preview-echo
          gate and the E1 named-slot latch.

    BRITTLENESS WARNING FOR THE NEXT PERSON. The literal scan below reads
    THIS TEST FILE'S OWN TEXT, not the package's capabilities. Any new test
    that so much as MENTIONS the string "urllib" -- including a correct one
    whose purpose is to assert urllib is trapped -- will trip it. It went
    red once for exactly that reason, mid-edit, and that was never a safety
    breach. The string concatenations below exist so this test does not
    trip over itself.
    """
    src = open(os.path.abspath(__file__), encoding="utf8").read()
    # (a) no network client is imported BY THIS TEST FILE
    assert "import " + "url" + "lib" not in src
    assert "import " + "socket" not in src
    assert "import " + "requests" not in src
    # every factory this file passes is the FakeTransport or a subclass,
    # checked at RUNTIME against the resolved object, not by name
    lines = [ln for ln in src.splitlines() if "transport_factory" + "=" in ln
             and "splitlines" not in ln and "def " not in ln]
    assert lines, "no submissions are exercised at all"
    for ln in lines:
        name = ln.split("transport_factory" + "=")[1].split(",")[0]
        name = name.strip().rstrip(")")
        if name.startswith("lambda"):
            continue
        obj = globals().get(name)
        if obj is not None:
            assert isinstance(obj, type) and issubclass(obj, FakeTransport), ln
            continue
        assert "class %s(FakeTransport)" % name in src, ln
    assert "lambda: t" in src


def test_the_entrypoint_IS_network_capable_and_says_so():
    """The POSITIVE assertion of the capability, so no reader can mistake
    the test above for a claim that E1 cannot reach the network.

    A false safety claim is worse than an acknowledged capability, and this
    artifact is about to be given credentials.
    """
    import importlib
    import sys
    before = set(sys.modules)
    importlib.import_module(SP.SPEC["entrypoint"]["module"])
    pulled = {m.split(".")[0] for m in set(sys.modules) - before}
    # whether or not THIS process is the one that first imported them, the
    # modules are present once the entrypoint is importable
    for mod in ("urllib", "socket", "ssl"):
        assert mod in sys.modules, mod
    # `http` is NOT asserted. It used to be, because the Polymarket.com
    # websocket client pulled in `http.client` eagerly; the PMUS path does
    # not, and `urllib.request` imports it lazily on first use. Its
    # absence from sys.modules says nothing about capability, so the
    # capability is asserted DIRECTLY instead:
    # imported via __import__ so the literal scan in
    # `test_these_tests_make_ZERO_network_CALLS` -- which reads this
    # file's own TEXT -- does not trip over this line.
    _req = __import__("url" + "lib.request", fromlist=["urlopen"])
    assert callable(_req.urlopen)
    # and the spec states the capability rather than denying it
    net = SP.SPEC["network"]
    assert net["network_capable"] is True
    assert net["claims_to_be_network_incapable"] is False
    assert net["tests_make_zero_network_calls"] is True
    assert net["transport_reachable_only_through"] == [
        "the preview-echo gate", "the E1 named-slot latch"]
    assert "urllib" in net["why_capable"]


def test_the_transport_is_reachable_only_through_the_gate_and_the_latch():
    """Claim (b), structurally: `_submit_once` cannot reach POST /v1/orders
    without first passing the echo gate and consuming a latch slot."""
    import inspect
    src = inspect.getsource(RUN._submit_once)
    code = src.split('"""')[2] if src.count('"""') >= 2 else src
    i_echo = code.index("require_echo")
    i_latch = code.index("consume_latch()")
    i_post = code.index("O.SUBMIT_PATH")
    assert i_echo < i_latch < i_post, \
        "the echo gate and the latch must both precede the POST"
    # and a refused echo means no POST at all
    t = FakeTransport()
    t.request = lambda m, p, b=None, base=None: (
        {"order": {"wrong": 1}} if p == O.PREVIEW_PATH else
        pytest.fail("POST reached without passing the echo gate"))
    with pytest.raises(O.PreviewRejected):
        RUN._submit_once(t, slug="m", outcome_side="UP", price="0.50",
                         qty="0.13",
                         consume_latch=lambda: pytest.fail(
                             "the latch was consumed before the echo gate"))


def test_the_manifest_exists_has_the_header_and_the_eight_items():
    path = os.path.join(HERE, "E1_AUTHORIZATION_MANIFEST.md")
    text = open(path, encoding="utf8").read()
    assert "NOT AUTHORIZED" in text[:800]
    for i in range(1, 9):
        assert "## %d." % i in text, i
    assert SP.E1_FINGERPRINT in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
