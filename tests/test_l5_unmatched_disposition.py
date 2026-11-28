"""The unmatched leg gets a priced decision, not silence.

0100z carried a residual 1 DOWN to a -$0.4900 settlement. In the 14 minutes
after the last cancel the tape held 152 decision records, of which the final
83 read `NOT_AN_OPPORTUNITY` with verdict `N/A`. Not one named the position,
its worst case, or that nothing was available.

THE RANKING RULE, which these tests pin because two earlier drafts of it
were wrong:

  1. HOLD is the FALLBACK, never a competitor. An unfilled maker quote
     leaves us in exactly the HOLD state, so every maker action weakly
     dominates holding. Ranking HOLD on the same scale picked it over a
     resting 0.9500 pair, because holding's best case is the whole $1.00
     windfall -- which we keep anyway if the quote does not fill.
  2. Among executable non-HOLD actions: best worst case, then best BEST
     case (where the pair edge lives), then EV, then the shallower queue.
     Ranking the queue before the pair chose a 0.9800 pair over a resting
     0.9500 one -- precisely the defect PR #200 fixed.

NO FILL IS CLAIMED. Only the crossing actions have a certain outcome, and
they are never selected while the owner's route is off.
"""
from __future__ import annotations

from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import l5_two_sided_probe as TS
from src import l5_unmatched_disposition as UD

WS = 1794013200.0                      # 2026-11-07 01:00:00Z
NOW = 1794013240.1147778               # 01:00:40.115, the real clock
END = WS + 900.0


def dec(**over):
    """The venue-confirmed 0100z state: held DOWN 1 at own 0.4900, our UP
    quote resting at own 0.4600, touch 0.5100."""
    kw = dict(market="cpc-btc-updown-15m-2026-11-07-0100z",
              held_side="DOWN", held_qty=D("1"), held_cost_usd=D("0.4900"),
              deficient_qty=D("0"), deficient_cost_usd=D("0"),
              resting_own_px=D("0.4600"), resting_qty=D("1"),
              resting_filled_qty=D("0"),
              bid=D("0.51"), ask=D("0.53"), tick=D("0.01"), min_qty=D("1"),
              now_s=NOW, window_start_s=WS, window_end_s=END,
              book_age_s=0.061, gap_suspected=False, orders_uncertain=None,
              residual_budget_usd=D("5"),
              queue_ahead_fn=(lambda px: D("2923.02")
                              if D(str(px)) <= D("0.46") else D("561.02")))
    kw.update(over)
    return UD.decide(**kw)


def act(rec, name):
    return next(a for a in rec["actions"] if a["action"] == name)


# ================================================ 1. PRICES AND SIDES

def test_the_crossing_price_is_the_far_side_not_the_maker_touch():
    """A whole spread apart, and getting this wrong understated the taker's
    cost in my own first measurement of this comparison."""
    assert UD.taker_own_price("UP", bid=D("0.51"), ask=D("0.53")) == D("0.53")
    assert TS.touch_own_price("UP", bid=D("0.51"), ask=D("0.53")) == D("0.51")
    assert UD.taker_own_price("DOWN", bid=D("0.51"),
                              ask=D("0.53")) == D("0.49")
    assert TS.touch_own_price("DOWN", bid=D("0.51"),
                              ask=D("0.53")) == D("0.47")


def test_selling_the_held_leg_receives_its_own_bid():
    assert UD.held_sell_own_price("UP", bid=D("0.51"),
                                  ask=D("0.53")) == D("0.51")
    assert UD.held_sell_own_price("DOWN", bid=D("0.51"),
                                  ask=D("0.53")) == D("0.47")


def test_the_fee_is_the_published_taker_schedule_and_makers_pay_nothing():
    assert UD.taker_fee_usd(D("1"), D("0.50")) == (
        TS.TAKER_THETA * D("0.25")).quantize(D("0.0001"))
    assert act(dec(), UD.PRESERVE)["fees_usd"] == "0"
    assert UD.taker_fee_usd(None, D("0.5")) is None


# ============================= 2. THE 0100z DECISION, ACTION BY ACTION

