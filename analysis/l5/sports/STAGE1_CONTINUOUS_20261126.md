# Stage 1: two concurrent markets, six hours, automatic rotation (for authorization)

26 Nov 2026. Nothing deployed or armed. It uses the running sports
release; no new service or controller.

## Configuration (sports service `pmus-spring-l5-probe`)

| Setting | Now | Stage 1 |
|---|---|---|
| Release | `5cbca4d3c` | `5cbca4d3c`. Switch to `e72cf329e` only after its enabled-mode safety tests pass and the owner authorizes it |
| `PMUS_TWO_SIDED_MAX_MARKETS` | 1 | **2** |
| `PMUS_TWO_SIDED_MAX_TRACKED` | 6 | 6 (unchanged; the watch list feeding selection) |
| `PMUS_RN1_PIN` | empty | empty (rotation) |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `1` | `window:<start>/<start+6h>`, grace 7,200 s unchanged |
| `PMUS_RN1_EDGE_GATE` | 0 | 0 |
| `PMUS_RN1_MARGINAL_EV`, `PMUS_RN1_FIT_ALL_LEVELS` | 1, 1 | unchanged |
| Every other setting | | unchanged |

## Limits that bound the two markets together (unchanged)

| Limit | Value | Scope |
|---|---|---|
| Account drawdown (venue equity) | $20 | whole account |
| Sports lane cap | $20 | the bot |
| At-risk budget (fills plus resting bids, worst case) | $20 | all markets |
| Total unpaired | $10 | all markets |
| Unpaired per market | $5 | each |
| Per market | $12 | each |
| Spend ceiling | $60 | campaign. A settled market stops counting, so capital is recycled |
| Campaign loss stop (counting resting bids) | $10 | campaign |
| Bot daily halt | $10 | UTC day |
| Ledger daily halt / cumulative ceiling | $50 / $75 (room now $12.69) | account |
| Maker-only, lock, safety latch, watchdog | on | — |

Two markets therefore share the same $20, rather than adding to it.

## How rotation works (existing code)

1. Each pass ranks every reward-eligible market in the allowed sports by
   its net estimate (theoretical reward minus expected fill cost) and
   holds the top two that are positive. There is one market per game.
2. A market is released after 600 s without a positive estimate, at the
   late cut-off, or when it closes or settles.
3. Its freed slot and its capital (SETTLEMENT_RECORDED takes it out of the
   spend ceiling) go to the next positive market.
4. After the window: completions only for 2 hours, then locked. Nothing
   relaunches after a crash, halt or window end.

The allowed sports are atp, atpdb, setkamemd, setkameua, utr, wta and wtadb.
Esports and cricket are off.

## The eight capabilities: what exists, what is missing

| # | Capability | Status |
|---|---|---|
| 1 | Rotation after settlement | **Exists**: release/reselect tests; live on 25 Nov |
| 2 | Concurrent markets | **Exists** (`MAX_MARKETS`; Campaign B ran up to 8 on 25 Nov) |
| 3 | Capital allocation by marginal EV, reward, inventory, liquidity, duration | **Partly.** Net-rank picks the clip size (1 or 2) and the placement. Selection ranks by reward per committed dollar, net of fill cost. Under capacity limits, marginal EV removes the bid with the lowest EV first (release `5cbca4d3c`, needs a fair value; `e72cf329e` extends it). **Missing:** remaining event duration is not in the ranking (only the late cut-off) |
| 4 | Multi-level quoting and replenishment | **Exists**: 3 levels, 10 s replenish, fit-all-levels |
| 5 | Shared portfolio exposure | **Exists**: the limits table above |
| 6 | Recycling settled capital | **Exists**: a settled market leaves the spend ceiling; completed pairs free unpaired room |
| 7 | Rewards and realized net by market, sport and campaign | **Partly.** `PILOT_PNL` gives settled P&L by market. `REWARD_CHECK` gives venue reward rows by market (date, status, paid or pending). **Missing:** one report joining them by market, sport and campaign, labelled settled / paid / pending |
| 8 | Manual trading compatibility | **Partly.** The round-trip correction (ledger `c111abd67`) prices a manual trade closed before settlement correctly; the bot skips markets with a foreign position. **Missing:** the bot-versus-total split (`dev/l5-portfolio-split-auto-manual`, prepared, off); it needs a check on the real feed and the owner's approval |

## Before Stage 2

- 2 markets × 6 hours with net positive after confirmed rewards. Measure:
  - capital in use;
  - reward-qualifying time;
  - fill loss by sport against the settings.
- Enabled-mode safety tests complete; `e72cf329e` authorized.
- Items 3 (duration) and 7 (the report) built.
