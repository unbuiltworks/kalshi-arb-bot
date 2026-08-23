from __future__ import annotations

import asyncio

import pytest

from src import sports_kp_live_combo_supervisor_v55 as v55


class Response429:
    status_code = 429


class Http429(Exception):
    def __init__(self) -> None:
        super().__init__("429 Too Many Requests")
        self.response = Response429()


def test_rate_limit_classifier_accepts_cloudflare_and_http_429() -> None:
    assert v55._is_transient_poly_rate_limit(Http429()) is True
    assert v55._is_transient_poly_rate_limit(RuntimeError("Cloudflare Error 1015")) is True
    assert v55._is_transient_poly_rate_limit(RuntimeError("ordinary bug")) is False


def test_rate_limited_stage_is_contained(capsys: pytest.CaptureFixture[str]) -> None:
    async def boom():
        raise Http429()

    result = asyncio.run(v55._run_rate_limit_tolerant("probe", boom))
    assert result is None
    text = capsys.readouterr().out
    assert "SPORTS_COMBO_TRANSIENT_SKIP" in text
    assert '"stage":"probe"' in text
    assert '"order_entry":false' in text


def test_non_rate_limit_exception_still_fails_closed() -> None:
    async def boom():
        raise RuntimeError("logic invariant failed")

    with pytest.raises(RuntimeError, match="logic invariant failed"):
        asyncio.run(v55._run_rate_limit_tolerant("probe", boom))


def test_run_continues_past_rate_limited_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def census():
        calls.append("census")
        return {"top_candidates": []}

    async def preview(_snapshot):
        calls.append("preview")
        raise Http429()

    async def chain():
        calls.append("chain")

    monkeypatch.setattr(v55.maker_census, "run_once", census)
    monkeypatch.setattr(v55.maker_preview, "run_once", preview)
    monkeypatch.setattr(v55.v53, "run", chain)

    asyncio.run(v55.run())
    assert calls == ["census", "preview", "chain"]
