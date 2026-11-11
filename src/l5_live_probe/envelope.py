"""The bounds a live probe would run under -- deliberately NOT chosen here.

THE ENVELOPE IS THE ONE INPUT THIS HARNESS REFUSES TO GUESS. Everything else
below the preflight is prepared and testable without it; the exposure numbers
are an owner decision that the FAILED prospective experiment does not support
making automatically, so `shell()` raises until they are set explicitly.

That is the same principle l5_risk.shell.RiskShell already encodes -- every
field must be set, there is no default stop -- applied one level up.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from src.l5_risk.shell import RiskShell

LABEL = "LIVE_ECONOMIC_MEASUREMENT/unarmed"

# The env names an owner sets to choose the envelope. Names only: this module
# never prints a value and none of these is a credential.
ENV = {
    "daily_loss_stop": "L5PROBE_DAILY_LOSS_STOP",
    "max_inventory_shares": "L5PROBE_MAX_INVENTORY_SHARES",
    "max_market_spend": "L5PROBE_MAX_MARKET_SPEND",
    "max_concurrent_markets": "L5PROBE_MAX_CONCURRENT_MARKETS",
    "deadman_s": "L5PROBE_DEADMAN_S",
}

# Distinct from every existing service root. The probe must not share a mutable
# path with the HALTED legacy PMUS executor; preflight asserts this.
STATE_ROOT = "/state/l5-live-probe"
PMUS_STATE_ROOTS = ("/state/pmus-spring", "/state/pmus-q1", "/state/pmus-ws",
                    "/state/pmus-sports", "/state/pmus-supervisor")


class EnvelopeNotChosen(RuntimeError):
    """Raised instead of defaulting. Defaulting is how an unreviewed limit ships."""


def configured() -> Dict[str, Optional[str]]:
    """What the environment currently offers, without deciding anything."""
    return {k: os.environ.get(v) for k, v in ENV.items()}


def missing() -> list:
    return sorted(k for k, v in configured().items() if v in (None, ""))


def shell(venue: str = "PMUS") -> RiskShell:
    """The probe's risk shell, or a refusal naming every unset bound."""
    miss = missing()
    if miss:
        raise EnvelopeNotChosen(
            "the live-probe envelope has not been chosen; unset: "
            + ", ".join(ENV[k] for k in miss))
    c = configured()
    return RiskShell(
        venue=venue,
        daily_loss_stop=str(c["daily_loss_stop"]),
        max_inventory_shares=str(c["max_inventory_shares"]),
        max_market_spend=str(c["max_market_spend"]),
        max_concurrent_markets=int(str(c["max_concurrent_markets"])),
        deadman_s=float(str(c["deadman_s"])),
    )


def describe() -> Dict[str, Any]:
    """For the state record: what is set, what is not. Never a value that could
    be a secret -- these are limits, but the shape is reported, not echoed."""
    miss = missing()
    return {"label": LABEL, "state_root": STATE_ROOT,
            "envelope_chosen": not miss, "unset_bounds": [ENV[k] for k in miss],
            "env_names": dict(ENV),
            "note": ("bounds are an owner decision; this module raises rather "
                     "than defaulting, so no limit can ship unreviewed")}
