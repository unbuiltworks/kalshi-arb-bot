"""The completion gate must not refuse a non-negative pair, and a completion
must be attributed to whoever actually opened the inventory.

EVERY NUMBER HERE IS FROM THE 2026-10-30 THREE-WINDOW CAMPAIGN. The books,
bases, quantities and client order ids are the recorded ones, so a regression
is measured against what the venue actually did rather than against a
construction chosen to pass.

  1415z  5 pairs at 0.98 / 1.01 / 1.01 / 0.92 / 1.02, settled +$0.102400
  1430z  2 pairs at 0.97 / 1.00,                      settled +$0.061800
  1445z  1.91 DOWN at own 0.37 on a book 1 tick wide (bid 0.6200/ask 0.6300),
         complement posted at own 0.6000 -- two ticks behind the bid --
         cancelled unfilled, leg stranded to expiry for -$0.706700.

A matched 1.91-contract pair at 0.99 costs $1.8909 and redeems $1.9100, so it
is worth +$0.0191. That is a HYPOTHETICAL maker fill at a fillable price and
never an executed result: these tests assert what the engine is ALLOWED to
submit, and nothing here asserts that a resting order fills.
"""
from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS

M1415 = "cpc-btc-updown-15m-2026-10-30-1415z"
M1430 = "cpc-btc-updown-15m-2026-10-30-1430z"
M1445 = "cpc-btc-updown-15m-2026-10-30-1445z"

WS = 1793371500.0                 # 14:45:00Z, the 1445z window start
NOW = WS + 170.0                  # just after the recorded 14:47:48 fill

#: the recorded 1445z book: one tick wide for 205 of 210 two-sided ticks
BOOK_1TICK = {"bid": "0.6200", "ask": "0.6300", "stream_age_s": "0.007",
              "bid_shares": "50", "ask_shares": "50"}

#: the three client order ids 1445z actually submitted, and their keys
W3_COIDS = {
    "l5f-312b00f86c8bd73712299990": ("r0", "UP"),    # cancelled, cum 0
    "l5f-ac9c9293a6e0dc62f12dac15": ("r1", "DOWN"),  # FILLED 1.91 @ own 0.37
    "l5f-cc1796871fa15ddc3b8b2901": ("r1", "UP"),    # cancelled, cum 0
}


def decide(**kw):
    """1445z's held inventory, with only the named fields overridden."""
    base = dict(
        market=M1445, held_side="DOWN",
        held_qty=D("1.91"), held_cost_usd=D("0.7067"),
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        book=dict(BOOK_1TICK), now_t=NOW, window_start=WS,
        resting=(), tick="0.01", min_qty="0.01",
        depth_ticks=0, residual_budget_usd=D("3"),
        submissions_left=5, last_decide_t=None)
    base.update(kw)
    return BC.decide(**base)


# ---------------------------------------------------------------- the defect

def test_1445z_completion_at_the_bid_is_now_permitted():
    """The recorded window, at the recorded book. This refused before."""
    rec = decide()
    assert rec["ok"] is True, rec
    assert rec["action"] == "COMPLETE"
    assert rec["reason"] == "RISK_REDUCING_COMPLETION"
    assert rec["pair_own_cost"] == "0.9900"
    leg = rec["leg"]
    assert leg["side"] == "UP"
    assert leg["price"] == "0.6200"          # AT the bid, not behind it
    assert leg["post_only"] is True
    assert leg["mode"] == "MAKER"


def test_the_pair_at_0_99_is_worth_plus_one_and_ninety_one_hundredths_of_a_cent():
    """+$0.0191, NOT -$0.0191. A pair below $1.00 is a gain, not a loss."""
    rec = decide()
    assert D(rec["locked_pnl_usd"]) == D("0.0191")
    assert D(rec["locked_pnl_usd"]) > 0
    # the arithmetic, independently: cost vs redemption
    cost = D("1.91") * D("0.99")
    assert cost == D("1.8909")
    assert D("1.91") - cost == D("0.0191")


