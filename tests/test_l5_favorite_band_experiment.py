"""The late-window favorite-band experiment is OFFLINE and cannot promote.

No network and no corpus: every case builds its own synthetic fills.
"""
import ast
import os
import random

import pytest

from analysis.l5 import favorite_band_experiment as FB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fills(n_windows, px, win_rate, t_off=700.0, seed=7, per_window=4):
    """``n_windows`` windows of favorite BUY fills at ``px``."""
    rng = random.Random(seed)
    out = []
    for i in range(n_windows):
        ws = 1_700_000_000.0 + 900.0 * i
        won = rng.random() < win_rate
        for k in range(per_window):
            out.append({"slug": "btc-updown-15m-%d" % int(ws), "ws": ws,
                        "t_off": t_off + k, "side": "BUY", "outcome": "Up",
                        "px": px, "size": 1.0,
                        "winner": "Up" if won else "Down"})
    return out


# ------------------------------------------------------------ definitions
def test_the_four_owner_named_bands_and_nothing_else():
    assert [b for b, _lo, _hi in FB.BANDS] == ["60c", "70c", "80c", "90c"]
    for (_a, lo, hi), (_b, lo2, _hi2) in zip(FB.BANDS, FB.BANDS[1:]):
        assert hi == lo2 and round(hi - lo, 6) == 0.10


def test_selection_is_by_band_and_by_time_and_buys_only():
    ts = fills(3, 0.65, 1.0, t_off=100.0) + fills(3, 0.65, 1.0, t_off=650.0,
                                                  seed=9)
    ts.append(dict(ts[0], side="SELL", t_off=800.0))
    ts.append(dict(ts[0], px=0.75, t_off=800.0))
    late = FB.select(ts, 0.60, 0.70, 600.0)
    assert late and all(t["t_off"] >= 600.0 and t["side"] == "BUY"
                        and 0.60 <= t["px"] < 0.70 for t in late)
    assert len(FB.select(ts, 0.60, 0.70, 0.0)) == 24


def test_split_is_chronological_and_on_windows_not_trades():
    ts = fills(50, 0.85, 0.9)
    is_w, oos_w = FB.split_windows(ts)
    assert len(is_w) == 30 and len(oos_w) == 20 and not (is_w & oos_w)
    last_is = max(t["ws"] for t in ts if t["slug"] in is_w)
    first_oos = min(t["ws"] for t in ts if t["slug"] in oos_w)
    assert last_is < first_oos


# ------------------------------------------------------- conservative EV
def test_conservative_uses_the_lower_bound_net_of_fee_never_the_mean():
    edge = {"windows": 100, "contracts": 400.0, "window_mean_edge": 0.05,
            "ci95": [0.004, 0.096]}
    assert FB.conservative(edge, 0.0)["positive"] is True
    c = FB.conservative(edge, 0.01)
    assert c["conservative_net"] == pytest.approx(-0.006)
    assert c["positive"] is False          # a positive MEAN is not enough


def test_no_data_is_never_positive():
    c = FB.conservative(None, 0.0)
    assert c["positive"] is False and c["windows"] == 0


def test_a_band_needs_both_halves_not_one():
    """A strong early half and a null late half is a fitted band."""
    good = fills(120, 0.85, 0.99, seed=1)
    bad = fills(80, 0.85, 0.70, seed=2)
    for i, t in enumerate(bad):
        t["ws"] += 900.0 * 1000
        t["slug"] = "btc-updown-15m-%d" % int(t["ws"])
    ts = good + bad
    is_w, oos_w = FB.split_windows(ts)
    r = FB.evaluate_band(ts, 0.80, 0.90, 600.0, is_w, oos_w)
    assert r["maker"]["IS"]["positive"] is True
    assert r["maker"]["OOS"]["positive"] is False
    assert r["maker"]["verdict"] == "NOT SUPPORTED"


def test_a_fair_priced_favorite_is_not_supported():
    ts = fills(400, 0.85, 0.85, seed=3)
    is_w, oos_w = FB.split_windows(ts)
    r = FB.evaluate_band(ts, 0.80, 0.90, 0.0, is_w, oos_w)
    assert r["maker"]["verdict"] == "NOT SUPPORTED"


def test_taker_is_never_easier_than_maker():
    ts = fills(400, 0.85, 0.93, seed=4)
    is_w, oos_w = FB.split_windows(ts)
    r = FB.evaluate_band(ts, 0.80, 0.90, 0.0, is_w, oos_w)
    for half in ("all", "IS", "OOS"):
        assert (r["taker"][half]["conservative_net"]
                < r["maker"][half]["conservative_net"])


# ------------------------------------------------------------ promotion
@pytest.mark.parametrize("win_rate", [0.50, 0.85, 0.999])
def test_no_result_can_promote(win_rate):
    """Even a band that L5 history supports is NO PROMOTION: the evidence is
    Polymarket's, the action is directional, and approval is the owner's."""
    r = FB.run(fills(300, 0.85, win_rate, seed=5))
    assert r["promotion"] == "NO PROMOTION"
    assert "not PMUS" in r["evidence_class"]
    assert any("owner approval" in w for w in r["why"])


def test_own_cross_check_can_veto_and_fails_safe_when_unreadable(tmp_path):
    own = FB.own_cross_check()
    assert own["available"] is True
    assert float(own["favourite_ge_0.80"]["per_unit"]) < 0     # realized loss
    assert own["legs_held_at_favourite_prices"] < own["legs_needed_for_2_sigma"]
    assert FB.own_cross_check(str(tmp_path / "absent.json")) == {
        "available": False, "error": "FileNotFoundError"}


# ------------------------------------------------------------- isolation
def test_the_experiment_has_no_path_to_an_order():
    with open(FB.__file__) as fh:
        tree = ast.parse(fh.read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "json", "typing", "analysis.l5"}
    assert "os" not in mods                # reads no environment variable


def test_nothing_under_src_or_the_executor_image_references_it():
    hits = []
    for base in ("src", os.path.join("deploy", "l5_pmus_live")):
        for dirpath, _d, files in os.walk(os.path.join(ROOT, base)):
            for f in files:
                if not f.endswith((".py", "Dockerfile", ".json")):
                    continue
                with open(os.path.join(dirpath, f), errors="ignore") as fh:
                    if "favorite_band_experiment" in fh.read():
                        hits.append(os.path.join(dirpath, f))
    assert hits == []
