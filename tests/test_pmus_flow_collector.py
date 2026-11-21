"""Tests for the PMUS contemporaneous-flow collector.

The properties that matter here are the ones that would quietly corrupt the
study: a gap must never read as zero flow, the measurement must be a first
difference and not the lifetime counter, and the module must stay read-only.
Each is paired with a companion that proves the check goes red when broken.
"""

import ast
from decimal import Decimal as D

import pytest

from src.pmus_flow_collector import (BBO_PATH, deciles, delta, discover,
                                     parse_bbo, plan_cycle, poll_once, rank,
                                     run, spread_of, summarise, two_sided_hint)


def bbo(shares="100", last="0.50", bid="0.49", ask="0.51", state="MARKET_STATE_OPEN"):
    def money(v):
        return None if v is None else {"value": v, "currency": "USD"}
    return {"marketData": {"marketSlug": "m", "state": state,
                           "sharesTraded": shares, "openInterest": "10",
                           "lastTradePx": money(last), "bestBid": money(bid),
                           "bestAsk": money(ask), "bidDepth": 5, "askDepth": 7}}


def sample(shares, t, last="0.50", bid="0.49", ask="0.51"):
    s = parse_bbo(bbo(shares, last, bid, ask))
    s["t"] = t
    return s


# ------------------------------ parsing ------------------------------

def test_parse_unwraps_the_money_envelope():
    s = parse_bbo(bbo())
    assert s["best_bid"] == D("0.49") and s["best_ask"] == D("0.51")
    assert s["shares_traded"] == D("100")
    assert spread_of(s) == D("0.02")


def test_never_traded_market_reads_as_none_not_zero():
    """The gateway sends empty strings for a market with no trades ever.

    Zero would be a measurement; empty is the absence of one.
    """
    s = parse_bbo({"marketData": {"marketSlug": "m", "sharesTraded": "",
                                  "openInterest": "", "lastTradePx": None,
                                  "bestBid": None, "bestAsk": None}})
    assert s["shares_traded"] is None
    assert s["last_trade_px"] is None
    assert spread_of(s) is None


def test_unreadable_numbers_do_not_raise():
    s = parse_bbo(bbo(shares="n/a", last="junk"))
    assert s["shares_traded"] is None and s["last_trade_px"] is None


# ------------------------------ the first difference ------------------------------

def test_flow_is_the_difference_not_the_lifetime_counter():
    """A market with 500,000 lifetime shares and no delta must score zero.

    This is the owner's whole point: headline volume is not activity.
    """
    d = delta(sample("500000", 0.0), sample("500000", 60.0))
    assert d["shares"] == D(0)
    assert d["printed"] is False
    assert d["shares_per_min"] == 0.0


def test_a_small_market_that_is_trading_outranks_a_big_one_that_is_not():
    big = summarise([delta(sample("500000", 0.0), sample("500000", 60.0)),
                     delta(sample("500000", 60.0), sample("500000", 120.0))])
    small = summarise([delta(sample("100", 0.0, "0.49"), sample("140", 60.0, "0.49")),
                       delta(sample("140", 60.0, "0.51"), sample("190", 120.0, "0.51"))])
    ranked = rank({"big": big, "small": small}, min_intervals=1)
    assert ranked[0]["slug"] == "small"
    assert small["two_sided"] is True and big["two_sided"] is False


def test_rate_is_per_minute_and_uses_the_real_interval():
    d = delta(sample("0", 0.0), sample("30", 30.0))
    assert d["shares_per_min"] == pytest.approx(60.0)


# ------------------------------ gaps are not zeros ------------------------------

def test_first_sample_is_a_gap_not_a_zero():
    assert delta(None, sample("100", 0.0))["gap"] is True


def test_a_backwards_counter_is_a_gap_not_negative_flow():
    """Cumulative counters do not decrease; a drop means a reset, not selling."""
    d = delta(sample("500", 0.0), sample("100", 60.0))
    assert d["gap"] is True and d["why"] == "counter_went_backwards"


def test_unreadable_shares_is_a_gap():
    assert delta(sample("100", 0.0), sample("n/a", 60.0))["gap"] is True


def test_gaps_are_excluded_from_rates_never_counted_as_zero_flow():
    """Goes red if a gap is folded into the denominator as a quiet zero."""
    real = delta(sample("0", 0.0), sample("60", 60.0))
    s = summarise([{"gap": True, "why": "request_failed"}, real])
    assert s["intervals"] == 1 and s["gaps"] == 1
    assert s["observed_s"] == 60.0
    assert s["shares_per_min"] == pytest.approx(60.0)