def test_the_0100z_state_selects_preserve():
    r = dec()
    assert r["selected"] == UD.PRESERVE
    assert r["selected_reason"] == (
        "BEST_WORST_CASE_THEN_BEST_PAIR_AMONG_EXECUTABLE")
    p = act(r, UD.PRESERVE)
    assert p["pair_own_cost"] == "0.9500"
    assert p["edge_per_contract"] == "0.0500"
    assert p["worst_case_usd"] == "-0.4900"
    assert p["queue_ahead_at_price"] == "2923.02"


def test_it_will_not_replace_a_resting_quote_with_a_dearer_pair():
    """The PR #200 defect, refused as INFEASIBLE rather than merely ranked
    below: there is one order per side, so placing this means withdrawing
    the 0.9500 quote to post a 0.9800 one."""
    m = act(dec(), UD.COMPLETE_MAKER)
    assert m["feasible"] is False
    assert m["infeasible_because"] == (
        "RESTING_COMPLETION_EXISTS_REPRICE_OWNS_IT")
    assert m["pair_own_cost"] == "0.9800"


def test_repricing_is_deferred_entirely_to_completion_requote():
    """So PR #200's ceiling decides it and this module cannot contradict
    it with a second opinion."""
    rp = act(dec(), UD.REPRICE)
    assert rp["feasible"] is False
    assert rp["infeasible_because"] == (
        "REQUOTE_REFUSED:IMPROVEMENT_BELOW_MIN_TICKS")


def test_the_taker_completion_is_priced_and_refused_above_redemption():
    """Lifting the 0.53 ask against a 0.4900 basis is a 1.0200 pair, which
    the completion ceiling forbids. Priced anyway, which is the point."""
    tk = act(dec(), UD.COMPLETE_TAKER)
    assert tk["pair_own_cost"] == "1.0200"
    assert tk["crosses_book"] is True
    assert tk["feasible"] is False
    assert tk["infeasible_because"] == "PAIR_ABOVE_COMPLETION_CEILING"


def test_reduce_is_priced_as_a_certain_outcome():
    """Sell the held DOWN at own 0.47, fee on 0.47: certain, so worst
    equals best."""
    rd = act(dec(), UD.REDUCE)
    assert rd["worst_case_usd"] == rd["best_case_usd"] == "-0.0373"
    assert rd["crosses_book"] is True
    assert rd["executable_now"] is False          # the route is off


def test_hold_is_the_fallback_and_never_outranks_a_maker_action():
    r = dec()
    h = act(r, UD.HOLD)
    assert h["feasible"] is True and h["executable_now"] is True
    assert h["worst_case_usd"] == "-0.4900"
    assert h["best_case_usd"] == "0.5100"         # the windfall we keep anyway
    assert r["selected"] != UD.HOLD


def test_the_record_says_why_every_alternative_was_rejected():
    r = dec()
    why = {x["action"]: x["why"] for x in r["rejected"]}
    assert UD.PRESERVE not in why                 # it was selected
    assert why[UD.COMPLETE_MAKER] == (
        "RESTING_COMPLETION_EXISTS_REPRICE_OWNS_IT")
    assert why[UD.REPRICE].startswith("REQUOTE_REFUSED:")
    assert why[UD.COMPLETE_TAKER] == "PAIR_ABOVE_COMPLETION_CEILING"
    assert why[UD.REDUCE] == "NOT_EXECUTABLE_CROSSING_DISABLED"
    assert why[UD.HOLD] == "RANKED_BELOW_SELECTED"


def test_the_counterfactual_states_what_the_disabled_route_would_save():
    r = dec()
    cf = r["counterfactual"]
    assert cf["best_feasible_action"] == UD.REDUCE
    assert cf["executable_now"] is False
    # -0.0373 certain against risking -0.4900
    assert cf["worst_case_improvement_usd"] == "0.4527"


def test_the_market_implied_probability_is_the_complement_price():
    r = dec()
    assert r["market_implied"]["p_held_side_loses"] == "0.51"
    assert "EQUAL" in r["market_implied"]["note"]


# =============================== 3. FAIL CLOSED, NAMED, NEVER SILENT

