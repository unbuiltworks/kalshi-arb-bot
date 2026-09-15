"""The replay's value rests entirely on it being unable to cheat."""
from analysis.l5.repair import replay as R


def _w(fills, won_up=True, end=1000):
    return {"cid": "x", "t": fills[0][0], "end": end, "s": fills,
            "won_up": won_up, "outcome_source": "redeem_row"}


def test_the_clone_can_only_ever_refuse_never_invent_a_fill():
    """No policy may take a fill L5 did not take, at a price he did not pay."""
    fills = [(0, 0.40, "Up", 10.0, 4.0), (10, 0.55, "Down", 10.0, 5.5)]
    w = _w(fills)
    for pol in (R.Policy("control"),
                R.Policy("cap", resid_cap=5),
                R.Policy("c96", complement_only=True, pair_ceiling=0.96)):
        r = R.run_window(w, pol, "C_fee_free")
        assert r["taken"] + r["refused"] == len(fills)
        assert r["taken"] <= len(fills)


def test_refusing_the_complement_leaves_the_residual_it_was_meant_to_fix():
    """A pair ceiling that refuses the only complement must STRAND inventory --
    the policy cannot decline the price and keep the repair."""
    fills = [(0, 0.40, "Up", 10.0, 4.0), (10, 0.70, "Down", 10.0, 7.0)]
    w = _w(fills, won_up=True)
    loose = R.run_window(w, R.Policy("loose", complement_only=True, pair_ceiling=1.20), "C_fee_free")
    tight = R.run_window(w, R.Policy("tight", complement_only=True, pair_ceiling=0.96), "C_fee_free")
    assert loose["taken"] == 2 and loose["stranded_residual"] == 0.0
    assert tight["taken"] == 1 and tight["stranded_residual"] == 10.0


def test_a_residual_cap_blocks_the_leading_leg_but_never_the_repair():
    fills = [(0, 0.40, "Up", 10.0, 4.0),      # opens
             (5, 0.41, "Up", 10.0, 4.1),      # leading: would take residual to 20
             (9, 0.55, "Down", 10.0, 5.5)]    # complement: always allowed
    r = R.run_window(_w(fills), R.Policy("cap10", resid_cap=10), "C_fee_free")
    assert r["taken"] == 2 and r["refused"] == 1
    assert r["stranded_residual"] == 0.0


def test_tte_gate_stops_new_inventory_but_not_repair():
    fills = [(0, 0.40, "Up", 10.0, 4.0),       # tte 1000, opens
             (700, 0.41, "Up", 10.0, 4.1),     # tte 300, leading -> blocked
             (800, 0.55, "Down", 10.0, 5.5)]   # tte 200, complement -> allowed
    r = R.run_window(_w(fills), R.Policy("t", tte_no_open=500), "C_fee_free")
    assert r["taken"] == 2 and r["refused"] == 1 and r["stranded_residual"] == 0.0


def test_repair_relaxes_as_expiry_approaches():
    p = R.Policy("relax", pair_ceiling=0.96, tte_relax=[(300, 1.00), (120, 1.05)])
    assert p.ceiling_at(900) == 0.96
    assert p.ceiling_at(250) == 1.00
    assert p.ceiling_at(60) == 1.05


def test_the_four_fee_scenarios_order_as_their_economics_require():
    px, sz = 0.40, 100.0
    usd = px * sz * (1 + R.L5_FEE * (1 - px))          # a fee-bearing observed fill
    c = {s: R.cost_of(px, sz, usd, s) for s in R.SCENARIOS}
    assert c["B_pmus_maker"] < c["C_fee_free"] < c["D_all_fee"]
    assert abs(c["A_observed"] - c["D_all_fee"]) < 1e-9   # this fill WAS fee-bearing
    assert abs(c["C_fee_free"] - 40.0) < 1e-9
    # the PMUS rebate is credited, so cost falls below notional
    assert abs(c["B_pmus_maker"] - (40.0 - 0.0125 * 0.4 * 0.6 * 100)) < 1e-9


def test_decomposition_reconciles_to_realized_cash():
    fills = [(0, 0.40, "Up", 10.0, 4.0), (10, 0.55, "Down", 6.0, 3.3)]
    for won in (True, False):
        r = R.run_window(_w(fills, won_up=won), R.Policy("control"), "C_fee_free")
        assert abs((r["matched_pnl"] + r["residual_pnl"]) - r["net"]) < 1e-9


def test_a_window_with_no_redeem_row_is_scored_as_a_loss_not_dropped():
    fills = [(0, 0.40, "Up", 10.0, 4.0)]
    w = {"cid": "x", "t": 0, "end": 1000, "s": fills, "won_up": False,
         "outcome_source": "no_redeem_implies_loss"}
    r = R.run_window(w, R.Policy("control"), "C_fee_free")
    assert r["net"] == -4.0 and r["residual_pnl"] == -4.0
