"""Gate-classification tests for the read-only V5.1 qualifier diagnostic.

The diagnostic normally runs inside the frozen V5.1 release image, where
``src.gemini_liquidity_reward_live_probe_q10`` and ``src.gemini_q10_rescue_policy``
exist. The control branch does not carry those modules, so these tests inject
faithful stubs (fee math copied from the frozen release) before loading the
diagnostic by file path. What is being tested is the diagnostic's own logic:
gate ordering, the one-sided premium parity gate, and the fresh-depth retry.
"""
from __future__ import annotations

import asyncio
import importlib.util
import math
import sys
import types
from decimal import Decimal as D
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DIAG_PATH = REPO / "deploy" / "aws" / "v51_qualifier_diagnostic.py"

ONE = D("1")
Q = D("10")


def _ceil_cent(value: D) -> D:
    return D(math.ceil(value * 100)) / D("100")


def _maker_fee(price: D, quantity: D) -> D:
    # Mirrors gemini_predictions_maker_fee in the frozen release.
    return _ceil_cent(D("0.0175") * quantity * price * (ONE - price))


def _probe_model(bid: D, ask: D) -> dict[str, D]:
    # Mirrors probe._model at ff4bc84 (q=10 liquidity-reward probe).
    yes = D(str(bid))
    no = ONE - D(str(ask))
    yes_fee = _maker_fee(yes, Q)
    no_fee = _maker_fee(no, Q)
    locked = Q * (ONE - yes - no) - yes_fee - no_fee
    mid = (D(str(bid)) + D(str(ask))) / D("2")
    return {
        "yes_quote": yes,
        "no_quote": no,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "locked_edge": locked,
        "yes_distance": mid - yes,
        "no_distance": (ONE - mid) - no,
        "max_one_sided_premium": max(yes * Q, no * Q),
    }


class _Event:
    ticker = "BTC5M-TEST"
    symbol = "GEMI-BTC05M-TEST-UP"
    seconds_to_expiry = 300.0
    price_increment = D("0.01")


def _load_diagnostic(book: tuple[D, D, D, D], *, depth_failures_before_success: int = 0,
                     depth_always_fails: bool = False, rescue_safe: bool = True):
    calls = {"depth": 0}

    async def discover_btc5m():
        return _Event()

    async def fresh_top(symbol: str, timeout: float = 4.0):
        calls["depth"] += 1
        if depth_always_fails or calls["depth"] <= depth_failures_before_success:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        return book

    probe = types.ModuleType("src.gemini_liquidity_reward_live_probe_q10")
    probe.REST = "https://api.gemini.com"
    probe.MIN_SECONDS_TO_EXPIRY = 240.0
    probe.MAX_SECONDS_TO_EXPIRY = 330.0
    probe.MIN_PRICE = D("0.20")
    probe.MAX_PRICE = D("0.80")
    probe.MAX_ONE_SIDED_PREMIUM = D("6.50")
    probe._model = _probe_model
    probe.base = types.SimpleNamespace(discover_btc5m=discover_btc5m, fresh_top=fresh_top)

    class _Standing:
        safe = rescue_safe

        def as_dict(self):
            return {"safe": rescue_safe}

    rescue = types.ModuleType("src.gemini_q10_rescue_policy")
    rescue.standing_rescue_check = lambda **kwargs: _Standing()

    import src as src_pkg

    sys.modules["src.gemini_liquidity_reward_live_probe_q10"] = probe
    sys.modules["src.gemini_q10_rescue_policy"] = rescue
    src_pkg.gemini_liquidity_reward_live_probe_q10 = probe
    src_pkg.gemini_q10_rescue_policy = rescue

    spec = importlib.util.spec_from_file_location("v51_qualifier_diagnostic_under_test", DIAG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def reward_detail(ticker: str):
        return (
            {"config": {"enabled": True, "max_spread_cents": 10},
             "event": {"event_ticker": ticker, "qualifying_maker_count": 0}},
            # Success-path detail carries no "reason" key, matching the real
            # reward_detail; a stray key here would clobber the report's reason
            # through the diagnostic's `**reward_meta` spread.
            {"config_status": 200, "events_status": 200, "event_found": True},
        )

    module.reward_detail = reward_detail
    module._depth_calls = calls
    return module


def _evaluate(module):
    return asyncio.run(module.evaluate_once())


def test_balanced_book_qualifies():
    diag = _load_diagnostic((D("0.45"), D("50"), D("0.55"), D("50")))
    found, report = _evaluate(diag)
    assert report["classification"] == "QUALIFIED"
    assert found is not None


def test_premium_gate_matches_frozen_qualifier():
    # yes 0.68 => one-sided premium $6.80 > the probe's $6.50 cap. The live V5.1
    # qualifier rejects this book inside probe._candidate even though the V5.1
    # entry gate (band 0.20-0.80) would accept it. The diagnostic must agree.
    diag = _load_diagnostic((D("0.68"), D("50"), D("0.75"), D("50")))
    model = _probe_model(D("0.68"), D("0.75"))
    assert model["locked_edge"] >= D("0.50")          # edge gate passes
    assert model["max_one_sided_premium"] == D("6.8")  # premium gate must fire
    found, report = _evaluate(diag)
    assert found is None
    assert report["classification"] == "ECONOMIC_GATE"
    assert report["reason"] == "ONE_SIDED_PREMIUM_GATE"


def test_premium_cap_is_read_from_the_probe_module():
    diag = _load_diagnostic((D("0.45"), D("50"), D("0.55"), D("50")))
    assert diag.MAX_ONE_SIDED_PREMIUM == D("6.50")


def test_depth_retry_recovers_from_one_transient_miss():
    diag = _load_diagnostic(
        (D("0.45"), D("50"), D("0.55"), D("50")), depth_failures_before_success=1,
    )
    found, report = _evaluate(diag)
    assert report["classification"] == "QUALIFIED"
    assert report["depth"]["attempt"] == 2
    assert len(report["depth"]["failures"]) == 1


def test_depth_exhaustion_is_venue_data_error_with_all_attempts_recorded():
    diag = _load_diagnostic(
        (D("0.45"), D("50"), D("0.55"), D("50")), depth_always_fails=True,
    )
    found, report = _evaluate(diag)
    assert found is None
    assert report["classification"] == "VENUE_DATA_ERROR"
    assert report["reason"] == "DEPTH_UNAVAILABLE_AFTER_RETRIES"
    assert len(report["depth"]["failures"]) == 3
    assert diag._depth_calls["depth"] == 3


def test_rescue_gate_failure_is_safety_gate():
    diag = _load_diagnostic((D("0.45"), D("50"), D("0.55"), D("50")), rescue_safe=False)
    found, report = _evaluate(diag)
    assert found is None
    assert report["classification"] == "SAFETY_GATE"
    assert report["reason"] == "V5_RESCUE_GATE"
