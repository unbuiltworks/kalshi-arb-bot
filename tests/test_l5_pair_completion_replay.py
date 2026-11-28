"""The pair-completion replay: its fill rule, its accounting, its isolation.

No corpus and no network. Every case builds its own price path.
"""
import ast
import os

import pytest

from analysis.l5 import pair_completion_replay as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C99 = ("C", 0.99)


def path(*pts):
    return [(float(t), float(m)) for t, m in pts]


def one(p, winner="Up", **kw):
    kw.setdefault("policy", C99)
    return R.replay_window(p, winner, **kw)


# ------------------------------------------------------------ the opening
def test_the_opening_is_the_engines_own_pair_at_depth_two():
    """mid 0.50, one-tick book: UP 0.47, DOWN 0.47 -> a 0.94/0.95 pair."""
    out = one(path((10, 0.50), (20, 0.455), (30, 0.545)))
    pair = [r for r in out["rounds"] if r["kind"] == "PAIR"][0]
    assert pair["pair"] == pytest.approx(0.94, abs=0.011)


def test_no_opening_below_the_first_leg_floor_or_after_the_cutoff():
    assert one(path((10, 0.20), (20, 0.05), (30, 0.40)))["rounds"] == []
    assert one(path((500, 0.50), (510, 0.40), (520, 0.60)))["rounds"] == []


def test_a_window_that_never_moves_fills_nothing():
    out = one(path(*[(t, 0.50) for t in range(10, 600, 10)]))
    assert out["rounds"] == [] and out["net"] == 0


# ------------------------------------------------------------- the fills
def test_a_touch_is_not_a_fill():
    """UP rests at 0.47. A print AT 0.47 does not fill it; one tick beyond
    does. This is the rule that keeps the replay honest."""
    assert one(path((10, 0.50), (20, 0.47), (30, 0.50)))["rounds"] == []
    out = one(path((10, 0.50), (20, 0.46), (900, 0.46)))
    assert out["strands"] == 1


def test_through_zero_is_the_optimistic_bound_and_fills_at_the_touch():
    out = one(path((10, 0.50), (20, 0.47), (900, 0.47)), through=0)
    assert out["strands"] == 1


def test_both_legs_fill_only_on_a_round_trip_and_then_at_plan():
    out = one(path((10, 0.50), (20, 0.455), (30, 0.545)))
    assert out["pairs"] == 1 and out["strands"] == 0
    assert out["net"] == pytest.approx(0.06, abs=0.011)


# ----------------------------------------------------------- the strand
def test_a_stranded_leg_settles_on_the_venues_outcome_not_a_mark():
    run_away = path((10, 0.50), (20, 0.40), (60, 0.20), (900, 0.05))
    lost = one(run_away, winner="Down")
    assert lost["strands"] == 1
    assert lost["net"] == pytest.approx(-0.47, abs=0.011)
    won = one(run_away, winner="Up")
    assert won["net"] == pytest.approx(0.53, abs=0.011)


def test_the_first_fill_is_the_side_the_market_ran_through():
    down_move = one(path((10, 0.50), (20, 0.40), (900, 0.30)), "Down")
    assert down_move["rounds"][0]["side"] == "UP"
    up_move = one(path((10, 0.50), (20, 0.60), (900, 0.70)), "Up")
    assert up_move["rounds"][0]["side"] == "DOWN"


# ------------------------------------------------------- the completion
#: UP fills at 0.47 as the market falls to 0.40, then recovers only as far as
#: 0.49. DOWN can be bought at 0.52 -- a 0.99 pair -- and never at 0.49.
PARTIAL_REVERSION = [(10, 0.50), (20, 0.40), (40, 0.47), (60, 0.49),
                     (80, 0.47), (900, 0.47)]


def test_a_partial_reversion_completes_near_redemption_not_at_plan():
    """THE 0200z AND 1300z SHAPE. Never 0.95."""
    out = one(path(*PARTIAL_REVERSION))
    assert out["pairs"] == 1
    assert out["rounds"][0]["pair"] == pytest.approx(0.99)


def test_a_patient_ceiling_refuses_that_pair_and_strands_the_leg():
    """The lower pair cost is paid for with the stranded leg."""
    out = one(path(*PARTIAL_REVERSION), winner="Down", policy=("C", 0.96))
    assert out["pairs"] == 0 and out["strands"] == 1
    assert out["net"] == pytest.approx(-0.47)


