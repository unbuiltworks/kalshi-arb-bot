"""Shadow breadth: many virtual L5s, zero dollars, no relabelling.

The tests that matter here are the ones guarding against a loop that runs
happily while counting nothing.
"""
from __future__ import annotations

import pytest

from src import pmus_sports_shadow_breadth as SB


# ------------------------------------------- observation rows feed the policy
def _md(bid="0.40", ask="0.41", shares="1000", notional="5000", last="0.40"):
    return {
        "bids": [{"px": {"value": bid}, "qty": "100"}],
        "offers": [{"px": {"value": ask}, "qty": "80"}],
        "stats": {"sharesTraded": shares,
                  "notionalTraded": {"value": notional},
                  "lastTradePx": {"value": last}},
        "state": "MARKET_STATE_OPEN",
        "transactTime": "2026-09-10T18:00:00Z",
    }


def test_observation_row_matches_the_schema_the_policy_consumes():
    """pmus_status_report._ts / infer_trades read `t`, `bid`, `ask`,
    `shares_traded_cum`, `last_trade_px`. If this drifts, decide_market
    silently sees nothing."""
    from src import pmus_status_report as st
    row = SB.observation_row("m", _md(), 1_789_000_000.0)
    for k in ("t", "bid", "ask", "shares_traded_cum", "last_trade_px"):
        assert k in row, k
    assert st._ts(row) == pytest.approx(1_789_000_000.0, abs=1)
    assert row["bid"] == 0.40 and row["ask"] == 0.41


def test_observation_reads_offers_not_asks():
    """The venue serves sell depth under `offers`. Reading `asks` yields an
    absent ask on every market -- indistinguishable from a one-sided book,
    and it would disqualify the entire universe."""
    md = _md()
    md["asks"] = md.pop("offers")
    row = SB.observation_row("m", md, 1.0)
    assert row["ask"] is None, "an `asks`-only payload must not parse"
    assert SB.observation_row("m", _md(), 1.0)["ask"] == 0.41


def test_infer_trades_consumes_our_rows():
    from src import pmus_status_report as st
    a = SB.observation_row("m", _md(shares="1000"), 1_789_000_000.0)
    b = SB.observation_row("m", _md(shares="1100", last="0.41"),
                           1_789_000_060.0)
    trades = st.infer_trades([a, b])
    assert len(trades) == 1 and trades[0]["vol"] == pytest.approx(100.0)


# --------------------------------------------------- the always-zero guard
def _act(rel, fill="possible", mpc="0.98", decision="quote"):
    return {"decision": decision, "relation": rel, "fill_class": fill,
            "max_pair_cost": mpc, "t": 1.0}


def test_completions_are_counted_at_all():
    """THE REGRESSION. The first version filtered on a `pair_cost` key that
    plan_action never emits, so this count read 0 forever in a loop whose
    whole purpose is counting completions."""
    out = SB.classify_actions({"actions_replayed": [
        _act("open"), _act("complete"), _act("complete")]})
    assert out["virtual_completions_le_1"] == 2
    assert out["virtual_first_fills"] == 1


def test_a_key_rename_would_not_silently_zero_the_count():
    """Even with max_pair_cost absent the completion still counts -- absence
    of the assertion field is not evidence of a bad completion."""
    a = _act("complete"); a.pop("max_pair_cost")
    assert SB.classify_actions({"actions_replayed": [a]})[
        "virtual_completions_le_1"] == 1


def test_an_above_par_completion_is_recorded_as_a_violation_not_dropped():
    out = SB.classify_actions({"actions_replayed": [_act("complete", mpc="1.02")]})
    assert out["virtual_completions_le_1"] == 0
    assert out["pair_ceiling_violations"] == [{"t": 1.0, "max_pair_cost": "1.02"}]


