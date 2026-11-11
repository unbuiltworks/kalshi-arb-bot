"""The cohort split — BTC15m is the maker target, the rest are another animal.

Averaging them produced every misleading cross-cohort figure this programme has
published. The other cohorts are retained, not deleted; they are simply kept
out of maker-policy statistics.
"""
from src.l5_shadow import cohorts as C


def test_btc15m_is_the_only_maker_policy_cohort():
    assert C.MAKER_COHORTS == ("BTC15m",)
    assert C.is_maker_cohort("BTC15m") is True
    for c in ("BTC5m", "ETH15m", "ETH5m"):
        assert C.is_maker_cohort(c) is False


def test_the_other_cohorts_are_labelled_not_discarded():
    for c in ("BTC5m", "ETH15m", "ETH5m"):
        assert C.regime(c) == "PREDOMINANTLY_TAKER"
    assert set(C.TAKER_REGIME_COHORTS) == {"BTC5m", "ETH15m", "ETH5m"}


def test_splitting_keeps_every_row_somewhere():
    rows = [{"cohort": "BTC15m"}, {"cohort": "BTC5m"}, {"cohort": "ETH5m"},
            {"cohort": "SOL5m"}]
    s = C.split(rows)
    assert len(s["maker_policy"]) == 1
    assert len(s["taker_regime"]) == 2
    assert len(s["unknown_cohort"]) == 1
    assert sum(len(v) for v in s.values()) == len(rows)


def test_an_unknown_cohort_is_not_silently_treated_as_maker():
    assert C.regime("SOL5m") == "UNKNOWN"
    assert C.is_maker_cohort("SOL5m") is False
    assert C.split([{"cohort": "SOL5m"}])["unknown_cohort"]


def test_a_report_carries_its_regime_so_no_reader_has_to_remember():
    r = C.label({"pair_cost": "0.96"}, cohort="BTC5m")
    assert r["regime"] == "PREDOMINANTLY_TAKER"
    assert r["in_maker_policy_aggregate"] is False
    assert "not the same quantity" in r["regime_note"]


def test_the_measured_rates_match_what_the_chain_and_feed_agreed():
    assert C.MEASURED_MAKER_RATE["BTC15m"] == 0.955
    assert all(C.MEASURED_MAKER_RATE[c] < 0.01 for c in C.TAKER_REGIME_COHORTS)
