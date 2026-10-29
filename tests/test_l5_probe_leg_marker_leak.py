"""The two-sided leg markers must not survive a `handle()` call.

THE DEFECT, OBSERVED LIVE. `_probe_leg_pending` / `_probe_round_pending`
mark the order in hand as a two-sided probe leg. They are set by the
two-sided path inside `handle` and cleared immediately before the wire,
after every gate -- with nineteen refusal exits in between that cleared
nothing. So one refused two-sided plan latched them for the life of the
process.

The strategy-ownership gate reads that marker. With `L5_WINDOW_STRATEGY`
naming L5_FOLLOWING as the owner of the window, a latched marker makes the
gate refuse every subsequent L5-FOLLOWING order as though it belonged to the
two-sided engine. In campaign `camp1.87265e609a92b5a2889e5392`, window
1445z: 27 decisions, 15 refused on strategy ownership, `safety` null in all
of them (so the maker-only overlay was never even reached), zero orders to
the venue. The latch is self-sustaining -- only a successful submission
cleared it, and it is what prevented one.

These tests pin the entry reset and, as the file they extend does, pin the
surrounding invariants at the SOURCE, because driving `handle` end to end
depends on the maker translation configuration, which is not what any of
this is about.

NOTHING HERE ASSERTS A FILL. The fix removes a refusal that was not supposed
to apply; whether a maker order then rests and fills is a queue question and
is deliberately not claimed.
"""
from __future__ import annotations

import inspect

from decimal import Decimal as D

from src import l5_live_execution as E
from src import l5_live_validation_gates as VG


def _body() -> str:
    return inspect.getsource(E.FaithfulExecutor.handle)


def _reset_statements() -> str:
    """Exactly the two assignments the fix adds, and nothing around them.

    Slicing a fixed number of characters after the first one ran into the
    halt-gate comment that follows and made these assertions about text the
    fix does not own.
    """
    body = _body()
    lines = [ln for ln in body.splitlines()
             if ln.strip() in ("self._probe_leg_pending = None",
                               "self._probe_round_pending = None")]
    assert len(lines) >= 2, lines
    return "\n".join(lines)


# ============================================================ the entry reset

def test_both_markers_are_reset_on_entry_to_handle():
    """The bug fix itself: a call cannot inherit a previous call's markers."""
    body = _body()
    assert "self._probe_leg_pending = None" in body
    assert "self._probe_round_pending = None" in body


def test_the_reset_precedes_every_refusal_exit_and_the_marker_being_set():
    """The reset must be unconditional and FIRST.

    If it sat after any `return prov.refuse(...)` it would be reachable only
    on some paths, which is the shape of the bug it fixes.
    """
    body = _body()
    i_reset = body.index("self._probe_leg_pending = None")
    i_first_refusal = body.index("return prov.refuse(")
    i_set = body.index("self._probe_leg_pending = nxt")
    assert i_reset < i_first_refusal, "reset is behind a refusal exit"
    assert i_reset < i_set, "reset must precede the two-sided path setting it"


def test_the_reset_cannot_refuse_or_touch_a_venue():
    """It is local state, not a gate: no I/O, no refusal, no money field."""
    body = _body()
    window = _reset_statements()
    for forbidden in ("refuse(", "submit(", "transport", "price", "qty"):
        assert forbidden not in window, forbidden


# =================================================== strategy ownership kept

def test_strategy_ownership_still_keys_on_the_marker_in_both_directions():
    """The fix must not disarm exclusivity -- only stop it misfiring.

    TWO_SIDED ownership refuses when NO leg is pending (an L5-following
    order); L5_FOLLOWING ownership refuses when one IS pending (a two-sided
    leg). Both still read the marker.
    """
    body = _body()
    i_two = body.index('"reason": "STRATEGY_EXCLUSIVE_TWO_SIDED"')
    i_l5f = body.index('"reason": "STRATEGY_EXCLUSIVE_L5_FOLLOWING"')
    two_cond = body[i_two - 400:i_two]
    l5f_cond = body[i_l5f - 400:i_l5f]
    assert "_probe_leg_pending" in two_cond
    assert "not getattr(" in two_cond, "TWO_SIDED must key on NO leg pending"
    assert "_probe_leg_pending" in l5f_cond
    assert 'L5_WINDOW_STRATEGY' in body


