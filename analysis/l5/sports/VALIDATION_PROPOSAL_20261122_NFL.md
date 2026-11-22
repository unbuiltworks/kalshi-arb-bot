# Bounded live validation — NFL, profit-first controller (proposal)

22 Nov 2026. A proposal only. Nothing here is deployed, armed or
authorized. Every numbered step in section 6 needs the owner's explicit
approval of that step.

## 1. Purpose

Measure, with venue-confirmed fills, what the profit-first controller's
fills are worth after they happen. Not to make money at this size: at two
contracts an order the result is cents either way.

## 2. Build

Sports release candidate `158685d3ab0b446ac31387bf3ba32bb7bc5659fd`
(`release/pmus-sports-rn1-158685d3a`; it supersedes `6aa8bc839`, cut an
hour earlier without the cent-based allowances and a memory bound). Over the running `7af13644a`:
reward-aware ranking, the marginal-edge test on every bid, post-fill
markout records, reward credit only above the venue's minimum payout,
loop-gap heartbeat, stall-drill switch, optional order expiry (off).

Regression: sports 2,032 passed / 6 failed; full 5,168 / 24; the same
failing tests as before the change.

## 3. Markets and time (two options; times UTC)

| | Window | Markets (moneyline) | Kick-off |
|---|---|---|---|
| A | 22 Nov 21:35–23:05 | `aec-nfl-nyj-lac-2026-11-22`, `aec-nfl-jax-nyg-2026-11-22`, `aec-nfl-lv-den-2026-11-22` | 21:05, 21:25, 21:25 |
| B | 23 Nov 01:30–03:00 | `aec-nfl-min-sf-2026-11-22` | 01:20 |

Selection constraints inside either window, all existing: allowlist set to
`nfl` only; price 25c to 75c; spread at most 6c; at least 10 contracts at
the touch; an international price match required; late-game rule; one
market per game; at most two markets at once.

A market outside the price band at the time is simply not traded. Two of
seven early games today were inside it at 18:34Z.

## 4. Limits (all existing, unchanged)

| Limit | Value |
|---|---|
| Campaign spend | $16 |
| Campaign unmatched exposure | $6 |
| Per-market spend | $8 |
| Per-market unmatched | $3 |
| Campaign loss stop | $4 |
| Daily halt | $5 |
| Order size | 2 contracts |
| Concurrent markets | 2 |
| Orders | maker only; taker disabled |
| Pair cap | $0.97 |
| Window | relocks itself at the end; resting orders cancelled; no grace |

## 5. What will be measured (venue-confirmed)

- Orders, fills, fees (expected zero), cash before and after.
- Each fill's markout at 5 s, 30 s, 2 min and 5 min from the bot's own
  record, checked against an independent quote recording run from here.
- Settled trading result per market; pairs and unpaired legs separately.
- Reconciliation clean throughout; no safety latch; watchdog healthy.
- The account ledger's day figure against cash, with the sports feed on.

Success: zero safety or reconciliation faults; accounting on both services
equals cash; markout of opening fills at 30 s not below zero by more than
the 1c allowance. Fewer than about 10 fills proves nothing about edge
either way; that outcome is likely here, because NFL ticks in quarter
cents and the test only bids where PMUS is at least about 0.7c cheap
against the international price.

Failure: any taker fill, any fill outside the limits, a reconciliation
mismatch, a latch, a watchdog halt, or an unpaired leg above its cap.

## 6. Steps, each needing its own approval

1. Locked deploy of sports `158685d3a…`; verify SHA, lock, zero orders,
   watchdog.
2. Account ledger: deploy `f5a5434d6c3530e501c76879345b5199813efa9b`
   (`release/l5-pmus-live-sports-ownership-20261122`) and set its two
   variables (feed address; start day chosen by the owner).
3. Watchdog drill, no orders: a 5-minute stall window on the locked bot;
   pass when the watchdog's note for the sports lane changes to "not a
   managed lane" within about 140 s and returns afterwards.
4. The live window: lock variable set to the window; allowlist set to
   `nfl`. Final pre-arm check first; relock without waiting if it fails.

## 7. One existing limit that would end it early

The account ledger also enforces an equity drawdown limit: at 18:42Z it
read 2.2144 used of 3.00, room $0.79. A sports loss of more than $0.79
would reach it before the sports bot's own $4 campaign stop. It is an
account protection and is not proposed to change; the owner should know
it is the binding limit.

Not in this validation: order expiry (unproven at the venue; proposed for
the one-order watchdog test), the one-order stall test, any size increase.
