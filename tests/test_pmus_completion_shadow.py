"""Tests for the 24/7 non-trading completion-economics shadow.

Two classes of property matter: the arithmetic that decides good completion
from bad, and the guarantee that this process cannot trade.
"""

import ast
import json
from decimal import Decimal as D

import pytest

from src.pmus_completion_shadow import (PAR, classify, complement_touch,
                                        completion_economics, cycle, heartbeat,
                                        propose)


def book(bid="0.40", ask="0.42", slug="m", state="MARKET_STATE_OPEN"):
    return {"slug": slug, "state": state, "bid": D(bid), "ask": D(ask),
            "bid_depth": 5, "ask_depth": 5, "shares_traded": D("100")}


# ------------------------------ the complement side ------------------------------

def test_complement_of_a_no_surplus_is_the_yes_bid_not_one_minus_ask():
    """The bug this test exists for: on a 0.90/0.94 book a NO surplus at 0.06
    pairs with YES at the 0.90 bid for 0.96. Reading the complement as
    1-ask gives 0.06 and a 'pair' of 0.12, which is not a pair at all."""
    b = book("0.90", "0.94")
    assert complement_touch(b, "NO") == D("0.90")
    assert complement_touch(b, "YES") == D("0.06")


def test_pair_cost_uses_the_side_the_surplus_is_actually_on():
    b = book("0.90", "0.94")
    no_side = propose(b, D(5), D(5) * D("0.06"), D(0), D(0), surplus_side="NO")
    assert no_side["marginal_pair_cost"] == "0.96"
    assert no_side["complement_side"] == "YES"


def test_complement_refuses_a_book_with_no_quotes():
    assert complement_touch({"bid": None, "ask": D("0.5")}, "YES") is None


# ------------------------------ marginal vs weighted ------------------------------

def test_marginal_and_resulting_pair_cost_can_disagree():
    """The 2026-09-09 finding: each marginal pair can look acceptable while
    the inventory-weighted total lands above par. If these two ever collapse
    into one number the whole study is lost."""
    # 10 pairs already on the book at 0.90; now complete 5 more at 1.20
    e = completion_economics(surplus_qty=D(5), surplus_spend=D(5) * D("0.60"),
                             matched_qty=D(10), matched_spend=D(9),
                             complement_px=D("0.60"), fill_qty=D(5),
                             regime="l5_touch")
    assert e["marginal_pair_cost"] == "1.20"
    assert e["inventory_weighted_pair_cost_before"] == "0.9"
    assert D(e["resulting_pair_cost_if_filled"]) == D(15) / D(15)
    assert e["above_par_marginal"] is True
    # they diverge here, and the CLASSIFICATION must follow the finished
    # book: an above-par marginal that still lands the book at par is a
    # good completion. Goes red if classify() is pointed at the margin.
    assert e["above_par_resulting"] is False
    assert e["classification"].startswith("good_completion")


def test_resulting_above_par_is_classified_as_a_bad_completion():
    e = completion_economics(D(10), D(10) * D("0.70"), D(0), D(0),
                             complement_px=D("0.50"), fill_qty=D(10),
                             regime="l5_touch")
    assert e["marginal_pair_cost"] == "1.20"
    assert e["classification"].startswith("bad_completion")
    assert e["above_par_resulting"] is True


def test_resulting_at_or_below_par_is_a_good_completion():
    e = completion_economics(D(10), D(10) * D("0.40"), D(0), D(0),
                             complement_px=D("0.55"), fill_qty=D(10),
                             regime="l5_touch")
    assert e["classification"].startswith("good_completion")


def test_classification_is_decided_on_the_finished_book_not_the_margin():
    """Goes red if classify() is ever pointed at the marginal pair."""
    assert classify(None) == "no_pairs"
    assert classify(PAR).startswith("good_completion")
    assert classify(PAR + D("0.0001")).startswith("bad_completion")


def test_residual_before_and_after_are_both_recorded():
    e = completion_economics(D(8), D(4), D(0), D(0), D("0.5"), D(3), "l5_touch")
    assert (e["residual_before"], e["residual_after"]) == ("8", "5")


