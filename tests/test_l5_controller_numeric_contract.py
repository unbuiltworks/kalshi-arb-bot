"""The controller route's numeric contract, and the 1815z completion crash.

WHAT HAPPENED. `l5_live_execution.handle` coerces `price` and `l5_size` to
Decimal on the L5 route (`FT.l5_native`) and took the controller route's
decision mapping verbatim. `maybe_book_completion` seeds its controller
re-entry from `TS.complement_order`, whose `leg` carries `str(px)` and
`str(q)`, so `dec["l5_size"] * self.lam` raised TypeError before any gate,
order, refusal or record. On 1815z (2026-11-04) that silenced all 14
RISK_REDUCING_COMPLETION decisions in 93 seconds.

These tests assert the CONTRACT, not the crash: a readable string is
accepted as the Decimal it denotes, and an unreadable one is a named
refusal rather than an exception.
"""
from decimal import Decimal as D

import pytest

from src import l5_execution_translations as X
from src import l5_faithful_translation as FT
from src import l5_two_sided_probe as TS


# ----------------------------------------------------- the crash, exactly

def test_the_shipped_expression_raises_on_a_str_size():
    """`dec["l5_size"] * self.lam` with a str size is the production error.

    Pinned to the exact message Railway recorded 14 times on 1815z so a
    future reader can match the tape to this test.
    """
    with pytest.raises(TypeError) as ei:
        _ = "1" * D("1")
    assert "can't multiply sequence by non-int" in str(ei.value)
    assert "decimal.Decimal" in str(ei.value)


def test_a_decimal_size_multiplies_which_is_why_openings_worked():
    assert D("1") * D("1") == D("1")


# ------------------------------------- the two routes' numeric contracts

def test_l5_native_hands_back_decimals_not_strings():
    """The contract the controller route now matches."""
    dec = FT.l5_native({"l5": {"pmus_side": "UP", "l5_price": "0.46",
                               "l5_size": "1", "relation_actual": "open"}})
    assert dec is not None
    assert isinstance(dec["price"], D) and dec["price"] == D("0.46")
    assert isinstance(dec["l5_size"], D) and dec["l5_size"] == D("1")


@pytest.mark.parametrize("bad", ["", "abc", None, "nan"])
def test_l5_native_refuses_an_unreadable_number(bad):
    """None, which `handle` turns into a named refusal -- never a crash."""
    dec = FT.l5_native({"l5": {"pmus_side": "UP", "l5_price": bad,
                               "l5_size": "1"}})
    assert dec is None or dec["price"] is not None


def test_l5_native_refuses_a_non_positive_size():
    assert FT.l5_native({"l5": {"pmus_side": "UP", "l5_price": "0.46",
                                "l5_size": "0"}}) is None


# --------------------------- what complement_order actually hands upwards

def _completion_leg_like_1815z() -> dict:
    """A PERMITTED complement in 1815z's state: held UP 1 at own 0.46, the
    DOWN touch at own 0.50, pair 0.96. Asserted permitted so the test below
    cannot pass vacuously on a refusal -- the first version of it did."""
    out = TS.complement_order(
        side="DOWN", qty="1", cost_px="0.46", bid="0.49", ask="0.50",
        tick="0.01", min_qty="0.01", depth_ticks=0, book_age_s=0.1,
        bid_shares="500", ask_shares="500",
        held_side="UP", held_qty="1", held_cost_usd="0.46",
        deficient_qty="1", residual_budget_usd="1.50")
    assert out.get("ok") is True, out.get("reason")
    assert out.get("pair_cost") == "0.96"
    return dict(out.get("leg") or {})


def test_complement_order_leg_carries_strings_which_is_the_trap():
    """THE SOURCE OF THE BAD TYPE, asserted so the fix cannot be undone by
    'the caller surely passes Decimals'. It does not: it passes `str`."""
    leg = _completion_leg_like_1815z()
    assert leg["price"] == "0.50" and isinstance(leg["price"], str)
    assert leg["qty"] == "1" and isinstance(leg["qty"], str)


def test_that_exact_leg_crashes_the_shipped_multiply_and_survives_coercion():
    """End to end on the real leg, not a hand-made one."""
    leg = _completion_leg_like_1815z()
    with pytest.raises(TypeError):
        _ = leg["qty"] * D("1")
    assert X._d(leg["qty"]) * D("1") == D("1")
    assert X._d(leg["price"]) == D("0.50")


# ------------------------------------------- the coercion the fix applies

