# One entry-selection correction: open in the first 180s, not the first 600s

**All shadow and replay figures below are MODEL-DEPENDENT and labelled as such.** Nothing here
is a live-loss claim or a profitability claim in either direction. The only venue-confirmed
number in this document is the 1945z settlement of **−$0.6864**.

**Nothing changed in production.** `armed=false`, `shadow_lock=true`, `campaign_id=null`,
cap 0.96, every risk limit at its deployed value. No capital increase, no widened limit, no
service, no strategy change.

## 1. Why the previous positive replay result is not evidence — accepted

The two-layer replay returned +$12.65 across 517 windows, with every seed positive. It is
**not** evidence our engine can select entries, because it only ever acted at **L5's own
timestamps and prices** and so inherited his selection for free. The tell was in the same run:
the strand rate matched live (27.5% vs 28–33%) while the P&L was **opposite in sign**
(+$0.0245/window vs live −$0.1553). Same inventory mechanics, opposite outcome ⇒ the
difference is entry selection. That figure is withdrawn as evidence and kept only as the
diagnostic that localized the problem.

## 2. L5's actual entries, measured chronologically from his own fills

527 openings (fill #1 of each window), via the existing pipeline, no future information:

| | p10 | p25 | **p50** | p75 | p90 |
|---|---|---|---|---|---|
| seconds remaining at the opening | 741 | 853 | **885** | 893 | 894 |
| ⇒ seconds INTO the 900s window | 159 | 47 | **15** | 7 | 6 |
| opening own price | 0.4000 | 0.4600 | **0.5000** | 0.5600 | 0.6300 |

- **He opens at a median 15 seconds into the window.** 79.7% of openings fall in the first
  60s, 87.7% in the first 120s.
- **He opens near the money: median 0.5000**, p10 0.4000. He does not open on cheap longshots.
- Sides are balanced: 272 Up / 255 Down.

## 3. The observable difference, and it is the one L5's own data says matters most

`analysis/l5/l5_residual_state_replay.json`, **2,216 L5 windows** — completion rate by how
much of the window remained when he took his first leg:

| window remaining at first leg | n | L5 completion rate |
|---|---|---|
| 0.0–0.2 | 71 | **38.03%** |
| 0.2–0.4 | 190 | 42.11% |
| 0.4–0.6 | 310 | 59.03% |
| 0.6–0.8 | 441 | 73.02% |
| **0.8–1.0** | **1,204** | **82.89%** |

**Monotone across all five buckets.** `complete_fill_predictors.json` names this his strongest
predictor — *"TIME TO WORK IT"* — and records our own position on that axis as
**"UNMEASURABLE. All 871 acted decisions sit in one TTE bucket."**

**Our deployed gate permits a first leg while `tte >= 300` of 900s — i.e. down to 0.333 window
remaining, inside the 0.2–0.4 bucket where his completion rate is 42.11%.** We are authorised
to open in his worst regime, and because all our entries land in a single bucket we cannot
even see the difference from our own data.

Independently corroborating on a different quantity — **our own** fill rate by price band
(`complete_fill_predictors.json`, n=592 of our placements):

| band | p10-25c | p25-45c | **p45-55c** | p55-75c | p75-90c |
|---|---|---|---|---|---|
| our fill rate | 12.50% | 15.91% | **31.68%** | 21.21% | 7.14% |

Our fill rate peaks exactly where L5 opens (0.45–0.55), and is less than half that in the
cheap bands our `ts_min_first_leg_own_price = 0.20` permits. Two different measurements, two
different quantities, same shape.

## 4. Attribution of the realized losses — which differences plausibly explain them

| difference | observable before submit? | plausibly explains losses? |
|---|---|---|
| **opening with little window left** | **YES — the clock** | **YES.** Completion rate is what decides strands, and a strand settles 0 or 1. Every measured strand had 0/6 win rate; 1945z lost the full premium, −$0.6864 venue-confirmed. |
| **opening cheap (0.20–0.40)** | **YES — the price** | **YES.** Every replay strand opened at own price 0.30–0.40, below L5's p10 of 0.40, and our own fill rate there is 12–16% against 31.68% near the money. |
| book depth at entry | NO for L5's windows | cannot be tested — no PMUS book for 2026-03→08 |
| momentum / order flow | NO | PMUS tape carries **no print rows**; order flow is not derivable at all |
| fill selection (queue) | NO | same blocker |

So of the six dimensions the owner listed, **two are observable pre-submission and both point
the same way**; the other four are blocked by the absence of PMUS prints, and I am not going
to model them and present the model as a finding.

## 5. THE CORRECTION — one parameter, narrowing not widening

> **Raise `ts_min_entry_runway_s` from 300 to 720** — equivalently cut the effective first-leg
> entry window from the first 600s to the **first 180s** of a 900s window.

| | now | proposed |
|---|---|---|
| first leg permitted while | `tte >= 300` (0.333 remaining) | `tte >= 720` (0.80 remaining) |
| L5's completion rate in the permitted region | **42.11%–82.89%** | **82.89%** only |
| share of L5's own openings inside it | 98.5% | ~88% |

It is the right shape for this program: it **narrows** entry, reduces fill count, requires no
new data, no capital, no new limit, and no strategy. It moves our only measurable entry
variable from the regime where L5 completes 42% of the time into the one where he completes
83%.

### Economic consequence, labelled model-dependent

Strand frequency is what the completion rate buys. Our live strand rate is 28–33%; L5's
0.8–1.0 bucket implies ~17%. At the venue-confirmed strand cost of **$0.6864** (1945z, the
full premium), a 13-point reduction is worth **≈ +$0.09/window** — **about 6x the $0.0156 a
completed pair earns at our clip.** That ordering is the point: strand avoidance dominates
pair economics, so entry timing matters more than the completion ceiling we spent the day on.

### The caveats, from the source file itself, not softened

- *"These are conditional rates with two confounders held fixed, **not an experiment**. They
  say where completion happens, not what would happen if we changed our behaviour."*
- *"every magnitude here is measured on crypto"* — our lane is crypto BTC15m, so that
  particular transfer limit is satisfied, but the magnitudes remain L5's, not ours.
- the direction survives in 4 of 5 strata (0.8), not 5 of 5.
- the +$0.09/window is **arithmetic on a model-dependent strand rate**, not a measured return.

## 6. Bounded live-validation plan — fresh authorization required

Identical to `L5_LIVE_CAMPAIGN_PLAN.md` in every risk parameter, with **one** change: the
entry runway. Every figure is an existing deployed value except that one, which **narrows**.

| | |
|---|---|
| series / windows | `cpc-btc-updown-15m-`, **8 consecutive** |
| **the only change under test** | `ts_min_entry_runway_s` **300 → 720** |
| pair ceiling | **0.96 — unchanged** |
| exposure | **$5.00** aggregate, `NO_RECYCLING`; $2.00 per-market worst case |
| additional loss | **$5.00** campaign-scoped |
| **effective daily loss stop** | **$5.00** — the account-wide halt binds first, not the approved $20 |
| cumulative stop | $20, verified headroom **$14.7543** |
| order budget | 56 submissions: open 16 / complete 24 / replace 32 |
| clip | $0.80 notional, 2-contract ceiling, min price $0.20 unchanged |
| concurrency | 1 market, 1 live order, maker-only, defensive completion off |
| cancellation | TTL 120s, cancel margin 300s, halt latch refuses when absent |
| expiry | issued + 2h30m, hard; fail-closed, no auto-relaunch |
| reconciliation | PROVISIONAL → CONFIRMED only on `VENUE_SETTLEMENT_PX` |

**What it can answer:** whether entries confined to the first 180s fill at all, and whether
the strand rate moves toward 17% from 28–33%. **Strand rate is a proportion**, so unlike a
cent of pair economics it is observable at this sample size.

**What it cannot answer:** whether the strategy is profitable. ~4 fills cannot resolve
expectancy against ±$0.87 residual swings, and I will not report its outcome as a verdict in
either direction.

**Not armed, not requested.** Arming needs a fresh approval document: `approved`, `nonce`,
`series`, `issued_utc`, `expires_utc`, `max_windows`, `max_total_exposure_usd`,
`max_daily_loss_usd`. The code intersects and never widens; the nonce is single-use.
