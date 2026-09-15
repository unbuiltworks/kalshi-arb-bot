"""The executable shadow: what a venue can ACTUALLY supply, and what it cannot.

These tests pin the distinctions that have cost this program money or evidence
before: unknown vs absent, modelled vs observed, touch depth vs executable
depth, and a maker fill that nobody saw happen.
"""
from decimal import Decimal as D

import pytest

from src.l5_adapters.base import AdapterEvaluation
from src.l5_adapters.gemini_mapping import resolve
from src.l5_intent.action import DesiredInventoryAction
from src.l5_router.route import route
from src.l5_shadow import depth as DEP
from src.l5_shadow import maker as MK
from src.l5_shadow import terms as TERMS
from src.l5_shadow import service as SVC
from src.l5_shadow.executable import VenueTerms, evaluate_executable, walk_levels


def _intent(qty="10", ceiling="0.70", outcome="Up"):
    return DesiredInventoryAction(
        cohort="BTC15m", market_key="btc-updown-15m-1789470900",
        source_state_id="s1", t_utc=1789470910.0, tte_s=890.0,
        action="OPEN", desired_outcome=outcome, desired_inventory=qty,
        incremental_qty=qty, max_acceptable_price=ceiling,
        current_matched="0", residual="0", model_version="test")


def _terms(*, taker=lambda p, q: D("0"), maker=None, minsz=None, tick="0.01"):
    return VenueTerms(venue="TEST", taker_fee=taker, maker_fee=maker,
                      min_order_size=(None if minsz is None else D(str(minsz))),
                      tick=D(tick), source="test")


# ----------------------------------------------------------------- the ladder

def test_partial_fill_is_reported_not_rounded_up():
    lv = [(D("0.60"), D("3"))]
    r = DEP.vwap_for_qty(lv, D("10"))
    assert r["filled"] == "3" and r["complete"] is False
    assert r["vwap"] == "0.600000"          # average over what EXISTS, not over 10


def test_touch_depth_of_one_does_not_mean_the_venue_cannot_supply_five():
    """The finding that motivated this module: 1 at the touch, size behind it."""
    lv = [(D("0.66"), D("1")), (D("0.68"), D("4")), (D("0.70"), D("30"))]
    lad = DEP.executable_ladder(lv, max_price=D("0.69"))
    assert lad["immediately_executable_qty"] == "5"
    assert lad["by_qty"]["5"]["complete"] is True
    assert lad["by_qty"]["10"]["complete"] is False       # size-limited, not absent


def test_levels_above_the_ceiling_are_dropped_before_the_walk():
    """A VWAP that averages an acceptable price with an unacceptable one can
    land under the ceiling while containing fills we would never accept."""
    lv = [(D("0.50"), D("1")), (D("0.90"), D("9"))]
    lad = DEP.executable_ladder(lv, max_price=D("0.70"))
    assert lad["by_qty"]["10"]["filled"] == "1"


def test_no_ladder_from_yes_inverts_price_and_keeps_size():
    out = DEP.no_ladder_from_yes([(D("0.72"), D("5")), (D("0.70"), D("500"))])
    assert out == [(D("0.28"), D("5")), (D("0.30"), D("500"))]


# --------------------------------------------- the bug that read as an empty book

def test_polymarket_sized_levels_parse_with_polymarket_keys():
    bk = {"asks": [{"price": "0.74", "size": "4549"},
                   {"price": "0.71", "size": "508.49"}],
          "price_key": "price", "size_key": "size"}
    lv = DEP.parse_book(bk, "asks")
    assert lv[0] == (D("0.71"), D("508.49"))          # best-first for a BUY


def test_levels_present_but_unparseable_raise_rather_than_read_as_empty():
    """112 real levels once reported as 'venue quotes no Up side at all'."""
    bk = {"asks": [{"price": "0.74", "size": "4549"}], "size_key": "amount"}
    with pytest.raises(DEP.BookParseError):
        DEP.parse_book(bk, "asks")


def test_a_genuinely_empty_side_is_still_empty_not_an_error():
    assert DEP.parse_book({"asks": [], "size_key": "amount"}, "asks") == []


