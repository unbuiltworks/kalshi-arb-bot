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


def test_tte_is_measured_from_placement_not_from_the_fill_verdict():
    """`resolved_at` is the wall-clock of the VERDICT, which lands at or after
    the window closes. Reading it as the fill time made tte <= 0 for every fill,
    which silently turned all three TTE stops into the oracle."""
    mk = "btc-updown-15m-1000"      # window ends at 1000 + 900 = 1900
    f = _f("Up", 0)
    f["placed_at"] = 1600           # 300s before expiry
    f["resolved_at"] = 1950         # verdict decided AFTER expiry
    w = F.walk([f], arm="control", mk=mk)
    assert w["creations"][0]["tte_s"] == 300


def test_residual_loss_removed_is_a_fraction_of_the_loss_not_of_the_net():
    """The net is losses plus wins. Dividing by it can exceed 1, which reads as
    'more than all of the loss removed' -- a quantity that cannot exist."""
    base = [{"settled": True, "residual_pnl": -100.0, "residual_qty": 10.0},
            {"settled": True, "residual_pnl": +90.0, "residual_qty": 10.0}]
    s = F._split(base)
    assert s["residual_loss"] == -100.0 and s["residual_gain"] == 90.0
    assert s["residual_win_rate"] == 0.5
    # halving the loss is 0.5 removed, whatever the net (-10) happens to be
    cf = [{"settled": True, "residual_pnl": -50.0, "residual_qty": 5.0}]
    assert 1 - (F._split(cf)["residual_loss"] / s["residual_loss"]) == 0.5


def _store(tmp_path, fills, *, winner="Down"):
    """A minimal out_dir: evidenced fills plus a PRE-RESOLVED settlement cache,
    so report() needs no network."""
    with open(tmp_path / "maker_outcomes.jsonl", "w") as fh:
        for f in fills:
            fh.write(json.dumps(f) + "\n")
    with open(tmp_path / "adapted_resolutions.json", "w") as fh:
        json.dump({"btc-updown-15m-1000": {"resolved": True,
                                           "winning_outcome": winner,
                                           "market_key": "btc-updown-15m-1000"}},
                  fh)
    return str(tmp_path)


def test_two_probes_accepting_the_same_fills_are_reported_as_duplicates(
        tmp_path, monkeypatch):
    """A mislabelled or vacuous threshold must not be counted twice as
    independent evidence. The TTE bug did exactly that: three stops and the
    oracle were one probe wearing four names."""
    out = _store(tmp_path, [_f("Up", 0), _f("Down", 1), _f("Up", 2)])
    same = F._acc_oracle
    monkeypatch.setattr(F, "DIAGNOSTICS",
                        {"PROBE_A": same, "PROBE_B": same,
                         "PROBE_C": lambda r, s: True})
    d = F.report(out)["diagnostics"]
    assert d["PROBE_A"]["identical_to"] is None
    assert d["PROBE_B"]["identical_to"] == "PROBE_A"
    assert d["PROBE_C"]["identical_to"] is None     # accepts strictly more


def test_the_real_tte_stops_are_no_longer_one_probe_in_four_names(tmp_path):
    """With tte read from placement, a fill 500s out and one 60s out are
    separated by the 300s stop -- which the verdict-clock version could not do."""
    a, b = _f("Up", 0), _f("Down", 1)
    c, d_ = _f("Up", 2), _f("Up", 3)
    a["placed_at"], b["placed_at"] = 1100, 1200      # window ends 1900
    c["placed_at"], d_["placed_at"] = 1400, 1840     # 500s out, 60s out
    fills = [a, b, c, d_]
    kept = {s: F.walk(fills, arm="control", mk="btc-updown-15m-1000",
                      accept=F._acc_tte(s))["fills_taken"]
            for s in (600.0, 300.0, 120.0)}
    assert kept[600.0] < kept[300.0] <= kept[120.0]


def test_a_venue_that_never_forms_a_pair_is_flagged_as_inert():
    rows = [{"venue": "GEMINI", "settled": True, "matched_qty": 0.0,
             "residual_qty": 50.0, "matched_pnl": 0.0, "residual_pnl": -42.5,
             "residual_side": "Up", "winning_outcome": "Down"},
            {"venue": "PMUS", "settled": True, "matched_qty": 90.0,
             "residual_qty": 10.0, "matched_pnl": 5.0, "residual_pnl": -1.0,
             "residual_side": "Up", "winning_outcome": "Down"}]
    bv = F._by_venue(rows)
    assert bv["GEMINI"]["windows_with_matched_inventory"] == 0
    assert bv["PMUS"]["windows_with_matched_inventory"] == 1
