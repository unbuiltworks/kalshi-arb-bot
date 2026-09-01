import asyncio

from src import gemini_prediction_auth as m


def test_private_rest_pacer_disabled_by_default(monkeypatch):
    monkeypatch.delenv(m.PRIVATE_REST_MIN_INTERVAL_ENV, raising=False)
    assert m._private_rest_min_interval() == 0.0


def test_private_rest_pacer_serializes_nonce_consumers(monkeypatch):
    monkeypatch.setenv(m.PRIVATE_REST_MIN_INTERVAL_ENV, "1.0")
    m._private_rest_pacers.clear()
    clock = [100.0]
    sleeps = []

    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])

    async def fake_sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(m.asyncio, "sleep", fake_sleep)

    async def run():
        await m._pace_private_rest()
        await m._pace_private_rest()
        await m._pace_private_rest()

    asyncio.run(run())
    assert sleeps == [1.0, 1.0]


def test_private_rest_pacer_rejects_negative_interval(monkeypatch):
    monkeypatch.setenv(m.PRIVATE_REST_MIN_INTERVAL_ENV, "-0.1")
    try:
        m._private_rest_min_interval()
    except RuntimeError as exc:
        assert "must be >= 0" in str(exc)
    else:
        raise AssertionError("negative pacing interval was accepted")
