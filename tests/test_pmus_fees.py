"""The PMUS fee schedule, checked against the venue's own published table.

`0.06` was the taker coefficient in eight places. PMUS republished it at
0.0695 effective 12 AM ET 2026-09-17 and none of the copies moved. On the
execution path that understated taker cost by 15.8% and therefore LOOSENED
the fee-safe complement bound, which is the dangerous direction.

The venue publishes a 99-row table -- what a 100-lot taker pays and a 100-lot
maker receives at every price from $0.01 to $0.99. Checking the headline
coefficients would prove much less than checking all 99 rows, because the rows
also pin the rounding rule. The captured table is
`analysis/pmus/pmus_published_fee_schedule.json`.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from decimal import Decimal as D

import pytest

from src import pmus_fees as F

ROOT = pathlib.Path(__file__).resolve().parents[1]
TABLE = json.loads(
    (ROOT / "analysis/pmus/pmus_published_fee_schedule.json").read_text())


def test_the_captured_table_is_the_whole_published_range():
    rows = TABLE["rows"]
    assert len(rows) == 99
    assert rows[0]["price"] == "0.01" and rows[-1]["price"] == "0.99"


#: the captured table was published under the FLAT schedule, so it is
#: reproduced AT that regime's instant. Re-rating it at today's tiered
#: coefficients would corrupt evidence that was right when it was recorded,
#: which is the same reason this module is effective-dated at all.
FLAT_INSTANT = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize("row", TABLE["rows"], ids=lambda r: r["price"])
def test_every_published_row_reproduces_exactly(row):
    p = D(row["price"])
    assert str(F.taker_fee(p, D(100), cents=True, when=FLAT_INSTANT)) == \
        row["taker_pays"]
    assert str(F.maker_rebate(p, D(100), cents=True, when=FLAT_INSTANT)) == \
        row["maker_receives"]


def test_the_flat_regime_coefficients_are_still_the_republished_ones():
    assert F.taker_theta(FLAT_INSTANT) == D("0.0695")
    assert F.maker_theta(FLAT_INSTANT) == D("0.0125")


def test_this_account_qualifies_for_the_worst_tier_on_both_schedules():
    """THE DEFECT THIS DOCUMENTS. 0.0695 is now Taker Tier 4. This account
    is Tier 1 on both schedules: 0.095 taker and NO maker rebate."""
    assert F.tiered() is True
    assert F.account_thetas() == (D("0.095"), D("0"))
    # the top tier is still reachable, but only by asking for it
    assert F.thetas(taker_volume_usd=D("250000"),
                    maker_volume_usd=D("200000000")) == (D("0.0695"),
                                                         D("0.0104"))


def test_the_default_IS_this_accounts_tier(monkeypatch):
    """Owner instruction 2026-11-17: correct the coefficient to the
    verified account value. The default and the account figure are now
    the same thing, and the published TOP tier must be asked for."""
    assert F.thetas() == F.account_thetas() == (D("0.095"), D("0"))
    assert F.taker_theta() == D("0.095")
    assert F.maker_theta() == D("0")
    assert F.thetas(taker_volume_usd=D("250000")) != F.thetas()


def test_correcting_the_coefficient_did_not_permit_taker_execution():
    """What the gates BELIEVE a taker fee costs is not permission to send
    a taker order. The correction must not have touched that."""
    from src import l5_btc5m_controller as C
    assert C.Params().taker_allowed is False


def test_history_is_still_priced_at_the_coefficient_that_applied(monkeypatch):
    """The whole reason this module is effective-dated: evidence recorded
    under the flat schedule must not be silently re-rated."""
    assert F.thetas(FLAT_INSTANT) == (D("0.0695"), D("0.0125"))
    before = dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc)
    assert F.thetas(before) == (D("0.06"), D("0.0125"))


def test_the_account_figure_is_the_worst_tier_on_both_tables():
    """What this account qualifies for is the extreme of each table: the
    highest taker coefficient and the lowest (zero) maker rebate."""
    at, am = F.account_thetas()
    assert at == max(c for _f, c in F.TAKER_FEE_TIERS)
    assert am == min(c for _f, c in F.MAKER_REBATE_TIERS)
    assert am == D("0")


@pytest.mark.parametrize("vol,coef", [
    ("0", "0.095"), ("9999.99", "0.095"),
    ("10000", "0.085"), ("49999.99", "0.085"),
    ("50000", "0.075"), ("249999.99", "0.075"),
    ("250000", "0.0695"), ("99999999", "0.0695")])
def test_every_taker_fee_tier_boundary(vol, coef):
    assert F.taker_theta(taker_volume_usd=D(vol)) == D(coef)


@pytest.mark.parametrize("vol,coef", [
    ("0", "0"), ("9999999.99", "0"),
    ("10000000", "0.0035"), ("49999999.99", "0.0035"),
    ("50000000", "0.0070"), ("199999999.99", "0.0070"),
    ("200000000", "0.0104")])
def test_every_maker_rebate_tier_boundary(vol, coef):
    """Tier 1 is ZERO, not a small rebate. $10,000,000 of 30-day maker
    volume is the first threshold, against this account's ~$38,880 of
    monthly capacity under its ceilings."""
    assert F.maker_theta(maker_volume_usd=D(vol)) == D(coef)


def test_a_lower_tier_never_costs_less_or_credits_more():
    """Monotonicity, so a MIS-resolved tier can only ever err conservatively
    in the direction this account cares about."""
    vols = [D(v) for v in ("0", "10000", "50000", "250000", "10000000",
                           "50000000", "200000000")]
    takers = [F.taker_theta(taker_volume_usd=v) for v in vols]
    makers = [F.maker_theta(maker_volume_usd=v) for v in vols]
    assert takers == sorted(takers, reverse=True)
    assert makers == sorted(makers)


def test_the_tier_boundary_is_midnight_ET_expressed_in_UTC():
    """12:00 AM ET, Saturday 2026-11-14, when ET was UTC-5 (EST)."""
    assert F.TIER_REGIME_UTC == dt.datetime(
        2026, 11, 14, 5, 0, tzinfo=dt.timezone.utc)
    # the ACCOUNT figure is what moves at the boundary; the default does
    # not, by design
    assert F.account_thetas(F.TIER_REGIME_UTC) == (D("0.095"), D("0"))
    assert F.account_thetas(
        F.TIER_REGIME_UTC - dt.timedelta(seconds=1)) == (D("0.0695"),
                                                          D("0.0125"))


def test_the_taker_fee_this_account_faces_is_materially_HIGHER():
    """How much the correction tightened bounds by, quantified."""
    top = F.taker_fee(D("0.44"), D(100), taker_volume_usd=D("250000"))
    ours = F.taker_fee(D("0.44"), D(100))
    assert ours > top
    assert (ours / top - 1) > D("0.36")


def test_the_september_credits_are_not_evidence_of_a_tiered_rebate():
    """CREDITED_OBSERVED is from 2026-09-06/07, inside the FLAT regime. It
    must not be read as proof this account is paid a rebate today."""
    assert F.CREDITED_OBSERVED["rows"] == 25
    assert F.account_thetas()[1] == D("0")
    assert F.provenance()["maker_rebate_available_to_this_account"] is False


def test_a_pre_change_instant_still_gets_the_old_coefficient():
    """Analyses of fills from before 2026-09-17 are CORRECT at 0.06; silently
    re-rating them would corrupt evidence that was right when recorded."""
    before = dt.datetime(2026, 9, 16, 23, 59, tzinfo=dt.timezone.utc)
    assert F.taker_theta(before) == D("0.06")


def test_the_boundary_is_midnight_ET_expressed_in_UTC():
    """12 AM ET on 2026-09-17, when ET was UTC-4."""
    assert F.THETA_CHANGE_UTC == dt.datetime(
        2026, 9, 17, 4, 0, tzinfo=dt.timezone.utc)
    assert F.taker_theta(F.THETA_CHANGE_UTC) == D("0.0695")
    assert F.taker_theta(
        F.THETA_CHANGE_UTC - dt.timedelta(seconds=1)) == D("0.06")


def test_the_execution_path_reads_the_schedule_and_not_a_local_copy():
    from src import pmus_q1_executor as q1
    assert q1.taker_fee(D("0.50")) == F.taker_fee(D("0.50"))
    assert q1.maker_rebate(D("0.50")) == F.maker_rebate(D("0.50"))
    # and the number actually moved, so this test would have failed before
    assert q1.taker_fee(D("0.50")) == D("0.0238")   # Tier 1, 0.095


def test_no_execution_module_still_hardcodes_the_stale_coefficient():
    """Analysis modules may keep 0.06 for pre-change data. The EXECUTION path
    may not.

    Parsed from the AST, not grepped: the fix's own docstring says the word
    `D("0.06")` while explaining what it replaced, and a text search cannot
    tell that from live code.
    """
    import ast
    for name in ("pmus_q1_executor.py", "pmus_micro_executor.py"):
        tree = ast.parse((ROOT / "src" / name).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(
                f, "id", None)
            if fname not in ("D", "Decimal"):
                continue
            for a in node.args:
                if isinstance(a, ast.Constant) and a.value == "0.06":
                    raise AssertionError(
                        "%s:%d still builds the stale taker coefficient"
                        % (name, node.lineno))


def test_a_per_contract_fee_is_not_quantised_away():
    """Rounding a one-contract fee to the cent would turn a real charge into
    $0.00 and make a bound look safer than it is."""
    assert F.taker_fee(D("0.50"), D(1)) > 0
    assert F.taker_fee(D("0.50"), D(1), cents=True) == D("0.02")


def test_the_taker_rebate_is_zero_for_this_account():
    assert F.taker_rebate_pct(D("0")) == D("0")
    assert F.taker_rebate_pct(D("249999")) == D("0")
    assert F.provenance()["taker_rebate_available_to_this_account"] is False


@pytest.mark.parametrize("vol,pct", [
    ("249999", "0"), ("250000", "0.03"), ("1000000", "0.05"),
    ("10000000", "0.10"), ("25000000", "0.15"), ("50000000", "0.30")])
def test_the_tiered_taker_rebate_table(vol, pct):
    """The taker-rebate table IS switched by date, because it is reporting
    only -- no risk bound is derived from it."""
    assert F.taker_rebate_pct(D(vol)) == D(pct)


def test_the_flat_regime_taker_rebate_table_is_preserved():
    """Pre-2026-11-14 analyses were right at the old table."""
    assert F.taker_rebate_pct(D("250000"), when=FLAT_INSTANT) == D("0.10")
    assert F.taker_rebate_pct(D("10000000"), when=FLAT_INSTANT) == D("0.50")


def test_the_maker_rebate_is_returned_as_a_positive_credit():
    """It is a credit reported on its own line, never a negative fee netted
    into edge. Checked where a rebate exists at all -- at this account's
    current tier it is zero, which is asserted separately."""
    assert F.maker_rebate(D("0.50"), when=FLAT_INSTANT) > 0
    assert F.maker_rebate(D("0.50"), maker_volume_usd=D("200000000")) > 0
    # ...and zero at this account's tier, which is asserted separately
    assert F.maker_rebate(
        D("0.50"), maker_volume_usd=F.ACCOUNT_MAKER_VOLUME_USD) == 0


def test_the_crypto_adapter_reports_the_literal_clock_and_the_BRTI_rule():
    from src.l5_venue_adapter import PMUSAdapter, PMUS_PRODUCT_CRYPTO_UPDOWN
    s = PMUSAdapter(PMUS_PRODUCT_CRYPTO_UPDOWN).settlement()
    assert "BRTI" in s["index"]
    assert "LITERAL" in s["clock"]
    assert "must NOT be applied" in s["clock"]
    assert "greater than or equal" in s["rule"]


def test_the_sports_adapter_is_unchanged_by_default():
    from src.l5_venue_adapter import PMUSAdapter
    s = PMUSAdapter().settlement()
    assert "game end" in s["clock"]
    assert "index" not in s


def test_an_undeclared_product_is_refused():
    from src.l5_venue_adapter import PMUSAdapter
    with pytest.raises(ValueError):
        PMUSAdapter("crypto")


def test_the_adapter_quotes_the_live_coefficient_not_a_frozen_string():
    from src.l5_venue_adapter import PMUSAdapter
    assert "0.095" in PMUSAdapter().provenance()["fees"]


# --------------------------------------------------------------------------
# The complement grid is a property of the MARKET, not of the module.

def test_the_default_grid_is_unchanged_so_the_sports_path_is_byte_identical():
    from src import pmus_q1_executor as q1
    for cost in ("0.20", "0.33", "0.48", "0.61", "0.755"):
        assert q1.complement_bound(D(cost)) == q1.complement_bound(
            D(cost), tick=q1.TICK)


def test_a_bound_on_a_one_cent_market_lands_on_the_one_cent_grid():
    """0.475 is arithmetically inside the bound and is NOT a price PMUS
    BTC15m accepts."""
    from src import pmus_q1_executor as q1
    for cost in ("0.20", "0.33", "0.48", "0.505", "0.61", "0.755"):
        px = q1.complement_bound(D(cost), tick=D("0.01"))
        if px is None:
            continue
        assert (px / D("0.01")) % 1 == 0, (cost, px)


def test_a_coarser_grid_is_never_looser_than_a_finer_one():
    """Rounding stays DOWN, so a coarser grid can only be more conservative."""
    from src import pmus_q1_executor as q1
    for cost in ("0.20", "0.33", "0.48", "0.61", "0.755"):
        fine = q1.complement_bound(D(cost), tick=D("0.005"))
        coarse = q1.complement_bound(D(cost), tick=D("0.01"))
        if fine is None or coarse is None:
            continue
        assert coarse <= fine, (cost, fine, coarse)


def test_an_implausible_grid_refuses_rather_than_dividing_by_it():
    from src import pmus_q1_executor as q1
    assert q1.complement_bound(D("0.48"), tick=D("0")) is None
    assert q1.complement_bound(D("0.48"), tick=D("1")) is None


def test_the_venue_grid_is_read_from_the_market_and_validated():
    from src import pmus_q1_executor as q1
    assert q1.tick_for_market({"orderPriceMinTickSize": 0.01}) == D("0.01")
    assert q1.tick_for_market({"orderPriceMinTickSize": 0.005}) == D("0.005")
    assert q1.tick_for_market({}) is None
    assert q1.tick_for_market({"orderPriceMinTickSize": 0}) is None
    assert q1.tick_for_market({"orderPriceMinTickSize": "nonsense"}) is None
    assert q1.tick_for_market(None) is None


def test_the_adapter_no_longer_discards_the_grid_it_was_handed():
    """The chosen price was rounded onto the caller's tick while the BOUND was
    computed on the module constant."""
    from decimal import Decimal
    from src import l5_venue_adapter as va
    px, econ = va.PMUSAdapter().complement_price(
        Decimal("0.48"), Decimal("0.55"), Decimal("0.01"))
    assert px is not None and (px / Decimal("0.01")) % 1 == 0
    assert econ["hard_bound_px"] is not None
    assert (Decimal(econ["hard_bound_px"]) / Decimal("0.01")) % 1 == 0
