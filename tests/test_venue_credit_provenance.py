"""PMUS and Gemini credits must never pool into one coefficient.

2026-09-10: no row in venue_rebate_credits.json carried a venue, and both
venues credit "maker rebate" against this account. We came within one edit of
re-attributing a 25-row PMUS sample to Gemini. The primary source settled it,
but a coefficient computed over a pooled set would have been wrong for BOTH
venues while looking entirely reasonable.
"""
from __future__ import annotations

import json

import pytest

from src import l5_venue_credits as vc

LEDGER = "analysis/l5/venue_rebate_credits.json"


def test_the_real_ledger_is_fully_venue_tagged():
    rows = vc.load_credits(LEDGER)
    assert len(rows) == 25
    assert {r["venue"] for r in rows} == {"polymarket_us"}


def test_the_pmus_sample_still_reproduces_after_tagging():
    """The calibration must survive the migration untouched."""
    rows = vc.for_venue(vc.load_credits(LEDGER), "polymarket_us")
    got = vc.coefficient_inputs(rows)
    assert got["contracts"] == "119.4900"
    assert got["credited_usd"] == "0.4200"
    assert abs(float(got["gross_per_contract"]) - 0.003515) < 1e-6


def test_every_row_keeps_its_raw_identifier():
    for r in vc.load_credits(LEDGER):
        assert r["raw_source_id"] == r["id"], "classification must stay auditable"


def test_a_row_without_a_venue_is_refused_not_defaulted():
    with pytest.raises(vc.MissingVenue):
        vc.validate_row({"id": "X1", "qty": "5"})


def test_an_unknown_venue_is_refused():
    with pytest.raises(vc.UnknownVenue):
        vc.validate_row({"id": "X1", "venue": "kalshi"})


def test_mixing_venues_into_one_coefficient_is_REFUSED():
    """THE POINT. A pooled k describes neither venue and reads plausible."""
    mixed = [
        {"id": "P1", "venue": "polymarket_us", "qty": "10",
         "our_commission": "-0.04", "credited_to_us": True},
        {"id": "G1", "venue": "gemini", "qty": "10",
         "our_commission": "-0.02", "credited_to_us": True},
    ]
    with pytest.raises(vc.MixedVenues):
        vc.coefficient_inputs(mixed)
    # and each alone is fine
    for v in ("polymarket_us", "gemini"):
        vc.coefficient_inputs(vc.for_venue(mixed, v))


def test_gemini_gross_is_not_income_because_gemini_charges_makers():
    """Polymarket's maker fee is zero; Gemini's is not. A gross rebate quoted
    without its maker fee is not a comparable number."""
    assert vc.MAKER_FEE_IS_ZERO["polymarket_us"] is True
    assert vc.MAKER_FEE_IS_ZERO["gemini"] is False


def test_absent_gemini_records_raise_rather_than_reading_as_zero():
    """'no Gemini credits' and 'could not read Gemini credits' are different
    facts. Only one of them is a measurement."""
    with pytest.raises(vc.SourceUnavailable):
        vc.ingest_gemini_admin_credits(source="/nonexistent/gemini.json")


def test_ingested_gemini_rows_are_tagged_and_keep_provenance(tmp_path):
    src = tmp_path / "g.json"
    src.write_text(json.dumps({"transfers": [
        {"eid": "A1", "type": "Predictions maker rebate",
         "instrument": "GEMI-BTC", "amount": "0.03"}]}))
    out = vc.ingest_gemini_admin_credits(source=str(src))
    assert out[0]["venue"] == "gemini"
    assert out[0]["raw_source_id"] == "A1"
    assert out[0]["raw_source_label"] == "Predictions maker rebate"


def test_the_published_example_table_is_not_an_accepted_source():
    """Gemini's docs illustrate +$0.08/100 contracts. That is their example,
    not our executions, and must never enter the ledger as data."""
    import inspect
    src = inspect.getsource(vc.ingest_gemini_admin_credits)
    assert "example table is NOT a substitute" in src
    assert "0.08" not in src, "no published figure may be hardcoded as data"
