"""The PMUS sports surface: a per-side funnel that cannot flatter itself."""
import pytest

from src import pmus_l5_surface_shadow as S
from src.l5_surface import lanes as LN, params as PR


def _surf(slug="aec-t-2026-09-17"):
    return S.MarketSurface(slug, S.spec())


_UNSET = object()


def _mkt(bid="0.43", ask="0.48", bq="20", aq="570", trades=_UNSET):
    """`trades=None` means an UNREADABLE tape and must pass through as None.
    Coercing it to [] would assert nobody traded -- the very distinction
    these tests exist to hold -- so the default needs its own sentinel."""
    return {"bid": bid, "ask": ask, "bid_qty": bq, "ask_qty": aq,
            "trades": [] if trades is _UNSET else trades}


def _funnel(surf):
    return S.report({surf.slug: surf})["per_side"]


# ----------------------------------------------------- the venue mapping

def test_it_uses_the_frozen_pmus_lane_spec():
    assert S.spec().label == LN.PMUS_SPORTS


def test_the_engine_is_addressed_in_its_own_outcome_names():
    """The frozen spec's scope is ["Up", "Down"]. Handing it YES/NO produced
    emitted=True with zero rungs -- a surface that silently quoted nothing."""
    outs = S.outcomes_from(_mkt())
    assert set(outs) == {"Up", "Down"}
    assert set(S.spec().get("both_sides_policy")["outcomes"]) == {"Up", "Down"}


def test_buying_no_is_selling_yes_at_one_minus_the_price():
    outs = S.outcomes_from(_mkt(bid="0.43", ask="0.48"))
    assert outs["Down"]["bid"] == "0.52"      # 1 - 0.48
    assert outs["Down"]["ask"] == "0.57"      # 1 - 0.43


def test_a_one_sided_book_supports_no_surface():
    """Inventing the missing side is how a surface quotes against nothing."""
    assert S.outcomes_from(_mkt(ask=None)) is None
    assert S.outcomes_from(_mkt(bid=None)) is None


def test_a_crossed_book_supports_no_surface():
    assert S.outcomes_from(_mkt(bid="0.50", ask="0.48")) is None


def test_a_surface_that_quotes_nothing_says_so():
    surf = _surf()
    S.step(surf, _mkt(ask=None), 100.0, tte_s=3600.0)
    assert surf.empty_reason["book_not_two_sided"] == 1
    assert dict(surf.ladder) == {}


# ------------------------------------------------ queue-ahead at placement

def test_queue_ahead_is_captured_at_placement_from_the_side_we_join():
    surf = _surf()
    S.step(surf, _mkt(bq="20", aq="570"), 100.0, tte_s=3600.0)
    q = surf.queue_report()
    assert q["BUY_YES"]["median_queue_ahead_at_placement"] == "20"
    assert q["BUY_NO"]["median_queue_ahead_at_placement"] == "570"


def test_queue_ahead_is_frozen_and_not_re_read():
    """A quote that rests while the level grows is behind what was there
    when it joined, not behind the new volume."""
    surf = _surf()
    S.step(surf, _mkt(bq="20"), 100.0, tte_s=3600.0)
    S.step(surf, _mkt(bq="9999"), 101.0, tte_s=3600.0)
    assert surf.queue_report()["BUY_YES"][
        "median_queue_ahead_at_placement"] == "20"


def test_unserved_depth_is_unknown_not_zero():
    """Zero would put us at the front of a queue we cannot see."""
    surf = _surf()
    S.step(surf, _mkt(bq=None), 100.0, tte_s=3600.0)
    assert surf.queue_report()["BUY_YES"]["queue_unknown"] == 1
    assert surf.queue_report()["BUY_YES"][
        "median_queue_ahead_at_placement"] is None


def test_an_unknown_queue_can_never_clear():
    """Assuming zero would manufacture a fill out of any trade at all."""
    surf = _surf()
    S.step(surf, _mkt(bq=None), 100.0, tte_s=3600.0)
    rows = S.step(surf, _mkt(bq=None, trades=[
        {"t": 110.0, "px": "0.43", "qty": "9999"}]), 120.0, tte_s=3600.0)
    assert rows == []
    assert _funnel(surf)["BUY_YES"]["TRADE_FLOW"] == 1
    assert _funnel(surf)["BUY_YES"]["EVIDENCED_FILL"] == 0


