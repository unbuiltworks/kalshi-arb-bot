"""EXACTLY ONE reachable monetary authority on the Rung-A path: the mandate.

WHY (OWNER 2026-09-28). The probe's own DISARMED observe run printed

    live_limits {"max_usd_per_market": "5", "max_total_exposure_usd": "5",
                 "daily_realized_loss_halt_usd": "5", ...}

while the approved envelope was $1.00, because `l5_probe_manifest.probe_limits()`
hardcoded $5 and the entrypoint built `self.limits` from it.

That is NOT the RAMP_LADDER_001 failure. The ladder was dynamic -- `active()`
reloaded ten-day-old owner rungs and turned an authorized $0.05 into a committed
$19.3295 across eleven markets. This was static. Which is exactly why the
structural test aimed at dynamic rung caps did not flag it, and why "there is no
rung table" is not the same claim as "there is one authority".

A tighter mandate in front of a looser static ceiling is still two authorities:
the looser one is operative the moment the tighter one is absent, misparsed, or
bypassed. The fix was DERIVATION, not retuning -- the numbers were not changed to
$1, they are taken from the mandate, so they are whatever it says.
"""
from __future__ import annotations

import ast
from decimal import Decimal as D

import pytest

from src import l5_import_closure as C
from src import l5_live_execution as EX
from src import l5_probe_manifest as PM

ENTRY = "scripts/l5_pmus_live_oneshot.py"
MONEY_FIELDS = ("max_usd_per_market", "max_total_exposure_usd",
                "max_abs_residual", "daily_realized_loss_halt_usd")


class Mandate:
    max_spend_per_market = D("1.00")
    max_total_spend = D("1.00")
    max_residual = D("1.00")
    max_daily_realized_loss = D("1.00")
    max_markets = 1
    max_total_markets = 1


# --------------------------------------------------------------- structural

