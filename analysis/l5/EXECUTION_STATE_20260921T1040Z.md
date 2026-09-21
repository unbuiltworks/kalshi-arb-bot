# Execution state, 2026-09-21T10:40Z

Labels are strict: historical replay → shadow → live probe → submitted → filled
→ settled → **realized**. Nothing below is called realized.

## 1. Collector hygiene patch — DEPLOYED AND WORKING

Commit `769f5bcd`, deployment `18a0cd2e-24d8-4c6a-8e32-8a5dddd959ef` (SUCCESS).
Production before (`6553d6dd`, 10,764s uptime) vs after (`18a0cd2e`, 326s):

| counter | before | after | reading |
|---|---|---|---|
| `book_errors` | 11,229 | **0** | genuine fetch-health failures |
| `book_gone` | (did not exist) | 120 | expected rollover 404/410 |
| `tokens_retired` | 3,718 | 40 | 36.1/min → ~7/min |
| `habitat_readd_suppressed` | (did not exist) | 6→46→86 | the tombstone working |

`intl_flow` disambiguation, live across three heartbeats:
`sweeps 10 → 12 → 16` while `selected 0 → 119 → 0`, `tape 1000`,
`fetch_errors 0`. A flat row counter is now provably a quiet slate rather than
a stalled loop.

All four streams advancing; sweeps 0.20–0.29s (pmus) and 0.33–0.39s (intl).

## 2. Prospective evidence — BELOW THE FLOOR, NOT SCORED

Durable `prospective.events` 1,232 at 10:31Z. Per-market scorable
**precondition** (venue-labelled MAKER, pre-fill book present, ≥1 share ahead),
reconstructed from 501 durable event log lines spanning 09:43–10:30Z:

| market | events | maker | +pre_book | +queue≥1sh |
|---|---|---|---|---|
| `btc-updown-15m-1789983900` | 152 | 151 | 151 | **103** |
| `btc-updown-15m-1789984800` | 149 | 147 | 147 | 96 |
| `btc-updown-15m-1789985700` | 79 | 79 | 79 | 36 |
| `btc-updown-15m-1789983000` | 52 | 50 | 50 | 29 |
| 11 others (5m windows) | 69 | 9 | 9 | 5 |

**Best single market 103 against the preregistered floor of 200. NOT SCORED.**
`pooled()` refuses, as preregistered.

### A structural problem with the floor, reported not resolved

A `btc-updown-15m` window lives 900s and yields ~100 scorable fills, then the
market ends. **No single market in this habitat can reach 200.** The floor is
not merely unmet, it may be unreachable here.

I am not moving the floor — changing a preregistered threshold after seeing the
data is exactly the manoeuvre the preregistration exists to prevent. This needs
an owner decision between: a longer-lived market type, a market-clustered
alternative stated in advance, or accepting that H-QUEUE-MISMEASURED cannot be
scored in the updown habitat.

The 2026-09-21 03:55:13Z–06:07:17Z PMUS interval remains **CENSORED**.

## 3. PMUS executable L5 states — 43 of 61

`analysis/pmus/executable_state_census.py`, 12 sweeps at 8s over the live slate,
running the existing decision layer unchanged (`analyze_market` → `_proposal` /
`plan_action` → `open_admission`). No gate relaxed, no fill invented.

```
ELIGIBLE                  43
model_no_open             17
one_sided_book (no bid)    1
```

Sample: `aec-cs2-sashi-fokus` 0.28/0.29, `aec-dota2-nem-mouz` 0.39/0.40,
`aec-setkamecz-kukzde-hrstom` 0.53/0.54 — all `q1_depth=True`.

**A FIRST RUN OF THIS CENSUS REPORTED 0 EXECUTABLE AND WAS MY BUG.** I read
touch depth from `bestBidQuantity`, which does not exist on the gateway payload;
the real fields are `bidShares` / `askShares` (31,000 shares at the touch in the
first market sampled). Every market therefore failed `no_q1_touch_depth`. The
same field-name class of error is called out in `pmus_status_report` itself.
Reported because a zero from a typo is indistinguishable from a zero from the
market.

## 4. Gemini q=1 lane — see GEMINI_Q1_STATE_20260921.md