# --------------------------------------------------------- the funnel

def test_the_funnel_is_monotone_on_both_sides():
    surf = _surf()
    for i in range(5):
        S.step(surf, _mkt(), 100.0 + i, tte_s=3600.0)
    S.step(surf, _mkt(trades=[{"t": 110.0, "px": "0.43", "qty": "25"}]),
           120.0, tte_s=3600.0)
    for side, v in _funnel(surf).items():
        assert (v["PLACED"] >= v["RESTABLE"] >= v["BOOK_REACHED"]
                >= v["TRADE_FLOW"] >= v["EVIDENCED_FILL"]), side


def test_a_quote_counts_once_per_stage_however_long_it_rests():
    """Mixing per-order PLACED with per-observation stages printed RESTABLE
    above PLACED, which reads as more quotes resting than were ever placed."""
    surf = _surf()
    for i in range(20):
        S.step(surf, _mkt(), 100.0 + i, tte_s=3600.0)
    v = _funnel(surf)["BUY_YES"]
    assert v["PLACED"] == 1 and v["RESTABLE"] == 1 and v["BOOK_REACHED"] == 1


def test_a_fill_always_carries_the_flow_that_caused_it():
    """A fill with no flow behind it is incoherent: flow is its prerequisite."""
    surf = _surf()
    S.step(surf, _mkt(), 100.0, tte_s=3600.0)
    S.step(surf, _mkt(trades=[{"t": 110.0, "px": "0.43", "qty": "25"}]),
           120.0, tte_s=3600.0)
    v = _funnel(surf)["BUY_YES"]
    assert v["EVIDENCED_FILL"] == 1 and v["TRADE_FLOW"] == 1


def test_flow_that_does_not_clear_the_queue_is_not_a_fill():
    surf = _surf()
    S.step(surf, _mkt(bq="100"), 100.0, tte_s=3600.0)
    rows = S.step(surf, _mkt(bq="100", trades=[
        {"t": 110.0, "px": "0.43", "qty": "30"}]), 120.0, tte_s=3600.0)
    assert rows == []
    v = _funnel(surf)["BUY_YES"]
    assert v["TRADE_FLOW"] == 1 and v["EVIDENCED_FILL"] == 0


def test_a_trade_above_our_bid_is_not_flow_through_it():
    surf = _surf()
    S.step(surf, _mkt(), 100.0, tte_s=3600.0)
    S.step(surf, _mkt(trades=[{"t": 110.0, "px": "0.47", "qty": "500"}]),
           120.0, tte_s=3600.0)
    assert _funnel(surf)["BUY_YES"]["TRADE_FLOW"] == 0


def test_a_trade_before_placement_never_counts():
    surf = _surf()
    S.step(surf, _mkt(), 100.0, tte_s=3600.0)
    S.step(surf, _mkt(trades=[{"t": 50.0, "px": "0.40", "qty": "999"}]),
           120.0, tte_s=3600.0)
    assert _funnel(surf)["BUY_YES"]["TRADE_FLOW"] == 0


def test_the_no_side_fills_on_flow_through_its_yes_equivalent():
    """A NO rung at 0.52 is a YES offer at 0.48; it fills when YES trades
    at or ABOVE 0.48."""
    surf = _surf()
    S.step(surf, _mkt(aq="10"), 100.0, tte_s=3600.0)
    rows = S.step(surf, _mkt(aq="10", trades=[
        {"t": 110.0, "px": "0.48", "qty": "40"}]), 120.0, tte_s=3600.0)
    assert len(rows) == 1 and rows[0]["side"] == "BUY_NO"
    assert rows[0]["venue_outcome"] == "NO"
    assert surf.qty["Down"] == 1


# ------------------------------------------------------------ inventory

def test_only_an_evidenced_fill_moves_inventory():
    surf = _surf()
    for i in range(10):
        S.step(surf, _mkt(), 100.0 + i, tte_s=3600.0)
    assert surf.qty["Up"] == 0 and surf.qty["Down"] == 0


