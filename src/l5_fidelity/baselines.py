"""Trivial predictors FIDELITY must beat. Accuracy alone is not evidence.

With a 2 s median inter-fill on BTC15m, "always ACT" is right most of the time
at a 30 s horizon. A model that cannot beat that is not a model.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


def always_wait(**_: Any) -> Dict[str, Any]:
    return {"name": "always_WAIT", "p_act": 0.0, "predicted_outcome": None}


def always_act(**_: Any) -> Dict[str, Any]:
    return {"name": "always_ACT", "p_act": 1.0, "predicted_outcome": None}


def historical_rate(rate: float, **_: Any) -> Dict[str, Any]:
    return {"name": "historical_cadence", "p_act": rate,
            "predicted_outcome": None}


def repeat_previous(last_outcome: Optional[str], **_: Any) -> Dict[str, Any]:
    return {"name": "repeat_previous_outcome", "p_act": None,
            "predicted_outcome": last_outcome}


def dearer(price: Optional[float], **_: Any) -> Dict[str, Any]:
    """Buy whichever side is the DEARER one at this quote."""
    if price is None:
        return {"name": "DEARER", "p_act": None, "predicted_outcome": None}
    return {"name": "DEARER", "p_act": None,
            "predicted_outcome": ("Up" if price > 0.5 else "Down")}


def cheaper(price: Optional[float], **_: Any) -> Dict[str, Any]:
    if price is None:
        return {"name": "CHEAPER", "p_act": None, "predicted_outcome": None}
    return {"name": "CHEAPER", "p_act": None,
            "predicted_outcome": ("Down" if price > 0.5 else "Up")}


def cohort_frequency(p_up: float, **_: Any) -> Dict[str, Any]:
    return {"name": "cohort_empirical_frequency", "p_act": None,
            "predicted_outcome": ("Up" if p_up >= 0.5 else "Down")}


ALL = ("always_WAIT", "always_ACT", "historical_cadence",
       "repeat_previous_outcome", "DEARER", "CHEAPER",
       "cohort_empirical_frequency")
