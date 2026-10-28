"""The pair gate separates OPENING exposure from COMPLETING what we hold.

WHERE THE DECISION ACTUALLY HAPPENS, which is not where the first two
attempts put it. `pair_cost_refusal` sits at the SUBMISSION boundary, after
`complement_order` has priced the leg, and it judges the REALISED basis:

    eff = (leg_cost[UP] + leg_cost[DOWN] + px * qty) / matched_after

For 1245z -- 2.27 UP held for $0.7264, completing DOWN at own 0.65 --
`eff` is 2.2019 / 2.27 = 0.9699, and the 0.96 ceiling refused it HERE. So
widening the ceiling inside the complement achieved nothing on the books
that produced the stranded legs. This is the gate that had to change.

THE SEPARATION, and why each condition is necessary:

  reduces        held[other] > held[side]   or it is an opening in disguise
  no_reversal    after[side] <= held[other] or it buys through parity into
                                               fresh exposure
  eff <= 1.00                               or the matched book is a certain
                                               loss, since a pair pays
                                               exactly $1.00 a share
  px <= ceiling                             the completion economics and the
                                               owner's residual budget

A defensive mandate keeps its own looser 1.20 ceiling and is untouched.
"""
from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates

from tests.test_l5_two_sided_wiring import (_book, _row, mk,  # noqa: F401
                                            probe)

M = "m"


def gates(deficit="3"):
    return ValidationGates(max_cash_deployed_usd=D("12"),
                           max_unmatched_contracts=D("10"),
                           max_cumulative_loss_usd=D("20"),
                           max_worst_case_deficit_usd=D(deficit))


def rescue(ex, *, side, other, px, qty, held, after, lc, eff):
    return ex.completion_pair_rescue(
        M, side=side, other=other, px=D(px), qty=D(qty),
        held={k: D(v) for k, v in held.items()},
        after={k: D(v) for k, v in after.items()},
        lc={k: (None if v is None else D(v)) for k, v in lc.items()},
        eff=D(eff))


@pytest.fixture()
def ex(probe, tmp_path):
    H, E = probe
    e, _t = mk(H, E, tmp_path)
    e.validation_gates = gates()
    return e


# ------------------------------------- the three recorded live cases

def test_1215z_completion_is_rescued(ex):
    """4.21 UP for $1.5547 against 1.92 DOWN for $0.7488, completing DOWN
    2.29 at own 0.49. eff = (2.3035 + 1.1221) / 4.21 = 0.8137, already
    inside 0.96 -- so this one never needed the rescue, and the test records
    that rather than implying it did."""
    r = rescue(ex, side="DOWN", other="UP", px="0.49", qty="2.29",
               held={"UP": "4.21", "DOWN": "1.92"},
               after={"UP": "4.21", "DOWN": "4.21"},
               lc={"UP": "1.5547", "DOWN": "0.7488"}, eff="0.8137")
    assert r["ok"] is True, r
    assert r["reduces"] is True and r["no_reversal"] is True


def test_1245z_the_case_the_ceiling_actually_refused(ex):
    """2.27 UP for $0.7264, nothing on the other side, completing DOWN 2.27
    at own 0.65: eff 0.9699, refused by 0.96 and permitted as a completion."""
    r = rescue(ex, side="DOWN", other="UP", px="0.65", qty="2.27",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "2.27", "DOWN": "2.27"},
               lc={"UP": "0.7264", "DOWN": None}, eff="0.9699")
    assert r["ok"] is True, r
    assert D(r["effective_pair_cost"]) > TS.max_pair_cost()
    assert D(r["effective_pair_cost"]) <= D(1)
    assert r["completion_ceiling"] == "0.95"


def test_1230z_completion_is_rescued(ex):
    """4.79 UP for $1.1972 against 2.38 DOWN for $1.4518, completing DOWN
    2.41 at own 0.62: eff = (2.649 + 1.4942) / 4.79 = 0.8649."""
    r = rescue(ex, side="DOWN", other="UP", px="0.62", qty="2.41",
               held={"UP": "4.79", "DOWN": "2.38"},
               after={"UP": "4.79", "DOWN": "4.79"},
               lc={"UP": "1.1972", "DOWN": "1.4518"}, eff="0.8649")
    assert r["ok"] is True, r


# ----------------------------- it may not open, reverse, or lose

def test_an_OPENING_is_never_rescued(ex):
    """Nothing unmatched on the other side, so this is fresh exposure and
    the 0.96 ceiling is the whole answer."""
    r = rescue(ex, side="DOWN", other="UP", px="0.65", qty="2.00",
               held={"UP": "0", "DOWN": "0"},
               after={"UP": "0", "DOWN": "2.00"},
               lc={"UP": None, "DOWN": None}, eff="0.99")
    assert r["ok"] is False
    assert r["reason"] == "NOT_A_COMPLETION_NOTHING_UNMATCHED"


def test_adding_to_the_side_we_already_HOLD_is_never_rescued(ex):
    r = rescue(ex, side="UP", other="DOWN", px="0.65", qty="1.00",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "3.27", "DOWN": "0"},
               lc={"UP": "0.7264", "DOWN": None}, eff="0.99")
    assert r["ok"] is False
    assert r["reason"] == "NOT_A_COMPLETION_NOTHING_UNMATCHED"


