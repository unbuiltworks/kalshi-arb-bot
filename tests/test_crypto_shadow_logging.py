from __future__ import annotations

import logging
import sys

from src import gemini_kalshi_opportunity_settlement_risk_shadow as shadow


def test_configure_shadow_logging_routes_normal_telemetry_to_stdout(monkeypatch):
    captured = {}

    def fake_basic_config(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)
    shadow.configure_shadow_logging()

    assert captured["level"] == logging.INFO
    assert captured["stream"] is sys.stdout
    assert captured["force"] is True
    assert "%(levelname)" in captured["format"]
