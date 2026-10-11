"""MAKER_OPEN -> HYBRID_COMPLETE: the complement priced on the real ladder.

The asymmetry is the whole point. Gemini publishes ONE ladder, priced in
YES; buying YES lifts the asks, buying NO is a SELL of YES and hits the
bids. Pricing a NO complement off the ask ladder would invent liquidity.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import ab_arms as AB
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import hybrid_complete as HC

BOOK = {"yes": {"bid": "0.40", "ask": "0.44"},
        "no": {"bid": "0.56", "ask": "0.60"}}
LADDER = {"readable": True,
          "bids": {"levels": [{"price": "0.40", "qty": "5"},
                              {"price": "0.38", "qty": "500"}]},
          "asks": {"levels": [{"price": "0.44", "qty": "5"},
                              {"price": "0.46", "qty": "500"}]}}


# ------------------------------------------------------- the asymmetry
def test_YES_COMPLEMENT_LIFTS_THE_ASKS_AND_NO_COMPLEMENT_HITS_THE_BIDS():
    y = HC.executable_complement(BOOK, LADDER, "YES", D("0.01"))
    n = HC.executable_complement(BOOK, LADDER, "NO", D("0.01"))
    assert y["executable"] and y["vwap"] == "0.44"        # the ask
    # buying NO at the touch costs 1 - best bid, NOT the ask ladder
    assert n["executable"] and n["vwap"] == "0.60" == str(D("1") - D("0.40"))


def test_A_NO_COMPLEMENT_WALKS_DOWN_THE_BIDS_GETTING_DEARER():
    """Size beyond the touch is worse, and on the correct side."""
    deep = HC.executable_complement(BOOK, LADDER, "NO", D("10"))
    # 5 at (1-0.40)=0.60 then 5 at (1-0.38)=0.62
    assert deep["executable"] and deep["vwap"] == "0.61"
    assert deep["levels_consumed"] == 2


def test_AN_UNCOVERED_COMPLEMENT_IS_NOT_AN_OPPORTUNITY():
    out = HC.executable_complement(BOOK, LADDER, "YES", D("10000"))
    assert out["executable"] is False
    assert "does not cover" in out["why"]


def test_AN_UNREADABLE_LADDER_IS_NOT_PRICED_AT_THE_TOUCH():
    out = HC.executable_complement(BOOK, {"readable": False}, "YES", D("0.01"))
    assert out["executable"] is False


# ------------------------------------ two reads, two clocks, fail closed
def test_A_LADDER_INSIDE_THE_BOOK_TOUCH_IS_A_DISAGREEMENT_NOT_A_BARGAIN():
    """`depth` is fetched separately from `book`. When the ladder prices the
    complement BELOW the book's own ask the two reads contradict each other,
    and taking the cheaper one would manufacture liquidity from staleness."""
    stale = {"readable": True,
             "bids": {"levels": [{"price": "0.40", "qty": "500"}]},
             "asks": {"levels": [{"price": "0.20", "qty": "500"}]}}
    out = HC.executable_complement(BOOK, stale, "YES", D("0.01"))
    assert out["executable"] is False
    assert out["disagreement"] == "0.24"
    assert out["ladder_vwap"] == "0.20"


def test_A_LADDER_AT_OR_OUTSIDE_THE_TOUCH_IS_FINE():
    assert HC.executable_complement(BOOK, LADDER, "YES",
                                    D("0.01"))["executable"] is True


# ----------------------------------------------------------- the basis
def test_THE_PAIR_PAYS_MAKER_ON_ONE_LEG_AND_TAKER_ON_THE_OTHER():
    p = HC.all_in_basis(D("0.40"), D("0.60"))
    assert p["gross"] == D("1.00")
    assert p["maker_fee"] == HC.MAKER_RATE * D("0.40") * D("0.60")
    assert p["taker_fee"] == HC.TAKER_RATE * D("0.60") * D("0.40")
    assert p["taker_fee"] > p["maker_fee"]          # 0.07 vs 0.0175
    assert p["all_in"] > D("1")                     # par gross is a LOSS


@pytest.mark.parametrize("p", ["0.05", "0.25", "0.50", "0.75", "0.95"])
def test_A_MAKER_FILL_AT_THE_TOUCH_CANNOT_BE_COMPLETED_BELOW_PAR_AT_ONCE(p):
    """The structural prior, as arithmetic rather than as a hope.

    A rested NO bid at n is a YES ask at (1-n); being lifted means the
    surviving ask is >= (1-n), so the gross pair is >= 1. Restating it as a
    test keeps a future refactor from quietly producing free money.
    """
    n = D(p)
    ask = D("1") - n                       # our own price, still the touch
    book = {"yes": {"bid": str(ask - D("0.04")), "ask": str(ask)},
            "no": {"bid": str(n - D("0.04")), "ask": str(n)}}
    lad = {"readable": True,
           "bids": {"levels": [{"price": str(ask - D("0.04")), "qty": "500"}]},
           "asks": {"levels": [{"price": str(ask), "qty": "500"}]}}
    ex = HC.executable_complement(book, lad, "YES", D("0.01"))
    assert ex["executable"]
    parts = HC.all_in_basis(n, D(ex["vwap"]))
    assert parts["gross"] >= D("1")
    assert parts["all_in"] > D("1")        # fees make equality a loss


# ------------------------------------------------- residual cost basis
def test_THE_RESIDUAL_IS_PRICED_FIFO_NOT_ON_THE_WHOLE_LEG_AVERAGE():
    """What a completion must beat is the cost of the contracts still open."""
    fills = [{"leg": "NO", "qty": "1", "price": "0.40"},
             {"leg": "NO", "qty": "1", "price": "0.60"},
             {"leg": "YES", "qty": "1", "price": "0.50"}]
    leg, qty, cost = HC.residual_basis(fills)
    assert (leg, qty) == ("NO", D("1"))
    assert cost == D("0.60")               # the 0.40 was paired off first
    assert HC.residual_basis([]) is None
    assert HC.residual_basis([{"leg": "NO", "qty": "1", "price": "0.4"},
                              {"leg": "YES", "qty": "1", "price": "0.5"}]) is None


# ----------------------------------------------------------- the hook
def _cycle(tte, book=None, depth=None):
    return {"tte_s": tte, "book": book or BOOK, "depth": depth or LADDER,
            "l5_action": []}


def test_OBSERVING_TAKES_NOTHING_AND_STILL_PRICES_EVERY_RESIDUAL_STATE():
    inv = CP.Inventory()
    inv.apply_fill("NO", D("0.01"), D("0.56"), source="T")
    c = HC.Completer(symbol="S", threshold=None)
    live = {"YES": object()}
    c(cycle=_cycle(200.0), now_ms=D("0"), inv=inv, live=live, fills=[])
    assert c.taker_fills == []
    assert live == {"YES": live["YES"]}          # nothing cancelled
    assert inv.residual() == D("-0.01")          # inventory untouched
    assert len(c.rows) == 1 and c.rows[0]["executable"]
    assert c.rows[0]["complement_leg"] == "YES"


def test_TAKING_FILLS_THE_INVENTORY_AND_STOPS_WORKING_THE_PASSIVE_RUNG():
    """A rung left resting after the position is closed would re-open it."""
    inv = CP.Inventory()
    inv.apply_fill("NO", D("0.01"), D("0.20"), source="T")   # deep in profit
    c = HC.Completer(symbol="S", threshold=D("1.00"))
    live = {"YES": "resting"}
    c(cycle=_cycle(200.0), now_ms=D("0"), inv=inv, live=live, fills=[])
    assert len(c.taker_fills) == 1
    assert c.taker_fills[0]["cancelled_resting_complement"] is True
    assert "YES" not in live
    assert inv.residual() == D("0") and inv.matched() == D("0.01")
    assert c.episodes[-1]["closed_by"] == "TAKER_COMPLETE"


def test_A_DEARER_COMPLEMENT_THAN_THE_THRESHOLD_IS_LEFT_ALONE():
    inv = CP.Inventory()
    inv.apply_fill("NO", D("0.01"), D("0.56"), source="T")   # 0.56+0.44 > 1
    c = HC.Completer(symbol="S", threshold=D("1.00"))
    c(cycle=_cycle(200.0), now_ms=D("0"), inv=inv, live={}, fills=[])
    assert c.taker_fills == []
    assert inv.residual() == D("-0.01")


def test_AN_EPISODE_ENDED_BY_A_PASSIVE_FILL_IS_LABELLED_AS_SUCH():
    inv = CP.Inventory()
    inv.apply_fill("NO", D("0.01"), D("0.56"), source="T")
    c = HC.Completer(symbol="S")
    c(cycle=_cycle(200.0), now_ms=D("0"), inv=inv, live={}, fills=[])
    inv.apply_fill("YES", D("0.01"), D("0.42"), source="T")   # maker COMPLETE
    c(cycle=_cycle(190.0), now_ms=D("0"), inv=inv, live={}, fills=[])
    assert [e["closed_by"] for e in c.episodes] == ["MAKER_COMPLETE_FILLED"]
    assert c.episodes[0]["opened_tte_s"] == 200.0


def test_A_RESIDUAL_THAT_FLIPS_SIDES_IS_TWO_EPISODES_NOT_ONE():
    """Merging them would average two different positions into one basis."""
    inv = CP.Inventory()
    inv.apply_fill("NO", D("0.01"), D("0.56"), source="T")
    c = HC.Completer(symbol="S")
    c(cycle=_cycle(200.0), now_ms=D("0"), inv=inv, live={}, fills=[])
    inv.apply_fill("YES", D("0.02"), D("0.42"), source="T")
    c(cycle=_cycle(190.0), now_ms=D("0"), inv=inv, live={}, fills=[])
    assert [e["closed_by"] for e in c.episodes] == ["RESIDUAL_FLIPPED"]
    c.finish()
    assert c.episodes[-1]["closed_by"] == "WINDOW_ENDED_STILL_RESIDUAL"
    assert c.episodes[-1]["residual_leg"] == "YES"


# ------------------------------------------------- isolation of the arm
def test_THE_REPLAY_WITHOUT_A_HOOK_IS_EXACTLY_THE_MAKER_ONLY_ARM():
    """No hook, no taker -- the deployed path cannot acquire one by import."""
    cycles = [_cycle(280.0), _cycle(270.0)]
    a = AB.replay("S", cycles, [], arm=AB.AB)
    b = AB.replay("S", cycles, [], arm=AB.AB, on_cycle=None)
    assert a == b
    assert a["fills"] == 0


def test_NO_TAKER_PATH_EXISTS_IN_THE_MODULES_THE_LIVE_LANE_IMPORTS():
    """`shadow_soak` reaches ab_arms; ab_arms must not reach the taker.

    CODE ONLY. The last guard of this shape failed on the prose explaining
    it, so the modules are parsed and their docstrings and comments dropped
    before the name is looked for -- a mention in a docstring is
    documentation, an import is a path to execution.
    """
    import ast
    import pathlib
    from src.gemini_execution import shadow_soak as SS

    def code_only(mod):
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef)) and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        return ast.unparse(tree)

    def code_of(text):
        tree = ast.parse(text)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef)) and body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        return ast.unparse(tree)

    for mod in (AB, SS):
        src = code_only(mod)
        assert "hybrid_complete" not in src, mod.__name__
        assert HC.Completer.TAKER_SOURCE not in src, mod.__name__
    # NEGATIVE CONTROL: a guard that can never fire is not a guard. Prose is
    # ignored; a real import is caught.
    assert "hybrid_complete" not in code_of('"""hybrid_complete in prose."""')
    assert "hybrid_complete" in code_of(
        "from src.gemini_execution import hybrid_complete\n")
