"""A controller COMPLETION intent must reach the executor, not raise.

END TO END through the real `FaithfulExecutor.handle`, on the same fixture
`test_l5_controller_open_trigger` uses for openings. The difference is the
one that mattered in production: `maybe_book_completion` seeds its intent
from `TS.complement_order`, whose `leg` carries `"price": str(px)` and
`"qty": str(q)`, while `maybe_controller_open` passes Decimals. So openings
worked and completions raised `TypeError: can't multiply sequence by
non-int of type 'decimal.Decimal'` at `dec["l5_size"] * self.lam`, before
any gate, order, refusal or record -- 14 times in 93 seconds on 1815z,
2026-11-04.

These tests assert the OUTCOME the owner asked for: a valid completion
decision reaches the executor and comes back as a decision record, and an
invalid one fails closed by name. Nothing is armed; `FakeTransport` is the
wire.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "CC-E2E-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "CC-E2E-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
    return H, E


def _book():
    return {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
            "ask_shares": "5000", "stream_age_s": "0.05"}


def _ex(H, E, tmp_path):
    t = H.FakeTransport(fill_all=False)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=2), transport=t,
                            now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = ValidationGates(
        max_cash_deployed_usd=D("8"), max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("10"),
        max_worst_case_deficit_usd=D("3"))
    return ex, t


def _completion_intent(E, price, size):
    """EXACTLY the shape `maybe_book_completion` sends: the complement leg's
    own fields, with `relation: "complete"`, and no maker class."""
    return {"provenance": E.CONTROLLER_GENERATED,
            "maker_class": None,
            "decision": {"side": "DOWN", "price": price,
                         "l5_size": size, "relation": "complete"}}


# ------------------------------------------------- a VALID string decision
#
# `handle` returns a PROVENANCE CHAIN. The fields that say what happened are
# `refused_by_layer`, `refused_reason` and `stage_reached`; a decision that
# was not refused carries None for the first two.

VALID = [("0.50", "1"), ("0.41", "1"), ("0.53", "1"), ("0.50", "0.69")]

# NaN and Infinity are READABLE Decimals and not numbers: `X._d("nan")`
# returns Decimal("NaN"), for which `<= 0` is False. Both are in here
# because both slipped a null-and-sign check and raised InvalidOperation
# inside `controller_translate` until the boundary gained `is_finite()`.
INVALID = [("abc", "1", "price"), ("", "1", "price"),
           ("nan", "1", "price"), ("inf", "1", "price"),
           ("-inf", "1", "price"), (D("NaN"), "1", "price"),
           ("0.50", "abc", "l5_size"), ("0.50", "", "l5_size"),
           ("0.50", "0", "l5_size"), ("0.50", "-1", "l5_size"),
           ("0.50", D("0"), "l5_size"), ("0.50", "nan", "l5_size"),
           ("0.50", "inf", "l5_size")]


@pytest.mark.parametrize("price,size", VALID)
def test_a_valid_string_completion_is_not_refused_at_all(probe, tmp_path,
                                                         price, size):
    """The production shape, and the real prices the policy proposed on
    1815z and 1745z. Pre-fix every one of these raised TypeError before any
    layer could record anything."""
    H, E = probe
    ex, _t = _ex(H, E, tmp_path / ("ok-%s-%s" % (price, size)))
    rec = ex.handle(None, _book(), market="m", window="21:00",
                    controller=_completion_intent(E, price, size))
    assert rec.get("refused_by_layer") is None, rec.get("refused_reason")
    assert rec.get("refused_reason") is None
    # it got all the way through translation, the two-sided probe, the
    # overlay, the pair slot, the claim and the residual guard
    assert rec.get("stage_reached") == "INVENTORY"
    for layer in ("PMUS_TRANSLATION", "TWO_SIDED_PROBE", "SAFETY_OVERLAY",
                  "SUBMISSION_CLAIM", "FINAL_RESIDUAL_GUARD"):
        assert layer in rec, (layer, sorted(rec))


def test_a_decimal_completion_reaches_exactly_the_same_place(probe, tmp_path):
    """Coercion is a no-op on the shape that already worked, so the opening
    path cannot regress."""
    H, E = probe
    ex_s, _ = _ex(H, E, tmp_path / "s")
    r_s = ex_s.handle(None, _book(), market="m", window="21:00",
                      controller=_completion_intent(E, "0.50", "1"))
    ex_d, _ = _ex(H, E, tmp_path / "d")
    r_d = ex_d.handle(None, _book(), market="m", window="21:00",
                      controller=_completion_intent(E, D("0.50"), D("1")))
    for k in ("refused_by_layer", "refused_reason", "stage_reached"):
        assert r_s.get(k) == r_d.get(k), k


# ----------------------------------------------- INVALID decisions: closed

@pytest.mark.parametrize("price,size,bad_field", INVALID)
def test_an_unreadable_number_fails_closed_by_name(probe, tmp_path,
                                                   price, size, bad_field):
    H, E = probe
    ex, t = _ex(H, E, tmp_path / ("bad-%s-%s" % (price, size)))
    rec = ex.handle(None, _book(), market="m", window="21:00",
                    controller=_completion_intent(E, price, size))
    assert rec.get("refused_by_layer") == "PMUS_TRANSLATION"
    assert rec.get("refused_reason") == (
        "CONTROLLER_DECISION_UNREADABLE:%s" % bad_field)
    # refused at the EARLIEST layer, so nothing downstream was consulted
    assert rec.get("stage_reached") == "L5_NATIVE"
    assert not getattr(t, "submitted", None)


@pytest.mark.parametrize("missing", ["price", "l5_size"])
def test_an_absent_number_fails_closed_rather_than_raising(probe, tmp_path,
                                                           missing):
    """`FT.controller_translate` does `decision["price"]` unconditionally,
    so an absent number used to raise KeyError here -- the same family of
    crash as the TypeError, one layer further on. The L5 route has always
    refused by name on a missing price; this route now does too."""
    H, E = probe
    ex, t = _ex(H, E, tmp_path / ("miss-%s" % missing))
    intent = _completion_intent(E, "0.50", "1")
    del intent["decision"][missing]
    rec = ex.handle(None, _book(), market="m", window="21:00",
                    controller=intent)
    assert rec.get("refused_reason") == (
        "CONTROLLER_DECISION_UNREADABLE:%s" % missing)
    assert not getattr(t, "submitted", None)


def test_no_completion_shape_raises_at_all(probe, tmp_path):
    """The regression in one assertion: across valid, unreadable and absent
    numbers, `handle` RETURNS rather than raising."""
    H, E = probe
    shapes = ([(p, s) for p, s in VALID]
              + [(p, s) for p, s, _ in INVALID]
              + [(None, "1"), ("0.50", None), (None, None)])
    for i, (p, s) in enumerate(shapes):
        ex, _t = _ex(H, E, tmp_path / ("shape%d" % i))
        d = {"provenance": E.CONTROLLER_GENERATED, "maker_class": None,
             "decision": {"side": "DOWN", "relation": "complete"}}
        if p is not None:
            d["decision"]["price"] = p
        if s is not None:
            d["decision"]["l5_size"] = s
        out = ex.handle(None, _book(), market="m", window="21:00",
                        controller=d)
        assert isinstance(out, dict), (p, s, out)


def test_nothing_in_this_suite_ever_reached_a_wire(probe, tmp_path):
    H, E = probe
    ex, t = _ex(H, E, tmp_path)
    for p, s in VALID + [(x, y) for x, y, _ in INVALID]:
        ex.handle(None, _book(), market="m", window="21:00",
                  controller=_completion_intent(E, p, s))
    assert not getattr(t, "submitted", None)
