"""OWNER #167 5574408755 item 3: the empirical PMUS incentive break-even
arithmetic -- whole-cent credits, the rounded step vs the smooth model,
the break-even solve, and unproven rewards at zero.  Offline."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_incentive_breakeven as be


def test_credits_are_whole_cents_with_the_venue_floor_and_steps():
    # below $0.005 raw -> nothing (the six uncredited prints of ours)
    assert be.credit_rounded(D("1.41"), D("0.50")) == D("0.00")
    assert be.credit_rounded(D("1.00"), D("0.79")) == D("0.00")
    # 2.52 sh @0.78: raw 0.00541 -> one cent (the smallest credit the venue paid us)
    assert be.credit_rounded(D("2.52"), D("0.78")) == D("0.01")
    # 5 sh @0.38: raw 0.014725 -> ONE cent, not two: the step matters at R4 size
    assert be.credit_rounded(D("5"), D("0.38")) == D("0.01")
    # 5 sh @0.47: raw 0.015569 -> two cents
    assert be.credit_rounded(D("5"), D("0.47")) == D("0.02")
    # banker's rounding at the exact half: 0.015 -> 0.02 (even)
    assert be.credit_rounded(D("5"), D("0.60")) == D("0.02")
    # symmetric in p: the NO print at 0.22 credits the same as YES at 0.78
    assert be.credit_rounded(D("5"), D("0.22")) == be.credit_rounded(D("5"), D("0.78"))


def test_every_observed_credit_with_an_exact_price_is_reproduced_by_the_rounded_step():
    rows, uncredited = be.credited_prints(be._load(be.CREDITS))
    assert len(rows) == 25 and sum(D(r["credit_usd"]) for r in rows) == D("0.42")
    exact = [r for r in rows if r["price_source"] == "exact"]
    assert len(exact) == 19 and all(r["model_matches"] for r in exact)
    assert all(r["model_matches"] for r in rows if r["price_source"] == "elimination")
    # the only misses are the wisc-nd prints priced at the market average (DERIVED)
    assert {r["market"] for r in rows if not r["model_matches"]} == {"asc-cfb-wisc-nd-2026-09-06-pos-10pt5"}
    # six sub-floor prints, every one $0.00 under the same rule
    assert len(uncredited) == 6 and all(u["model_matches"] for u in uncredited)
    # never more than two cents on one print of ours
    assert max(D(r["credit_usd"]) for r in rows) == D("0.02")


def test_pair_net_and_break_even_solve_at_r4_size():
    # q=5, p1=0.38, complement 0.62 (pair 1.00): cash 0, credits 0.01 + 0.01
    cash, cr, net = be.pair_net(D("5"), D("0.38"), D("0.62"))
    assert (cash, cr, net) == (D("0"), D("0.02"), D("0.02"))
    # at 1.01 the pair loses three cents even with both credits
    assert be.pair_net(D("5"), D("0.38"), D("0.63"))[2] == D("-0.03")
    # ceiling: one cent a leg -> 1 + 0.02/5 = 1.004; two cents a leg -> 1.008
    assert be.break_even_pair_cost(D("5"), D("0.38")) == D("1.004")
    assert be.break_even_pair_cost(D("5"), D("0.47")) == D("1.008")
    # net >= 1c ceilings sit one cent-per-qty lower
    assert be.break_even_pair_cost(D("5"), D("0.38"), floor=D("0.01")) == D("1.002")
    assert be.break_even_pair_cost(D("5"), D("0.47"), floor=D("0.01")) == D("1.006")
    # executable grid: 1.004 -> 1.000, 1.008 -> 1.005
    assert be.to_grid(D("1.004")) == D("1.000") and be.to_grid(D("1.008")) == D("1.005")
    # the smooth model is never the verdict, but it must agree to within a cent
    assert be.break_even_pair_cost(D("5"), D("0.38"), smooth=True) == D("1.005")


def test_report_tables_never_show_a_profitable_pair_above_1_008_and_unproven_is_zero():
    j = be.build()
    for r in j["C_net_per_pair_table"]["rows"]:
        assert r["unproven_rewards_usd"] == "0.00"
        if D(r["pair_cost"]) >= D("1.01"):
            assert r["verdict"] == "LOSS" and D(r["net_empirical_usd"]) < 0
        if D(r["pair_cost"]) <= D("1.00"):
            assert r["verdict"] == "profit"
    cs = [D(r["c_star_empirical_net_ge_0"]) for r in j["B_break_even_ceilings"]["rows"]]
    assert max(cs) == D("1.008") and min(cs) == D("1.003")
    assert all(not r["within_observed_sizes"] for r in j["B_break_even_ceilings"]["rows"] if r["qty"] == "6.5")
    assert j["D_unproven_rewards"]["total_value_in_break_even_usd"] == "0.00"
    assert all(r["value_in_break_even_usd"] == "0.00" for r in j["D_unproven_rewards"]["rows"])
    assert j["E_verdict"]["max_credit_ever_on_one_print_usd"] == "0.0200"