# ------------------------------------------------------------- the evaluator

def test_unreadable_book_is_no_market_and_says_unknown_not_absent():
    ev = evaluate_executable(_intent(), _terms(), None)
    assert ev.stage == "no_market" and ev.would_submit is False
    assert "UNKNOWN" in (ev.mapping_reason or "")


def test_missing_fee_refuses_rather_than_pricing_at_zero():
    ev = evaluate_executable(_intent(), _terms(taker=None),
                             [(D("0.60"), D("50"))])
    assert ev.would_submit is False
    assert "fee unknown" in ev.would_submit_reason
    assert ev.effective_cost is None


def test_fee_is_summed_per_level_not_charged_once_at_the_average():
    lv = [(D("0.60"), D("5")), (D("0.62"), D("5"))]
    seen = []

    def fee(p, q):
        seen.append((p, q))
        return D("0.01") * q
    ev = evaluate_executable(_intent(qty="10", ceiling="0.70"), _terms(taker=fee),
                             lv)
    assert (D("0.60"), D("5")) in seen and (D("0.62"), D("5")) in seen
    assert ev.expected_fee == "0.10"


def test_venue_minimum_order_size_is_a_hard_gate():
    ev = evaluate_executable(_intent(qty="1", ceiling="0.70"),
                             _terms(minsz=5), [(D("0.60"), D("500"))])
    assert ev.would_submit is False
    assert "minimum order size" in ev.would_submit_reason


def test_executable_qty_counts_every_acceptable_level_not_just_the_touch():
    lv = [(D("0.66"), D("1")), (D("0.68"), D("499"))]
    ev = evaluate_executable(_intent(qty="10", ceiling="0.70"), _terms(), lv)
    assert ev.available_depth == "1"            # the touch
    assert ev.executable_qty == "500"           # what we could actually take
    assert ev.would_submit is True


def test_the_full_qty_ladder_is_recorded_even_when_the_intents_size_fails():
    lv = [(D("0.60"), D("3"))]
    ev = evaluate_executable(_intent(qty="10", ceiling="0.70"), _terms(), lv)
    assert ev.would_submit is False
    lad = ev.extra["qty_ladder"]
    assert lad["1"]["complete"] is True and lad["2"]["complete"] is True
    assert lad["10"]["complete"] is False       # size-limited, not incapable


def test_partial_capacity_is_not_reported_as_a_submittable_order():
    ev = evaluate_executable(_intent(qty="10", ceiling="0.70"), _terms(),
                             [(D("0.60"), D("3"))])
    assert ev.would_submit is False and ev.would_fill is False


# ---------------------------------------------------------------- the router

def test_router_uses_executable_depth_rather_than_the_touch():
    i = _intent(qty="10", ceiling="0.70")
    thin_touch = evaluate_executable(
        i, _terms(), [(D("0.66"), D("1")), (D("0.68"), D("499"))])
    d = route(i, [thin_touch])
    assert d.route == "TEST", d.declined


def test_router_still_fails_closed_when_capacity_is_unknown():
    ev = AdapterEvaluation(venue="X", intent_id=_intent().id,
                           market_key="m", evaluated_at=0.0,
                           would_submit=True, within_ceiling=True,
                           effective_cost="0.50")
    assert route(_intent(), [ev]).route == "NEITHER"


# ----------------------------------------------------------------- the maker

def test_no_prints_is_undecided_not_a_no_fill():
    r = MK.evidence_for_quote(quote_px="0.60", quote_qty="10", ahead="0",
                              placed_at=100.0, prints=[], observed_until=220.0)
    assert r["evidenced_fill"] is None
    assert "NOT a no-fill" in r["reason"]


def test_unreadable_prints_are_unknown_not_a_no_fill():
    r = MK.evidence_for_quote(quote_px="0.60", quote_qty="10", ahead="0",
                              placed_at=100.0, prints=None, observed_until=220.0)
    assert r["evidenced_fill"] is None and "UNKNOWN" in r["reason"]


