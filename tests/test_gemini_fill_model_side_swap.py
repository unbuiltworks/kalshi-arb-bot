"""SIDE-SWAP INVERSION: does the fill model treat YES and NO identically?

THE REASON THIS EXISTS. The leading money hypothesis is that the first-fill
LEG predicts completion -- NO first fills never complete, YES first fills
complete half the time. That claim is worthless if the simulator prefers a
side, and this codebase has already been bitten once: until 2026-09-20
`size_ahead` returned None for every NO leg, `Quote.__post_init__` turned None
into ZERO, and a NO rung filled from the front of a queue it never had to
clear. Nine consecutive BTC15m windows ended long NO and never once long YES.

So the audit is a property, not an anecdote. Build a book that is its own
mirror under the complement identity, apply the exact inversion

    leg   YES <-> NO
    book  yes <-> no
    depth bid level at px  <->  ask level at (1 - px)
    print (sell, tp)       <->  (buy, 1 - tp)

and require IDENTICAL behaviour. A difference here is a simulator artifact and
the YES-only result must be withdrawn until it is explained.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import tape_replay as TR


# ----------------------------------------------------------------- mirrors
def mirror_leg(leg):
    return "NO" if str(leg).upper() == "YES" else "YES"


def mirror_price(px):
    return D("1") - D(str(px))


def book_of(yes_bid, yes_ask):
    """A quoted book, with the NO side derived through the identity the
    venue's own `outcome_book` verifies: no_bid == 1 - yes_ask."""
    return {"yes": {"bid": str(yes_bid), "ask": str(yes_ask)},
            "no": {"bid": str(mirror_price(yes_ask)),
                   "ask": str(mirror_price(yes_bid))}}


def mirror_book(b):
    return {"yes": dict(b["no"]), "no": dict(b["yes"])}


def depth_of(bids, asks):
    return {"readable": True,
            "bids": {"levels": [{"price": str(p), "qty": str(q)}
                                for p, q in bids]},
            "asks": {"levels": [{"price": str(p), "qty": str(q)}
                                for p, q in asks]}}


def mirror_depth(d):
    """A bid at px of size q is, to the other leg, an ask at (1 - px)."""
    bids = [(mirror_price(lv["price"]), lv["qty"])
            for lv in d["asks"]["levels"]]
    asks = [(mirror_price(lv["price"]), lv["qty"])
            for lv in d["bids"]["levels"]]
    return depth_of(bids, asks)


def print_of(kind, price, amount, *, tid=None, ms=1000):
    return {"tid": tid, "utc_ms": D(str(ms)), "amount": D(str(amount)),
            "price": str(price), "type": kind}


def mirror_print(p):
    return {**p, "type": ("buy" if p["type"] == "sell" else "sell"),
            "price": str(mirror_price(p["price"]))}


#: a self-mirrored book: yes 0.40/0.60 means no 0.40/0.60 as well
SELF_MIRROR = book_of("0.40", "0.60")
#: 100 at the touch, 200 behind it, on BOTH ladders
SELF_MIRROR_DEPTH = depth_of([("0.40", 100), ("0.39", 200)],
                             [("0.60", 100), ("0.61", 200)])


def test_the_fixture_really_is_its_own_mirror():
    """If the fixture is not symmetric the whole test file proves nothing."""
    assert mirror_book(SELF_MIRROR) == SELF_MIRROR
    assert mirror_depth(SELF_MIRROR_DEPTH) == SELF_MIRROR_DEPTH


# ------------------------------------------------------- primitive: reaches
@pytest.mark.parametrize("px", ["0.05", "0.25", "0.40", "0.50", "0.75",
                                "0.95"])
@pytest.mark.parametrize("tp,amt", [("0.10", 5), ("0.40", 150), ("0.50", 1),
                                    ("0.60", 300), ("0.90", 12)])
def test_reaches_is_invariant_under_the_side_swap(px, tp, amt):
    yes_p = print_of("sell", tp, amt)
    assert TR.reaches("YES", px, yes_p) is TR.reaches(
        "NO", px, mirror_print(yes_p))
    no_p = print_of("buy", tp, amt)
    assert TR.reaches("NO", px, no_p) is TR.reaches(
        "YES", px, mirror_print(no_p))


def test_a_print_in_the_wrong_direction_reaches_neither_leg():
    # a BUY cannot hit our YES bid; a SELL cannot hit our NO bid
    assert TR.reaches("YES", "0.40", print_of("buy", "0.10", 500)) is False
    assert TR.reaches("NO", "0.40", print_of("sell", "0.90", 500)) is False


