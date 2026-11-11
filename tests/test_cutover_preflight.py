"""The 16:33Z stale-flat-state failure, encoded so it cannot recur.

THE ACTUAL SEQUENCE, which every test here is built from:
  16:21:48Z  risk set empty            <- measured
  16:29:10Z  funnel no_candidate       <- measured
  16:33:43Z  cutover triggered         <- ACTED, on a 12-minute-old read
  (between) aec-cs2-mzp-ezt filled 8.11 NO contracts for $3.0443

Nothing was lost, because the boot gate swept zero orphaned orders. That was
the gate working, not the operator. These tests make the operator's step fail
closed too.
"""
import itertools

from src import pmus_cutover_preflight as cp

FLAT = {"held": [], "open_orders_ours": 0, "open_orders_foreign": 0,
        "risk_bearing": [], "unmatched_usd": "0"}
FILLED = {"held": ["aec-cs2-mzp-ezt-2026-09-13"], "open_orders_ours": 0,
          "open_orders_foreign": 0,
          "risk_bearing": ["aec-cs2-mzp-ezt-2026-09-13"],
          "unmatched_usd": "3.0443"}


def _clock(*ts):
    it = itertools.chain(ts, itertools.repeat(ts[-1]))
    return lambda: next(it)


def test_the_actual_failure_is_now_refused():
    """Authorize on the 16:21:48Z flat read, act at 16:33:43Z. 715 seconds."""
    auth = cp.authorize(lambda: FLAT, now=_clock(0.0))
    assert auth["authorized"] is True
    out = cp.may_act(auth, lambda: FILLED, now=_clock(715.0))
    assert out["act"] is False
    assert out["reason"] == "stale_authorization"
    assert out["age_s"] == 715.0


def test_state_change_inside_the_window_still_aborts():
    """Even a FRESH authorization aborts if the world moved before acting.
    This is the half that staleness alone would not catch."""
    auth = cp.authorize(lambda: FLAT, now=_clock(0.0))
    out = cp.may_act(auth, lambda: FILLED, now=_clock(5.0))
    assert out["act"] is False
    assert out["reason"] == "state_changed_since_authorization"
    d = out["diff"]
    assert d["held"]["before"] == [] and d["held"]["after"] == ["aec-cs2-mzp-ezt-2026-09-13"]
    assert d["unmatched_usd"]["before"] == "0"
    assert d["unmatched_usd"]["after"] == "3.0443"


def test_fresh_and_unchanged_authorizes():
    auth = cp.authorize(lambda: FLAT, now=_clock(0.0))
    out = cp.may_act(auth, lambda: FLAT, now=_clock(3.0))
    assert out["act"] is True and out["age_s"] == 3.0


def test_exactly_at_the_window_passes_and_one_tick_past_does_not():
    for age, want in ((cp.CUTOVER_MAX_AGE_S, True),
                      (cp.CUTOVER_MAX_AGE_S + 0.001, False)):
        auth = cp.authorize(lambda: FLAT, now=_clock(0.0))
        out = cp.may_act(auth, lambda: FLAT, now=_clock(age))
        assert out["act"] is want, (age, out)


def test_a_held_position_refuses_authorization_under_the_strict_boundary():
    auth = cp.authorize(lambda: FILLED, now=_clock(0.0))
    assert auth["authorized"] is False
    assert set(auth["failed"]) == {"no_held_market", "no_risk_bearing_market"}


def test_the_revised_boundary_still_reports_what_it_carries():
    """require_flat=False is the dormant-residual boundary. It may authorize,
    but the residual must still be visible to a reader of the deploy log."""
    auth = cp.authorize(lambda: FILLED, require_flat=False, now=_clock(0.0))
    assert auth["authorized"] is True
    assert auth["state"]["risk_bearing"] == ["aec-cs2-mzp-ezt-2026-09-13"]
    assert auth["state"]["unmatched_usd"] == "3.0443"
    assert auth["require_flat"] is False


def test_an_open_order_refuses_at_either_boundary():
    for flat in (True, False):
        st = {**FLAT, "open_orders_ours": 1}
        assert cp.authorize(lambda: st, require_flat=flat,
                            now=_clock(0.0))["authorized"] is False
        st = {**FLAT, "open_orders_foreign": 1}
        assert cp.authorize(lambda: st, require_flat=flat,
                            now=_clock(0.0))["authorized"] is False


def test_an_unreadable_venue_is_a_refusal_not_a_clean_state():
    def boom():
        raise RuntimeError("429 Too Many Requests")
    auth = cp.authorize(boom, now=_clock(0.0))
    assert auth["authorized"] is False and auth["reason"] == "venue_read_failed"
    good = cp.authorize(lambda: FLAT, now=_clock(0.0))
    out = cp.may_act(good, boom, now=_clock(1.0))
    assert out["act"] is False and out["reason"] == "recheck_venue_read_failed"


def test_a_malformed_read_is_a_refusal():
    auth = cp.authorize(lambda: ["not", "a", "mapping"], now=_clock(0.0))
    assert auth["authorized"] is False and auth["reason"] == "venue_read_malformed"


def test_time_can_only_invalidate_never_authorize():
    """No amount of waiting turns a refusal into a pass -- the same shape as
    the re-arm gate, where `refusals` may be reported but never compared."""
    auth = cp.authorize(lambda: FILLED, now=_clock(0.0))
    for age in (0.0, 1.0, 59.0, 600.0, 86400.0):
        assert cp.may_act(auth, lambda: FLAT, now=_clock(age))["act"] is False


def test_venue_list_ordering_is_not_a_state_change():
    a = {**FLAT, "held": ["m1", "m2"], "risk_bearing": ["m1", "m2"]}
    b = {**FLAT, "held": ["m2", "m1"], "risk_bearing": ["m2", "m1"]}
    auth = cp.authorize(lambda: a, require_flat=False, now=_clock(0.0))
    assert cp.may_act(auth, lambda: b, now=_clock(1.0))["act"] is True


def test_decimal_formatting_is_not_a_state_change_but_a_cent_is():
    auth = cp.authorize(lambda: {**FLAT, "unmatched_usd": "3.0443"},
                        require_flat=False, now=_clock(0.0))
    same = cp.may_act(auth, lambda: {**FLAT, "unmatched_usd": "3.04430"},
                      now=_clock(1.0))
    assert same["act"] is True
    moved = cp.may_act(auth, lambda: {**FLAT, "unmatched_usd": "3.0543"},
                       now=_clock(1.0))
    assert moved["act"] is False
    assert moved["reason"] == "state_changed_since_authorization"


def test_unreadable_dollars_are_not_zero():
    st = cp.read_state(lambda: {**FLAT, "unmatched_usd": "not-a-number"},
                       now=_clock(0.0))
    assert st["state"]["unmatched_usd"] is None


def test_the_module_cannot_place_or_cancel_an_order():
    """Read-only by construction: no order verb may appear in its source."""
    import pathlib, re
    src = pathlib.Path(cp.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for verb in ("place_order", "submit", "cancel", "POST", "DELETE",
                 "redeploy", "set_variables"):
        assert verb not in code, verb
