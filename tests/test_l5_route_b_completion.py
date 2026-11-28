"""ROUTE B, PROVEN BEFORE IT IS MODIFIED (#208 item 2).

The owner's instruction was explicit: prove the existing controller works
before changing it. `src/l5_book_completion.py` already decouples the
completion decision from L5's action arrival -- the runner's own tick calls
it every `DECIDE_EVERY_S` -- and it is enabled in production. These tests
drive the REAL decision function against the REAL books recorded in the
seven reconstructed windows, plus the adversarial cases those windows do not
contain.

NO FILL IS CREDITED ANYWHERE HERE. A `COMPLETE` is a decision to rest a
quote; whether it fills is a queue question this source cannot answer.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "analysis", "l5", "issue208"))
os.environ.setdefault("L5_BOOK_COMPLETION", "1")

from src import l5_book_completion as BC          # noqa: E402
import route_b_replay as RB                       # noqa: E402

#: a real book from the 0200z presubmit, used where a test needs a book
#: that is fresh and readable but not the window under test.
FRESH_BOOK = {"bid": "0.3800", "ask": "0.3900", "spread": "0.01",
              "stream_age_s": 0.02, "age_s": 0.02,
              "observed_t": 1792806000.0, "last_update_t": 1792806000.0,
              "feed_source": "authenticated_ws"}


def base(**kw):
    """The deployed call shape, with one held leg and nothing opposite."""
    args = dict(market="cpc-btc-updown-15m-2026-10-24-0200z",
                held_side="UP", held_qty="1.00", held_cost_usd="0.350000",
                deficient_qty="0", deficient_cost_usd="0",
                book=dict(FRESH_BOOK), now_t=1792806000.0,
                window_start=1792805880.0, resting=(), tick="0.01",
                min_qty="0.01", depth_ticks=BC.completion_depth_ticks(),
                residual_budget_usd="1.00", submissions_left=2,
                outstanding_qty="0", last_decide_t=None)
    args.update(kw)
    return BC.decide(**args)


# =====================================================================
# 1. IT ASKS, AND IT ASKS ON THE REAL BOOKS.
# =====================================================================

@pytest.fixture(scope="module")
def replayed():
    return RB.replay()


def test_it_asks_to_complete_every_reconstructed_window(replayed):
    """Seven windows, seven real books, seven priced legs."""
    assert len(replayed) == 7
    for r in replayed:
        assert r["action"] == "COMPLETE", r
        assert r["leg_side"] and r["leg_px"], r
        assert r["leg_side"] != r["held_side"]          # the MISSING side


def test_every_leg_it_would_rest_beats_carrying_the_residual(replayed):
    """The economics, not the fill count. Carrying an unmatched leg is
    measured at -0.0236/contract; a pair is worth having only above that."""
    for r in replayed:
        edge = D(r["route_b_edge"])                     # per contract, gross
        assert edge > RB.RESIDUAL_PER_CONTRACT, r
        assert edge > 0, r


def test_it_quotes_at_the_touch_which_costs_more_than_resting_behind(replayed):
    """`completion_depth_ticks()` is 0, so ROUTE B rests AT the touch. That
    is a more fillable price and a dearer pair, and the comparison is the
    whole trade-off -- it must not be presented as a free improvement."""
    assert BC.completion_depth_ticks() == 0
    dearer = 0
    for r in replayed:
        if not r["attempted_pair"]:
            continue
        if D(r["route_b_pair"]) > D(r["attempted_pair"]):
            dearer += 1
    assert dearer == 7                                  # every one of them


def test_two_windows_clear_only_through_the_rescue_branch(replayed):
    """0100z and 0145z imply a 0.98 pair, above the 0.96 opening ceiling.
    They are permitted because completing beats carrying, inside the
    residual budget -- not because the ceiling was raised."""
    above = [r for r in replayed if D(r["route_b_pair"]) > D("0.96")]
    assert len(above) == 2
    assert {r["reason"] for r in above} == {"RISK_REDUCING_COMPLETION"}
    for r in above:
        assert D(r["route_b_pair"]) == D("0.98")


# =====================================================================
# 2. THE CADENCE. Bounded re-decision is what replaces waiting for L5.
# =====================================================================

def test_it_refuses_inside_the_cadence_and_decides_at_it():
    assert BC.DECIDE_EVERY_S == 5.0
    t = 1792806000.0
    inside = base(now_t=t, last_decide_t=t - (BC.DECIDE_EVERY_S - 0.1))
    assert inside["action"] == "HOLD"
    assert inside["reason"] == "DECIDE_COOLDOWN"
    at = base(now_t=t, last_decide_t=t - BC.DECIDE_EVERY_S)
    assert at["reason"] != "DECIDE_COOLDOWN"
    assert at["action"] == "COMPLETE"


def test_the_cooldown_refusal_still_reports_the_book_it_held():
    """So a cooldown can be told apart from a decline on the economics."""
    t = 1792806000.0
    rec = base(now_t=t, last_decide_t=t - 1.0)
    assert rec["reason"] == "DECIDE_COOLDOWN"
    assert rec.get("market") and rec.get("held_side") == "UP"


# =====================================================================
# 3. IT FAILS CLOSED, AND HOLD IS THE SAFE DIRECTION.
# =====================================================================

def test_an_unreadable_book_age_declines_rather_than_guesses():
    bk = dict(FRESH_BOOK)
    bk.pop("stream_age_s")
    rec = base(book=bk)
    assert rec["action"] == "HOLD" and rec["reason"] == "BOOK_AGE_UNKNOWN"


def test_a_book_with_no_prices_declines():
    rec = base(book={"stream_age_s": 0.02})
    assert rec["action"] == "HOLD"
    assert rec["reason"] not in ("COMPLETE", "REPRICE")


def test_nothing_to_complete_when_both_sides_hold_the_same():
    rec = base(deficient_qty="1.00", deficient_cost_usd="0.600000")
    assert rec["action"] == "HOLD"
    assert rec["reason"] == "ALREADY_MATCHED"


# =====================================================================
# 4. ORDER-SLOT CONTENTION AND THE RESERVATION. Two slots, two sides.
# =====================================================================

def test_no_submission_budget_means_no_order():
    rec = base(submissions_left=0)
    assert rec["action"] == "HOLD"
    assert "SUBMISSION" in rec["reason"] or "BUDGET" in rec["reason"], rec


def test_contracts_already_working_are_not_ordered_twice():
    """The reservation. 1.00 needed and 1.00 already outstanding on the
    deficient side leaves nothing to ask for."""
    rec = base(outstanding_qty="1.00")
    assert rec["action"] == "HOLD", rec
    assert rec["reason"] != "COMPLETE"


def test_a_partial_reservation_asks_only_for_the_remainder():
    rec = base(held_qty="1.00", outstanding_qty="0.60")
    assert rec["action"] == "COMPLETE", rec
    assert D(str(rec["leg"]["qty"])) == D("0.40"), rec["leg"]


# =====================================================================
# 5. THE PRICE IT RESTS MUST BE A MAKER PRICE.
# =====================================================================

def test_the_leg_it_would_rest_never_crosses(replayed):
    """Post-only by construction: an UP leg rests at or below the bid, a
    DOWN leg's wire price at or above the ask."""
    for r in replayed:
        px = D(r["leg_px"])
        assert D(0) < px < D(1), r
    rec = base()
    leg = rec["leg"]
    bid, ask = D(FRESH_BOOK["bid"]), D(FRESH_BOOK["ask"])
    if leg["side"] == "UP":
        assert D(str(leg["price"])) <= bid
    else:
        assert (D(1) - D(str(leg["price"]))) >= ask