def test_the_live_policy_cannot_emit_a_completion_above_one_dollar():
    """The invariant the assertion is checking, checked against the real
    policy rather than a fixture."""
    from decimal import Decimal as D
    from src import pmus_sports_l5_decision as dec
    px, econ = dec.complement_target(D("0.60"), D("0.99"))
    assert D(econ["max_pair_cost"]) <= D("1.00")
    px2, econ2 = dec.complement_target(D("0.95"), D("0.99"))
    assert px2 is None or D(econ2["max_pair_cost"]) <= D("1.00")


def test_unfilled_and_non_quote_actions_do_not_count():
    out = SB.classify_actions({"actions_replayed": [
        _act("open", fill="no_fill"),
        _act("complete", decision="no_action")]})
    assert out["virtual_first_fills"] == 0
    assert out["virtual_completions_le_1"] == 0


def test_residual_is_open_when_acquisitions_exceed_completions():
    assert SB.classify_actions({"actions_replayed": [
        _act("open"), _act("extend")]})["virtual_residual_open"] is True
    assert SB.classify_actions({"actions_replayed": [
        _act("open"), _act("complete")]})["virtual_residual_open"] is False


# ------------------------------------------------------ cohorts are PROSPECTIVE
def test_tiers_are_assigned_by_rank_position():
    assert SB.tier_of(0) == "T1_top8"
    assert SB.tier_of(7) == "T1_top8"
    assert SB.tier_of(8) == "T2_9_16"
    assert SB.tier_of(31) == "T3_17_32"
    assert SB.tier_of(32) is None, "beyond the watched set is untiered"


def test_funnel_reports_per_tier_and_never_divides_by_zero():
    out = SB.funnel({
        "T1_top8": {"selection_cycles": 5, "markets": {"a", "b"},
                    "virtual_completions_le_1": 4,
                    "residual_opening_markets": {"a", "b"}},
        "T2_9_16": {"selection_cycles": 3, "markets": {"c"},
                    "virtual_completions_le_1": 0}})
    assert out["T1_top8"]["completions_per_residual_market"] == 2.0
    assert out["T2_9_16"]["completions_per_residual_market"] is None


def test_funnel_never_reports_cycles_as_markets():
    """A market watched nine times is ONE market. Conflating the two inflates
    every rate computed below it."""
    out = SB.funnel({"T1_top8": {"selection_cycles": 9, "markets": {"only"}}})
    assert out["T1_top8"]["selection_cycles"] == 9
    assert out["T1_top8"]["distinct_markets"] == 1


def test_the_shadow_ratio_is_not_called_a_dollar_ratio():
    """The owner's primary metric is completions per residual DOLLAR. Shadow
    has no dollars, so the shadow analogue must not borrow the name."""
    import inspect
    out = SB.funnel({"T1_top8": {"selected": 1, "virtual_residual_open": 1,
                                 "virtual_completions_le_1": 1}})
    assert "completions_per_residual_market" in out["T1_top8"]
    assert not any("dollar" in k for k in out["T1_top8"])
    assert "no dollars" in inspect.getdoc(SB.funnel)


# ----------------------------------------------------------- read-only, bounded
def test_module_imports_no_executor_and_no_credentials():
    import inspect
    src = inspect.getsource(SB)
    for bad in ("pmus_q1_executor", "pmus_spring_l5_probe_entry",
                "PMUS_SPRING_", "api_key", "submit_order"):
        assert bad not in src, f"{bad} must not appear in a shadow module"


def test_labels_say_a_virtual_fill_is_not_a_fill():
    assert SB.LABELS["order_entry"] is False
    assert SB.LABELS["realized_pnl"] is None
    assert "never a fill" in SB.LABELS["fill_semantics"]


def test_an_unreadable_book_is_skipped_not_recorded_as_quiet():
    calls = {"n": 0}

    def boom(path):
        calls["n"] += 1
        raise OSError("network")

    sb = SB.ShadowBreadth(get_json=boom, sleep=None)
    assert sb.poll(["a", "b"]) == 0
    assert sb.obs == {}, "a failed read must leave no observation at all"
    assert calls["n"] == 2