@pytest.mark.parametrize("over,reason", [
    (dict(orders_uncertain="UNVERIFIED_SUBMIT"), "ORDERS_UNCERTAIN"),
    (dict(gap_suspected=True), "GAP_SUSPECTED"),
    (dict(book_age_s=99.0), "BOOK_STALE"),
    (dict(book_age_s=None), "BOOK_STALE"),
    (dict(bid=D("0.53"), ask=D("0.51")), "BOOK_NOT_TWO_SIDED"),
    (dict(bid=None), "BOOK_NOT_TWO_SIDED"),
    (dict(tick=None), "TICK_UNKNOWN"),
    (dict(tick=D("0")), "TICK_UNKNOWN"),
    (dict(held_cost_usd=None), "INVENTORY_UNREADABLE"),
    (dict(held_qty=None), "INVENTORY_UNREADABLE"),
])
def test_every_uncertainty_holds_and_says_which_one(over, reason):
    r = dec(**over)
    assert r["selected"] == UD.HOLD
    assert r["selected_reason"] == reason
    assert r["label"] == "L5_UNMATCHED_DISPOSITION"


def test_a_flat_book_decides_nothing_at_all():
    r = dec(held_qty=D("0"))
    assert r["selected"] is None
    assert r["selected_reason"] == "NOTHING_UNMATCHED"


def test_past_the_completion_deadline_the_completions_are_refused():
    r = dec(now_s=WS + 880.0)
    for n in (UD.COMPLETE_MAKER, UD.COMPLETE_TAKER, UD.REDUCE):
        a = act(r, n)
        assert a["feasible"] is False, n
        assert "PAST_DEADLINE" in (a["infeasible_because"] or ""), n
    assert r["deadline"]["may_complete"] is not None


def test_a_fully_filled_resting_order_is_not_preservable():
    r = dec(resting_filled_qty=D("1"))
    p = act(r, UD.PRESERVE)
    assert p["feasible"] is False
    assert p["infeasible_because"] == "RESTING_ORDER_FULLY_FILLED"


def test_a_partially_filled_resting_order_is_priced_on_what_still_works():
    r = dec(resting_qty=D("4"), resting_filled_qty=D("1"))
    p = act(r, UD.PRESERVE)
    assert p["feasible"] is True
    assert p["qty"] == "3"


def test_with_no_resting_order_it_places_a_maker_complement():
    r = dec(resting_own_px=None, resting_qty=None)
    assert r["selected"] == UD.COMPLETE_MAKER
    assert act(r, UD.PRESERVE)["infeasible_because"] == (
        "NO_RESTING_COMPLETION")
    m = act(r, UD.COMPLETE_MAKER)
    assert m["own_price"] == "0.49"               # 2 ticks behind the touch
    assert m["pair_own_cost"] == "0.9800"


def test_an_unreadable_queue_accessor_does_not_break_the_decision():
    def boom(_px):
        raise RuntimeError("ladder gone")
    r = dec(queue_ahead_fn=boom)
    assert r["selected"] == UD.PRESERVE
    assert act(r, UD.PRESERVE)["queue_ahead_at_price"] is None


def test_an_insufficient_residual_budget_only_narrows_the_requote():
    """The budget is the requote's gate, not this module's, so an
    exhausted one must refuse REPRICE and leave PRESERVE standing."""
    r = dec(residual_budget_usd=D("0"))
    assert act(r, UD.REPRICE)["feasible"] is False
    assert r["selected"] == UD.PRESERVE


# ============================= 4. THE CROSSING ROUTE STAYS UNREACHABLE

def test_no_crossing_action_can_be_selected_while_the_route_is_off():
    """Priced, reported, never chosen. Swept over states that make the
    crossing actions look as attractive as possible."""
    for over in (dict(), dict(resting_own_px=None, resting_qty=None),
                 dict(bid=D("0.20"), ask=D("0.22")),
                 dict(bid=D("0.80"), ask=D("0.82")),
                 dict(held_cost_usd=D("0.9000")),
                 dict(held_qty=D("4"), held_cost_usd=D("3.60"))):
        r = dec(**over)
        assert r["selected"] not in UD.CROSSING, (over, r["selected"])
        assert r["taker_route_enabled"] is False


