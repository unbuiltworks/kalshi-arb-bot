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
TIERED = json.loads(
    (ROOT / "analysis/pmus/pmus_published_fee_schedule_20261112.json").read_text())
U = dt.timezone.utc
#: an instant inside the FLAT 0.0695 / 0.0125 era the first table describes
FLAT_ERA = dt.datetime(2026, 10, 1, tzinfo=U)
#: an instant inside the TIERED schedule
TIERED_ERA = dt.datetime(2026, 11, 12, tzinfo=U)


def test_the_captured_table_is_the_whole_published_range():
    rows = TABLE["rows"]
    assert len(rows) == 99
    assert rows[0]["price"] == "0.01" and rows[-1]["price"] == "0.99"


@pytest.mark.parametrize("row", TABLE["rows"], ids=lambda r: r["price"])
def test_every_published_row_reproduces_exactly(row):
    p = D(row["price"])
    assert str(F.taker_fee(p, D(100), cents=True, when=FLAT_ERA)) == row["taker_pays"]
    assert str(F.maker_rebate(p, D(100), cents=True, when=FLAT_ERA)) == row["maker_receives"]


def test_the_flat_era_coefficients_are_the_september_ones():
    assert F.taker_theta(FLAT_ERA) == D("0.0695")
    assert F.maker_theta(FLAT_ERA) == D("0.0125")


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
    # the tiered schedule, Tier 1 (this account): 0.095 * 0.25
    assert q1.taker_fee(D("0.50")) == D("0.0238")


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
    assert F.taker_rebate_pct(D("0"), FLAT_ERA) == D("0")
    assert F.taker_rebate_pct(D("249999"), FLAT_ERA) == D("0")
    assert F.taker_rebate_pct(D("250000"), FLAT_ERA) == D("0.10")
    assert F.taker_rebate_pct(D("10000000"), FLAT_ERA) == D("0.50")
    # tiered schedule: 3/5/10/15/30 %, paid weekly
    assert F.taker_rebate_pct(D("249999"), TIERED_ERA) == D("0")
    assert F.taker_rebate_pct(D("250000"), TIERED_ERA) == D("0.03")
    assert F.taker_rebate_pct(D("1000000"), TIERED_ERA) == D("0.05")
    assert F.taker_rebate_pct(D("50000000"), TIERED_ERA) == D("0.30")
    assert F.provenance()["taker_rebate_available_to_this_account"] is False


def test_the_maker_rebate_is_returned_as_a_positive_credit():
    """It is a credit reported on its own line, never a negative fee netted
    into edge."""
    assert F.maker_rebate(D("0.50"), when=FLAT_ERA) > 0
    assert F.maker_rebate(D("0.50"), when=TIERED_ERA, maker_tier=2) > 0


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
    assert str(F.taker_theta()) in PMUSAdapter().provenance()["fees"]


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



# ---------- the tiered schedule (effective 10 PM ET 2026-10-28) ----------

def test_the_tiered_schedule_starts_at_10pm_ET_oct_28():
    assert F.TIER_SCHEDULE_UTC == dt.datetime(2026, 10, 29, 2, 0, tzinfo=U)
    before = F.TIER_SCHEDULE_UTC - dt.timedelta(seconds=1)
    assert F.thetas(before) == (D("0.0695"), D("0.0125"))
    assert F.thetas(F.TIER_SCHEDULE_UTC) == (D("0.095"), D("0"))


def test_this_account_is_tier_1_no_maker_rebate(monkeypatch):
    monkeypatch.delenv(F.MAKER_TIER_ENV, raising=False)
    monkeypatch.delenv(F.TAKER_TIER_ENV, raising=False)
    assert F.thetas(TIERED_ERA) == (D("0.095"), D("0"))
    assert F.maker_rebate(D("0.50"), D(1000), when=TIERED_ERA, cents=True) == 0
    assert F.rebate_floor_contracts(D("0.50"), TIERED_ERA) is None
    assert F.provenance(TIERED_ERA)["maker_rebate_available_to_this_account"] is False


@pytest.mark.parametrize("tier,taker,maker", [(1, "0.095", "0"), (2, "0.085", "0.0035"),
                                              (3, "0.075", "0.0070"), (4, "0.0695", "0.0104")])
def test_each_published_tier(tier, taker, maker):
    assert F.thetas(TIERED_ERA, taker_tier=tier, maker_tier=tier) == (D(taker), D(maker))


@pytest.mark.parametrize("bad", ["", "0", "5", "x", "-1", "4.5"])
def test_an_unreadable_tier_is_tier_1(monkeypatch, bad):
    monkeypatch.setenv(F.MAKER_TIER_ENV, bad)
    monkeypatch.setenv(F.TAKER_TIER_ENV, bad)
    assert F.thetas(TIERED_ERA) == (D("0.095"), D("0"))


def test_an_owner_confirmed_tier_is_read_from_the_env(monkeypatch):
    monkeypatch.setenv(F.MAKER_TIER_ENV, "2")
    assert F.maker_theta(TIERED_ERA) == D("0.0035")


def test_historical_fills_are_never_re_rated():
    sept = dt.datetime(2026, 9, 6, tzinfo=U)
    assert F.maker_rebate(D("0.50"), D("5.15"), when=sept, cents=True) == D("0.02")


@pytest.mark.parametrize("row", TIERED["rows"], ids=lambda r: r["price"])
def test_every_tiered_published_row_reproduces_exactly(row):
    """The 2026-11-12 table is printed for Taker Tier 4 / Maker Tier 4."""
    p = D(row["price"])
    assert str(F.taker_fee(p, D(100), cents=True, when=TIERED_ERA,
                           taker_tier=4)) == row["taker_pays_tier4"]
    assert str(F.maker_rebate(p, D(100), cents=True, when=TIERED_ERA,
                              maker_tier=4)) == row["maker_receives_tier4"]