def test_buying_THROUGH_parity_into_new_exposure_is_refused(ex):
    """2.27 UP held, buying 4.00 DOWN would leave 1.73 DOWN unmatched the
    other way -- an opening hiding inside a completion."""
    r = rescue(ex, side="DOWN", other="UP", px="0.65", qty="4.00",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "2.27", "DOWN": "4.00"},
               lc={"UP": "0.7264", "DOWN": None}, eff="0.97")
    assert r["ok"] is False
    assert r["reason"] == "WOULD_CROSS_ZERO_INTO_NEW_EXPOSURE"


def test_a_pair_above_the_dollar_it_redeems_is_always_refused(ex):
    """The line between a dear rescue and a certain loss. No residual
    argument crosses it."""
    r = rescue(ex, side="DOWN", other="UP", px="0.90", qty="2.27",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "2.27", "DOWN": "2.27"},
               lc={"UP": "0.7264", "DOWN": None}, eff="1.0028")
    assert r["ok"] is False
    assert r["reason"] == "EFFECTIVE_PAIR_ABOVE_REDEMPTION"


def test_a_price_above_the_completion_ceiling_is_refused(ex):
    ex.validation_gates = gates(deficit="0")
    r = rescue(ex, side="DOWN", other="UP", px="0.90", qty="2.27",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "2.27", "DOWN": "2.27"},
               lc={"UP": "0.7264", "DOWN": None}, eff="0.99")
    assert r["ok"] is False
    assert r["reason"] == "ABOVE_COMPLETION_CEILING"


# ------------------------------------------------ fails closed

def test_an_unreadable_residual_budget_refuses(ex):
    ex.validation_gates = None
    r = rescue(ex, side="DOWN", other="UP", px="0.65", qty="2.27",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "2.27", "DOWN": "2.27"},
               lc={"UP": "0.7264", "DOWN": None}, eff="0.9699")
    assert r["ok"] is False
    assert r["reason"] == "RESIDUAL_BUDGET_UNKNOWN"


def test_an_unreadable_basis_refuses(ex):
    r = rescue(ex, side="DOWN", other="UP", px="0.65", qty="2.27",
               held={"UP": "2.27", "DOWN": "0"},
               after={"UP": "2.27", "DOWN": "2.27"},
               lc={"UP": None, "DOWN": None}, eff="0.9699")
    assert r["ok"] is False
    assert r["reason"] in ("NO_COMPLETION_CEILING", "CEILING_UNREADABLE")


# --------------------------------------- end to end through handle

def test_the_OPENING_pair_ceiling_still_refuses_a_flat_market(probe,
                                                              tmp_path):
    """The property that must not be traded away: a flat market cannot open
    a pair above 0.96, and the refusal still names the pair gate."""
    H, E = probe
    e, t = mk(H, E, tmp_path)
    e.validation_gates = gates()
    # a book whose planned pair exceeds the ceiling
    rec = e.handle(_row(H), _book(bid="0.0300", ask="0.0400"),
                   market=M, window="21:00")
    assert not t.sent
    assert rec.get("refused_reason") is not None


def test_a_held_residual_reaches_SUBMISSION_through_the_pair_gate(probe,
                                                                 tmp_path):
    """THE QUESTION THE OWNER ASKED: can the mechanism actually reach the
    wire.

    2.29 UP held for $0.8457. Book bid 0.3500 / ask 0.3600 puts the two-tick
    DOWN quote at own 0.62, so the REALISED pair is
    (0.8457 + 1.4198) / 2.29 = 0.9893 -- above the 0.96 opening ceiling and
    under the $1.00 a pair redeems. The old gate refused this; the
    completion separation lets it reach submission.
    """
    H, E = probe
    e, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_qty="0"))
    e.validation_gates = gates()
    e.store.apply_fill(M, "UP", D("2.29"), D("0.3693"), basis=E.OWN)
    rec = e.handle(_row(H), _book(bid="0.3500", ask="0.3600"),
                   market=M, window="21:00")
    assert t.sent, rec.get("refused_reason")
    o = t.sent[-1]
    assert o["side"] == "DOWN"
    assert o["mode"] == "MAKER"
    assert D(o["qty"]) <= D("2.29")          # reduces, never crosses zero
    det = (rec.get("ORDER") or {}).get("detail") or {}
    assert det == {} or det.get("why") != "PAIR_COST_ABOVE_CEILING"


def test_a_completion_above_the_DOLLAR_is_still_refused_end_to_end(probe,
                                                                   tmp_path):
    """The same inventory on a worse book: own 0.64 makes the realised pair
    1.0093, a certain loss, and the rescue refuses by name."""
    H, E = probe
    e, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_qty="0"))
    e.validation_gates = gates()
    e.store.apply_fill(M, "UP", D("2.29"), D("0.3693"), basis=E.OWN)
    rec = e.handle(_row(H), _book(bid="0.3300", ask="0.3400"),
                   market=M, window="21:00")
    assert not t.sent
    det = (rec.get("ORDER") or {}).get("detail") or {}
    resc = det.get("completion_rescue") or {}
    assert resc.get("reduces") is True
    assert resc.get("no_reversal") is True
    assert resc.get("reason") == "EFFECTIVE_PAIR_ABOVE_REDEMPTION"
    assert D(resc["effective_pair_cost"]) > D(1)
