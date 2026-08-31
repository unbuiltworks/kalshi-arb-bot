# Gemini Burst Census — Interim (overnight 2026-08-31, 02:00–11:00 UTC)

First read of the volume-velocity experiment: every active Crypto window's
cumulative volume polled every 20s overnight. This sample is ALL dead hours —
the first hot-window capture (12:00–15:00 UTC) runs today.

## Volume by hour (UTC, final volume per window, 292 windows)

| Hour | n | median | p90 | max | share ≥$250 |
|---|---|---|---|---|---|
| 02 | 10 | $0 | $19 | $19 | 0% |
| 03 | 32 | $10 | $350 | $817 | 12% |
| 04 | 32 | $0 | $9 | $662 | 3% |
| 05 | 32 | $0 | $7 | $626 | 6% |
| 06 | 32 | $0 | $0 | $101 | 0% |
| 07 | 32 | $0 | $0 | $155 | 0% |
| 08 | 32 | $0 | $0 | $6 | 0% |
| 09 | 32 | $0 | $0 | $792 | 3% |
| 10 | 32 | $0 | $0 | $227 | 0% |

Overnight Gemini is a desert with occasional geysers: the median window trades
NOTHING, but 14/292 windows (~5%) did ≥$100, several $400–800, at essentially
random hours. Consistent with the Kalshi overnight lesson: no ambient flow to
make against off-peak.

## Burst shape (the 14 windows ≥$100)

- Median: ~42% of final volume done by the 40% mark of the window, ~98% by the
  60% mark. Overnight volume arrives as a single early/mid-window burst — one
  actor hitting the book over seconds-to-a-couple-minutes — NOT a steady tape
  and NOT an end-of-window rush.
- In at least one clean case (BTC05M 09:35): $32 by the 16% mark, then $760
  more by the 36% mark. A "first-flow wakes the quoter" trigger would have
  armed before the bulk of that burst. In several others the entire volume sits
  inside one 20s poll gap — burst over before any 20s-resolution reaction.
- Resolution caveat: only 1–4 in-window volume snapshots per window (20s poll +
  collector downtime), so burst duration is bounded, not measured.

## Instrumentation finding (fixed before today's hot window)

The ws trade stream captured ZERO prints for every big overnight window: the
collector reconnected every 45s to pick up newly rolled windows and kept
dropping the stream exactly when bursts hit. Fixed: one persistent ws session
(10 min), newly rolled windows subscribed live on the same connection.
Verified live: steady depth stream where before there was silence. Today's
hot-window tape will have print-level burst timing.

## What today's 12:00–15:00 UTC capture must answer

1. Hot-hour volume distribution per window (vs the earlier 474-window baseline:
   BTC5m median ~$250, 26% ≥$1,000).
2. Burst duration at print resolution: is there time to join after first flow?
3. Early-velocity → final-volume: does the first minute's volume predict a
   ≥$500 window well enough to gate quoting on it?
4. Spread/depth during bursts (from the now-working depth5 stream): what would
   a maker actually have been filled at?

## Provisional read for the flow-gated design

The dead/active regime split is even sharper on Gemini than on Kalshi. Any
Gemini maker MUST be flow-gated — the reward-farm tier (resting quotes for
incentives in dead hours) and the active tier (quote only on flow) are
different businesses, and the overnight data says the active tier simply does
not exist off-peak. Nothing here changes the frozen Policy D constants.
