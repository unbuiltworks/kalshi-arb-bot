# Gemini BTC5m Inventory-Maker Depth Preflight Incident — 2026-08-27

## Classification

No-trade infrastructure/preflight failure. No reservation, order entry, venue mutation, position, or realized P&L resulted from this attempt.

## Attempt

The user explicitly approved exactly one live q=1 Gemini BTC5m inventory-maker campaign using feature checkpoint `93217457dae996636b1cb4a3c40037ffeba53e69` and AWS launcher `deploy/aws/run_gemini_btc5m_inventory_maker_q1_live_once.sh`.

The exact-image safety suite passed: 13 tests in 0.28s. The launcher then failed during its authenticated DISARMED market preflight before the clean-account gate, reservation, arm-ID generation, or live container start.

Failure:

`RuntimeError: no fresh Gemini BTC5m depth`

Call path:
- `gemini_short_crypto_btc5m_inventory_maker_canary_q1.py::preflight`
- `gemini_short_crypto_btc5m_maker_canary_q1.py::fresh_top`

The underlying helper used one websocket subscription with a roughly 4-second bounded wait. Earlier preflights had succeeded on the same feed, so this is treated as a transient depth/subscription miss rather than trading evidence.

## Safety conclusion

Because the exception occurred inside preflight before `budget.reserve(...)`, no local reservation was created. The maker order-entry session was never connected and no order placement code ran. This attempt must be counted as $0 realized P&L and no trade.

## Fix

On `feature/gemini-short-crypto-live-q1`:
- `src/gemini_short_crypto_btc5m_inventory_maker_canary_q1_v2.py` adds up to 3 independent fresh-depth websocket attempts.
- Each attempt remains bounded and uses the existing fresh-only depth path.
- There is no stale-price or REST fallback.
- If all 3 attempts fail, the strategy still fails closed before mutation.
- Trading, arm, q=1 sizing, inventory, reservation, cancellation, and settlement semantics are unchanged.
- Regression tests cover retry-then-success and fail-closed-after-three-attempts.

Fixed feature checkpoint: `f1c70209a57764695b45fec586859a05d7640770`.

AWS v2 runners:
- DISARMED preflight: `deploy/aws/run_gemini_btc5m_inventory_maker_q1_v2_preflight.sh`
- one-shot live (NOT approved yet): `deploy/aws/run_gemini_btc5m_inventory_maker_q1_v2_live_once.sh`

## Next gate

Run the v2 DISARMED preflight. If it passes exact-image tests, authenticated fresh-depth preflight, and clean-account/local-ledger audit, obtain fresh explicit approval before running the v2 live launcher. The prior live approval applied to the old checkpoint and does not carry over to the changed code.