def test_poll_records_readable_books():
    sb = SB.ShadowBreadth(get_json=lambda p: {"marketData": _md()},
                          sleep=None, now=lambda: 1_789_000_000.0)
    assert sb.poll(["a"]) == 1
    assert sb.obs["a"][-1]["bid"] == 0.40


def test_ranked_drops_markets_with_no_observations():
    sb = SB.ShadowBreadth(get_json=lambda p: None, sleep=None)
    sb.obs["empty"] = __import__("collections").deque()
    assert sb.ranked() == []


# --------------------------------------------- flow window (the 32->5->1 bug)
def _obs(t, notional, iso):
    return {"_poll_t": t, "notional_traded_cum": notional,
            "transact_time": iso}


def test_flow_uses_the_trailing_window_not_adjacent_polls():
    """THE REGRESSION. Differencing adjacent ~45s polls made eligibility flap
    32 -> 5 -> 1 live, because most markets print nothing in any given 45s.
    Over 5 minutes the same market shows real flow."""
    now = 1000.0
    obs = [_obs(now - 300, "0", "2026-09-10T18:00:00Z"),
           _obs(now - 150, "0", "2026-09-10T18:02:30Z"),      # quiet middle
           _obs(now, "600", "2026-09-10T18:05:00Z")]
    assert SB.flow_over_window(obs, now, 300) == pytest.approx(120.0)
    # adjacent-poll view of the same quiet stretch sees nothing
    assert SB.flow_over_window(obs[:2], now, 300) == 0.0


def test_flow_window_drops_observations_older_than_the_window():
    now = 1000.0
    obs = [_obs(now - 9999, "0", "2026-09-10T15:00:00Z"),
           _obs(now - 120, "0", "2026-09-10T18:03:00Z"),
           _obs(now, "240", "2026-09-10T18:05:00Z")]
    assert SB.flow_over_window(obs, now, 300) == pytest.approx(120.0)


def test_a_single_observation_gives_no_rate():
    assert SB.flow_over_window([_obs(1000.0, "5", "2026-09-10T18:05:00Z")],
                               1000.0, 300) is None
    assert SB.flow_over_window([], 1000.0, 300) is None


def test_poll_stamps_the_poll_time_so_the_window_can_be_applied():
    row = SB.observation_row("m", _md(), 1_789_000_000.0)
    assert row["_poll_t"] == 1_789_000_000.0


# ------------------------------- re-replay double counting (the 5 -> 29 bug)
class _Rec:
    """decide_market replays its WHOLE window each call, so the same virtual
    action reappears every cycle."""

    def __init__(self, acts):
        self.acts = acts

    def __call__(self, slug, rows, now, model):
        return {"actions_replayed": self.acts, "executable_now": True}


def test_the_same_virtual_action_is_counted_exactly_once(monkeypatch):
    """THE REGRESSION. Nine live cycles over five markets reported 29 first
    fills, because each cycle re-counted the same replayed OPEN."""
    acts = [dict(_act("open"), t=10.0, target_price="0.40"),
            dict(_act("extend"), t=40.0, target_price="0.41")]
    from src import pmus_sports_l5_decision as dec
    monkeypatch.setattr(dec, "decide_market", _Rec(acts))

    sb = SB.ShadowBreadth(get_json=lambda p: {"marketData": _md()},
                          sleep=None, now=lambda: 1_789_000_000.0)
    sb.obs["m"] = __import__("collections").deque(
        [SB.observation_row("m", _md(), 1.0),
         SB.observation_row("m", _md(), 2.0)])
    for _ in range(9):
        sb.decide(["m"], {})
    f = SB.funnel(sb.cohorts)["T1_top8"]
    assert f["selection_cycles"] == 9
    assert f["distinct_markets"] == 1
    assert f["virtual_first_fills"] == 1, "one OPEN, replayed nine times"
    assert f["virtual_extends"] == 1


