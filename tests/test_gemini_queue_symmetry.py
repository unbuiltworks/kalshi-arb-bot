"""The queue model must not advantage one leg over the other.

THE DEFECT THIS PINS. `size_ahead` returned None for every NO leg, and
`Quote.__post_init__` turns None into ZERO size ahead. So a NO rung filled
from the front of a queue it never had to clear while a YES rung cleared the
real displayed ladder. Nine consecutive live BTC15m windows ended long NO and
never once long YES, and replaying 59 captured contracts showed every matched
pair in the corpus disappearing once the queue was made symmetric.
"""
from decimal import Decimal as D

from src.gemini_execution import tape_replay as TR

#: contract ladder priced in YES: bid 0.40 x500, ask 0.60 x500
BOOK = {"readable": True,
        "bids": {"levels": [{"price": "0.40", "qty": "500"}]},
        "asks": {"levels": [{"price": "0.60", "qty": "500"}]}}


def _quote(leg, price, mode=TR.QUEUE_SYMMETRIC):
    a = TR.size_ahead(BOOK, leg, price, mode=mode)
    return TR.Quote(leg=leg, price=D(price), qty=D("0.01"), placed_ms=D("0"),
                    ahead=a, queue_known=a is not None, kind="OPEN")


def test_BOTH_LEGS_GET_A_QUEUE_FROM_A_READABLE_LADDER():
    """The regression itself: NO must no longer come back unknown."""
    assert TR.size_ahead(BOOK, "YES", "0.40") == D("500")
    assert TR.size_ahead(BOOK, "NO", "0.40") == D("500")


def test_THE_TRUE_MIRROR_FILLS_BOTH_LEGS_OR_NEITHER():
    """Buying NO at n IS resting a YES ask at (1-n), so the mirror of a YES
    bid at the bid touch is a NO bid at the ASK touch -- not at 1-bid."""
    yes, no = _quote("YES", "0.40"), _quote("NO", "0.40")
    sell = {"type": "sell", "price": "0.40", "amount": "1"}
    buy = {"type": "buy", "price": "0.60", "amount": "1"}
    assert TR.reaches("YES", yes.price, sell)
    assert TR.reaches("NO", no.price, buy)
    # a one-unit print clears neither 500-deep queue
    assert yes.take(D("1"))[0] == D("0")
    assert no.take(D("1"))[0] == D("0")
    # and a print big enough clears both, identically
    yes2, no2 = _quote("YES", "0.40"), _quote("NO", "0.40")
    assert yes2.take(D("600"))[0] == D("0.01")
    assert no2.take(D("600"))[0] == D("0.01")


def test_THE_OLD_ASYMMETRY_IS_UNREACHABLE():
    """Under the pre-correction code this mirror filled NO and not YES."""
    yes, no = _quote("YES", "0.40"), _quote("NO", "0.40")
    assert yes.ahead == no.ahead
    assert yes.queue_known is no.queue_known is True


def test_THE_NO_QUEUE_COMES_FROM_THE_ASK_LADDER_NOT_AN_INVENTED_NO_SIZE():
    """metadata.contract_depth refuses to relabel an ask size as a NO bid
    size. No such number is invented: the asks are counted AS asks, being
    what an incoming BUY actually consumes on its way to us."""
    book = {"readable": True,
            "bids": {"levels": [{"price": "0.40", "qty": "999"}]},
            "asks": {"levels": [{"price": "0.55", "qty": "7"},
                                {"price": "0.60", "qty": "11"},
                                {"price": "0.70", "qty": "13"}]}}
    # NO at 0.45 -> our resting YES ask sits at 0.55; asks at <= 0.55 is 7
    assert TR.size_ahead(book, "NO", "0.45") == D("7")
    # NO at 0.40 -> ask 0.60; asks at <= 0.60 is 7 + 11
    assert TR.size_ahead(book, "NO", "0.40") == D("18")
    # the bid ladder is never consulted for a NO rung
    assert TR.size_ahead(book, "NO", "0.40") != D("999")


def test_UNKNOWN_STAYS_UNKNOWN_FOR_BOTH_LEGS():
    """An unreadable ladder must not advantage either side."""
    for depth in ({"readable": False}, None, {}):
        assert TR.size_ahead(depth, "YES", "0.40") is None
        assert TR.size_ahead(depth, "NO", "0.40") is None


def test_THE_FRONT_OF_QUEUE_UPPER_BOUND_IS_STILL_REPRODUCIBLE():
    """The pre-correction numbers must stay comparable -- but as a labelled
    bound applied to BOTH legs, never as a default on one."""
    assert TR.size_ahead(BOOK, "NO", "0.40", mode=TR.QUEUE_FRONT) == D("0")
    assert TR.size_ahead(BOOK, "YES", "0.40", mode=TR.QUEUE_FRONT) == D("0")
    assert TR.QUEUE_SYMMETRIC == "SYMMETRIC"


def test_THE_LEDGER_DEFAULTS_TO_THE_CORRECTED_MODEL():
    from src.gemini_execution import hypothetical as HY
    assert HY.HypotheticalBook(symbol="S", policy="P").queue_mode \
        == TR.QUEUE_SYMMETRIC


# ------------------------------------------- the corrected evidence cohort

def test_EVERY_WINDOW_AND_HEARTBEAT_STAMPS_THE_EVIDENCE_COHORT():
    """Old biased rows must not be summable with corrected ones.

    Separating them by timestamp means every future reader has to remember
    which day the fill simulator changed. Stamping the cohort makes the
    separation a property of the evidence.
    """
    import inspect
    from src.gemini_execution import shadow_soak as SS
    assert SS.EVIDENCE_COHORT == "CORRECTED_QUEUE_V1"
    win = inspect.getsource(SS.one_window)
    assert '"evidence_cohort": EVIDENCE_COHORT' in win
    assert '"queue_model": getattr(hyp, "queue_mode", None)' in win
    hb = inspect.getsource(SS.CohortHeartbeat.emit)
    assert '"evidence_cohort": EVIDENCE_COHORT' in hb


def test_THE_COHORT_NOTE_SAYS_WHAT_UNSTAMPED_ROWS_MEAN():
    from src.gemini_execution import shadow_soak as SS
    note = SS.EVIDENCE_COHORT_NOTE.lower()
    assert "symmetric" in note and "do not count" in note


def test_THE_RUNNING_LEDGER_IS_THE_CORRECTED_ONE():
    """A cohort stamped CORRECTED_QUEUE_V1 while running the old model would
    be worse than no stamp at all."""
    from src.gemini_execution import hypothetical as HY
    from src.gemini_execution import tape_replay as TR
    assert HY.HypotheticalBook(symbol="S", policy="P").queue_mode \
        == TR.QUEUE_SYMMETRIC
