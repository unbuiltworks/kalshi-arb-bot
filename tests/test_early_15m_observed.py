"""Both legs priced from real trades; queue position stays UNOBSERVABLE."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import early_15m_observed as EO


def _p(ms, price, amount, side):
    return {"utc_ms": ms, "price": price, "amount": amount, "type": side}


EXP = 1_000_000


def _tte(sec):
    """A print `sec` seconds before expiry."""
    return EXP - sec * 1000


# ============================================ the whole point of the rewrite

def test_THE_BASIS_IS_NOT_CONSTRUCTED_IT_IS_TWO_REAL_TRADES():
    """The old replay priced the complement at 1 - held - tick, so every
    basis was 0.99 by arithmetic. Here both legs come off the tape."""
    prints = [_p(_tte(800), "0.40", "5", "sell"),    # reaches a YES bid @0.40
              _p(_tte(700), "0.55", "5", "buy")]     # NO then costs 1-0.55
    r = EO.replay_observed("S", prints, expiry_ms=EXP)
    assert r["held_leg"] == "YES" and r["held_price"] == "0.40"
    assert r["opposite_price"] == "0.45"
    assert r["pair_basis"] == "0.85"
    assert r["at_or_below_par"] is True
    # and the CODE never derives one leg from the other. Checked on the AST
    # rather than the text, because the module's docstring deliberately
    # describes the old construction in order to disown it.
    import ast
    import inspect
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(EO)))
              if isinstance(n, ast.FunctionDef) and n.name == "replay_observed")
    body = ast.unparse(fn)
    assert "TICK" not in body
    # comp_price comes off a print, never off held_price
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "comp_price" for t in node.targets):
            assert "held_price" not in ast.unparse(node.value)


def test_a_pair_above_par_is_reported_as_such_not_hidden():
    prints = [_p(_tte(800), "0.60", "5", "sell"),
              _p(_tte(700), "0.50", "5", "buy")]     # NO costs 0.50
    r = EO.replay_observed("S", prints, expiry_ms=EXP)
    assert r["pair_basis"] == "1.10"
    assert r["at_or_below_par"] is False
    assert r["fee_positive"] is False


def test_par_holds_exactly_when_the_later_buy_is_at_or_above_the_earlier_sell():
    """b >= h is the observable condition on two real trades."""
    for h, b, par in (("0.40", "0.40", True), ("0.40", "0.41", True),
                      ("0.40", "0.39", False)):
        r = EO.replay_observed("S", [_p(_tte(800), h, "5", "sell"),
                                     _p(_tte(700), b, "5", "buy")],
                               expiry_ms=EXP)
        assert r["at_or_below_par"] is par, (h, b)


# ====================================================== tape semantics

def test_a_SELL_reaches_a_YES_bid_and_a_BUY_reaches_a_NO_bid():
    yes = EO.replay_observed("S", [_p(_tte(800), "0.30", "5", "sell"),
                                   _p(_tte(700), "0.80", "5", "buy")],
                             expiry_ms=EXP)
    assert yes["held_leg"] == "YES" and yes["held_price"] == "0.30"
    no = EO.replay_observed("S", [_p(_tte(800), "0.30", "5", "buy"),
                                  _p(_tte(700), "0.20", "5", "sell")],
                            expiry_ms=EXP)
    assert no["held_leg"] == "NO" and no["held_price"] == "0.70"


def test_the_complement_is_the_first_OPPOSITE_print_not_the_next_print():
    prints = [_p(_tte(800), "0.40", "5", "sell"),
              _p(_tte(790), "0.41", "5", "sell"),   # same direction, skipped
              _p(_tte(700), "0.55", "5", "buy")]
    r = EO.replay_observed("S", prints, expiry_ms=EXP)
    assert r["seconds_to_complement"] == 100.0


# ============================================== what is never invented

def test_QUEUE_POSITION_IS_UNOBSERVABLE_ON_EVERY_ROW():
    r = EO.replay_observed("S", [_p(_tte(800), "0.40", "5", "sell"),
                                 _p(_tte(700), "0.55", "5", "buy")],
                           expiry_ms=EXP)
    assert r["maker_reachable"] == EO.UNOBSERVABLE
    assert "no book history" in r["why_unreachable_unknown"]
    c = EO.census([r])
    assert c["maker_reachable_complements"] == EO.UNOBSERVABLE


def test_no_opposite_flow_leaves_a_residual_and_no_basis():
    r = EO.replay_observed("S", [_p(_tte(800), "0.40", "5", "sell"),
                                 _p(_tte(700), "0.42", "5", "sell")],
                           expiry_ms=EXP)
    assert r["opposite_flow"] is False
    assert r["pair_basis"] == EO.UNOBSERVABLE
    assert r["terminal_residual"] == "0.01"
    assert r["terminal_residual_leg"] == "YES"


def test_only_the_early_window_is_read():
    """A print inside the clone's own band is not early-window evidence."""
    r = EO.replay_observed("S", [_p(_tte(250), "0.40", "5", "sell"),
                                 _p(_tte(200), "0.55", "5", "buy")],
                           expiry_ms=EXP)
    assert r["computable"] is False
    assert EO.EARLY_LO_S == 300.0 and EO.EARLY_HI_S == 900.0