def test_recycling_needs_both_legs_evidenced():
    r = S.recycling([{"outcome": "Up", "t": 100.0},
                     {"outcome": "Up", "t": 101.0}])
    assert r["n_opposite_within_300s"] == 0
    r2 = S.recycling([{"outcome": "Up", "t": 100.0},
                      {"outcome": "Down", "t": 110.0}])
    assert r2["n_opposite_within_15s"] == 1


# ----------------------------------------------------------- the verdict

def test_no_flow_and_queue_bound_are_different_verdicts():
    """The distinction that mattered on the Gemini lane."""
    quiet = _surf("quiet")
    for i in range(3):
        S.step(quiet, _mkt(), 100.0 + i, tte_s=3600.0)
    assert S.report({"quiet": quiet})["verdict"] == "NO_FILLS_NO_FLOW"

    bound = _surf("bound")
    S.step(bound, _mkt(bq="9999", aq="9999"), 100.0, tte_s=3600.0)
    S.step(bound, _mkt(bq="9999", aq="9999", trades=[
        {"t": 110.0, "px": "0.43", "qty": "5"},
        {"t": 111.0, "px": "0.48", "qty": "5"}]), 120.0, tte_s=3600.0)
    assert S.report({"bound": bound})["verdict"] == "NO_FILLS_BOTH_SIDES_QUEUE_BOUND"


def test_pair_cost_admits_and_refuses_nothing():
    """Owner 2026-09-17: <=0.96 is not an entry requirement here."""
    import ast
    import inspect
    # AST CONSTANTS, not text: the docstring explains why 0.96 is not a gate
    # here, and a substring scan fails on the sentence saying so. Fourth time
    # a prose-versus-code scan has bitten in this work.
    tree = ast.parse(inspect.getsource(S))
    consts = {str(n.value) for n in ast.walk(tree)
              if isinstance(n, ast.Constant)}
    assert "0.96" not in consts
    # a wide book whose pair cost is far above any ceiling still quotes
    surf = _surf()
    S.step(surf, _mkt(bid="0.49", ask="0.51"), 100.0, tte_s=3600.0)
    assert _funnel(surf)["BUY_YES"]["PLACED"] == 1


# --------------------------------------------------- holds nothing it should

def test_it_holds_no_credential_and_opens_no_socket():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(S))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    assert not (mods & {"os", "httpx", "websockets", "socket", "urllib"})
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (names & {"environ", "getenv", "urlopen"})


def test_no_order_path_is_reachable():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(S))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & {"place_limit_buy", "request", "submit"})


# ------------------------------- unreadable tape is not observed absence

def test_an_unreadable_tape_is_not_reported_as_no_flow():
    """The public gateway serves no tape at all. Calling that NO_FLOW turns a
    missing input into a finding about the market."""
    surf = _surf()
    for i in range(3):
        S.step(surf, _mkt(trades=None), 100.0 + i, tte_s=3600.0)
    r = S.report({surf.slug: surf})
    assert r["verdict"] == "TRADE_FLOW_NOT_OBSERVABLE"
    assert r["tape"] == {"readable_observations": 0,
                         "unreadable_observations": 3,
                         "flow_observable": False}


def test_a_readable_empty_tape_IS_observed_absence():
    surf = _surf()
    for i in range(3):
        S.step(surf, _mkt(trades=[]), 100.0 + i, tte_s=3600.0)
    r = S.report({surf.slug: surf})
    assert r["verdict"] == "NO_FILLS_NO_FLOW"
    assert r["tape"]["flow_observable"] is True


def test_an_unreadable_tape_with_nothing_placed_says_both():
    surf = _surf()
    S.step(surf, _mkt(ask=None, trades=None), 100.0, tte_s=3600.0)
    assert S.report({surf.slug: surf})["verdict"] == \
        "SURFACE_NEVER_PLACED_AND_FLOW_NOT_OBSERVABLE"


def test_tape_readability_is_counted_per_observation_not_per_quote():
    surf = _surf()
    S.step(surf, _mkt(trades=None), 100.0, tte_s=3600.0)
    assert surf.tape_unreadable == 1        # not 2, despite two live quotes
