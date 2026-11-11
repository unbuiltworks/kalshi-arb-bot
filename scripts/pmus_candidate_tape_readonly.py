"""Read-only launcher for the exact PMUS candidate tape diagnostic.

This intentionally does NOT import the spring/micro/q1 execution stack.  The
existing ``scripts.spring_candidate_tape_read`` analyzer needs only two pure
spring economics values: the 0.96 pair target and the hard complement bound.
We inject a minimal stand-in module with those values before importing the
analyzer, preserving the recorder image's no-order-path boundary.

No network calls. No order code. No arming. Reads only the durable recorder
tape selected by PMUS_WS_ROOT / the analyzer CLI.
"""
from __future__ import annotations

import runpy
import sys
import types
from decimal import Decimal as D, ROUND_DOWN


ONE = D("1")
DEFAULT_TICK = D("0.005")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
MIN_EDGE = D("0.01")
SPRING_PAIR_TARGET = D("0.96")


def complement_bound(first_cost, min_edge=MIN_EDGE):
    """Pure mirror of the Q1 fee-safe complement bound; no execution code."""
    px = ONE - D(str(first_cost)) - D(str(min_edge))
    px = (px / DEFAULT_TICK).to_integral_value(rounding=ROUND_DOWN) * DEFAULT_TICK
    if px < MIN_PRICE or px > MAX_PRICE:
        return None
    return px


def install_readonly_spring_stub() -> None:
    q1 = types.SimpleNamespace(complement_bound=complement_bound)
    spring = types.ModuleType("src.pmus_spring_l5_session")
    spring.SPRING_PAIR_TARGET = SPRING_PAIR_TARGET
    spring.q1 = q1
    spring.__dict__["READ_ONLY_DIAGNOSTIC_STUB"] = True
    sys.modules["src.pmus_spring_l5_session"] = spring


def main() -> int:
    install_readonly_spring_stub()
    # Preserve the analyzer's CLI and exit code exactly.
    runpy.run_module("scripts.spring_candidate_tape_read", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