def test_fill_is_capped_by_the_residual_it_can_pair():
    e = completion_economics(D(2), D(1), D(0), D(0), D("0.5"), D(99), "l5_touch")
    assert e["fill_qty_modelled"] == "2" and e["residual_after"] == "0"


# ------------------------------ the pair_cost_max reading ------------------------------

def test_l5_touch_regime_is_recorded_as_ungated():
    """The answer to the owner's question, pinned. Under l5_touch the pricer
    returns pair_cost_max None and labels the pair 'reported not gated', so
    0.98 travels as an observation cap only."""
    e = completion_economics(D(5), D(5) * D("0.60"), D(0), D(0),
                             D("0.60"), D(5), "l5_touch")
    assert e["pair_cost_max_gates_this_regime"] is False
    assert e["pair_cost_max_permits"] is None
    assert e["observation_cap"] == "0.98"


def test_fee_safe_regime_is_gated_and_the_verdict_is_recorded():
    over = completion_economics(D(5), D(5) * D("0.60"), D(0), D(0),
                                D("0.60"), D(5), "fee_safe")
    under = completion_economics(D(5), D(5) * D("0.40"), D(0), D(0),
                                 D("0.50"), D(5), "fee_safe")
    assert over["pair_cost_max_gates_this_regime"] is True
    assert over["pair_cost_max_permits"] is False      # 1.20 > 0.98
    assert under["pair_cost_max_permits"] is True      # 0.90 <= 0.98


# ------------------------------ the loop ------------------------------

def test_cycle_seeds_the_cheaper_touch_and_remembers_its_side():
    inv = {}
    rows = cycle(["m"], inv, getter=lambda p: {"marketData": {
        "marketSlug": "m", "state": "MARKET_STATE_OPEN",
        "bestBid": {"value": "0.90"}, "bestAsk": {"value": "0.94"},
        "sharesTraded": "10", "bidDepth": 1, "askDepth": 1}})
    assert inv["m"]["side"] == "NO"          # 1-0.94 = 0.06 is cheaper than 0.90
    assert rows[0]["surplus_side"] == "NO"
    assert rows[0]["marginal_pair_cost"] == "0.96"


def test_a_market_that_fails_to_read_is_skipped_not_fatal():
    def getter(p):
        raise TimeoutError("boom")
    assert cycle(["bad"], {}, getter=getter) == []


def test_heartbeat_reports_the_non_trading_flags(tmp_path):
    hb = heartbeat(3, 12, 40, 0.0, out_dir=str(tmp_path))
    assert hb["places_orders"] is False and hb["read_only"] is True
    assert (tmp_path / "heartbeat.json").exists()


def test_heartbeat_survives_an_unwritable_state_dir():
    hb = heartbeat(1, 1, 1, 0.0, out_dir="/proc/nonexistent/nope")
    assert "heartbeat_write_failed" in hb


# ------------------------------ cannot trade, by AST ------------------------------