def test_the_old_constant_would_have_refused_every_price_on_this_book():
    """Why nothing rested for 12 minutes: no maker price clears 0.9532."""
    assert D("0.37") + D("0.6200") == D("0.9900")   # at the bid
    assert D("0.37") + D("0.6000") == D("0.9700")   # two ticks behind
    for pair in ("0.9900", "0.9700"):
        assert D(pair) > TS.BREAKEVEN_PAIR
    # and both are non-negative outcomes the engine should be free to take
    for pair in ("0.9900", "0.9700"):
        assert D(pair) <= BC.REDEMPTION_PAIR


def test_a_stranded_leg_is_the_alternative_being_priced_against():
    """-$0.7067 was the realized cost of refusing. Settled, not modelled."""
    assert D("1.91") * D("0.37") == D("0.7067")


# ------------------------------------------------- the hard line that remains

def test_a_pair_above_redemption_is_still_refused():
    """$1.00 is arithmetic, not preference: above it the pair is a loss."""
    rec = decide(held_cost_usd=D("1.91") * D("0.40"))   # basis 0.40
    assert D(rec["pair_own_cost"]) == D("1.0200")
    assert rec["ok"] is False
    assert rec["reason"] == "PAIR_ABOVE_REDEMPTION"
    assert rec["redemption"] == "1.00"


def test_the_defensive_pairs_this_campaign_took_are_not_available_here():
    """1415z completed at 1.01/1.01/1.02 through the DEFENSIVE mandate, whose
    ceiling is 1.20. This module must not become a second author of that."""
    assert DEF_CEILING > BC.REDEMPTION_PAIR
    for basis_px in ("0.39", "0.40"):            # pairs 1.01 and 1.02
        rec = decide(held_cost_usd=D("1.91") * D(basis_px))
        assert rec["ok"] is False
        assert rec["reason"] == "PAIR_ABOVE_REDEMPTION"


def test_exactly_at_redemption_is_permitted_and_locks_zero():
    """1430z's second pair settled at exactly 1.00 for $0.0000."""
    rec = decide(held_cost_usd=D("1.91") * D("0.38"))
    assert D(rec["pair_own_cost"]) == D("1.0000")
    assert rec["ok"] is True
    assert D(rec["locked_pnl_usd"]) == 0


# -------------------------------------------- the preference, still recorded

def test_the_cheap_pairs_still_take_the_preferred_reason():
    """1415z's 0.92 and 1430z's 0.97 cleared the old gate and must be
    unchanged: same action, same reason, flagged within target."""
    for basis_px, pair in (("0.30", "0.9200"), ("0.35", "0.9700")):
        rec = decide(held_cost_usd=D("1.91") * D(basis_px))
        assert D(rec["pair_own_cost"]) == D(pair), rec
        assert rec["ok"] is True
        if D(pair) <= TS.BREAKEVEN_PAIR:
            assert rec["reason"] == "PAIR_BEATS_CARRY"
            assert rec["within_preferred_target"] is True


def test_the_target_survives_as_a_preference_not_a_gate():
    rec = decide()
    assert rec["breakeven_pair"] == "0.9532"
    assert rec["within_preferred_target"] is False
    assert rec["ok"] is True          # recorded, and NOT a refusal


def test_the_breakeven_number_still_has_one_source():
    assert BC.BREAKEVEN_PAIR is TS.BREAKEVEN_PAIR
    assert TS.BREAKEVEN_PAIR == D("0.9532")


def test_the_opening_cap_is_untouched():
    """0.96 still governs OPENING a pair. This path only completes."""
    assert TS.max_pair_cost() == D("0.96")
    assert TS.MAX_PAIR_COST == D("0.96")


# ------------------------------------------------- completion depth is its own

def test_completion_depth_defaults_to_the_touch(monkeypatch):
    monkeypatch.delenv(BC.COMPLETION_DEPTH_TICKS_ENV, raising=False)
    assert BC.completion_depth_ticks() == 0


