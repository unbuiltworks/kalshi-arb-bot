"""The price band is a bound on the price we QUOTE, not on the touch.

Live, 2026-11-11 19:00Z. Holding UP 0.30 at 0.47 with completions resting
at the 0.98 target, the DOWN completion belonged at 0.51. From T+534 the
DOWN touch was 0.97-0.99, above the engine's 0.95 band, and
`complement_order` refused PRICE_OUT_OF_BAND on the TOUCH -- so the 0.51
order, well inside the band, was not re-placed for the last 66 seconds
before the cancel time. It could not have filled there; the refusal simply
named the wrong price.

The band still applies to what is actually quoted: off, low touches and an
out-of-band target are refused exactly as before.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

TICK = D("0.01")
SWITCHES = (TS.INVENTORY_RECOVERY_ENV, TS.COMPLETION_RESTS_AT_CEILING_ENV,
            TS.COMPLETION_MAX_PAIR_ENV, TS.PARTIAL_COMPLETION_BASIS_ENV)


def comp(side, held_side, hq, hc, bid, ask, **over):
    kw = dict(side=side, qty=D(hq), cost_px=D(hc) / D(hq), held_qty=D(hq),
              held_cost_usd=D(hc), deficient_qty=D(0),
              deficient_cost_usd=D(0), residual_budget_usd=D("1.00"),
              bid=D(bid), ask=D(ask), tick=TICK, min_qty=D("0.01"),
              depth_ticks=0, book_age_s=D("0.5"), gap_suspected=False,
              market="m", held_side=held_side, bid_shares=D(500),
              ask_shares=D(500))
    kw.update(over)
    return TS.complement_order(**kw)


#: 19:00Z: UP 0.30 bought at 0.47; the YES book late in the window
HELD = ("DOWN", "UP", "0.30", "0.1410")
LATE = ("0.02", "0.03")                    # DOWN touch 0.97


@pytest.fixture
def off(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def resting(monkeypatch, off):
    monkeypatch.setenv(TS.INVENTORY_RECOVERY_ENV, "ON")
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "0.98")


def test_off_the_refusal_is_exactly_as_it_was(off):
    r = comp(*HELD, *LATE)
    assert (r["ok"], r["reason"]) == (False, "PRICE_OUT_OF_BAND")
    assert D(r["price"]) == D("0.97")


def test_1900z_the_completion_rests_at_its_own_price(resting):
    r = comp(*HELD, *LATE)
    assert r["ok"] is True
    leg = r["leg"]
    assert (leg["side"], D(leg["price"])) == ("DOWN", D("0.51"))
    assert D("0.47") + D(leg["price"]) == D("0.98")
    assert TS.MIN_PRICE <= D(leg["price"]) <= TS.MAX_PRICE


@pytest.mark.parametrize("book", [("0.02", "0.03"), ("0.01", "0.02"),
                                  ("0.03", "0.04")])
def test_any_touch_above_the_band_rests_at_the_same_target(resting, book):
    r = comp(*HELD, *book)
    assert r["ok"] is True and D(r["leg"]["price"]) == D("0.51")


def test_the_mirror_case_holding_down(resting):
    # DOWN 0.30 bought at 0.47; UP has run to 0.97
    r = comp("UP", "DOWN", "0.30", "0.1410", "0.97", "0.98")
    assert r["ok"] is True and D(r["leg"]["price"]) == D("0.51")


def test_the_dollar_ceiling_still_binds(resting, monkeypatch):
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "1.20")
    r = comp(*HELD, *LATE)
    assert r["ok"] is True
    assert D("0.47") + D(r["leg"]["price"]) <= D("1.00")


@pytest.mark.parametrize("paid", ["0.0060", "0.0150", "0.0300", "0.1410",
                                  "0.2700"])
def test_whatever_is_quoted_is_inside_the_band(resting, paid):
    """Held very cheaply, the target (0.98 - paid) can itself sit above the
    band; then the quote is the dearest in-band price the ceilings allow,
    or the refusal stands. It is never a price outside the band."""
    r = comp("DOWN", "UP", "0.30", paid, *LATE)
    if r["ok"]:
        px = D(r["leg"]["price"])
        assert TS.MIN_PRICE <= px <= TS.MAX_PRICE
        assert D(paid) / D("0.30") + px <= D("0.98")
    else:
        assert r["reason"] == "PRICE_OUT_OF_BAND"


def test_a_touch_below_the_band_is_still_refused(resting):
    # the completing side trades at 0.03: that IS the price we would pay
    r = comp("DOWN", "UP", "0.30", "0.1410", "0.97", "0.98")
    assert (r["ok"], r["reason"]) == (False, "PRICE_OUT_OF_BAND")


def test_inside_the_band_nothing_changes(resting, off, monkeypatch):
    a = comp(*HELD, "0.40", "0.41")
    monkeypatch.setenv(TS.INVENTORY_RECOVERY_ENV, "ON")
    monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "0.98")
    b = comp(*HELD, "0.40", "0.41")
    assert a.get("reason") != "PRICE_OUT_OF_BAND"
    assert b.get("reason") != "PRICE_OUT_OF_BAND"
