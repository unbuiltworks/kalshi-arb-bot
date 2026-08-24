# ARB checkpoint — 2026-08-24 XRP Gemini-maker / Kalshi-hedge live canary

## Classification

This lane is **LIVE_THRESHOLD_DOMINANCE_INDEX_RISK_NOT_GUARANTEED_ARBITRAGE** because Gemini and Kalshi use different underlying index/settlement sources. Do not count modeled locked edge, accepted maker orders, or two-leg fills as realized arbitrage until authoritative settlement/reconciliation.

## User authorization

The user explicitly authorized a real-money test on 2026-08-24 after reviewing the read-only maker economics. Tests remained bounded to q<=1 and the existing $5 research-loss ceiling was not reset or increased.

## Pre-live evidence

- Gemini account 7-day aggregate history audit: 38 crypto prediction orders, 28 with fills, 0 fractional filled quantities observed historically. This is empirical history only; live code still assumes fractional maker fills are possible.
- Read-only XRP maker economics showed that tiny partial fills can be negative after cent-rounded fees.
- Partial-resilient quoting therefore preserves >=$0.03/contract at a configured 0.25 first-fill quantity after one adverse Kalshi tick; smaller unsafe fills must be flattened/hard-halted rather than hedged as arbitrage.
- Shared Command Center maker budget reserves $1 before mutation and enforces the existing $5 realized-loss research ceiling.

## Live order attempt 1

Campaign: `xrp-maker-20260824-1240et-a1`

Deployment: `4ff13ac8-ddc8-4c31-aa40-b2c6a18d509f`

- Gemini accepted a real MOC maker BUY YES at $0.48.
- Order was recovered by account state.
- Modeled q1 edge at placement: $0.08; modeled 0.25 first-fill edge: $0.03/contract after one adverse Kalshi tick.
- No fill occurred.
- Bot canceled successfully by authenticated WS when independent Gemini/Kalshi book freshness diverged.
- Terminal status: `MAKER_CANCELED_ZERO_FILL`.
- Realized P&L: $0.00. No Kalshi hedge was submitted.

This exposed a transport-observation issue: independent Gemini WS and Kalshi REST polling could create transient mixed-age snapshots even when neither venue was stale individually.

## Fill-wait attempt

Campaign: `xrp-maker-20260824-1247et-b1`

- Added at-bid admission: a partial-safe quote must be >= the current Gemini bid before posting.
- Added 100 ms Kalshi REST polling.
- No order was placed.
- Repeated full pair discovery hit Kalshi series 429; process failed closed before mutation.
- Realized P&L: $0.00.

Pair discovery and fee metadata were then cached for the bounded campaign. Tests increased to 57 passing.

## Atomic paired-book transport

New module: `src/gemini_kalshi_xrp_maker_triggered_canary.py`

Key commits:
- `da1b06b6c7a38fc1deb4bb03256df5f49d29f4b5` — Gemini-triggered exact Kalshi paired-book canary.
- `af68ab61811f6502b6dc976531ce6db6ef5a9214` — pair freshness tests.
- `950059c8f1a2883331a1d7b4df8f8e90ef4b0912` — Railway config switched to atomic triggered canary.

Architecture:
- Public Gemini bookTicker event is received and timestamped.
- It immediately triggers exact authenticated Kalshi `/markets/{ticker}/orderbook?depth=1`.
- Kalshi quote timestamp is conservative HTTP request start, not response arrival.
- Only pairs already <=50 ms skew and <=250 ms age enter admission/monitoring.
- Minimum 100 ms between Kalshi exact reads.
- 429/error produces no paired quote.
- Safety thresholds were not relaxed.

Disarmed production preflight:
- 61 tests passed.
- Three atomic pairs examined.
- Observed paired skew: 0 ms.
- XRP dominance gap: ~2.141 bp, safe direction `GEMINI_YES_KALSHI_NO`.
- Read-only partial-safe maker quote improved the live Gemini bid and modeled $0.08 on q1 / $0.03 per contract at q=0.25.
- Status: `READ_ONLY_READY_AT_BID`.

## Live order attempt 2 — atomic transport

Campaign: `xrp-maker-20260824-1302et-d1`

Deployment: `e8eb2904-4c82-4724-8fc1-dd094117a9c2`

- 61 tests passed before runtime.
- Gemini accepted a real MOC maker BUY YES at $0.08.
- Order was recovered.
- Admission book: Gemini bid $0.05 / ask $0.14; Kalshi NO observed hedge $0.80, one-tick cushioned $0.81.
- Atomic paired-book skew at admission: 0 ms.
- Modeled q1 edge: $0.08; modeled 0.25 first-fill edge: exactly $0.03/contract.
- No fill occurred.
- When hedge economics deteriorated enough that the configured partial-safe floor no longer held, maker remainder was canceled successfully.
- Terminal status: `MAKER_CANCELED_ZERO_FILL`, reason `PARTIAL_SAFE_FLOOR_DROPPED`.
- Realized P&L: $0.00. No hedge was submitted.

## Current live state

- Dedicated XRP maker canary gate is OFF.
- `CRYPTO_MAKER_CANARY_ARM` is empty.
- No maker order from these campaigns remains intentionally armed.
- New realized maker P&L from this work: **$0.00**.
- The previously discussed 8 cents is modeled full-fill edge, not earned profit.

## Highest-impact next step

Transport timing is now validated: real Gemini maker orders can be posted/recovered/canceled safely with atomic <=50 ms paired books. The remaining bottleneck is **maker fill probability while preserving partial-fill economics**.

Next work should measure/implement bounded zero-fill repricing within one reserved campaign (cancel/reprice at the current partial-safe frontier, only one maker active at a time, stop on first fill) rather than weakening the 3-cent floor or repeatedly launching independent campaigns. First fill must still trigger immediate remainder cancellation, exact quantity economics, Kalshi FOK hedge or Gemini flatten, and hard halt on ambiguity.
