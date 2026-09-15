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

# `evaluate` is exported LAZILY. Importing it eagerly pulls in
# `src.l5_venue_adapter`, which lazily reaches `pmus_q1_executor` -- the order
# path. That made a read-only research image that touches only `base` carry the
# executor one attribute lookup away, and its Docker image carry modules it has
# no business shipping. PEP 562 keeps the re-export working for callers that
# actually want it while `from src.l5_adapters.base import ...` stays clean.
__all__ = ["STAGES", "AdapterEvaluation", "no_market", "within_ceiling",
           "attach_fill_evidence", "evaluate"]

_LAZY = {"attach_fill_evidence", "evaluate"}


def __getattr__(name):
    if name in _LAZY:
        # import_module, NOT `from . import evaluate`: the submodule and the
        # function share the name `evaluate`, so a from-import re-enters this
        # very hook looking for the attribute and recurses forever.
        import importlib
        return getattr(importlib.import_module("src.l5_adapters.evaluate"),
                       name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
