"""The flow-evidence ladder, deduplication, and the book-move classifier."""
import pytest

from src.gemini_execution.econ import book_move as BM
from src.gemini_execution.econ import entry as EN
from src.gemini_execution.econ import fill_sequence as FS
from src.gemini_execution.econ import hazard as HZ
from src.gemini_execution.econ import policy as PO

BOOK = {"yes": {"bid": "0.30", "ask": "0.55"},
        "no": {"bid": "0.33", "ask": "0.58"}}
PRINTS = [{"side": "buy", "qty": "1"}, {"side": "sell", "qty": "1"},
          {"side": "buy", "qty": "1"}, {"side": "sell", "qty": "1"},
          {"side": "buy", "qty": "1"}]


def _admit(n_prints, rung, book=BOOK):
    pol = PO.policy_for("BTC5m", variant=rung)
    f = HZ.features(tte_s=200, held_leg="YES", held_leg_price=book["yes"]["bid"],
                    required_complement_price="0.34", book=book,
                    prints=PRINTS[:n_prints])
    return EN.admit_entry(feat=f, thresholds=pol.thresholds,
                          proposed_price=book["yes"]["bid"],
                          inventory={"directional_residual": "0"},
                          flow_vouched=True)


# ------------------------------------------------------------- the ladder

def test_the_rungs_differ_only_in_the_print_minimum():
    """Anything else differing would mean the ladder measures two changes."""
    pols = {r: PO.policy_for("BTC5m", variant=r) for r in PO.FLOW_LADDER}
    assert {r: p.thresholds.min_prints for r, p in pols.items()} == {
        "F0": 0, "F1": 1, "F2": 2, "F5": 5}
    ref = pols["F5"]
    for r, p in pols.items():
        for fld in ("min_alternation", "max_largest_print_share",
                    "max_required_gap_ticks", "max_reference_disagreement",
                    "hazard_threshold", "min_edge", "max_spread",
                    "spread_mode"):
            assert getattr(p.thresholds, fld) == getattr(ref.thresholds, fld), (
                "%s differs on %s" % (r, fld))
        for fld in ("quote_band_s", "entry_until_tte_s", "mixed_until_tte_s",
                    "withdraw_below_tte_s", "max_residual",
                    "toxicity_horizon_s", "rescue_below_tte_s",
                    "flow_window_s"):
            assert getattr(p, fld) == getattr(ref, fld), (
                "%s differs on %s" % (r, fld))


def test_every_rung_uses_the_identical_passive_pair_rule():
    for r in PO.FLOW_LADDER:
        p = PO.policy_for("BTC5m", variant=r)
        assert p.thresholds.spread_mode == EN.SPREAD_PASSIVE_PAIR


def test_f5_matches_the_production_arm_it_stands_for():
    """F5 is the production hypothesis and must not have drifted."""
    prod = PO.policy_for("BTC5m", variant=PO.PASSIVE_PAIR_VARIANT)
    f5 = PO.policy_for("BTC5m", variant="F5")
    assert f5.thresholds.min_prints == prod.thresholds.min_prints == 5
    assert f5.thresholds.min_alternation == prod.thresholds.min_alternation


def test_the_ladder_admits_monotonically_as_the_minimum_drops():
    """With one print, the looser rungs admit and the tighter ones do not."""
    got = {r: _admit(1, r)["admitted"] for r in ("F0", "F1", "F2", "F5")}
    assert got == {"F0": True, "F1": True, "F2": False, "F5": False}


def test_zero_prints_is_admitted_only_by_f0():
    got = {r: _admit(0, r)["admitted"] for r in ("F0", "F1", "F2", "F5")}
    assert got == {"F0": True, "F1": False, "F2": False, "F5": False}


def test_a_measured_flow_failure_still_refuses_on_every_rung():
    """Only UNIDENTIFIABILITY is waived on the loose rungs. A statistic that
    can be computed and fails refuses everywhere, or the ladder would be a
    licence to ignore evidence rather than a test of it."""
    toxic = [{"side": "buy", "qty": "500"}] + [{"side": "buy", "qty": "1"}] * 4
    for rung in ("F0", "F1", "F2", "F5"):
        pol = PO.policy_for("BTC5m", variant=rung)
        f = HZ.features(tte_s=200, held_leg="YES", held_leg_price="0.30",
                        required_complement_price="0.34", book=BOOK,
                        prints=toxic)
        r = EN.admit_entry(feat=f, thresholds=pol.thresholds,
                           proposed_price="0.30",
                           inventory={"directional_residual": "0"},
                           flow_vouched=True)
        assert r["admitted"] is False, rung
        assert {"FLOW_REVERSIBLE", "NOT_CONCENTRATED"} & set(r["failed_gates"])


def test_all_rungs_agree_once_flow_is_identifiable():
    """At 5 prints every statistic is computable, so the rungs must return the
    same verdict -- the ladder differs only where evidence is absent."""
    assert len({_admit(5, r)["admitted"] for r in PO.FLOW_LADDER}) == 1


