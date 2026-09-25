# RUN MARKER — PROBE 06, REAL MONEY

Owner-authorized, conditional on a clean full suite returning the established
baseline. It did: **26 failed, 14 errors, identical file-for-file to
baseline, zero new regressions.**

- session `l5probe-20260925-btc15m-06`, fresh and one-use
- market pinned `cpc-btc-updown-15m-2026-09-25-1700z`
- account pinned `d51b44a3fa83`; a different fingerprint fails closed
  before arming

Envelope unchanged and not scaled: $5 per market, $5 total potential
exposure, one live outstanding order, residual <= 10 shares, one concurrent
market, $5 daily realized-loss halt.

Frozen and untouched: L5_NATIVE 030d8bf, lambda and relative sizing, the
authenticated WS book, exact preview-echo validation, quantity flooring to
the authoritative increment, the market->token identity gate, the corrected
risk_exposure_usd $5 gates, and authoritative order refresh/reconciliation.

## What this run is for

1. Prove no foreign-market action reaches translation or submission. Probe 05
   traded two that did.
2. Exercise the post-fill lifecycle again, continuously. A fill is not a
   reason to stop.
3. Exercise the corrected risk metric against REAL inventory. Observe-only
   holds none, so the bound was never stressed.
4. Compare every legitimate L5 action with our actual response.
5. Obtain authoritative settlement and realized P&L.

Identity counters are per DISTINCT action, not per poll: the 1615z window
reported 147 refusals and 5,070 admissions against 97 distinct actions,
because the feed re-serves the same rows every poll.

One probe. No Probe 07. No scaling. No strategy change.
