"""The failure decomposition must classify by a fixed rule and never tune."""
import json
import pytest

from analysis.l5.adapted import failure as F


def test_the_classification_rule_is_fixed_and_discriminates():
    assert F.classify([-100, -2, -1, -1]) == "TAIL_BLOWUPS"
    assert F.classify([-10] * 12) == "BROAD_LEAKAGE"
    assert F.classify([]) == "BROAD_LEAKAGE"
    # one window at 25% of the loss, top3 under 60% -> neither extreme
    assert F.classify([-25, -15, -15, -12, -12, -11, -10]) == "MIXED"


def test_concentration_reports_shares_not_just_a_verdict():
    c = F._concentration([-60, -20, -10, -5, -5])
    assert c["n_losing"] == 5
    assert c["top1_share_of_loss"] == pytest.approx(0.60)
    assert c["top3_share_of_loss"] == pytest.approx(0.90)
    assert c["worst_window_loss"] == -60


def test_winning_residual_windows_are_excluded_from_the_loss_concentration():
    c = F._concentration([-10, +40, -10])
    assert c["n_losing"] == 2
    assert c["total_residual_loss"] == -20


def _f(side, i, qty="10", px="0.50"):
    return {"schema": "x/maker_outcome", "venue": "PMUS",
            "market_key": "btc-updown-15m-1000", "desired_outcome": side,
            "evidenced_fill": True, "resolved_at": 1000 + i,
            "intent_id": f"i{i}", "quote_price": px, "filled_qty": qty,
            "fill_criteria": {"strongest_satisfied": "VOLUME_AHEAD_CLEARED"}}


def test_a_residual_creation_records_the_state_known_at_that_moment(tmp_path):
    fills = [_f("Up", 0), _f("Down", 1), _f("Up", 2)]
    w = F.walk(fills, arm="control", mk="btc-updown-15m-1000")
    # fill 0 grows residual from 0 to 10; fill 1 returns it to 0; fill 2 regrows
    assert [c["grows_residual"] for c in w["creations"]] == [True, True]
    last = w["creations"][-1]
    assert last["after_two_sided"] is True      # matched existed by then
    assert last["run_len"] == 1                 # it followed a Down
    assert last["price_minus_vwap"] == pytest.approx(0.0)
    assert w["max_abs_imbalance"] == pytest.approx(10.0)


def test_the_oracle_forbids_growing_residual_once_two_sided(tmp_path):
    fills = [_f("Up", 0), _f("Down", 1), _f("Up", 2), _f("Up", 3)]
    free = F.walk(fills, arm="control", mk="btc-updown-15m-1000")
    orc = F.walk(fills, arm="control", mk="btc-updown-15m-1000",
                 accept=F._acc_oracle)
    assert free["fills_taken"] == 4
    assert orc["fills_taken"] == 2          # the two post-match Up buys refused
    assert orc["state"]["up_qty"] == orc["state"]["dn_qty"]   # perfectly flat


def test_a_diagnostic_never_blocks_a_matching_fill():
    """Every probe targets residual growth only. Refusing a fill that BUILDS
    matched inventory would remove the business rather than the risk."""
    fills = [_f("Up", 0), _f("Down", 1)]
    for name, acc in F.DIAGNOSTICS.items():
        w = F.walk(fills, arm="control", mk="btc-updown-15m-1000", accept=acc)
        assert w["state"]["dn_qty"] > 0, name    # the matching Down survived


def test_no_threshold_is_selected_anywhere():
    """The sweeps are reported in full; nothing picks a winner."""
    src = (F.__doc__ or "") + open(F.__file__).read()
    assert "tunes nothing" in src
    assert len(F.MAX_RESIDUAL_USD) > 1 and len(F.TTE_STOPS) > 1
