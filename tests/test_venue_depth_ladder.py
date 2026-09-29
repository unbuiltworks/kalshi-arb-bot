"""P0: the completion walk gets the VENUE's depth, and never a synthesized one.

`SUBSCRIPTION_TYPE_MARKET_DATA` carries 24-29 levels per side (measured on
the live venue 2026-09-29). `apply_events` collapses that to one level, so
the frozen theta walk -- built to walk depth -- had never been given any.
These tests pin that the ladder is venue-observed, that nothing is invented
when the venue has not spoken, and that no completion economics moved.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_book_feed as F
from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E

#: a real frame, trimmed, exactly as the venue sent it on 2026-09-29
VENUE_FRAME = {
    "marketData": {
        "marketSlug": "cpc-btc-updown-15m-2026-09-29-1345z",
        "bids": [{"px": {"value": "0.4100", "currency": "USD"}, "qty": "1831.8200"},
                 {"px": {"value": "0.4000", "currency": "USD"}, "qty": "263.0000"},
                 {"px": {"value": "0.3900", "currency": "USD"}, "qty": "5214.0000"},
                 {"px": {"value": "0.3800", "currency": "USD"}, "qty": "1922.0000"}],
        "offers": [{"px": {"value": "0.4200", "currency": "USD"}, "qty": "143.3500"},
                   {"px": {"value": "0.4300", "currency": "USD"}, "qty": "697.0000"},
                   {"px": {"value": "0.4400", "currency": "USD"}, "qty": "191.7600"}],
        "state": "MARKET_STATE_OPEN",
    }
}
SLUG = "cpc-btc-updown-15m-2026-09-29-1345z"


def feed():
    return F.AuthenticatedBookFeed(SLUG, clock=lambda: 1000.0)


# ---- the ladder is the venue's -----------------------------------------

def test_every_level_comes_from_the_frame():
    f = feed()
    f.ingest(VENUE_FRAME)
    lad = f.ladder
    assert lad["bids"] == [["0.4100", "1831.8200"], ["0.4000", "263.0000"],
                           ["0.3900", "5214.0000"], ["0.3800", "1922.0000"]]
    assert lad["offers"] == [["0.4200", "143.3500"], ["0.4300", "697.0000"],
                             ["0.4400", "191.7600"]]


def test_more_than_one_level_reaches_the_walk():
    """The whole point: the walk can now consume depth below the touch."""
    f = feed()
    f.ingest(VENUE_FRAME)
    assert len(S.buy_levels(f.ladder, "up")) == 3
    assert len(S.buy_levels(f.ladder, "down")) == 4


def test_nothing_is_synthesized_when_the_venue_has_not_spoken():
    """A fabricated one-level ladder would let the walk admit a completion
    against depth the venue never showed us."""
    f = feed()
    assert f.ladder is None
    assert f.ladder_health()["source"] == "NONE"
    assert f.ladder_health()["synthesized"] is False


def test_a_trade_only_frame_creates_no_ladder():
    f = feed()
    f.ingest({"trade": {"marketSlug": SLUG, "price": {"value": "0.42"},
                        "quantity": "10"}})
    assert f.ladder is None


def test_another_markets_depth_is_never_adopted():
    f = feed()
    other = {"marketData": dict(VENUE_FRAME["marketData"],
                                marketSlug="cpc-btc-updown-15m-OTHER")}
    f.ingest(other)
    assert f.ladder is None, "depth from a different market was adopted"


def test_unparseable_and_zero_size_levels_are_dropped_not_guessed():
    f = feed()
    f.ingest({"marketData": {"marketSlug": SLUG, "bids": [
        {"px": {"value": "0.41"}, "qty": "10"},
        {"px": {"value": "0.40"}, "qty": "0"},        # no size
        {"px": {"value": "1.50"}, "qty": "5"},        # not a probability
        {"px": {"value": "abc"}, "qty": "5"},         # unreadable
        {"nope": 1},                                  # wrong shape
    ]}})
    assert f.ladder == {"bids": [["0.41", "10"]]}


def test_provenance_is_reported():
    f = feed()
    f.ingest(VENUE_FRAME)
    h = f.ladder_health()
    assert h["source"] == "VENUE_WS_MARKET_DATA"
    assert h["depth_frames"] == 1
    assert h["bid_levels"] == 4 and h["offer_levels"] == 3
    assert h["synthesized"] is False


def test_depth_refreshes_with_each_frame():
    f = feed()
    f.ingest(VENUE_FRAME)
    f.ingest({"marketData": {"marketSlug": SLUG,
                             "bids": [{"px": {"value": "0.44"}, "qty": "7"}],
                             "offers": [{"px": {"value": "0.45"}, "qty": "9"}]}})
    assert f.ladder == {"bids": [["0.44", "7"]], "offers": [["0.45", "9"]]}
    assert f.ladder_health()["depth_frames"] == 2


# ---- completion economics are untouched --------------------------------

def test_the_frozen_criterion_is_unchanged_by_this_wiring():
    assert S.THETA_TAKER == D("0.0695")
    assert E.THETA == D("0.96")
    assert E.RESIDUAL_CAP_K == D(2)
    assert E.VARIANT == "B"
    assert float(E.HOLD_S) == 60.0


def test_depth_only_changes_HOW_MUCH_not_WHETHER():
    """Same theta, same fee model. Depth lets the walk fill more of `want`
    at prices it already admits; it cannot admit a price it would refuse."""
    f = feed()
    f.ingest(VENUE_FRAME)
    lv = S.buy_levels(f.ladder, "up")
    basis, theta = D("0.30"), D("0.96")
    n, cost, fee, _ = S.marginal_complete(lv, D(500), basis, theta)
    # every admitted level satisfies the frozen inequality, individually
    worst = E.worst_admitted_price(lv, D(500), basis, theta)
    assert worst is not None
    px = D(str(worst))
    assert basis + px + S.THETA_TAKER * px * (D(1) - px) <= theta
    assert n > D(143)          # more than the touch alone could give


def test_one_level_input_still_behaves_as_before():
    """A venue that shows only the touch yields exactly the old behaviour."""
    f = feed()
    f.ingest({"marketData": {"marketSlug": SLUG,
                             "bids": [{"px": {"value": "0.41"}, "qty": "100"}],
                             "offers": [{"px": {"value": "0.42"}, "qty": "50"}]}})
    assert S.buy_levels(f.ladder, "up") == [["0.42", "50"]]


def test_no_ladder_admits_no_completion():
    """The engine's own guard, unchanged, is what decides here."""
    import inspect
    src = inspect.getsource(E.NativeEngine.on_book)
    assert "S.buy_levels(self.ladder, comp) if self.ladder else []" in src
    n, _, _, _ = S.marginal_complete([], D(20), D("0.30"), D("0.96"))
    assert n == 0


def test_touch_ladder_is_no_longer_the_feeds_ladder_source():
    """STRUCTURAL: the synthetic builder must not feed the engine."""
    import inspect
    src = inspect.getsource(F.AuthenticatedBookFeed.ladder.fget)
    assert "touch_ladder" not in src
    assert "venue_ladder" in src
