"""The portfolio controller's replay and the fill-value surface.

No corpus and no network: every case builds its own path. The exceptions
read the committed result files and nothing else.
"""
import ast
import inspect
import json
import os
import random

import pytest

from analysis.l5 import edge_comparison as EC
from analysis.l5 import maker_fill_value_surface as SF
from analysis.l5 import portfolio_controller_replay as PR
from src import l5_portfolio_controller as PC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP, DOWN = PC.UP, PC.DOWN


def path(*pts):
    return [(float(t), float(m)) for t, m in pts]


def random_path(seed, n=160):
    rng = random.Random(seed)
    m, out = 0.50, []
    for i in range(n):
        m = min(0.97, max(0.03, m + rng.choice((-0.03, -0.02, -0.01, 0.0,
                                                0.01, 0.02, 0.03))))
        out.append((5.0 + i * 5.0, round(m, 2)))
    return out


def ctl(**kw):
    return PR.make_controller(PC.Params(**kw).checked())


def run(p, winner="Up", model=None, through=1, **kw):
    return EC.run_window(p, winner, ctl(**kw), model=model, through=through)


ZERO = dict(margin=0.0, adverse=0.0, risk=0.0)


# ------------------------------------------------- it is the production code
def test_the_replay_drives_the_production_decision_function():
    src = inspect.getsource(PR.make_controller)
    assert "PC.decide(" in src and "PC.portfolio(" in src
    assert PR.PC is PC


def test_the_replays_book_is_the_rulers_book():
    for m in (0.50, 0.505, 0.37, 0.93):
        b = PR.book_for(m)
        assert b["bid"] == pytest.approx(EC._down(m - EC.HALF))
        assert round(1 - b["ask"], 10) == pytest.approx(
            EC._down(1 - m - EC.HALF))
        assert b["bid"] < b["ask"]


# ------------------------------------------------------------- the behaviour
def test_both_sides_rest_at_the_touch_and_an_oscillation_makes_a_pair():
    r = run(path((10, 0.50), (20, 0.47), (30, 0.53), (40, 0.50)), **ZERO)
    assert [(f["side"], f["px"]) for f in r["log"][:2]] == [
        (UP, pytest.approx(0.49)), (DOWN, pytest.approx(0.52))]
    assert r["pairs"] >= 1


def test_a_filled_side_is_replenished_from_the_current_book():
    r = run(path((10, 0.50), (20, 0.47), (30, 0.44), (40, 0.41)),
            max_unmatched=6, **ZERO)
    ups = [f["px"] for f in r["log"] if f["side"] == UP]
    assert len(ups) >= 2 and ups == sorted(ups, reverse=True)


@pytest.mark.parametrize("cap", [1, 3, 6])
@pytest.mark.parametrize("seed", range(10))
def test_unmatched_inventory_never_exceeds_its_cap(cap, seed):
    r = run(random_path(seed), max_unmatched=cap, max_qty=min(3, cap),
            size_per_cent=0.25, **ZERO)
    assert r["max_unmatched"] <= cap


def test_the_haircut_keeps_quotes_behind_the_touch():
    p = random_path(3)
    near = run(p, **ZERO)
    far = run(p, margin=0.03, adverse=0.03, risk=0.0)
    assert far["fills"] < near["fills"]


def test_without_a_model_a_large_haircut_quotes_nothing():
    """The book's own mid less six cents is more than six ticks back."""
    r = run(random_path(4), margin=0.04, adverse=0.04, risk=0.0)
    assert r["fills"] == 0


def test_a_model_that_favors_a_side_accumulates_that_side():
    p = random_path(6)
    r = run(p, model=lambda t: 0.95, margin=0.03, adverse=0.03, risk=0.0,
            max_unmatched=6, max_qty=3, size_per_cent=0.25)
    adds = [f for f in r["log"] if f["tag"].endswith("ADD")]
    assert adds and all(f["side"] == UP for f in adds)


