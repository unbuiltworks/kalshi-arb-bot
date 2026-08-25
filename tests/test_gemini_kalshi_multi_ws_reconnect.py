from src.gemini_kalshi_multi_ws_shadow import (
    _is_transient_ws_error,
    _retry_backoff_s,
)


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


class _ResponseError(Exception):
    def __init__(self, status_code):
        super().__init__(f"server rejected WebSocket connection: HTTP {status_code}")
        self.response = _Response(status_code)


def test_rate_limit_and_temporary_upstream_failures_are_retryable():
    assert _is_transient_ws_error(_ResponseError(429)) is True
    assert _is_transient_ws_error(_ResponseError(503)) is True
    assert _is_transient_ws_error(RuntimeError("HTTP 502 Bad Gateway")) is True
    assert _is_transient_ws_error(RuntimeError("too many requests")) is True


def test_auth_and_programming_failures_remain_fatal():
    assert _is_transient_ws_error(_ResponseError(401)) is False
    assert _is_transient_ws_error(_ResponseError(403)) is False
    assert _is_transient_ws_error(ValueError("bad payload")) is False


def test_backoff_is_bounded_and_assets_are_staggered():
    assert _retry_backoff_s(0, "BTC") == 5.0
    assert _retry_backoff_s(0, "ETH") == 6.0
    assert _retry_backoff_s(0, "SOL") == 7.0
    assert _retry_backoff_s(0, "XRP") == 8.0
    assert _retry_backoff_s(10, "BTC") == 60.0
    assert _retry_backoff_s(10, "XRP") == 60.0


def test_backoff_rejects_bad_inputs():
    try:
        _retry_backoff_s(-1, "BTC")
    except ValueError as exc:
        assert "nonnegative" in str(exc)
    else:
        raise AssertionError("negative attempt must fail")

    try:
        _retry_backoff_s(0, "DOGE")
    except ValueError as exc:
        assert "unsupported asset" in str(exc)
    else:
        raise AssertionError("unknown asset must fail")
