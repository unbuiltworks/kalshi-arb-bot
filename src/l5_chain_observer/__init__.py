"""L5_CHAIN_OBSERVER -- read-only research infrastructure. NOT PROMOTED.

Reads L5's fills from Polygon directly instead of waiting for Polymarket to
publish them. Measured 2026-09-15 over n=73 joined fills: the chain carried the
same fill a median 15.16 s earlier than /activity (p95 29.06) and won 73 of 73.
An earlier note here said 19.6 s from a 10-observation sample; the larger
series says 15.16 s.

The decomposition is what matters for the engine: the chain is readable
0.37 s after its block (p95 1.65 s) while /activity publishes 15.90 s after it
(p95 30.08 s). Both figures are OUR OWN first-sighting wall-clock times, so
this measures when each surface could be READ -- not two representations of the
same event clock, which is the error that made a later 'lead' metric report
0.0 s.

IT IS NOT CONNECTED TO ANYTHING. No execution path, no FIDELITY input, no
order verb, no credential. FIDELITY v0.2 is frozen and this does not touch it.
Promotion requires cross-validation against the activity feed first -- agreement
on WHAT, not merely speed on WHEN -- because a faster feed that disagrees about
the facts is worse than a slow one.
"""