# =====================================================================
# 6. THE EXPENSIVE COMPLEMENT. Faster scheduling must not buy bad pairs.
# =====================================================================

def test_a_complement_that_loses_against_carrying_is_refused():
    """The case faster scheduling cannot fix, and must not paper over.

    MIND THE CONVENTION. The missing side's own price is `bid` for UP and
    `1 - ask` for DOWN, so a HIGH yes price makes DOWN CHEAP. An earlier
    version of this test used a 0.72/0.73 book against a held UP leg and
    called it expensive; that is a 0.35 + 0.27 = 0.62 pair, and COMPLETE
    was the right answer.

    To make the complement genuinely dear the yes price has to fall: held
    UP at 0.35 against a 0.29/0.30 book puts DOWN's own price at 0.70 and
    the pair at 1.05, which locks in a loss rather than carrying the leg at
    the measured -0.0236/contract.
    """
    bk = dict(FRESH_BOOK, bid="0.2900", ask="0.3000")
    rec = base(book=bk, held_side="UP", held_cost_usd="0.350000")
    assert D("0.35") + (D(1) - D(bk["ask"])) == D("1.05")      # the premise
    assert rec["action"] != "COMPLETE", rec
    assert rec["action"] in ("HOLD", "REPRICE")


def test_the_dear_direction_is_the_one_the_convention_hides():
    """Both sides of the same book, so the asymmetry is explicit and a
    future reader cannot repeat the mistake above."""
    cheap_down = dict(FRESH_BOOK, bid="0.7200", ask="0.7300")
    held_up = base(book=cheap_down, held_side="UP",
                   held_cost_usd="0.350000")
    assert held_up["action"] == "COMPLETE"                  # pair 0.62
    assert D(held_up["leg"]["price"]) == D(1) - D("0.7300")
    held_down = base(book=cheap_down, held_side="DOWN",
                     held_cost_usd="0.350000")
    # UP's own price is the bid, 0.72, so this pair is 1.07
    assert held_down["action"] != "COMPLETE", held_down


