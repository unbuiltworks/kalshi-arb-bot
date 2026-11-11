"""The three defects this pipeline had, pinned so they cannot come back."""
import pytest

from analysis.l5.next_side import features as F


def _f(tx, outcome, ts, size="10", usd=None, price="0.5"):
    """usdcSize is the source of truth for price; aggregate_transactions
    recomputes price as usd/size, so the fixture keeps them consistent."""
    sz = float(size)
    u = float(usd) if usd is not None else sz * float(price)
    return {"transactionHash": tx, "outcome": outcome, "timestamp": ts,
            "size": sz, "usdcSize": u, "price": u / sz if sz else 0.0}


# ---------------------------------------- a transaction is one decision

def test_legs_of_one_transaction_collapse_to_one_event():
    """Every multi-leg BTC15m transaction has all legs on the SAME side --
    6,396 of them, 19.7% of fills. Counting legs as separate decisions
    inflated same-side persistence from 68.24% to 71.50%."""
    ev = F.aggregate_transactions([_f("a", "Up", 1), _f("a", "Up", 1),
                                   _f("a", "Up", 1), _f("b", "Down", 2)])
    assert [e["outcome"] for e in ev] == ["Up", "Down"]
    assert ev[0]["n_legs"] == 3
    assert ev[0]["size"] == 30.0 and ev[0]["usdcSize"] == 15.0


def test_aggregation_preserves_cost_and_size_exactly():
    ev = F.aggregate_transactions([_f("a", "Up", 1, size="7", usd="3.5"),
                                   _f("a", "Up", 1, size="3", usd="1.5")])
    assert ev[0]["size"] == 10.0
    assert ev[0]["usdcSize"] == 5.0
    assert ev[0]["price"] == 0.5          # size-weighted, not averaged


# ---------------------------------------- no lookahead

def test_progress_comes_from_the_clock_not_the_window_fill_count():
    """fills_so_far / total_fills_in_window is LOOKAHEAD: the window's
    eventual fill count is not knowable at fill i."""
    rows = F.build_window("btc-updown-15m-1000", [
        _f("a", "Up", 1000), _f("b", "Down", 1450), _f("c", "Up", 1890)])
    # progress is elapsed/900, independent of how many fills follow
    assert rows[0]["progress"] == pytest.approx(0.0)
    assert rows[1]["progress"] == pytest.approx(0.5)
    assert all("total_fills" not in k for k in rows[0])


def test_fill_intensity_counts_only_prior_events():
    """A fill must not count itself as incoming flow that preceded it."""
    rows = F.build_window("btc-updown-15m-1000", [
        _f("a", "Up", 1000), _f("b", "Up", 1001), _f("c", "Down", 1002)])
    assert rows[0]["fill_intensity_60s"] == 0.0
    assert rows[1]["fill_intensity_60s"] == 1.0


def test_the_label_comes_only_from_the_next_fill():
    rows = F.build_window("btc-updown-15m-1000", [
        _f("a", "Up", 1000), _f("b", "Down", 1010), _f("c", "Down", 1020)])
    assert [r["y_up"] for r in rows] == [0.0, 0.0]
    assert [r["y_continue"] for r in rows] == [0.0, 1.0]


# ---------------------------------------- flow must not smuggle the side

def test_flow_features_are_side_neutral():
    """`price` equals price_up_last exactly when the last fill was Up, which
    leaked last_side into the FLOW family and made flow_only score 0.694
    without ever seeing the side. p_up is the Up contract's probability
    however he traded."""
    up = F.build_window("btc-updown-15m-1000",
                        [_f("a", "Up", 1000, price="0.40"),
                         _f("b", "Up", 1010)])[0]
    dn = F.build_window("btc-updown-15m-1000",
                        [_f("a", "Down", 1000, price="0.60"),
                         _f("b", "Up", 1010)])[0]
    assert up["p_up"] == pytest.approx(0.40)
    assert dn["p_up"] == pytest.approx(0.40)   # 1 - 0.60, same market state
    for name in F.feature_names(list(F.FLOW_BLOCKS)):
        assert name not in ("price", "price_up_last", "price_dn_last",
                            "x_side_price"), name


def test_the_side_tagged_prices_live_in_a_control_block():
    ctrl = F.feature_names(list(F.CONTROL_BLOCKS))
    assert "price" in ctrl and "x_side_price" in ctrl


def test_run_length_is_persistence_not_inventory():
    """Documented separately because it carried the ENTIRE inventory-block
    gain: run length alone reaches AUC 0.611 on CONTINUE, imbalance alone
    0.5026 against a 0.498 last-side baseline."""
    rows = F.build_window("btc-updown-15m-1000", [
        _f("a", "Up", 1000), _f("b", "Up", 1001), _f("c", "Up", 1002),
        _f("d", "Down", 1003), _f("e", "Down", 1004)])
    assert [r["run_len"] for r in rows] == [1.0, 2.0, 3.0, 1.0]