def test_a_market_seen_only_through_gaps_has_no_rate_at_all():
    s = summarise([{"gap": True, "why": "x"}, {"gap": True, "why": "y"}])
    assert s["intervals"] == 0 and s["shares_per_min"] is None


def test_under_observed_markets_are_flagged_not_ranked_at_zero():
    thin = summarise([delta(sample("0", 0.0), sample("5", 60.0))])
    ranked = rank({"thin": thin}, min_intervals=4)
    assert ranked[0]["under_observed"] is True


def test_deciles_exclude_under_observed_rows():
    rows = [{"slug": "a", "shares_per_min": 1.0, "two_sided": True},
            {"slug": "b", "shares_per_min": 9.0, "two_sided": False},
            {"slug": "c", "shares_per_min": None, "two_sided": False,
             "under_observed": True}]
    got = deciles(rows, n=2)
    assert [d["markets"] for d in got] == [1, 1]
    assert "c" not in [s for d in got for s in d["slugs"]]


# ------------------------------ two-sidedness is an inference ------------------------------

def test_side_hint_reads_bid_ask_and_inside():
    assert two_sided_hint(parse_bbo(bbo(last="0.49"))) == "at_bid"
    assert two_sided_hint(parse_bbo(bbo(last="0.51"))) == "at_ask"
    assert two_sided_hint(parse_bbo(bbo(last="0.50"))) == "inside"


def test_side_hint_refuses_a_crossed_or_absent_book():
    assert two_sided_hint(parse_bbo(bbo(bid="0.60", ask="0.40"))) is None
    assert two_sided_hint(parse_bbo(bbo(bid=None))) is None


def test_two_sided_needs_both_sides_not_just_repeated_prints():
    one = summarise([delta(sample("0", 0.0, "0.49"), sample("5", 60.0, "0.49")),
                     delta(sample("5", 60.0, "0.49"), sample("9", 120.0, "0.49"))])
    assert one["prints_seen"] == 2 and one["two_sided"] is False


# ------------------------------ rate plan ------------------------------

def test_plan_stretches_the_interval_to_stay_inside_the_budget():
    p = plan_cycle(200, poll_s=5.0, max_markets=60)
    assert p["kept_markets"] == 60 and p["dropped_markets"] == 140
    assert p["effective_poll_s"] >= 60 / (3.0 - 0.5)


def test_plan_never_shortens_a_generous_interval():
    assert plan_cycle(2, poll_s=30.0, max_markets=60)["effective_poll_s"] == 30.0


# ------------------------------ read-only, by AST not by grep ------------------------------