def test_no_residual_budget_means_the_rescue_branch_cannot_fire():
    """The rescue is what permits a pair above the opening ceiling. Without
    an owner budget to charge it against, it must not be available."""
    bk = dict(FRESH_BOOK, bid="0.6300", ask="0.6400")
    with_budget = base(book=bk, held_side="DOWN", held_cost_usd="0.350000",
                       residual_budget_usd="1.00")
    without = base(book=bk, held_side="DOWN", held_cost_usd="0.350000",
                   residual_budget_usd=None)
    assert with_budget["action"] == "COMPLETE"
    assert D(with_budget["leg"]["price"]) + D("0.35") > D("0.96")
    assert without["action"] != "COMPLETE", without


# =====================================================================
# 7. WHAT IS ALREADY RESTING. Idempotence, downward-only repricing, and
#    the restart / uncertain-ACK case that turned out to be a defect.
# =====================================================================

def test_a_quote_already_resting_at_or_better_is_not_duplicated():
    """Idempotence judged against the venue's book, not our own memory."""
    rec = base(resting=({"side": "DOWN", "price": "0.9900",
                         "market": "cpc-btc-updown-15m-2026-10-24-0200z"},))
    assert rec["action"] == "HOLD"
    assert rec["reason"] == "ALREADY_RESTING_AT_OR_BETTER"


def test_repricing_only_ever_goes_cheaper_and_names_what_it_replaces():
    rec = base(held_side="DOWN", held_cost_usd="0.350000",
               resting=({"side": "UP", "price": "0.0100",
                         "market": "cpc-btc-updown-15m-2026-10-24-0200z"},))
    assert rec["action"] == "HOLD", rec        # ours is dearer -> hold
    dear = base(held_side="DOWN", held_cost_usd="0.350000",
                resting=({"side": "UP", "price": "0.9900",
                          "market": "cpc-btc-updown-15m-2026-10-24-0200z"},))
    assert dear["action"] == "REPRICE"
    assert dear["reason"] == "CHEAPER_THAN_RESTING"
    assert D(dear["leg"]["price"]) < D("0.9900")
    assert dear["replaces_resting_own"] == "0.9900"


@pytest.mark.parametrize("held,want", [("UP", "DOWN"), ("DOWN", "UP")])
@pytest.mark.parametrize("price", [None, "", "n/a"])
def test_an_unreadable_resting_price_fails_closed_on_both_sides(held, want,
                                                                price):
    """THE DEFECT THIS FIXES, in the restart / uncertain-ACK case.

    The read was `min((_d(r.get("price")) or D(9)) for r in mine)`. The
    comparison happens in OWN terms and the conversion is side-dependent,
    so the 9 sentinel was dear only one way round:

        want UP    rest_own = 9      own >= 9  False -> REPRICE
        want DOWN  rest_own = 1 - 9  own >= -8 True  -> HOLD

    A row whose price could not be read therefore refused safely when the
    missing side was DOWN and, when it was UP, cancelled and replaced an
    order of unknown price -- forfeiting its queue position, possibly
    chasing upward, and recording CHEAPER_THAN_RESTING with nothing behind
    it. Both sides now decline, because the order already exists at the
    venue: leaving it resting may fill, replacing it destroys a position we
    cannot evaluate.
    """
    rec = base(held_side=held, held_cost_usd="0.350000",
               resting=({"side": want, "price": price,
                         "market": "cpc-btc-updown-15m-2026-10-24-0200z"},))
    assert rec["action"] == "HOLD", rec
    assert rec["reason"] == "RESTING_PRICE_UNREADABLE", rec
    assert "leg" not in rec or not rec.get("ok")


def test_one_unreadable_row_among_readable_ones_still_fails_closed():
    """The minimum over a set containing an unknown is not known."""
    m = "cpc-btc-updown-15m-2026-10-24-0200z"
    rec = base(resting=({"side": "DOWN", "price": "0.5000", "market": m},
                        {"side": "DOWN", "price": "x", "market": m}))
    assert rec["reason"] == "RESTING_PRICE_UNREADABLE"
    assert rec["unreadable_rows"] == 1 and rec["resting_rows"] == 2


# =====================================================================
# 8. TWO SLOTS, TWO SIDES. The completion needs one of them.
# =====================================================================

def test_an_opening_quote_on_the_held_side_does_not_block_the_completion():
    """Order-slot coordination: a resting order on the side we ALREADY hold
    is not the completion and must not be mistaken for it."""
    m = "cpc-btc-updown-15m-2026-10-24-0200z"
    rec = base(resting=({"side": "UP", "price": "0.3800", "market": m},))
    assert rec["action"] == "COMPLETE", rec
    assert rec["leg"]["side"] == "DOWN"


def test_a_resting_order_in_another_market_does_not_block_it():
    rec = base(resting=({"side": "DOWN", "price": "0.0100",
                         "market": "some-other-market"},))
    assert rec["action"] == "COMPLETE", rec


def test_the_last_slot_is_still_enough_for_the_completion():
    assert base(submissions_left=1)["action"] == "COMPLETE"
    assert base(submissions_left=0)["action"] == "HOLD"