def test_the_risk_charge_holds_openings_back_and_keeps_hedging():
    """A charge on unmatched contracts moves opening quotes back from the
    touch -- fewer openings fill -- while every opening is still hedged."""
    plain = {"ADD": 0, "HEDGE": 0}
    skew = {"ADD": 0, "HEDGE": 0}
    for seed in range(12):
        p = random_path(seed)
        for tot, kw in ((plain, dict(risk=0.0)), (skew, dict(risk=0.04))):
            r = PR.split_roles([run(p, margin=0, adverse=0,
                                    max_unmatched=6, **kw)])
            tot["ADD"] += r["ADD"]
            tot["HEDGE"] += r["HEDGE"]
    assert 0 < skew["ADD"] < plain["ADD"]
    assert skew["HEDGE"] >= 0.9 * skew["ADD"]


@pytest.mark.parametrize("name", sorted(PR.PRESETS))
@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("through", [0, 1])
def test_the_parts_add_up_to_the_cash_result(name, seed, through):
    winner = "Up" if seed % 2 else "Down"
    r = EC.run_window(
        random_path(seed), winner,
        PR.make_controller(PC.Params(**PR.PRESETS[name]).checked()),
        model=lambda t: 0.5 + 0.3 * ((seed % 3) - 1), through=through)
    cash = sum(1.0 - c for c in r["pair_costs"]) + sum(r["unmatched_results"])
    assert r["net"] == pytest.approx(cash, abs=1e-9)
    assert sum(r["buckets"].values()) == pytest.approx(cash, abs=1e-9)


@pytest.mark.parametrize("name", sorted(PR.PRESETS))
@pytest.mark.parametrize("seed", range(4))
def test_what_happens_later_cannot_change_what_was_done_earlier(name, seed):
    p = random_path(seed)
    cut_t = p[80][0]
    other = p[:81] + [(t, 0.03 if m >= 0.5 else 0.97) for t, m in p[81:]]
    mk = lambda: PR.make_controller(PC.Params(**PR.PRESETS[name]).checked())
    a = EC.run_window(p, "Up", mk(), model=lambda t: 0.6)
    b = EC.run_window(other, "Up", mk(), model=lambda t: 0.6)
    early = lambda r: [f for f in r["log"] if f["t"] <= cut_t]
    assert early(a) == early(b)


# -------------------------------------------------------- honesty of the run
def test_tuning_reads_the_first_part_only():
    src = inspect.getsource(PR.run)
    tuning = src[src.index("tried = []"):src.index('out["frontier"] = []')]
    assert 'score(pan["IS"], ctl(kw))' in tuning
    assert 'score(pan["OOS"]' not in tuning
    # the frontier picks each band's setting on the first part, and only
    # then scores that one setting on the second
    fr = src[src.index('out["frontier"] = []'):src.index(
        "for name, sf in cands.items()")]
    assert fr.index('max(band, key=lambda x: x[1]["net_per_window"])') < \
        fr.index('score(pan["OOS"], ctl(kw))')
    assert fr.count('score(pan["OOS"]') == 1


def test_the_presets_include_an_aggressive_one_and_are_fixed():
    agg = PR.PRESETS["AGGRESSIVE (haircut 0, touch, size on edge)"]
    assert agg["margin"] == 0 and agg["adverse"] == 0 and agg["risk"] == 0
    assert agg["max_unmatched"] == 6 and agg["size_per_cent"] > 0
    assert len(PR.GRID) == 240


def test_a_setting_that_does_not_trade_cannot_win_the_tuning():
    assert PR.MIN_FILLS_PER_WINDOW >= 1.0
    src = inspect.getsource(PR.run)
    assert 'if s["fills_per_window"] >= MIN_FILLS_PER_WINDOW' in src


def test_the_analysis_is_not_imported_by_production():
    for base, _d, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if f.endswith(".py"):
                txt = open(os.path.join(base, f), errors="ignore").read()
                assert "portfolio_controller_replay" not in txt, f
                assert "maker_fill_value_surface" not in txt, f
    for mod in (PR, SF):
        tree = ast.parse(open(mod.__file__).read())
        tops = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                tops.add((node.module or "").split(".")[0])
        assert not ({"requests", "urllib", "socket", "http", "subprocess"}
                    & tops)


