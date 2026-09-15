"""Venue adapters: the only place that knows how a venue behaves.

An adapter is handed a frozen `DesiredInventoryAction` it did not create and
answers, for one venue, whether that inventory can actually be acquired -- at
what price, with what fee, and separately whether it actually filled.

Both venues share ONE evaluation path (`evaluate`), parameterised by the
existing `VenueAdapter` implementations. Two evaluators would make every
observed difference between Polymarket and Gemini ambiguous: real economics, or
an implementation accident?
"""
from src.l5_adapters.base import (
    STAGES, AdapterEvaluation, no_market, within_ceiling,
)
from src.l5_adapters.evaluate import attach_fill_evidence, evaluate

__all__ = ["STAGES", "AdapterEvaluation", "no_market", "within_ceiling",
           "attach_fill_evidence", "evaluate"]