def test_a_print_before_placement_cannot_fill_an_order_that_did_not_exist():
    p = [{"t": 99.0, "price": D("0.55"), "size": D("100")}]
    r = MK.evidence_for_quote(quote_px="0.60", quote_qty="10", ahead="0",
                              placed_at=100.0, prints=p, observed_until=220.0)
    assert r["evidenced_fill"] is None


def test_we_join_behind_the_queue_and_it_must_be_cleared_first():
    p = [{"t": 101.0, "price": D("0.60"), "size": D("30")}]
    r = MK.evidence_for_quote(quote_px="0.60", quote_qty="10", ahead="25",
                              placed_at=100.0, prints=p, observed_until=220.0)
    assert r["evidenced_fill"] is False          # 30 - 25 = 5 of our 10
    assert r["modelled_fill_qty"] == "5"


def test_a_cleared_queue_and_enough_size_evidences_the_fill():
    p = [{"t": 101.0, "price": D("0.59"), "size": D("40")}]
    r = MK.evidence_for_quote(quote_px="0.60", quote_qty="10", ahead="25",
                              placed_at=100.0, prints=p, observed_until=220.0)
    assert r["evidenced_fill"] is True and r["time_to_fill_s"] == 1.0


def test_prints_above_our_bid_never_count():
    p = [{"t": 101.0, "price": D("0.61"), "size": D("999")}]
    r = MK.evidence_for_quote(quote_px="0.60", quote_qty="10", ahead="0",
                              placed_at=100.0, prints=p, observed_until=220.0)
    assert r["evidenced_fill"] is None           # nothing crossed us


def test_join_price_does_not_improve_the_touch_by_default():
    lv = [(D("0.60"), D("10")), (D("0.59"), D("5"))]
    assert MK.join_price(lv, tick=D("0.01"), ceiling=D("0.70")) == D("0.60")
    assert MK.join_price(lv, tick=D("0.01"), ceiling=D("0.70"),
                         improve=True) == D("0.61")


def test_join_price_declines_when_the_touch_is_over_the_ceiling():
    assert MK.join_price([(D("0.80"), D("10"))], tick=D("0.01"),
                         ceiling=D("0.70")) is None


def test_queue_ahead_counts_only_our_own_price_level():
    lv = [(D("0.60"), D("10")), (D("0.59"), D("500"))]
    assert MK.queue_ahead(lv, D("0.60")) == D("10")


# ------------------------------------------------------------------ the terms

def test_polymarket_maker_pays_nothing_only_when_the_venue_says_takeronly():
    t = TERMS.pm_terms({"fee_rate": 0.07, "fee_taker_only": True,
                        "order_min_size": 5, "tick": 0.01, "terms_source": "x"})
    assert t.maker_fee(D("0.66"), D("10")) == D("0")
    u = TERMS.pm_terms({"fee_rate": 0.07, "fee_taker_only": None,
                        "order_min_size": 5, "tick": 0.01, "terms_source": "x"})
    assert u.maker_fee is None                   # undeclared is not zero


def test_undeclared_polymarket_fee_refuses_rather_than_assuming_a_rate():
    t = TERMS.pm_terms({"order_min_size": 5, "tick": 0.01, "terms_source": "x"})
    assert t.taker_fee is None and "UNKNOWN" in t.source


def test_unreachable_gamma_is_unknown_terms_not_free_terms():
    t = TERMS.pm_terms(None)
    assert t.taker_fee is None and t.maker_fee is None


def test_gemini_maker_waiver_is_not_applied():
    g = TERMS.gemini_terms()
    assert g.maker_fee(D("0.66"), D("10")) > 0
    assert "NOT applied" in g.extra["maker_waiver_sep2_dec31"]


def test_polymarket_taker_fee_matches_the_measured_per_share_rate():
    t = TERMS.pm_terms({"fee_rate": 0.07, "fee_taker_only": True,
                        "order_min_size": 5, "tick": 0.01, "terms_source": "x"})
    assert t.taker_fee(D("0.60"), D("10")) == D("0.07") * D("0.60") * D("0.40") * 10


# ------------------------------------------------------------ side-aware mapping

