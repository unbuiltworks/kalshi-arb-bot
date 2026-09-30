# P0 FINDING: the continuous event stream already exists, and every analysis so far used 0.77% of it

**Observed live at 2026-09-30T13:32:40Z on the running service
`pmus-ws-evidence-recorder`** (`conn=connected`, `auth=ok`,
`messages_received=4,647,866`). No new collector is required. It was already
running while I built four rounds of analysis on a 50-sample-per-window tape.

## What the live recorder is producing right now

| | live recorder | the tape every analysis used |
|---|---|---|
| book states / 15-min window | **~6,500** (1315z full window: **7,656**) | **50** |
| book update cadence | **~100 ms** (raw `t` deltas 0.0997s) | bimodal 2–3s bursts, then ~28s blind |
| TRADE prints / window | **~3,195** | **zero** |
| per-trade detail | px, qty, venue `trade_time`, **`bid_at_trade`/`ask_at_trade`**, taker direction | only `shares_traded_delta`, an interval aggregate |
| depth | full L2, per side, per state | top of book |

**We analysed 0.77% of the available book states, and none of the trade prints.**

Every conclusion I have drawn about opportunity frequency — "0.98 pairs per
window", "3.98 fills per window", "37 of 67 episodes are a single observation",
"C is unmeasurable from this tape" — was measured through a 130× undersample of
a stream that was being recorded at 100ms the whole time. Those numbers describe
our *observation path*, not this market.

## This answers the primary question directly

> Are we failing because profitable liquidity isn't available, or because our
> architecture participates in only a tiny fraction of an extremely active
> market?

**The latter, and the fraction is now measured rather than inferred.** A market
serving ~6,500 book states and ~3,195 trade prints per window was being sampled
50 times, with an engine acting ~1–3 times per minute. Liquidity availability was
never the constraint being tested; observation was.

This also retires the "C is unmeasurable" claim for good. Trade prints with the
book stamped at the instant of each trade are exactly what a causal fill model
needs, and they exist.

## What changes in the plan

1. **The packaging blocker is off the critical path.** The shadow collector was
   blocking forward data. It no longer is: the recorder already carries
   `pmus_ws_evidence_recorder` and is already streaming. The packaging gate
   still needs the real-build escalation before anything new is deployed, but it
   no longer gates this experiment.
2. **No new deployment for the tape.** Harvest the existing stream. The
   harvest path already exists (`data/pmus_btc15m_tape/raw_railway_logs/`), and
   the recorder emits `PMUS_BOOK_ROWS` and `PMUS_TRADE_ROWS` with provenance,
   schema and venue timestamps already attached.
3. **The causal simulator gets built against event time, not snapshots** — 100ms
   book states, real prints, real queue depletion, with an order placed at T
   using only information at or before T.

## Honest statement of what this does to earlier work

The mechanism *identification* survives: persistent complement, basis-improving
extend, reactive fee-safe take. The *magnitudes* do not — all of them were
computed on 0.77% of the book states and with no trade prints, so every
frequency figure is a floor of unknown tightness, and the fill assumptions were
carrying weight that real prints will now carry instead.

I am not restating the $/window numbers until they are recomputed on the event
stream. The one figure I would still defend is edge per deployed dollar, because
it is a ratio of realised quantities rather than a frequency.

## P0 sequence from here

1. Harvest a contiguous set of full windows of `PMUS_BOOK_ROWS` + `PMUS_TRADE_ROWS`, preserving receipt time, venue `trade_time`, full depth and provenance.
2. Build the causal persistent-order simulator on that stream: price, qty, queue ahead, depth changes, real prints, partial fills, cancel/replace, both-outcome inventory, aggregate basis, matched/residual, fees, credited incentives, settlement. Touch is never a fill; displayed depth is never a fill.
3. Measure cycles/window, fills/min, contracts/min, turnover, matched and residual P&L, fees, incentives, net, peak capital, worst residual, net/peak-capital — against our engine and the L5 signature.
4. Freeze V1 from diagnostic evidence, then evaluate on windows collected *after* the freeze.

No live orders, no R7, no capital change, no modification to the frozen baseline
or Variant C.