def test_a_genuinely_new_action_still_counts(monkeypatch):
    from src import pmus_sports_l5_decision as dec
    rec = _Rec([dict(_act("open"), t=10.0, target_price="0.40")])
    monkeypatch.setattr(dec, "decide_market", rec)
    sb = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                          now=lambda: 1_789_000_000.0)
    sb.obs["m"] = __import__("collections").deque(
        [SB.observation_row("m", _md(), 1.0)] * 2)
    sb.decide(["m"], {})
    rec.acts = rec.acts + [dict(_act("complete"), t=70.0, target_price="0.58")]
    sb.decide(["m"], {})
    f = SB.funnel(sb.cohorts)["T1_top8"]
    assert f["virtual_first_fills"] == 1 and f["virtual_completions_le_1"] == 1


def test_each_cycle_reports_window_and_new_separately(monkeypatch):
    from src import pmus_sports_l5_decision as dec
    monkeypatch.setattr(dec, "decide_market",
                        _Rec([dict(_act("open"), t=10.0, target_price="0.40")]))
    sb = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                          now=lambda: 1_789_000_000.0)
    sb.obs["m"] = __import__("collections").deque(
        [SB.observation_row("m", _md(), 1.0)] * 2)
    first = sb.decide(["m"], {})[0]
    second = sb.decide(["m"], {})[0]
    assert first["new_this_cycle"]["virtual_first_fills"] == 1
    assert second["new_this_cycle"]["virtual_first_fills"] == 0
    assert second["window"]["virtual_first_fills"] == 1, \
        "the window view still shows the action; only the tally dedupes"


# --------------------------------------------------------------- discovery
def test_discovery_excludes_futures_and_non_sports():
    pages = [{"markets": [
        {"slug": "game", "category": "sports",
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE"},
        {"slug": "future", "category": "sports",
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_FUTURE"},
        {"slug": "politics", "category": "politics",
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE"},
    ]}]

    def get(path):
        return pages.pop(0) if pages else {"markets": []}

    assert SB.discover_game_slugs(get) == ["game"]


def test_discovery_degrades_rather_than_dies():
    def get(path):
        if "offset=0" in path:
            return {"markets": [{"slug": "a", "category": "sports",
                                 "sportsMarketTypeV2":
                                 "SPORTS_MARKET_TYPE_TOTAL"}] * 500}
        raise OSError("gateway 429")

    assert SB.discover_game_slugs(get, max_pages=5) == ["a"] * 500


def test_deploy_watch_patterns_do_not_touch_the_probe():
    """Pushing this module must never restart the armed live campaign."""
    import json as _j
    probe = set(_j.load(open("deploy/pmus_spring_l5/railway.json"))
                ["build"]["watchPatterns"])
    mine = set(_j.load(open("deploy/pmus_shadow_breadth/railway.json"))
               ["build"]["watchPatterns"])
    assert not (probe & mine)
    assert "src/pmus_sports_shadow_breadth.py" not in probe


# ------------------------------------ the watch set must outlive eligibility
def test_watch_set_keeps_ineligible_markets_so_they_can_become_eligible():
    """THE REGRESSION. Retaining only currently-eligible markets collapsed the
    watch set 32 -> 5 in one live cycle; a market briefly one-sided would then
    never be polled again and could never be seen becoming eligible."""
    import collections
    sb = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                          now=lambda: 1000.0)

    def obs(slug, notional, ask="0.41"):
        dq = collections.deque()
        for i, t in enumerate((700.0, 1000.0)):
            md = _md(ask=ask, notional=str(int(notional) * i))
            row = SB.observation_row(slug, md, t)
            row["transact_time"] = f"2026-09-10T18:0{i}:00Z"
            dq.append(row)
        return dq

    # dated slugs: the ranker now gates on settlement horizon, and an
    # undated placeholder is refused by design
    busy, one_sided = "aec-x-busy-2026-09-10", "aec-x-onesided-2026-09-10"
    sb.obs[busy] = obs(busy, 6000)
    sb.obs[one_sided] = obs(one_sided, 6000, ask=None)
    for r in sb.obs[one_sided]:
        r["ask"] = None
    assert [r["slug"] for r in sb.ranked()] == [busy], "only busy is eligible"
    assert set(sb.watch_set()) == {busy, one_sided}, \
        "the ineligible market must stay watched"


def test_discovery_is_paced_because_it_shares_a_gateway_with_the_live_probe():
    """An unpaced ~400-GET burst once drew 429s that halted the live campaign
    with a leg held. A service that costs nothing must not be the thing that
    stops the one that doesn't."""
    pauses = []
    pages = [{"markets": [{"slug": f"g{i}", "category": "sports",
                           "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL"}
                          for i in range(500)]} for _ in range(3)]
    pages.append({"markets": []})

    def get(path):
        return pages.pop(0)

    SB.discover_game_slugs(get, max_pages=6, sleep=pauses.append)
    assert pauses, "discovery must pause between pages"
    assert all(p >= 0.2 for p in pauses), pauses


def test_the_first_page_is_not_delayed():
    pauses = []
    SB.discover_game_slugs(lambda p: {"markets": []}, sleep=pauses.append)
    assert pauses == [], "no pause before the first request"


# ------------------------------------------- cohort durability across restarts
def test_cohorts_survive_a_restart(tmp_path, monkeypatch):
    """THE REGRESSION. Cohorts lived only in memory until pushing 502fdf27 --
    which touches pmus_sports_opportunity_rank.py, a WATCHED path for this
    service -- redeployed it and silently reset every prospective tally. A
    loop whose purpose is accumulating evidence across time cannot keep it
    where a routine push erases it."""
    from src import pmus_sports_l5_decision as dec
    path = str(tmp_path / "cohorts.json")
    acts = [dict(_act("open"), t=10.0, target_price="0.40")]
    monkeypatch.setattr(dec, "decide_market", _Rec(acts))

    first = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                             now=lambda: 1_789_000_000.0, state_path=path)
    first.obs["m"] = __import__("collections").deque(
        [SB.observation_row("m", _md(), 1.0)] * 2)
    first.decide(["m"], {})
    first.report([], watched=1, eligible=1)          # report() persists
    assert SB.funnel(first.cohorts)["T1_top8"]["virtual_first_fills"] == 1

    # a restart: brand-new object, same state file
    second = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                              now=lambda: 1_789_000_000.0, state_path=path)
    f = SB.funnel(second.cohorts)["T1_top8"]
    assert f["virtual_first_fills"] == 1, "the tally must survive"
    assert f["distinct_markets"] == 1
    assert second.cycles == 1


