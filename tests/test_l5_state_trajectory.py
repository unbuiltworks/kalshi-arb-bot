"""One walker, no lookahead, and an identity that must hold exactly."""
import pytest

from src import l5_state_trajectory as st

UP, DN = st.UP, st.DN


def f(t, side, price, qty=1.0, **kw):
    return {"t": t, "side": side, "price": price, "qty": qty, **kw}


# ---------- no lookahead

def test_a_state_never_reads_a_later_fill():
    """Truncating the episode must not change any state that survives."""
    full = [f(0, UP, 0.40), f(1, UP, 0.50), f(2, DN, 0.45), f(3, UP, 0.90)]
    a = st.walk(full)
    b = st.walk(full[:3])
    for x, y in zip(a, b):
        assert x == y


def test_the_first_fill_has_a_flat_pre_state():
    s = st.walk([f(0, UP, 0.40)])[0]
    assert s["held_side"] is None and s["held_avg_cost"] is None
    assert s["matched_qty"] == 0 and s["residual_signed"] == 0
    assert s["gain_per_unit"] is None


def test_episodes_are_aligned_at_the_first_fill():
    s = st.walk([f(10, DN, 0.30), f(11, DN, 0.35)])
    assert s[0]["is_first_fill"] and s[0]["entry_price"] == 0.30
    assert s[1]["entry_price"] == 0.30 and s[1]["entry_side"] == DN


# ---------- running state

def test_the_running_average_is_size_weighted_not_a_mean_of_prices():
    s = st.walk([f(0, UP, 0.40, 1.0), f(1, UP, 0.60, 3.0), f(2, DN, 0.50)])
    assert s[2]["held_avg_cost"] == pytest.approx(0.55)   # not 0.50


def test_gain_per_unit_is_the_held_side_marked_against_its_own_average():
    s = st.walk([f(0, UP, 0.40), f(1, UP, 0.60), f(2, DN, 0.50)])
    # avg 0.50, last up price 0.60 -> +0.10
    assert s[2]["gain_per_unit"] == pytest.approx(0.10)


def test_matched_and_residual_track_the_two_sides():
    s = st.walk([f(0, UP, 0.4, 3.0), f(1, DN, 0.5, 1.0), f(2, UP, 0.4)])
    assert s[2]["matched_qty"] == pytest.approx(1.0)
    assert s[2]["residual_signed"] == pytest.approx(2.0)


def test_a_switch_is_flagged_only_when_the_side_changes():
    s = st.walk([f(0, UP, 0.4), f(1, UP, 0.4), f(2, DN, 0.5), f(3, DN, 0.5)])
    assert [x["is_switch"] for x in s] == [False, False, True, False]


def test_cadence_is_measured_separately_for_fills_and_switches():
    s = st.walk([f(0, UP, 0.4), f(5, UP, 0.4), f(9, DN, 0.5), f(20, UP, 0.4)])
    assert s[2]["dt_since_prev_fill"] == 4
    assert s[3]["dt_since_prev_switch"] == 11


def test_attainable_pair_uses_the_last_opposite_price_not_the_action():
    s = st.walk([f(0, UP, 0.40), f(1, DN, 0.70), f(2, UP, 0.40),
                 f(3, DN, 0.55)])
    # before the 0.55 switch the last observed DN price is 0.70
    assert s[3]["attainable_pair_cost"] == pytest.approx(0.40 + 0.70)
    assert s[3]["realised_pair_cost"] == pytest.approx(0.40 + 0.55)


# ---------- the identity

def test_the_excess_pair_decomposition_is_exact():
    s = st.walk([f(0, UP, 0.40, 1.0), f(1, UP, 0.60, 1.0), f(2, DN, 0.55)])
    d = st.decompose_excess_pair(s[2])
    assert d["identity_residual"] == pytest.approx(0.0, abs=1e-12)
    assert d["C_own_fills"] == pytest.approx(0.50 - 0.40)
    assert d["B_market_move"] == pytest.approx(0.55 - 0.60)


@pytest.mark.parametrize("prices", [
    (0.10, 0.20, 0.85), (0.90, 0.80, 0.05), (0.50, 0.50, 0.50),
    (0.33, 0.67, 0.99), (0.05, 0.95, 0.50)])
def test_the_identity_holds_on_a_grid(prices):
    a, b, c = prices
    s = st.walk([f(0, UP, a), f(1, UP, b), f(2, DN, c)])
    d = st.decompose_excess_pair(s[2])
    assert abs(d["identity_residual"]) < 1e-12
    assert abs(d["excess_pair"] - (d["C_own_fills"] + d["B_market_move"])) \
        < 1e-12


def test_entry_price_is_carried_as_a_stratifier_not_a_level_contribution():
    """Entering anywhere gives a par pair if the complement is at 1-entry, so
    entry price must not appear as a term in the excess."""
    lo = st.decompose_excess_pair(st.walk([f(0, UP, 0.10), f(1, DN, 0.90)])[1])
    hi = st.decompose_excess_pair(st.walk([f(0, UP, 0.80), f(1, DN, 0.20)])[1])
    assert lo["excess_pair"] == pytest.approx(0.0)
    assert hi["excess_pair"] == pytest.approx(0.0)
    assert lo["A_entry_price"] != hi["A_entry_price"]


def test_a_state_that_formed_no_pair_returns_none():
    assert st.decompose_excess_pair(st.walk([f(0, UP, 0.4)])[0]) is None


# ---------- reweighting is composition, never causation

def test_reweighting_shifts_the_mean_toward_the_target_composition():
    states = ([{"k": "a", "v": 0.0}] * 90) + ([{"k": "b", "v": 1.0}] * 10)
    r = st.reweight(states, "k", {"a": 0.5, "b": 0.5}, "v")
    assert r["observed_mean"] == pytest.approx(0.10)
    assert r["reweighted_mean"] == pytest.approx(0.50)


def test_target_strata_the_source_never_visited_are_dropped_not_imputed():
    states = [{"k": "a", "v": 1.0}]
    r = st.reweight(states, "k", {"a": 0.4, "never_seen": 0.6}, "v")
    assert r["target_mass_covered"] == pytest.approx(0.4)
    assert r["target_mass_dropped_unobserved"] == pytest.approx(0.6)
    assert r["reweighted_mean"] == pytest.approx(1.0)


def test_the_reweighting_labels_itself_associational():
    r = st.reweight([{"k": "a", "v": 1.0}], "k", {"a": 1.0}, "v")
    assert "associational" in r["labels"]
    assert "not additive" in r["labels"]


def test_weights_sum_to_one():
    w = st.weights([{"k": "a"}, {"k": "a"}, {"k": "b"}], "k")
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["a"] == pytest.approx(2 / 3)


# ---------- hygiene

def test_malformed_fills_are_skipped_not_defaulted():
    s = st.walk([f(0, UP, 0.4), {"t": 1, "side": "sideways", "price": 0.5,
                                 "qty": 1}, f(2, DN, 0.5)])
    assert len(s) == 2
    assert s[1]["action_side"] == DN


def test_a_zero_or_negative_size_is_not_a_fill():
    assert st.walk([f(0, UP, 0.4, 0.0)]) == []


def test_distribution_reports_n_zero_rather_than_inventing_percentiles():
    assert st.distribution([])["n"] == 0
    assert "p50" not in st.distribution([])


def test_it_reads_and_never_acts():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(st))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    code = ast.unparse(tree)
    for tok in ("place_limit", "submit", "cancel_order", "requests.",
                "httpx", "os.environ", "open("):
        assert tok not in code, tok
