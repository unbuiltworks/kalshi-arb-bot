# ARB Short-Crypto Inventory-Maker v3 Status — 2026-08-27

## Why v3 exists
Two consecutive approved q=1 inventory-maker live attempts stopped before order entry because the one-shot Gemini prediction-market `depth5@100ms` WebSocket snapshot produced no usable fresh depth. Both attempts were clean pre-trade failures: no local reservation, no maker session/order entry, no venue mutation, and $0 P&L.

The repo already contains a proven Gemini prediction-market REST-book transport (`GET /v1/book/{symbol}`) specifically because prediction WebSocket snapshots can omit usable depth. v3 reuses that existing market-data pattern rather than increasing WebSocket retries.

## v3 feature checkpoint
`5a131b5d755d8b59155d6b3845d9fba161656f0c` on `feature/gemini-short-crypto-live-q1`.

Entrypoint:
`src/gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3.py`

Behavior changes from v2:
- public BTC5m top-of-book reads use Gemini REST `/v1/book/{symbol}`;
- REST response must be complete, uncrossed, positive-depth, and <=750ms HTTP RTT;
- up to 3 bounded REST attempts, then fail closed;
- private Gemini maker order/fill transport is unchanged;
- underlying inventory strategy still re-reads market data immediately before reservation/order entry;
- live v3 may wait read-only up to 420 seconds for one BTC5m window with >=195 seconds remaining and >=1c modeled pair edge;
- waiting performs only public discovery/book reads and cannot reserve or submit orders;
- after readiness, underlying canary re-discovers/revalidates and can execute at most one q=1-per-outcome campaign.

## AWS runners
DISARMED preflight:
`deploy/aws/run_gemini_btc5m_inventory_maker_q1_v3_preflight.sh`
AWS commit adding it: `3493c5950af5707b601112a37286171876d4ad83`.

One-shot LIVE launcher, NOT YET APPROVED:
`deploy/aws/run_gemini_btc5m_inventory_maker_q1_v3_live_once.sh`
AWS commit adding it: `d07d40d6fc15f629758e1db10d1d483674e11ba0`.

## Current accounting / exposure
No executed trade resulted from either failed inventory-maker v1/v2 live attempt. Those failures are $0 P&L. Last authoritative live-probe realized venue P&L remains +$0.64 from canary #1's one-sided NO fill that settled in-the-money; paired-arbitrage realized P&L remains $0. Do not use that +$0.64 as evidence of the intended inventory strategy's EV.

## Immediate gate
1. Run v3 DISARMED AWS preflight.
2. Require exact-image tests pass, REST market preflight pass, and clean account/local reservation audit.
3. Obtain fresh approval for one v3 q=1-per-outcome campaign.
4. Execute/reconcile that one campaign.

## Overnight objective
User wants the maker strategy running overnight as soon as safely possible.

Do NOT jump directly from preflight to an uncapped overnight loop. After one clean v3 live lifecycle is authoritatively reconciled, prepare/arm a tightly bounded overnight supervisor with explicit approval. Initial overnight limits should remain q=1 per outcome, one campaign at a time, clean-account/local-reservation gate before every campaign, no overlapping windows, hard stop on any ambiguous order/cancel/reconciliation state, and an explicit maximum campaign count and loss cap. Scaling above q=1 is not approved.
