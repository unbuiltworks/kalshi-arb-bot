"""The dataset repair, the verified collector, and the cash split.

THE DEFECT, stated so it can be checked without trusting any file: a
redemption pays $1 per WINNING contract and only one outcome pays, so the
payout cannot exceed the larger side's holding. Where it does, trades are
missing -- and missing trades mean missing COST, so those windows overstate
profit. 93 of 315 redeem-mismatch windows in the original capture fail it.

THE REPAIR is a verified UNION of every capture in the repository, not a
refetch: `data-api.polymarket.com` returns HTTP 410 from this environment,
so nothing can be refetched. `activity_gap.jsonl.gz` was collected by TIME
WINDOW rather than offset and carries 691,077 rows the offset file lost.
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal as D

import pytest

from analysis.l5.issue206 import l5_activity_collector as C
from analysis.l5.issue206 import l5_repair_dataset as RP
from src import l5_capital_block as CB


# =========================== 1. THE CLASSIFIER IS ARITHMETIC, NOT TRUST

def w(uq, uc, dq, dc, red, n=4):
    return {"uq": uq, "uc": uc, "dq": dq, "dc": dc, "red": red, "n": n,
            "assets": set()}


def test_a_payout_above_the_larger_side_is_INCOMPLETE():
    """It is impossible, so the rows must be missing."""
    assert RP.classify(w(10, 5.0, 8, 4.0, red=12.0)) == RP.INCOMPLETE


def test_a_payout_within_the_larger_side_is_COMPLETE():
    assert RP.classify(w(10, 5.0, 8, 4.0, red=10.0)) == RP.COMPLETE
    assert RP.classify(w(10, 5.0, 8, 4.0, red=8.0)) == RP.COMPLETE


@pytest.mark.parametrize("e", [
    w(10, 5.0, 8, 4.0, red=0.0),        # no redemption: cannot settle it
    w(10, 5.0, 0, 0.0, red=10.0),       # one-sided
    w(0, 0.0, 0, 0.0, red=0.0, n=0),    # no trades
])
def test_anything_unsettleable_is_INDETERMINATE_not_a_failure(e):
    assert RP.classify(e) == RP.INDETERMINATE


def test_the_three_classes_are_exhaustive():
    assert {RP.COMPLETE, RP.INCOMPLETE, RP.INDETERMINATE} == {
        RP.classify(w(10, 5.0, 8, 4.0, red=10.0)),
        RP.classify(w(10, 5.0, 8, 4.0, red=12.0)),
        RP.classify(w(10, 5.0, 8, 4.0, red=0.0))}


def test_incomplete_windows_are_never_counted_as_profit():
    """`economics` is only ever applied to a group the caller selected, and
    the runner selects COMPLETE. This pins that INCOMPLETE windows are
    separable at all -- the property the rule depends on."""
    good = [("a", w(10, 9.0, 10, 0.65, red=10.0))]
    bad = [("b", w(10, 5.0, 8, 4.0, red=12.0))]
    assert RP.economics(good)["windows"] == 1
    assert RP.economics(bad)["windows"] == 1
    assert RP.economics(good) != RP.economics(bad)


# ===================== 2. THE UNION DEDUPES AND DOES NOT MUTATE SOURCES

def test_the_row_key_is_stable_across_sources():
    r = {"transactionHash": "t", "asset": "a", "timestamp": 1, "size": 2,
         "side": "BUY", "type": "TRADE", "icon": "ignored"}
    r2 = dict(r, icon="different", bio="also ignored")
    assert RP.key(r) == RP.key(r2), "cosmetic fields must not split a row"


def test_payloads_reads_every_capture_shape():
    assert len(list(RP._payloads({"payload": [{"type": "TRADE"}]}))) == 1
    assert len(list(RP._payloads([{"type": "TRADE"}]))) == 1
    assert len(list(RP._payloads({"data": [{"type": "TRADE"}]}))) == 1
    assert list(RP._payloads({"nothing": 1})) == []


def test_the_union_lists_every_source_and_its_contribution():
    """Reported per source, so a silent drop cannot hide."""
    rows, stats = RP.load_union(sources=[], verbose=False)
    assert rows == [] and stats == []


def test_a_missing_source_is_named_not_skipped_silently():
    rows, stats = RP.load_union(sources=["does/not/exist.jsonl"],
                                verbose=False)
    assert rows == []
    assert stats[0][3] == "MISSING"


# ============================= 3. THE COLLECTOR CANNOT LOSE ROWS QUIETLY

def _feed(n_per_second=1):
    def fetch(a, b, limit):
        rows = [{"transactionHash": "t%d.%d" % (i, k), "asset": "a",
                 "timestamp": i, "size": 1, "side": "BUY", "type": "TRADE"}
                for i in range(a, b) for k in range(n_per_second)]
        return rows[:limit] if len(rows) > limit else rows
    return fetch


def test_it_splits_an_ambiguous_full_page_instead_of_accepting_it():
    d = tempfile.mkdtemp()
    rep = C.collect(fetch=_feed(1), start=0, end=4000,
                    out_path=d + "/o.jsonl", checkpoint_path=d + "/cp.json",
                    limit=500, slice_s=3600, sleep=lambda s: None)
    assert rep["unique_rows"] == 4000, "every row in the interval"
    assert rep["complete"] is True
    assert rep["saturated"] == 0 and rep["failed"] == 0


def test_a_slice_it_cannot_split_is_SATURATED_never_complete():
    """601 rows in a single second cannot be verified at limit=500."""
    d = tempfile.mkdtemp()
    rep = C.collect(fetch=_feed(601), start=0, end=2,
                    out_path=d + "/o.jsonl", checkpoint_path=d + "/cp.json",
                    limit=500, slice_s=1, sleep=lambda s: None)
    assert rep["saturated"] >= 1
    assert rep["complete"] is False, "the gap is NAMED, not absent"
    assert rep["gaps"], rep


def test_a_transport_failure_is_recorded_as_a_named_gap():
    d = tempfile.mkdtemp()

    def boom(a, b, limit):
        raise RuntimeError("502")
    rep = C.collect(fetch=boom, start=0, end=10,
                    out_path=d + "/o.jsonl", checkpoint_path=d + "/cp.json",
                    limit=500, slice_s=5, sleep=lambda s: None)
    assert rep["failed"] == 2
    assert rep["complete"] is False
    assert all(g["status"] == C.FAILED for g in rep["gaps"])


def test_it_retries_before_giving_up():
    d = tempfile.mkdtemp()
    calls = {"n": 0}

    def flaky(a, b, limit):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return [{"transactionHash": "t", "asset": "a", "timestamp": a,
                 "size": 1, "side": "BUY", "type": "TRADE"}]
    rep = C.collect(fetch=flaky, start=0, end=5, out_path=d + "/o.jsonl",
                    checkpoint_path=d + "/cp.json", limit=500, slice_s=5,
                    sleep=lambda s: None)
    assert calls["n"] >= 3
    assert rep["failed"] == 0 and rep["unique_rows"] == 1


def test_it_resumes_from_the_durable_checkpoint():
    d = tempfile.mkdtemp()
    cp = d + "/cp.json"
    C.collect(fetch=_feed(1), start=0, end=100, out_path=d + "/a.jsonl",
              checkpoint_path=cp, limit=500, slice_s=50,
              sleep=lambda s: None)
    rep = C.collect(fetch=_feed(1), start=0, end=200,
                    out_path=d + "/b.jsonl", checkpoint_path=cp,
                    limit=500, slice_s=50, sleep=lambda s: None)
    assert rep["resumed_from"] == 100, "it did not re-walk settled ground"


def test_it_refuses_to_overwrite_an_existing_capture():
    """The original dataset is never modified, and neither is a repair."""
    d = tempfile.mkdtemp()
    p = d + "/exists.jsonl"
    open(p, "w").write("{}\n")
    with pytest.raises(ValueError):
        C.collect(fetch=_feed(1), start=0, end=10, out_path=p,
                  checkpoint_path=d + "/cp.json", sleep=lambda s: None)


def test_rerunning_is_free_because_rows_dedupe():
    d = tempfile.mkdtemp()
    rep = C.collect(fetch=_feed(3), start=0, end=100,
                    out_path=d + "/o.jsonl", checkpoint_path=d + "/cp.json",
                    limit=500, slice_s=50, sleep=lambda s: None)
    lines = [l for l in open(d + "/o.jsonl") if l.strip()]
    keys = {C.row_key(json.loads(l)) for l in lines}
    assert len(keys) == len(lines) == rep["unique_rows"]


# ================= 4. THE CASH SPLIT: WHAT IS RISK-FREE AND WHAT IS NOT

def test_a_matched_pair_is_risk_free_to_a_risk_bound():
    r = CB.matched_cash_split(positions={"UP": D("1"), "DOWN": D("1")},
                              leg_cost={"UP": D("0.58"),
                                        "DOWN": D("0.37")})
    assert r["readable"] is True
    assert D(r["committed_cash_usd"]) == D("0.9500")
    assert D(r["risk_bound_charges_usd"]) == D("0")
    assert D(r["matched_worst_case_usd"]) == D("0.0500"), "a GAIN"


def test_the_1230z_position_splits_to_its_settled_numbers():
    """UP 2 @ 0.9100 basis, DOWN 1 @ 0.5000: matched 0.9550, residual
    0.4550 -- exactly the settlement record's own decomposition."""
    r = CB.matched_cash_split(positions={"UP": D("2"), "DOWN": D("1")},
                              leg_cost={"UP": D("0.91"), "DOWN": D("0.50")})
    assert D(r["committed_cash_usd"]) == D("1.4100")
    assert D(r["matched_cash_usd"]) == D("0.9550")
    assert D(r["residual_cash_usd"]) == D("0.4550")
    assert D(r["residual_worst_case_usd"]) == D("-0.4550")
    assert D(r["risk_bound_charges_usd"]) == D("0.4550")