def test_both_cohorts_have_every_rung():
    for c in ("BTC5m", "BTC15m"):
        for r in PO.FLOW_LADDER:
            assert PO.policy_for(c, variant=r) is not None


# ------------------------------------------------------- deduplication

def test_the_same_market_observation_shares_an_id_across_arms():
    """A state admitted by F0, F1 and F2 is ONE observation seen three times.
    Counting it three times would inflate the looser rungs by construction."""
    a = FS.observation_id(symbol="S", t=100.1, leg="YES", price="0.30")
    b = FS.observation_id(symbol="S", t=100.9, leg="YES", price="0.30")
    assert a == b


def test_different_cycles_legs_or_prices_are_different_observations():
    base = dict(symbol="S", t=100.1, leg="YES", price="0.30")
    a = FS.observation_id(**base)
    assert FS.observation_id(**{**base, "t": 102.0}) != a
    assert FS.observation_id(**{**base, "leg": "NO"}) != a
    assert FS.observation_id(**{**base, "price": "0.31"}) != a
    assert FS.observation_id(**{**base, "symbol": "T"}) != a


def test_the_funnel_reports_unique_observations():
    L = FS.FillSequenceLedger(arm="PP_F0")
    for _ in range(3):                       # same cycle, three arms' worth
        L.candidate(cohort="BTC5m", symbol="S", t=10.0, tte_s=200,
                    leg="YES", price="0.30", book=BOOK)
    L.close_symbol("S")
    r = L.report()
    assert r["candidate_states"] == 3
    assert r["unique_observations"] == 1


# ------------------------------------------------------- the book move

def _snap(yb, ya, nb, na):
    return BM.snapshot({"yes": {"bid": yb, "ask": ya},
                        "no": {"bid": nb, "ask": na}})


def test_both_bids_rising_is_compression_not_a_directional_move():
    """THE OBSERVATION THIS EXPLAINS: both legs marked favourably at once,
    which a directional move cannot produce."""
    r = BM.classify(_snap("0.30", "0.55", "0.33", "0.58"),
                    _snap("0.36", "0.52", "0.39", "0.55"))
    assert r["klass"] == BM.COMPRESSION
    assert r["d_bid_sum_ticks"] > 0


def test_opposite_moves_with_a_flat_sum_are_directional():
    r = BM.classify(_snap("0.30", "0.55", "0.33", "0.58"),
                    _snap("0.35", "0.60", "0.28", "0.53"))
    assert r["klass"] == BM.DIRECTIONAL


def test_both_bids_falling_is_expansion():
    r = BM.classify(_snap("0.30", "0.55", "0.33", "0.58"),
                    _snap("0.24", "0.58", "0.27", "0.61"))
    assert r["klass"] == BM.EXPANSION


def test_an_unreadable_leg_is_unknown_never_defaulted():
    r = BM.classify(_snap("0.30", "0.55", "0.33", "0.58"),
                    BM.snapshot({"yes": {"bid": "0.36"}}))
    assert r["klass"] == BM.UNKNOWN
    assert "NOT defaulted" in r["why"]


def test_a_book_that_did_not_move_is_not_a_tiny_compression():
    r = BM.classify(_snap("0.30", "0.55", "0.33", "0.58"),
                    _snap("0.30", "0.55", "0.33", "0.58"))
    assert r["klass"] != BM.COMPRESSION


def test_the_bid_sum_is_what_a_passive_pair_costs():
    s = _snap("0.30", "0.55", "0.33", "0.58")
    assert s["bid_sum"] == "0.63"
    assert s["yes_spread"] == "0.25"


def test_the_census_separates_wide_books():
    """The question is whether WIDE books compress after we would post."""
    c = BM.BookMoveCensus()
    for _ in range(3):
        c.record(horizon_s=60, wide=True, klass=BM.COMPRESSION,
                 d_bid_sum_ticks=8.0, bid_sum_t0="0.63")
    c.record(horizon_s=60, wide=False, klass=BM.EXPANSION,
             d_bid_sum_ticks=-3.0, bid_sum_t0="0.97")
    h = c.report()["by_horizon"]["60"]
    assert h["n"] == 4 and h["wide_n"] == 3
    assert h["wide_compression_share"] == 1.0
    assert h["compression_share"] == 0.75


def test_the_sequence_captures_the_dual_rung_book_at_t0():
    L = FS.FillSequenceLedger(arm="PP_F0")
    sq = L.candidate(cohort="BTC5m", symbol="S", t=0.0, tte_s=200,
                     leg="YES", price="0.30", book=BOOK)
    assert sq.book_t0["bid_sum"] == "0.63"
    assert sq.book_t0["yes_spread"] == "0.25"
    assert sq.book_t0["no_spread"] == "0.25"