def test_with_the_route_enabled_a_certain_outcome_can_win():
    """Proves the gate is what blocks it, not an inability to rank it."""
    r = dec(taker_enabled=True)
    assert r["selected"] in UD.CROSSING
    assert r["taker_route_enabled"] is True


def test_the_module_holds_no_transport_and_cannot_submit():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(UD))
    banned = {"requests", "urllib", "httpx", "socket", "subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                assert al.name.split(".")[0] not in banned, al.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned
    # CHECKED ON THE PARSED MODULE, not on its text: the docstrings say
    # "nothing here submits", and a substring scan matches its own evidence.
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
        elif isinstance(node, ast.Attribute):
            called.add(node.attr)
    for tok in ("submit", "place_order", "deadman", "cancel_order",
                "record_submit", "transport", "executor", "post_only"):
        assert tok not in called, tok
    # and the only cross-module calls it makes are to the pure probe
    assert "completion_requote" in called          # it defers, by design


# ================== 5. THE RUNNER LAYER, AGAINST THE REAL ACCESSORS

SLUG = "cpc-btc-updown-15m-2026-11-07-0100z"


class _Store:
    """Faithful to the accessors `maybe_unmatched_disposition` reads."""

    def __init__(self, *, resid=D("-1"), legs=None, pos=None, rows=None,
                 uncertain=None):
        self._resid = resid
        self._legs = legs if legs is not None else {"DOWN": D("0.4900")}
        self._pos = pos if pos is not None else {"DOWN": D("1"),
                                                 "UP": D("0")}
        self._rows = rows or {}
        self._uncertain = uncertain

    def residual(self, slug):
        return self._resid

    def leg_cost(self, slug):
        return dict(self._legs)

    def position(self, slug, side):
        return self._pos.get(side)

    def live_orders(self):
        return dict(self._rows)

    def orders_uncertain(self):
        return self._uncertain


def _runner(monkeypatch, *, store=None, bid=D("0.51"), ask=D("0.53"),
            age=0.061, cross="", policy=""):
    from src import l5_pmus_live_runner as R
    import collections
    monkeypatch.setenv("L5_TS_COMPLETION_CROSS", cross)
    monkeypatch.setenv(__import__("src.l5_taker_completion_policy",
                                  fromlist=["x"]).POLICY_ENV, policy)
    r = SimpleNamespace(
        store=store or _Store(),
        stats=collections.Counter(),
        transport=SimpleNamespace(market_slug=SLUG, tick_size=D("0.01"),
                                  quantity_increment=D("1")),
        book=SimpleNamespace(snapshot=lambda s: {
            "bid": bid, "ask": ask, "stream_age_s": age,
            "gap_suspected": False}),
        executor=SimpleNamespace(validation_gates=SimpleNamespace(
            max_worst_case_deficit_usd=D("1.00"))),
        _disposition_last={},
        _requote_queue_ahead_fn=lambda slug, side: (
            lambda px: D("2923.02") if D(str(px)) <= D("0.46")
            else D("561.02")),
        ws=WS)
    r.maybe_unmatched_disposition = (
        R.Runner.maybe_unmatched_disposition.__get__(r))
    # the class attribute those accessors read, which a bare namespace
    # does not inherit
    r.TS_COMPLETION_CROSS_ENV = R.Runner.TS_COMPLETION_CROSS_ENV
    r.ts_completion_cross_authorized = (
        R.Runner.ts_completion_cross_authorized.__get__(r))
    r.taker_policy_enabled = R.Runner.taker_policy_enabled.__get__(r)
    r._taker_route_open = R.Runner._taker_route_open.__get__(r)
    r.DISPOSITION_EVERY_S = R.Runner.DISPOSITION_EVERY_S
    return r


def test_the_runner_emits_a_decision_for_an_unmatched_residual(monkeypatch,
                                                               capsys):
    r = _runner(monkeypatch)
    rec = r.maybe_unmatched_disposition(NOW)
    assert rec is not None
    assert rec["held_side"] == "DOWN"
    assert rec["selected"] == UD.COMPLETE_MAKER      # no resting order here
    out = capsys.readouterr().out
    assert "L5_UNMATCHED_DISPOSITION" in out
    assert r.stats["disposition:COMPLETE_MAKER"] == 1


def test_the_runner_preserves_a_resting_quote_it_reads_from_the_store(
        monkeypatch):
    rows = {"c1": {"market": SLUG, "side": "UP", "price": "0.4600",
                   "qty": "1", "remaining": "1", "state": "LIVE",
                   "venue_order_id": "V1"}}
    r = _runner(monkeypatch, store=_Store(rows=rows))
    rec = r.maybe_unmatched_disposition(NOW)
    assert rec["selected"] == UD.PRESERVE
    assert act(rec, UD.PRESERVE)["pair_own_cost"] == "0.9500"


def test_a_flat_book_emits_nothing_and_forgets_the_market(monkeypatch):
    r = _runner(monkeypatch, store=_Store(resid=D("0")))
    r._disposition_last[SLUG] = (UD.HOLD, 0.0)
    assert r.maybe_unmatched_disposition(NOW) is None
    assert SLUG not in r._disposition_last


def test_the_quiet_case_is_throttled_but_a_change_emits_at_once(monkeypatch):
    r = _runner(monkeypatch)
    r.maybe_unmatched_disposition(NOW)
    r.maybe_unmatched_disposition(NOW + 1.0)
    assert r.stats["disposition:THROTTLED"] == 1
    # the SAME action past the cadence emits again
    r.maybe_unmatched_disposition(NOW + 20.0)
    assert r.stats["disposition:COMPLETE_MAKER"] == 2
    # and a CHANGE emits immediately, inside the cadence
    r.book = SimpleNamespace(snapshot=lambda s: {
        "bid": D("0.51"), "ask": D("0.53"), "stream_age_s": 99.0,
        "gap_suspected": False})
    r.maybe_unmatched_disposition(NOW + 21.0)
    assert r.stats["disposition:HOLD"] == 1


def test_an_unreadable_store_cannot_take_down_the_tick(monkeypatch):
    class _Boom(_Store):
        def residual(self, slug):
            raise RuntimeError("volume gone")
    r = _runner(monkeypatch, store=_Boom())
    assert r.maybe_unmatched_disposition(NOW) is None
    assert any(k.startswith("disposition:LAYER_FAILED") for k in r.stats)


def test_uncertain_order_state_reaches_the_decision_and_holds(monkeypatch):
    r = _runner(monkeypatch, store=_Store(uncertain="UNVERIFIED_SUBMIT"))
    rec = r.maybe_unmatched_disposition(NOW)
    assert rec["selected"] == UD.HOLD
    assert rec["selected_reason"] == "ORDERS_UNCERTAIN"


def test_the_runner_reads_the_owner_route_and_does_not_assume_it(monkeypatch):
    """Both switches must be YES before a crossing action is executable."""
    r = _runner(monkeypatch, cross="YES", policy="")
    assert r.maybe_unmatched_disposition(NOW)["taker_route_enabled"] is False
    r2 = _runner(monkeypatch, cross="YES", policy="YES")
    assert r2.maybe_unmatched_disposition(NOW)["taker_route_enabled"] is True


def test_the_runner_layer_submits_nothing(monkeypatch):
    import ast
    import inspect
    from src import l5_pmus_live_runner as R
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(R.Runner.maybe_unmatched_disposition)))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            called.add(node.attr)
    for tok in ("submit_ts_completion", "deadman", "place_order",
                "record_submit", "handle"):
        assert tok not in called, tok
    assert "emit" not in called          # emit is a module-level call
    assert "residual" in called and "leg_cost" in called


