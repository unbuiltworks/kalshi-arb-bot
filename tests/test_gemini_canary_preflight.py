"""The preflight prints what WOULD be submitted, and can submit nothing."""
import ast
import inspect
import json

import pytest

from src import gemini_canary_preflight as P


# ------------------------------------------------------- it cannot trade

def test_it_has_no_order_transport_and_imports_none():
    tree = ast.parse(inspect.getsource(P))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    assert not (mods & {"src.pmus_q1_executor", "src.l5_surface_exec.adapter",
                        "src.gemini_kalshi_probe_transport"})
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for banned in ("place_limit_buy", "new_order", "submit_order",
                   "cancel_order"):
        assert banned not in names


def test_it_says_it_authorizes_nothing():
    k = P.kill_state()
    assert k["armed"] is False and k["submission_reachable"] is False


# ------------------------------------------------------- the envelope

def test_the_envelope_is_the_tightened_first_probe_one():
    assert str(P.QTY) == "0.01"
    assert P.MAX_ORDERS == 1 and P.MAX_WINDOWS == 1
    assert str(P.MAX_RESIDUAL) == "0.01"
    assert P.SIDE == "BUY"


def test_placement_is_the_touch_and_is_not_tuned_for_priority():
    """A placement change would make the first live result incomparable with
    the observations that justified running it."""
    assert P.TICK_OFFSET == 0


# --------------------------------------------------- window eligibility

def test_a_window_outside_the_tte_band_is_refused():
    # 23:24:37 -> nearest window has 23s left, next has 323s: neither is in
    # [60, 300], so the preflight refuses rather than pick a bad instrument
    w = P.choose_window(now=1789687477.0)
    assert w["chosen"] is None
    assert all("TTE outside" in r["why"] for r in w["rejected"])


def test_refusing_is_a_result_not_a_crash():
    out = P.build(now=1789687477.0)
    assert out["ok"] is False and out["verdict"] == "NO_ELIGIBLE_WINDOW"
    assert "authorization" not in out or "NOT GRANTED" in str(
        out.get("authorization", "NOT GRANTED"))


def test_expiry_is_parsed_from_the_venues_own_symbol():
    assert P.expiry_of("GEMI-BTC05M2609172325-UP") == 1789687500
    assert P.expiry_of("nonsense") is None


def test_candidate_windows_are_all_in_the_future():
    now = 1789687477.0
    for s in P.candidate_symbols(now):
        assert P.expiry_of(s) > now


# ------------------------------------------------------ balance handling

def test_balance_is_a_sufficiency_boolean_and_the_amount_is_withheld():
    """An artifact that carries the account balance is one that leaks it."""
    src = inspect.getsource(P.account_checks)
    assert "WITHHELD_BY_DESIGN" in src
    tree = ast.parse(src)
    # the amount is compared, never returned
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    flat = " ".join(ast.dump(r) for r in returns)
    assert "'available'" not in flat and '"available"' not in flat


# --------------------------------------------------------- the ladder

def test_the_status_ladder_is_strict_and_ends_at_realized():
    assert P.status_ladder() == ["SUBMITTED", "FILLED_OR_UNFILLED",
                                 "EXPIRED_OR_SETTLED", "RECONCILED",
                                 "REALIZED"]


def test_every_acceptance_path_is_named():
    p = P.paths()
    assert set(p) == {"durable_intent", "authoritative_order_fill",
                      "settlement_position", "cash_reconciliation"}
    for v in p.values():
        assert v["path"] and v["why"]


def test_realized_is_defined_as_venue_cash():
    why = P.paths()["cash_reconciliation"]["why"]
    assert "venue's cash movement" in why and "the venue is right" in why