def test_completion_depth_does_not_inherit_the_opening_probe(monkeypatch):
    """The 1445z defect: the opening depth of 2 priced the complement."""
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    monkeypatch.delenv(BC.COMPLETION_DEPTH_TICKS_ENV, raising=False)
    assert BC.completion_depth_ticks() == 0


def test_completion_depth_is_overridable_and_fails_to_its_own_default(
        monkeypatch):
    monkeypatch.setenv(BC.COMPLETION_DEPTH_TICKS_ENV, "1")
    assert BC.completion_depth_ticks() == 1
    for bad in ("", "   ", "nonsense", "-3"):
        monkeypatch.setenv(BC.COMPLETION_DEPTH_TICKS_ENV, bad)
        assert BC.completion_depth_ticks() == 0


def test_depth_two_posts_behind_the_bid_as_recorded():
    """What actually happened: own 0.6000 into a 0.6200 bid."""
    rec = decide(depth_ticks=2)
    assert rec["leg"]["price"] == "0.6000"
    assert D(rec["pair_own_cost"]) == D("0.9700")


# ------------------------------------------- every pre-existing gate survives

def test_a_crossing_price_is_still_refused():
    rec = decide(book=dict(BOOK_1TICK, bid="0.6200", ask="0.6250"),
                 depth_ticks=-0)
    if rec["ok"]:
        own = D(rec["leg"]["price"])
        assert own < D("0.6250")          # never takes the ask


def test_a_stale_book_still_refuses():
    rec = decide(book=dict(BOOK_1TICK, stream_age_s="99"))
    assert rec["ok"] is False
    assert rec["reason"] == "STALE_BOOK"


def test_an_unknown_book_age_still_refuses():
    b = dict(BOOK_1TICK)
    b.pop("stream_age_s")
    rec = decide(book=b)
    assert rec["ok"] is False
    assert rec["reason"] == "BOOK_AGE_UNKNOWN"


def test_a_suspected_gap_still_refuses():
    rec = decide(book=dict(BOOK_1TICK, gap_suspected=True))
    assert rec["ok"] is False
    assert rec["reason"] == "BOOK_GAP_SUSPECTED"


def test_expiry_still_refuses():
    """1445z stranded its leg THROUGH expiry; past terminal must not act."""
    rec = decide(now_t=WS + 721.0)
    assert rec["ok"] is False
    assert rec["reason"] == "PAST_TERMINAL"


def test_a_missing_basis_still_refuses_rather_than_assuming_one():
    """Fails closed EARLIER than the pair test, inside the economics that own
    the basis. The stale/absent-basis case never reaches a price."""
    rec = decide(held_cost_usd=None)
    assert rec["ok"] is False
    assert rec["reason"] == "ECONOMICS:FIRST_LEG_COST_UNKNOWN"


def test_no_inventory_still_refuses():
    rec = decide(held_qty=D("0"), held_cost_usd=D("0"))
    assert rec["ok"] is False
    assert rec["reason"] == "NO_INVENTORY"


def test_already_matched_still_refuses():
    rec = decide(deficient_qty=D("1.91"), deficient_cost_usd=D("0.60"))
    assert rec["ok"] is False
    assert rec["reason"] == "ALREADY_MATCHED"


def test_no_submissions_left_still_refuses():
    rec = decide(submissions_left=0)
    assert rec["ok"] is False
    assert rec["reason"] == "NO_SUBMISSIONS_LEFT"


def test_the_cadence_still_binds():
    rec = decide(last_decide_t=NOW - 1.0)
    assert rec["ok"] is False
    assert rec["reason"] == "DECIDE_COOLDOWN"


