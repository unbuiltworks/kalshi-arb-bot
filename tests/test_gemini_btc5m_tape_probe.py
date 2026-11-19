"""Regressions for the BTC05M tape probe.

The probe's two jobs are to EXCLUDE the right things. Both exclusions were
found by measurement on 2026-11-19 and both change the verdict by more than an
order of magnitude, so both are pinned.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from src.gemini_btc5m_tape_probe import (
    DEGENERATE_SPREAD,
    LABEL,
    observe,
    summarize,
)

NOW = datetime(2026, 11, 19, 12, 26, tzinfo=timezone.utc)


def _event(ticker, *, series="BTC05M", volume="0", expiry=None,
           bid=None, ask=None):
    exp = expiry or (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    contracts = []
    if bid is not None and ask is not None:
        contracts = [{"instrumentSymbol": "GEMI-%s-UP" % ticker,
                      "prices": {"bestBid": bid, "bestAsk": ask}}]
    return {"ticker": ticker, "series": series, "volume": volume,
            "expiryDate": exp, "status": "active", "contracts": contracts}


def _fold(events, now=NOW, series=("BTC05M",)):
    w, s = {}, []
    observe(events, series, w, s, now)
    return w, s


# ------------------------------------------------- unstarted windows excluded

def test_unstarted_windows_are_excluded_not_averaged_in():
    """The artefact that would have understated the tape 6x on the first read:
    11 of 13 listed windows had not started and read $0."""
    future = (NOW + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    events = [_event("DONE", volume="54.72"),
              _event("SOON", volume="0", expiry=future),
              _event("LATER", volume="0", expiry=future)]
    w, s = _fold(events)
    got = summarize(w, s, NOW)
    assert got["completed_windows_observed"] == 1
    assert got["unstarted_windows_excluded"] == 2
    assert got["window_volume_usd_mean"] == "54.72"


def test_two_completed_windows_average_both():
    events = [_event("A", volume="54.72"), _event("B", volume="25.86")]
    w, s = _fold(events)
    got = summarize(w, s, NOW)
    assert got["completed_windows_observed"] == 2
    assert D(got["window_volume_usd_mean"]) == (D("54.72") + D("25.86")) / D("2")


def test_no_completed_windows_reports_none_not_zero():
    """A measurement nobody made is not a measurement of zero."""
    future = (NOW + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    w, s = _fold([_event("SOON", expiry=future)])
    got = summarize(w, s, NOW)
    assert got["completed_windows_observed"] == 0
    assert got["window_volume_usd_mean"] is None
    assert got["window_volume_usd_median"] is None


# ------------------------------------------------- degenerate books excluded

def test_one_sided_books_are_excluded_from_the_spread():
    """A book with nothing on one side quoted 0.01/0.99 is not a 98c spread."""
    events = [_event("A", bid="0.50", ask="0.51"),
              _event("B", bid="0.50", ask="0.51"),
              _event("C", bid="0.01", ask="0.99")]
    w, s = _fold(events)
    got = summarize(w, s, NOW)
    assert got["spread_observations"] == 3
    assert got["spread_degenerate_excluded"] == 1
    assert D(got["touch_spread_median"]) == D("0.01")


def test_the_outlier_window_does_not_become_the_spread():
    """2026-11-19: one window showed 0.43/0.58 and 15 showed 0.50/0.51.
    Reporting the outlier would overstate gross margin fifteenfold."""
    events = [_event("W%d" % i, bid="0.50", ask="0.51") for i in range(15)]
    events.append(_event("OUT", bid="0.43", ask="0.58"))
    w, s = _fold(events)
    got = summarize(w, s, NOW)
    assert D(got["touch_spread_median"]) == D("0.01")
    assert D(got["touch_spread_p75"]) == D("0.01")


def test_degenerate_threshold_is_half_a_dollar():
    assert DEGENERATE_SPREAD == D("0.5")
    events = [_event("A", bid="0.20", ask="0.69"),   # 0.49 -> kept
              _event("B", bid="0.20", ask="0.71")]   # 0.51 -> excluded
    w, s = _fold(events)
    got = summarize(w, s, NOW)
    assert got["spread_degenerate_excluded"] == 1


def test_a_crossed_or_flat_book_contributes_no_spread():
    events = [_event("A", bid="0.50", ask="0.50"),
              _event("B", bid="0.52", ask="0.50")]
    w, s = _fold(events)
    assert s == []
    assert summarize(w, s, NOW)["touch_spread_median"] is None


# ------------------------------------------------------------------ plumbing

def test_other_series_are_ignored():
    w, s = _fold([_event("X", series="ETH05M", volume="99", bid="0.1", ask="0.9")])
    assert w == {}


def test_repeated_polls_keep_the_latest_volume_and_accumulate_spreads():
    """Volume is a running total, so the last read wins; spreads are samples."""
    w, s = {}, []
    observe([_event("A", volume="38.96", bid="0.50", ask="0.51")],
            ("BTC05M",), w, s, NOW)
    observe([_event("A", volume="54.72", bid="0.50", ask="0.51")],
            ("BTC05M",), w, s, NOW)
    assert w["A"]["volume"] == "54.72"
    assert len(s) == 2


def test_summary_declares_itself_read_only():
    got = summarize(*_fold([_event("A", volume="1")]), NOW)
    assert got["LABEL"] == LABEL
    assert got["read_only"] is True
    assert got["order_entry"] is False
    assert got["credentials_used"] is False


def test_probe_imports_no_credential_or_order_surface():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("src/gemini_btc5m_tape_probe.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    for banned in ("src.gemini_prediction_auth", "os", "hmac", "hashlib",
                   "base64"):
        assert banned not in mods, banned

    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                called.add(f.id.lower())
            elif isinstance(f, ast.Attribute):
                called.add(f.attr.lower())
    for verb in ("submit", "cancel", "amend", "private_rest_request"):
        assert not any(verb in c for c in called), verb
