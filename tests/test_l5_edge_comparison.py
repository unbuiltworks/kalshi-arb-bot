"""The edge comparison: its fill rule, its accounting, its honesty.

No corpus and no network. Every case builds its own price path; the one
exception reads the committed result file and nothing else.
"""
import ast
import inspect
import json
import os
import random

import pytest

from analysis.l5 import edge_comparison as EC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP, DOWN = EC.UP, EC.DOWN


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


STRATEGIES = {
    "pair": lambda: EC.make_pair(2),
    "grind": lambda: EC.make_grind(0),
    "grind_back": lambda: EC.make_grind(1),
    "chase": lambda: EC.make_grind(0, ceiling=None),
    "spot": lambda: EC.make_spot(0.04, 1),
    "veto": lambda: EC.make_veto(0.0),
}


def model_half(_t):
    return 0.5


# ------------------------------------------------------------ the fill rule
def test_an_order_never_fills_on_the_print_that_placed_it():
    b = EC.Book("Up")
    b.place(UP, 0.50, 10.0, 0.40)
    assert b.match(10.0, 0.40) == [] and b.fills == 0


def test_a_touch_is_not_a_fill_under_the_conservative_rule():
    b = EC.Book("Up", through=1)
    b.place(UP, 0.50, 10.0, 0.52)
    assert b.match(20.0, 0.50) == []          # touched
    assert b.match(30.0, 0.495) == []         # not a full tick through
    assert len(b.match(40.0, 0.49)) == 1      # traded through by one tick


def test_a_touch_does_fill_under_the_optimistic_rule():
    b = EC.Book("Up", through=0)
    b.place(UP, 0.50, 10.0, 0.52)
    assert len(b.match(20.0, 0.50)) == 1


def test_a_down_order_fills_when_the_up_price_rises_through_it():
    b = EC.Book("Up")
    b.place(DOWN, 0.40, 10.0, 0.58)            # DOWN mark 0.42
    assert b.match(20.0, 0.60) == []           # DOWN mark 0.40: a touch
    assert len(b.match(30.0, 0.61)) == 1       # DOWN mark 0.39


def test_orders_outside_the_price_grid_are_refused():
    b = EC.Book("Up")
    assert b.place(UP, 0.0, 1.0, 0.5) is None
    assert b.place(UP, 1.0, 1.0, 0.5) is None
    assert b.orders == {}


# ----------------------------------------------------------- the accounting
@pytest.mark.parametrize("winner", ["Up", "Down"])
def test_a_pair_earns_one_dollar_less_its_cost_whoever_wins(winner):
    b = EC.Book(winner)
    b.place(UP, 0.47, 10.0, 0.50)
    b.place(DOWN, 0.47, 10.0, 0.50)
    b.match(20.0, 0.40)
    b.match(30.0, 0.60)
    b.settle(0.60)
    r = b.result()
    assert r["pairs"] == 1 and r["fills"] == 2
    assert r["net"] == pytest.approx(1.0 - 0.94)
    assert r["unmatched_results"] == []


@pytest.mark.parametrize("winner,expect", [("Up", 1.0 - 0.47), ("Down", -0.47)])
def test_an_unmatched_leg_settles_on_the_outcome(winner, expect):
    b = EC.Book(winner)
    b.place(UP, 0.47, 10.0, 0.50)
    b.match(20.0, 0.40)
    b.settle(0.30)
    r = b.result()
    assert r["net"] == pytest.approx(expect)
    assert r["unmatched_results"] == [pytest.approx(expect)]


def test_the_five_parts_of_an_unmatched_leg_are_what_they_say():
    b = EC.Book("Down")
    b.place(UP, 0.47, 10.0, 0.50)       # asked for 3 cents under the mark
    b.match(20.0, 0.40)                 # filled 10 cents under placement
    b.settle(0.30)                      # carried down another 10, then lost
    k = b.result()["buckets"]
    assert k["entry"] == pytest.approx(0.03)
    assert k["fill"] == pytest.approx(-0.10)
    assert k["unmatched"] == pytest.approx(-0.10)
    assert k["settlement"] == pytest.approx(-0.30)
    assert k["completion"] == 0.0