def test_a_partial_fill_completes_only_the_remainder():
    """1415z filled in clips; the complement is for what is UNMATCHED."""
    rec = decide(held_qty=D("1.91"), held_cost_usd=D("0.7067"),
                 deficient_qty=D("0.95"), deficient_cost_usd=D("0.57"))
    assert rec["ok"] is True
    assert rec["need"] == "0.96"
    assert D(rec["leg"]["qty"]) == D("0.96")


def test_a_resting_completion_at_or_better_is_not_duplicated():
    """The unfilled-complement case: one order, never two."""
    rec = decide(resting=({"side": "UP", "price": "0.6200",
                           "market": M1445},))
    assert rec["ok"] is False
    assert rec["reason"] == "ALREADY_RESTING_AT_OR_BETTER"


def test_a_dearer_resting_completion_is_repriced_downward_only():
    rec = decide(resting=({"side": "UP", "price": "0.5000",
                           "market": M1445},))
    # 0.6200 is DEARER than a resting 0.5000, so it must not chase upward
    assert rec["ok"] is False
    assert rec["reason"] == "ALREADY_RESTING_AT_OR_BETTER"


def test_the_decision_key_is_idempotent():
    a = BC.decision_key(M1445, "UP", D("1.91"), D("0.62"))
    b = BC.decision_key(M1445, "UP", D("1.91"), D("0.62"))
    assert a == b
    assert a != BC.decision_key(M1445, "UP", D("1.91"), D("0.63"))


# ----------------------------------------------- inventory-origin attribution

def _state(entries, submitted=None):
    return {"fill_provenance": {M1445: {"legs": {}, "entries": list(entries)}},
            "submitted": dict(submitted or {})}


def test_the_recorded_1445z_coids_are_all_controller_keyed():
    """Real ids from the campaign tape, attributed with no stamp at all."""
    ns = LX.controller_coid_namespace(M1445)
    for coid, (rnd, leg) in W3_COIDS.items():
        assert coid in ns, coid
        assert ns[coid].startswith(LX.CONTROLLER_KEY_PREFIX + "|")
        assert ns[coid].endswith("|%s|%s" % (rnd, leg))


def test_controller_inventory_is_attributed_to_the_controller():
    st = _state([{"coid": "l5f-ac9c9293a6e0dc62f12dac15",
                  "side": "DOWN", "qty": "1.91"}])
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_CONTROLLER


def test_the_stamp_is_preferred_over_the_namespace():
    st = _state([{"coid": "whatever", "side": "DOWN", "qty": "1.91"}],
                submitted={"whatever": {
                    "provenance": LX.CONTROLLER_GENERATED}})
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_CONTROLLER


def test_l5_inventory_is_attributed_to_l5():
    st = _state([{"coid": "l5f-deadbeefdeadbeefdeadbeef",
                  "side": "DOWN", "qty": "1.91"}],
                submitted={"l5f-deadbeefdeadbeefdeadbeef": {
                    "provenance": LX.L5_FOLLOWED}})
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_L5


def test_an_action_key_alone_attributes_without_a_provenance_field():
    st = _state([{"coid": "c1", "side": "DOWN", "qty": "1.91"}],
                submitted={"c1": {"action_key": LX.controller_action_key(
                    M1445, 1, "DOWN")}})
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_CONTROLLER


def test_unattributable_inventory_reads_unknown_and_never_guesses():
    st = _state([{"coid": "l5f-000000000000000000000000",
                  "side": "DOWN", "qty": "1.91"}])
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_UNKNOWN


def test_both_origins_present_reads_mixed():
    st = _state([{"coid": "l5f-ac9c9293a6e0dc62f12dac15",
                  "side": "DOWN", "qty": "1.00"},
                 {"coid": "x1", "side": "DOWN", "qty": "0.91"}],
                submitted={"x1": {"provenance": LX.L5_FOLLOWED}})
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_MIXED


def test_no_fills_reads_unknown():
    assert LX.inventory_origin({}, M1445) == LX.ORIGIN_UNKNOWN
    assert LX.inventory_origin(_state([]), M1445) == LX.ORIGIN_UNKNOWN


