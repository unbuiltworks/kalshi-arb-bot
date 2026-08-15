from pathlib import Path

import src.gemini_kalshi_live_service as service


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


def test_live_requires_cli_env_and_single_use_token(monkeypatch, tmp_path: Path):
    token = tmp_path / "arm"
    monkeypatch.delenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", raising=False)
    token.write_text("ARM\n")
    assert service._armed_once(True, token) is False

    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    assert service._armed_once(False, token) is False
    assert service._armed_once(True, token) is True

    assert service._consume_arm_token(token) is True
    assert not token.exists()
    assert service._armed_once(True, token) is False
    assert service._consume_arm_token(token) is False


def test_arm_token_env_override(monkeypatch, tmp_path: Path):
    token = tmp_path / "custom-arm"
    monkeypatch.setenv("LIVE_PROBE_ARM_TOKEN_FILE", str(token))
    assert service._arm_token_path() == token


def test_jsonl_writer_flushes_one_record(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    service._write_jsonl(path, {"mode": "TEST", "n": 1})
    assert path.read_text().strip() == '{"mode":"TEST","n":1}'
