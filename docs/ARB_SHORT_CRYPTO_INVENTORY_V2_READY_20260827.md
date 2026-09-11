# Gemini BTC5m Inventory-Maker v2 — Live Gate Ready

Date: 2026-08-27

## Classification

DISARMED preflight passed. No order entry, venue mutation, position, reservation, or realized P&L occurred in this preflight.

## Fixed feature checkpoint

`f1c70209a57764695b45fec586859a05d7640770`

The v2 change is limited to fresh market-data acquisition for the inventory-maker q=1 canary:
- up to 3 independent fresh Gemini depth websocket attempts;
- no stale-price fallback;
- no REST-price fallback;
- fail closed before mutation if all attempts fail;
- trading, q=1 sizing, arm, inventory, reservation, cancellation, and settlement semantics otherwise unchanged.

## Successful v2 DISARMED AWS preflight

- Safety tests: 15 passed in 0.31s.
- Market: `GEMI-BTC05M2608272015-UP`.
- Seconds to expiry: 182.484.
- q=1 supported: true.
- YES maker quote: $0.36.
- NO maker quote: $0.52.
- Pair cost before modeled fees: $0.88.
- Conservative modeled maker fees: $0.01 YES + $0.01 NO.
- Modeled matched-pair edge after fees: +$0.10.
- Entry floor: +$0.01.
- Gate passed: true.
- Account audit: active short-crypto orders 0; active short-crypto positions 0; local open reservation false.
- Order entry: false.
- Mutation: false.
- Realized P&L: $0.

## Matching live launcher

`deploy/aws/run_gemini_btc5m_inventory_maker_q1_v2_live_once.sh`

The launcher is pinned to the same fixed feature checkpoint `f1c70209a57764695b45fec586859a05d7640770` and:
- reruns the exact 15-test safety suite;
- runs a fresh authenticated DISARMED v2 preflight;
- fails closed on any existing Gemini short-crypto exposure or AWS-local reservation;
- proves the actual YES/NO client IDs are distinct;
- uses the inventory-specific dual-key arm namespace;
- runs exactly one BTC 5m campaign with max q=1 per outcome;
- uses maker-only BUY LIMIT MOC entries;
- allows bounded temporary one-sided inventory;
- does not perform a normal SELL exit;
- cancels remaining resting orders before expiry;
- leaves any executed inventory non-realized until authoritative Gemini settlement/reconciliation;
- has no restart/loop.

## Live approval gate

The previous approval applied to the pre-v2 checkpoint and does not carry over. Fresh explicit approval is required for exactly one live q=1 inventory-maker v2 campaign.

Do not scale or auto-repeat. After any executed or ambiguous result, reconcile authoritative Gemini orders/positions/settlement and AWS-local reservation before another live run.
