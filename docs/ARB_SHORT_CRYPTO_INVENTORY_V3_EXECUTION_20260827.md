# Gemini BTC5m Inventory-Maker v3 — First Executed Campaign

Date: 2026-08-27

## Authorized campaign

- Strategy: `MAKER_FIRST_TEMPORAL_INVENTORY`
- Venue/product: Gemini Prediction Markets BTC 5-minute
- Feature checkpoint: `5a131b5d755d8b59155d6b3845d9fba161656f0c`
- Quantity cap: q=1 per outcome
- User authorization: exactly one campaign; consumed by this run
- Overnight/scaling authorization: NOT included

## Pre-live state

- 18 safety tests passed.
- Gemini REST-book market data path passed.
- Account clean: active short-crypto orders 0, positions 0.
- Local reservation false before live start.

## Executed market

- Event: `BTC05M2608272040`
- Symbol: `GEMI-BTC05M2608272040-UP`
- Live window became ready at about 296 seconds to expiry.
- Fresh modeled maker quotes: YES $0.44, NO $0.43.
- Conservative modeled pair edge at entry snapshot: +$0.11 after modeled maker fees.
- Both maker-only BUY LIMIT MOC orders were accepted and posted with distinct client IDs.

## Live behavior

- A first execution event was observed on client ID ending `-no`, quantity 1.00.
- Strategy correctly did NOT panic-flatten; it kept the opposite maker working.
- At bounded-work completion both maker orders were cancelled successfully.
- Final REST reconciliation reported `yes_fill=1`, `no_fill=0`.
- This live-stream-vs-final-reconciliation outcome-label disagreement must be resolved from exact client IDs plus settled positions before overnight trading.
- Final campaign classification: `ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED`.
- This is an actually executed live inventory trade, NOT paired arbitrage and NOT realized P&L yet.

## Post-run state

- Active short-crypto orders: 0.
- Active short-crypto positions endpoint: 0.
- AWS-local reservation: true, intentionally retained pending settlement.
- Local trade ID: `crypto-gem-btc5m-inventory-aws-btc5m-inv-q1-v3-20260827T203457Z-`.
- Realized P&L currently countable from this campaign: $0 until authoritative settlement reconciliation.

## Settlement reconciliation

A new exact-client reconciler is implemented on `feature/gemini-short-crypto-live-q1` and generalized for one-sided, matched, and fractional bounded inventory.

Current reconciler/test checkpoint: `aa0ed03cecaf37e1cf8fc89d88f0d4e6749cfc8f`.

AWS runner:
`deploy/aws/run_gemini_btc5m_inventory_settlement_reconcile.sh`

The reconciler:
- derives exact distinct YES/NO client IDs from the reserved trade ID;
- verifies Gemini order history maps `-yes` to YES and `-no` to NO;
- verifies authoritative filled quantities;
- requires matching Gemini settled positions;
- sums cost basis, payout, and venue `netProfit` across actual filled outcomes;
- records realized P&L and clears the local reservation only after all checks agree;
- never mutates Gemini orders/positions.

## Overnight supervisor prepared but NOT approved

Feature checkpoint: `692c2deb6a5681e43e15c6c24b91f0484a460ac5`.

AWS disarmed gate:
`deploy/aws/run_gemini_btc5m_inventory_overnight_preflight.sh`

AWS live launcher (requires fresh explicit approval ID; not currently authorized):
`deploy/aws/run_gemini_btc5m_inventory_overnight_live.sh`

Hard code-level ceilings for first overnight version:
- q=1 per outcome;
- one campaign at a time;
- no next campaign while any local reservation exists;
- automatic exact-client settlement reconciliation between executed campaigns;
- maximum 24 executed campaigns;
- maximum 8 hours runtime;
- hard stop at cumulative session settled P&L <= -$3.00;
- any child exception, ambiguous local status, client-ID/outcome mismatch, or settlement timeout hard-stops;
- no auto-scaling.

## Next gate

1. Run exact-client settlement reconciliation for the executed 20:40 market.
2. Confirm exact filled outcome, resolution, cost basis, payout, and Gemini `netProfit`; reservation must clear.
3. Run the disarmed overnight preflight from a clean state.
4. Obtain separate explicit user approval for the bounded overnight session.
5. Only then launch the detached overnight supervisor.
