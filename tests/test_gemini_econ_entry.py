"""Entry admission: the gates, and the orderings the brief demands."""
import pytest

from src.gemini_execution.econ import entry as E
from src.gemini_execution.econ import hazard as H

BOOK = {"yes": {"bid": "0.60", "ask": "0.65", "size": "5"},
        "no": {"bid": "0.35", "ask": "0.40", "size": "3"}}

KW = dict(min_alternation=0.3, max_largest_print_share=0.5,
          max_required_gap_ticks=3, max_reference_disagreement="0.05",
          min_prints=4, hazard_threshold=0.35, min_edge="0.005",
          max_spread="0.08")


def _th(**over):
    kw = dict(KW)
    kw.update(over)
    return E.Thresholds(cohort="BTC5m", **kw)


def _feat(prints, **over):
    kw = dict(tte_s=200, held_leg="YES", held_leg_price="0.60",
              required_complement_price="0.34", book=BOOK, prints=prints)
    kw.update(over)
    return H.features(**kw)


TWO_SIDED = [{"side": "buy", "qty": "1"}, {"side": "sell", "qty": "2"},
             {"side": "buy", "qty": "1"}, {"side": "sell", "qty": "1"}]

#: high volume, one direction, one dominant print -- the window the brief says
#: must NOT be preferred
TOXIC = [{"side": "buy", "qty": "500"}, {"side": "buy", "qty": "5"},
         {"side": "buy", "qty": "5"}, {"side": "buy", "qty": "5"}]


def _admit(feat, th=None, **over):
    kw = dict(feat=feat, thresholds=th or _th(), proposed_price="0.60",
              inventory={"directional_residual": "0"})
    kw.update(over)
    return E.admit_entry(**kw)


def test_the_quiet_reversible_window_beats_the_loud_one_directional_window():
    """THE ORDERING THE BRIEF NAMES, asserted directly.

    TOXIC carries ~50x the volume of TWO_SIDED and must still be refused.
    """
    good = _admit(_feat(TWO_SIDED))
    bad = _admit(_feat(TOXIC))
    assert good["admitted"] is True
    assert bad["admitted"] is False
    assert "FLOW_REVERSIBLE" in bad["failed_gates"]
    assert "NOT_CONCENTRATED" in bad["failed_gates"]


def test_volume_is_not_a_gate_at_all():
    """If volume were an input, scaling every print would change the verdict."""
    small = _admit(_feat([{"side": "buy", "qty": "1"},
                          {"side": "sell", "qty": "1"},
                          {"side": "buy", "qty": "1"},
                          {"side": "sell", "qty": "1"}]))
    large = _admit(_feat([{"side": "buy", "qty": "1000"},
                          {"side": "sell", "qty": "1000"},
                          {"side": "buy", "qty": "1000"},
                          {"side": "sell", "qty": "1000"}]))
    assert small["admitted"] == large["admitted"] is True
    names = [g["gate"] for g in small["gates"]]
    assert not any("VOLUME" in n for n in names)


def test_an_unpairable_entry_price_is_refused_at_its_origin():
    """The gate that stops a 1.13 pair BEFORE the first fill, not after it."""
    r = _admit(_feat(TWO_SIDED), proposed_price="0.995")
    assert r["admitted"] is False
    assert "ECONOMIC_FEASIBILITY" in r["failed_gates"]


def test_a_complement_too_far_away_is_refused():
    f = _feat(TWO_SIDED, required_complement_price="0.05")   # ~30 ticks away
    r = _admit(f)
    assert r["admitted"] is False
    assert "COMPLEMENT_REACHABLE" in r["failed_gates"]


def test_a_flared_spread_is_refused():
    wide = {"yes": {"bid": "0.40", "ask": "0.65", "size": "5"},
            "no": {"bid": "0.35", "ask": "0.60", "size": "3"}}
    r = _admit(_feat(TWO_SIDED, book=wide))
    assert r["admitted"] is False
    assert "SPREAD" in r["failed_gates"]


def test_a_one_sided_book_is_refused():
    one = {"yes": {"bid": "0.60", "ask": "0.65", "size": "5"},
           "no": {"bid": "0.35"}}          # no ask quoted on the complement
    r = _admit(_feat(TWO_SIDED, book=one))
    assert r["admitted"] is False
    assert "TWO_SIDED_QUOTE" in r["failed_gates"]


def test_too_few_prints_is_not_evidence_of_direction():
    r = _admit(_feat([{"side": "buy", "qty": "1"}]))
    assert r["admitted"] is False
    assert "FLOW_REVERSIBLE" in r["failed_gates"]


def test_unreadable_inventory_refuses_because_unreadable_is_not_flat():
    r = _admit(_feat(TWO_SIDED), inventory={})
    assert r["admitted"] is False
    assert "INVENTORY_HEADROOM" in r["unknown_gates"]


def test_a_calibrated_low_hazard_refuses_but_an_uncalibrated_one_does_not():
    """The asymmetry that lets the sample bootstrap without lying about it."""
    cold = {"calibrated": False, "cell": "x", "n": 3,
            "why": "UNCALIBRATED"}
    r_cold = _admit(_feat(TWO_SIDED), hazard_estimate=cold)
    assert r_cold["admitted"] is True
    assert "HAZARD" in r_cold["waived_unknown_gates"]

    low = {"calibrated": True, "p": 0.05, "cell": "x", "n": 100}
    r_low = _admit(_feat(TWO_SIDED), hazard_estimate=low)
    assert r_low["admitted"] is False
    assert "HAZARD" in r_low["failed_gates"]


def test_live_phase_carries_no_advisory_gates_structurally():
    """Not a convention -- the constructor empties them, so no configuration
    commits capital on a gate that was merely being watched."""
    live = _th(phase=E.LIVE, advisory_gates=("HAZARD", "REFERENCE_AGREEMENT"))
    assert live.advisory_gates == ()
    r = _admit(_feat(TWO_SIDED), th=live)
    assert r["admitted"] is False
    assert set(r["unknown_gates"]) >= {"REFERENCE_AGREEMENT", "HAZARD"}
    assert r["waived_unknown_gates"] == []


def test_an_advisory_gate_waives_unknown_but_never_a_measured_failure():
    """'could not measure' is waived; 'measured and failed' never is."""
    th = _th(advisory_gates=("HAZARD",))
    low = {"calibrated": True, "p": 0.01, "cell": "x", "n": 100}
    r = _admit(_feat(TWO_SIDED), th=th, hazard_estimate=low,
               reference_disagreement="0.001")
    assert r["admitted"] is False
    assert "HAZARD" in r["failed_gates"]


def test_reference_disagreement_refuses_when_it_is_measured_and_large():
    r = _admit(_feat(TWO_SIDED), reference_disagreement="0.40")
    assert r["admitted"] is False
    assert "REFERENCE_AGREEMENT" in r["failed_gates"]


def test_an_unknown_phase_is_rejected_rather_than_defaulted():
    with pytest.raises(ValueError):
        _th(phase="MAYBE")


def test_every_refusal_names_the_gate_that_refused():
    r = _admit(_feat(TOXIC))
    assert r["why"] and "refused by" in r["why"]
    for g in r["failed_gates"]:
        assert g in r["why"]
