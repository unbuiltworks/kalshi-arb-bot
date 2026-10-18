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


def test_the_fee_is_proportional_so_no_real_size_rescues_a_0_97_pair():
    """gross is 3.00% of qty and the taker fee 3.29%, so the pair loses at
    every size that pays a fee at all -- and it loses MORE as it grows."""
    for q in ("1", "1.25", "5", "25", "100"):
        assert G.evaluate(up_px="0.60", dn_px="0.37", qty=q)["ok"] is False
    assert D(G.evaluate(up_px="0.60", dn_px="0.37", qty="100")["net_usd"]) \
        < D(G.evaluate(up_px="0.60", dn_px="0.37", qty="1")["net_usd"])


def test_a_sub_half_cent_clip_rounds_the_fee_to_zero_and_that_is_a_TRAP():
    """Round-to-nearest means a raw fee under half a cent is charged as
    nothing, so a pair that is structurally negative still "passes" at a
    0.01 clip. The gate reports it truthfully and the caller must not read
    it as a way to make a bad pair good: the margin is $0.0003.

    IT IS ALSO NOT THE EXPLANATION FOR OUR ZERO-FEE FILLS. All four
    zero-fee single-fill legs in the audit carry raw fees of $0.0057 to
    $0.0174, which round to a cent, and were charged nothing anyway -- so
    those are genuine maker treatment, not this rounding."""
    r = G.evaluate(up_px="0.60", dn_px="0.37", qty="0.01")
    assert r["ok"] is True and r["fee_usd"] == "0.00"
    assert D(r["gross_usd"]) < D("0.001")


def test_0_96_viability_is_NOT_monotone_in_size_but_a_rounding_comb():
    """THE FINDING, and it refutes the "minimum clip" framing I started
    with. At the deployed <=0.96 discipline the gross-to-fee gap is 0.70pp
    of notional while ONE CENT of rounding on a ~$1 leg is 1.0pp, so the
    cent decides the SIGN and passing is not monotone in qty.

    Measured at the IDENTIFIED round-to-nearest: 0.50 and 1.00 land exactly
    on break-even and refuse, while 0.51, 0.75, 1.25, 2.00 and 5.00 pass."""
    g = lambda q: G.evaluate(up_px="0.59", dn_px="0.37", qty=q)["ok"]
    for q in ("0.50", "1.00"):
        assert g(q) is False, q
    for q in ("0.51", "0.75", "1.25", "2.00", "5.00", "10.00"):
        assert g(q) is True, q


def test_breakeven_qty_returns_the_smallest_passer_not_a_floor():
    """Smallest passer is the degenerate fee-rounds-to-zero clip, and
    larger sizes can still refuse -- so it is not a floor."""
    assert G.breakeven_qty(up_px="0.59", dn_px="0.37") == D("0.01")
    assert G.evaluate(up_px="0.59", dn_px="0.37", qty="1.00")["ok"] is False


def test_a_cheaper_pair_nets_more_at_the_same_size():
    cheap = G.evaluate(up_px="0.56", dn_px="0.37", qty="5")
    dear = G.evaluate(up_px="0.59", dn_px="0.37", qty="5")
    assert cheap["ok"] and dear["ok"]
    assert D(cheap["net_usd"]) > D(dear["net_usd"])


# ------------------------------------------------ the fee itself

def test_leg_fee_reproduces_the_real_venue_charge_at_the_real_quantity():
    """cpc-btc-updown-15m-2026-10-15-2230z|DOWN: the 2026-10-16 audit gives
    qty 1.27 at avg price 0.7700 in ONE fill, charged $0.0200.
    0.0695*1.27*0.77*0.23 = 0.015626 -> round-to-nearest 0.02.

    An earlier version of this test assumed qty=1 and price=cost, which the
    audit's own quantities refuted."""
    assert G.leg_fee("1.27", "0.7700") == D("0.02")


def test_round_to_nearest_is_the_identified_behaviour_not_ceiling():
    """28 of 28 fee-bearing single-fill legs match nearest; ceiling matches
    only 17. Ceiling stays available as an explicit worst case."""
    assert G.leg_fee("1", "0.2921") == D("0.01")
    assert G.leg_fee("1", "0.2921",
                     rounding=G.ROUNDING_WORST_CASE) == D("0.02")


def test_rounding_is_charged_per_fill_and_can_cut_EITHER_way():
    """cpcxrp-...-0330z|UP: 50 shares at 0.02 over FIVE fills, charged
    $0.0500 -- 0.0136 per fill rounding to a cent each. One fill would have
    been 0.0681 -> $0.07.

    So under the IDENTIFIED round-to-nearest, splitting a leg can LOWER the
    bill, not only raise it. Cent rounding is super-additive only under
    CEILING, which is not what this venue does -- an earlier version of
    this test asserted the ceiling behaviour and was wrong."""
    one = G.leg_fee("50", "0.02", fills_per_leg=1)
    five = G.leg_fee("50", "0.02", fills_per_leg=5)
    assert five == D("0.05") and one == D("0.07")
    assert five < one
    # and the other direction exists too: 1.18 shares at 0.50 is $0.02 in
    # one fill and $0.04 in four, because each 0.0051 slice rounds to a cent
    assert G.leg_fee("1.18", "0.50", fills_per_leg=1) == D("0.02")
    assert G.leg_fee("1.18", "0.50", fills_per_leg=4) == D("0.04")


def test_the_rounding_surcharge_decays_with_size():
    """0.55 of a cent billed as a cent is an 83% surcharge; the same
    formula at 10 contracts rounds 4.4 cents to 5, a 14% surcharge."""
    small = G.leg_fee("1.25", "0.50")
    big = G.leg_fee("10", "0.50")
    raw_small = D("0.0695") * D("1.25") * D("0.5") * D("0.5")
    raw_big = D("0.0695") * D("10") * D("0.5") * D("0.5")
    assert small == D("0.02") and big == D("0.17")
    # rounding moves the small clip proportionally further than the large
    rel_small = abs(small - raw_small) / raw_small
    rel_big = abs(big - raw_big) / raw_big
    assert rel_small > rel_big


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