# ---------------------------------------------------- primitive: size_ahead
@pytest.mark.parametrize("px", ["0.20", "0.39", "0.40", "0.50", "0.60",
                                "0.61", "0.80"])
def test_size_ahead_is_invariant_under_the_side_swap(px):
    a = TR.size_ahead(SELF_MIRROR_DEPTH, "YES", px)
    b = TR.size_ahead(mirror_depth(SELF_MIRROR_DEPTH), "NO", px)
    assert a == b, (px, a, b)


def test_size_ahead_on_an_ASYMMETRIC_book_swaps_with_the_book():
    """The invariance must hold for lopsided books too, or it is an accident
    of the fixture rather than a property of the function."""
    d = depth_of([("0.40", 7), ("0.30", 11)],
                 [("0.60", 913), ("0.75", 2)])
    for px in ("0.25", "0.40", "0.55", "0.70"):
        assert TR.size_ahead(d, "YES", px) == TR.size_ahead(
            mirror_depth(d), "NO", px), px
        assert TR.size_ahead(d, "NO", px) == TR.size_ahead(
            mirror_depth(d), "YES", px), px


def test_an_unreadable_ladder_is_UNKNOWN_on_BOTH_legs_not_zero_on_one():
    """The original defect: None on NO became zero size ahead, so NO filled
    from the front of the queue while YES had to clear the whole ladder."""
    for d in ({"readable": False}, None, {}, {"readable": True}):
        a = TR.size_ahead(d, "YES", "0.40")
        b = TR.size_ahead(d, "NO", "0.40")
        assert a == b, (d, a, b)


def test_the_regression_itself_is_pinned():
    """A deep book must put size in front of BOTH legs. Under the old code
    this returned a positive number for YES and None -> 0 for NO."""
    ahead_yes = TR.size_ahead(SELF_MIRROR_DEPTH, "YES", "0.40")
    ahead_no = TR.size_ahead(SELF_MIRROR_DEPTH, "NO", "0.40")
    assert ahead_yes == D("100") and ahead_no == D("100")


def test_FRONT_OF_QUEUE_remains_symmetric_too():
    for px in ("0.20", "0.40", "0.80"):
        assert TR.size_ahead(SELF_MIRROR_DEPTH, "YES", px,
                             mode=TR.QUEUE_FRONT) == D("0")
        assert TR.size_ahead(SELF_MIRROR_DEPTH, "NO", px,
                             mode=TR.QUEUE_FRONT) == D("0")


# -------------------------------------------------- primitive: Quote.take
def _quote(leg, px, depth, qty="1"):
    ahead = TR.size_ahead(depth, leg, px)
    return TR.Quote(leg=leg, price=D(str(px)), qty=D(qty),
                    placed_ms=D("0"), ahead=ahead,
                    queue_known=ahead is not None, kind="OPEN")


@pytest.mark.parametrize("volume", ["0.5", "50", "100", "100.5", "101",
                                    "500"])
def test_the_queue_eats_the_same_amount_on_either_leg(volume):
    y = _quote("YES", "0.40", SELF_MIRROR_DEPTH)
    n = _quote("NO", "0.40", mirror_depth(SELF_MIRROR_DEPTH))
    assert y.take(D(volume)) == n.take(D(volume))


# --------------------------------------------- the whole rung-to-fill path
def _run(leg, px, book, depth, prints, qty="1"):
    """Rest ONE rung and walk the tape, using only the fill model.

    `HypotheticalBook.consume` re-asks the POLICY after every fill, and the
    policy is not side-symmetric by design -- it decides which side to quote.
    That is a separate question. This isolates the FILL MODEL, which must be.
    """
    q = _quote(leg, px, depth, qty=qty)
    out = []
    for p in prints:
        if not TR.reaches(leg, q.price, p):
            continue
        got, ate = q.take(p["amount"])
        out.append({"got": got, "ate": ate, "price": q.price,
                    "queue_known": q.queue_known})
        if q.filled >= q.qty:
            break
    return {"fills": out, "filled": q.filled, "ahead": q.ahead}


