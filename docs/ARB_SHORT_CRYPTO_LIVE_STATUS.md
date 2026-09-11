# ARB Short-Crypto Live Status

Last updated: 2026-08-27 15:55 ET

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

## Inventory-maker q=1 canary — READY FOR FRESH LIVE APPROVAL

Feature checkpoint: `93217457dae996636b1cb4a3c40037ffeba53e69` on `feature/gemini-short-crypto-live-q1`.

Behavior:
- BTC 5m only.
- Max q=1 posted per outcome.
- BUY LIMIT MOC / maker-only entries.
- Requires >=1c initial modeled matched-pair edge after conservative maker fees.
- Requires >=180 seconds to expiry at entry.
- A first one-sided/fractional fill does **not** trigger normal flattening. The opposite maker order keeps working until the bounded work window ends.
- Remaining maker orders are cancelled before expiry.
- Any observed inventory within q<=1/outcome is held for authoritative settlement; no normal SELL exit.
- Operational second-post failure may use the proven exact-quantity emergency flatten helper.
- Executed inventory remains non-realized until authoritative Gemini settlement/reconciliation.
- Uses a separate inventory-specific dual-key arm namespace so the old canary variables cannot arm it.
- Uses inventory-specific AWS-local ledger classification and does not label one-sided inventory as paired arbitrage.

Successful disarmed AWS preflight at ~15:55 ET on 2026-08-27:
- 13 tests passed in 0.40s.
- Market: `GEMI-BTC05M2608272000-UP`.
- Seconds to expiry: 297.084.
- q=1 supported: true.
- YES bid / maker quote: $0.58.
- NO maker quote: $0.31.
- Pair cost before fees: $0.89.
- Conservative maker fees: $0.01 YES + $0.01 NO.
- Modeled matched-pair edge after fees: +$0.09.
- Inventory entry floor: +$0.01.
- Gate passed: true.
- Account clean: active short-crypto orders 0, positions 0, local reservation false.
- Order entry during preflight: false.

Disarmed preflight runner: `deploy/aws/run_gemini_btc5m_inventory_maker_q1_preflight.sh`.

One-shot live launcher is now ready on `aws-migration-foundation` at commit `1a878a27c85f381db98b17116e3715a5e4d4b3da`:
`deploy/aws/run_gemini_btc5m_inventory_maker_q1_live_once.sh`.

The live launcher re-runs the exact safety suite, a fresh authenticated DISARMED preflight, and a clean-account/local-state gate immediately before generating a unique inventory arm ID. It launches detached, runs exactly one campaign, has no restart/loop, and performs a read-only post-run audit. **Do not run without fresh explicit user approval. Do not rerun after an executed/ambiguous result until authoritative reconciliation clears the reservation/exposure.**

## Replica profiling / live teacher work

On `ev-wallet-shadow`, `src/polymarket_l5_replica_profile.py` profiles the completed full-history spool without refetching data. It measures fill sizes, first-fill timing, inter-fill gaps, side switching, opposite-side lag, inventory imbalance, FIFO pair lag/cost, and stream-level behavior.

The existing live Polymarket wallet follower remains the teacher-data lane. The next research upgrade is pre-fill rolling CLOB book capture aligned to each newly observed wallet fill, so the hidden resting quote / repricing policy can be inferred rather than guessed.

## Next gate

1. Obtain fresh explicit approval for exactly one q=1 inventory-maker live campaign.
2. Run `deploy/aws/run_gemini_btc5m_inventory_maker_q1_live_once.sh` once.
3. Classify the result from authoritative fills:
   - zero fill => no trade, release reservation;
   - matched inventory => executed, not realized until settlement;
   - one-sided/imbalanced inventory => valid inventory-strategy execution, not paired arb, not realized until settlement;
   - ambiguous cancel/operational failure => hard stop, no further live run until Gemini state is reconciled.
4. After settlement, reconcile actual fill prices, fees, payout, any maker rebate/reward actually credited, and net realized P&L.
5. Do not scale from q=1 until repeated live evidence supports positive realized economics.

## Profit accounting

As of this checkpoint for this workstream:
- Realized live-probe venue P&L from canary #1: +$0.64.
- Realized paired-arbitrage P&L: $0.
- Canary #2 realized P&L: $0 (no trade).
- Inventory-maker q=1 preflight realized P&L: $0 (no order entry).
- Total realized live-probe venue P&L in this workstream: +$0.64.
- Reference-wallet / backtest / shadow economics are separate and are not user realized profit.