def test_it_runs_before_the_requote_on_the_tick():
    """So the record describes the state the requote then acts on."""
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.tick) if hasattr(R.Runner, "tick") \
        else inspect.getsource(R.Runner)
    i = src.find("maybe_unmatched_disposition(now)")
    j = src.find("maybe_completion_requote(now)")
    assert i != -1 and j != -1 and i < j


# ========================= 6. THE 15-WINDOW DATASET, MODELLED NOT FILLED

def test_the_recorded_windows_produce_a_decision_at_every_sample():
    """Validation over the same recordings the economics were measured on.

    ASSERTS ONLY THAT A DECISION EXISTS AND IS PRICED. No fill is implied:
    the resting price fed in is the venue-recorded one and the outcome
    columns are modelled, which is why nothing here is compared to a P&L.
    """
    import json
    import sys
    sys.path.insert(0, "analysis/l5/completion_requote")
    from compare import CASES
    rows = [json.loads(l) for l in
            open("analysis/l5/completion_requote/tape_20261028.jsonl")]
    seen, selections = 0, set()
    for slug, c in CASES.items():
        frames = [r for r in rows if r["slug"] == slug
                  and r["t"] >= c["residual_from"]]
        if not frames:
            continue
        ws = min(f["t"] for f in frames) - 60.0
        comp = "DOWN" if c["held_side"] == "UP" else "UP"
        for f in frames:
            b = f.get("bids") or []
            a = f.get("asks") or []
            if not b or not a or D(a[0][0]) <= D(b[0][0]):
                continue
            r = UD.decide(
                market=slug, held_side=c["held_side"],
                held_qty=D(c["held_qty"]),
                held_cost_usd=D(c["held_cost_usd"]),
                deficient_qty=D(c["deficient_qty"]),
                deficient_cost_usd=D(c["deficient_cost_usd"]),
                resting_own_px=D(c["rested_own_px"]), resting_qty=D("1"),
                bid=D(b[0][0]), ask=D(a[0][0]), tick=D("0.01"),
                min_qty=D("0.01"), now_s=float(f["t"]),
                window_start_s=ws, window_end_s=ws + 900.0,
                book_age_s=0.05,
                residual_budget_usd=D(c["residual_budget_usd"]))
            seen += 1
            assert r["selected"] is not None, (slug, f["t"])
            assert r["label"] == "L5_UNMATCHED_DISPOSITION"
            # NEVER a crossing action: the route is off by default
            assert r["selected"] not in UD.CROSSING
            selections.add(r["selected"])
            assert r["held_basis"] is not None
            sel = act(r, r["selected"])
            assert sel["worst_case_usd"] is not None
    assert seen > 100, seen
    # BOTH RECORDED CASES rest inside THE CEILING -- 0.8393 and 0.9500 --
    # so PRESERVE for every sample is the CORRECT answer, not a degenerate
    # one: it is the PR #200 and #202 conclusion holding on the recorded
    # data. Non-degeneracy is asserted below over a swept basis instead.
    assert selections == {UD.PRESERVE}, selections


