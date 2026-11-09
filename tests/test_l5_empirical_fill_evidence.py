from src.l5_empirical_fill_evidence import calibrate_confirmed_windows


def windows():
    rows = []
    for i in range(40):
        kind = i % 4
        rows.append(dict(market_id="market-%s" % i, opportunity_id="entry-1",
                         book_observation_id="book-%s" % i, book_age_s="0.5",
                         up_filled_qty=("1" if kind in (0, 1) else "0"),
                         down_filled_qty=("1" if kind in (0, 2) else "0"),
                         settlement_side=("UP" if i % 2 else "DOWN"),
                         settlement_confirmed=True, fills_venue_confirmed=True,
                         orders_terminal=True, inventory_reconciled=True,
                         fees_attested=True, rebates_attested=True,
                         observation_complete=True, opportunity_predeclared=True))
    return rows


def test_empirical_frequencies_are_descriptive_not_live_authorization():
    r = calibrate_confirmed_windows(windows())
    assert r["eligible"]
    assert r["counts"] == dict(both=10, up_only=10, down_only=10, neither=10)
    assert r["p_both"] == "0.25"
    assert r["production_authorized"] is False


def test_missing_no_fill_cases_cannot_hide_in_denominator():
    r = calibrate_confirmed_windows(windows()[:20], min_windows=30)
    assert r["reason"] == "INSUFFICIENT_WINDOWS"


def test_unattested_fills_refused():
    rows = windows()
    rows[0]["fills_venue_confirmed"] = False
    assert calibrate_confirmed_windows(rows)["reason"] == "UNATTESTED_EVIDENCE"


def test_duplicate_opportunity_refused():
    rows = windows()
    rows[-1]["market_id"] = rows[0]["market_id"]
    assert calibrate_confirmed_windows(rows)["reason"] == "DUPLICATE_OPPORTUNITY"


def test_stale_book_refused():
    rows = windows()
    rows[0]["book_age_s"] = "6.5"
    assert calibrate_confirmed_windows(rows)["reason"] == "STALE_BOOK"


def test_missing_settlement_refused():
    rows = windows()
    rows[0]["settlement_confirmed"] = False
    assert calibrate_confirmed_windows(rows)["reason"] == "UNATTESTED_EVIDENCE"
