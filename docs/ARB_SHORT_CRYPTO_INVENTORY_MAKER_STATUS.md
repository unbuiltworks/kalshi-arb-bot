# ARB Short-Crypto Inventory-Maker Status

Last updated: 2026-08-27

## Objective

Replicate the observable maker/inventory behavior of Polymarket reference wallet `@l5Zn1bWoM8eTsK` / `0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68` as closely as public data permits, then validate the behavior independently on Gemini Prediction Markets. Reference-wallet/backtest economics are never user realized P&L.

## Empirical reference profile

Completed full-history profile: 679,504 TRADE rows across 14,653 short-crypto markets.

BTC 5m key behavior:
- 6.41 buys/window average.
- ~0 routine sells.
- median fill size 35 shares.
- first trade median 71 sec after open.
- first opposite-side fill median 67 sec after first-side fill.
- inter-fill gap median 10 sec.
- two-sided in ~65.56% of markets.
- median side switches 1.
- median FIFO pair cost ~0.9700; FIFO pairing is historical decomposition, not proof of atomic arbitrage.

BTC 15m is more continuous-market-maker-like:
- 120.30 buys/window average.
- ~0 routine sells.
- first trade median 18 sec after open.
- first opposite-side fill median 27 sec.
- inter-fill gap median 2 sec.
- two-sided in ~99.19% of markets.
- median side switches 28.
- median FIFO pair cost ~0.98296.

Exact resting quote/cancel policy is not fully recoverable from fill history because unfilled private orders are not public.

## Live reference-wallet follower

Railway project `arb-ev-wallet-shadow` has a live read-only `polymarket-wallet-shadow` service. It polls the reference wallet and checks the contemporaneous public CLOB after newly observed fills. It is shadow-only and never places orders. Fresh observations continue to show repeated maker-like fills on both outcomes, including common 50-share clips plus partial/fractional fills.

Do not literal-copy the wallet as a taker after public detection: public-feed delay often means the price has moved, destroying maker economics. Use the live fills as teacher data for quote-policy inference.

## Gemini validated live state

Canary #1 authoritative result:
- only NO filled q=1 at $0.36;
- YES did not fill;
- market resolved NO;
- Gemini reported +$0.64 settled venue net profit;
- classification: one-sided live probe, NOT paired arbitrage.

Canary #2: clean no-trade because the fresh modeled pair edge fell to 0 before entry.

Current realized live-probe venue P&L: +$0.64.
Current realized paired-arbitrage P&L: $0.

## New inventory-maker q=1 canary

Feature branch: `feature/gemini-short-crypto-live-q1`.
Pinned checkpoint: `93217457dae996636b1cb4a3c40037ffeba53e69`.

New files:
- `src/gemini_short_crypto_inventory_local_budget.py`
- `src/gemini_short_crypto_btc5m_inventory_maker_canary_q1.py`
- `tests/test_gemini_short_crypto_inventory_maker_q1.py`

Behavior:
- BTC 5m only for the first live probe.
- q<=1 per outcome; no stacking above one share on either side.
- passive BUY LIMIT MOC maker orders on YES and NO.
- initial matched-pair modeled edge must be >=1c after conservative maker fees.
- at least 180 seconds to expiry at entry.
- one-sided passive fill is a valid inventory state, not an automatic emergency flatten.
- after a first fill, the opposite passive maker order continues working during a bounded inventory window.
- normal strategy path does not SELL; observed inventory is held to settlement.
- zero fills release the durable reservation.
- matched or one-sided fills remain reserved until authoritative Gemini settlement reconciliation.
- cancellation ambiguity, quantity >1, or second-leg post operational failure hard-halts; emergency exact-quantity flatten is retained for operational failure only.
- separate inventory-specific dual-key arm prevents the old canary environment variables from arming this strategy.
- AWS-local fsynced accounting remains required while Command Center/Neon is unavailable.

This is the first behaviorally-correct inventory canary, not yet the final full reference replica. It does not yet dynamically reprice the opposite order or use live fair-value/inventory skew; those are subsequent upgrades after q=1 inventory lifecycle validation.

## Current gate

AWS disarmed preflight runner:
`deploy/aws/run_gemini_btc5m_inventory_maker_q1_preflight.sh`

AWS branch commit adding it:
`77e3048ddb86955212f7b4e54794cf87631317ed`

The preflight must pass before any live inventory-maker approval. It runs the exact pinned image, offline regression tests, distinct client-ID/separate-arm proof, authenticated disarmed BTC5m market preflight, and read-only Gemini/local-ledger exposure audit.

No recurring/scaled live trading is approved. Any first inventory-maker live run requires a fresh explicit approval after the preflight is reviewed.