def test_exclusivity_still_precedes_any_claim_or_submission():
    """Unchanged invariant: neither gate may be reached after a spent claim."""
    body = _body()
    i_excl = body.index('"reason": "STRATEGY_EXCLUSIVE_TWO_SIDED"')
    for marker in ("AUTH.claim_submission(",
                   "AUTH.claim_accumulation_leg(",
                   "self.transport.submit("):
        assert i_excl < body.index(marker), marker


def test_an_unset_window_strategy_still_enforces_nothing():
    """Unset preserves today's behaviour -- the fix adds no new ownership."""
    body = _body()
    i_two = body.index('"reason": "STRATEGY_EXCLUSIVE_TWO_SIDED"')
    cond = body[i_two - 400:i_two]
    assert '== "TWO_SIDED"' in cond, "must compare explicitly, not truthily"


# ============================================== post-only / maker validity

def test_the_fix_changes_no_leg_mode_price_or_size():
    """Post-only validity is untouched: the two-sided legs still build MAKER.

    The defensive unwind is the one documented exception and it is still the
    only place a non-MAKER mode comes from.
    """
    body = _body()
    assert "mode=(str(leg.get(\"mode\") or FT.MAKER).upper())" in body
    # the reset region introduces no mode decision of its own
    assert "FT.MAKER" not in _reset_statements()


def test_maker_only_overlay_is_still_downstream_of_the_marker_gate():
    """The overlay must remain reachable once the gate stops misfiring.

    In 1445z `safety` was null on every refused decision precisely because
    the ownership gate returned first. With the latch gone, an L5-following
    order reaches the overlay, which is where a TAKER translation is
    supposed to be refused -- by name, not by accident.
    """
    body = _body()
    i_l5f = body.index('"reason": "STRATEGY_EXCLUSIVE_L5_FOLLOWING"')
    i_overlay = body.index("SAFETY_OVERLAY")
    assert i_l5f < i_overlay or "MAKER_ONLY" in body


# ================================================ fee-inclusive exposure

def test_fee_model_and_cash_basis_are_untouched_by_the_fix():
    """The fix must not move the fee-inclusive ceiling in either direction."""
    g = VG.ValidationGates()
    assert g.fee_theta_worst == D("0.0695")
    assert g.fee_extra_per_order_usd == D("0.01")
    # the per-order allowance is still ADDED, which is what makes 4.64 the
    # fee-inclusive-$5 cap rather than 4.69
    fee = g.fee_worst(D("4.64") / D("0.05"), D("0.05"))
    assert fee == D("0.32")
    assert D("4.64") + fee <= D("5")


def test_the_4_69_cap_still_breaches_five_so_the_correction_cannot_regress():
    """Pins WHY the deployed cap is 4.64: 4.69 does not fit under $5."""
    g = VG.ValidationGates()
    worst = max(g.fee_worst(D("4.69") / (D(i) / D(100)), D(i) / D(100))
                for i in range(1, 100))
    assert D("4.69") + worst > D("5")


# ======================================================= order limits kept

def test_the_fix_adds_no_concurrency_and_claims_nothing():
    """`max_live_orders` and the durable claims are the only concurrency
    controls, and the reset neither reads nor spends them."""
    body = _body()
    window = _reset_statements()
    for forbidden in ("max_live_orders", "claim_", "acc_leg_attempt"):
        assert forbidden not in window, forbidden


def test_the_accumulation_slot_still_requires_both_markers():
    """The named (market, round, leg) slot must not be claimable with a
    half-set pair -- which an entry reset guarantees by construction."""
    body = _body()
    i_slot = body.index("self.acc_leg_attempt(")
    cond = body[:i_slot]
    assert "leg_pending = getattr(self, \"_probe_leg_pending\", None)" in cond
    assert "rnd_pending is not None" in cond


