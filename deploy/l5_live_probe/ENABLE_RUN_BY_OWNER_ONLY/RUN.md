# RUN MARKER — PROBE 05, REAL MONEY

Owner-authorized. One market, one authorization, spent on use.

- session id `l5probe-20260925-btc15m-05` (fresh, one-use; the ledger
  refuses it forever once claimed)
- market pinned `cpc-btc-updown-15m-2026-09-25-1500z` — pinned rather than
  derived so a container starting either side of 15:00Z cannot bind to a
  window with seconds left on it
- account pinned `d51b44a3fa83`; a different fingerprint fails closed
  before arming

Envelope unchanged, and not scaled: $5 per market, $5 total potential
exposure including outstanding orders, one live outstanding order, residual
<= 10 shares, one concurrent market, $5 daily realized-loss halt.

Frozen and untouched: L5_NATIVE 030d8bf, lambda, timing, side selection,
PMUS translation, quantity flooring to the authoritative increment, exact
preview-echo validation, uncertainty quarantine, authoritative
reconciliation.

## Why this probe exists

Probe 04 filled two milliseconds after insert and the session never asked
again, so 29 later actions were refused MAX_LIVE_ORDERS against an order
that no longer existed. Probe 05's primary objective is the behaviour AFTER
a real fill: whether the repaired lifecycle frees the live-order slot and
lets the sequential engine continue.

Required sequence, per action:

    L5 action -> executable translation -> quantization -> reserve ->
    preview -> exact echo -> submit -> ACK -> authoritative order refresh ->
    fill/partial detection -> inventory update -> release/update outstanding
    reservation -> next L5 action

A fill is not a reason to stop. The probe continues normally inside the
unchanged envelope.

Order state is polled while an order is outstanding and only the INCREMENT
in cumulative fill is booked, so repeated identical refreshes cannot
double-book inventory. Close-out refreshes authoritative state before
cancelling and does not cancel an order already proven terminal; a genuinely
live remainder uses the existing cancel path; any uncertain mutation outcome
stays fail-closed.

Afterwards: reconcile against venue open orders and positions, and on fills
a GET-only settlement/cashflow follow-up. Realized P&L is recorded only
after fill + settlement + attributable cashflow.

Probe 05 only. No automatic Probe 06. No risk increase.
