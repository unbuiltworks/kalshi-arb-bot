"""L5's own fills, segmented by first-fill TTE. Real executions, deduped."""
import json
from decimal import Decimal as D

import pytest

from analysis.l5.tte import segments as S


def _page(fills):
    return json.dumps({"payload": fills})


def _fill(slug, ts, outcome, side, price, size, usdc, tx="0xa", asset="A"):
    return {"eventSlug": slug, "timestamp": ts, "outcome": outcome,
            "side": side, "price": price, "size": size, "usdcSize": usdc,
            "transactionHash": tx, "asset": asset}


def test_tte_is_computed_from_the_slug_start_not_estimated():
    """The slug carries the window START; a 5m fill 277s in has tte 23."""
    assert S.FAMILY_S["btc-updown-15m"] == 900
    assert S.FAMILY_S["btc-updown-5m"] == 300


def test_THE_PAGES_OVERLAP_AND_DUPLICATES_ARE_DROPPED(tmp_path):
    """Counting a paginated fill twice inflates fills and inventory alike."""
    f = _fill("btc-updown-15m-1000", 1100, "Up", "BUY", 0.5, 10, 5.0)
    p = tmp_path / "a.jsonl"
    p.write_text(_page([f]) + "\n" + _page([dict(f)]) + "\n", encoding="utf-8")
    bw = S.load_fills(str(p))
    assert sum(len(v) for v in bw.values()) == 1
    assert S.load_fills.dropped["duplicate"] == 1


def test_a_fill_with_no_side_is_dropped_not_guessed(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_page([
        _fill("btc-updown-15m-1000", 1100, "Up", "", 0.5, 10, 5.0, tx="0xb"),
        _fill("btc-updown-15m-1000", 1101, "Up", "BUY", 0.5, 10, 5.0),
    ]) + "\n", encoding="utf-8")
    bw = S.load_fills(str(p))
    assert sum(len(v) for v in bw.values()) == 1
    assert S.load_fills.dropped["no_side"] == 1


# =========================================== the economics are a price

def test_a_matched_pair_basis_is_the_sum_of_the_two_average_prices():
    rows = [
        {"ts": 1, "tte_s": 800.0, "outcome": "Up", "side": "BUY",
         "price": D("0.6"), "size": D("10"), "usdc": D("6")},
        {"ts": 2, "tte_s": 700.0, "outcome": "Down", "side": "BUY",
         "price": D("0.35"), "size": D("10"), "usdc": D("3.5")},
    ]
    e = S.window_economics(rows)
    assert e["matched"] == D("10")
    assert e["pair_basis"] == D("0.95")
    assert e["par_constrained"] is True
    assert e["residual"] == D("0")


def test_A_DEGENERATE_BASIS_IS_REPORTED_NOT_AVERAGED_IN():
    """An earlier run of this study reported a median pair basis of -12.7.

    When a window's buys and sells nearly cancel, the surviving quantity is a
    rounding remnant and the price it implies is meaningless. Folding those
    into a median lets them dominate it.
    """
    rows = [
        {"ts": 1, "tte_s": 800.0, "outcome": "Up", "side": "BUY",
         "price": D("0.6"), "size": D("10"), "usdc": D("6")},
        {"ts": 2, "tte_s": 799.0, "outcome": "Up", "side": "SELL",
         "price": D("0.6"), "size": D("9.999"), "usdc": D("9000")},
        {"ts": 3, "tte_s": 700.0, "outcome": "Down", "side": "BUY",
         "price": D("0.35"), "size": D("10"), "usdc": D("3.5")},
    ]
    e = S.window_economics(rows)
    assert e["pair_basis"] is None
    assert e["degenerate_basis"] is not None


def test_sells_are_netted_before_anything_is_matched():
    rows = [
        {"ts": 1, "tte_s": 800.0, "outcome": "Up", "side": "BUY",
         "price": D("0.5"), "size": D("10"), "usdc": D("5")},
        {"ts": 2, "tte_s": 799.0, "outcome": "Up", "side": "SELL",
         "price": D("0.5"), "size": D("10"), "usdc": D("5")},
        {"ts": 3, "tte_s": 700.0, "outcome": "Down", "side": "BUY",
         "price": D("0.5"), "size": D("10"), "usdc": D("5")},
    ]
    e = S.window_economics(rows)
    assert e["up_qty"] == D("0")
    assert e["matched"] == D("0"), "a leg bought and sold again is not inventory"


# ======================================================= the buckets

def test_the_buckets_are_the_ones_asked_for():
    assert [b[0] for b in S.BUCKETS] == [">600", "300-600", "120-300",
                                         "60-120", "<60"]
    assert S.bucket_of(887.0) == ">600"
    assert S.bucket_of(504.0) == "300-600"
    assert S.bucket_of(217.0) == "120-300"
    assert S.bucket_of(81.0) == "60-120"
    assert S.bucket_of(5.0) == "<60"


def test_A_FILL_AFTER_EXPIRY_IS_NOT_A_TTE_OBSERVATION():
    """Negative TTE is a clock or slug problem, not a late fill."""
    bw = {"w": [{"ts": 1, "tte_s": -102.0, "outcome": "Up", "side": "BUY",
                 "price": D("0.5"), "size": D("1"), "usdc": D("0.5")}]}
    seg = S.segment(bw)
    assert seg["windows_first_fill_after_expiry"] == 1
    assert all(seg["buckets"][b]["windows"] == 0 for b, _, _ in S.BUCKETS)
    cen = S.fills_by_tte(bw)
    assert cen["fills_after_expiry_excluded"] == 1
    assert cen["fills"] == 0


def test_the_clone_band_is_read_for_reporting_and_never_applied():
    import ast
    import inspect
    src = inspect.getsource(S)
    assert S.CLONE_MAX_TTE_S == 300.0
    # segment() may REPORT the band, but must never COMPARE against it --
    # a comparison there would silently drop L5's early fills, which are the
    # whole point of the study.
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "segment")
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            assert "CLONE_MAX_TTE_S" not in ast.unparse(node)
    # it is reported, though
    assert "CLONE_MAX_TTE_S" in ast.unparse(fn)


def test_this_is_labelled_as_evidence_about_L5_not_about_us():
    assert "OBSERVED_RETROSPECTIVE" in S.LABELS
    assert "never our cost basis" in S.LABELS