def _calls(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_the_entrypoint_never_calls_the_static_limit_source():
    called = _calls(ENTRY)
    assert "probe_limits" not in called, (
        "the static $5 monetary source is back on the Rung-A path")
    assert "worst_case" not in called, (
        "PM.worst_case() defaults to probe_limits(), so it reintroduces those "
        "dollars through the report")


def test_the_entrypoint_derives_its_limits_from_the_mandate():
    called = _calls(ENTRY)
    assert "from_mandate" in called
    assert "no_monetary_authority" in called, (
        "a run without a mandate must get ZERO dollars explicitly, not a default")


def test_the_mandate_is_the_only_reachable_module_defining_monetary_defaults():
    """Across the packaged graph, only the mandate may source per-probe dollars.

    `SafetyLimits.__init__` carries keyword defaults, so the check is on who
    SUPPLIES values on this path, proven by the two tests above plus the tag
    recorded on every instance the entrypoint builds.
    """
    packaged = C.closure(ENTRY)
    assert "src/pmus_live_mandate.py" in packaged
    # the legacy static source must not be reachable-and-used: the module may be
    # present for LAMBDA/SERIES, but the entrypoint must not call into its dollars
    assert "src/l5_probe_manifest.py" in packaged
    assert "probe_limits" not in _calls(ENTRY)
    # and no rung/ladder authority, static or dynamic
    for bad in ("pmus_l5_ramp_ladder", "pmus_micro_executor"):
        assert not [p for p in packaged if bad in p], bad


def test_operational_controls_carry_no_money():
    controls = PM.operational_controls()
    assert set(controls) == {"max_live_orders", "band"}
    assert controls["band"] is None
    assert isinstance(controls["max_live_orders"], int)
    for k, v in controls.items():
        assert not isinstance(v, D), "%s is a monetary value" % k


def test_limits_record_where_their_dollars_came_from():
    assert EX.SafetyLimits.from_mandate(
        Mandate(), **PM.operational_controls()).monetary_authority \
        == "PMUS_LIVE_MANDATE"
    assert EX.SafetyLimits.no_monetary_authority(
        **PM.operational_controls()).monetary_authority == "NONE_NO_MANDATE"


def test_no_mandate_means_no_dollars_not_default_dollars():
    lim = EX.SafetyLimits.no_monetary_authority(**PM.operational_controls())
    for f in MONEY_FIELDS:
        assert getattr(lim, f) == D(0), f
    with pytest.raises(ValueError):
        EX.SafetyLimits.from_mandate(None, **PM.operational_controls())


def test_the_limits_are_exactly_the_mandate_values():
    lim = EX.SafetyLimits.from_mandate(Mandate(), **PM.operational_controls())
    assert lim.max_usd_per_market == D("1.00")
    assert lim.max_total_exposure_usd == D("1.00")
    assert lim.max_abs_residual == D("1.00")
    assert lim.daily_realized_loss_halt_usd == D("1.00")
    assert lim.max_concurrent_markets == 1
    # and NOT the legacy static ceiling
    assert lim.max_usd_per_market != PM.probe_limits().max_usd_per_market


# ------------------------------------- a $1 mandate cannot commit more than $1

MKT = "cpc-btc-updown-15m-2026-09-28-0600z"


def lim():
    return EX.SafetyLimits.from_mandate(Mandate(), **PM.operational_controls())


def refuse(**kw):
    base = dict(market=MKT, committed_by_market={}, residual_after=D(0),
                realized_today=D(0), open_markets=[],
                outstanding_by_market={}, live_order_count=0)
    base.update(kw)
    return lim().refuse(**base)


def test_an_entry_over_one_dollar_is_refused():
    assert refuse(price=D("0.60"), qty=D(2)) == "MAX_USD_PER_MARKET"
    assert refuse(price=D("0.50"), qty=D(2)) is None          # exactly $1.00


def test_a_resting_order_counts_against_the_dollar():
    """A rest plus a new order may not exceed $1 even though neither alone does."""
    assert refuse(price=D("0.60"), qty=D(1),
                  outstanding_by_market={MKT: D("0.60")}) == "MAX_USD_PER_MARKET"


def test_filled_inventory_counts_against_the_dollar():
    assert refuse(price=D("0.60"), qty=D(1),
                  committed_by_market={MKT: D("0.60")}) == "MAX_USD_PER_MARKET"


def test_a_completion_cannot_exceed_the_dollar():
    """The opposite leg is priced the same way: it is an order, not an exception."""
    assert refuse(price=D("0.55"), qty=D(1),
                  committed_by_market={MKT: D("0.50")}) == "MAX_USD_PER_MARKET"


def test_a_retry_cannot_spend_the_dollar_twice():
    """Two attempts at the same intent: the second sees the first outstanding."""
    assert refuse(price=D("0.90"), qty=D(1)) is None
    assert refuse(price=D("0.90"), qty=D(1),
                  outstanding_by_market={MKT: D("0.90")}) == "MAX_USD_PER_MARKET"


def test_total_exposure_bounds_a_second_market_even_if_per_market_passes():
    other = "cpc-btc-updown-15m-2026-09-28-0615z"
    assert refuse(price=D("0.60"), qty=D(1),
                  committed_by_market={other: D("0.60")}) in (
        "MAX_TOTAL_EXPOSURE", "MAX_CONCURRENT_MARKETS")


def test_residual_is_bounded_at_one_dollar():
    assert refuse(price=D("0.10"), qty=D(1),
                  residual_after=D("1.50")) == "MAX_ABS_RESIDUAL"


def test_the_daily_loss_stop_is_one_dollar():
    assert refuse(price=D("0.10"), qty=D(1),
                  realized_today=D("-1.00")) == "DAILY_REALIZED_LOSS_HALT"


def test_uncertain_order_state_refuses_before_any_dollar_test():
    assert refuse(price=D("0.01"), qty=D(1),
                  orders_uncertain="unreconciled") == "ORDER_STATE_UNCERTAIN"


def test_lock_cannot_raise_the_dollar():
    """LOCK is a decision, not an authority. The bound is unchanged by it.

    pmus_lock5_translation.submit() calls mandate.admit before the transport and
    raises SafetyRefusal on a refusal; there is no LOCK branch that bypasses it.
    Here: whatever LOCK would like, the limits object still refuses over $1.
    """
    from src import pmus_lock5_translation as L5
    src = open(L5.__file__, encoding="utf-8").read()
    assert "MANDATE.admit" in src
    tree = ast.parse(src)
    submits = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "submit"]
    assert len(submits) == 1
    body = ast.dump(submits[0])
    assert "admit" in body, "submit() must consult the mandate"
    # and the refusal is unconditional: no `if self.locked` guard around admit
    assert "locked" not in body.split("admit")[0][-400:], (
        "a LOCK-conditional path to admit would make LOCK an authority")
    assert refuse(price=D("0.99"), qty=D(2)) == "MAX_USD_PER_MARKET"


