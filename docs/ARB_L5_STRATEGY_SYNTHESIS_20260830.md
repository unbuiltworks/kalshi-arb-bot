# ARB L5 Strategy Synthesis — 2026-08-30

## Decision

Do not copy L5 fill-for-fill, and do not require pristine two-sided geometry before participating.

The current build hypothesis is the composition of three layers:

1. **Operating envelope from L5 behavior**
   - enter near window open;
   - maintain continuous two-sided maker presence;
   - tolerate bounded temporary directional imbalance;
   - allow bounded defensive taker completion later in the window;
   - stop roughly 60–90 seconds before expiry.

2. **Selective edge from frozen tape-contra Policy D**
   - bias quoting toward the side the recent tape is *not* accumulating;
   - require quote price >= $0.30;
   - treat the constants as frozen until fresh-data confirmation;
   - never count modeled rebates as realized P&L.

3. **Execution/risk substrate from Gemini adaptive maker V5.5**
   - collision-proof campaign/client IDs;
   - bounded rescue and campaign-loss controls;
   - persistent daily-loss accounting;
   - fail-closed reconciliation;
   - sanitized venue reject observability;
   - no live promotion until release qualification is clean.

## Evidence

Claude commit `5acbb65f7e61c35099f26f4f8e72ed4f717cc969` inferred the L5 BTC 15m operating machine from 240 recent windows / 42,371 BUY fills. Key measured behavior:

- first fill median 9 seconds after open;
- roughly symmetric two-sided fill flow regardless of imbalance;
- temporary directional residual tolerated while inventory grows;
- same-side continuation after own-side price drops ~66%;
- taker share rises late in the window to ~8.9% of fills;
- last fill median 87 seconds before expiry;
- 78% of windows end with matched pair cost < $1, achieved dynamically rather than demanded per trade.

The same commit compared three 10%-scale replays on the same tape:

| Policy | Trading P&L | P&L + modeled rebate | Peak exposure | $/hour |
|---|---:|---:|---:|---:|
| L5 behavior clone + risk caps | -$158.87 | +$12.61 | ~$247 | $0.21 |
| Raw accept-all L5 behavior | -$243.64 | +$41.30 | ~$610 | $0.69 |
| Frozen tape-contra Policy D | +$450.53 | +$565.66 | ~$254 | $9.47 |

Interpretation: L5's raw behavior is optimized for incentive-scaled volume, not small-bankroll trade-selection quality. At our scale, the operating cadence is useful but the selective tape-contra rule is the economically interesting component.

## Promotion gates

This synthesis is **not** live authorization. Promotion requires both:

1. **Fresh-data confirmation** of the frozen Policy D constants with no retuning on the confirmation slice.
2. **Accessible-venue portability** on Kalshi and/or Gemini short-duration crypto using read-only/shadow data, with positive after-fee economics and sufficient opportunity frequency.

If either fails, keep the L5 operating envelope as a research prior but do not promote Policy D live.

## Current Gemini live evidence

The first bounded V5.4 continuous session produced one genuine maker fill and one genuine bounded rescue, realizing -$0.17 for that campaign. A later apparent fill was proven to be a reconciliation ghost caused by truncated client-order-ID reuse across campaigns. V5.5 exists to correct that incident class. No additional V5.5 live authorization is implied by this document.

## P&L discipline

Always distinguish:

- historical/replay modeled P&L;
- shadow opportunity P&L;
- submitted orders;
- confirmed fills;
- matched-but-unsettled inventory;
- settled/realized P&L;
- modeled or realized incentives.

Only settled venue-confirmed cash outcomes count as realized profit.
