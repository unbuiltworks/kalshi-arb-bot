"""Which cohorts are the maker-policy target, and which are a different animal.

MEASURED, NOT ASSUMED. Chain-attributed roles cross-checked against the venue's
own flag at 1,270/1,270:

    cohort   windows   fills   maker%   fills/window
    BTC15m       527  63,569    95.5%       121
    BTC5m        957   6,227     0.05%        6.5
    ETH15m        29     117     0%           4.0
    ETH5m        703   2,069     0.24%        2.9

BTC15m runs a continuous two-sided market-making operation. The others run a
low-frequency TAKER activity that is not the same strategy, and averaging them
together produced every misleading cross-cohort figure this programme has
published -- "L5 median pair cost 0.9646" and "72.7% two-sided" among them.

THE OTHER COHORTS ARE NOT DELETED. They are a real, separate regime and their
research stands on its own; they are simply excluded from any statistic that
describes the MAKER policy, because a taker's pair cost and a maker's pair cost
are not the same quantity and must never land in one average.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

# the maker-policy replication target
MAKER_COHORTS = ("BTC15m",)
# real, researched, and a different strategy
TAKER_REGIME_COHORTS = ("BTC5m", "ETH15m", "ETH5m")

REGIME = {
    "BTC15m": "MAKER",
    "BTC5m": "PREDOMINANTLY_TAKER",
    "ETH15m": "PREDOMINANTLY_TAKER",
    "ETH5m": "PREDOMINANTLY_TAKER",
}

MEASURED_MAKER_RATE = {
    "BTC15m": 0.955, "BTC5m": 0.0005, "ETH15m": 0.0, "ETH5m": 0.0024,
}


def regime(cohort: Any) -> str:
    return REGIME.get(str(cohort), "UNKNOWN")


def is_maker_cohort(cohort: Any) -> bool:
    return str(cohort) in MAKER_COHORTS


def split(rows: Sequence[dict], *, key: str = "cohort") -> Dict[str, List[dict]]:
    """Separate maker-policy rows from the taker regimes. Nothing is dropped."""
    out: Dict[str, List[dict]] = {"maker_policy": [], "taker_regime": [],
                                  "unknown_cohort": []}
    for r in rows:
        c = str(r.get(key))
        if is_maker_cohort(c):
            out["maker_policy"].append(r)
        elif c in TAKER_REGIME_COHORTS:
            out["taker_regime"].append(r)
        else:
            out["unknown_cohort"].append(r)
    return out


def label(rep: Dict[str, Any], *, cohort: Any = None) -> Dict[str, Any]:
    """Stamp a report with its regime so no reader has to remember."""
    c = str(cohort) if cohort is not None else None
    return {**rep, "cohort": c, "regime": regime(c),
            "in_maker_policy_aggregate": is_maker_cohort(c),
            "regime_note": (
                "BTC15m is the maker-policy target (95.5% maker, 121 "
                "fills/window). BTC5m, ETH15m and ETH5m are a separate "
                "PREDOMINANTLY TAKER regime -- researched, retained, and "
                "excluded from maker-policy aggregates because a taker's pair "
                "cost and a maker's are not the same quantity")}
