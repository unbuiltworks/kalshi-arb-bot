# ARB Short-Crypto Live Status

Last updated: 2026-08-27 14:00 ET

## Scope

This is the durable handoff for the isolated short-duration crypto maker workstream. Keep it separate from the sports AWS maker migration and from the unrelated SOL executor.

## Current venue / strategy

- Venue for live validation: Gemini Prediction Markets.
- Product under live validation: BTC 5-minute binary market.
- Live mode remains isolated q=1 probes only unless separately authorized.
- **Primary strategy target has changed from instant-pair canary logic to observable reference-wallet replication.**
- Reference target: Polymarket `@l5Zn1bWoM8eTsK` / `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`.
- Live scaling is NOT approved.

## Reference-wallet replication target

The goal is to reproduce the reference wallet's observable short-crypto execution pattern as closely as public data permits, then adapt it to Gemini's independent fees/rewards/queue/settlement rules.

Observable target behavior:
1. Participate in eligible 5m windows as a maker rather than waiting for a simultaneous locked pair.
2. Maintain passive YES/NO liquidity and accumulate fills over time.
3. Allow bounded temporary one-sided inventory; a one-sided passive fill is not automatically an error.
4. Continue working the opposite side when doing so improves inventory economics.
5. Use fair value + inventory to skew quote placement/size.
6. Hold matched inventory to settlement; avoid routine SELL exits.
7. Use taker fills selectively rather than as the default execution mode.
8. Keep maker trading/spread, maker rebate, liquidity reward, and residual directional P&L as separate accounting buckets.

Known public evidence from the research lane:
- Recent 10k-fill maker/taker comparison inferred ~83.28% maker fills and ~16.72% taker fills.
- Recent history is overwhelmingly BUY + REDEEM rather than SELL scalping.
- Two-sided inventory is common, especially in 15m, but historical FIFO pairing is not proof of atomic arb.
- 5m direct trading economics looked materially stronger than 15m in the audited recent sample.
- Maker rebates and liquidity rewards are first-class parts of the reference wallet's economics.

Exact quote placement/cancel policy cannot be recovered from fill history alone because public history does not expose resting orders that never filled. Therefore "exact replication" means exact observable behavior where measurable, plus forward quote/book alignment to infer the hidden quote policy without pretending it is known.

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
- Exact fractional-fill flatten logic remains in place for the safety canary.

Current fixed feature checkpoint: `282522639047b3d41b2f37cb29758b4fe7e39e65`.

The old AWS live launcher `deploy/aws/run_gemini_short_crypto_btc5m_live_q1_local_once.sh` is intentionally hard-disabled and must not be reused.

## Canary #2 live attempt — CLEAN NO-TRADE

Disarmed preflight first showed `GEMI-BTC05M2608271750-UP` with a stale modeled +$0.23 pair edge. User approved one q=1 campaign.

Fresh live market selected: `GEMI-BTC05M2608271800-UP`.
- YES maker quote: $0.48.
- NO maker quote: $0.50.
- Pair cost before modeled fees: $0.98.
- Conservative maker fees: $0.01 YES + $0.01 NO.
- Modeled pair edge after fees: $0.00.
- Required safety-canary floor: >=$0.03.
- Gate result: FAILED.
- Process stopped with `PAIR_FLOOR_BELOW_3C`.
- Order entry: false.
- Venue mutation: false.
- Active short-crypto orders after run: 0.
- Active short-crypto positions after run: 0.
- AWS-local open reservation after run: false.
- Realized P&L for canary #2: $0.

Interpretation: the stale instant-pair snapshot did not persist. This is useful evidence that instant-pair gating is not the correct primary model for reference-wallet replication.

## Replica profiling work

On `ev-wallet-shadow`, `src/polymarket_l5_replica_profile.py` was added at commit `46c216b06e36c5fadb7216ba6534e489730c876e`.

It reads the already-completed full-history SQLite spool only and profiles:
- fill-size distribution;
- first-fill timing;
- inter-fill gaps;
- YES/DOWN side-switching;
- same-side run lengths;
- first opposite-side fill lag;
- temporary inventory imbalance;
- FIFO pair lag and pair-cost distributions;
- results by 5m/15m and asset stream.

It makes no API calls and has no order authority.

AWS read-only runner: `deploy/aws/run_l5_reference_replica_profile.sh` on `aws-migration-foundation`.

## Next gate

Do not blindly rerun the old instant-pair live canary. Next:
1. Run the full-history reference replica profiler against the existing AWS spool.
2. Use those observed distributions to parameterize a Gemini BTC5m reference-replica shadow: continuous passive two-sided quoting, bounded inventory carry, opposite-side accumulation, fair-value/inventory skew, hold-to-settlement accounting.
3. Add forward reference-wallet fill/book alignment because history alone cannot reveal unfilled resting quotes/cancel policy.
4. Validate queue/fill and inventory economics in shadow.
5. Only then propose a new q=1 **inventory-maker** live probe under fresh explicit approval. That probe should not use the old "first one-sided fill = emergency failure" semantics except for hard exposure limits or genuinely ambiguous states.

## Profit accounting

As of this checkpoint for this workstream:
- Realized live-probe venue P&L from canary #1: +$0.64.
- Realized paired-arbitrage P&L: $0.
- Canary #2 realized P&L: $0 (no trade).
- Total realized live-probe venue P&L in this workstream: +$0.64.
- Reference-wallet / backtest / shadow economics are separate and are not user realized profit.