def test_attribution_is_stable_so_a_route_cannot_flap():
    """The old selector moved with the ingest feed. This moves only with
    booked fills, so the same inventory attributes the same way every tick."""
    st = _state([{"coid": "l5f-ac9c9293a6e0dc62f12dac15",
                  "side": "DOWN", "qty": "1.91"}])
    seen = {LX.inventory_origin(st, M1445) for _ in range(25)}
    assert seen == {LX.ORIGIN_CONTROLLER}


def test_attribution_is_side_selectable():
    st = _state([{"coid": "l5f-ac9c9293a6e0dc62f12dac15",
                  "side": "DOWN", "qty": "1.91"}])
    assert LX.inventory_origin(st, M1445, "DOWN") == LX.ORIGIN_CONTROLLER
    assert LX.inventory_origin(st, M1445, "UP") == LX.ORIGIN_UNKNOWN


def test_the_namespace_is_deterministic():
    a = LX.controller_coid_namespace(M1445)
    b = LX.controller_coid_namespace(M1445)
    assert a == b
    assert LX.controller_coid_namespace(M1415) != a


def test_attribution_reads_only_confirmed_fills_not_intentions():
    """A submitted-but-unfilled controller order must not attribute
    inventory: 1445z cancelled two of three orders unfilled."""
    st = {"fill_provenance": {},
          "submitted": {"l5f-cc1796871fa15ddc3b8b2901": {
              "provenance": LX.CONTROLLER_GENERATED}}}
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_UNKNOWN


def test_a_zero_quantity_entry_does_not_attribute():
    st = _state([{"coid": "l5f-ac9c9293a6e0dc62f12dac15",
                  "side": "DOWN", "qty": "0"}])
    assert LX.inventory_origin(st, M1445) == LX.ORIGIN_UNKNOWN


def test_attribution_never_raises_on_junk():
    for junk in ({}, {"fill_provenance": None},
                 {"fill_provenance": {M1445: None}},
                 {"fill_provenance": {M1445: {"entries": [None, 7, "x"]}}},
                 {"fill_provenance": {M1445: {"entries": [
                     {"coid": None, "side": "DOWN", "qty": "zzz"}]}}}):
        assert LX.inventory_origin(junk, M1445) in (
            LX.ORIGIN_CONTROLLER, LX.ORIGIN_L5,
            LX.ORIGIN_MIXED, LX.ORIGIN_UNKNOWN)


def test_record_submit_stamps_provenance_and_keeps_its_old_signature(tmp_path):
    st = LX.InventoryStore(str(tmp_path / "s.json"))
    st.record_submit("c-old", {"market": M1445, "side": "UP",
                               "price": "0.62", "qty": "1.91"})
    st.record_submit("c-new", {"market": M1445, "side": "UP",
                               "price": "0.62", "qty": "1.91"},
                     provenance=LX.CONTROLLER_GENERATED,
                     action_key=LX.controller_action_key(M1445, 1, "UP"))
    sub = st.state["submitted"]
    assert sub["c-old"]["provenance"] is None      # unchanged callers still work
    assert sub["c-new"]["provenance"] == LX.CONTROLLER_GENERATED
    assert sub["c-new"]["action_key"].startswith(LX.CONTROLLER_KEY_PREFIX)


try:
    from src.l5_defensive_completion import DEFENSIVE_PAIR_CEILING as DEF_CEILING
except Exception:                                              # noqa: BLE001
    DEF_CEILING = D("1.20")


# ----------------------------------- the route selector, at the source level

#: the RE-ENTRY branch exactly, at its own indent. `elif controller_route:`
#: contains the same text, so an unanchored search finds the wrong line.
REENTRY = "\n        if controller_route:"