def test_no_completion_is_ever_priced_above_its_ceiling():
    p = path((10, 0.50), (20, 0.40), (30, 0.42), (40, 0.47), (50, 0.52),
             (60, 0.55), (70, 0.50), (80, 0.45), (900, 0.45))
    for name, pol in R.POLICIES:
        for r in one(p, policy=pol)["rounds"]:
            if r["kind"] == "PAIR":
                assert r["pair"] <= 0.99 + 1e-9, (name, r)


def test_nothing_completes_after_the_completion_cutoff():
    p = path((10, 0.50), (20, 0.40), (650, 0.50), (660, 0.55), (670, 0.45),
             (900, 0.45))
    assert one(p, winner="Down")["strands"] == 1


@pytest.mark.parametrize("kind,arg,since,mark,cost,want", [
    ("C", 0.97, 0, 0.5, 0.5, 0.97),
    ("DECAY", 30.0, 10, 0.5, 0.5, 0.96),
    ("DECAY", 30.0, 31, 0.5, 0.5, 0.99),
    ("UNDERWATER", 3.0, 0, 0.49, 0.50, 0.96),
    ("UNDERWATER", 3.0, 0, 0.47, 0.50, 0.99),
])
def test_each_policy_states_its_ceiling(kind, arg, since, mark, cost, want):
    assert R.ceiling_at((kind, arg), since_fill_s=since, held_mark=mark,
                        held_cost=cost) == want


# ---------------------------------------------------------- the rounds
def test_a_second_round_opens_only_after_the_first_is_matched():
    osc = path((10, 0.50), (20, 0.455), (30, 0.545), (40, 0.50),
               (50, 0.455), (60, 0.545), (900, 0.50))
    assert one(osc, max_rounds=1)["pairs"] == 1
    assert one(osc, max_rounds=4)["pairs"] == 2


def test_a_stranded_leg_ends_the_windows_rounds():
    """No new pair is opened on top of an unmatched leg."""
    p = path((10, 0.50), (20, 0.40), (30, 0.30), (40, 0.20), (900, 0.10))
    out = one(p, winner="Down", max_rounds=8)
    assert out["strands"] == 1 and out["pairs"] == 0
    assert len(out["rounds"]) == 1


# ------------------------------------------------------- the accounting
def test_the_summary_is_window_clustered_and_adds_up():
    wins = [one(path((10, 0.50), (20, 0.455), (30, 0.545))),
            one(path((10, 0.50), (20, 0.40), (900, 0.05)), "Down"),
            one(path(*[(t, 0.50) for t in range(10, 600, 10)]))]
    s = R.summarise(wins)
    assert s["windows"] == 3 and s["first_fills"] == 2
    assert s["pairs"] == 1 and s["strands"] == 1
    assert s["strand_rate"] == 0.5
    assert s["net_per_window"] == pytest.approx(
        sum(w["net"] for w in wins) / 3)
    assert s["pair_pnl_total"] + s["strand_pnl_total"] == pytest.approx(
        sum(w["net"] for w in wins))


def test_paths_turn_a_down_print_into_an_up_price_and_sort_by_time():
    tr = [{"slug": "s", "ws": 1.0, "t_off": 30.0, "px": 0.40,
           "outcome": "Down", "winner": "Up"},
          {"slug": "s", "ws": 1.0, "t_off": 10.0, "px": 0.55,
           "outcome": "Up", "winner": "Up"},
          {"slug": "s", "ws": 1.0, "t_off": 950.0, "px": 0.5,
           "outcome": "Up", "winner": "Up"}]
    got = R.paths(tr)["s"]
    assert got["path"] == [(10.0, 0.55), (30.0, pytest.approx(0.60))]


# ----------------------------------------------------------- isolation
def test_the_replay_has_no_path_to_an_order():
    with open(R.__file__) as fh:
        tree = ast.parse(fh.read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "collections", "json", "math",
                    "statistics", "typing", "analysis.l5"}


def test_nothing_under_src_or_the_executor_image_imports_it():
    hits = []
    for base in ("src", os.path.join("deploy", "l5_pmus_live")):
        for dirpath, _d, files in os.walk(os.path.join(ROOT, base)):
            for f in files:
                if f.endswith((".py", "Dockerfile", ".json")):
                    with open(os.path.join(dirpath, f),
                              errors="ignore") as fh:
                        if "import pair_completion_replay" in fh.read():
                            hits.append(os.path.join(dirpath, f))
    assert hits == []
