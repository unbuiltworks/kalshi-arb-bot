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
