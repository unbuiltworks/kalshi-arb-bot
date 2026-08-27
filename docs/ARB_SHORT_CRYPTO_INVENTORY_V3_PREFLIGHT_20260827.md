# Gemini BTC5m Inventory-Maker v3 Preflight — 2026-08-27

Status: PASSED, DISARMED, no order entry.

Feature checkpoint: `5a131b5d755d8b59155d6b3845d9fba161656f0c` on `feature/gemini-short-crypto-live-q1`.

Observed AWS preflight:
- 18 tests passed in 0.32s.
- Market: `BTC05M2608272035` / `GEMI-BTC05M2608272035-UP`.
- Seconds to expiry: 270.657.
- q=1 supported: true.
- Market data: Gemini public REST book.
- YES bid / maker quote: 0.32.
- NO maker quote: 0.59.
- Conservative maker fees modeled: 0.01 YES + 0.01 NO.
- Pair cost before fees: 0.91.
- Modeled matched-pair edge after fees: +0.07.
- Inventory entry floor: +0.01.
- Gate passed: true.
- Active short-crypto orders: 0.
- Active short-crypto positions: 0.
- AWS-local open reservation: false.
- Order entry: false.
- Mutation: false.

Interpretation:
- The v3 REST-book market-data path resolved the transient one-shot WebSocket depth failure seen in v1/v2.
- This preflight is not a trade and generated $0 realized P&L.
- The +0.07 edge is a point-in-time observation only and must not be reused as stale authorization.

Next gate:
- Fresh explicit user approval is required for exactly one q=1-per-outcome v3 live inventory-maker campaign.
- Authorized live launcher, once approved: `deploy/aws/run_gemini_btc5m_inventory_maker_q1_v3_live_once.sh`.
- Launcher is pinned to `5a131b5d755d8b59155d6b3845d9fba161656f0c`, reruns the exact-image safety suite, performs a fresh authenticated REST-book preflight and clean-account audit, then can run at most one campaign.
- No scaling or overnight recurrence is approved by this preflight.
- Any executed inventory remains non-realized until authoritative Gemini fill/settlement reconciliation.
