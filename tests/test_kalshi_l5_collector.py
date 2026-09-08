"""The Kalshi collector is keyless, read-only, bounded on disk, and emits
exactly the tape shape the existing analyser reads (OWNER #167 5584095017
workstream B). It must never invent a quote, a print or a fee."""
from __future__ import annotations

import json

from src import kalshi_l5_collector as kc
from src.kalshi_l5_shadow import CERTIFIED_SERIES, load_tape


def _market(**over):
    m = {"ticker": "KXBTC15M-26SEP080800-00",
         "event_ticker": "KXBTC15M-26SEP080800",
         "open_time": "2026-09-08T11:45:00Z",
         "close_time": "2026-09-08T12:00:00Z",
         "yes_bid_dollars": "0.3000", "yes_bid_size_fp": "1146.12",
         "no_bid_dollars": "0.6900", "yes_ask_size_fp": "6501.55"}
    m.update(over)
    return m


def test_the_no_bid_size_is_the_yes_ask_size():
    """A resting NO bid IS the YES ask, so that is where its size comes
    from. Guessing it, or copying the YES bid size, would misreport the
    depth the shadow reasons about."""
    ev = kc.bbo_event(_market(), t=1000.0)
    assert ev["kind"] == "bbo" and ev["t"] == 1000.0
    assert ev["yes_bids"] == [[0.30, 1146.12]]
    assert ev["no_bids"] == [[0.69, 6501.55]]


def test_a_missing_side_is_left_out_not_invented():
    ev = kc.bbo_event(_market(no_bid_dollars=None), t=1.0)
    assert "yes_bids" in ev and "no_bids" not in ev
    assert kc.bbo_event(_market(yes_bid_dollars=None, no_bid_dollars=None)) is None


def test_prints_are_deduped_ordered_and_never_guessed(monkeypatch):
    """A trade with no usable time or price is dropped. Repeating prints
    the tape already holds would double-count the fill evidence the
    analyser reasons from."""
    monkeypatch.setattr(kc, "_get", lambda *a, **k: {"trades": [
        {"created_time": "2026-09-08T11:59:59.304491Z",
         "yes_price_dollars": "0.0010", "count_fp": "6.55",
         "taker_side": "yes", "trade_id": "c"},
        {"created_time": "2026-09-08T11:50:00Z",
         "yes_price_dollars": "0.3100", "count_fp": "10", "trade_id": "a"},
        {"created_time": "2026-09-08T11:55:00Z",
         "yes_price_dollars": "0.3200", "count_fp": "5", "trade_id": "b"},
        {"created_time": None, "yes_price_dollars": "0.5"},
        {"created_time": "2026-09-08T11:57:00Z", "yes_price_dollars": None},
    ]})
    out = kc.print_events("T")
    assert [e["trade_id"] for e in out] == ["a", "b", "c"]   # oldest first
    assert all(e["kind"] == "print" and e["venue"] == "kalshi" for e in out)
    cut = kc._iso_epoch("2026-09-08T11:50:00Z")
    assert [e["trade_id"] for e in kc.print_events("T", since_t=cut)] == ["b", "c"]


def test_only_certified_series_are_collected(monkeypatch):
    """An uncertified series has no established fee schedule. The analyser
    would refuse it, and collecting it would imply economics we have not
    established."""
    asked = []

    def _get(path, params=None):
        asked.append((params or {}).get("series_ticker"))
        return {"markets": [_market()]}

    monkeypatch.setattr(kc, "_get", _get)
    kc.open_markets()
    assert asked == list(CERTIFIED_SERIES)
    assert set(asked) == {"KXBTC15M", "KXETH15M"}


def test_the_tape_is_bounded_and_the_current_part_survives(monkeypatch, tmp_path):
    """The recorder's 00:40Z ENOSPC came from an unbounded tape filling a
    50 GB volume. Oldest parts go first, and never the one being written."""
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    d = tmp_path / "tape"
    d.mkdir(parents=True)
    current = kc.tape_path()
    for name in ("kalshi_l5_20260908_09.jsonl", "kalshi_l5_20260908_10.jsonl"):
        (d / name).write_text("x" * 5000)
    current.write_text("y" * 5000)
    out = kc.sweep_tape(budget=6000)
    assert current.exists(), "the current part is never deleted"
    assert out["deleted"] == ["kalshi_l5_20260908_09.jsonl",
                              "kalshi_l5_20260908_10.jsonl"]


