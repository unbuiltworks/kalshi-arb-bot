"""L5 intent: what the engine WANTS, stated without reference to any venue.

FIDELITY produces intent. It does not know which venue will execute it, or
whether any venue will. That separation is the whole point: the moment the
engine starts reasoning about Polymarket's fee schedule or Gemini's book, the
two questions -- "what would L5 do?" and "can we do it here?" -- collapse into
one and neither can be audited.
"""
from src.l5_intent.action import (
    ACTIONS, DesiredInventoryAction, intent_id, from_fidelity_state,
)

__all__ = ["ACTIONS", "DesiredInventoryAction", "intent_id",
           "from_fidelity_state"]