TAPES = [
    [print_of("sell", "0.40", 1)],
    [print_of("sell", "0.40", 99)],
    [print_of("sell", "0.40", 101)],
    [print_of("sell", "0.38", 5000)],
    [print_of("sell", "0.41", 5000)],                    # does not reach
    [print_of("sell", "0.40", 60), print_of("sell", "0.39", 60, ms=1001)],
    [print_of("buy", "0.10", 900)],                      # wrong direction
    [print_of("sell", "0.40", 30), print_of("buy", "0.90", 30, ms=1001),
     print_of("sell", "0.20", 300, ms=1002)],
]


@pytest.mark.parametrize("tape", TAPES)
@pytest.mark.parametrize("px", ["0.25", "0.40", "0.55"])
def test_a_YES_rung_and_its_MIRRORED_NO_rung_fill_identically(tape, px):
    yes = _run("YES", px, SELF_MIRROR, SELF_MIRROR_DEPTH, tape)
    no = _run("NO", px, mirror_book(SELF_MIRROR),
              mirror_depth(SELF_MIRROR_DEPTH), [mirror_print(p) for p in tape])
    assert yes == no, (px, tape, yes, no)


@pytest.mark.parametrize("tape", TAPES)
def test_the_same_holds_on_a_LOPSIDED_book(tape):
    """The real venue is not symmetric. The MODEL still must be: swapping the
    labels and the book together must swap the outcome and nothing else."""
    b = book_of("0.31", "0.53")
    d = depth_of([("0.31", 4), ("0.29", 800)], [("0.53", 260), ("0.55", 9)])
    yes = _run("YES", "0.31", b, d, tape)
    no = _run("NO", "0.31", mirror_book(b), mirror_depth(d),
              [mirror_print(p) for p in tape])
    assert yes == no, (tape, yes, no)


def test_reconcile_places_a_mirrored_rung_in_the_same_queue_position():
    """Placement, not just consumption: the rung must JOIN the same queue."""
    live_y, live_n = {}, {}
    ev_y = TR._reconcile(live_y, [{"leg": "YES", "price": "0.40",
                                   "kind": "OPEN", "qty": "1"}],
                         now_ms=D("0"), depth=SELF_MIRROR_DEPTH,
                         default_qty=D("1"))
    ev_n = TR._reconcile(live_n, [{"leg": "NO", "price": "0.40",
                                   "kind": "OPEN", "qty": "1"}],
                         now_ms=D("0"), depth=mirror_depth(SELF_MIRROR_DEPTH),
                         default_qty=D("1"))
    assert ev_y["placed"] == ev_n["placed"]
    assert live_y["YES"].ahead == live_n["NO"].ahead
    assert live_y["YES"].queue_known == live_n["NO"].queue_known


# ------------------------------------------- the defect, reconstructed
def test_the_ORIGINAL_defect_would_be_CAUGHT_by_this_property():
    """Proof the property has teeth.

    Reconstruct the pre-2026-09-20 behaviour exactly -- `size_ahead` returns
    None for NO, and `Quote` turns None into zero -- and show the mirrored
    rungs then diverge. If this test ever passes with the real function, the
    symmetry tests above are vacuous.
    """
    def old_size_ahead(depth, leg, price, *, mode=TR.QUEUE_SYMMETRIC):
        if str(leg).upper() != "YES":
            return None                      # "YES ladder only" -- the bug
        return TR.size_ahead(depth, leg, price, mode=mode)

    def quote(leg, px, depth):
        ahead = old_size_ahead(depth, leg, px)
        return TR.Quote(leg=leg, price=D(str(px)), qty=D("1"),
                        placed_ms=D("0"), ahead=ahead,
                        queue_known=ahead is not None, kind="OPEN")

    tape = [print_of("sell", "0.40", 1)]          # one contract, at the touch
    y = quote("YES", "0.40", SELF_MIRROR_DEPTH)
    n = quote("NO", "0.40", mirror_depth(SELF_MIRROR_DEPTH))
    yg, _ = y.take(tape[0]["amount"])
    ng, _ = n.take(mirror_print(tape[0])["amount"])

    # THE DEFECT: the NO rung fills on a 1-contract print that the YES rung,
    # sitting behind 100 displayed contracts, does not see at all.
    assert yg == D("0") and ng == D("1")
    assert y.ahead == D("100") and n.ahead is None

    # and the corrected function does NOT do this
    y2 = _quote("YES", "0.40", SELF_MIRROR_DEPTH)
    n2 = _quote("NO", "0.40", mirror_depth(SELF_MIRROR_DEPTH))
    assert y2.take(D("1")) == n2.take(D("1")) == (D("0"), D("1"))
