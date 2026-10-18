"""The fee-quantum gate: refuse-only, conservative, and scale-aware.

The claims under test are arithmetic, so they are asserted exactly rather
than approximately. The point of the module is that cent rounding is NOT
negligible at our clip, so any test that rounded would test nothing.
"""
from decimal import Decimal as D

import pytest

import src.l5_fee_quantum_gate as G


# ------------------------------------------------ the motivating numbers

def test_our_own_observed_shadow_quote_is_refused():
    """pair 0.97 at qty 1.25 -- what the shadow actually quoted, 3x."""
    r = G.evaluate(up_px="0.60", dn_px="0.37", qty="1.25")
    assert r["ok"] is False
    assert r["refusal"] == "MARGIN_BELOW_FEE_QUANTUM"
    assert r["gross_usd"] == "0.0375"
    assert D(r["net_usd"]) < 0


def test_the_fee_is_proportional_so_no_size_rescues_a_0_97_pair():
    """The decisive result: gross is 3.00% of qty and the taker fee 3.29%,
    so the pair loses at EVERY size. Rounding only worsens small clips."""
    assert G.breakeven_qty(up_px="0.60", dn_px="0.37") is None
    for q in ("1", "5", "25", "100"):
        assert G.evaluate(up_px="0.60", dn_px="0.37", qty=q)["ok"] is False


def test_0_96_viability_is_NOT_monotone_in_size_but_a_rounding_comb():
    """THE FINDING, and it refutes the "minimum clip" framing I started
    with. At the deployed <=0.96 discipline the gross-to-fee gap is 0.70pp
    of notional while ONE CENT of rounding on a ~$1 leg is 1.0pp, so the
    cent decides the SIGN and passing is not monotone in qty.

    0.51 passes; 0.75 through 2.50 -- which spans the whole authorized
    clip range -- do not; 5 and 10 do."""
    g = lambda q: G.evaluate(up_px="0.59", dn_px="0.37", qty=q)["ok"]
    assert g("0.51") is True
    for q in ("0.75", "1.00", "1.25", "1.50", "2.00", "2.50"):
        assert g(q) is False, q
    assert g("5.00") is True and g("10.00") is True


def test_breakeven_qty_returns_the_smallest_passer_not_a_floor():
    """So the name must not be read as "anything larger passes"."""
    q = G.breakeven_qty(up_px="0.59", dn_px="0.37")
    assert q == D("0.51")
    # 1.25 is larger than the "break-even" qty and still refuses
    assert G.evaluate(up_px="0.59", dn_px="0.37", qty="1.25")["ok"] is False


def test_cheaper_pairs_need_less_size():
    assert (G.breakeven_qty(up_px="0.56", dn_px="0.37")
            < G.breakeven_qty(up_px="0.59", dn_px="0.37"))


# ------------------------------------------------ the fee itself

def test_leg_fee_reproduces_a_real_venue_charge_exactly():
    """cpc-btc-updown-15m-2026-10-15-2230z|DOWN: cost 0.2921 in ONE fill,
    charged $0.0200. 0.0695*1*0.2921*0.7079 = 0.01437 -> ceil 0.02."""
    assert G.leg_fee("1", "0.2921") == D("0.02")


def test_rounding_is_charged_per_fill_not_per_order():
    """cpcxrp-...-0330z|UP carried five fills and $0.0500 -- a cent each.
    Cent rounding does not pool, so five fills cost five cents."""
    one = G.leg_fee("1", "0.2921", fills_per_leg=1)
    five = G.leg_fee("1", "0.2921", fills_per_leg=5)
    assert five > one
    assert five == D("0.05")


def test_the_rounding_surcharge_decays_with_size():
    """0.55 of a cent billed as a cent is an 83% surcharge; the same
    formula at 10 contracts rounds 4.4 cents to 5, a 14% surcharge."""
    small = G.leg_fee("1.25", "0.50")
    big = G.leg_fee("10", "0.50")
    raw_small = D("0.0695") * D("1.25") * D("0.5") * D("0.5")
    raw_big = D("0.0695") * D("10") * D("0.5") * D("0.5")
    assert small == D("0.03") and big == D("0.18")
    # 2.17 cents billed as 3 is a 38% surcharge; 17.4 billed as 18 is 3.6%
    assert (small - raw_small) / raw_small > D("0.35")
    assert (big - raw_big) / raw_big < D("0.05")


def test_the_maker_rebate_is_never_netted():
    """A credit we are not guaranteed cannot pay a fee we are. There is no
    parameter that subtracts a rebate, and the default theta is TAKER."""
    assert G.FEE_THETA_WORST == D("0.0695")
    assert "rebate" in G._BASIS.lower()


# ------------------------------------------------ fails closed

@pytest.mark.parametrize("kw,why", [
    (dict(up_px=None, dn_px="0.37", qty="1"), "PRICE_OR_QTY_UNREADABLE"),
    (dict(up_px="0.60", dn_px=None, qty="1"), "PRICE_OR_QTY_UNREADABLE"),
    (dict(up_px="0.60", dn_px="0.37", qty=None), "PRICE_OR_QTY_UNREADABLE"),
    (dict(up_px="0.60", dn_px="0.37", qty="0"), "NON_POSITIVE_QTY"),
    (dict(up_px="0.60", dn_px="0.37", qty="-1"), "NON_POSITIVE_QTY"),
    (dict(up_px="0.70", dn_px="0.37", qty="1"), "PAIR_COST_AT_OR_ABOVE_PAR"),
])
def test_unreadable_or_impossible_input_is_a_refusal_never_a_pass(kw, why):
    r = G.evaluate(**kw)
    assert r["ok"] is False and r["refusal"] == why


def test_an_unreadable_price_is_not_a_free_leg():
    assert G.leg_fee("1", None) is None
    assert G.leg_fee("1", "1.5") is None
    assert G.leg_fee(None, "0.5") is None


def test_a_higher_required_ratio_can_only_refuse_more():
    kw = dict(up_px="0.59", dn_px="0.37", qty="10")
    assert G.evaluate(**kw, required_ratio=D("1"))["ok"] is True
    assert G.evaluate(**kw, required_ratio=D("5"))["ok"] is False


def test_the_gate_only_ever_refuses():
    """It returns a verdict and nothing else: no arming, no limit, no
    mutation. The module exposes no setter and no state."""
    import inspect
    src = inspect.getsource(G)
    for forbidden in ("os.environ", "open(", "requests", "urllib",
                      "submit", "arm(", "subprocess"):
        assert forbidden not in src
