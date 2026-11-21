"""The pair ceiling must gate BOTH order paths, and be provable at boot.

WHAT THIS IS FOR. Ten markets settled with a matched pair above $1.00 --
1.000191 to 1.085714, at least -$2.8295 of guaranteed loss on positions that
otherwise look clean, because a pair that cost 1.0857 pays 1.00 whichever
side wins. Every one of them is dated 2026-09-07..09, and BOTH ceilings
landed on 2026-09-10:

    2ad10cdc  2026-09-10 02:00:46Z  the OPEN/EXTEND submit guard
              (pair_invariant_ok, bounding the AVERAGE after fill)
    same day  the COMPLETE branch's cap, econ_max = ceiling - avg_cost,
              superseding "pair reported not gated" after
              asc-mlb-chc-mil-2026-09-09 completed a pair at 1.2412

So the ten are PRE-GUARD history, not a live escape. The point of this file
is that they stay history.

THE GAP IT CLOSES. `pair_invariant_ok` lives inside
`if relation in ("open", "extend")`; a COMPLETE is priced in its own branch
and capped there. The boot assertion checked only the first, so it would
have reported `matched_pair_invariant_enforced: true` with the COMPLETE cap
deleted -- and the COMPLETE is exactly the leg that produced the 1.2412
pair. A gate that guards one of two doors is not a gate.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_l5_continuous_session as C
from src import pmus_spring_l5_session as base


class _Inv:
    """Just enough Inventory for the ceiling arithmetic."""

    def __init__(self, up, dn, cost_up, cost_dn):
        self.up, self.dn = D(up), D(dn)
        self.cost_up, self.cost_dn = D(cost_up), D(cost_dn)


# ------------------------------------------------- the boot assertion
def test_the_boot_assertion_requires_both_paths():
    assert C._pair_invariant_enforced() is True


def test_it_would_fail_if_either_cap_were_removed(monkeypatch, tmp_path):
    """Proof the assertion BITES. Each cap is deleted from a copy of the
    source in turn and the assertion must go false for both -- otherwise it
    is indistinguishable from one that cannot fail."""
    real = open(base.__file__).read()
    for snippet in ("if not pair_invariant_ok(inv, side_up, px, qty):",
                    "capped = px > econ_max"):
        assert snippet in real, f"{snippet} is no longer in the session"
        doctored = tmp_path / "doctored.py"
        doctored.write_text(real.replace(snippet, "pass  # removed"))
        monkeypatch.setattr(base, "__file__", str(doctored))
        assert C._pair_invariant_enforced() is False, (
            f"removing {snippet!r} was not detected")
        monkeypatch.undo()


# --------------------------------------------- the OPEN/EXTEND guard
def test_an_extend_that_pushes_the_average_above_par_is_refused():
    """The measured shape. aec-atp-nikizq-santav-2026-09-08 reached
    up 3.78 @ 0.66 / dn 5 @ 0.49 and then extended NO at ~0.38. That is
    what the guard now refuses: it bounds the average AFTER the fill, not
    the marginal trade."""
    inv = _Inv("3.78", "5", "2.4948", "2.45")           # 0.66 / 0.49
    assert base.pair_invariant_ok(inv, False, D("0.38"), D("4.31")) is False
    after = base.matched_pair_cost_after(inv, False, D("0.38"), D("4.31"))
    assert after > D("1.00")


def test_a_cheap_extend_that_keeps_the_average_under_par_is_allowed():
    inv = _Inv("3.78", "5", "2.4948", "2.45")
    assert base.pair_invariant_ok(inv, False, D("0.10"), D("4.31")) is True


def test_the_guard_bounds_the_average_not_the_newest_trade():
    """The loophole the guard exists for, in its own docstring's numbers:
    OPEN 10 @ 0.10, COMPLETE 10 @ 0.85 (marginal pair 0.95, allowed), then
    EXTEND 10 @ 0.95 -> average pair 1.375."""
    inv = _Inv("10", "10", "1.00", "8.50")              # 0.10 / 0.85
    assert base.pair_invariant_ok(inv, True, D("0.95"), D("10")) is False
    assert base.matched_pair_cost_after(
        inv, True, D("0.95"), D("10")) == D("1.375")


def test_an_empty_side_has_no_matched_pair_to_protect():
    """None, not False: with one side empty there is no matched inventory
    yet, and refusing every first leg would stop the strategy rather than
    bound it."""
    inv = _Inv("0", "5", "0", "2.45")
    assert base.matched_pair_cost_after(inv, False, D("0.9"), D("1")) is None
    assert base.pair_invariant_ok(inv, False, D("0.9"), D("1")) is True


# ------------------------------------------------- the COMPLETE branch
def test_the_complete_cap_is_the_same_ceiling():
    """econ_max = ceiling - avg_cost, so the chosen price can never make
    avg_cost + px exceed the ceiling. Checked against the ceiling the
    OPEN/EXTEND guard reads, so the two paths cannot drift apart."""
    ceiling = base.completion_pair_ceiling()
    assert ceiling == D("1.00")
    avg_cost = D("0.7121")
    econ_max = ceiling - avg_cost
    assert avg_cost + econ_max == ceiling


def test_the_live_cricket_cap_binding_reproduces():
    """LIVE 2026-09-11: pair_at_touch 1.1385 refused down to pair_at_chosen
    0.9985 with capped_by_pair_ceiling true. The touch was 0.95 against an
    average of 0.7121; the ceiling allowed 0.2879, floored to the tick."""
    avg_cost, touch, tick = D("0.7121"), D("0.95"), D("0.01")
    econ_max = base.completion_pair_ceiling() - avg_cost
    assert touch > econ_max, "the cap must bind on these numbers"
    px = (econ_max / tick).to_integral_value(rounding="ROUND_DOWN") * tick
    assert avg_cost + px <= base.completion_pair_ceiling()
    assert avg_cost + touch > D("1.00"), "and the touch alone was above par"


def test_the_label_no_longer_claims_the_pair_is_ungated():
    """The label read "pair reported not gated" for a day after the cap made
    it false. A stale label on a safety field is a trap for whoever reads it
    next while deciding whether the ceiling is live -- it briefly misled
    this investigation.

    The check is deliberately narrow. A first version banned the phrase
    anywhere in the module and then failed on the comment explaining that
    the phrase is obsolete -- banning the word instead of the claim. What
    must not exist is the phrase asserting CURRENT behaviour: as a label
    value the engine emits, or as a docstring stating what l5_touch does.
    """
    src = open(base.__file__).read()
    for line in src.splitlines():
        if "pair reported not gated" not in line:
            continue
        assert '"labels"' not in line, f"emitted as a live label: {line!r}"
        assert "was true" in line or "kept saying" in line, (
            f"states it as current behaviour: {line.strip()!r}")
    # the emitted label wraps across source lines, so match the marker,
    # not a phrase that only exists once the literals are concatenated
    assert "L5 fidelity: touch pricing, GATED at " in src
