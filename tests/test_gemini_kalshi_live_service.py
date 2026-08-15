from decimal import Decimal
from pathlib import Path

import pytest

import src.gemini_kalshi_live_service as service

D = Decimal


def test_live_service_defaults_are_first_probe_bounds(monkeypatch):
    for name in (
        "LIVE_PROBE_MAX_ATTEMPTS",
        "LIVE_PROBE_MIN_PERSISTENCE_MS",
        "LIVE_PROBE_WATCH_SECONDS",
        "LIVE_PROBE_MAX_CANDIDATE_AGE_MS",
        "LIVE_PROBE_MIN_EXPIRY_REMAINING_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert service._env_int("LIVE_PROBE_MAX_ATTEMPTS", 1, minimum=1) == 1
    assert service._env_int("LIVE_PROBE_MIN_PERSISTENCE_MS", 10, minimum=0) == 10
    assert service._env_float("LIVE_PROBE_WATCH_SECONDS", 900.0, minimum=1.0) == 900.0
    assert service._env_int("LIVE_PROBE_MAX_CANDIDATE_AGE_MS", 100, minimum=1) == 100
    assert service._env_float("LIVE_PROBE_MIN_EXPIRY_REMAINING_SECONDS", 0.5, minimum=0.0) == 0.5


def test_disarmed_requires_both_live_switches(monkeypatch):
    monkeypatch.delenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", raising=False)
    assert service.live_probe_authorized(True) is False
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    assert service.live_probe_authorized(False) is False
    assert service.live_probe_authorized(True) is True


def test_jsonl_writer_flushes_one_record(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    service._write_jsonl(path, {"mode": "TEST", "n": 1})
    assert path.read_text().strip() == '{"mode":"TEST","n":1}'
