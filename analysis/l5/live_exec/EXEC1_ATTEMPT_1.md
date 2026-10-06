# LIVE-L5-EXEC-1 — ATTEMPT 1: INFRASTRUCTURE SKIP / NO SUBMISSION

Classification: **SKIP (pre-submission gate failure). NOT a trading result.**
No order was submitted. No signing occurred. No lifecycle state was reached.
Capital at risk: **$0.0000**. matched-pair economics: **N/A**.

Owner authorization on record: fingerprint
`b1dbe7f1e628e6f9fc8e6459a2a5b95098ada2f16ae872f10081d5dc9bb82e06`,
envelope: PMUS btc-updown-15m, one market, one order total, 0.01 shares,
max possible loss $0.0099, maker/post-only, AuthBook only, 2.0s freshness,
no pair-cost reservation, no complement, no second/replacement/recovery order.

## Pre-submission reverification (run before any submission, as authorized)

| # | Gate | State | Evidence |
|---|------|-------|----------|
| 1 | Frozen fingerprint EXEC-1 | **PASS** | `fingerprint()` == `b1dbe7f1…2e06`; `assert_frozen()` returns |
| 2 | Other fingerprints unchanged | **PASS** | S3 `8b20a259…87df`, reserved micro `0522b0da…9118` both re-verify |
| 3 | Live venue tick / price grid | **PASS (read-only)** | every observed BTC15m spread exactly 0.01 in `PMUS_WS_STATUS`; tick read per-market from `orderPriceMinTickSize`, not assumed |
| 4 | Live `minimumTradeQty` | **PASS (read-only)** | 0.01, consistent with `VENUE_MIN_QTY` |
| 5 | Fresh authenticated BBO | **PASS (read-only)** | AuthBook `last_authenticated_update_age_s: 0.0`, `both_sides_served: true`, `last_frame_age_s 0.0`, `messages_received` strictly increasing; expired-window control at `…1400z` ages 526.4→554.8s, proving the age field is real |
| 6 | No other order-capable PMUS executor running | **PASS** | no `pmus`/executor process in this container; `micro_armed() == False`; `exec1_armed() == False` |
| 7 | Isolated `PMUS_EXEC1_*` arming keys present | **FAIL** | all four of `PMUS_EXEC1_LIVE`, `PMUS_EXEC1_SESSION_ID`, `PMUS_EXEC1_ARM`, `PMUS_EXEC1_GATE_EVIDENCE` unset; spec asserts `claude_may_not_set_these: True` |
| 8 | PMUS credentials reachable | **FAIL** | this container holds no `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` |
| 9 | Venue network reachable | **FAIL** | `gateway.polymarket.us` and `api.polymarket.us` both `connect_rejected` at the egress proxy (HTTP 000) |

Tests: 156 passed (`test_live_exec.py`, `test_live_micro.py`, `test_s3_instrument.py`).

## Why this is a SKIP and not a retry

Gates 7–9 are properties of *this ephemeral research container*, not of the
experiment. Per the authorization — "If any frozen gate fails, SKIP and
return. Do not alter a parameter to obtain a trade" — nothing was loosened,
no parameter changed, and no alternative transport was sought.

There is no `SUBMITTED → ACKNOWLEDGED → … → CASH_CONFIRMED` chain to report.
Writing one would be fabrication. Likewise there is no maker/taker role, no
commission, no rest time, no queue depletion and no markout: those are
measurements of an order that does not exist.

## What the owner must do for attempt 2 (not done here)

On the host that has working authenticated PMUS access
(`l5-live-probe-oneshot`):

1. Deploy the EXEC-1 code there — it is currently not present on that host.
2. Set exactly the four `PMUS_EXEC1_*` keys, with
   `PMUS_EXEC1_ARM == PMUS_EXEC1_SESSION_ID` and
   `PMUS_EXEC1_GATE_EVIDENCE` = the exact intended BTC15m market slug.
3. Set nothing else. The `PMUS_MICRO_*` and `PMUS_Q1_*` keys must stay unset,
   so a fill cannot be misattributed between executors.

Claude did not and will not set these keys.