def test_the_five_parts_of_a_pair_are_what_they_say():
    b = EC.Book("Up")
    b.place(UP, 0.47, 10.0, 0.50)
    b.place(DOWN, 0.47, 10.0, 0.50)
    b.match(20.0, 0.40)                 # UP opens, 10 under placement
    b.match(30.0, 0.60)                 # DOWN completes at DOWN mark 0.40
    k = b.result()["buckets"]
    assert k["entry"] == pytest.approx(0.03)
    assert k["fill"] == pytest.approx(-0.10)
    assert k["completion"] == pytest.approx(0.40 - 0.47)
    assert k["unmatched"] == pytest.approx(0.60 - 0.40)
    assert k["settlement"] == 0.0
    assert sum(k.values()) == pytest.approx(0.06)


@pytest.mark.parametrize("name", sorted(STRATEGIES))
@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("through", [0, 1])
def test_the_parts_always_add_up_to_the_cash_result(name, seed, through):
    """Independent of the marks: pairs pay $1 less cost, the rest settle."""
    winner = "Up" if seed % 2 else "Down"
    r = EC.run_window(random_path(seed), winner, STRATEGIES[name](),
                      model=model_half, through=through)
    cash = sum(1.0 - c for c in r["pair_costs"]) + sum(r["unmatched_results"])
    assert r["net"] == pytest.approx(cash, abs=1e-9)
    assert sum(r["buckets"].values()) == pytest.approx(cash, abs=1e-9)
    assert r["fills"] == 2 * r["pairs"] + len(r["unmatched_results"])


# ----------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("name", sorted(STRATEGIES))
@pytest.mark.parametrize("seed", range(6))
def test_what_happens_later_cannot_change_what_was_done_earlier(name, seed):
    p = random_path(seed)
    cut_t = p[80][0]
    other = p[:81] + [(t, 0.99 - m if m < 0.5 else 0.03) for t, m in p[81:]]
    a = EC.run_window(p, "Up", STRATEGIES[name](), model=model_half)
    b = EC.run_window(other, "Up", STRATEGIES[name](), model=model_half)
    early = lambda r: [f for f in r["log"] if f["t"] <= cut_t]
    assert early(a) == early(b)


def test_the_model_is_asked_only_about_the_present():
    asked = []

    def model(t):
        asked.append(t)
        return 0.5
    p = random_path(3)
    EC.run_window(p, "Up", EC.make_spot(0.04, 1), model=model)
    assert asked == [t for t, _m in p]


# ------------------------------------------------------------ the strategies
def test_the_pair_opens_two_ticks_back_and_its_complement_follows_the_touch():
    """UP 0.47 fills as the price falls; the DOWN leg is then worked up to
    the touch, bounded by the 0.99 pair -- the deployed behaviour, and why
    planned 0.94 pairs complete at 0.99."""
    r = EC.run_window(path((10, 0.50), (20, 0.455), (30, 0.545)), "Up",
                      EC.make_pair(2))
    assert [(f["side"], f["role"], f["px"]) for f in r["log"]] == [
        (UP, "OPEN", pytest.approx(0.47)),
        (DOWN, "COMPLETE", pytest.approx(0.52))]
    assert r["pair_costs"] == [pytest.approx(0.99)]


def test_both_legs_filling_at_their_opening_prices_is_the_planned_pair():
    b = EC.Book("Up")
    s = {}
    strat = EC.make_pair(2)
    strat(b, 10.0, 0.50, None, s)
    assert sorted((o["side"], round(o["px"], 2)) for o in b.orders.values()) \
        == [(DOWN, 0.47), (UP, 0.47)]


def test_the_pair_never_completes_above_its_ceiling():
    for seed in range(30):
        r = EC.run_window(random_path(seed), "Up", EC.make_pair(2, 0.99))
        assert all(c <= 0.99 + 1e-9 for c in r["pair_costs"])
        assert r["max_unmatched"] <= 1.0


def test_the_pair_does_not_open_below_the_floor_or_after_the_cutoff():
    assert EC.run_window(path((10, 0.20), (20, 0.05), (30, 0.40)), "Up",
                         EC.make_pair(2))["fills"] == 0
    assert EC.run_window(path((500, 0.50), (510, 0.40), (520, 0.60)), "Up",
                         EC.make_pair(2))["fills"] == 0


def test_the_bounded_grind_never_pays_past_its_ceiling_the_chase_does():
    bounded = [c for s in range(40) for c in EC.run_window(
        random_path(s), "Up", EC.make_grind(0))["pair_costs"]]
    chase = [c for s in range(40) for c in EC.run_window(
        random_path(s), "Up", EC.make_grind(0, ceiling=None))["pair_costs"]]
    assert bounded and max(bounded) <= 0.99 + 1e-9
    assert max(chase) > 1.0


