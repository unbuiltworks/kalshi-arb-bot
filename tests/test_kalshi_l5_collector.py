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


def test_collection_covers_certified_plus_observation(monkeypatch):
    """OWNER GO 2026-09-08 changed this invariant deliberately.

    We now COLLECT the incentivized series too, because the series Kalshi
    pays for are not the series we had evidence on. Collecting them implies
    NOTHING about their fee schedule: CERTIFIED_SERIES is unchanged, the
    analyser still refuses them, and only fee-independent structure is
    reported for them. The two original series stay in the set as the
    unincentivized control."""
    asked = []

    def _get(path, params=None):
        asked.append((params or {}).get("series_ticker"))
        return {"markets": [_market()]}

    monkeypatch.setattr(kc, "_get", _get)
    kc.open_markets()
    assert asked == list(kc.COLLECT_SERIES)
    assert {"KXBTC15M", "KXETH15M"} <= set(asked)          # control preserved
    assert "KXCRYPTOLEAD15M" in asked                       # where the money is
    assert set(CERTIFIED_SERIES) == {"KXBTC15M", "KXETH15M"}  # NOT widened


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


def test_the_shadow_input_is_bounded_in_events_not_in_parts(tmp_path, monkeypatch):
    """"the newest four hourly parts" scales with how many markets are live,
    so it is not a memory bound. RSS climbed 28.2 -> 39.0 MB over the first
    61 cycles on 4-6 of the 14 certified series; the cap has to be on what
    the analyser holds."""
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    d = kc.tape_dir()
    d.mkdir(parents=True, exist_ok=True)
    for hour in ("20260908_10", "20260908_11"):
        with open(d / f"kalshi_l5_{hour}.jsonl", "wt", encoding="utf-8") as fh:
            for i in range(50):
                fh.write(json.dumps({"venue": "kalshi", "market": "KXBTC15M-A",
                                     "kind": "bbo", "t": float(i),
                                     "hour": hour}) + "\n")

    full = kc.bounded_tape(kc.tape_parts(), max_events=1000)
    assert len(full["KXBTC15M-A"]) == 100

    capped = kc.bounded_tape(kc.tape_parts(), max_events=10)
    rows = capped["KXBTC15M-A"]
    assert len(rows) == 10
    # what survives is the NEWEST evidence: an analyser hunting completion
    # paths inside 15-minute windows must never drop the recent tape first
    assert {r["hour"] for r in rows} == {"20260908_11"}
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)


def test_bounded_tape_spills_into_the_older_part_only_with_budget_left(tmp_path,
                                                                      monkeypatch):
    monkeypatch.setenv(kc.STATE_DIR_ENV, str(tmp_path))
    kc.tape_dir().mkdir(parents=True, exist_ok=True)
    for hour, n in (("20260908_10", 30), ("20260908_11", 20)):
        with open(kc.tape_dir() / f"kalshi_l5_{hour}.jsonl", "wt",
                  encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps({"venue": "kalshi", "market": "M",
                                     "kind": "bbo", "t": float(i),
                                     "hour": hour}) + "\n")
    rows = kc.bounded_tape(kc.tape_parts(), max_events=25)["M"]
    assert len(rows) == 25
    hours = [r["hour"] for r in rows]
    assert hours[:5] == ["20260908_10"] * 5      # oldest kept are the spill
    assert hours[5:] == ["20260908_11"] * 20     # newest part kept whole


def test_whether_the_event_cap_is_binding_is_reported_not_inferred(monkeypatch):
    """RSS climbing does not distinguish "the bound is too loose" from "the
    tape has not filled to it yet". The compact line has to say."""
    monkeypatch.setattr(kc, "tape_parts", lambda: ["p"])
    monkeypatch.setattr(kc, "bounded_tape",
                        lambda parts: {"M": [{"kind": "bbo"}] * 3})
    monkeypatch.setattr(kc, "shadow_status", lambda by: {"markets": 1})
    out = kc.run_shadow()
    assert out["input_events"] == 3
    assert out["input_events_cap"] == kc.SHADOW_MAX_EVENTS
    compact = kc._compact_shadow(out)
    assert compact["input_events"] == 3
    assert compact["input_events_cap"] == kc.SHADOW_MAX_EVENTS


# --- OWNER GO 2026-09-08: incentivized-series observation -------------------

