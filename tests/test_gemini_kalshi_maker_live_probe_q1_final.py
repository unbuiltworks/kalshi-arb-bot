from src import gemini_kalshi_maker_live_probe_q1_final as final


def test_preflight_defaults_on(monkeypatch):
    monkeypatch.delenv(final.PREFLIGHT_ENV, raising=False)
    assert final.preflight_requested() is True


def test_preflight_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv(final.PREFLIGHT_ENV, "NO")
    assert final.preflight_requested() is False
