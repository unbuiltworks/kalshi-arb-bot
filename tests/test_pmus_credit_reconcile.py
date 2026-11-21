"""Only a venue-reported credit on one of OUR fills may be called realized.

The figures here are the real ones recovered from the single surviving
incentive-audit run (2026-10-07T03:02:51Z) and from the 25-row September
credit sample preserved in `analysis/l5/venue_rebate_credits.json`.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src import pmus_credit_reconcile as R
from src import l5_venue_credits as vc

VERDICT = {
    "retrieved_at_utc": "2026-10-07T03:02:51.400379+00:00",
    "verdict": {
        "venue_credited_on_our_fills_usd": "10.3600",
        "venue_charged_us_on_our_fills_usd": "2.8600",
        "our_fills_with_a_credit": 825,
        "our_fills_at_or_above_rebate_floor": 944,
        "rebate_at_our_executed_size_after_rounding": "0.00",
        "observed_credits_earnings_endpoint": "102 rows, total 10.51",
        "published_programs_covering_our_markets": 0,
        "reading": "only observed / venue-confirmed credits are realized",
    },
}


def test_the_venue_reported_credit_is_realized():
    r = R.reconcile(VERDICT)[R.REALIZED]
    assert r["credited_usd"] == "10.3600"
    assert r["charged_usd"] == "2.8600"
    assert r["net_usd"] == "7.5000"
    assert r["fills_with_a_credit"] == 825


def test_the_earnings_total_is_kept_separate_and_never_summed():
    rec = R.reconcile(VERDICT)
    assert rec[R.UNATTRIBUTED]["earnings_endpoint_usd"] == "10.51"
    # the two must not be added into one number anywhere
    assert rec[R.REALIZED]["credited_usd"] == "10.3600"


def test_the_fifteen_cent_disagreement_is_carried_as_unresolved():
    """$10.51 against $10.36. The audit did not reconcile it, so neither
    figure is discarded and neither is averaged away."""
    items = {u["item"]: u for u in R.reconcile(VERDICT)["unresolved"]}
    u = items["earnings_endpoint_vs_per_fill_total"]
    assert D(u["difference_usd"]) == D("0.15")


def test_the_formula_estimate_is_never_realized():
    rec = R.reconcile(VERDICT)
    assert rec[R.NEVER]["formula_estimate_at_our_size_usd"] == "0.00"
    assert "formula_estimate_at_our_size_usd" not in rec[R.REALIZED]


def test_published_programs_are_never_realized():
    assert R.reconcile(VERDICT)[R.NEVER][
        "published_programs_covering_our_markets"] == 0


def test_missing_per_fill_detail_is_reported_as_unresolved_with_a_remedy():
    items = {u["item"]: u for u in R.reconcile(VERDICT)["unresolved"]}
    assert "per_fill_credit_detail_absent" in items
    assert "re-run" in items["per_fill_credit_detail_absent"]["remedy"]


# ------------------------------------------- no estimate may stand in
def test_a_per_contract_rate_is_REFUSED_without_a_denominator():
    """The surviving evidence counts FILLS. A per-contract rate needs
    CONTRACTS, so asking for one must fail rather than return a guess."""
    with pytest.raises(R.CreditEvidenceMissing) as e:
        R.per_contract_rate(R.reconcile(VERDICT))
    assert "contract denominator" in str(e.value)


def test_a_per_contract_rate_is_given_when_a_denominator_exists():
    rec = R.reconcile(VERDICT)
    rec[R.REALIZED]["contracts_denominator"] = "119.49"
    assert R.per_contract_rate(rec) == D("10.3600") / D("119.49")


def test_no_credited_total_also_refuses():
    with pytest.raises(R.CreditEvidenceMissing):
        R.per_contract_rate({R.REALIZED: {"contracts_denominator": "10"}})


# ------------------------------------------- the durable per-fill store
def test_the_september_sample_still_sums_to_its_known_total():
    """The one per-fill detail that DID survive: 25 rows, $0.42, 119.49
    shares, every row a NEGATIVE commission on our passive execution."""
    rows = R.load_rows()
    assert len(rows) == 25
    credited = sum(abs(D(str(r["our_commission"]))) for r in rows
                   if D(str(r["our_commission"])) < 0)
    qty = sum(D(str(r["qty"])) for r in rows)
    assert credited == D("0.42")
    assert qty == D("119.49")
    assert all(r["our_side"] == "passiveExecution" for r in rows)


def test_every_surviving_credit_row_is_a_sports_market_not_btc15m():
    """A negative result that matters: no venue credit we can verify has
    ever been attributed to a cpc- BTC 15-minute market."""
    rows = R.load_rows()
    assert not [r for r in rows if str(r["market"]).startswith("cpc-")]
    assert all(str(r["market"])[:4] in ("tsc-", "asc-", "aec-")
               for r in rows)


def test_merging_tags_the_venue_and_dedupes_by_id():
    new = [{"id": "NEW1", "market": "asc-x", "qty": "2.00",
            "our_commission": "-0.0100", "our_side": "passiveExecution"}]
    out = R.merge_rows(R.load_rows(), new)
    assert len(out) == 26
    assert [r for r in out if r["id"] == "NEW1"][0]["venue"] == \
        vc.POLYMARKET_US


def test_an_untagged_row_cannot_be_forced_to_another_venue():
    """`merge_rows` defaults the tag but still validates, so an explicit
    bad venue is refused rather than silently kept."""
    with pytest.raises(vc.UnknownVenue):
        R.merge_rows([], [{"id": "X", "venue": "coinbase",
                           "our_commission": "-0.01"}])


def test_writing_the_record_is_durable_and_round_trips(tmp_path):
    p = str(tmp_path / "sub" / "rec.json")
    rec = R.reconcile(VERDICT)
    assert R.write(rec, p) == p
    with open(p) as fh:
        assert json.load(fh)[R.REALIZED]["net_usd"] == "7.5000"


def test_the_durable_store_path_is_in_the_repository_not_a_container_tmp():
    """The whole point. The 2026-10-07 detail died in container /tmp."""
    assert not R.STORE.startswith("/tmp")
    assert R.STORE.startswith("analysis")