def test_observation_series_are_collected_but_not_certified():
    """The whole point: collect where the money is, without implying a fee
    schedule we have not established."""
    from src import kalshi_l5_collector as C
    assert "KXCRYPTOLEAD15M" in C.OBSERVATION_SERIES
    assert set(C.CERTIFIED_SERIES) == {"KXBTC15M", "KXETH15M"}
    assert not set(C.OBSERVATION_SERIES) & set(C.CERTIFIED_SERIES)
    assert set(C.COLLECT_SERIES) == set(C.CERTIFIED_SERIES) | set(C.OBSERVATION_SERIES)


def test_the_control_series_are_preserved():
    from src import kalshi_l5_collector as C
    for s in ("KXBTC15M", "KXETH15M"):
        assert s in C.COLLECT_SERIES


def test_uncertified_series_still_fail_closed_in_the_analyser():
    from src.kalshi_l5_shadow import certification_blocker
    b = certification_blocker("KXCRYPTOLEAD15M-26SEP100000-ETH",
                              {"close_time": "2026-09-10T04:00:00Z"})
    assert b is not None and "not_certified" in b


def test_structure_report_is_fee_independent_and_not_pnl():
    from src import kalshi_l5_collector as C
    by = {"KXCRYPTOLEAD15M-X": [
        {"kind": "bbo", "market": "KXCRYPTOLEAD15M-X",
         "yes_bids": [[0.40, 100.0]], "no_bids": [[0.55, 50.0]]},
        {"kind": "bbo", "market": "KXCRYPTOLEAD15M-X",
         "yes_bids": [[0.48, 10.0]], "no_bids": [[0.52, 10.0]]},
    ]}
    rep = C.structure_report(by)
    s = rep["series"]["KXCRYPTOLEAD15M"]
    # pair cost = 2 - yes_bid - no_bid
    assert s["pair_cost_min"] == 1.0
    assert s["two_sided_quotes"] == 2
    assert s["certified_for_net_economics"] is False
    assert s["two_sided_depth_median"] in (10.0, 50.0)
    assert rep["order_entry"] is False
    assert rep["realized_pnl"] is None
    assert "NOT realized" in rep["labels"]


def test_one_sided_books_are_counted_not_imputed():
    from src import kalshi_l5_collector as C
    by = {"KXGOLD15M-Y": [{"kind": "bbo", "market": "KXGOLD15M-Y",
                           "yes_bids": [[0.40, 100.0]]}]}
    s = C.structure_report(by)["series"]["KXGOLD15M"]
    assert s["one_sided_quotes"] == 1
    assert s["two_sided_quotes"] == 0
    assert s["pair_cost_median"] is None


# The old invariant here was "never converts the raw reward", which held
# only while the unit was unknown. The unit is now source-proven
# (centi-cents, /10,000), so the invariant it is replaced by is the one
# that still protects us: the raw integer survives the conversion, and the
# dollar figure is labelled as a headline pool rather than as revenue.
def test_the_raw_period_reward_survives_the_conversion():
    from src import kalshi_l5_collector as C
    import inspect
    src = inspect.getsource(C.incentive_state)
    assert "period_reward_raw" in src
    assert "headline_reward_dollars" in src


def test_the_reward_unit_carries_its_source_and_its_worked_examples():
    from src import kalshi_l5_collector as C
    u = C.REWARD_UNIT
    assert u["units"] == "centi-cents"
    assert u["divisor"] == 10_000
    assert "Kalshi Liquidity Incentive Program documentation" in u["source"]
    for raw, usd in u["examples"].items():
        assert C.reward_dollars(raw) == usd, raw


def test_reward_dollars_converts_and_refuses_to_invent_a_zero():
    from src import kalshi_l5_collector as C
    assert C.reward_dollars(200_000) == 20.0
    assert C.reward_dollars(10_000) == 1.0
    # a missing program and a zero-funded one are different facts
    assert C.reward_dollars(None) is None
    assert C.reward_dollars("not a number") is None
    assert C.reward_dollars(0) == 0.0


def test_the_dollar_figure_is_labelled_headline_pool_not_revenue():
    from src import kalshi_l5_collector as C
    st = C.REWARD_UNIT["status"]
    assert "HEADLINE POOL ONLY" in st
    assert "not estimated obtainable reward" in st
    assert "not credited reward" in st


def test_the_withdrawn_subsidy_is_recorded_not_read_as_absent():
    from src import kalshi_l5_collector as C
    import inspect
    assert "2026-05-12" in inspect.getsource(C.incentive_state)


