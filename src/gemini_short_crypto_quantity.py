"""Quantity-grid helpers for Gemini short-duration prediction contracts."""
from __future__ import annotations

from decimal import Decimal

D = Decimal


def quantity_supported(*, quantity: D, minimum: D, increment: D) -> bool:
    """Return True when quantity is on the venue-advertised minimum/increment grid."""
    if quantity <= 0 or minimum <= 0 or increment <= 0 or quantity < minimum:
        return False
    steps = (quantity - minimum) / increment
    return steps == steps.to_integral_value()
