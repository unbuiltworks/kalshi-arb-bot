# q1 bounded live-probe plan — TWO_SIDED_FAVORITE_TRAIL

**NOT AUTHORIZED. NOT EXECUTED.** This document exists because
`PREREGISTRATION_C.md` required it to be prepared if the shadow mechanics
survived without semantic or accounting defects, and they did. Preparing it
is not authorization to run it, and nothing in this file may be read as
approval. No order has been placed.

It is also written against an honest premise: **C did not make money in
shadow** (−$0.157 per window over 28 windows). The probe below is therefore
a **mechanism test**, not a profit attempt, and its success criterion is
mechanical fidelity, not P&L.

## What the probe would be testing

Six things that shadow cannot establish, in this order:

1. that a taker seed on both sides is accepted and fills at the price the
   book displayed (the `price.value` long-side mapping for the DOWN leg is
   where 24 of 24 orders were rejected on 2026-09-05/06);
2. that an extension submits and fills while a 60 s loop is running;
3. that the reversal detector fires on live book updates, not only on marks;
4. that a residual exit actually executes at or near the displayed touch,
   which is the single number the whole variant depends on;
5. what the realized slippage is against the shadow's assumed fill price;
6. that settlement credits what the ledger expects, audited per leg.

## Hard limits, per window

| limit | value | why this number |
|---|---|---|
| allocation size | **$1** | the frozen shadow size; not raised for live |
| seed | $1 UP + $1 DOWN, once | as frozen |
| max extensions | **4** | shadow median extensions per window was ~7; 4 is below the behaviour being tested, deliberately |
| max gross cash out per window | **$6.00** | C's worst-window max capital in shadow was exactly $6.00 |
| max directional residual | **8 shares** | shadow worst-window residual was 6.6 shares |
| max loss per window, hard stop | **$2.00** | ~3.5x C's worst shadow window (−$0.58 drawdown), flatten and stand down on breach |
| cumulative loss across the probe, hard stop | **$6.00** | three windows' worth; on breach the probe ends and does not relaunch |
| concurrent markets | **1** | unchanged |
| outstanding orders | **1** | unchanged |
| windows | **3**, non-consecutive, chosen in advance by clock, never by looking at the book | so the sample cannot be selected for favourability |

No limit above may be raised to make the probe produce activity, and a
window where the limits prevent the full sequence is reported as
limit-bound, not retried with looser bounds.

## Execution constraints

- **Taker only.** C's shadow result uses no maker fill, so a live maker leg
  would be testing a mechanism the shadow did not model.
- Every order through the proven serializer (`src/l5_order_body.build`), so
  the DOWN leg is priced as long-side `1 − X` and cannot repeat the 2026-09
  rejection class.
- Fees from `src/pmus_fees.py`, never a transcribed constant.
- The reversal detector reads the **WebSocket** book. The executor has no
  WS→order path today (`grep -c "websocket|ws_evidence|/v1/ws/"
  src/pmus_q1_executor.py` → **0**), so this probe requires the read-only WS
  feed designed in `PMUS_CONTINUOUS_INVENTORY_GATES.md` to be built first.
  That is a prerequisite, not a detail.
- Fail closed: on disconnect, on a rejected order, on an unexpected
  position, or on hitting any limit above, cancel everything, flatten the
  residual if one exists, stop, and **do not relaunch automatically**.
- Deadman cancellation armed for the whole session.

## Success criteria — mechanical, not financial

The probe succeeds if, for each window: both seed legs filled at the
displayed touch; every extension submitted inside its 60 s period; the
reversal fired and the exit filled within 2 s of the trigger; realized
slippage against the shadow's assumed price is reported per leg; settlement
reconciled per leg against the authoritative ledger; and no limit was
breached.

It **fails** — and the lane stops — if any leg is rejected, if an exit
cannot be executed near the touch, or if realized slippage materially
exceeds what the shadow assumed, because the shadow's exit assumption is
then wrong and every number in the three result documents inherits that.

## Accounting labels for the probe

`submitted` → `filled` → `settled` → `realized`, in that order, per leg. No
figure from the probe may be called realized profit before settlement
reconciliation, and the shadow results remain labelled `historical replay`
regardless of what the probe does.