def test_a_write_failure_alarms_instead_of_ending_the_process(monkeypatch,
                                                             tmp_path, capsys):
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))

    def _boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("builtins.open", _boom)
    out = kc.append([{"venue": "kalshi", "market": "m", "kind": "bbo"}])
    assert out["written"] == 0 and out["error"] == "OSError"
    assert "tape_write_failed" in capsys.readouterr().out


def test_what_is_written_is_what_the_analyser_reads(monkeypatch, tmp_path):
    """The whole point of the collector: the tape must load straight into
    the existing shadow, keyed by market, with no adapter in between."""
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    m = _market()
    kc.append([kc.window_event(m), kc.bbo_event(m, 1.0),
               {"venue": "kalshi", "market": m["ticker"], "kind": "print",
                "t": 2.0, "yes_price": 0.31}])
    by_market = load_tape(kc.tape_parts())
    evs = by_market[m["ticker"]]
    assert [e["kind"] for e in evs] == ["window", "bbo", "print"]
    assert evs[0]["close_time"] == "2026-09-08T12:00:00Z"


def test_the_collector_carries_no_credentials_and_no_order_path():
    """Read-only and keyless is a property of the source, not a promise in
    a docstring."""
    import inspect
    src = inspect.getsource(kc)
    for forbidden in ("Authorization", "api_key", "private_key", "sign",
                      "POST", "DELETE", "/portfolio/", "/order"):
        assert forbidden not in src, forbidden
    assert kc.API.startswith("https://")


def test_the_shadow_summary_stays_one_readable_line():
    """The probe's 06:30Z exit printed a whole document and dropped 117,262
    log messages including its own terminal status."""
    row = kc._compact_shadow({
        "model_sha": "bbb55bf90638d2bd", "markets": 2,
        "markets_with_possible_fills": 1, "executable_paths": 1,
        "repeated_executable_path": False,
        "pair_costs_theoretical": ["0.99"] * 50,
        "rows": [{"blockers": ["stale_book@1.0"]}, {"blockers": []}]})
    assert row["order_entry"] is False and row["shadow_only"] is True
    assert row["realized_pnl"] is None
    assert len(row["pair_costs_theoretical"]) == 8
    assert row["blockers"] == ["stale_book@1.0"]
    assert len(json.dumps(row)) < 900


def test_a_restart_never_re_appends_prints_the_tape_already_holds(monkeypatch,
                                                                  tmp_path):
    """Collection must be idempotent across restarts, not only within one
    process: re-appending prints would make the analyser count the same
    fill evidence twice."""
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    kc.append([
        {"venue": "kalshi", "market": "A", "kind": "print", "t": 10.0,
         "yes_price": 0.3},
        {"venue": "kalshi", "market": "A", "kind": "print", "t": 25.0,
         "yes_price": 0.31},
        {"venue": "kalshi", "market": "B", "kind": "print", "t": 5.0,
         "yes_price": 0.5},
        {"venue": "kalshi", "market": "A", "kind": "bbo", "t": 99.0},
    ])
    assert kc.seed_seen_from_tape() == {"A": 25.0, "B": 5.0}
    # a market with nothing on the tape simply has no floor
    assert "C" not in kc.seed_seen_from_tape()


def test_seeding_survives_an_unreadable_or_corrupt_tape(monkeypatch, tmp_path):
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    d = tmp_path / "tape"
    d.mkdir(parents=True)
    (d / "kalshi_l5_20260908_09.jsonl").write_text(
        '{"venue":"kalshi","market":"A","kind":"print","t":7.0}\n'
        'not json at all {{{\n')
    assert kc.seed_seen_from_tape() == {"A": 7.0}


def test_a_collector_that_sees_nothing_says_so(monkeypatch, tmp_path, capsys):
    """A running collector with an empty view is indistinguishable from a
    healthy quiet market unless it alarms."""
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(kc, "seed_seen_from_tape", lambda *a, **k: {})
    monkeypatch.setattr(kc, "collect_once",
                        lambda *a, **k: {"kind": "kalshi_collect",
                                         "markets": 0, "events": 0,
                                         "written": 0})
    # first tick arms the staleness clocks at boot; the loop's own read is
    # already past the limit
    ticks = iter([0.0, kc.STALE_MARKETS_S + 1.0])
    kc.main(["--once"], sleep=lambda *_a: None,
            clock=lambda: next(ticks))
    out = capsys.readouterr().out
    assert "stale_data:no_open_markets" in out
    assert "looks" in out and "quiet market" in out