def _module_ast():
    """Parse the module and DROP docstrings, so prose describing a rule can
    never satisfy a check for the rule. An earlier study in this repo shipped
    a test that passed on its own docstring."""
    import src.pmus_flow_collector as m
    tree = ast.parse(open(m.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                node.body = body[1:]
    return tree


def test_module_issues_no_write_method():
    verbs = {"POST", "PUT", "DELETE", "PATCH"}
    found = {n.value for n in ast.walk(_module_ast())
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.upper() in verbs}
    assert not found, f"write verb in code: {found}"


def test_the_write_verb_check_catches_a_real_violation():
    """Companion: the check above must fail on an actual violation."""
    tree = ast.parse('def go():\n    return request(method="POST")\n')
    found = {n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.upper() in {"POST", "PUT", "DELETE", "PATCH"}}
    assert found == {"POST"}


def test_module_imports_no_order_transport_or_credentials():
    banned = ("order", "executor", "auth", "signing", "credential", "session_l5")
    mods = []
    for n in ast.walk(_module_ast()):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
    bad = [m for m in mods if any(b in m.lower() for b in banned)]
    assert not bad, f"order/credential import: {bad}"


# ------------------------------ loop wiring ------------------------------

def test_run_drives_the_loop_and_produces_flow(monkeypatch):
    """Drives run() with an injected getter/clock rather than reading source.

    A test that greps for the poll call passes on dead code; this one fails if
    the loop never polls or never differences.
    """
    counter = {"n": 0}

    def getter(path):
        if path.startswith("/v1/markets?"):
            return {"markets": [{"slug": "m1", "endDate": "2030-01-01T00:00:00Z"},
                                {"slug": "m2", "endDate": "2030-01-02T00:00:00Z"}]}
        counter["n"] += 1
        return bbo(shares=str(counter["n"] * 10))

    monkeypatch.setattr("src.pmus_flow_collector.http_get_json", getter)
    ticks = iter([0.0, 60.0, 120.0])
    rep = run(3, poll_s=1.0, max_markets=10, out_path=None,
              sleep=lambda s: None, getter=getter, clock=lambda: next(ticks))
    assert rep["markets"] == 2
    assert rep["per_market"]["m1"]["intervals"] == 2
    assert rep["per_market"]["m1"]["prints_seen"] == 2
    assert rep["read_only"] is True and rep["places_orders"] is False


def test_a_failing_market_becomes_a_gap_not_a_crash():
    def getter(path):
        if path == BBO_PATH % "bad":
            raise TimeoutError("boom")
        return bbo()
    out = poll_once(["good", "bad"], 1.0, getter=getter)
    assert out["bad"]["unreadable"] == "TimeoutError"
    assert out["good"]["shares_traded"] == D("100")


# ------------------------------ discovery ------------------------------

def _listing(*rows):
    return lambda path: {"markets": list(rows)}


def test_discovery_puts_nearest_resolution_first_not_listing_order():
    """The listing's first page is season futures that resolve weeks out and
    do not trade. Ranking by time-to-resolution puts in-play events first."""
    got = discover(10, now=0.0, getter=_listing(
        {"slug": "future", "endDate": "2030-01-01T00:00:00Z"},
        {"slug": "soon", "endDate": "1970-01-02T00:00:00Z"},
        {"slug": "mid", "endDate": "1971-01-01T00:00:00Z"}))
    assert got == ["soon", "mid", "future"]


def test_discovery_drops_markets_already_past_their_end_date():
    """A resolved book has no contemporaneous flow to measure."""
    got = discover(10, now=1000.0, getter=_listing(
        {"slug": "done", "endDate": "1970-01-01T00:00:00Z"},
        {"slug": "live", "endDate": "2030-01-01T00:00:00Z"}))
    assert got == ["live"]


def test_discovery_drops_rows_with_no_readable_end_date():
    got = discover(10, now=0.0, getter=_listing(
        {"slug": "nodate"}, {"slug": "bad", "endDate": "not-a-date"},
        {"slug": "ok", "endDate": "2030-01-01T00:00:00Z"}))
    assert got == ["ok"]


def test_discovery_respects_the_cap():
    rows = [{"slug": "s%d" % i, "endDate": "2030-01-0%dT00:00:00Z" % (i + 1)}
            for i in range(5)]
    assert len(discover(2, now=0.0, getter=_listing(*rows))) == 2


def test_discovery_prefers_the_live_event_set_over_the_open_listing():
    """A single listing page is not the near-term slate.

    Measured 2026-09-09: one limit=200 page returned 200 rows whose earliest
    resolution was a month out, with the in-play events buried behind it. The
    live-event set must win, so this goes red if discovery ever falls back to
    the listing while live events exist.
    """
    def getter(path):
        if path.startswith("/v1/events"):
            return {"events": [{"live": True, "slug": "cs2-x",
                                "markets": [{"slug": "inplay-a"},
                                            {"slug": "inplay-b"}]}]}
        return {"markets": [{"slug": "month-out",
                             "endDate": "2030-01-01T00:00:00Z"}]}
    assert discover(10, now=0.0, getter=getter)[:2] == ["inplay-a", "inplay-b"]


def test_discovery_falls_back_when_the_venue_has_no_live_events():
    """An empty live set is a real venue state, not a discovery failure."""
    def getter(path):
        if path.startswith("/v1/events"):
            return {"events": []}
        return {"markets": [{"slug": "soon", "endDate": "1970-01-02T00:00:00Z"},
                            {"slug": "later", "endDate": "2030-01-01T00:00:00Z"}]}
    assert discover(10, now=0.0, getter=getter) == ["soon", "later"]


def test_discovery_does_not_duplicate_a_market_present_in_both_sources():
    def getter(path):
        if path.startswith("/v1/events"):
            return {"events": [{"live": True, "slug": "e",
                                "markets": [{"slug": "dup"}]}]}
        return {"markets": [{"slug": "dup", "endDate": "2030-01-01T00:00:00Z"},
                            {"slug": "other", "endDate": "2030-01-02T00:00:00Z"}]}
    got = discover(10, now=0.0, getter=getter)
    assert got == ["dup", "other"]


def test_discovery_refuses_an_event_the_venue_does_not_mark_live():
    """Fail-closed: a scheduled-but-started event without the venue's own
    `live` flag must not admit its markets, or season futures leak in."""
    def getter(path):
        if path.startswith("/v1/events"):
            return {"events": [{"slug": "not-flagged",
                                "markets": [{"slug": "leaked"}]},
                               {"live": True, "ended": True, "slug": "over",
                                "markets": [{"slug": "finished"}]}]}
        return {"markets": [{"slug": "fallback",
                             "endDate": "2030-01-01T00:00:00Z"}]}
    got = discover(10, now=0.0, getter=getter)
    assert "leaked" not in got and "finished" not in got
    assert got == ["fallback"]
