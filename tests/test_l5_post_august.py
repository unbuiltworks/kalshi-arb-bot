"""Invariants for the post-August L5 harvest and report. READ-ONLY, no network.

These guard the four things that were actually got wrong at least once while
building this lane, so a regression is caught by the suite rather than by
reading a report that looks plausible.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from scripts import l5_post_august_harvest as H
from scripts import l5_post_august_report as R
from src import pmus_fee_schedule as F


# ---- 1. the identity rule: wallet, never handle -------------------------

def test_activity_feed_is_keyed_on_the_proxy_wallet_not_the_handle():
    """Passing the handle returns HTTP 400 "required query param 'user' not
    provided" -- the venue reports a non-address as ABSENT, which reads as
    "L5 did not trade". Only the address may ever be sent."""
    assert H.ADDRESS == "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
    assert H.ADDRESS == R.L5_ADDR
    assert "l5zn1bwom8etsk" not in "".join(
        u for u in [H.BASE, H.ADDRESS]).lower()


def test_harvester_only_ever_issues_reads():
    import inspect
    src = inspect.getsource(H)
    for forbidden in ("place_order", "create_order", "send_transaction",
                      "requests.post", "method=\"POST\""):
        assert forbidden not in src, forbidden


# ---- 2. the fee schedule is the effective-dated one ---------------------

def test_fee_schedule_is_frozen_and_is_the_corrected_one():
    F.assert_frozen()
    assert F.FINGERPRINT == R.EXPECT_FP


def test_taker_theta_rose_and_the_stale_scalar_is_not_used():
    before = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    after = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
    f_before = F.taker_fee(D("0.5"), D("100"), when=before)
    f_after = F.taker_fee(D("0.5"), D("100"), when=after)
    assert f_after > f_before, "the 2026-09-17 taker rise is not applied"
    assert str(F.taker_theta(when=after)) == "0.0695"


def test_the_date_conflict_window_is_flagged_not_silently_rated():
    assert F.date_conflict_window(
        dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)) is True
    assert F.date_conflict_window(
        dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc)) is False


def test_a_rebate_is_never_a_negative_fee():
    """maker_rebate is a POSITIVE credit and must never be netted into cost."""
    assert F.maker_rebate(D("0.45"), D("100")) > 0


# ---- 3. regime labelling, reported separately ---------------------------

@pytest.mark.parametrize("day,expect", [
    ("2026-04-24", "REGIME_PRE_2026-04-25"),
    ("2026-04-25", "REGIME_2026-04-25_ON"),
    ("2026-06-23", "REGIME_2026-06-23_ON"),
    ("2026-08-18", "REGIME_2026-06-23_ON"),
    ("2026-08-19", "REGIME_2026-08-19_ON"),
    ("2026-09-30", "REGIME_2026-08-19_ON"),
    ("2026-10-07", "REGIME_2026-08-19_ON"),
])
def test_canonical_regime_boundaries(day, expect):
    assert R.regime_of(day) == expect


def test_all_three_canonical_change_points_are_present():
    assert R.REGIME_CPS == ["2026-04-25", "2026-06-23", "2026-08-19"]


def test_months_split_september_from_october():
    assert R.month_of("2026-09-30") == "2026-09"
    assert R.month_of("2026-10-01") == "2026-10"


# ---- 4. the dedupe key is the validated one, not the defective one -----

def test_dedupe_key_includes_price_size_and_outcome():
    """(txh, asset, ts) is defective here: the /activity pages carry no
    `asset`, so it degenerates to (txh, None, ts) and collapses records that
    differ in price/size/side/outcome/type."""
    rows = [
        # same transactionHash, same timestamp, DIFFERENT price and size:
        # two genuinely distinct fills that the defective key would merge.
        {"transactionHash": "0xabc", "conditionId": "0x" + "1" * 64,
         "outcomeIndex": 0, "timestamp": 1790000000, "price": 0.41,
         "size": 10, "usdcSize": 4.1, "side": "BUY", "type": "TRADE",
         "eventSlug": "btc-updown-5m-1790000000"},
        {"transactionHash": "0xabc", "conditionId": "0x" + "1" * 64,
         "outcomeIndex": 0, "timestamp": 1790000000, "price": 0.43,
         "size": 25, "usdcSize": 10.75, "side": "BUY", "type": "TRADE",
         "eventSlug": "btc-updown-5m-1790000000"},
    ]
    diag = _fresh_diag()
    seen = {}
    kept = [R._norm(r, "t", seen, diag) for r in rows]
    assert all(k is not None for k in kept), "a distinct fill was dropped"
    assert diag["duplicates_dropped"] == 0
    assert diag["collisions_differing_fields"] == 0


def test_an_exact_duplicate_is_dropped_without_being_called_a_collision():
    rec = {"transactionHash": "0xabc", "conditionId": "0x" + "2" * 64,
           "outcomeIndex": 1, "timestamp": 1790000001, "price": 0.55,
           "size": 7, "usdcSize": 3.85, "side": "BUY", "type": "TRADE",
           "eventSlug": "eth-updown-15m-1790000000"}
    diag = _fresh_diag()
    seen = {}
    assert R._norm(dict(rec), "a", seen, diag) is not None
    assert R._norm(dict(rec), "b", seen, diag) is None
    assert diag["duplicates_dropped"] == 1
    assert diag["collisions_differing_fields"] == 0


def test_a_contradicting_identity_disqualifies_but_a_missing_name_does_not():
    base = {"transactionHash": "0xd", "conditionId": "0x" + "3" * 64,
            "outcomeIndex": 0, "timestamp": 1790000002, "price": 0.5,
            "size": 1, "usdcSize": 0.5, "side": "BUY", "type": "TRADE",
            "eventSlug": "btc-updown-5m-1790000000"}

    # no name/proxyWallet at all -> KEPT on provenance (this is the ~90% case)
    diag = _fresh_diag()
    assert R._norm(dict(base), "t", {}, diag) is not None
    assert diag["records_without_name_field"] == 1

    # a CONTRADICTING name -> dropped
    diag = _fresh_diag()
    bad = dict(base, name="someoneElse")
    assert R._norm(bad, "t", {}, diag) is None
    assert diag["records_contradicting_identity"] == 1

    # L5's own name -> kept
    diag = _fresh_diag()
    good = dict(base, name=R.L5_NAME)
    assert R._norm(good, "t", {}, diag) is not None


def _fresh_diag():
    from collections import Counter
    return {"records_seen": 0, "records_malformed": 0, "records_bad_numeric": 0,
            "records_with_name_field": 0, "records_without_name_field": 0,
            "records_contradicting_identity": 0,
            "records_nonhex_conditionid": 0, "records_empty_slug": 0,
            "trades_unresolved_slug": 0, "trades_unresolved_epoch": 0,
            "trades_bad_numeric": 0, "duplicates_dropped": 0,
            "collisions_differing_fields": 0, "collision_examples": [],
            "dup_by_pair": Counter(), "type_counts": Counter(),
            "kept_by_src": Counter()}


# ---- 5. the gaps the harvester claims to fetch -------------------------

def test_harvest_gaps_cover_the_measured_holes_and_nothing_already_held():
    """Sep 1-10 and Sep 22-25 already exist; the harvester must not refetch
    them, and must cover Sep 11-21 plus Sep 26 onward."""
    labels = {g[0] for g in H.GAPS}
    assert labels == {"2026-09-11", "2026-09-26"}
    g1 = [g for g in H.GAPS if g[0] == "2026-09-11"][0]
    assert dt.datetime.fromtimestamp(g1[1], dt.timezone.utc).strftime(
        "%Y-%m-%d") == "2026-09-11"
    assert dt.datetime.fromtimestamp(g1[2], dt.timezone.utc).strftime(
        "%Y-%m-%d") == "2026-09-22", "must stop before the Sep 22 harvest"
    g2 = [g for g in H.GAPS if g[0] == "2026-09-26"][0]
    assert g2[2] is None, "the forward gap must run to the run date"


def test_windows_are_small_enough_for_the_offset_cap():
    """limit/offset caps near 5,500, which reaches back only ~9h for this
    account, so windows must be materially smaller than that reach."""
    assert H.WINDOW_S <= 3 * 3600
    assert H.OFFSET_CEILING <= 5500