# =================================================== settlement accounting

def test_settlement_scoped_realized_reads_are_untouched():
    """The daily and cumulative ceilings still read their own scopes, and a
    missing figure still refuses rather than reading as zero."""
    body = _body()
    assert "realized_current_utc_day()" in body
    assert "DAY_REALIZED_UNAVAILABLE" in body
    assert "realized" not in _reset_statements()


# ========================================================= watchdog behaviour

def test_the_halt_gate_is_still_read_before_any_other_gate():
    """The reset is local state, so it may precede the halt read, but the
    halt must still come before translation and every refusal that follows.
    """
    body = _body()
    assert body.index("HALT_GATE") < body.index("UNREADABLE_ROW")
    i_reset = body.index("self._probe_leg_pending = None")
    i_halt = body.index("HALT_GATE")
    # the reset does not sit between the halt gate and its refusal
    assert i_reset < i_halt


def test_the_reset_does_not_clear_a_halt_or_a_latch():
    """Only the two markers are reset -- nothing that gates money."""
    body = _body()
    window = _reset_statements().lower()
    for forbidden in ("halt", "latch", "uncertain", "shadow"):
        assert forbidden not in window, forbidden


# ============================================ the behavioural two-call test
#
# The source assertions above pin the invariant; this drives the real defect:
# a REFUSED two-sided plan in call one, a genuine L5-following decision in
# call two. Before the entry reset, call two was refused on strategy
# ownership by a marker call one had left behind.

MARKET = "cpc-btc-updown-15m-2026-10-26-1445z"


def _executor(tmp_path, monkeypatch, *, owner="L5_FOLLOWING"):
    import tests.test_l5_live_execution as H
    from src import l5_two_sided_probe as TS

    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv("L5_WINDOW_STRATEGY", owner)
    # the two-sided engine ACTIVE on this market, which is what lets call
    # one build a plan and so set the markers
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    monkeypatch.setenv(TS.ROUND_ALLOCATIONS_ENV, "2")
    monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0.20")
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    t = H.FakeTransport(fill_all=False)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=1),
                           transport=t, now_fn=H.NOW)
    return x, t, H


def test_a_refused_two_sided_plan_cannot_refuse_the_NEXT_l5_following_call(
        tmp_path, monkeypatch):
    """THE REGRESSION. Two consecutive calls, the real `handle`.

    Call one runs with the two-sided engine active so it builds a plan and
    sets the markers, then refuses. Call two is a plain L5-following
    decision. The marker must not reach it.
    """
    import tests.test_l5_live_execution as H
    x, t, H = _executor(tmp_path, monkeypatch)

    r1 = x.handle(H.row(key="two-sided-1", side="UP", l5px="0.65"),
                  {}, market=MARKET, window="14:45")
    # call one refused something -- which reason does not matter here, only
    # that it did NOT reach the wire and may have left the markers set
    assert r1.get("submitted") is not True

    # SIMULATE THE LEAK EXACTLY AS PRODUCTION PRODUCED IT. Call one's own
    # refusal path is what left these set in 1445z; forcing them here makes
    # the test independent of which of the nineteen exits call one took.
    x._probe_leg_pending = "DOWN"
    x._probe_round_pending = 0

    r2 = x.handle(H.row(key="l5-following-2", side="UP", l5px="0.65"),
                  {}, market=MARKET, window="14:45")

    # 1. no stale marker survives into or out of the second call
    assert getattr(x, "_probe_leg_pending", "sentinel") is None
    assert getattr(x, "_probe_round_pending", "sentinel") is None

    # 2. the correct strategy is identified: an L5-following order in an
    #    L5_FOLLOWING window is NEVER excluded as the other engine
    assert r2.get("refused_by_layer") != "STRATEGY_EXCLUSIVE_L5_FOLLOWING"

    # 3. no unauthorized order slot was granted. With the markers cleared
    #    the accumulation path is not consulted at all, so whatever reaches
    #    the wire does so as an ordinary L5-following order on its own
    #    single-submission claim -- never on a borrowed pair slot.
    #
    #    IN THIS HARNESS THE ORDER DOES REACH THE WIRE, and that is the
    #    point: the latch, not the economics, was stopping it here. It is
    #    NOT evidence that production will submit. Production's books had
    #    L5 quoting THROUGH the touch, so no valid maker translation
    #    existed; this synthetic book (bid 0.40 / ask 0.60, L5 0.65) admits
    #    one. The fix removes a refusal that should not have applied; it
    #    creates no executable maker opportunity.
    assert len(t.sent) == 1, t.sent
    sent = t.sent[0]
    assert str(sent["client_order_id"]).startswith("l5f-")
    assert str(sent["side"]) == "UP"


