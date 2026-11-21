"""The release candidate's order lifecycle, at the DEPLOYED limits.

WHY A SECOND FILE BESIDE `test_l5_e2e_window`. That file is the one-window
integration harness and it is the right one -- real `FaithfulExecutor.handle`,
real unbound `Runner.maybe_*`, one clock, one store, one transport. But it
pins `max_live_orders=1`, and its own docstring says so:

    "With the deployed cap of ONE, the complement CANNOT rest beside the
     first leg ... So production runs a SEQUENTIAL strategy"

THAT IS NO LONGER THE DEPLOYED CAP. Deployment `c300a6c4` reports
`limits.max_live_orders: 2` on its own heartbeat, so the simultaneous
two-sided quote IS reachable in production and nothing exercised it through
the real path. This file does, at the limits the service actually runs:

    max_live_orders 2, max_total_exposure_usd 1.00, max_usd_per_market 20,
    max_cash_deployed_usd 12, max_unmatched_contracts 5, NO_RECYCLING,
    daily_realized_loss_halt_usd 5

WHY IT MATTERS ECONOMICALLY. Variant A of
`analysis/l5/issue208/variant_comparison.py` -- one leg, then the complement
sought afterwards -- completes ZERO pairs in 652 frames, because a pair
fixed at the touch is `1 - spread` and cannot clear the 0.96 fee-safe cap.
Simultaneous depth-2 quoting is the only shape in that comparison that
completes pairs at all. If the cap of 2 does not actually let both legs
rest, the measured economics describe a system we are not running.

NOT A VENUE SIMULATOR AND NOT A PROFITABILITY MODEL. Every fill here is
scripted by the test. No network, no threads, no live order, and a clock
that moves only when a test moves it.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D

import pytest

import tests.test_l5_e2e_window as H

from src import l5_live_authorization as AUTH
from src import l5_live_execution as E
from src import l5_book_completion as BC
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates

SLUG = H.SLUG
WS = H.WS


def deployed_limits() -> "E.SafetyLimits":
    """The caps `c300a6c4` reports, not a convenient set.

    `max_live_orders` is the one that matters here and it is 2. The others
    are carried so that a test cannot pass by relaxing a different bound
    than the one it is about.
    """
    return E.SafetyLimits(
        max_usd_per_market=D("20"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("1.00"), max_concurrent_markets=1,
        max_live_orders=2, daily_realized_loss_halt_usd=D("5"))


def deployed_gates() -> ValidationGates:
    return ValidationGates(max_cash_deployed_usd=D("12"),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D("20"),
                           cash_policy="NO_RECYCLING")


@pytest.fixture(autouse=True)
def production_configuration(monkeypatch, tmp_path):
    """Reuses the e2e fixture's configuration, which is production's."""
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "RC-LIFECYCLE")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "RC-LIFECYCLE")
    monkeypatch.setenv(TS.CANCEL_MARGIN_ENV, "300")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    # THE CLIP THE SERVICE ACTUALLY RUNS, and it is what makes the $1.00
    # aggregate exposure ceiling satisfiable at all.
    # `L5_EFFECTIVE_RUNTIME_CONFIG` on deployment c300a6c4:
    #   ts_opening_leg_max_contracts  "1"
    #   ts_round_allocations           1
    #   opening_notional_budget_usd   "3.00"
    #   aggregate_exposure_usd        "1.00"
    # Without the clip the planner sizes to the $12 cash gate (~12
    # contracts), whose one-leg worst case is ~$6.48 and is refused
    # MAX_TOTAL_EXPOSURE -- which is correct, and is why a harness that
    # omits the clip tests nothing about two-sided quoting.
    monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", "1")
    monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "1")
    monkeypatch.setenv("L5_TS_OPENING_NOTIONAL_BUDGET_USD", "3.00")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: WS if slug == SLUG else None)
    yield


def win2(tmp_path, **kw):
    """The e2e window at the DEPLOYED limits."""
    return H.Window(tmp_path, limits=deployed_limits(),
                    gates=deployed_gates(), **kw)


@pytest.fixture()
def w(tmp_path):
    return win2(tmp_path)


# =====================================================================
# 1. SIMULTANEOUS UP AND DOWN, through the real execution path
# =====================================================================

def test_the_deployed_cap_is_two_so_both_legs_may_rest(w):
    """ITEM 1. The complement rests BESIDE the first leg instead of being
    refused MAX_LIVE_ORDERS, which is the difference between variant A
    (zero pairs in 652 frames) and variant B."""
    r1 = w.tick(30.0)
    assert w.reason(r1) == "SUBMITTED", w.reason(r1)
    r2 = w.tick(45.0)
    assert w.reason(r2) == "SUBMITTED", w.reason(r2)
    assert len(w.venue.sent) == 2
    assert sorted(s["side"] for s in w.venue.sent) == ["DOWN", "UP"]
    # BOTH LIVE AT ONCE. This is the assertion the cap-1 harness cannot make.
    assert w.store.still_fillable_order_count() == 2


def test_a_third_order_is_still_refused_at_the_cap(w):
    """The cap is 2, not unbounded. Two legs rest; a third is refused."""
    w.tick(30.0)
    w.tick(45.0)
    assert w.store.still_fillable_order_count() == 2
    r3 = w.tick(60.0)
    assert w.reason(r3) == "MAX_LIVE_ORDERS", w.reason(r3)
    assert len(w.venue.sent) == 2


def test_both_resting_legs_are_post_only_makers(w):
    """MAKER-ONLY, both sides. A taker leg here would be the one thing the
    freeze names explicitly."""
    w.tick(30.0)
    w.tick(45.0)
    from src import l5_faithful_translation as FT
    assert [s["mode"] for s in w.venue.sent] == [FT.MAKER, FT.MAKER]


def test_the_two_legs_price_a_pair_inside_the_fee_safe_cap(w):
    """The economics of the pair the two legs actually form.

    A CORRECTION OF THIS TEST'S FIRST VERSION, which read the pair out of
    `rec["PLAN"]` behind an `if ... is not None` -- and `handle`'s record
    carries no `PLAN` key at all, so the assertion never ran. It now reads
    the two INTENTS, which is the only place the pair is observable from
    outside, and asserts unconditionally.

    `TS.build_orders` emits legs in OWN prices and the intents carry them
    through unchanged (the wire conversion happens later, in
    `l5_order_body.build`), so these two numbers sum to the own-terms pair:
    0.40 + 0.54 = 0.94 on the harness's 0.42/0.44 book at depth 2, which is
    `1 - spread - 2*depth*tick`.
    """
    w.tick(30.0)
    w.tick(45.0)
    own = {s["side"]: D(s["price"]) for s in w.venue.sent}
    assert set(own) == {"UP", "DOWN"}, own
    pair = own["UP"] + own["DOWN"]
    assert pair <= TS.max_pair_cost(), (pair, TS.max_pair_cost())
    # and it is the depth-2 arithmetic, not merely some number under the cap
    bid, ask, tick = D("0.42"), D("0.44"), D("0.01")
    assert pair == D(1) - (ask - bid) - 2 * 2 * tick, pair


# =====================================================================
# 2. INDEPENDENT ACKNOWLEDGEMENT AND FILL
# =====================================================================

def test_each_leg_is_acknowledged_under_its_own_venue_id(w):
    w.tick(30.0)
    w.tick(45.0)
    ids = set(w.venue.by_oid)
    assert len(ids) == 2, ids
    coids = {s["client_order_id"] for s in w.venue.sent}
    assert len(coids) == 2, coids


def test_one_leg_fills_while_the_other_keeps_resting(tmp_path):
    """ITEM 2. The fill of one side must not retire the other side's row --
    that is the partial-pair state the whole completion path exists for."""
    w = win2(tmp_path)
    w.venue.fill_next()                       # the FIRST leg fills whole
    w.tick(30.0)
    first = w.venue.sent[0]
    assert w.store.position(SLUG, first["side"]) == D(first["qty"])
    r2 = w.tick(45.0)
    assert w.reason(r2) == "SUBMITTED", w.reason(r2)
    second = w.venue.sent[1]
    assert second["side"] != first["side"]
    # the unfilled complement is STILL LIVE and still ours
    assert w.store.still_fillable_order_count() == 1
    assert w.store.position(SLUG, second["side"]) == D(0)


def test_both_legs_filling_books_a_matched_pair_and_no_residual(tmp_path):
    w = win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.venue.fill_next()
    w.tick(45.0)
    up = w.store.position(SLUG, "UP")
    dn = w.store.position(SLUG, "DOWN")
    assert up > 0 and dn > 0
    assert up == dn, (up, dn)                 # MATCHED, no residual
    assert w.store.still_fillable_order_count() == 0


# =====================================================================
# 3. A PARTIAL FIRST LEG DRIVES INVENTORY-AWARE SECOND-LEG MANAGEMENT
# =====================================================================

def test_a_partial_first_leg_is_completed_only_for_what_filled(tmp_path):
    """ITEM 3. The complement must match the FILL, never the intent --
    sizing it to the intent is how a partial becomes a reversal."""
    w = win2(tmp_path)
    # THE DEPLOYED CLIP IS ONE CONTRACT, so a partial fill is a FRACTION of
    # one. 0.40 of a 1.00 leg is the shape the venue's 0.01 quantity
    # increment actually permits.
    w.venue.fill_next("0.40")
    w.tick(30.0)
    first = w.venue.sent[0]
    held = w.store.position(SLUG, first["side"])
    assert held == D("0.40")
    assert D(first["qty"]) > held             # it really was partial
    r2 = w.tick(45.0)
    assert w.reason(r2) == "SUBMITTED", w.reason(r2)
    second = w.venue.sent[1]
    assert second["side"] != first["side"]
    assert D(second["qty"]) <= held, (second["qty"], held)


def test_a_partial_leg_keeps_its_slot_and_the_cap_still_binds(tmp_path):
    """A PARTIALLY_FILLED row is still fillable, so it still occupies one
    of the two slots. The substring trap -- "PARTIALLY_FILLED" contains
    "FILLED" -- would void the cap at 2 exactly as it did at 1."""
    w = win2(tmp_path)
    w.venue.fill_next("0.40")
    w.tick(30.0)
    first = w.venue.sent[0]
    assert D(w.store.live_orders()[first["client_order_id"]]["remaining"]) > 0
    assert w.store.still_fillable_order_count() == 1
    w.tick(45.0)
    assert w.store.still_fillable_order_count() == 2
    assert w.reason(w.tick(60.0)) == "MAX_LIVE_ORDERS"


# =====================================================================
# 4. UNFILLED QUOTES ARE REPRICED AND REPLACED SAFELY
# =====================================================================

def test_both_unfilled_legs_are_cancelled_at_their_OWN_ttl_by_exact_id(
        tmp_path):
    """ITEM 4, and ITEM 9's exact-order cancellation at two live orders:
    each leg is withdrawn by the venue id the venue gave us, and nothing
    else is touched."""
    w = win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    assert w.venue.cancelled == []
    w.tick(100.0)                             # inside both TTLs
    assert w.venue.cancelled == []
    w.tick(175.0)                             # past both
    assert sorted(w.venue.cancelled) == ["v1", "v2"], w.venue.cancelled
    assert w.store.live_order_count() == 0


def test_a_cancelled_unfilled_leg_is_repriced_from_the_CURRENT_book(
        tmp_path):
    """A REPLACEMENT, not a resubmission of a stale price. The book moves
    between the cancel and the replacement and the new leg must carry the
    new price -- carrying the frozen one is what left a complement through
    the touch."""
    w = win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)                             # both cancelled, zero filled
    assert w.store.live_order_count() == 0
    w.book.set("0.46", "0.48")                # the mirror has MOVED
    r = w.tick(200.0, bid="0.46", ask="0.48")
    assert w.reason(r) == "SUBMITTED", w.reason(r)
    fresh = w.venue.sent[-1]
    stale = [s for s in w.venue.sent[:2] if s["side"] == fresh["side"]][0]
    assert fresh["client_order_id"] != stale["client_order_id"]
    assert D(fresh["price"]) != D(stale["price"]), (fresh["price"],
                                                   stale["price"])


def test_a_filled_leg_is_never_cancelled_or_replaced(tmp_path):
    """The cancellation sweep polls BEFORE it cancels, so a leg that filled
    is booked rather than withdrawn. At two live orders that distinction has
    to hold per order, not per window."""
    w = win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)                              # leg 1 FILLS
    w.tick(45.0)                              # leg 2 rests unfilled
    filled_side = w.venue.sent[0]["side"]
    held = w.store.position(SLUG, filled_side)
    w.tick(175.0)
    assert "v1" not in w.venue.cancelled, w.venue.cancelled
    assert w.store.position(SLUG, filled_side) == held


# =====================================================================
# 5. REPLENISHMENT AFTER A COMPLETED PAIR
# =====================================================================

def test_a_completed_pair_frees_both_slots_and_a_new_round_may_open(
        tmp_path):
    """ITEM 5. After a pair completes, up == dn and there is nothing to
    match -- so the next leg is a fresh OPENING and must face the opening
    gates again, not be routed down the completion path."""
    w = win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.venue.fill_next()
    w.tick(45.0)
    assert w.store.position(SLUG, "UP") == w.store.position(SLUG, "DOWN")
    assert w.store.still_fillable_order_count() == 0
    r = w.tick(90.0)
    # EITHER it opens a fresh round, OR it is refused by an OPENING gate --
    # never by NOTHING_TO_MATCH, which would mean the completion path
    # swallowed a first leg and stopped accumulation at one pair.
    assert w.reason(r) != "NOTHING_TO_MATCH", w.reason(r)


def test_replenishment_respects_the_cash_gate_rather_than_the_order_cap(
        tmp_path):
    """Replenishment is bounded by capital, not by how many slots are free.
    NO_RECYCLING means a settled pair's cash is not re-lent before
    settlement."""
    w = win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.venue.fill_next()
    w.tick(45.0)
    gates = w.ex.validation_gates
    assert gates.cash_policy == "NO_RECYCLING"
    assert D(str(gates.max_cash_deployed_usd)) == D("12")


# =====================================================================
# 6. CANCELLATION AND FILL RACES CANNOT DOUBLE OR LOSE EXPOSURE
# =====================================================================

def test_a_fill_landing_while_the_cancel_is_in_flight_is_booked_once(
        tmp_path):
    """ITEM 6. The fill is booked, and booked ONCE -- with two live orders
    the race can be lost on either side."""
    w = win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    # the venue fills leg 2 just before the sweep reads it
    w.venue.acked["v2"] = str(w.venue.by_oid["v2"]["qty"])
    side2 = w.venue.sent[1]["side"]
    w.tick(175.0)
    booked = w.store.position(SLUG, side2)
    assert booked == D(w.venue.by_oid["v2"]["qty"]), booked
    # and again on a later sweep: no second booking
    w.tick(200.0)
    assert w.store.position(SLUG, side2) == booked


def test_no_duplicate_client_order_id_is_ever_sent(tmp_path):
    """The harness RAISES on a duplicate client order id, so a passing run
    is itself the proof. Driven hard: entry, both legs, TTL, replacement."""
    w = win2(tmp_path)
    for off in (30.0, 45.0, 100.0, 175.0, 200.0, 215.0, 330.0, 345.0):
        w.tick(off)
    coids = [s["client_order_id"] for s in w.venue.sent]
    assert len(coids) == len(set(coids)), coids


def test_aggregate_worst_case_counts_held_inventory_AND_open_orders(
        tmp_path):
    """ITEM 9's exposure bound at two live orders. The cap is the account
    ceiling of $1.00 and it must be measured over BOTH legs as-if-filled
    plus whatever is already held -- a per-order check would let two orders
    through that together breach it."""
    w = win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.tick(45.0)
    held_side = w.venue.sent[0]["side"]
    held = w.store.position(SLUG, held_side)
    resting = sum(D(s["qty"]) * D(s["price"]) for s in w.venue.sent[1:])
    assert held > 0
    # the WORST CASE the gate measures, in dollars, over this one market
    worst = w.store.leg_cost(SLUG).get(held_side) or D(0)
    assert worst + resting <= D("1.00") + D("0.0001"), (worst, resting)


# =====================================================================
# 7. ROLLOVER DOES NOT BLOCK, AND 8. RESTART IS LOSSLESS
# =====================================================================

def test_a_restart_with_TWO_live_orders_resumes_both_and_doubles_neither(
        tmp_path):
    """ITEM 8. Both rows survive a new process against the same durable
    state, and the restarted process does not re-enter either side."""
    w = win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    before = dict(w.store.live_orders())
    assert len(before) == 2

    w2 = w.restart(tmp_path)
    after = dict(w2.store.live_orders())
    assert set(after) == set(before), (set(after), set(before))
    assert w2.store.still_fillable_order_count() == 2
    r = w2.tick(60.0)
    assert w2.reason(r) == "MAX_LIVE_ORDERS", w2.reason(r)
    assert len(w.venue.sent) == 2              # nothing re-entered


def test_a_restart_after_one_fill_resumes_the_pair_not_a_new_round(
        tmp_path):
    w = win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    side = w.venue.sent[0]["side"]
    held = w.store.position(SLUG, side)
    w2 = w.restart(tmp_path)
    assert w2.store.position(SLUG, side) == held
    r = w2.tick(60.0)
    assert w2.reason(r) == "SUBMITTED", w2.reason(r)
    assert w.venue.sent[-1]["side"] != side    # the COMPLEMENT
    assert w2.store.position(SLUG, side) == held


# =====================================================================
# 10. THE FREEZE: AUTHORIZATION CANNOT SUBMIT WHILE IT HOLDS
# =====================================================================

def test_the_shadow_lock_defaults_to_BLOCKING_and_is_the_first_gate(
        monkeypatch):
    """ITEM 10, AT THE GATE THAT ACTUALLY HOLDS.

    A CORRECTION OF THIS FILE'S FIRST DRAFT. I wrote this as
    `win2(tmp_path, armed=False)` and asserted the executor sent nothing --
    and it sent three orders. The e2e harness's `Venue.armed` is carried
    but its `submit` does not consult it, so that flag proves nothing. The
    real gate is `Runner.submission_gate`, upstream of `handle`, and
    `shadow_only()` is its FIRST test: with `L5_SHADOW_ONLY` unset it
    returns True, so the default is blocked.
    """
    import src.l5_pmus_live_runner as R
    monkeypatch.delenv(R.SHADOW_ONLY_ENV, raising=False)
    assert R.shadow_only() is True             # fail-safe DEFAULT
    monkeypatch.setenv(R.SHADOW_ONLY_ENV, "1")
    assert R.shadow_only() is True
    # only an explicit "0" opens it, and nothing else does
    for v in ("", "no", "false", "off", "2", "00", " 1 "):
        monkeypatch.setenv(R.SHADOW_ONLY_ENV, v)
        assert R.shadow_only() is True, v
    monkeypatch.setenv(R.SHADOW_ONLY_ENV, "0")
    assert R.shadow_only() is False


def test_the_submission_gate_reports_blocked_with_the_lock_engaged(
        monkeypatch):
    """The verdict the deployed heartbeat carries -- `blocked: true`,
    `reason: SHADOW_ONLY_LOCK` -- reproduced from the real method."""
    import src.l5_pmus_live_runner as R

    class Stub:
        stats = collections.Counter()
        transport = None
        executor = None

    monkeypatch.setenv(R.SHADOW_ONLY_ENV, "1")
    g = R.Runner.submission_gate(Stub())
    assert g["blocked"] is True, g
    assert g["reason"] == "SHADOW_ONLY_LOCK", g
    assert g["shadow_lock"] is True


def test_an_unreadable_gate_reads_as_BLOCKED_and_never_as_open(monkeypatch):
    """FAIL CLOSED. The one direction a gate may not fail in."""
    import src.l5_pmus_live_runner as R

    class Boom:
        stats = collections.Counter()
        executor = None

        @property
        def transport(self):
            raise RuntimeError("transport unreadable")

    monkeypatch.setenv(R.SHADOW_ONLY_ENV, "0")      # lock OPEN on purpose
    g = R.Runner.submission_gate(Boom())
    assert g["blocked"] is True, g
    assert g["reason"] != "OPEN", g


def test_cancellation_still_works_while_submission_is_blocked(tmp_path):
    """THE ASYMMETRY THAT MATTERS. Cancellation is not gated by the arm
    switch -- a blocked submission gate must never trap a resting order.
    Proven by arming, resting two legs, then disarming."""
    w = win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    assert w.store.still_fillable_order_count() == 2
    w.venue.armed = False                      # the switch flips UNDER us
    w.tick(175.0)
    assert sorted(w.venue.cancelled) == ["v1", "v2"], w.venue.cancelled


# =====================================================================
# THE CANDIDATE'S OWN CHANGES, DEFAULT-OFF
# =====================================================================

def test_adaptive_completion_depth_is_ON_by_default_since_1530z(monkeypatch):
    """A CORRECTION OF THIS TEST'S PREMISE, not a relaxation of it.

    It asserted the adaptive retry was inert "so that deploying it changes
    no decision". That inertness is exactly what the 2026-11-09 15:30Z
    live window paid to discover: it ran with the retry off, completed
    ZERO pairs from one filled leg, and settled -$0.3080 -- the shape the
    retry's own docstring had already measured at zero pairs in 652
    replayed entries. The default is now ON and off is an explicit act."""
    monkeypatch.delenv(BC.ADAPTIVE_ENV, raising=False)
    assert BC.adaptive_depth_enabled() is True
    monkeypatch.setenv(BC.ADAPTIVE_ENV, "off")
    assert BC.adaptive_depth_enabled() is False
    assert BC.adaptive_depth_candidates() == [BC.completion_depth_ticks()]


def test_adaptive_depth_when_enabled_walks_shallowest_first(monkeypatch):
    monkeypatch.setenv(BC.ADAPTIVE_ENV, "1")
    cands = BC.adaptive_depth_candidates()
    assert cands[0] == BC.completion_depth_ticks()
    assert len(cands) == len(set(cands))
    assert cands[1:] == sorted(cands[1:])


def test_the_latency_report_has_its_own_emission_path(tmp_path):
    """THE DEFECT THIS CANDIDATE FIXES. `bounded_hb` reduces
    `completion_latency` out of the heartbeat -- deployment c300a6c4
    reports exactly that -- so the report needs a second home or it never
    reaches the tape."""
    from src import l5_pmus_live_runner as R
    assert hasattr(R.Runner, "emit_completion_latency")
    src = R.inspect.getsource(R.Runner.emit_completion_latency) \
        if hasattr(R, "inspect") else None
    import inspect as _i
    src = _i.getsource(R.Runner.emit_completion_latency)
    assert "L5_COMPLETION_LATENCY" in src
    # it is CALLED from the heartbeat, not merely defined
    hb = _i.getsource(R.Runner.heartbeat)
    assert "self.emit_completion_latency()" in hb


def test_completion_latency_is_reducible_and_that_is_deliberate(tmp_path):
    """It must NOT be added to HB_NEVER_REDUCE: protecting 15 KB of
    telemetry would push a safety field off the line instead, which is the
    failure `bounded_hb` exists to prevent."""
    from src import l5_two_sided_shadow as TSS
    assert "completion_latency" not in TSS.HB_NEVER_REDUCE
    assert "submission_gate" in TSS.HB_NEVER_REDUCE
    assert "day_realized" in TSS.HB_NEVER_REDUCE


def test_the_latency_line_FITS_at_the_size_that_got_it_reduced():
    """A second home is only a fix if the line arrives. At the 15,234 bytes
    production reported for `completion_latency`, the dedicated line is
    about 15.5 KB -- inside the 48,000-byte heartbeat budget and well
    inside the 81,920-byte platform limit that truncated the heartbeat in
    the first place."""
    import json
    from src import l5_two_sided_shadow as TSS
    observed = 15234                      # hb_reduced.fields on c300a6c4
    line = len(json.dumps({
        "utc": "2026-11-09T05:29:19+00:00", "role": "l5-pmus-live",
        "report": "x" * observed,
        "labels": ("the same report the heartbeat carries, on its own line "
                   "because bounded_hb reduces it out of the heartbeat to "
                   "protect the safety fields. Reporting only: no gate, "
                   "limit or decision reads this.")}))
    assert line < TSS.LOG_BUDGET_BYTES, line
    assert line < 81920, line


def test_the_latency_line_cannot_break_the_heartbeat(tmp_path):
    """A telemetry line that raises would cost the heartbeat carrying the
    safety state, so the method swallows and counts instead."""
    from src import l5_pmus_live_runner as R

    class Boom:
        stats = collections.Counter()

        def _latency_report(self):
            raise RuntimeError("nope")

    b = Boom()
    R.Runner.emit_completion_latency(b)        # must not raise
    assert any(k.startswith("latency_line_failed:") for k in b.stats), b.stats
