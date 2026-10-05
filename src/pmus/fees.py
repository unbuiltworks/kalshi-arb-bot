"""Canonical PMUS fee adapter for the BTC15m underdog experiment.

WHY AN ADAPTER
--------------
Every economic number in this pre-registration must come from ONE fee
implementation, so that discovery, holdout and any later live sizing cannot
silently disagree. This module is that single point.

Resolution order (first hit wins, recorded in ``FEE_SOURCE``):

1. ``PMUS_FEE_IMPL`` env var, ``module:function`` naming a callable with the
   signature ``(count: int, price_cents: int, side: str, liquidity: str) -> int``
   returning integer cents. Use this to bind the canonical PMUS fee
   implementation when it is available on the PYTHONPATH.
2. ``polymarket_us`` package, if it exposes ``fees.taker_fee_cents`` /
   ``fees.maker_fee_cents``.
3. The FROZEN FALLBACK SCHEDULE below.

FROZEN FALLBACK SCHEDULE
------------------------
As of the freeze date (2026-10-05) no PMUS fee module is importable in this
repository (``main`` carries only the Kalshi schedule in ``src/fee_calc.py``,
which is NOT PMUS and must not be used as a stand-in silently). The frozen
fallback is therefore explicit and parameterised:

    fee_cents = ceil_cents(rate * count * P * (1 - P))

with ``PMUS_TAKER_RATE`` / ``PMUS_MAKER_RATE`` (env-overridable), defaulting to
0.0 — Polymarket US's published zero-trading-fee posture at freeze time. The
convex form is kept so that binding a non-zero rate (or the canonical module)
changes only a constant, never the shape of any table in this framework.

EVERY artifact produced by this framework stamps ``FEE_SOURCE`` and
``fee_schedule_fingerprint()``. A table whose fingerprint does not match the
fingerprint recorded in the pre-registration is not the frozen table.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
from typing import Callable, Optional

__all__ = [
    "FEE_SOURCE",
    "fee_cents",
    "taker_fee_cents",
    "maker_fee_cents",
    "fee_schedule_fingerprint",
]


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


# Frozen fallback rates. Override via env to model a non-zero PMUS schedule.
PMUS_TAKER_RATE = _env_float("PMUS_TAKER_RATE", 0.0)
PMUS_MAKER_RATE = _env_float("PMUS_MAKER_RATE", 0.0)


def _ceil_cents(dollars: float) -> int:
    """Round UP to the next whole cent (fees never round in the trader's favour)."""
    cents = round(dollars * 100.0, 9)
    return math.ceil(cents)


def _fallback_fee_cents(count: int, price_cents: int, side: str, liquidity: str) -> int:
    if count <= 0 or price_cents <= 0 or price_cents >= 100:
        return 0
    rate = PMUS_TAKER_RATE if liquidity == "taker" else PMUS_MAKER_RATE
    if rate <= 0.0:
        return 0
    p = price_cents / 100.0
    return _ceil_cents(rate * count * p * (1.0 - p))


def _resolve() -> tuple[Callable[..., int], str]:
    spec = os.getenv("PMUS_FEE_IMPL", "").strip()
    if spec:
        mod_name, _, func_name = spec.partition(":")
        if not func_name:
            raise ValueError("PMUS_FEE_IMPL must be 'module:function'")
        func = getattr(importlib.import_module(mod_name), func_name)
        return func, f"env:{spec}"

    try:
        pm_fees = importlib.import_module("polymarket_us.fees")
    except ModuleNotFoundError:
        pm_fees = None
    if pm_fees is not None:
        taker = getattr(pm_fees, "taker_fee_cents", None)
        maker = getattr(pm_fees, "maker_fee_cents", None)
        if callable(taker) and callable(maker):
            def _canonical(count: int, price_cents: int, side: str, liquidity: str) -> int:
                fn = taker if liquidity == "taker" else maker
                return int(fn(count, price_cents))
            return _canonical, "polymarket_us.fees"

    return _fallback_fee_cents, (
        f"frozen-fallback(taker={PMUS_TAKER_RATE!r},maker={PMUS_MAKER_RATE!r})"
    )


_FEE_FN, FEE_SOURCE = _resolve()


def fee_cents(count: int, price_cents: int, side: str = "underdog", liquidity: str = "taker") -> int:
    """Fee in integer cents for ``count`` contracts executed at ``price_cents``."""
    return int(_FEE_FN(count, price_cents, side, liquidity))


def taker_fee_cents(count: int, price_cents: int) -> int:
    return fee_cents(count, price_cents, liquidity="taker")


def maker_fee_cents(count: int, price_cents: int) -> int:
    return fee_cents(count, price_cents, liquidity="maker")


def fee_schedule_fingerprint(sample: Optional[range] = None) -> str:
    """Stable digest of the live fee schedule over the 1..49c experiment domain.

    Any change of fee implementation or rate changes this digest, which is
    stamped into every generated table.
    """
    prices = sample if sample is not None else range(1, 50)
    h = hashlib.sha256()
    h.update(FEE_SOURCE.encode())
    for p in prices:
        for q in (1, 10, 100, 1000):
            h.update(f"|{p}:{q}:{taker_fee_cents(q, p)}:{maker_fee_cents(q, p)}".encode())
    return h.hexdigest()[:16]