def _module_ast():
    import src.pmus_completion_shadow as m
    tree = ast.parse(open(m.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and \
                    isinstance(b[0].value, ast.Constant) and \
                    isinstance(b[0].value.value, str):
                node.body = b[1:]
    return tree


def test_module_contains_no_write_verb():
    verbs = {"POST", "PUT", "DELETE", "PATCH"}
    found = {n.value for n in ast.walk(_module_ast())
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.upper() in verbs}
    assert not found, f"write verb in code: {found}"


def test_the_write_verb_check_catches_a_real_violation():
    tree = ast.parse('def go():\n    return req(method="POST")\n')
    found = {n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and n.value.upper() in {"POST", "PUT", "DELETE", "PATCH"}}
    assert found == {"POST"}


def test_module_imports_no_executor_auth_or_session():
    banned = ("executor", "auth", "spring_l5_session", "micro_executor",
              "fill_ledger", "credential")
    mods = []
    for n in ast.walk(_module_ast()):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
    bad = [m for m in mods if any(b in m.lower() for b in banned)]
    assert not bad, f"order/credential import: {bad}"


def test_the_dockerfile_copies_no_order_transport():
    """The container is the real boundary: if the executor is not in the
    image, the process cannot place an order however it is invoked."""
    txt = open("deploy/completion_shadow/Dockerfile", encoding="utf-8").read()
    copied = [l for l in txt.splitlines() if l.strip().startswith("COPY")]
    banned = ("executor", "auth", "spring_l5_session", "fill_ledger", "q1")
    bad = [l for l in copied if any(b in l.lower() for b in banned)]
    assert not bad, f"order transport copied into the image: {bad}"
    assert any("pmus_completion_shadow.py" in l for l in copied)


# ---- the multi-completion PATH: the failure mode the entry pair cannot show

from decimal import Decimal as _D

from src.pmus_completion_shadow import (advance_path, flow_delta,
                                        flow_summary, side_hint)


def _st(qty="10", spend="4.0", side="YES"):
    return {"qty": _D(qty), "spend": _D(spend), "matched": _D(0),
            "matched_spend": _D(0), "side": side, "flow": [], "prev": None,
            "tranches": 0}


def test_repeated_completion_at_rising_prices_degrades_the_weighted_average():
    """The 2026-09-09 mechanism, reproduced: every tranche can look sane
    while the finished book lands above par."""
    st = _st(qty="10", spend="4.0")             # surplus avg 0.40
    for px in ("0.55", "0.62", "0.70", "0.78"):  # the market runs away
        advance_path(st, _D(px), _D("2.5"))
    assert st["qty"] == 0 and st["tranches"] == 4
    weighted = st["matched_spend"] / st["matched"]
    assert weighted > _D("1.00"), weighted
    assert classify(weighted).startswith("bad_completion")


def test_a_stable_book_completes_below_par_over_the_same_path():
    st = _st(qty="10", spend="4.0")
    for _ in range(4):
        advance_path(st, _D("0.55"), _D("2.5"))
    assert st["matched_spend"] / st["matched"] == _D("0.95")


def test_a_tranche_never_exceeds_the_remaining_surplus():
    st = _st(qty="2", spend="1.0")
    advance_path(st, _D("0.5"), _D("99"))
    assert st["qty"] == 0 and st["matched"] == _D(2)
    advance_path(st, _D("0.5"), _D("5"))        # nothing left to pair
    assert st["tranches"] == 1


def test_surplus_average_is_preserved_as_the_surplus_is_drawn_down():
    """Goes red if drawing down the surplus silently re-prices what remains."""
    st = _st(qty="10", spend="4.0")
    advance_path(st, _D("0.55"), _D("4"))
    assert st["spend"] / st["qty"] == _D("0.40")


# ---- flow measured on the SAME poll as the completion

def _s(shares, t, last="0.50", bid="0.49", ask="0.51"):
    return {"shares_traded": _D(shares), "last_trade_px": _D(last),
            "bid": _D(bid), "ask": _D(ask), "t": t}


def test_flow_is_the_first_difference_not_the_lifetime_counter():
    assert flow_delta(_s("500000", 0.0), _s("500000", 5.0), 5.0)["shares"] == 0


def test_flow_gap_is_not_zero_flow():
    assert flow_delta(None, _s("1", 0.0), 0.0)["gap"] is True
    assert flow_delta(_s("500", 0.0), _s("100", 5.0), 5.0)["why"] == \
        "counter_went_backwards"


def test_flow_summary_excludes_gaps_from_the_rate():
    s = flow_summary([{"gap": True, "why": "x"},
                      flow_delta(_s("0", 0.0), _s("5", 5.0), 5.0)])
    assert s["intervals"] == 1 and s["gaps"] == 1
    assert s["shares_per_min"] == pytest.approx(60.0)


def test_two_sided_needs_both_a_bid_print_and_an_ask_print():
    one = [flow_delta(_s("0", 0.0, last="0.49"), _s("5", 5.0, last="0.49"), 5.0)]
    assert flow_summary(one)["two_sided"] is False
    both = one + [flow_delta(_s("5", 5.0, last="0.49"),
                             _s("9", 10.0, last="0.51"), 10.0)]
    assert flow_summary(both)["two_sided"] is True


def test_side_hint_refuses_a_crossed_book():
    assert side_hint({"last_trade_px": _D("0.5"), "bid": _D("0.6"),
                      "ask": _D("0.4")}) is None


def test_every_completion_row_carries_the_flow_of_the_same_market():
    """The join has to exist on the row, or the two variables can never be
    crossed at the same instant on the same market."""
    inv = {}
    n = {"i": 0}

    def getter(p):
        n["i"] += 1
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": "0.45"},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    cycle(["m"], inv, getter=getter, now=0.0)
    rows = cycle(["m"], inv, getter=getter, now=5.0)
    assert rows[0]["flow"]["intervals"] == 1
    assert rows[0]["flow"]["prints_seen"] == 1
    assert rows[0]["path_modelled"] is True


def test_a_market_keeps_being_watched_after_its_book_completes():
    """Measured 2026-09-09: every market emitted exactly 5 rows and went
    silent once the hypothetical surplus was paired, so no row ever reached
    more than 4 flow intervals and two-sidedness could never be classified.
    A 'flow does not separate' result from that window would have been an
    artifact of this loop. Goes red if observation ever stops at completion.
    """
    inv, n = {}, {"i": 0}

    def getter(p):
        n["i"] += 1
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": "0.45"},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    rows = []
    for i in range(20):
        rows += cycle(["m"], inv, getter=getter, now=float(i) * 30.0)
    assert len(rows) == 20, f"observation stopped after {len(rows)} rows"
    assert rows[-1]["sessions"] >= 1, "the book never re-seeded"
    assert rows[-1]["flow"]["intervals"] >= 15, rows[-1]["flow"]


def test_flow_keeps_accumulating_across_re_seeds():
    """Flow is a property of the MARKET, not of one hypothetical session, so
    it must survive the re-seed that starts a new one."""
    inv, n = {}, {"i": 0}
    last = {"px": "0.45"}

    def getter(p):
        n["i"] += 1
        last["px"] = "0.47" if n["i"] % 2 else "0.45"   # alternate the side
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": last["px"]},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    rows = []
    for i in range(20):
        rows += cycle(["m"], inv, getter=getter, now=float(i) * 30.0)
    f = rows[-1]["flow"]
    assert f["two_sided"] is True, f     # prints on both sides were seen
    assert f["prints_seen"] >= 15


def test_the_shadow_opens_and_closes_a_persistence_window(capsys):
    """OWNER GO: the 30-60s interval must actually be watched end to end, and
    a closed window must be emitted. Goes red if the wiring is dropped."""
    inv, n = {}, {"i": 0}

    def getter(p):
        n["i"] += 1
        return {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                               "bestBid": {"value": "0.45"},
                               "bestAsk": {"value": "0.47"},
                               "lastTradePx": {"value": "0.45"},
                               "sharesTraded": str(n["i"] * 10),
                               "bidDepth": 1, "askDepth": 1}}
    # warm the mid series first: a window will not open until pre-entry
    # drift is measurable, so the fixture has to give it history
    for i in range(13):
        cycle(["m"], inv, getter=getter, now=float(i) * 5.0)    # 0..60s
    for i in range(7):
        cycle(["m"], inv, getter=getter, now=60.0 + float(i) * 25.0)
    out = capsys.readouterr().out
    assert "PMUS_PERSISTENCE_WINDOW" in out, "no window was ever closed"
    row = json.loads(out[out.index("PMUS_PERSISTENCE_WINDOW") + 24:]
                     .splitlines()[0])
    assert set(row["buckets"]) == {"a_0_30s", "b_30_60s", "c_60_120s"}
    assert row["closed"] is True
    # the interval we never operated in was actually observed
    assert row["buckets"]["b_30_60s"]["observations"] > 0


# --------------------------------------------------------------------------
# Flow-ranked selection. The cadence fix was right and the sample was still
# worthless: 5 s polling of the first 12 markets the venue listed gave ten
# county cricket matches and 4 opportunities in 2,136 observations. Selection,
# not cadence, was binding.
# --------------------------------------------------------------------------

from src import pmus_completion_shadow as cs
from src import pmus_flow_roster as _fr


def _book_payload(bid="0.40", ask="0.42", shares="100", last="0.41"):
    return {"marketData": {"marketSlug": "s", "state": "live",
                           "bestBid": bid, "bestAsk": ask,
                           "sharesTraded": shares, "lastTradePx": last}}



def _warm(inv, slug="a", getter=None, start=0.0, step=5.0, n=13):
    """Poll a market until it carries enough mid history to characterise
    pre-entry drift. A window will not open before this -- deliberately."""
    t = start
    g = getter or (lambda p: _book_payload())
    for _ in range(n):
        t += step
        cs.cycle([slug], inv, getter=g, now=t)
    return t


def test_discover_returns_the_wide_universe_when_unlimited(monkeypatch):
    """The cut belongs to the roster now, not to list order."""
    slugs = ["m%02d" % i for i in range(60)]
    monkeypatch.setattr(
        "src.pmus_sports_collector.fetch_live_events",
        lambda getter: ([{"slug": s} for s in slugs], {}))
    monkeypatch.setattr(
        "src.pmus_sports_collector.live_event_targets",
        lambda events: [{"slug": e["slug"]} for e in events])
    assert len(cs.discover()) == 60
    assert len(cs.discover(12)) == 12


def test_no_window_opens_on_a_market_the_roster_has_not_pinned():
    """The gate the whole rotation exists to enforce."""
    roster = _fr.Roster(roster_size=2)
    roster.set_universe(["a", "b"])
    assert roster.phase == _fr.SURVEY
    inv = {}
    n = 0.0
    for _ in range(6):
        n += 5.0
        cs.cycle(["a"], inv, getter=lambda p: _book_payload(), now=n,
                 roster=roster)
    assert inv["a"].get("window") is None, \
        "a survey-phase market must not be observed"


def test_a_pinned_market_does_open_a_window():
    roster = _fr.Roster(roster_size=2)
    roster.set_universe(["a", "b"])
    roster.phase = _fr.PINNED
    roster.roster = ["a"]
    roster.pinned_scores = {"a": 1.0}
    inv = {}
    t = _warm(inv)
    cs.cycle(["a"], inv, getter=lambda p: _book_payload(), now=t + 5.0,
             roster=roster)
    assert inv["a"].get("window") is not None


def test_no_roster_preserves_the_previous_behaviour():
    """Callers that pass no roster are unchanged; the gate is opt-in."""
    inv = {}
    t = _warm(inv)
    cs.cycle(["a"], inv, getter=lambda p: _book_payload(), now=t + 5.0)
    assert inv["a"].get("window") is not None


def test_the_shadow_never_polls_more_than_the_rate_budget_allows():
    """12 is floor(BUDGET_RPS_headroom * poll_s) at 5 s, not a preference."""
    roster = _fr.Roster(roster_size=cs.DEFAULT_MAX_MARKETS)
    roster.set_universe(["m%02d" % i for i in range(60)])
    for _ in range(120):
        assert len(roster.current()) <= cs.DEFAULT_MAX_MARKETS
        roster.advance({s: {"intervals": 8, "prints_seen": 4,
                            "shares_per_min": 10.0, "two_sided": True}
                        for s in roster.universe})


def test_a_rotation_gap_is_a_gap_not_a_multi_minute_flow_interval():
    """Rotation leaves a market unpolled for a whole pinned phase.

    First-differencing across that is a several-minute average masquerading
    as a 5 s observation, and flow_summary weights by elapsed time, so one
    such interval would outweigh every contemporaneous one on the market.
    """
    prev = {"shares_traded": D("100"), "t": 0.0}
    cur = {"shares_traded": D("500"), "bid": D("0.40"), "ask": D("0.42"),
           "last_trade_px": D("0.41")}
    d = cs.flow_delta(prev, cur, cs.MAX_FLOW_INTERVAL_S + 1.0)
    assert d["gap"] is True
    assert d["why"] == "sampling_gap"
    assert "shares_per_min" not in d
    # and an interval inside the bound is still a measurement
    ok = cs.flow_delta(prev, cur, cs.MAX_FLOW_INTERVAL_S - 1.0)
    assert ok["gap"] is False


def test_a_gapped_rotation_interval_does_not_enter_the_flow_rate():
    """The gap must be excluded from the rate, not folded in as zero."""
    good = [cs.flow_delta({"shares_traded": D("0"), "t": 0.0},
                          {"shares_traded": D("10"), "bid": D("0.40"),
                           "ask": D("0.42"), "last_trade_px": D("0.41")}, 5.0)]
    gapped = good + [{"gap": True, "why": "sampling_gap", "interval_s": 700.0}]
    assert cs.flow_summary(good)["shares_per_min"] == \
        cs.flow_summary(gapped)["shares_per_min"]
    assert cs.flow_summary(gapped)["gaps"] == 1


def test_flow_history_is_bounded_to_recent_activity():
    inv = {}
    n = 0.0
    for _ in range(cs.FLOW_HISTORY + 25):
        n += 5.0
        cs.cycle(["a"], inv, getter=lambda p: _book_payload(shares=str(n)),
                 now=n)
    assert len(inv["a"]["flow"]) <= cs.FLOW_HISTORY


def test_one_print_cannot_decide_two_sidedness():
    """The defect the first live pin exposed, pinned at its source.

    Deciding two-sidedness needs a print on each side, so one print decides
    nothing. Reporting it as "not two-sided" gave all 18 scored markets the
    same value and silently removed the term from the ranking.
    """
    one = [flow_delta(_s("0", 0.0, last="0.49"), _s("5", 5.0, last="0.49"), 5.0)]
    s = flow_summary(one)
    assert s["sided_prints"] == 1
    assert s["two_sided_resolvable"] is False, \
        "one print is not a decision about two-sidedness"
    two = one + [flow_delta(_s("5", 5.0, last="0.49"),
                            _s("9", 10.0, last="0.49"), 10.0)]
    assert flow_summary(two)["two_sided_resolvable"] is True
    assert flow_summary(two)["two_sided"] is False, \
        "two prints on the SAME side decides it, and decides it one-sided"


def test_prints_without_a_readable_side_do_not_count_toward_the_decision():
    """A crossed or unreadable book yields no side hint; that is not evidence."""
    d = flow_delta({"shares_traded": D("0"), "t": 0.0},
                   {"shares_traded": D("5"), "bid": D("0.60"),
                    "ask": D("0.40"), "last_trade_px": D("0.50")}, 5.0)
    assert d["side_hint"] is None
    s = flow_summary([d, d])
    assert s["prints_seen"] == 2
    assert s["sided_prints"] == 0
    assert s["two_sided_resolvable"] is False


# --------------------------------------------------------------------------
# Pre-entry drift. Definition frozen in analysis/l5/PREREG_PRE_ENTRY_DRIFT.md
# BEFORE this field existed. These tests pin the frozen definition; changing
# them to make a result look better is the failure they exist to prevent.
# --------------------------------------------------------------------------

def _mids(*pairs):
    return [(t, D(str(m))) for t, m in pairs]


def test_the_frozen_constants_are_the_preregistered_ones():
    assert cs.DRIFT_LOOKBACK_S == 60.0
    assert cs.MIN_DRIFT_SAMPLES == 6
    assert cs.DRIFT_LOOKBACKS_S == (30.0, 60.0, 120.0)


def test_drift_is_unsigned():
    """Ex ante we cannot know which direction hurts; the loss is asymmetric."""
    up = _mids(*[(i * 10.0, 0.40 + i * 0.01) for i in range(7)])
    down = _mids(*[(i * 10.0, 0.40 - i * 0.01) for i in range(7)])
    a = cs.drift_at(up, 60.0, 60.0)
    b = cs.drift_at(down, 60.0, 60.0)
    assert a["drift_abs"] == b["drift_abs"], "sign must not change the metric"
    assert D(a["drift_abs"]) > 0


def test_too_few_samples_is_unresolvable_not_zero_drift():
    """A just-pinned market must not land in the calm half by construction."""
    r = cs.drift_at(_mids((0.0, 0.5), (5.0, 0.5)), 60.0, 60.0)
    assert r["resolvable"] is False
    assert "drift_abs" not in r, "an unresolvable window must carry no value"
    assert "need 6" in r["why"]


def test_enough_samples_over_too_short_a_span_is_unresolvable():
    """Six samples crammed into 10s cannot speak for a 60s lookback."""
    r = cs.drift_at(_mids(*[(50.0 + i, 0.5) for i in range(7)]), 60.0, 60.0)
    assert r["resolvable"] is False
    assert "spans" in r["why"]


def test_a_flat_market_resolves_to_zero_drift():
    r = cs.drift_at(_mids(*[(i * 10.0, 0.5) for i in range(7)]), 60.0, 60.0)
    assert r["resolvable"] is True
    assert D(r["drift_abs"]) == 0
    assert D(r["excursion"]) == 0


def test_excursion_catches_chop_that_returns_to_its_start():
    """drift_abs sees nothing; the secondary metric is what notices."""
    chop = _mids((0.0, 0.50), (10.0, 0.60), (20.0, 0.40), (30.0, 0.55),
                 (40.0, 0.45), (50.0, 0.50), (60.0, 0.50))
    r = cs.drift_at(chop, 60.0, 60.0)
    assert D(r["drift_abs"]) == 0, "start and end are the same"
    assert D(r["excursion"]) == D("0.20"), "but the market moved 20 cents"


def test_samples_outside_the_lookback_are_excluded():
    old = _mids(*[(i * 10.0, 0.20) for i in range(7)])
    recent = _mids(*[(100.0 + i * 10.0, 0.50) for i in range(7)])
    r = cs.drift_at(old + recent, 160.0, 60.0)
    assert r["resolvable"] is True
    assert D(r["drift_abs"]) == 0, "the 0.20 era is outside the 60s lookback"


def test_pre_entry_drift_reports_the_primary_and_both_secondaries():
    mids = _mids(*[(i * 5.0, 0.40 + i * 0.002) for i in range(25)])
    d = cs.pre_entry_drift(mids, 120.0)
    assert d["primary_lookback_s"] == 60.0
    assert set(d["by_lookback"]) == {"30", "60", "120"}
    assert d["resolvable"] is True
    assert d["drift_abs"] == d["by_lookback"]["60"]["drift_abs"], \
        "the top-level value must be the PRIMARY lookback, not the best one"
    assert d["prereg"].endswith("PREREG_PRE_ENTRY_DRIFT.md")


def test_an_unresolvable_primary_says_why_at_the_top_level():
    d = cs.pre_entry_drift(_mids((0.0, 0.5)), 60.0)
    assert d["resolvable"] is False
    assert d["drift_abs"] is None
    assert d["why_unresolvable"]


def test_a_window_is_deferred_until_its_drift_can_be_measured():
    """A freshly pinned market has no history; do not open a window we would
    only have to discard. Deferring changes no metric -- the prereg already
    excludes unresolvable windows -- it just stops us paying for them."""
    inv, n = {}, 0.0
    for _ in range(3):
        n += 5.0
        cs.cycle(["a"], inv, getter=lambda p: _book_payload(), now=n)
    assert inv["a"].get("window") is None, "too little history to characterise"
    assert inv["a"]["drift_deferrals"] >= 1


def test_the_window_carries_drift_measured_only_from_samples_before_it():
    """No look-ahead: the series is pruned to `now` when the window opens."""
    inv, n = {}, 0.0
    px = 0.40
    for _ in range(20):
        n += 5.0
        px += 0.001
        cs.cycle(["a"], inv,
                 getter=lambda p, v=px: _book_payload(bid="%.3f" % v,
                                                      ask="%.3f" % (v + 0.02)),
                 now=n)
    w = inv["a"].get("window")
    assert w is not None, "60s of history should allow a window to open"
    d = w["pre_entry_drift"]
    assert d["resolvable"] is True
    assert d["by_lookback"]["60"]["span_s"] <= 60.0
    assert D(d["drift_abs"]) > 0, "a drifting market must show drift"


def test_a_crossed_book_yields_no_mid_and_no_drift_sample():
    assert cs.mid_of({"bid": D("0.60"), "ask": D("0.40")}) is None
    assert cs.mid_of({"bid": None, "ask": D("0.40")}) is None
    assert cs.mid_of({"bid": D("0.40"), "ask": D("0.42")}) == D("0.41")