# ========================================================== fees

def test_both_legs_pay_a_maker_fee():
    r = EO.replay_observed("S", [_p(_tte(800), "0.40", "5", "sell"),
                                 _p(_tte(700), "0.60", "5", "buy")],
                           expiry_ms=EXP)
    # basis 0.40 + 0.40 = 0.80, both legs at 0.40
    assert r["pair_basis"] == "0.80"
    expect = 2 * float(EO.MAKER_RATE) * 0.40 * 0.60
    assert abs(float(r["fees_per_pair"]) - expect) < 1e-9
    assert r["fee_positive"] is True


def test_the_census_keeps_unobservable_apart_from_zero():
    rows = [EO.replay_observed("A", [_p(_tte(800), "0.40", "5", "sell")],
                               expiry_ms=EXP),
            EO.replay_observed("B", [_p(_tte(800), "0.40", "5", "sell"),
                                     _p(_tte(700), "0.55", "5", "buy")],
                               expiry_ms=EXP)]
    c = EO.census(rows)
    assert c["first_fill_states"] == 2
    assert c["opposite_flow"] == 1
    assert c["price_observable_complements"] == 1
    assert c["terminal_residual_states"] == 1
    assert c["maker_reachable_complements"] == EO.UNOBSERVABLE
    assert "UPPER BOUND" in c["labels"]


def test_distinct_bases_would_have_caught_the_old_tautology():
    """One distinct basis across every match is the signature of a
    constructed price, and the census reports the count."""
    rows = [EO.replay_observed("A", [_p(_tte(800), "0.40", "5", "sell"),
                                     _p(_tte(700), "0.55", "5", "buy")],
                               expiry_ms=EXP),
            EO.replay_observed("B", [_p(_tte(800), "0.30", "5", "sell"),
                                     _p(_tte(700), "0.75", "5", "buy")],
                               expiry_ms=EXP)]
    c = EO.census(rows)
    assert c["distinct_bases"] == 2


def test_BAND_IS_A_PARAMETER_SO_THE_CONTROL_USES_THIS_SAME_CODE():
    """The frozen [0,300] control must be measurable by the same function.

    Two instruments differing in more than timing could not answer whether
    TIMING is what changes the outcome.
    """
    exp = 1_700_000_000_000
    prints = [
        # TTE 800 -- inside the early band only
        {"utc_ms": exp - 800_000, "price": "0.40", "amount": "5",
         "type": "sell"},
        {"utc_ms": exp - 700_000, "price": "0.62", "amount": "5",
         "type": "buy"},
        # TTE 200 -- inside the late band only
        {"utc_ms": exp - 200_000, "price": "0.30", "amount": "5",
         "type": "sell"},
        {"utc_ms": exp - 150_000, "price": "0.55", "amount": "5",
         "type": "buy"},
    ]
    early = EO.replay_observed("S", prints, expiry_ms=exp)
    late = EO.replay_observed("S", prints, expiry_ms=exp,
                              lo=EO.LATE_LO_S, hi=EO.LATE_HI_S)

    assert early["band_tte_s"] == [EO.EARLY_LO_S, EO.EARLY_HI_S]
    assert late["band_tte_s"] == [EO.LATE_LO_S, EO.LATE_HI_S]
    # each band saw only its own two prints, and priced its own pair
    assert early["prints_in_early_window"] == 2
    assert late["prints_in_early_window"] == 2
    assert early["held_price"] == "0.40" and early["pair_basis"] == "0.78"
    assert late["held_price"] == "0.30" and late["pair_basis"] == "0.75"


def test_A_BAND_WITH_NO_PRINTS_IS_NOT_COMPUTABLE_RATHER_THAN_EMPTY_PAIR():
    exp = 1_700_000_000_000
    prints = [{"utc_ms": exp - 800_000, "price": "0.40", "amount": "5",
               "type": "sell"}]
    late = EO.replay_observed("S", prints, expiry_ms=exp,
                              lo=EO.LATE_LO_S, hi=EO.LATE_HI_S)
    assert late["computable"] is False
    assert "0-300s" in late["why"]
    assert "pair_basis" not in late


def test_CENSUS_NAMES_THE_BAND_IT_SUMMARISES():
    rows = [{"computable": False, "band_tte_s": [0.0, 300.0]}]
    c = EO.census(rows, label="LATE_15M_CONTROL")
    assert c["label"] == "LATE_15M_CONTROL"
    assert c["band_tte_s"] == [0.0, 300.0]