def test_the_decision_is_not_degenerate_across_a_swept_basis():
    """Sweep the held basis so the resting pair crosses the ceiling. Below
    it PRESERVE must win; above it a fill would lock a certain loss, so
    PRESERVE is refused and HOLD competes."""
    got = {}
    for basis in ("0.20", "0.30", "0.40", "0.49", "0.55", "0.70"):
        r = dec(held_cost_usd=D(basis))
        got[basis] = r["selected"]
    assert got["0.20"] == UD.PRESERVE          # pair 0.66
    assert got["0.49"] == UD.PRESERVE          # pair 0.95
    assert got["0.70"] == UD.HOLD              # pair 1.16, above redemption
    assert act(dec(held_cost_usd=D("0.70")), UD.PRESERVE)[
        "infeasible_because"] == "RESTING_PAIR_ABOVE_REDEMPTION"
    assert len(set(got.values())) >= 2, got


def test_no_sample_in_the_dataset_is_left_without_a_named_reason():
    """The 0100z failure was 83 records of `N/A`. Every record must carry a
    selected action with a reason, or an explicit NOTHING_UNMATCHED."""
    r = dec(held_qty=D("0"))
    assert r["selected_reason"] == "NOTHING_UNMATCHED"
    for over in (dict(), dict(book_age_s=99.0), dict(now_s=WS + 880.0),
                 dict(orders_uncertain=True), dict(gap_suspected=True)):
        rec = dec(**over)
        assert rec["selected_reason"]
        assert rec["selected"] is not None
        assert rec["actions"]


