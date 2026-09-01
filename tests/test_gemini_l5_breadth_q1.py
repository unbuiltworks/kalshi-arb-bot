from __future__ import annotations

import asyncio
from decimal import Decimal as D
from pathlib import Path

from src import gemini_l5_breadth_q1 as m
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base


def test_disarmed_run_has_no_venue_calls(monkeypatch, capsys):
    for name in (m.LIVE_ENV, m.SID_ENV, m.ARM_ENV):
        monkeypatch.delenv(name, raising=False)

    async def forbidden(*args, **kwargs):
        raise AssertionError("venue call while disarmed")

    monkeypatch.setattr(m, "_gemini_terms_ok", forbidden)
    monkeypatch.setattr(m, "_gemini_available_usd", forbidden)
    monkeypatch.setattr(m, "discover_5m", forbidden)
    asyncio.run(m.run())
    out = capsys.readouterr().out
    assert '"stage":"DISARMED"' in out
    assert '"order_entry":false' in out


def test_dual_key_arm(monkeypatch):
    monkeypatch.setenv(m.LIVE_ENV, "YES")
    monkeypatch.setenv(m.SID_ENV, "abc")
    monkeypatch.setenv(m.ARM_ENV, "wrong")
    assert m.armed() is False
    monkeypatch.setenv(m.ARM_ENV, "abc")
    assert m.armed() is True


def test_quote_one_cent_behind_each_top_and_edge_gate():
    model = m.quote_pair(D("0.48"), D("0.50"))
    assert model["yes"] == D("0.47")
    assert model["no"] == D("0.49")
    assert model["edge"] == D("0.02")
    assert m.safe_pair(model)


def test_price_band_fails_closed():
    assert not m.safe_pair(m.quote_pair(D("0.20"), D("0.80")))


def test_client_ids_unique_and_bounded():
    ids = {
        m.client_id("session", asset, side)
        for asset in m.ASSETS
        for side in ("YES", "NO")
    }
    assert len(ids) == 8
    assert all(len(value) <= 36 for value in ids)


def test_q1_support_requires_cent_tick_and_divisible_increment():
    good = base.Event("BTC05M2609010300", "GEMI-X-UP", 9_999_999_999_999, D("1"), D("1"), D("0.01"))
    bad_tick = base.Event("BTC05M2609010300", "GEMI-X-UP", 9_999_999_999_999, D("1"), D("1"), D("0.001"))
    bad_min = base.Event("BTC05M2609010300", "GEMI-X-UP", 9_999_999_999_999, D("2"), D("1"), D("0.01"))
    assert m.q1_supported(good)
    assert not m.q1_supported(bad_tick)
    assert not m.q1_supported(bad_min)


def test_summary_hard_caps_each_market_residual_at_one(monkeypatch):
    specs = [
        {"asset": "BTC", "event": "BTC05M", "symbol": "BTC-UP", "side": "YES", "price": D("0.47"), "id": "y", "row": None},
        {"asset": "BTC", "event": "BTC05M", "symbol": "BTC-UP", "side": "NO", "price": D("0.49"), "id": "n", "row": None},
    ]
    rows = {
        "y": {"filledQuantity": "1"},
        "n": {"filledQuantity": "0"},
    }
    out = m.summarize(specs, rows)
    assert out["markets"][0]["yes_qty"] == "1"
    assert out["markets"][0]["no_qty"] == "0"
    assert out["markets"][0]["residual_qty"] == "1"


def test_source_contains_no_sell_rescue_or_session_wide_cancel():
    source = Path(m.__file__).read_text()
    assert '"side": "SELL"' not in source
    assert "order.cancel_session" not in source
    assert "MARKET" not in source
    assert "rescue" in source.lower()  # documentation says it is forbidden