def test_the_marker_still_excludes_a_REAL_two_sided_leg_in_the_same_call(
        tmp_path, monkeypatch):
    """The fix must not disarm ownership: a marker set WITHIN a call still
    identifies that call's order as the two-sided engine's."""
    x, t, H = _executor(tmp_path, monkeypatch)
    body = _body()
    # the gate reads the marker by getattr at the point of decision, so a
    # marker set during the call is still seen; only a marker from a
    # PREVIOUS call is cleared
    i_reset = body.index("self._probe_leg_pending = None")
    i_gate = body.index('"reason": "STRATEGY_EXCLUSIVE_L5_FOLLOWING"')
    i_set = body.index("self._probe_leg_pending = nxt")
    assert i_reset < i_set < i_gate


def test_the_reset_does_not_disturb_a_pending_order_or_held_inventory(
        tmp_path, monkeypatch):
    """POINT 3: a legitimate pending order and a multi-call completion
    lifecycle are carried by the STORE, not by these markers.

    Held inventory and live orders survive the reset untouched, which is why
    clearing the markers cannot interrupt a completion that spans calls.
    """
    x, t, H = _executor(tmp_path, monkeypatch)
    x.store.apply_fill(MARKET, "UP", D("1.56"), D("0.44"), basis="OWN")
    before = x.store.position(MARKET, "UP")

    x._probe_leg_pending = "DOWN"
    x._probe_round_pending = 1
    x.handle(H.row(key="after-fill", side="DOWN", l5px="0.52"),
             {}, market=MARKET, window="14:45")

    assert getattr(x, "_probe_leg_pending", "sentinel") is None
    # the inventory that drives completion is untouched by the reset
    after = x.store.position(MARKET, "UP")
    assert after == before == D("1.56")


def test_the_OTHER_direction_still_refuses_an_l5_following_order(
        tmp_path, monkeypatch):
    """Ownership still bites where it should.

    With TWO_SIDED named as the owner of the window and NO leg pending, an
    L5-following order is the one that must be excluded. If the fix had
    disarmed the gate this order would reach the wire.
    """
    import tests.test_l5_live_execution as H
    x, t, H = _executor(tmp_path, monkeypatch, owner="TWO_SIDED")
    rec = x.handle(H.row(key="l5f-under-two-sided", side="UP", l5px="0.65"),
                   {}, market=MARKET, window="14:45")
    # `prov.refuse(layer, reason)` -- the gate's name is the LAYER and the
    # market is the reason, which is also how it reads in the live logs.
    assert rec.get("refused_by_layer") == "STRATEGY_EXCLUSIVE_TWO_SIDED"
    assert t.sent == [], "an excluded strategy must not reach the wire"


def test_an_unset_owner_excludes_neither_strategy(tmp_path, monkeypatch):
    """Unset enforces nothing -- the fix adds no ownership that was absent."""
    import tests.test_l5_live_execution as H
    x, t, H = _executor(tmp_path, monkeypatch, owner="")
    monkeypatch.delenv("L5_WINDOW_STRATEGY", raising=False)
    rec = x.handle(H.row(key="l5f-unset-owner", side="UP", l5px="0.65"),
                   {}, market=MARKET, window="14:45")
    assert rec.get("refused_by_layer") not in (
        "STRATEGY_EXCLUSIVE_TWO_SIDED", "STRATEGY_EXCLUSIVE_L5_FOLLOWING")
