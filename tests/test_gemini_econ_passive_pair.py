"""PASSIVE_PAIR_V1: the second arm, and the fill sequence it is judged on."""
import pytest

from src.gemini_execution.econ import arm as ARM
from src.gemini_execution.econ import entry as EN
from src.gemini_execution.econ import fill_sequence as FS
from src.gemini_execution.econ import hazard as HZ
from src.gemini_execution.econ import policy as PO
from src.gemini_execution.econ import registry as REG

PRINTS = [{"side": "buy", "qty": "1"}, {"side": "sell", "qty": "2"},
          {"side": "buy", "qty": "1"}, {"side": "sell", "qty": "1"},
          {"side": "buy", "qty": "1"}]
WIDE = {"yes": {"bid": "0.30", "ask": "0.55"},
        "no": {"bid": "0.33", "ask": "0.58"}}
EXPENSIVE = {"yes": {"bid": "0.58", "ask": "0.62"},
             "no": {"bid": "0.44", "ask": "0.48"}}


@pytest.fixture(autouse=True)
def _clean():
    REG.reset()
    yield
    REG.reset()


def _th(**over):
    kw = dict(cohort="BTC5m", min_alternation=0.35,
              max_largest_print_share=0.45, max_required_gap_ticks=2,
              max_reference_disagreement="0.04", min_prints=5,
              hazard_threshold=0.40, min_edge="0.005", max_spread="0.08",
              spread_mode=EN.SPREAD_PASSIVE_PAIR)
    kw.update(over)
    return EN.Thresholds(**kw)


def _admit(book, th=None, price=None):
    f = HZ.features(tte_s=200, held_leg="YES",
                    held_leg_price=(price or book["yes"]["bid"]),
                    required_complement_price="0.34", book=book,
                    prints=PRINTS)
    return EN.admit_entry(feat=f, thresholds=th or _th(),
                          proposed_price=(price or book["yes"]["bid"]),
                          inventory={"directional_residual": "0"},
                          flow_vouched=True)


def _gate(r, name):
    return next(g for g in r["gates"] if g["gate"] == name)


# ------------------------------------------------------------- the rule

def test_a_wide_spread_is_admitted_because_the_passive_pair_is_cheap():
    """The whole point: a resting BUY joins each outcome's BID, so a wide
    spread pushes yes_bid + no_bid DOWN."""
    r = _admit(WIDE)
    g = _gate(r, "PASSIVE_PAIR_ECONOMIC")
    assert g["pass"] is True
    assert float(g["passive_pair_cost"]) < float(g["ceiling"])
    assert r["admitted"] is True


def test_an_expensive_passive_pair_is_refused_however_tight_the_spread():
    """spread 0.04 -- well inside the old 0.08 bound -- but the two bids sum
    above the edge, so there is no pair worth owning."""
    r = _admit(EXPENSIVE)
    g = _gate(r, "PASSIVE_PAIR_ECONOMIC")
    assert g["pass"] is False
    assert "above the" in g["why"]
    assert r["admitted"] is False


def test_there_is_no_fixed_maximum_spread_in_this_mode():
    r = _admit(WIDE)
    assert not any(g["gate"] == "SPREAD" for g in r["gates"])
    assert r["spread_mode"] == EN.SPREAD_PASSIVE_PAIR


def test_the_boolean_arm_is_untouched():
    """The original arm must keep emitting SPREAD and refusing the wide book."""
    r = _admit(WIDE, th=_th(spread_mode=EN.SPREAD_BOOLEAN))
    assert _gate(r, "SPREAD")["pass"] is False
    assert not any(g["gate"] == "PASSIVE_PAIR_ECONOMIC" for g in r["gates"])


def test_rebates_are_excluded_from_admission():
    r = _admit(WIDE)
    assert _gate(r, "PASSIVE_PAIR_ECONOMIC")["rebate_excluded"] is True
    assert any("excludes rebates" in x for x in r["labels"])


def test_unreadable_bids_refuse_rather_than_default():
    r = _admit({"yes": {"bid": "0.30", "ask": "0.55"}, "no": {"ask": "0.58"}})
    g = _gate(r, "PASSIVE_PAIR_ECONOMIC")
    assert g["pass"] is None
    assert r["admitted"] is False


def test_an_unknown_spread_mode_is_rejected():
    with pytest.raises(ValueError):
        _th(spread_mode="SOMETIMES")


# ------------------------------------------------- the two arms are twins

def test_the_variant_differs_in_spread_mode_and_nothing_else():
    """A second arm that also moved another threshold would answer a
    different question."""
    b = PO.policy_for("BTC5m")
    v = PO.policy_for("BTC5m", variant=PO.PASSIVE_PAIR_VARIANT)
    assert v.thresholds.spread_mode == EN.SPREAD_PASSIVE_PAIR
    assert b.thresholds.spread_mode == EN.SPREAD_BOOLEAN
    for f in ("min_alternation", "max_largest_print_share",
              "max_required_gap_ticks", "max_reference_disagreement",
              "min_prints", "hazard_threshold", "min_edge", "max_spread"):
        assert getattr(b.thresholds, f) == getattr(v.thresholds, f), f
    for f in ("quote_band_s", "entry_until_tte_s", "mixed_until_tte_s",
              "withdraw_below_tte_s", "max_residual", "toxicity_horizon_s",
              "rescue_below_tte_s", "flow_window_s"):
        assert getattr(b, f) == getattr(v, f), f


