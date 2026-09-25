# RUN MARKER — OBSERVE-ONLY, identity gate proof

`L5_OBSERVE_ONLY=YES`, live authorization variables cleared, `armed` false,
`NullTransport` in the executor. This run cannot send an order.

What it must prove over one complete BTC15m window:

- **zero foreign tokens admitted** — `admitted_tokens` contains at most the
  two ids in `market_binding`, and nothing else;
- **exact market binding on every eligible action** — the binding is the
  venue's own record for this window: conditionId, both clobTokenIds, and an
  endDate equal to the window's end;
- **authenticated book healthy** — `AUTHENTICATED_WS`, frames arriving;
- **corrected risk gate active** — the $5 caps read `risk_exposure_usd`
  (worst case over both settlements) rather than `committed_usd` notional;
- **`submitted_live: 0`**.

Probe 05's 1500z window admitted five distinct tokens where the venue
publishes two, and traded two of the foreign ones. Replayed through this
gate it admits 104 of 110 and refuses 6 as MARKET_IDENTITY_MISMATCH,
including both erroneous orders.

No Probe 06. Nothing armed. Envelope unchanged.