# ========================= THE MEASUREMENT ENVELOPE IS NEVER LIVE AUTHORITY
# OBSERVE is preserved as a counterfactual measurement path (owner, 2026-09-28).
# Zeroing its envelope had silently turned `would_submit` from 1 into 0 -- it
# destroyed the diagnostic rather than tightening it, because the envelope refused
# intents before the NullTransport was ever consulted. So OBSERVE keeps a real
# envelope, explicitly labelled, and these tests are the enforcement that it can
# never be mistaken for authority.

import scripts.l5_pmus_live_oneshot as EP


class _PostCapable:
    name = "REAL_LOOKING"

    def __init__(self):
        self.posts = []

    def request(self, method, path, body=None, **kw):
        self.posts.append((method, path, body))
        return {}


def measurement():
    return EX.SafetyLimits.measurement_only(**PM.measurement_envelope())


def test_the_measurement_envelope_is_labelled_and_not_an_authority():
    m = measurement()
    assert m.monetary_authority == "NONE_OBSERVE_ONLY_NO_SPEND_POSSIBLE"
    assert m.is_measurement_only is True
    assert m.as_dict()["monetary_authority"] == \
        "NONE_OBSERVE_ONLY_NO_SPEND_POSSIBLE"
    # and it is NOT the mandate authority, nor the no-authority sentinel
    assert m.monetary_authority != "PMUS_LIVE_MANDATE"
    assert m.monetary_authority != "NONE_NO_MANDATE"


def test_the_measurement_envelope_keeps_the_counterfactual_measurable():
    """Non-zero bounds, or the diagnostic measures nothing."""
    m = measurement()
    for f in MONEY_FIELDS:
        assert getattr(m, f) > D(0), f
    assert m.max_concurrent_markets >= 1


def test_measurement_envelope_plus_post_capable_transport_fails_closed():
    """THE INVARIANT."""
    tp = _PostCapable()
    with pytest.raises(EX.MeasurementEnvelopeMisuse):
        EX.refuse_measurement_authority(measurement(), tp, where="test")
    assert tp.posts == []


def test_measurement_envelope_with_a_null_transport_is_permitted():
    EX.refuse_measurement_authority(measurement(), EX.NullTransport(),
                                    where="observe")


def test_a_mandate_envelope_is_never_refused_by_the_invariant():
    EX.refuse_measurement_authority(
        EX.SafetyLimits.from_mandate(Mandate(), **PM.operational_controls()),
        _PostCapable(), where="live")


def test_post_capable_defaults_to_capable_for_an_undeclared_transport():
    """Conservative direction: an unknown transport counts as able to spend."""
    class Undeclared:
        def request(self, *a, **k):
            return {}
    assert EX.post_capable(Undeclared()) is True
    assert EX.post_capable(EX.NullTransport()) is False
    assert EX.post_capable(None) is False


def test_the_paired_lock_transport_refuses_a_measurement_envelope():
    """The live boundary itself fails construction, not just a helper check."""
    class _L5:
        book = type("B", (), {"q": {"up": D(0), "down": D(0)}})()
    with pytest.raises(EX.MeasurementEnvelopeMisuse):
        EP.ADAPT.PairedLockTransport(_PostCapable(), lock5=_L5(), paired=None,
                                     limits=measurement())


def test_no_execution_path_converts_measurement_into_live_authority():
    """Source-level, by AST: only the OBSERVE function may build one.

    A text scan would flag the docstrings that explain the rule, which is how a
    guard gets deleted for crying wolf -- this repository has done that once.
    """
    import ast
    tree = ast.parse(open(EP.__file__, encoding="utf-8").read())
    builders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "measurement_only":
                builders.append(fn.name)
    assert builders == ["observe"], (
        "measurement_only() is built outside observe(): %s" % (builders,))