@pytest.mark.parametrize("raw,want", [
    ("1", D("1")), ("0.69", D("0.69")), (D("2"), D("2")), (2, D("2")),
])
def test_the_helper_accepts_every_shape_a_caller_sends(raw, want):
    assert X._d(raw) == want


@pytest.mark.parametrize("bad", ["", "abc", None, object()])
def test_the_helper_returns_none_on_garbage_rather_than_raising(bad):
    assert X._d(bad) is None


def test_a_coerced_str_size_then_multiplies():
    """The whole point: after coercion the shipped expression works."""
    assert X._d("1") * D("1") == D("1")
    assert X._d("0.69") * D("1") == D("0.69")


# -------------------------------------------- the fix is present in source

def _src() -> str:
    import io
    return io.open("src/l5_live_execution.py", encoding="utf-8").read()


def test_the_controller_route_coerces_both_numeric_fields():
    s = _src()
    assert 'for _f in ("price", "l5_size"):' in s
    assert "_v = X._d(dec.get(_f))" in s
    assert "dec[_f] = _v" in s


def test_an_unreadable_controller_number_is_a_named_refusal():
    s = _src()
    assert "CONTROLLER_DECISION_UNREADABLE" in s
    assert '"PMUS_TRANSLATION",' in s
    assert "_bad_num: Optional[str] = None" in s
    assert "if _bad_num is not None:" in s
    # the refusal must be REACHABLE: it has to sit after `prov` exists,
    # or it would raise NameError instead of refusing.
    assert s.index("prov = Provenance(key, dec or") < s.index(
        "if _bad_num is not None:")


def test_a_non_positive_controller_size_is_refused_like_l5_native():
    s = _src()
    assert '(_f == "l5_size" and _v <= 0)' in s


def test_non_finite_numbers_are_refused():
    """NaN and Infinity are readable Decimals and not numbers, and `<= 0`
    is False for NaN, so a null-and-sign check alone let them through to
    raise InvalidOperation deeper in."""
    s = _src()
    assert "not _v.is_finite()" in s


def test_an_absent_number_is_treated_as_unreadable():
    """`dec.get` not `dec[...]`, so a missing field becomes the same named
    refusal rather than a KeyError in controller_translate."""
    s = _src()
    assert "X._d(dec.get(_f))" in s
    assert "if dec.get(_f) is None:" not in s


def test_the_coercion_runs_before_the_three_multiplication_sites():
    """Ordering is the whole fix: coercing after the multiply fixes nothing."""
    s = _src()
    assert s.index("dec[_f] = _v") < s.index('want = dec["l5_size"] * self.lam')


def _coercion_block(s: str) -> str:
    """Just the coercion loop, anchored on the loop itself rather than on
    the `_bad_num` declaration -- that declaration has to sit ahead of the
    controller/L5 branch so both routes bind it, so it is not a usable
    anchor for 'what the controller branch does'."""
    i = s.index('for _f in ("price", "l5_size"):')
    j = s.index("# PROVISIONAL, and refined to the real", i)
    return s[i:j]


def test_only_the_two_numeric_fields_are_touched():
    """Every other key must reach the gates as the caller wrote it."""
    block = _coercion_block(_src())
    assert '"price", "l5_size"' in block
    for k in ("side", "relation", "maker_class", "provenance"):
        assert ('dec["%s"] =' % k) not in block


def test_the_l5_route_is_not_touched_by_this_change():
    """`dec = FT.l5_native(row)` is still the whole of the L5 route's
    coercion: the new loop must not reach into it."""
    s = _src()
    assert "dec = FT.l5_native(row)" in s
    # the block may NAME l5_native in a comment; it must not CALL it.
    assert "FT.l5_native" not in _coercion_block(s)


def test_bad_num_is_bound_on_both_routes():
    """The refusal is read after the branch rejoins, so the L5 route must
    bind it too or it raises UnboundLocalError where it used not to."""
    s = _src()
    assert s.index("_bad_num: Optional[str] = None") < s.index(
        "if controller is not None:")


def test_the_refusal_is_taken_after_the_marker_reset():
    """`test_l5_probe_leg_marker_leak` requires the unconditional reset to
    precede every refusal exit; this refusal must obey that."""
    s = _src()
    assert s.index("self._probe_leg_pending = None") < s.index(
        "if _bad_num is not None:")


def test_the_new_refusal_is_in_the_reviewed_inventory():
    """`test_l5_intent_gate` counts refusal sites against this tuple."""
    from src import l5_intent_gate as G
    assert ("PMUS_TRANSLATION:CONTROLLER_DECISION_UNREADABLE"
            in G.EXECUTOR_GATES_NOT_EVALUATED_HERE)
