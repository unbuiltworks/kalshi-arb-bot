"""Live-path shadow: event selection + sequential replay of the handoff.

The shadow exists to choose ONE event and show the exact action sequence.
Its two failure modes are both silent, so both are pinned here: hindsight
leaking into a decision, and a starved tape reading as a bad market.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from datetime import datetime, timezone

from src import pmus_l5_handoff_shadow as sh
from src import pmus_l5_live_handoff as ho
from src import pmus_sports_l5_decision as l5d

T0 = 1_757_000_000.0


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _book(slug: str, t: float, bid: str, ask: str, cum: str,
          last: str | None = None) -> dict:
    return {"kind": "bbo_book", "t": _iso(t), "slug": slug, "league": "cfb",
            "bid": bid, "bid_touch_qty": "500", "ask": ask,
            "ask_touch_qty": "500", "ask_book_missing": False,
            "last_trade_px": last if last is not None else bid,
            "shares_traded_cum": cum, "market_state": "MARKET_STATE_OPEN"}


def _discovery(t: float, slug: str, event: str) -> dict:
    return {"kind": "discovery", "t": _iso(t),
            "targets": [{"slug": slug, "event_slug": event, "league": "cfb"}],
            "diagnostics": {"rotation_cycle": 0}}


def _tape(slug="mkt-a", event="evt-a", n=40, step=6.0):
    """A two-sided mid-band market that keeps printing on both sides."""
    rows = [_discovery(T0, slug, event)]
    cum = 1000.0
    for i in range(n):
        t = T0 + i * step
        # alternate a print at the bid and one at the ask so direction
        # inference sees genuine two-sided flow
        cum += 25.0
        last = "0.4500" if i % 2 == 0 else "0.4700"
        rows.append(_book(slug, t, "0.4500", "0.4700", f"{cum:.2f}", last))
    return rows


# ---------------- event selection ----------------

def test_selects_exactly_one_event_and_names_the_market():
    rows = _tape()
    now = T0 + 40 * 6.0
    rank = sh.rank_events(rows, now)
    pick = sh.select_event(rank)
    assert pick["selected"] == "evt-a"
    assert pick["market"] == "mkt-a"
    assert pick["blockers"] == []


def test_a_state_the_frozen_model_never_visited_is_never_selected():
    """Similarity is model support, not book attractiveness. An
    off-distribution state cannot be attributed to L5, so it cannot be
    the event we spend the one-event budget on."""
    rows = _tape()
    # 0.02/0.03 is a perfectly tight book and a state v2 never visited
    for r in rows:
        if r["kind"] == "bbo_book":
            r["bid"], r["ask"] = "0.0200", "0.0300"
            r["last_trade_px"] = "0.0200"
    rank = sh.rank_events(rows, T0 + 240)
    market = rank["events"][0]["markets"][0]
    assert market["in_distribution"] is False
    assert market["similarity"] == 0.0
    # pinned specifically: the refusal must come from model support, not
    # merely from the band filter, or the test passes for a reason that
    # would disappear the moment an off-distribution state sat in-band.
    assert "state_off_distribution" in market["reasons"]
    pick = sh.select_event(rank)
    assert pick["selected"] is None
    assert "state_off_distribution" in pick["closest_reasons"]


def test_selection_fails_closed_with_named_blockers_not_a_guess():
    rank = sh.rank_events([], T0)
    pick = sh.select_event(rank)
    assert pick["selected"] is None
    assert pick["blockers"] == ["no_live_events_on_tape"]


def test_a_starved_tape_reads_as_starvation_not_as_a_bad_market():
    """An empty observation window must not be reported as a one-sided
    book -- that would hide a collector failure as a market property."""
    rows = _tape()
    sim = ho.event_similarity(
        [r for r in rows if r["kind"] == "bbo_book"],
        T0 + 86_400)                      # a day later: nothing in window
    assert sim["reasons"] == ["no_observations_in_window"]


# ---------------- sequential replay ----------------

def test_decision_never_reads_a_print_from_its_own_future():
    """No hindsight: the fill simulator must ignore everything at or
    before the decision instant, or a decision could be justified by the
    very print that is supposed to fill it."""
    trades = [{"t": T0 - 1, "px": D("0.40"), "vol": 10.0,
               "dir_inferred": "sell"},
              {"t": T0, "px": D("0.40"), "vol": 10.0,
               "dir_inferred": "sell"}]
    assert sh._simulate_fill(trades, T0, D("0.45"), True)["filled"] is False


def test_maker_rest_is_not_held_past_the_freshness_bound():
    late = [{"t": T0 + sh.MAKER_REST_TIMEOUT_S + 1, "px": D("0.40"),
             "vol": 10.0, "dir_inferred": "sell"}]
    out = sh._simulate_fill(trades=late, after_t=T0, px=D("0.45"),
                            side_yes=True)
    assert out["filled"] is False
    assert "no_cross_within" in out["reason"]


def test_a_yes_join_fills_only_on_a_sell_through_it():
    cross = [{"t": T0 + 5, "px": D("0.45"), "vol": 3.0,
              "dir_inferred": "sell"}]
    miss = [{"t": T0 + 5, "px": D("0.46"), "vol": 3.0,
             "dir_inferred": "sell"}]
    buy = [{"t": T0 + 5, "px": D("0.40"), "vol": 3.0,
            "dir_inferred": "buy"}]
    assert sh._simulate_fill(cross, T0, D("0.45"), True)["filled"] is True
    assert sh._simulate_fill(miss, T0, D("0.45"), True)["filled"] is False
    assert sh._simulate_fill(buy, T0, D("0.45"), True)["filled"] is False


def test_a_no_join_fills_on_a_buy_through_its_complement():
    """Buying NO at c rests a YES offer at 1-c; it fills when YES trades
    up through 1-c, not when YES trades down."""
    up = [{"t": T0 + 5, "px": D("0.56"), "vol": 3.0, "dir_inferred": "buy"}]
    assert sh._simulate_fill(up, T0, D("0.44"), False)["filled"] is True
    down = [{"t": T0 + 5, "px": D("0.40"), "vol": 3.0,
             "dir_inferred": "buy"}]
    assert sh._simulate_fill(down, T0, D("0.44"), False)["filled"] is False


def test_replay_is_sequential_inventory_advances_only_on_a_fill():
    rows = _tape()
    obs = [r for r in rows if r["kind"] == "bbo_book"]
    res = sh.shadow_market(obs, T0 + 40 * 6.0)
    acts = res["actions"]
    assert acts, "the model produced no action on a mid-band two-sided book"
    filled = [a for a in acts if (a.get("fill") or {}).get("filled")]
    assert res["inventory"].yes + res["inventory"].no == len(filled)


def test_replay_never_exceeds_the_event_notional_cap():
    rows = _tape(n=200, step=6.0)
    obs = [r for r in rows if r["kind"] == "bbo_book"]
    res = sh.shadow_market(obs, T0 + 200 * 6.0)
    assert res["spent"] <= ho.MAX_EVENT_NOTIONAL
    submitted = sum(1 for a in res["actions"] if a["refuse_reason"] is None)
    assert submitted <= ho.MAX_ACTIONS


def test_every_action_row_carries_a_null_realized_pnl():
    rows = _tape()
    obs = [r for r in rows if r["kind"] == "bbo_book"]
    res = sh.shadow_market(obs, T0 + 240)
    assert all(a["realized_pnl"] is None for a in res["actions"])
    assert all(a["status"].startswith("shadow") for a in res["actions"])


# ---------------- report contract ----------------

def test_submission_boundary_is_never_ready_while_disarmed():
    out = sh.submission_boundary({"selected": "evt-a", "blockers": []},
                                 None)
    assert out["ready"] is False
    assert "handoff_disarmed_by_design" in out["blockers"]
    assert "provenance_inferred_not_observed" in out["blockers"]


def test_report_labels_the_min_trade_qty_as_an_assumption(tmp_path):
    (tmp_path / "raw").mkdir(parents=True)
    p = tmp_path / "raw" / f"pmus_{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    now = datetime.now(timezone.utc).timestamp()
    with p.open("w", encoding="utf-8") as fh:
        for r in _tape():
            r["t"] = _iso(now - 300 + (T0 - T0))
            fh.write(json.dumps(r) + "\n")
    out = sh.run(tmp_path, hours=1.0, now=now)
    assert out["sizing"]["minimum_trade_qty_source"] == "declared_assumption"
    assert out["armed"] is False
    assert out["realized_pnl"] is None
    assert out["submission_boundary"]["ready"] is False


def test_sizing_uses_price_times_quantity_not_the_price():
    qty = sh.mx.choose_micro_quantity(D("0.45"), sh.DECLARED_MIN_TRADE_QTY,
                                      ho.MAX_ACTION_NOTIONAL,
                                      ho.MAX_ACTION_NOTIONAL)
    assert ho.action_notional(D("0.45"), qty) <= ho.MAX_ACTION_NOTIONAL


def test_shadow_module_has_no_order_path():
    src = (sh.__file__ and open(sh.__file__, encoding="utf-8").read()) or ""
    for banned in ("urllib", "requests", "POST", "/v1/orders", "X-PM-"):
        assert banned not in src, f"shadow must not reach the venue: {banned}"


def test_similarity_is_model_support_not_a_tight_spread():
    """A tighter book must not raise similarity. Similarity measures how
    often the frozen model stood in this state -- nothing about the book."""
    wide = _tape()
    tight = _tape()
    for r in tight:
        if r["kind"] == "bbo_book":
            r["ask"] = "0.4550"          # 0.5c spread instead of 2c
    w = sh.rank_events(wide, T0 + 240)["events"][0]["best_similarity"]
    t = sh.rank_events(tight, T0 + 240)["events"][0]["best_similarity"]
    assert w == t


# ---- observation limits are not market verdicts ----

def test_flow_is_unmeasured_not_thin_when_we_barely_looked():
    """One rotation visit yields zero inferred trades by construction --
    volume-delta inference needs consecutive snapshots. Reporting that as
    thin flow asserts a market does not trade when we did not look."""
    rows = _tape(n=2, step=6.0)
    obs = [r for r in rows if r["kind"] == "bbo_book"]
    sim = ho.event_similarity(obs, T0 + 12)
    assert any(r.startswith("flow_unmeasured_only_") for r in sim["reasons"])
    assert not any(r.startswith("thin_flow") for r in sim["reasons"])
    assert sim["measurable"] is False


def test_flow_is_judged_once_there_is_enough_to_judge():
    rows = _tape(n=40, step=6.0)
    obs = [r for r in rows if r["kind"] == "bbo_book"]
    sim = ho.event_similarity(obs, T0 + 240)
    assert sim["measurable"] is True
    assert not any(r.startswith("flow_unmeasured") for r in sim["reasons"])


def test_an_unwatched_candidate_becomes_a_pin_recommendation():
    """A market blocked ONLY by how little we watched it must surface as
    something to pin, not vanish into 'nothing qualifies'."""
    rows = _tape(n=2, step=6.0)
    rank = sh.rank_events(rows, T0 + 12)
    pins = sh.pin_recommendations(rank)
    assert [p["slug"] for p in pins] == ["mkt-a"]
    assert pins[0]["blocked_only_by"]


def test_a_genuinely_unsuitable_market_is_not_a_pin_recommendation():
    """More observation cannot fix an off-distribution state or a price
    outside the band, so those must never be recommended for pinning."""
    rows = _tape(n=2, step=6.0)
    for r in rows:
        if r["kind"] == "bbo_book":
            r["bid"], r["ask"], r["last_trade_px"] = "0.0200", "0.0300", "0.02"
    rank = sh.rank_events(rows, T0 + 12)
    assert sh.pin_recommendations(rank) == []


def test_boundary_says_unwatched_rather_than_nothing_qualifies():
    out = sh.submission_boundary(
        {"selected": None, "blockers": ["thin_flow"]}, None,
        pins=[{"slug": "mkt-a"}])
    assert any(b.startswith("observation_limited_candidates_unpinned")
               for b in out["blockers"])


def test_holding_a_residual_is_disclosed_not_blocked():
    """OWNER DECISION 5547834091: a bounded residual is HELD to
    settlement. That is the intended behaviour, so it must be disclosed
    with its accounting -- blocking on it would block the strategy."""
    session = {"actions": [{"refuse_reason": None, "fill": {"filled": True},
                            "predicted_action": {"relation": "open"}}],
               "ended_legged": True, "residual_uncompleted": 2}
    out = sh.submission_boundary({"selected": "evt-a", "blockers": []},
                                 session)
    assert any(b.startswith("holds_residual_to_settlement")
               for b in out["disclosures"])
    assert any("no_completion_in_window" in b for b in out["disclosures"])
    assert not any("legged" in b for b in out["blockers"])
    assert not any("hold_vs_clear" in b for b in out["blockers"])
    assert out["residual_policy"] == "HOLD_TO_SETTLEMENT"


def test_a_flat_session_discloses_no_residual():
    session = {"actions": [{"refuse_reason": None, "fill": {"filled": True},
                            "predicted_action": {"relation": "complete"}}],
               "ended_legged": False, "residual_uncompleted": 0}
    out = sh.submission_boundary({"selected": "evt-a", "blockers": []},
                                 session)
    assert not any("residual" in d for d in out["disclosures"])
    assert not any("no_completion_in_window" in d for d in out["disclosures"])


def test_residual_exposure_is_quantified_not_merely_flagged():
    """"Ends legged" is not actionable on its own -- the owner needs the
    dollars at risk if the unmatched units settle to zero."""
    rows = _tape(n=60, step=6.0)
    obs = [r for r in rows if r["kind"] == "bbo_book"]
    res = sh.shadow_market(obs, T0 + 360)
    assert res["inventory"].residual != 0, "fixture must end legged"
    cost = res["residual_cost_theoretical"]
    assert cost > 0
    assert cost <= ho.MAX_EVENT_NOTIONAL


def test_a_flat_session_reports_no_residual_exposure():
    inv = l5d.Inventory()
    assert inv.residual == 0
    out = sh.submission_boundary({"selected": "e", "blockers": []},
                                 {"actions": [{"refuse_reason": None,
                                               "fill": {"filled": True},
                                               "predicted_action":
                                                   {"relation": "complete"}}],
                                  "ended_legged": False,
                                  "residual_uncompleted": 0})
    assert "owner_decision_needed_hold_vs_clear_residual" not in out["blockers"]


# ---- HOLD-to-settlement accounting (OWNER DECISION 5547834091) ----

def test_a_held_residual_reports_both_ends_of_settlement():
    """Quoting only the loss understates the position; quoting only the
    payout flatters it. Both ends, or the number is propaganda."""
    inv = l5d.Inventory()
    inv.record(True, D("0.40"))
    inv.record(True, D("0.50"))          # 2 YES units, avg 0.45
    acct = ho.residual_settlement_accounting(inv, D("0.02"))
    assert acct["policy"] == "HOLD_TO_SETTLEMENT"
    assert acct["residual_units"] == 2
    assert acct["residual_side"] == "YES"
    assert acct["held_to_settlement"] is True
    # cost basis 0.45 * 2 * 0.02 = 0.018
    assert D(acct["cost_basis"]) == D("0.018")
    assert D(acct["worst_case_settlement"]) == D("-0.018")
    # payout 2 * 0.02 = 0.04, less the 0.018 paid
    assert D(acct["best_case_settlement"]) == D("0.022")
    assert acct["realized_pnl"] is None


def test_a_flat_book_holds_nothing_and_says_so():
    acct = ho.residual_settlement_accounting(l5d.Inventory(), D("0.02"))
    assert acct["residual_units"] == 0
    assert acct["held_to_settlement"] is False
    assert acct["realized_pnl"] is None


def test_the_worst_case_never_exceeds_what_was_actually_committed():
    """A held residual cannot lose more than it cost -- these are
    binaries, not margin. The cap on spend is therefore the cap on loss."""
    inv = l5d.Inventory()
    for px in ("0.60", "0.62", "0.65"):
        inv.record(True, D(px))
    acct = ho.residual_settlement_accounting(inv, D("0.02"))
    assert -D(acct["worst_case_settlement"]) == D(acct["cost_basis"])
    assert D(acct["cost_basis"]) <= ho.MAX_EVENT_NOTIONAL


def test_no_flatten_path_exists_in_the_handoff():
    """The owner ruled HOLD, and explicitly ruled out inventing a flatten
    behaviour. Scanned as CODE, not prose -- the module discusses
    flattening in comments precisely because it must never do it."""
    import ast
    tree = ast.parse(open(ho.__file__, encoding="utf-8").read())
    banned = ("flatten", "sell", "close_position", "liquidate", "exit_position")
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    for b in banned:
        hits = [n for n in names if b in n.lower()]
        assert not hits, f"no exit path may exist here: {b} -> {hits}"


def test_the_session_report_states_the_residual_policy():
    inv = l5d.Inventory()
    inv.record(True, D("0.45"))
    rep = ho.session_report("evt", [], inv, D("0.009"))
    assert rep["residual_policy"] == "HOLD_TO_SETTLEMENT"
    assert rep["realized_pnl"] is None