def test_a_restart_does_not_double_count_the_same_action(tmp_path, monkeypatch):
    """Dedupe keys must survive too, or a restart re-counts the window."""
    from src import pmus_sports_l5_decision as dec
    path = str(tmp_path / "c.json")
    acts = [dict(_act("open"), t=10.0, target_price="0.40")]
    monkeypatch.setattr(dec, "decide_market", _Rec(acts))
    a = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                         now=lambda: 1.0, state_path=path)
    a.obs["m"] = __import__("collections").deque(
        [SB.observation_row("m", _md(), 1.0)] * 2)
    a.decide(["m"], {}); a.report([], 1, 1)
    b = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                         now=lambda: 1.0, state_path=path)
    b.obs["m"] = __import__("collections").deque(
        [SB.observation_row("m", _md(), 1.0)] * 2)
    b.decide(["m"], {})
    assert SB.funnel(b.cohorts)["T1_top8"]["virtual_first_fills"] == 1


def test_a_missing_state_file_starts_clean_rather_than_crashing(tmp_path):
    sb = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                          state_path=str(tmp_path / "nope" / "x.json"))
    assert sb.cohorts == {} and sb.cycles == 0


def test_an_unwritable_state_path_does_not_stop_the_loop(monkeypatch):
    """Observing matters more than persisting; a failed write is not fatal."""
    sb = SB.ShadowBreadth(get_json=lambda p: None, sleep=None,
                          state_path="/proc/cannot/write/here.json")
    rep = sb.report([], watched=0, eligible=0)
    assert rep["cycle"] == 1
