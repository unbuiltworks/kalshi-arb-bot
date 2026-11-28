"""Regression tests for the two P8 dataset defects.

Both defects were silent: they produced a dataset that looked fine and made
every downstream number bigger. These tests exist so that neither can come
back without a test going red.
"""
from fractions import Fraction as F

import pytest

from src.gemini_execution import tape_normalize as TN


def _c(**kw):
    base = {"symbol": "GEMI-X", "duration": "1h", "prints": [],
            "start": "2026-09-16T10:00:00.000Z",
            "expiry": "2026-09-16T11:00:00.000Z"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- defect 1
def test_duration_comes_from_the_declared_lifetime_not_the_ticker():
    c = _c(symbol="GEMI-BTC2609161000", duration="1h",
           start="2026-09-16T10:00:00.000Z", expiry="2026-09-16T10:05:00.000Z")
    assert TN.duration_of(c) == "5m"
    assert TN.classify(c)["agrees"] is False


def test_a_multiday_strike_market_named_like_an_hourly_is_quarantined():
    """The exact contamination: an hourly-shaped ticker that lives for days."""
    c = _c(symbol="GEMI-BTC2609161000", duration="1h",
           start="2026-09-14T00:00:00.000Z", expiry="2026-09-17T00:00:00.000Z")
    assert TN.duration_of(c) is None
    assert "in no studied band" in TN.classify(c)["quarantine"]


def test_a_contract_with_no_declared_lifetime_is_quarantined_not_guessed():
    c = _c(start=None, expiry=None)
    assert TN.duration_of(c) is None
    assert TN.classify(c)["quarantine"] == "no declared lifetime"


def test_quarantined_contracts_leave_the_dataset_and_are_counted():
    good = _c(symbol="A", start="2026-09-16T10:00:00.000Z",
              expiry="2026-09-16T11:00:00.000Z")
    bad = _c(symbol="B", start="2026-09-14T00:00:00.000Z",
             expiry="2026-09-17T00:00:00.000Z")
    rows, rep = TN.normalize_all([good, bad])
    assert [r["symbol"] for r in rows] == ["A"]
    assert rep["quarantined"] == 1
    assert rep["quarantined_detail"][0]["symbol"] == "B"


def test_the_lane_bands_cannot_overlap():
    """Two bands overlapping would make duration_of order-dependent."""
    spans = sorted((float(lo), float(hi)) for _n, lo, hi in TN.DURATION_BOUNDS)
    for (_, hi), (lo, _) in zip(spans, spans[1:]):
        assert hi < lo


# ---------------------------------------------------------------- defect 2
def test_a_mirrored_twin_is_one_execution_not_two():
    pr = [[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"]]
    kept, dropped = TN.dedupe_prints(pr)
    assert len(kept) == 1
    assert len(dropped) == 1
    assert "mirrored twin" in dropped[0]["reason"]


def test_same_second_opposite_prices_but_different_quantity_are_both_real():
    pr = [[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "9"]]
    kept, _ = TN.dedupe_prints(pr)
    assert len(kept) == 2


def test_same_second_same_quantity_but_prices_do_not_sum_to_one():
    pr = [[1000, "sell", "0.40", "5"], [1000, "buy", "0.55", "5"]]
    kept, _ = TN.dedupe_prints(pr)
    assert len(kept) == 2


def test_different_seconds_are_never_twinned():
    pr = [[1000, "sell", "0.40", "5"], [1001, "buy", "0.60", "5"]]
    kept, _ = TN.dedupe_prints(pr)
    assert len(kept) == 2


def test_the_surviving_twin_continues_the_price_series():
    pr = [[900, "sell", "0.41", "5"],
          [1000, "sell", "0.42", "5"], [1000, "buy", "0.58", "5"]]
    kept, _ = TN.dedupe_prints(pr)
    assert [r[2] for r in kept] == ["0.41", "0.42"]
    pr = [[900, "sell", "0.59", "5"],
          [1000, "sell", "0.42", "5"], [1000, "buy", "0.58", "5"]]
    kept, _ = TN.dedupe_prints(pr)
    assert [r[2] for r in kept] == ["0.59", "0.58"]


def test_deduplication_is_idempotent():
    pr = [[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"],
          [1001, "sell", "0.41", "2"]]
    once, _ = TN.dedupe_prints(pr)
    twice, dropped = TN.dedupe_prints(once)
    assert once == twice
    assert dropped == []


# ---------------------------------------------------------------- audit
def test_deduplication_is_off_unless_asked_for():
    """Suspended by the P8 audit: the twin rule's stated mechanism was
    falsified, so it must not run by default."""
    c = _c(prints=[[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"]])
    assert len(TN.normalize(c)["prints"]) == 2
    assert TN.normalize(c)["normalization"]["dedupe_applied"] is False
    assert len(TN.normalize(c, dedupe=True)["prints"]) == 1
    rows, rep = TN.normalize_all([c])
    assert rep["twins_dropped"] == 0 and rep["dedupe_applied"] is False


def test_normalization_preserves_the_raw_rows_and_is_reversible():
    c = _c(prints=[[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"]],
           start="2026-09-16T10:00:00.000Z", expiry="2026-09-16T10:15:00.000Z")
    n = TN.normalize(c, dedupe=True)
    assert n["duration"] == "15m"
    assert len(n["prints"]) == 1
    assert n["prints_raw"] == [list(r) for r in c["prints"]]
    assert TN.restore(n) == c


def test_every_dropped_row_is_recorded_with_what_replaced_it():
    c = _c(prints=[[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"]])
    n = TN.normalize(c, dedupe=True)
    d = n["normalization"]["twins_dropped"]
    assert len(d) == 1
    assert d[0]["row"] in ([1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"])
    assert d[0]["kept"] in ([1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"])
    assert d[0]["row"] != d[0]["kept"]


def test_the_counts_in_the_report_reconcile():
    rows = [_c(symbol=str(i),
               prints=[[1000, "sell", "0.40", "5"], [1000, "buy", "0.60", "5"],
                       [1001, "sell", "0.41", "2"]])
            for i in range(3)]
    out, rep = TN.normalize_all(rows, dedupe=True)
    assert rep["contracts_out"] == 3
    assert rep["twins_dropped"] == 3
    assert rep["prints_out"] == 6
    assert sum(len(r["prints"]) for r in out) == rep["prints_out"]


def test_the_module_is_offline():
    """No network, no clock: the same input must always normalize the same way."""
    import inspect
    src = inspect.getsource(TN)
    imports = {ln.strip() for ln in src.splitlines()
               if ln.startswith(("import ", "from "))}
    assert imports == {
        "from __future__ import annotations",
        "import datetime as _dt",
        "from collections import defaultdict",
        "from fractions import Fraction as F",
        "from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple",
    }, imports


def test_a_measured_lifetime_field_is_honoured():
    """The P4/P6 collectors record `life_min` instead of start/expiry."""
    assert TN.duration_of({"life_min": 60.0}) == "1h"
    assert TN.duration_of({"life_min": 5.0}) == "5m"
    assert TN.duration_of({"life_min": 4320.0}) is None


def test_print_timestamps_are_seconds_in_the_stored_corpus():
    """A silent unit change would shrink every measured duration by 1000x and
    still produce a plausible table. This checks the corpus against the
    contracts' OWN declared lifetimes, which is the only independent clock we
    have."""
    import datetime as dt
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "gemini", "p7_bulk_tape.json")
    if not os.path.exists(path):                    # corpus not in this tree
        return
    rows, _ = TN.normalize_all(json.load(open(path)), dedupe=True)

    def epoch(v):
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()

    assert TN.PRINT_TIME_UNIT == "s"
    inside = outside = 0
    for c in rows:
        if not c["prints"]:
            continue
        # generous on the early side: the venue lists a contract before its
        # measurement window opens, so prints legitimately precede `start`
        # (median 77s, p99 249s in this corpus). Nothing prints after expiry.
        lo, hi = epoch(c["start"]) - 3600, epoch(c["expiry"]) + 60
        for p in c["prints"]:
            if lo <= p[0] <= hi:
                inside += 1
            else:
                outside += 1
    assert inside > 20000
    assert outside == 0, outside


def test_rows_carrying_tid_and_timestampms_are_handled():
    """Collectors keep six fields since the P8 audit. Nothing that reads a
    print may assume exactly four, and `tid_of` must find the id."""
    wide = [1000, "sell", "0.40", "5", 987654321, 1000123]
    narrow = [1000, "sell", "0.40", "5"]
    assert TN.tid_of(wide) == 987654321
    assert TN.tid_of(narrow) is None
    c = _c(prints=[wide, [1000, "buy", "0.60", "5", 987654322, 1000456]])
    n = TN.normalize(c, dedupe=True)
    assert len(n["prints"]) == 1 and len(n["prints"][0]) == 6
    assert len(TN.normalize(c)["prints"]) == 2