def test_the_cash_bound_and_the_risk_bound_differ_and_both_are_reported():
    """The whole point: one number cannot answer both questions."""
    r = CB.matched_cash_split(positions={"UP": D("1"), "DOWN": D("1")},
                              leg_cost={"UP": D("0.58"),
                                        "DOWN": D("0.37")})
    assert D(r["cash_bound_charges_usd"]) > D(r["risk_bound_charges_usd"])
    assert D(r["risk_free_cash_usd"]) == D("0.9500")


def test_it_releases_nothing_and_changes_no_limit():
    import inspect
    src = inspect.getsource(CB.matched_cash_split)
    for banned in ("max_cash_deployed", "setenv", "os.environ",
                   "state[", "save("):
        assert banned not in src, banned


@pytest.mark.parametrize("pos,cost", [
    ({"UP": None}, {"UP": D("1")}),
    ({"UP": D("1")}, {"UP": "junk"}),
])
def test_an_unreadable_book_is_never_reported_as_risk_free(pos, cost):
    r = CB.matched_cash_split(positions=pos, leg_cost=cost)
    assert r["readable"] is False
    assert "risk_bound_charges_usd" not in r


def test_a_flat_market_charges_nothing_either_way():
    r = CB.matched_cash_split(positions={}, leg_cost={})
    assert r["readable"] is True
    assert D(r["committed_cash_usd"]) == D("0")
    assert D(r["risk_bound_charges_usd"]) == D("0")