def test_a_book_with_no_asks_can_still_supply_down():
    one_sided = {"bids": [{"price": "0.95"}], "asks": []}
    m, conf, why = resolve("btc-updown-5m-1789477500", lambda s: one_sided,
                           want_side="Down")
    assert conf == "exact" and m["top_ask"] is None


def test_the_same_book_cannot_supply_up():
    one_sided = {"bids": [{"price": "0.95"}], "asks": []}
    m, conf, why = resolve("btc-updown-5m-1789477500", lambda s: one_sided,
                           want_side="Up")
    assert conf == "none" and "quotes no asks" in why


def test_omitting_the_side_keeps_the_stricter_two_sided_test():
    one_sided = {"bids": [{"price": "0.95"}], "asks": []}
    assert resolve("btc-updown-5m-1789477500", lambda s: one_sided)[1] == "none"


# ---------------------------------------------------------------- the intent

def test_the_ceiling_is_l5s_own_price_on_the_side_we_want():
    anchor = {"price": 0.0299999993, "outcome": "Down", "t": 1.0, "size": 5.0,
              "cid": "c"}
    assert SVC.implied_price_for("Up", anchor) == D("0.9700")
    assert SVC.implied_price_for("Down", anchor) == D("0.0300")


def test_replay_intent_restates_l5s_own_fill_and_carries_its_provenance():
    anchor = {"price": 0.62, "outcome": "Up", "t": 1789470910.0, "size": 7.0,
              "cid": "c", "window_end": 1789471800}
    st = {"inv_up": 7.0, "inv_dn": 0.0, "tte_s": 890.0}
    i = SVC.replay_intent("BTC15m", "btc-updown-15m-1789470900", anchor, st,
                          now=1789470925.0)
    assert i.desired_outcome == "Up" and i.action == "EXTEND"
    assert i.max_acceptable_price == "0.6200"
    assert i.extra["intent_source"] == "REPLAY"
    assert i.extra["l5_observation_lag_s"] == 15.0
    assert i.model_version == "replay/l5_anchor_fill"


def test_a_flat_book_state_opens_and_an_opposed_residual_switches():
    a = {"price": 0.62, "outcome": "Up", "t": 1.0, "size": 7.0, "cid": "c"}
    flat = SVC.replay_intent("BTC15m", "btc-updown-15m-1789470900", a,
                             {"inv_up": 0.0, "inv_dn": 0.0, "tte_s": 1.0}, now=2.0)
    opp = SVC.replay_intent("BTC15m", "btc-updown-15m-1789470900", a,
                            {"inv_up": 0.0, "inv_dn": 9.0, "tte_s": 1.0}, now=2.0)
    assert flat.action == "OPEN" and opp.action == "SWITCH"


def test_walk_levels_reports_the_per_level_shape_of_the_fill():
    used = walk_levels([(D("0.60"), D("3")), (D("0.62"), D("10"))], D("5"))
    assert used == [(D("0.60"), D("3")), (D("0.62"), D("2"))]


# ------------------------------------------------------------ money-safety

