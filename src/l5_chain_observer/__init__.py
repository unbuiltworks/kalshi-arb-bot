"""L5_CHAIN_OBSERVER -- read-only research infrastructure. NOT PROMOTED.

Reads L5's fills from Polygon directly instead of waiting for Polymarket to
publish them. Measured 2026-09-15: the chain carried the same fill a median
19.6 s earlier than /activity and won 10 of 10 observations.

IT IS NOT CONNECTED TO ANYTHING. No execution path, no FIDELITY input, no
order verb, no credential. FIDELITY v0.2 is frozen and this does not touch it.
Promotion requires cross-validation against the activity feed first -- agreement
on WHAT, not merely speed on WHEN -- because a faster feed that disagrees about
the facts is worse than a slow one.
"""