def test_both_cohorts_have_a_variant():
    for c in ("BTC5m", "BTC15m"):
        assert PO.policy_for(c, variant=PO.PASSIVE_PAIR_VARIANT) is not None


def test_two_arms_share_the_market_but_never_the_evidence():
    """One socket and one census; separate coverage, attribution, labels,
    forks and fill sequences -- or the comparison is destroyed."""
    a = REG.state_for("BTC5m", arm="GEMINI_ECON_V1")
    b = REG.state_for("BTC5m", arm="PASSIVE_PAIR_V1")
    assert a is not b
    assert a.market is b.market                  # shared
    assert a.spreads is b.spreads                # shared
    for attr in ("coverage", "attribution", "blocked", "cf", "fills"):
        assert getattr(a, attr) is not getattr(b, attr), attr
    a.coverage.cycle(symbol="S", t=1.0, vouched=True, quoting=True)
    assert b.coverage.report()["total_cycles"] == 0


# --------------------------------------------------- the fill sequence

def _seq():
    L = FS.FillSequenceLedger(arm="PASSIVE_PAIR_V1")
    sq = L.candidate(cohort="BTC5m", symbol="S", t=0.0, tte_s=200)
    sq.note_rest(["YES"])
    return L, sq


def test_a_complement_price_and_a_complement_fill_are_counted_apart():
    """THE THESIS UNDER TEST. If prices appear and fills never follow, the
    0.34 basis was always a mirage."""
    L, _ = _seq()
    L.note_fills([{"leg": "YES", "qty": "0.01", "price": "0.30"}], t=5.0,
                 symbol="S")
    # a cheap complement is quoted, but never fills
    L.observe(book={"yes": {"bid": "0.31", "ask": "0.40"},
                    "no": {"bid": "0.33", "ask": "0.42"}}, t=40.0, symbol="S")
    L.close_symbol("S", residual_qty="0.01")
    r = L.report()
    assert r["complement_price_appearances"] == 1
    assert r["complement_fills"] == 0
    assert r["maker_to_maker_completion_rate_of_first_fills"] == 0.0
    assert r["matched_pairs"] == 0
    assert r["matched_basis_p50"] is None      # no basis without both legs
    assert r["residual_rate_of_first_fills"] == 1.0


def test_a_basis_is_reported_only_when_both_legs_actually_filled():
    L, _ = _seq()
    L.note_fills([{"leg": "YES", "qty": "0.01", "price": "0.30"}], t=5.0,
                 symbol="S")
    L.note_fills([{"leg": "NO", "qty": "0.01", "price": "0.33"}], t=50.0,
                 symbol="S")
    L.close_symbol("S")
    r = L.report()
    assert r["matched_pairs"] == 1
    assert r["matched_basis_p50"] == 0.63
    assert r["median_completion_time_s"] == 45.0
    assert r["matched_net_total_excl_subsidy"] > 0


def test_the_funnel_counts_every_step_separately():
    L, _ = _seq()
    L.candidate(cohort="BTC5m", symbol="S", t=1.0, tte_s=200)   # never rests
    L.note_fills([{"leg": "YES", "qty": "0.01", "price": "0.30"}], t=5.0,
                 symbol="S")
    L.close_symbol("S")
    r = L.report()
    assert r["candidate_states"] == 2
    assert r["orders_hypothetically_rested"] == 1
    assert r["first_fills"] >= 1


def test_capacity_is_reported_per_day():
    """A sequence that completes twice a day is a curiosity; forty times is a
    business."""
    L, _ = _seq()
    L.note_fills([{"leg": "YES", "qty": "0.01", "price": "0.30"}], t=5.0,
                 symbol="S")
    L.note_fills([{"leg": "NO", "qty": "0.01", "price": "0.33"}], t=50.0,
                 symbol="S")
    L.observe(book=WIDE, t=100.0, symbol="S")
    L.close_symbol("S")
    r = L.report()
    assert r["completions_per_day"] and r["completions_per_day"] > 0
    assert r["candidates_per_day"] is not None


def test_first_fill_legs_are_reported_separately():
    L = FS.FillSequenceLedger(arm="PASSIVE_PAIR_V1")
    for leg, px in (("YES", "0.30"), ("NO", "0.33")):
        sq = L.candidate(cohort="BTC5m", symbol=leg, t=0.0, tte_s=200)
        sq.note_rest([leg])
        L.note_fills([{"leg": leg, "qty": "0.01", "price": px}], t=5.0,
                     symbol=leg)
        L.close_symbol(leg)
    by = L.report()["by_first_fill_leg"]
    assert set(by) == {"YES", "NO"}


def test_a_sequence_is_never_fed_another_contracts_fills():
    L, _ = _seq()
    L.note_fills([{"leg": "YES", "qty": "0.01", "price": "0.99"}], t=5.0,
                 symbol="OTHER")
    L.close_symbol("S")
    assert L.report()["first_fills"] == 0


def test_nothing_in_the_funnel_is_labelled_realized():
    L, _ = _seq()
    L.close_symbol("S")
    r = L.report()
    assert any("nothing is submitted, filled, settled or realized" in x
               for x in r["labels"])
    assert any("price geometry" in x for x in r["labels"])
