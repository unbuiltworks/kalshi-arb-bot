# Existing controller: built capabilities against effective runtime settings

25 Nov 2026, 14:30 UTC. Source: the sports bot's boot line at 13:36 UTC
(deployment `64a59ac8`, build `bc3a5b6b9`), the settings set for the
campaign, and the first 13 minutes of the window. No code change is
proposed anywhere in this document.

| Capability (already built) | Runtime state | What restricts it | Setting that exercises it |
|---|---|---|---|
| Same-side accumulation while unpaired | ON, price-blind | size 1; never above the side's average cost + 2c | — |
| Inventory-aware margin test for each addition (needs more edge per clip already held) | **OFF** (`extend_ev` false) | switched off for the venue-native profile; needs a reference price | `PMUS_RN1_EXTEND_EV=1` |
| Reference-priced markets (ATP, WTA, three soccer leagues) | **unreachable**: not in the competition list | list is Setka and UTR only | add `atp,wta` to `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` |
| Dynamic order sizing (size by phase; in-play size halves while the price moves ≥ 2c in 30 s) | **inert**: both sizes are 1, so there is nothing to halve | `pregame_qty` 1, `live_qty` 1 | `PMUS_RN1_PREGAME_QTY=2`, `PMUS_RN1_LIVE_QTY=2` |
| Competitive quoting: join the best bid | ON but engaged on 1 of 68 opening bids | joins only at a spread of two ticks or more; 94 of 127 decisions were on one-tick spreads | none: at one tick the built-in near-cross rule cancels a joined bid on every book update |
| Depth filter | ON at 10 contracts | refused a side for 37% of market-time | `PMUS_TWO_SIDED_MIN_TOUCH_QTY=3` |
| Smart cancellation: pull on adverse move, jump pause, volatility pause, keep a bid that is merely deeper | ON | working as built (14% and 10% of market-time paused) | — |
| Rapid replenishment | ON, 10 s | — | — |
| Fresh entry after a completed pair without the old price ceiling | ON | — | — |
| Slots not held by passive inventory; re-selection of idle markets | ON | — | — |
| Reward-share ranking | ON | — | — |
| Ranking by net opportunity (edge + reward) | OFF (`rank_net` false) | needs the fair-value test on for every market, which would stop Setka and UTR | leave off |
| Fair-value test on every bid | OFF (`edge_gate` false) | all-or-nothing: on, a market with no reference gets no bid at all | leave off |
| Completion bid at a locking price | ON | — | — |
| Completions after the window | ON, two hours | — | — |
| Batched fill detection | ON, verified on the venue | — | — |
| Unpaired caps | $3 a market, $6 campaign | hard limits, unchanged | — |
| Account limit | $13, about $10 free | binds inventory dollar for dollar | — |

## Configuration that exercises what is built (limits unchanged)

| Setting | Now | Proposed |
|---|---|---|
| `PMUS_RN1_PREGAME_QTY` / `PMUS_RN1_LIVE_QTY` | 1 / 1 | 2 / 2 |
| `PMUS_TWO_SIDED_VOL_SHRINK_USD` | 0.02 | 0.02 (unchanged; this is what halves the in-play size) |
| `PMUS_RN1_EXTEND_EV` | 0 | 1 |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | setkameua,setkamemd,utr | setkameua,setkamemd,utr,atp,wta |
| `PMUS_TWO_SIDED_MIN_TOUCH_QTY` | 10 | 3 |
| `PMUS_TWO_SIDED_MAX_MARKETS` / `MAX_TRACKED` | 8 / 16 | 4 / 8 |

Four markets because two contracts on both sides rest about $1.94 a
market: four use $7.76 of roughly $10 free, leaving about $2.40 for
inventory. Six would not fit.

Consequences: a fill costs up to $1.40 (two contracts); the $3 per-market
cap allows about three unpaired two-contract fills; with the margin test
on, a side already unpaired is added to only where a reference price shows
the required margin, so Setka and UTR stop averaging down blind. Worst
case for the campaign is still the $4 loss stop.