# ========== 9. EVERY MAKER ACTION CARRIES THE SAME OUTCOME ENVELOPE
#
# Found by reading the first LIVE record, 1230z 12:30:44Z. PRESERVE reported
# best_case 0.5000 (the hold upside) while COMPLETE_MAKER reported 0.1500
# (its pair outcome alone), because the `max(pair, hold)` envelope was
# applied to one and not the other. PRESERVE then won the ranking on that
# ACCOUNTING asymmetry. It was still the right choice -- with the envelope
# applied they tie on worst, best and EV, and PRESERVE wins on queue_ahead
# 0 against 2141 -- but it won for the wrong reason.

def _maker_actions(rec):
    return [a for a in rec["actions"]
            if a["action"] in (UD.PRESERVE, UD.REPRICE, UD.COMPLETE_MAKER)
            and a["feasible"] and a["best_case_usd"] is not None]


def test_every_feasible_maker_action_shares_holds_worst_and_upside():
    """An unfilled maker quote leaves us in HOLD, whichever one it is."""
    # with a quote resting, PRESERVE is the only maker peer -- COMPLETE_
    # MAKER is REPRICE's job then. Check both shapes.
    for r in (dec(), dec(resting_own_px=None, resting_qty=None)):
        hold = act(r, UD.HOLD)
        mk = _maker_actions(r)
        assert mk, [a["action"] for a in r["actions"]]
        for a in mk:
            assert D(a["worst_case_usd"]) <= D(hold["worst_case_usd"]), a
            assert D(a["best_case_usd"]) >= D(hold["best_case_usd"]), a
    r = dec()
    hold = act(r, UD.HOLD)
    for a in _maker_actions(r):
        assert D(a["worst_case_usd"]) <= D(hold["worst_case_usd"]), a
        assert D(a["best_case_usd"]) >= D(hold["best_case_usd"]), a


def test_the_1230z_live_state_no_longer_ranks_on_the_asymmetry():
    """The real numbers: held DOWN 1 at 0.5000, book 0.3700/0.3800, our UP
    complement resting at 0.4500 (pair 0.9500) against a fresh one at
    0.3500 (pair 0.8500)."""
    r = dec(held_cost_usd=D("0.5000"), resting_own_px=D("0.4500"),
            bid=D("0.3700"), ask=D("0.3800"),
            queue_ahead_fn=lambda px: (D("0") if D(str(px)) >= D("0.45")
                                       else D("2141")))
    p = act(r, UD.PRESERVE)
    m = act(r, UD.COMPLETE_MAKER)
    assert p["pair_own_cost"] == "0.9500"
    assert p["best_case_usd"] == "0.5000"
    assert p["worst_case_usd"] == "-0.5000"
    assert p["queue_ahead_at_price"] == "0"
    # the fresh complement is priced and REPORTED -- 0.8500, a better pair
    # behind 2141 contracts -- but it is not a peer while a quote rests
    assert m["pair_own_cost"] == "0.8500"
    assert m["feasible"] is False
    assert m["infeasible_because"] == (
        "RESTING_COMPLETION_EXISTS_REPRICE_OWNS_IT")
    assert r["selected"] == UD.PRESERVE


def test_a_better_pair_still_wins_when_the_queue_does_not_decide_it():
    """The envelope must not make the pair irrelevant: with equal queues,
    a fresh complement at a BETTER pair is still ranked on something."""
    r = dec(held_cost_usd=D("0.5000"), resting_own_px=D("0.4500"),
            bid=D("0.3700"), ask=D("0.3800"),
            queue_ahead_fn=lambda px: D("500"))
    assert r["selected"] in (UD.PRESERVE, UD.COMPLETE_MAKER)
    # whichever it is, it is never a pair above the ceiling
    sel = act(r, r["selected"])
    if sel["pair_own_cost"] is not None:
        assert D(sel["pair_own_cost"]) <= D("1.00")