def test_the_shadow_imports_no_order_path():
    """A research service that can import a signer is one edit from trading.

    IN A FRESH SUBPROCESS, because `sys.modules` is process-global: run inside
    the full suite, any other test that imports the executor makes this pass or
    fail for reasons having nothing to do with the shadow. The first version of
    this test asserted against the whole suite's imports and passed standalone
    purely by accident of ordering.
    """
    import subprocess
    import sys
    code = (
        "import sys;"
        "import src.l5_shadow.service;"
        "import src.l5_shadow.venues;"
        "import src.l5_shadow.executable;"
        "import src.l5_shadow.maker;"
        "from src.l5_shadow import terms;"
        "terms.gemini_terms();"
        "print(' '.join(sorted(m for m in sys.modules "
        "if m.startswith('src.'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    closure = out.stdout.split()
    banned = [m for m in closure
              if m.startswith(("src.pmus_", "src.l5_venue_adapter",
                               "src.kalshi_", "src.gemini_"))]
    assert not banned, f"order path reachable from the shadow: {banned}"
    assert "src.l5_shadow.service" in closure      # the probe really ran


# ------------------------------------------------------- the deployment shape

def _deploy_text(name):
    import pathlib
    return pathlib.Path("deploy/l5_exec_shadow") .joinpath(name).read_text()


def test_the_image_copies_every_module_the_service_actually_imports():
    """A missing COPY is a crash on the first boot, not a lint failure."""
    df = _deploy_text("Dockerfile")
    for need in ("src/l5_shadow/", "src/l5_intent/", "src/l5_router/",
                 "src/l5_fidelity/", "src/l5_adapters/base.py",
                 "src/l5_adapters/gemini_mapping.py",
                 "src/polymarket_l5/transfer_backtest.py",
                 "artifacts/l5_fidelity/"):
        assert need in df, f"Dockerfile does not COPY {need}"


def test_the_image_does_not_carry_an_order_path():
    """`evaluate.py` imports l5_venue_adapter, which reaches pmus_q1_executor.

    Only COPY directives are scanned: the Dockerfile NAMES these modules in a
    comment explaining why they are absent, and a test that greps the whole
    file would fail on the explanation rather than on the shipped contents.
    """
    copies = [l for l in _deploy_text("Dockerfile").splitlines()
              if l.strip().startswith("COPY ")]
    for banned in ("l5_adapters/evaluate.py", "l5_venue_adapter",
                   "pmus_q1_executor", "pmus_spring", "pmus_l5_continuous"):
        hit = [l for l in copies if banned in l]
        assert not hit, f"read-only image would ship {banned}: {hit}"


def test_the_service_deploys_only_from_its_promotion_sentinel():
    """Editing source must not rebuild it; appending to the sentinel must."""
    import json
    cfg = json.loads(_deploy_text("railway.json"))
    assert cfg["build"]["watchPatterns"] == ["deploy/_promote/l5-exec-shadow"]
    assert cfg["deploy"]["restartPolicyType"] == "ALWAYS"


def test_the_promotion_sentinel_exists():
    import pathlib
    assert pathlib.Path("deploy/_promote/l5-exec-shadow").exists()


# ----------------------------------------------------------- what we would hold

def _pair_intent(**kw):
    base = dict(cohort="BTC15m", market_key="btc-updown-15m-1789470900",
                source_state_id="s1", t_utc=1.0, tte_s=9.0, action="COMPLETE",
                desired_outcome="Down", desired_inventory="10",
                incremental_qty="10", max_acceptable_price="0.70",
                current_matched="4", residual="6", model_version="test")
    base.update(kw)
    return DesiredInventoryAction(**base)


def test_resulting_inventory_is_reconstructed_from_the_frozen_state():
    ev = evaluate_executable(_pair_intent(), _terms(), [(D("0.60"), D("100"))])
    # up = 4 + 6 = 10, dn = 4 + 0 = 4, then +10 Down -> dn 14
    assert ev.resulting_matched == "10" and ev.resulting_residual == "-4"
    inv = ev.extra["resulting_inventory"]
    assert inv["completes_pair"] is True and inv["reduces_residual"] is True
    assert inv["matched_delta"] == "6"


def test_extending_the_long_side_does_not_complete_a_pair():
    ev = evaluate_executable(_pair_intent(desired_outcome="Up", action="EXTEND"),
                             _terms(), [(D("0.60"), D("100"))])
    inv = ev.extra["resulting_inventory"]
    assert inv["completes_pair"] is False
    assert inv["reduces_residual"] is False
    assert ev.resulting_residual == "16"


def test_pair_cost_is_left_none_rather_than_filled_with_half_a_pair():
    """A pair cost needs the other leg's average, and we hold nothing yet."""
    ev = evaluate_executable(_pair_intent(), _terms(), [(D("0.60"), D("100"))])
    assert ev.pair_cost is None
    assert "not computable from one action" in ev.extra["pair_cost_basis"]


def test_resulting_inventory_is_absent_when_the_action_is_not_submittable():
    ev = evaluate_executable(_pair_intent(max_acceptable_price="0.50"),
                             _terms(), [(D("0.60"), D("100"))])
    assert ev.would_submit is False
    assert "resulting_inventory" not in ev.extra
