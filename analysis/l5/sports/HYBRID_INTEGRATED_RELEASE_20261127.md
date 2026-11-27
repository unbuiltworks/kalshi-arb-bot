# Integrated sports release candidate: hybrid allocator + reward qualification + feasible ladder

27 Nov 2026. Branch `dev/pmus-hybrid-integrated`, built on the deployed
`a51cfdbd2`. **Disarmed: every new behaviour is default off. Nothing is
deployed, and no limit is changed.**

## Contents (one controller, no second engine)

| Component | Source | Flag (default) |
|---|---|---|
| Account/day reward qualification (selection, touch EV, marginal test, standing start) | `dev/pmus-reward-qualification-scope` | `PMUS_RN1_REWARD_QUAL_SCOPE` (`market`) |
| Feasible reward ladder (standing start) | ChatGPT `3981feabf`, integrated | same flag |
| Feasible ladder on the regular path; safe refused results (fixes a `KeyError` in the campaign caps' priority) | this work | same flag |
| Directional allocator: additions priced by marginal EV, favoured side, risk charge, freshness | `dev/pmus-directional-allocation` | `PMUS_RN1_DIRECTIONAL` (0) |
| Sportsbook probability source | same | `PMUS_RN1_DIR_SHARP` (0) |
| EV-priced additions in place of the "don't chase" cap | same | `PMUS_RN1_DIR_PRICE_ADDS` (0) |
| Favoured-side extra levels; upsizing capped at the per-order maximum | same | `PMUS_RN1_DIR_EXTRA_LEVELS` (0), `PMUS_RN1_DIR_UP_MULT` (1) |
| Adaptive pause depth (rest below the move during jump/vol pauses) | this work | `PMUS_RN1_PAUSE_DEPTH` (0) |
| Faster rotation of paused flat markets | this work | `PMUS_RN1_PAUSE_ROTATE_S` (0) |

## Evidence (THEORETICAL backtest on recorded books; production pauses emulated)

Data: 65 recorded CFB/NBA/NHL markets with the international book
(21 Nov). The harness runs production `quotes`, the guard, the ladder and
pauses, with modelled fills and 3 s latency. Rewards are not modelled.
Source: `directional/BACKTEST_PAUSE_DEPTH_20261127.txt`.

| Policy | Touch fills: trading P&L | Through fills: trading P&L | Deployed capital (time-weighted, all 65 markets) |
|---|---|---|---|
| No pauses | −$12.67 | −$14.64 | $201 / $184 |
| **Production pauses (as deployed)** | **−$5.87** | **−$7.74** | $185 / $159 |
| + pause depth | −$12.18 (paired −$0.29 ± 0.13/market) | −$4.95 (+$0.19 ± 0.33) | $210 / $187 |
| + allocator, additions only | −$8.31 (−$0.13 ± 0.13) | **−$2.12** (+$0.40 ± 0.32) | $101 / $87 |
| + both | −$10.54 | −$4.35 | $128 / $114 |

- **Pauses:** they protect. Removing them roughly doubles the loss.
- **Adaptive pause depth:** it adds capital and is worse under optimistic
  fills (about 2.2 standard errors). **Not recommended; it stays off.**
- **Allocator (additions only):** best under strict fills and slightly worse
  under optimistic fills, so its sign depends on the queue model. Its earlier
  no-pause result was +$0.63 ± 0.29 per market under strict fills.
- **Capital utilization:** on these books, more deployed capital lost more.
  The live gap (mean $1.30 deployed, 6.7% of the window resting, 98.5% of
  the time below the reward minimum) is mainly the reward-qualification
  scope. `account_day` addresses that scope; its payout effect cannot be
  backtested here because rewards are not recorded.

## Recommended activation set for a bounded window (needs owner authorization)

- `PMUS_RN1_REWARD_QUAL_SCOPE=account_day`: consistency fix. The $1
  minimum is unchanged, and so is the gate.
- `PMUS_RN1_DIRECTIONAL=1` with `PMUS_RN1_DIR_JUDGE_OPENINGS=0`: acts only
  with a settled international or fresh sportsbook probability.
- Leave off: `PMUS_RN1_PAUSE_DEPTH`, `PMUS_RN1_DIR_PRICE_ADDS`,
  `PMUS_RN1_DIR_EXTRA_LEVELS`.

`PMUS_RN1_PAUSE_ROTATE_S` (for example 30) is optional. It only frees a
slot held by a paused market that holds nothing.

## Validation

- **Full regression, flags off:** 6,059 passed. Failure names are identical
  to `a51cfdbd2` (26 pre-existing).
- **New tests:**
  - qualification scope: 11;
  - feasible ladder: 4;
  - pause depth and rotation: 6;
  - directional allocator: module 18, engine 13, harness 7.
- **Enabled-mode safety and reward suites:** pass with
  `PMUS_RN1_REWARD_QUAL_SCOPE` both `market` and `account_day`.
- **Expected flag-on difference:** with `PMUS_RN1_DIRECTIONAL=1`,
  `test_campaign_cap_with_marginal_ev_drops_the_lower_value` changes
  outcome. Its stub international fair makes the unfavoured YES rest a tick
  deeper, so the spend cap then drops NO. That is intended allocation
  behaviour, not a defect, and parity is judged with the flags off.
- **Docker:** clean build of this branch.
  - 62/62 `src` modules import with every new flag on.
  - The engine boots with RN1 on and the lock at `1`, and ran 75 s with no
    loop error, latch or market error.
- **Shared allocator:** byte-identical to the crypto copy
  (`dev/l5-crypto-shared-allocator`, sha256 `5b8b7e0b…`).
