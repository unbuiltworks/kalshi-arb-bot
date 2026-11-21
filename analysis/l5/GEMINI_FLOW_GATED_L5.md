# Gemini Flow-Gated L5: Volume Distribution and Test Protocol

Date: 2026-08-31. Corrects the "dead venue" characterization: that read came
from a Sunday-evening sample plus an undercounting ws collection cycle.
Measured from 474 settled windowed events (public volume field):

## Volume distribution per window

| Series | n | median | ≥$250 | ≥$500 | ≥$1,000 | max |
|---|---|---|---|---|---|---|
| BTC 5m | 178 | $250 | 52% | 39% | **26%** | $7,454 |
| BTC 15m | 59 | $23 | 15% | 12% | 5% | $1,244 |
| ETH 5m | 178 | $0 | 20% | 11% | 1% | $1,100 |
| ETH 15m | 59 | $0 | 5% | 0% | 0% | $300 |

BTC5m median by UTC hour: 12:00–15:00 runs $1,217–$1,901; 21:00–00:00 runs
$507–$1,414; 16:00–20:00 is near-dead ($10–$100). **Gemini is bursty, not
dead — and its liquid duration is 5m, the opposite of Kalshi's 15m.**

## Design: flow-tiered activation (per review direction)

- Dead window (< ~$250 projected / tape silent): reward-quote only, max-width
  minimal-size, or nothing.
- Moderate flow: small size.
- High flow (≥$1,000-class): full bounded L5-style engine.
- Flow collapse mid-window: stop adding risk; manage/exit existing inventory.

The committed decision core already embeds the activation organically: quoting
is refused until 30 tape prints are observed (TAPE_WARMUP), so dead windows
never arm. The tiering layers on as size policy, not new logic. This also
reframes the first V5.4 canary: it ran in a quiet Sunday stretch, i.e., partly
a venue-timing artifact rather than purely a strategy result.

## Test protocol (pre-registered)

1. Capture Gemini tape during hot hours (12:00–15:00 UTC; wired into the daily
   12:00 routine) plus Kalshi tape as the control.
2. Bucket windows by settled volume ($0–250 / 250–500 / 500–1,000 / 1,000+),
   volumes fetched from settled events at evaluation time.
3. Per bucket, shadow-evaluate: fill rate (print-through proxy), completed-pair
   share, rescue/completion cost, net markout P&L after fees, for (a) the
   frozen tape-contra policy and (b) an L5-envelope replay.
4. Acceptance: if metrics improve monotonically with volume and the ≥$1,000
   bucket is net-positive, the gate is the missing piece and the existing
   V5.3/V5.4 machinery gains a volume-tier wrapper; if not, Gemini stays
   seat-holding only.

No constants of the frozen policy are tuned by this test.
