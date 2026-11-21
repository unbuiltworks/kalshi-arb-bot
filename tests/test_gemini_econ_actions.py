"""The four post-fill paths, and the invariants that keep them honest."""
from decimal import Decimal as D

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution.econ import actions as A
from src.gemini_execution.econ import veto as VT

# an identity-consistent book: no_bid == 1 - yes_ask, no_ask == 1 - yes_bid
BOOK = {"yes": {"bid": "0.60", "ask": "0.65"},
        "no": {"bid": "0.35", "ask": "0.40"}}


def test_a_and_b_pay_the_same_while_the_identity_holds():
    """The finding that makes 'COMPLETE the pair' a choice rather than a rule."""
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=BOOK,
                         qty="0.01", min_edge="0.001")
    assert r["identity"]["holds"] is True
    a = A.net_of(r["paths"][A.PATH_A])
    b = A.net_of(r["paths"][A.PATH_B])
    assert a == b, (a, b)


def test_a_and_b_diverge_when_the_identity_breaks():
    """A book that violates the identity makes the two paths pay differently,
    which is exactly when having kept both matters."""
    broken = {"yes": {"bid": "0.60", "ask": "0.65"},
              "no": {"bid": "0.30", "ask": "0.40"}}      # no_bid != 1 - yes_ask
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=broken,
                         qty="0.01", min_edge="0.001")
    assert r["identity"]["holds"] is False
    assert A.net_of(r["paths"][A.PATH_A]) != A.net_of(r["paths"][A.PATH_B])


def test_the_aggressive_paths_are_priced_at_the_taker_rate():
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=BOOK,
                         qty="0.01", min_edge="0.001")
    assert r["paths"][A.PATH_A]["liquidity"] == "MAKER"
    assert r["paths"][A.PATH_B]["liquidity"] == "MAKER"
    assert r["paths"][A.PATH_C]["liquidity"] == "TAKER"
    assert r["paths"][A.PATH_D]["liquidity"] == "TAKER"
    # crossing costs more than resting, so C's ceiling is below A's
    assert D(r["paths"][A.PATH_C]["ceiling"]) < D(r["paths"][A.PATH_A]["ceiling"])


def test_a_rescue_tolerance_can_never_finance_a_completion():
    """A negative edge reaches path D ONLY. If it leaked into A or C it would
    authorise exactly the negative pair the veto exists to stop."""
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=BOOK,
                         qty="0.01", min_edge="0.001", rescue_edge="-0.50")
    assert r["paths"][A.PATH_D]["rescue_edge_applied"] == "-0.50"
    for p in (A.PATH_A, A.PATH_C):
        assert "rescue_edge_applied" not in r["paths"][p]
    # and a wildly expensive complement is still refused
    rich = {"yes": {"bid": "0.60", "ask": "0.65"},
            "no": {"bid": "0.55", "ask": "0.60"}}
    r2 = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=rich,
                          qty="0.01", min_edge="0.001", rescue_edge="-0.50")
    assert r2["paths"][A.PATH_A]["admitted"] is False
    assert r2["paths"][A.PATH_C]["admitted"] is False


def test_the_113_pair_is_refused_on_every_completing_path():
    """Held 0.63 against a complement quoted at 0.50 -- basis 1.13."""
    bad = {"yes": {"bid": "0.60", "ask": "0.65"},
           "no": {"bid": "0.50", "ask": "0.55"}}
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=bad,
                         qty="0.01", min_edge="0.001")
    assert r["paths"][A.PATH_A]["admitted"] is False
    assert r["paths"][A.PATH_C]["admitted"] is False
    assert A.PATH_A not in r["admitted_paths"]


def test_an_unquoted_side_is_unavailable_not_uneconomic():
    """'the venue did not quote it' and 'we judged it bad' are different facts."""
    partial = {"yes": {"bid": "0.60", "ask": "0.65"}, "no": {"bid": "0.35"}}
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=partial,
                         qty="0.01", min_edge="0.001")
    c = r["paths"][A.PATH_C]
    assert c["available"] is False and c["admitted"] is False
    assert "not quoted" in c["why"]
    assert r["paths"][A.PATH_A]["available"] is True


def test_a_flat_state_offers_no_paths():
    r = A.evaluate_paths(residual="0", held_avg_cost="0.63", book=BOOK,
                         qty="0.01", min_edge="0.001")
    assert r["state"] == "FLAT"
    assert r["paths"] == {}


def test_held_and_complement_follow_the_inventory_sign_convention():
    """`Inventory.residual` is positive when long YES. Getting this backwards
    would buy MORE of the leg we are already long."""
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.01", "0.63", source="TEST")
    assert inv.residual() > 0
    assert A.held_and_complement(inv.residual())["held"] == "YES"
    assert A.held_and_complement(inv.residual())["complement"] == "NO"

    inv2 = CP.Inventory()
    inv2.apply_fill("NO", "0.01", "0.37", source="TEST")
    assert inv2.residual() < 0
    assert A.held_and_complement(inv2.residual())["held"] == "NO"


def test_dispositions_never_contaminate_the_l5_vocabulary():
    """REDUCE and EXIT have no L5 counterpart and must not be emitted into a
    field that L5 fidelity is scored from."""
    assert set(A.DISPOSITIONS) - set(A.L5_LABELS) == {"REDUCE", "EXIT"}
    assert "HOLD" not in A.DISPOSITIONS
    # and the canonical labels are still exactly what continuous_probe defines
    assert (CP.OPEN, CP.EXTEND, CP.COMPLETE, CP.HOLD) == A.L5_LABELS


def test_capital_weighted_ranking_refuses_to_guess_a_holding_period():
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=BOOK,
                         qty="0.01", min_edge="0.001")
    rows = A.rank(r, capital_weighted=True, expected_hold_minutes=None)
    assert rows == []          # nothing scored, nothing invented
    rows2 = A.rank(r, capital_weighted=True, expected_hold_minutes="5")
    assert rows2 and all(x.get("score") for x in rows2)


def test_the_round_trip_ties_up_less_capital_than_the_pair():
    """At equal net cents the round trip must win on capital, which is what
    makes B worth keeping beside A."""
    r = A.evaluate_paths(residual="0.01", held_avg_cost="0.63", book=BOOK,
                         qty="0.01", min_edge="0.001")
    rows = {x["path"]: x for x in
            A.rank(r, capital_weighted=True, expected_hold_minutes="5")}
    assert D(rows[A.PATH_B]["capital_dollars"]) < D(
        rows[A.PATH_A]["capital_dollars"])
    assert D(rows[A.PATH_B]["score"]) > D(rows[A.PATH_A]["score"])


def test_a_round_trip_reports_no_settlement_and_no_residual():
    econ = VT.roundtrip_economics(qty="0.01", held_avg_cost="0.63",
                                  exit_price="0.65")
    assert econ["residual_qty"] == "0"
    assert "never reaches settlement" in econ["settlement_cashflow"]
    assert "net_pair_economics" not in econ      # not a pair, not reported as one


def test_module_holds_no_transport():
    import src.gemini_execution.econ.actions as mod
    src = open(mod.__file__).read()
    for forbidden in ("httpx", "requests", "order.place", "private_key"):
        assert forbidden not in src