def test_the_grind_never_holds_more_than_its_cap():
    for seed in range(40):
        r = EC.run_window(random_path(seed), "Up", EC.make_grind(0, cap=3))
        assert r["max_unmatched"] <= 3.0


def test_the_spot_rule_quotes_nothing_without_a_model():
    r = EC.run_window(random_path(1), "Up", EC.make_spot(0.02, 1),
                      model=lambda t: None)
    assert r["fills"] == 0


def test_the_spot_rule_quotes_only_the_side_the_model_calls_cheap():
    for seed in range(20):
        r = EC.run_window(random_path(seed), "Up", EC.make_spot(0.02, 1),
                          model=lambda t: 0.99)
        assert all(f["side"] == UP for f in r["log"])


def test_the_spot_rule_needs_the_whole_margin():
    p = path((10, 0.50), (20, 0.40), (30, 0.40))
    # UP quote at 0.48; fair 0.50 is two cents over it
    assert EC.run_window(p, "Up", EC.make_spot(0.03, 1),
                         model=lambda t: 0.50)["fills"] == 0
    assert EC.run_window(p, "Up", EC.make_spot(0.02, 1),
                         model=lambda t: 0.50)["log"][0]["side"] == UP


def test_the_veto_pulls_an_opening_leg_the_model_calls_overpriced():
    p = path((10, 0.50), (20, 0.50), (30, 0.40), (40, 0.60))
    plain = EC.run_window(p, "Down", EC.make_pair(2))
    assert plain["log"][0]["side"] == UP
    # the model says UP is worth 0.30: the 0.47 UP bid is pulled, DOWN stays
    vetoed = EC.run_window(p, "Down", EC.make_veto(0.0),
                           model=lambda t: 0.30)
    assert all(f["side"] == DOWN for f in vetoed["log"])


def test_the_veto_without_a_model_is_the_plain_pair():
    for seed in range(10):
        a = EC.run_window(random_path(seed), "Up", EC.make_pair(2))
        b = EC.run_window(random_path(seed), "Up", EC.make_veto(0.0),
                          model=lambda t: None)
        assert a["log"] == b["log"]


# ----------------------------------------------------------------- scoring
def test_the_summary_is_per_filled_contract_and_reports_the_worst():
    rs = [EC.run_window(random_path(s), "Up" if s % 2 else "Down",
                        EC.make_pair(2)) for s in range(30)]
    s = EC.summarise(rs)
    fills = sum(r["fills"] for r in rs)
    assert s["fills"] == fills and s["windows"] == 30
    assert s["net_per_contract"] == pytest.approx(
        sum(r["net"] for r in rs) / fills)
    assert sum(s["buckets_per_contract"].values()) == pytest.approx(
        s["net_per_contract"])
    assert s["worst_window"] == min(r["net"] for r in rs)
    lo, hi = s["net_per_contract_ci95"]
    assert lo <= s["net_per_contract"] <= hi


def test_the_intervals_are_reproducible():
    nets, dens = [0.1, -0.3, 0.05, -0.02, 0.0], [2.0, 1.0, 2.0, 2.0, 1.0]
    assert EC._boot_ratio(nets, dens) == EC._boot_ratio(nets, dens)
    d = EC.paired_difference([0.1, 0.2, 0.3], [0.0, 0.3, 0.1])
    assert d["mean"] == pytest.approx(0.2 / 3)
    assert d["ci95"][0] <= d["mean"] <= d["ci95"][1]


def test_several_books_on_one_window_are_summed_exactly():
    parts = [EC.run_window(random_path(4), "Up", EC.make_pair(d))
             for d in (2, 3, 4)]
    c = EC.combine(parts)
    assert c["net"] == pytest.approx(sum(p["net"] for p in parts))
    assert c["fills"] == sum(p["fills"] for p in parts)
    assert sum(c["buckets"].values()) == pytest.approx(c["net"])


def test_l5s_own_result_is_settlement_less_what_he_paid():
    trades = [
        {"slug": "a", "side": "BUY", "outcome": "Up", "winner": "Up",
         "px": 0.40, "size": 10.0},
        {"slug": "a", "side": "BUY", "outcome": "Down", "winner": "Up",
         "px": 0.55, "size": 10.0},
        {"slug": "b", "side": "BUY", "outcome": "Up", "winner": "Down",
         "px": 0.50, "size": 4.0},
        {"slug": "zzz", "side": "BUY", "outcome": "Up", "winner": "Up",
         "px": 0.01, "size": 1000.0},
    ]
    r = EC.l5_actual(trades, ["a", "b"])
    assert r["windows"] == 2 and r["contracts"] == 24.0
    assert r["net_total"] == pytest.approx(6.0 - 5.5 - 2.0)


