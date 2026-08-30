"""Policy-free venue-flow census.

Measures whether a venue's public trade flow could support L5-style two-sided
inventory accumulation, before any quoting policy is designed. Read-only: no
credentials, no order entry, public market data only.

Core metrics (per window and aggregate):

  * flow density, clip sizes, side alternation, both-side presence
  * q-aware, residual-capped sequential matched ceiling -- the maximum
    matched inventory a passive q-sized maker could have formed from the
    observed tape while never letting |imbalance| exceed R
  * dollar capacity at the tape's own prices, and residual-adjusted EV at
    lambda in {6, 12, 24} cents per residual contract

Benchmarks (measured in this repo's L5 reconstruction, 2026-08-29 afternoon,
polymarket.com):  matched:residual 4.38:1, ~43 fills/window, median clip 13.5,
alternation 37.2%, pair edge 3.9c. Gemini's first tape ceiling: 0.29:1.
"""