class TestTheRouteFollowsTheInventoryNotTheFeed:
    """Source-level, as `maybe_book_completion` needs a live transport, an
    ingest loop and a book feed to run end to end."""

    @property
    def src(self):
        import inspect
        from src import l5_pmus_live_runner as R
        return inspect.getsource(R.Runner.maybe_book_completion)

    def test_the_route_is_decided_on_held_inventory_origin(self):
        s = self.src
        assert "LX.inventory_origin(self.store.state, slug)" in s
        assert "if origin == LX.ORIGIN_CONTROLLER:" in s
        assert "controller_route = True" in s

    def test_an_l5_row_no_longer_overrides_controller_owned_inventory(self):
        """The case the old selector could not see at all."""
        s = self.src
        assert "elif controller_route:" in s
        assert "CONTROLLER_INVENTORY_WITH_L5_ROW" in s

    def test_a_missing_row_with_held_inventory_still_takes_the_controller(self):
        s = self.src
        assert 'if (_u or 0) == 0 and (_d or 0) == 0:' in s
        assert 'self.stats["book_completion:NO_ACTION_YET"] += 1' in s

    def test_mixed_and_unknown_leave_the_previous_route_in_force(self):
        """Neither engine is credited with the other's stock."""
        s = self.src
        assert "if origin == LX.ORIGIN_CONTROLLER:" in s
        # the ONLY origin that changes the route
        assert "ORIGIN_MIXED" not in s
        assert "ORIGIN_L5" not in s

    def test_attribution_failure_falls_back_and_never_raises(self):
        s = self.src
        assert "origin = LX.ORIGIN_UNKNOWN" in s

    def test_the_origin_it_routed_on_is_recorded(self):
        s = self.src
        assert '"inventory_origin": origin,' in s
        assert '"l5_row_present": bool(row is not None),' in s
        assert 'self.stats["book_completion:ORIGIN:%s" % origin] += 1' in s

    def test_exactly_one_reentry_vehicle_runs_per_call(self):
        """The duplicate-submission question. The two vehicles are the two
        arms of ONE if/else, so a single call cannot take both."""
        s = self.src
        # the RE-ENTRY branch, anchored so the earlier `elif controller_route`
        # (which contains this as a substring) cannot match instead
        assert s.count(REENTRY) == 1
        assert s.count("self.executor.handle(") == 1
        assert s.count("self.handle(row, now)") == 1
        i = s.index(REENTRY)
        assert "else:" in s[i:]
        assert s.index("self.executor.handle(") > i
        assert s.index("self.handle(row, now)") > s.index("else:", i)

    def test_the_one_resting_order_limit_still_gates_before_any_reentry(self):
        """No second order while one rests, on either route."""
        s = self.src
        assert 'self.stats["book_completion:ORDER_ALREADY_RESTING"] += 1' in s
        assert s.index("ORDER_ALREADY_RESTING") < s.index(REENTRY)

    def test_a_refusal_returns_before_any_reentry(self):
        s = self.src
        assert 'if not rec.get("ok"):' in s
        assert s.index('if not rec.get("ok"):') < s.index(REENTRY)

    def test_the_cadence_is_stamped_even_on_a_refusal(self):
        s = self.src
        assert "self._book_completion_t = now" in s

    def test_completion_depth_comes_from_the_completion_env(self):
        s = self.src
        assert "depth_ticks=BC.completion_depth_ticks()," in s
        assert "_evidence_depth_ticks()" not in s

    def test_the_owner_budget_is_still_what_bounds_the_deficit(self):
        s = self.src
        assert "max_worst_case_deficit_usd" in s

    def test_the_campaign_submission_budget_is_still_consulted(self):
        s = self.src
        assert "submissions_left=AUTH.max_submissions()" in s

    def test_inventory_still_comes_from_the_ledger_not_from_memory(self):
        s = self.src
        assert "self.store.position(slug" in s
        assert "self.store.leg_cost(slug)" in s

    def test_the_controller_reentry_counter_is_now_reachable(self):
        s = self.src
        assert 'self.stats["book_completion:REENTERED:CONTROLLER"] += 1' in s