def test_each_series_gets_its_own_reward_histograms():
    """Regression. The first cut of this used a module-level template and
    dict(), a shallow copy, so all four nested histograms were SHARED. Every
    series then reported one venue-wide reward distribution as its own --
    including KXBTC15M/KXETH15M, whose programs ended, which appeared to
    carry an active $20 pool. Buckets must not alias."""
    from src import kalshi_l5_collector as C
    a, b = C._incentive_bucket(), C._incentive_bucket()
    a["period_reward_raw"]["200000"] = 1
    a["headline_reward_dollars"]["20.0000"] = 1
    a["target_size_raw"]["1000.00"] = 1
    a["types"]["liquidity"] = 1
    assert b["period_reward_raw"] == {}
    assert b["headline_reward_dollars"] == {}
    assert b["target_size_raw"] == {}
    assert b["types"] == {}


def test_structure_report_breaks_out_legs_not_just_series():
    """KXCRYPTOLEAD15M's assets differ by an order of magnitude in cost, so
    a series median is the wrong unit for a decision."""
    from src import kalshi_l5_collector as C
    def bbo(yb, nb, sz=100.0):
        return {"kind": "bbo", "yes_bids": [[yb, sz]], "no_bids": [[nb, sz]]}
    rep = C.structure_report({
        "KXCRYPTOLEAD15M-26SEP082015-BTC": [bbo(0.50, 0.49)],   # pair 1.01
        "KXCRYPTOLEAD15M-26SEP082015-HYPE": [bbo(0.50, 0.34)],  # pair 1.16
    })
    assert set(rep["legs"]) == {"KXCRYPTOLEAD15M:BTC", "KXCRYPTOLEAD15M:HYPE"}
    assert rep["legs"]["KXCRYPTOLEAD15M:BTC"]["pair_cost_median"] == 1.01
    assert rep["legs"]["KXCRYPTOLEAD15M:HYPE"]["pair_cost_median"] == 1.16
    # the series roll-up still exists, and is explicitly labelled unfit for
    # comparing those two
    assert "KXCRYPTOLEAD15M" in rep["series"]
    assert "MUST NOT be used to compare" in rep["leg_note"]


def test_a_window_suffix_does_not_shatter_a_commodity_leg():
    from src import kalshi_l5_collector as C
    def bbo(yb, nb):
        return {"kind": "bbo", "yes_bids": [[yb, 10.0]], "no_bids": [[nb, 10.0]]}
    rep = C.structure_report({
        "KXGOLD15M-26SEP082015-15": [bbo(0.50, 0.49)],
        "KXGOLD15M-26SEP082030-15": [bbo(0.50, 0.49)],
    })
    assert list(rep["legs"]) == ["KXGOLD15M"]
    assert rep["legs"]["KXGOLD15M"]["markets"] == 2


def test_the_incentive_row_actually_emits_on_the_first_cycle(monkeypatch,
                                                             capsys):
    """BEHAVIOURAL, not a source grep. The first attempt at this fix put the
    cycle==1 branch INSIDE `if cycle % every == 0`, where cycle==1 is
    unreachable (1 % 15 == 1). It shipped, emitted nothing, and the test
    that "covered" it only checked that the string was present in the
    source -- so it passed on dead code. This test runs the loop."""
    from src import kalshi_l5_collector as C

    monkeypatch.setattr(C, "collect_once",
                        lambda *a, **k: {"kind": "kalshi_collect",
                                         "markets": 1, "events": 0})
    monkeypatch.setattr(C, "incentive_state",
                        lambda *a, **k: {"kind": "KALSHI_INCENTIVE_STATE",
                                         "series": {}})
    monkeypatch.setattr(C, "seed_seen_from_tape", lambda *a, **k: {})
    monkeypatch.setattr(C, "sweep_tape", lambda *a, **k: {"deleted": []})
    monkeypatch.setattr(C, "tape_parts", lambda *a, **k: [])
    monkeypatch.setattr(C, "run_shadow", lambda *a, **k: None)

    class Stop(Exception):
        pass

    calls = {"n": 0}

    def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] >= 2:          # let exactly two cycles run
            raise Stop

    try:
        C.main([], sleep=fake_sleep)
    except Stop:
        pass
    out = capsys.readouterr().out
    assert "KALSHI_INCENTIVE " in out, "no incentive row on the first cycles"
    # exactly once across two cycles -- cycle 1 only, not every cycle
    assert out.count("KALSHI_INCENTIVE ") == 1


def test_the_incentive_emission_is_not_nested_in_the_shadow_gate():
    """The bug was structural: correct condition, unreachable location."""
    from src import kalshi_l5_collector as C
    import inspect
    src = inspect.getsource(C.main)
    inc = src.index("if cycle == 1 or cycle % (every * INCENTIVE_EVERY_N)")
    gate = src.index("if cycle % every == 0:")
    assert inc < gate, "incentive emission must precede the shadow gate"
