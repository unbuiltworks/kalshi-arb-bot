"""Fractional positions are read from the venue's DECIMAL fields.

Observed 2026-11-21 06:23:17Z in cpc-btc-updown-5m-2026-11-21-0620z: the
lane held UP 0.39 / DOWN 0.56 and two steps charged 0.05 contracts as a
FOREIGN position. The venue row carries whole-number `qtyBought`/`qtySold`
next to `qtyBoughtDecimal`/`qtySoldDecimal`; 0 + 1 - 0.95 = 0.05. (The raw
row of that moment was not logged, so the rounding is inferred from the
arithmetic and from the row format the account service documents.)
"""
from decimal import Decimal as D

from src import l5_btc5m_execution as X

M = "cpc-btc-updown-5m-2026-11-21-0620z"


class Venue:
    def __init__(self, rows):
        self.rows = rows

    def open_orders(self):
        return []

    def positions(self):
        return self.rows


def read(rows, own=(D("0"), D("0"))):
    ex = X.Btc5mExecutor.__new__(X.Btc5mExecutor)
    ex._ledger = {"orders": {}, "settled": {}, "inventory": {}}
    ex.transport = Venue(rows)
    ex.owns = lambda c: False
    ex._own_exposure = lambda: {"resting": D(0), "filled": D(0)}
    ex.inventory = lambda m: {
        X.C.UP: {"qty": own[0] if m == M else D(0), "cost": D(0)},
        X.C.DOWN: {"qty": own[1] if m == M else D(0), "cost": D(0)}}
    return X.Btc5mExecutor.account_exposure(ex)


def test_own_fractional_position_is_not_charged_as_foreign():
    r = read([{"marketSlug": M, "qtyBought": "0", "qtySold": "1",
               "qtyBoughtDecimal": "0.3900", "qtySoldDecimal": "0.5600",
               "netPositionDecimal": "-0.1700"}],
             own=(D("0.39"), D("0.56")))
    assert r["unknown"] is False
    assert r["other_position_contracts"] == D("0")
    assert r["other_usd"] == D("0")


def test_a_foreign_fractional_position_is_not_rounded_away():
    r = read([{"marketSlug": "some-other-market", "qtyBought": "0",
               "qtySold": "0", "qtyBoughtDecimal": "0.4000",
               "qtySoldDecimal": "0.0000"}])
    assert r["other_position_contracts"] == D("0.4000")
    assert r["other_usd"] == D("0.4000")


def test_plain_fields_still_read_when_the_decimal_ones_are_absent():
    r = read([{"marketSlug": "some-other-market",
               "qtyBought": "2", "qtySold": "1"}])
    assert r["other_position_contracts"] == D("3")


def test_net_is_still_the_fallback_and_absence_still_refuses():
    r = read([{"marketSlug": "x", "netPositionDecimal": "-0.25"}])
    assert r["other_position_contracts"] == D("0.25")
    r = read([{"marketSlug": "x"}])
    assert r["unknown"] is True
    assert r["unknown_reason"] == "POSITION_QUANTITY_UNREADABLE"
