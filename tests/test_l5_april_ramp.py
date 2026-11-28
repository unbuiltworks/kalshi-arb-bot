"""L5's April ramp reconstruction.

The derived CSV stopped at 2026-04-21 and made April look like 11
sporadic days. These pin the things that would quietly corrupt the ramp
table: double-counting the overlapping raw dumps, netting incentives
into trading cash, and inventing a maker label the feed never carried.
"""
from __future__ import annotations

import gzip
import json

from src.polymarket_l5 import april_ramp as ar


def _row(ts, slug="btc-updown-15m-1", outcome="Up", side="BUY",
         price=0.5, size=10.0, typ="TRADE", tx="0xa"):
    return {"timestamp": ts, "eventSlug": slug, "outcome": outcome,
            "side": side, "price": price, "size": size,
            "usdcSize": price * size, "type": typ, "transactionHash": tx,
            "conditionId": "0xc"}


def _write(tmp_path, forward, gap):
    (tmp_path / "activity.jsonl").write_text(
        "\n".join(json.dumps({"payload": p}) for p in forward) + "\n",
        encoding="utf-8")
    with gzip.open(tmp_path / "activity_gap.jsonl.gz", "wt") as fh:
        for p in gap:
            fh.write(json.dumps({"payload": p}) + "\n")
    return tmp_path


def test_overlapping_raw_dumps_are_deduplicated(tmp_path):
    """The forward sweep and the gap sweep overlap. Counting a trade
    twice would inflate every volume figure in the ramp table."""
    r = _row(1_775_300_000)
    root = _write(tmp_path, [[r]], [[r]])
    assert len(list(ar.iter_activity(root))) == 1


def test_distinct_trades_are_all_kept(tmp_path):
    rows = [_row(1_775_300_000 + i, tx=f"0x{i}") for i in range(5)]
    root = _write(tmp_path, [rows[:3]], [rows[3:]])
    assert len(list(ar.iter_activity(root))) == 5


def test_incentives_never_enter_trading_cash(tmp_path):
    """21.7% of L5's April total was rebates. Netting them into trading
    cash would turn a ~flat trading month into a profitable-looking one."""
    ts = 1_775_300_000
    root = _write(tmp_path, [[
        _row(ts, side="BUY", price=0.5, size=10.0),
        {"timestamp": ts + 1, "type": "MAKER_REBATE", "usdcSize": 7.0,
         "transactionHash": "0xr", "eventSlug": "btc-updown-15m-1"},
    ]], [])
    day = ar.build(month="2026-04", root=root)["days"][0]
    assert day["incentives_usdc"] == 7.0
    assert day["net_trading_cash_usdc"] == -5.0      # the buy only
    assert day["cum_incentives_usdc"] == 7.0


def test_maker_is_never_asserted_for_april(tmp_path):
    """The activity feed carries no maker/taker. The takerOnly feed that
    supports the label covers late August only."""
    root = _write(tmp_path, [[_row(1_775_300_000)]], [])
    day = ar.build(month="2026-04", root=root)["days"][0]
    assert day["maker_taker"] == {"unknown": 1}
    assert "maker" not in day["maker_taker"]


def test_relations_follow_inventory_not_a_label():
    """First action opens; same outcome extends; opposite completes."""
    acts = [(1, "Up", 0.5, 10.0, 5.0), (2, "Up", 0.5, 10.0, 5.0),
            (3, "Down", 0.45, 10.0, 4.5)]
    seq = ar.sequences({"w": acts})
    assert seq["relation_counts"] == {"open": 1, "extend": 1, "complete": 1}
    assert seq["windows_reaching_complete"] == 1
    assert seq["complete_rate"] == 1.0


def test_residual_is_the_unmatched_remainder():
    acts = [(1, "Up", 0.5, 30.0, 15.0), (2, "Down", 0.5, 10.0, 5.0)]
    seq = ar.sequences({"w": acts})
    assert seq["median_matched_units"] == 10.0
    assert seq["median_residual_units"] == 20.0


def test_a_one_sided_window_is_all_residual():
    seq = ar.sequences({"w": [(1, "Up", 0.5, 25.0, 12.5)]})
    assert seq["median_residual_units"] == 25.0
    assert seq["complete_rate"] == 0.0


def test_concurrency_counts_overlapping_windows():
    """L5 ran one window at a time through April. A concurrency bug would
    make that look like parallelism he never had."""
    serial = {"a": [(1, "Up", .5, 1, .5), (2, "Up", .5, 1, .5)],
              "b": [(3, "Up", .5, 1, .5), (4, "Up", .5, 1, .5)]}
    assert ar.concurrency(serial)["max"] == 1
    overlap = {"a": [(1, "Up", .5, 1, .5), (9, "Up", .5, 1, .5)],
               "b": [(3, "Up", .5, 1, .5), (5, "Up", .5, 1, .5)]}
    assert ar.concurrency(overlap)["max"] == 2


def test_category_is_derived_from_the_venue_slug():
    assert ar._category("btc-updown-15m-1787903100") == "btc-15m"
    assert ar._category("eth-updown-5m-1787903100") == "eth-5m"


def test_build_reports_its_own_limits(tmp_path):
    root = _write(tmp_path, [[_row(1_775_300_000)]], [])
    doc = ar.build(month="2026-04", root=root)
    assert doc["kind"] == "L5_APRIL_RAMP"
    joined = " ".join(doc["limits"])
    assert "maker" in joined and "never counted as maker" in joined
    assert "NOT per-position realized" in joined
