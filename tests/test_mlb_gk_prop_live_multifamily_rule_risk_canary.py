from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.gemini_kalshi_execution_shadow import TopOfBook
from src import mlb_gk_prop_live_multifamily_rule_risk_canary as canary

D = Decimal


def _book(*, yes_bid="0.40", yes_ask="0.42", bid_size="3", ask_size="4", ts=1000):
    return TopOfBook(
        venue="x",
        market_id="m",
        ts_ms=ts,
        yes_bid=D(yes_bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(yes_ask),
        yes_ask_size=D(ask_size),
    )


def test_selected_families_fail_closed_on_hits(monkeypatch):
    monkeypatch.setenv(canary.FAMILIES_ENV, canary.TOTAL_BASES + "," + canary.STRIKEOUTS)
    assert canary._families() == frozenset({canary.TOTAL_BASES, canary.STRIKEOUTS})
    monkeypatch.setenv(canary.FAMILIES_ENV, "baseball_player_hits")
    with pytest.raises(ValueError):
        canary._families()


def test_home_run_discovery_extension_is_strict():
    canary._install_home_run_discovery()
    assert canary.adapters.FAMILY_SERIES[canary.HOME_RUNS] == "KXMLBHR"
    assert canary.discovery.SERIES_TO_FAMILY["KXMLBHR"] == canary.HOME_RUNS
    assert canary.discovery.FAMILY_TO_GEMINI_STAT[canary.HOME_RUNS] == "home_runs"
    match = canary.adapters.KALSHI_PLAYER_LINE_RE.search("Kyle Schwarber: 1+ home runs?")
    assert match is not None
    assert canary.adapters.KALSHI_STAT_TO_FAMILY[match.group("stat").lower()] == canary.HOME_RUNS


def test_batter_participation_requires_start_and_plate_appearance(monkeypatch):
    key = ("2026-08-19", ("phi", "wsh"))
    monkeypatch.setattr(canary, "candidate_game", lambda _slug: key)
    row = SimpleNamespace(family=canary.TOTAL_BASES, player="Kyle Schwarber", game_slug="x")
    monkeypatch.setattr(
        canary,
        "participation_proof",
        lambda **_kwargs: SimpleNamespace(
            started=True,
            plate_appearance_recorded=True,
            starting_pitcher=False,
            batter_faced_recorded=False,
            source="MLB",
        ),
    )
    ok, _proof, status = canary._participation_ok(row, {key: {"status": "Live"}})
    assert ok is True
    assert status == "Live"


def test_strikeout_participation_requires_starting_pitcher_and_batter_faced(monkeypatch):
    key = ("2026-08-19", ("phi", "wsh"))
    monkeypatch.setattr(canary, "candidate_game", lambda _slug: key)
    row = SimpleNamespace(family=canary.STRIKEOUTS, player="Pitcher", game_slug="x")
    monkeypatch.setattr(
        canary,
        "participation_proof",
        lambda **_kwargs: SimpleNamespace(
            started=False,
            plate_appearance_recorded=False,
            starting_pitcher=True,
            batter_faced_recorded=True,
            source="MLB",
        ),
    )
    ok, _proof, _status = canary._participation_ok(row, {key: {"status": "Live"}})
    assert ok is True

    monkeypatch.setattr(
        canary,
        "participation_proof",
        lambda **_kwargs: SimpleNamespace(
            started=False,
            plate_appearance_recorded=False,
            starting_pitcher=True,
            batter_faced_recorded=False,
            source="MLB",
        ),
    )
    ok, _proof, _status = canary._participation_ok(row, {key: {"status": "Live"}})
    assert ok is False


def test_payloads_are_one_contract_and_deterministic(monkeypatch):
    monkeypatch.setenv(canary.RUN_ID_ENV, "multi1")
    row = SimpleNamespace(
        gemini_side=SimpleNamespace(side="YES", venue_market_id="GEMI-X"),
        kalshi_side=SimpleNamespace(side="YES", venue_market_id="KX-X"),
    )
    g = _book(yes_bid="0.40", yes_ask="0.41")
    k = _book(yes_bid="0.55", yes_ask="0.56")
    gp, kp = canary._payloads(row, "GEMINI_YES_KALSHI_NO", g, k)
    assert gp["quantity"] == "1"
    assert kp["count"] == "1.00"
    assert gp["clientOrderId"] == "mlbgkm-multi1-g"
    assert kp["client_order_id"] == "mlbgkm-multi1-k"


def test_software_gate_requires_explicit_multi_arm(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    monkeypatch.setenv(canary.ARM_ENV, "NO")
    assert canary._software_authorized(True) is False
    monkeypatch.setenv(canary.ARM_ENV, "YES")
    assert canary._software_authorized(True) is True
    assert canary._software_authorized(False) is False
