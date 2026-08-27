# ARB Short-Crypto Live Status

Last updated: 2026-08-27 13:41 ET

## Scope

This is the durable handoff for the isolated short-duration crypto maker workstream. Keep it separate from the sports AWS maker migration and from the unrelated SOL executor.

## Current venue / strategy

- Venue: Gemini Prediction Markets.
- Product under live validation: BTC 5-minute binary market.
- Mode: isolated q=1 live probe only.
- Intended strategy: two-sided maker BUYs on YES and NO when fresh modeled pair edge after conservative maker fees is >= $0.03.
- Live scaling is NOT approved. One campaign at a time only.

## Why Gemini

Polymarket US does not expose the short-duration 5m/15m crypto markets needed for the reference-wallet strategy. Gemini currently exposes short-window crypto markets, so Gemini is the live validation venue. Do not assume Polymarket international economics, maker rewards, fees, settlement, or historical ROI transfer to Gemini.

## AWS state

- Existing EC2 host is used through SSM.
- Gemini secrets are stored in `/opt/arb/secrets/gemini-short-crypto.env` with mode `640 root:arb`.
- AWS-local durable fallback state directory: `/opt/arb/gemini-short-crypto-state`.
- The central Command Center ledger is currently unavailable because its Neon database exceeded data-transfer quota. Do not bypass accounting silently.
- Temporary fallback uses Gemini direct exposure checks plus an fsynced AWS-local journal/open-reservation file. Events are marked for later replay into Command Center.

## Canary #1 authoritative result

Market: `GEMI-BTC05M2608271730-UP`

Original modeled entry snapshot:
- YES maker quote: $0.42
- NO maker quote: $0.36
- Modeled pair cost: $0.78
- Modeled pair edge after conservative fees: $0.20

Authoritative Gemini reconciliation:
- YES order: cancelled, filled quantity 0.
- NO order: filled 1 at $0.36.
- Market resolution: NO.
- Payout: $1.00.
- Venue-reported net profit: +$0.64.

Accounting classification:
- Actually executed: one-sided NO maker fill.
- Settled / realized venue P&L: +$0.64.
- Paired-arbitrage realized P&L: $0.
- Two-sided maker strategy validated: NO.
- Gemini BTC5m maker fill + settlement plumbing validated: YES.
- Do not use the +$0.64 as evidence of paired-arbitrage EV; the one-sided position happened to win.

Post-reconciliation state:
- Active short-crypto orders: 0.
- Active short-crypto positions: 0.
- AWS-local open reservation: false.

## Canary #1 bug and fix

Bug: YES and NO client-order IDs were formed by appending `-yes` / `-no` to a long trade ID and then truncating to 36 chars. Both collapsed to the same client ID, causing reconciliation to count the same order twice.

Fix on `feature/gemini-short-crypto-live-q1`:
- Client IDs now use a deterministic SHA-256 digest of the trade ID plus outcome suffix.
- IDs remain <=36 chars and are distinct (`...-yes`, `...-no`).
- Regression test uses the same long canary-ID shape that caused the collision.
- Exact fractional-fill flatten logic remains in place.

Current fixed feature checkpoint: `282522639047b3d41b2f37cb29758b4fe7e39e65`.

The old AWS live launcher `deploy/aws/run_gemini_short_crypto_btc5m_live_q1_local_once.sh` is intentionally hard-disabled and must not be reused.

## Canary #2 preflight — PASSED

Disarmed AWS preflight passed on fixed commit `282522639047b3d41b2f37cb29758b4fe7e39e65`.

Observed preflight market: `GEMI-BTC05M2608271750-UP`.
- Safety tests: 8 passed.
- Gemini terms accepted: true.
- Available USD: $62.4255.
- q=1 supported: true (`quantityMinimum=0.01`, `quantityIncrement=0.01`).
- YES bid / maker quote: $0.27.
- NO maker quote: $0.48.
- Modeled pair cost: $0.75.
- Conservative modeled pair edge: $0.23.
- >=$0.03 live floor: passed.
- Active short-crypto orders: 0.
- Active short-crypto positions: 0.
- AWS-local open reservation: false.
- Order entry: false; mutation: false.

This snapshot is stale immediately after observation and must never be reused as a live price. The live runner must fetch a fresh book and pass the >=$0.03 floor again.

## Canary #2 live gate

A new one-shot launcher exists at `deploy/aws/run_gemini_short_crypto_btc5m_canary2_live_once.sh` on `aws-migration-foundation`.

Safety behavior:
1. Pins the exact fixed feature commit `282522639047b3d41b2f37cb29758b4fe7e39e65`.
2. Rebuilds and reruns quantity-grid, client-ID uniqueness, fractional-flatten, and local-ledger tests on the exact live image.
3. Runs another authenticated DISARMED BTC5m market preflight.
4. Calls the clean-account gate and fails closed on any existing short-crypto order, position, or local reservation.
5. Generates the actual canary ID and proves the exact YES/NO client IDs are distinct and <=36 chars before arming.
6. Rechecks a fresh modeled pair edge >=$0.03 again inside the live process before ledger reservation/order entry.
7. Uses AWS-local fsynced reservation before the order-entry session connects.
8. Runs exactly one q=1 BTC5m campaign and has no restart loop.
9. Any one-sided or fractional fill is risk, not arb; exact observed inventory is flattened via IOC and retained for reconciliation if flatten is incomplete.
10. After execution, each outcome must be reconciled independently from authoritative Gemini order history/settlement before any profit classification.

Fresh explicit user approval is required before this launcher is run.

## Profit accounting

As of this checkpoint for this workstream:
- Realized live-probe venue P&L from canary #1: +$0.64.
- Realized paired-arbitrage P&L: $0.
- Canary #2: not yet executed.
- Reference-wallet / backtest / shadow economics are separate and are not user realized profit.