# ------------------------------------------------------- honesty of the run
def test_everything_tuned_is_tuned_on_the_first_part_only():
    src = inspect.getsource(EC.run)
    for name in ("best_spot", "best_veto"):
        start = src.index("%s = max(" % name)
        stmt = src[start:src.index("\n\n", start)] if "\n\n" in src[start:] \
            else src[start:]
        stmt = stmt[:stmt.index("))") + 2 + 60]
        assert 'halves["IS"]' in stmt
        assert "OOS" not in stmt and "ALL" not in stmt


def test_the_split_is_chronological():
    src = inspect.getsource(EC.run)
    assert 'order = sorted(models, key=lambda s: ps[s]["ws"])' in src
    assert 'halves = {"ALL": order, "IS": order[:cut], "OOS": order[cut:]}' \
        in src


def test_no_rebate_and_no_fee_credit_is_counted():
    src = open(EC.__file__).read()
    code = "\n".join(l for l in src.split('"""')[2].splitlines())
    assert "rebate" not in code.lower() and "reward" not in code.lower()


def test_the_module_touches_no_production_code_and_nothing_imports_it():
    tree = ast.parse(open(EC.__file__).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert not [m for m in mods if m == "src" or m.startswith("src.")
                or m.startswith("l5_")]
    assert not ({"requests", "urllib", "socket", "http", "subprocess"}
                & {m.split(".")[0] for m in mods})
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if f.endswith(".py"):
                assert "edge_comparison" not in open(
                    os.path.join(base, f), errors="ignore").read(), f


# ------------------------------------------------- the committed result file
def _committed():
    with open(os.path.join(ROOT, "analysis", "l5",
                           "edge_comparison.json")) as fh:
        return json.load(fh)


def test_the_committed_result_names_its_evidence_class():
    r = _committed()
    assert "not PMUS fills, not our own" in r["evidence_class"]
    assert r["windows"] == {"ALL": 312, "IS": 187, "OOS": 125}
    assert r["wide"]["windows"] == {"ALL": 526, "IS": 315, "OOS": 211}


def test_the_committed_result_is_no_promotion():
    """Every candidate loses out of sample, on both panels, under both fill
    rules. If a rerun ever changes this, this test must be changed by hand
    -- with the evidence -- not silently."""
    r = _committed()
    for name, by in r["candidates"].items():
        assert by["OOS"]["net_per_contract"] < 0, name
        assert by["ALL"]["net_per_contract"] < 0, name
    for name, s in r["through0"].items():
        assert s["net_per_contract"] < 0, name
    for name, by in r["wide"]["candidates"].items():
        assert by["OOS"]["net_per_contract"] < 0, name
        assert by["OOS"]["net_per_contract_ci95"][1] < 0, name


def test_the_committed_result_shows_l5s_own_fills_at_break_even():
    a = _committed()["wide"]["l5_actual"]["ALL"]
    lo, hi = a["net_per_contract_ci95"]
    assert a["windows"] == 526 and lo < 0 < hi
    assert abs(a["net_per_contract"]) < 0.005


# ------------------------------------------------------- cancellation latency
def test_a_cancel_in_flight_can_still_be_filled():
    b = EC.Book("Up")
    oid = b.place(UP, 0.50, 10.0, 0.52)
    b.cancel(oid, at=14.0)
    assert b.resting() == [] and len(b.resting(cancelling=True)) == 1
    assert len(b.match(12.0, 0.48)) == 1          # before the cancel landed


def test_a_cancel_that_has_landed_cannot_be_filled():
    b = EC.Book("Up")
    oid = b.place(UP, 0.50, 10.0, 0.52)
    b.cancel(oid, at=14.0)
    assert b.match(14.0, 0.40) == [] and b.orders == {}


def test_an_immediate_cancel_is_unchanged_and_the_earliest_cancel_wins():
    b = EC.Book("Up")
    a = b.place(UP, 0.50, 10.0, 0.52)
    b.cancel(a)
    assert b.orders == {}
    c = b.place(UP, 0.50, 10.0, 0.52)
    b.cancel(c, at=20.0)
    b.cancel(c, at=30.0)
    assert b.orders[c]["cancel_at"] == 20.0
    b.cancel(999, at=5.0)                         # unknown id: nothing
