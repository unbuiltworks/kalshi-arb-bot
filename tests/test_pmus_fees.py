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


@pytest.mark.parametrize("row", TABLE["rows"], ids=lambda r: r["price"])
def test_every_published_row_reproduces_exactly(row):
    p = D(row["price"])
    assert str(F.taker_fee(p, D(100), cents=True)) == row["taker_pays"]
    assert str(F.maker_rebate(p, D(100), cents=True)) == row["maker_receives"]


def test_the_current_taker_coefficient_is_the_republished_one():
    assert F.taker_theta() == D("0.0695")
    assert F.maker_theta() == D("0.0125")


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
    assert q1.taker_fee(D("0.50")) == D("0.0174")


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
    assert F.taker_rebate_pct(D("250000")) == D("0.10")
    assert F.taker_rebate_pct(D("10000000")) == D("0.50")
    assert F.provenance()["taker_rebate_available_to_this_account"] is False


def test_the_maker_rebate_is_returned_as_a_positive_credit():
    """It is a credit reported on its own line, never a negative fee netted
    into edge."""
    assert F.maker_rebate(D("0.50")) > 0


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
    assert "0.0695" in PMUSAdapter().provenance()["fees"]
