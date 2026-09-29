"""SHADOW CANDIDATE ONLY. Not wired into any production decision path.

THE FINDING. Across 10,992 imbalanced BTC-15m states in our canonical L5
history, the only feature that predicts which side L5 accumulates next is
HIS OWN PREVIOUS ACTION:

  previous action EXTENDED the overweight side -> 70.3% extend again
  previous action REBALANCED                   -> 29.9% extend

Held out by MARKET (226 train / 97 test markets), `prev_extended` alone
scores 0.700 accuracy against a 0.502 unconditional baseline. Removing it
from the full model collapses accuracy to 0.563. Absolute residual scores
0.498 -- indistinguishable from the baseline -- and residual/matched ratio
scores 0.493, WORSE than the baseline. Price, momentum, velocity, pair
economics, time remaining and inter-fill gap add nothing out of sample.

So L5 is a PERSISTENT LATENT MODE, not a residual-driven rebalancer:

  ACCUMULATE_UP / ACCUMULATE_DOWN   keep taking one side
  REBALANCE                          take the complement
  (WAIT is unobservable in fills-only data and is NOT modelled)

LOCK5's TWO_SIDED_OUR_TOUCH has no mode at all: it rests one clip on BOTH
sides every tick, so which side we accumulate is chosen by whoever lifts us,
not by us. That is the gap this candidate exists to measure.

IMPORTANT CAVEAT, stated because it bounds what this can claim: mode
persistence is partly MECHANICAL. A single resting quote that is repeatedly
hit produces a same-side run without any decision behind it. Fills-only
public data cannot separate a deliberate mode from a queue artifact, and
77.4% of L5's extensions continue an existing run. This candidate therefore
measures BEHAVIOURAL AGREEMENT, and is not evidence that adopting a mode
would earn more.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Mapping, Optional

ACCUMULATE_UP = "ACCUMULATE_UP"
ACCUMULATE_DOWN = "ACCUMULATE_DOWN"
REBALANCE = "REBALANCE"

#: the flag. Absent or false, nothing in this module is consulted.
SHADOW_ENV = "L5_SHADOW_MODE_SWITCH"

_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "analysis", "l5", "l5_switch_model.json")


def shadow_enabled() -> bool:
    return os.getenv(SHADOW_ENV, "").strip() not in ("", "0", "false", "False")


def load_model(path: Optional[str] = None) -> Dict[str, Any]:
    with open(path or _MODEL_PATH) as fh:
        return json.load(fh)


def p_extend(model: Mapping[str, Any], feats: Mapping[str, float]) -> float:
    w = model["w"]
    z = float(model["b"]) + sum(float(w[k]) * float(feats.get(k, 0.0))
                                for k in w)
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def mode_of(*, held: Optional[str], prev_extended: Optional[bool]) -> str:
    """The latent mode implied by the state, under the fitted model.

    `held` None (a balanced book) has no overweight side and therefore no
    mode; the caller keeps its ordinary two-sided behaviour.
    """
    if held is None:
        return REBALANCE
    if prev_extended:
        return ACCUMULATE_UP if held == "up" else ACCUMULATE_DOWN
    return REBALANCE


def preferred_side(*, held: Optional[str],
                   prev_extended: Optional[bool]) -> Optional[str]:
    """Which side this candidate would accumulate next. None = no preference.

    This is the ONLY behavioural difference from frozen: frozen expresses no
    preference at all.
    """
    if held is None:
        return None
    m = mode_of(held=held, prev_extended=prev_extended)
    if m == ACCUMULATE_UP:
        return "up"
    if m == ACCUMULATE_DOWN:
        return "down"
    return "down" if held == "up" else "up"
