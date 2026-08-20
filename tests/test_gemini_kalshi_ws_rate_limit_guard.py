import asyncio

from src import gemini_kalshi_ws_rate_limit_guard as guard


class FakeKalshi:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_rest_preflight_does_not_open_throwaway_authenticated_ws(monkeypatch):
    kalshi = FakeKalshi()
    monkeypatch.setattr(guard.v2.executor, "KalshiV2Executor", lambda: kalshi)

    async def preflight(_kalshi, *, live_requested, token_path):
        assert _kalshi is kalshi
        assert live_requested is False
        assert str(token_path).endswith("sol-canary-v2-preflight-no-arm")
        return {
            "gemini_usd_available": "10",
            "kalshi_usd_available": "10",
            "gemini_terms_accepted": True,
            "kalshi_production_authenticated": True,
        }

    monkeypatch.setattr(guard.v2.executor, "_preflight", preflight)

    # If the old redundant verification path is accidentally called, fail loudly.
    async def forbidden_ws_verify(*_args, **_kwargs):
        raise AssertionError("throwaway authenticated WebSocket verification must not run")

    monkeypatch.setattr(guard.v2, "verify_authenticated_ws", forbidden_ws_verify, raising=False)
    asyncio.run(guard._rest_only_authenticated_preflight())
    assert kalshi.closed is True


def test_rest_preflight_still_fails_closed_on_insufficient_funding(monkeypatch):
    kalshi = FakeKalshi()
    monkeypatch.setattr(guard.v2.executor, "KalshiV2Executor", lambda: kalshi)

    async def preflight(*_args, **_kwargs):
        return {
            "gemini_usd_available": "0.50",
            "kalshi_usd_available": "10",
            "gemini_terms_accepted": True,
            "kalshi_production_authenticated": True,
        }

    monkeypatch.setattr(guard.v2.executor, "_preflight", preflight)
    try:
        asyncio.run(guard._rest_only_authenticated_preflight())
    except RuntimeError as exc:
        assert "funding preflight failed" in str(exc)
    else:
        raise AssertionError("insufficient funding must fail closed")
    assert kalshi.closed is True


def test_cooldown_reason_classifies_only_safe_connection_misses():
    assert guard.cooldown_reason({"startup_retry": True, "result": {}}) == "PRECLAIM_CHILD_STARTUP_FAILURE"
    assert guard.cooldown_reason({
        "result": {
            "outcome": "PRIMARY_NOT_FILLED",
            "gemini": {"reason": "PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED"},
            "kalshi": None,
        }
    }) == "PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED"
    assert guard.cooldown_reason({"result": {"outcome": "BOTH_FILLED"}}) is None
    assert guard.cooldown_reason({
        "result": {"outcome": "UNKNOWN", "gemini": {"reason": "POST_SEND_AMBIGUITY"}}
    }) is None


def test_parent_wrapper_paces_every_start_and_penalizes_safe_ws_miss(monkeypatch):
    calls = []

    class FakePacer:
        cooldown_s = 8.0

        async def wait_turn(self):
            calls.append("wait")

        async def penalize(self):
            calls.append("penalize")

    monkeypatch.setattr(guard, "_PACER", FakePacer())

    async def original(*_args, **_kwargs):
        calls.append("original")
        return {
            "result": {
                "outcome": "PRIMARY_NOT_FILLED",
                "gemini": {"reason": "PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED"},
                "kalshi": None,
            }
        }

    record = asyncio.run(guard._wrap_parent_obtain(original)("run", "BTC", None, None, None, None))
    assert record["result"]["outcome"] == "PRIMARY_NOT_FILLED"
    assert calls == ["wait", "original", "penalize"]


def test_parent_wrapper_never_downgrades_unsafe_exception(monkeypatch):
    class FakePacer:
        cooldown_s = 8.0

        async def wait_turn(self):
            return None

        async def penalize(self):
            raise AssertionError("unsafe exception must not be converted to cooldown retry")

    monkeypatch.setattr(guard, "_PACER", FakePacer())

    async def original(*_args, **_kwargs):
        raise RuntimeError("claim exists without result")

    try:
        asyncio.run(guard._wrap_parent_obtain(original)("run", "BTC", None, None, None, None))
    except RuntimeError as exc:
        assert "claim exists without result" in str(exc)
    else:
        raise AssertionError("unsafe exception must remain fail closed")


def test_default_pacing_is_global_and_nonzero():
    assert guard.BASE_START_SPACING_S >= 1.0
    assert guard.UNHEALTHY_COOLDOWN_S >= guard.BASE_START_SPACING_S