# ----------------------------------------------------- the fill-value surface
def test_a_surface_fill_needs_a_later_trade_through():
    # the UP bid sits at 0.49: a print AT 0.49 is a touch and fills nothing
    assert [x for x in SF.fills(path((10, 0.50), (20, 0.49)), "Up", depth=0)
            if x["side"] == UP] == []
    # a print at 0.47 has traded through it
    ups = [x for x in SF.fills(path((10, 0.50), (20, 0.47)), "Up", depth=0)
           if x["side"] == UP]
    assert ups[0]["price"] == pytest.approx(0.49)
    assert ups[0]["value"] == pytest.approx(1.0 - 0.49)
    # the optimistic rule fills on the touch
    assert [x["price"] for x in SF.fills(path((10, 0.50), (20, 0.49)), "Up",
                                         depth=0, through=0)
            if x["side"] == UP] == [pytest.approx(0.49)]
    assert SF.fills(path((10, 0.50), (20, 0.50)), "Up", depth=0) == []


def test_a_surface_fill_is_worth_settlement_less_price():
    p = path((10, 0.50), (20, 0.40))
    f = [x for x in SF.fills(p, "Down", depth=0) if x["side"] == UP]
    assert f[0]["value"] == pytest.approx(-0.49)


def test_surface_state_is_what_was_known_when_the_quote_was_live():
    p = path((10, 0.60), (70, 0.50), (80, 0.40))
    f = [x for x in SF.fills(p, "Up", depth=0, model=lambda t: 0.7)
         if x["side"] == UP][-1]
    # quoted at t=70 after a ten-cent fall over the minute before
    assert f["momentum"] == pytest.approx(-0.10)
    assert f["edge"] == pytest.approx(0.7 - 0.49)
    assert SF.momentum_band(f["momentum"]) == "momentum <= -5c"
    assert SF.edge_band(f["edge"]) == "model edge > +10c"


def test_surface_cells_are_clustered_by_window():
    by = {"a": [1.0, 10], "b": [-1.0, 10], "c": [0.5, 10]}
    s = SF.cell_stats(by)
    assert s["mean"] == pytest.approx(0.5 / 30) and s["windows"] == 3
    assert s["lo"] <= s["mean"] <= s["hi"]
    assert SF.cell_stats(by) == s


# ------------------------------------------------- the committed result files
def _json(name):
    with open(os.path.join(ROOT, "analysis", "l5", name)) as fh:
        return json.load(fh)


def test_committed_no_state_has_profitable_maker_fills():
    """If a rerun ever changes this, change the test by hand, with the
    evidence."""
    r = _json("maker_fill_value_surface.json")
    assert all(v == [] for v in r["cells_positive_in_both_halves"].values())
    for key, cells in r["surfaces"].items():
        assert cells["ALL"]["hi"] < 0, key


def test_committed_aggression_loses_more_on_both_panels():
    r = _json("portfolio_controller_replay.json")
    for pname, pan in r["panels"].items():
        base = pan["candidates"]["PAIR (deployed)"]["OOS"]
        for name, by in pan["candidates"].items():
            if not name.startswith("AGGRESSIVE"):
                continue
            s = by["OOS"]
            assert s["net_per_window"] < base["net_per_window"], name
            assert s["net_per_window_ci95"][1] < 0, name
            assert pan["vs_pair"][name]["OOS"]["ci95"][1] < 0, name
        agg = pan["candidates"][
            "AGGRESSIVE (haircut 0, touch, size on edge)"]["OOS"]
        assert agg["fills_per_window"] >= 20        # the owner's target


def test_committed_more_activity_is_more_loss():
    r = _json("portfolio_controller_replay.json")
    for pname, pan in r["panels"].items():
        fr = pan["frontier"]
        assert len(fr) >= 3
        top, low = fr[-1], fr[0]
        assert top["OOS"]["fills_per_window"] > low["OOS"]["fills_per_window"]
        assert top["OOS"]["net_per_window"] < low["OOS"]["net_per_window"]
        assert top["OOS"]["net_per_window_ci95"][1] < 0


def test_committed_the_best_setting_is_not_distinguishable_from_zero():
    r = _json("portfolio_controller_replay.json")
    spot = r["panels"]["spot"]
    t = spot["candidates"]["TUNED on IS, by $/window"]["OOS"]
    lo, hi = t["net_per_window_ci95"]
    assert lo < 0 < hi and t["net_per_window"] <= 0
    # it beats the deployed pair on the first part and is ahead on the second
    v = spot["vs_pair"]["TUNED on IS, by $/window"]
    assert v["IS"]["ci95"][0] > 0 and v["OOS"]["mean"] > 0
    # without a model nothing in the grid is positive even in sample
    assert r["panels"]["wide"]["grid_IS_positive"] == 0
