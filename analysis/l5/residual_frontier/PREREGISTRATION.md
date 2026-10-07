# PREREGISTRATION — the residual-efficiency frontier (Lane B)

**Protocol** `RESIDUAL-EFFICIENCY-FRONTIER-LANE-B`
**Frozen fingerprint** `4b16c92bc658583265d014c196a3becf42540cad20c2e98999f954bea16e074d`
**Spec module** `analysis/l5/residual_frontier/spec.py` (`assert_frozen()`, hard-coded literal)
**Parent protocol** `analysis/l5/norebate/PREREGISTRATION_NOREBATE.md @ 3fb8fccb`
**Population** `data/l5/raw/activity.jsonl` → the frozen Q1 corpus, 2,218 events, 22 observed days
**Written** before any counterfactual P&L was computed. The grid, the metric
definitions, the strata and the no-hindsight clause are fixed by the
fingerprint above and cannot be edited without breaking `assert_frozen()`.

> **LABEL.** Everything produced under this protocol is **historical replay**
> of another trader's public Polymarket activity, counterfactually modified.
> It is not shadow, not a live probe, not submitted, not filled, not settled
> and **never realized**. L5's historical cash is not our profit.
> Rebates and rewards are **exactly $0.00** throughout, as in 3fb8fccb.

---

## 1. The fact to explain

From the frozen no-rebate audit:

| component | FULL_CORPUS |
|---|---|
| matched-pair economics | **+$22,568.25** |
| directional residual | **−$25,313.78** |
| charged fees (embedded) | $3,993.43 |
| rebates / rewards | $0.00 |
| **covered net ex incentives** | **−$2,745.53** |

The matched engine earns; the leftover directional exposure returns all of it
and a little more. `CLAUDE.md` already states that residual directional
exposure has no demonstrated predictive skill. This protocol asks the
narrow, bounded question:

> Is there a **contemporaneous** bound on directional residual that removes a
> material share of the −$25,313.78 **without** destroying the +$22,568.25?

## 2. What is held fixed

- **The population.** The same 2,218-event corpus, same `load()`, same
  slug-keyed event definition, same era labels as 3fb8fccb.
- **L5's own fill sequence.** No cell invents a fill, reorders fills, changes
  a price or a size, or adds a side L5 did not take. A cell can only **refuse**
  a fill that L5 actually took. The counterfactual is strictly a suppression.
- **Fees.** The charged wedge `max(0, usdcSize − price·size)`, never the
  `0.07·p·(1−p)` formula, and only on fills the cell took.
- **Rebates/rewards.** $0.00. No cell may be rescued by an incentive.

## 3. CONTEMPORANEOUS STATE ONLY — the constraint and how it is enforced

A rule receives a frozen `DecisionState` whose **complete** field set is:

`shares_up`, `shares_down`, `cand_is_up`, `cand_size`, `cand_price`,
`tte_s`, `duration_s`, `taken_count`

`shares_up` / `shares_down` are built **only from fills this cell already
took**, so a suppressing cell sees its own book, not L5's. `tte_s` comes from
the event slug: the trailing integer is the window **OPEN** epoch and
`TTE = epoch + duration − t`, verified in `analysis/l5/EXTEND_AND_SWITCH.md`
against `markets.jsonl` `endDate` on 3,528 conditions with **0** mismatches.

Structurally absent from that object, and named in
`spec.FORBIDDEN_STATE_FIELDS`: the resolved winner, redemption cash, the
event's net, the event's matched shares, the event's peak residual, the
event's total fill count, and every later fill.

`tests/test_residual_frontier.py` asserts:
1. `set(DecisionState fields) == set(ALLOWED_STATE_FIELDS)` — no extra field;
2. no `FORBIDDEN_STATE_FIELDS` name is reachable on the state object;
3. every rule is a pure function of a `DecisionState` and the cell's frozen
   parameters, proven by evaluating each rule against a synthetic state and
   showing the decision is unchanged when the event's settlement outcome is
   flipped;
4. the preregistered fingerprint matches and is **non-vacuous** (mutating any
   grid or metric value changes it);
5. the metric key set emitted for every cell is **exactly** `spec.METRICS`,
   with no cell-specific exception.

Settlement **is** used — but only in **scoring**, never in a decision. The
winner is the `outcomeIndex` of the event's `REDEEM` record. That resolver was
validated before the grid was frozen: median |redeem cash − modelled payout|
= **$0.0000**, the redeem-index hypothesis is closer than its complement on
**98.2%** of 2,000 resolved events and exact (<$0.001) on **89.2%**.

## 4. NO POST-HOC THRESHOLD SEARCH

Every threshold was placed on a measured percentile of L5's **own state
distribution**, computed before any counterfactual P&L existed. Those
distributions, over the 2,216 traded events of the frozen corpus:

| state variable | p10 | p25 | p50 | p75 | p90 | p99 |
|---|---|---|---|---|---|---|
| peak \|residual\| (shares) | 19.5 | 46.5 | 91.5 | 173.5 | 334.1 | 755.3 |
| terminal \|residual\| (shares) | 8.4 | 23.5 | 56.1 | 130.8 | 251.9 | 721.7 |
| gross inventory (shares) | 23.5 | 66.4 | 196.8 | 406.0 | 2827.8 | 6719.8 |
| terminal residual fraction | 0.04 | 0.12 | 0.38 | 1.00 | 1.00 | 1.00 |
| fill time-to-expiry (s) | 84 | 180 | 380 | 614 | 785 | 888 |

This scale calibration is disclosed because it is the only thing about the
grid that looked at data first, and it looked at **state**, not at returns.
Nothing below is re-tuned after a result is seen.

## 5. THE COMPLETE GRID — 30 cells

Suppression semantics (`spec.SEMANTICS`):

- **`EXTEND_ONLY`** — the preregistered primary. Once the cap binds,
  **additional same-side exposure is refused (EXTEND blocked) while a
  COMPLETE is still allowed.** A COMPLETE is a fill on the side currently
  holding strictly fewer shares, i.e. one that **reduces** |residual|. This
  mirrors the live shell, where `residual_cap_units` gates EXTEND and not
  COMPLETE (`FRONTIER_SUPERSEDED_20260914.md`, Result 4).
- **`ALL_FILLS`** — contrast arm only. Refuses any breaching fill, COMPLETE
  included. Carried so the value of the EXTEND_ONLY semantic is measurable
  rather than assumed.

| # | cell_id | family | semantics | abs cap (sh) | frac cap | TTE tighten |
|---|---|---|---|---|---|---|
| 1 | `BASELINE` | BASELINE | ALL_FILLS | — | — | — |
| 2 | `ABS25_EXTEND_ONLY` | ABS | EXTEND_ONLY | 25 | — | — |
| 3 | `ABS50_EXTEND_ONLY` | ABS | EXTEND_ONLY | 50 | — | — |
| 4 | `ABS100_EXTEND_ONLY` | ABS | EXTEND_ONLY | 100 | — | — |
| 5 | `ABS200_EXTEND_ONLY` | ABS | EXTEND_ONLY | 200 | — | — |
| 6 | `ABS400_EXTEND_ONLY` | ABS | EXTEND_ONLY | 400 | — | — |
| 7 | `ABS800_EXTEND_ONLY` | ABS | EXTEND_ONLY | 800 | — | — |
| 8–13 | `ABS{25,50,100,200,400,800}_ALL_FILLS` | ABS | ALL_FILLS | as named | — | — |
| 14 | `FRAC0.1_EXTEND_ONLY` | FRAC | EXTEND_ONLY | — | 0.10 | — |
| 15 | `FRAC0.2_EXTEND_ONLY` | FRAC | EXTEND_ONLY | — | 0.20 | — |
| 16 | `FRAC0.35_EXTEND_ONLY` | FRAC | EXTEND_ONLY | — | 0.35 | — |
| 17 | `FRAC0.5_EXTEND_ONLY` | FRAC | EXTEND_ONLY | — | 0.50 | — |
| 18 | `FRAC0.75_EXTEND_ONLY` | FRAC | EXTEND_ONLY | — | 0.75 | — |
| 19 | `TTE120x0.5_EXTEND_ONLY` | TTE | EXTEND_ONLY | 200 | — | <120 s → ×0.50 |
| 20 | `TTE120x0.25_EXTEND_ONLY` | TTE | EXTEND_ONLY | 200 | — | <120 s → ×0.25 |
| 21 | `TTE60x0.5_EXTEND_ONLY` | TTE | EXTEND_ONLY | 200 | — | <60 s → ×0.50 |
| 22 | `TTE60x0.25_EXTEND_ONLY` | TTE | EXTEND_ONLY | 200 | — | <60 s → ×0.25 |
| 23–28 | `ABS{50,100,200}+FRAC{0.2,0.35}_EXTEND_ONLY` | COMBO | EXTEND_ONLY | as named | as named | — |
| 29 | `ABS100+FRAC0.35+TTE60x0.5_EXTEND_ONLY` | COMBO | EXTEND_ONLY | 100 | 0.35 | <60 s → ×0.50 |
| 30 | `ABS200+FRAC0.35+TTE60x0.5_EXTEND_ONLY` | COMBO | EXTEND_ONLY | 200 | 0.35 | <60 s → ×0.50 |

### Why each family is an OBSERVED L5 state variable

| family | reads | evidenced in this repository |
|---|---|---|
| ABS | `peak_resid` | `analysis/l5/CANON_REGIMES.md` §2 daily observable list, "`peak_resid` — peak \|up-down\| share residual"; `analysis/l5/norebate/REGIME.json` `per_day[].residual_shares` |
| FRAC | `term_resid_frac` | `analysis/l5/CANON_REGIMES.md` §2, "`term_resid_frac` — terminal residual / gross shares" |
| TTE | `first_tte_s` | `analysis/l5/CANON_REGIMES.md` §2, "`first_tte_s` — seconds from first acquisition to window CLOSE"; `analysis/l5/EXTEND_AND_SWITCH.md` Q4: "inside the last 30 seconds P(STOP) jumps by an order of magnitude" — L5's own clock already governs STOP, so a clock-tightened cap sharpens observed behaviour rather than inventing it |
| EXTEND/COMPLETE split | `extend_per_event`, `complement_per_event`, `completion_rate` | `analysis/l5/CANON_REGIMES.md` §2; `analysis/l5/EXTEND_AND_SWITCH.md` (EXTEND 60.2% / SWITCH 36.8% / STOP 3.0% over 204,599 decision points) |

No family reads a variable the repository does not already measure on L5.

## 6. METRIC DEFINITIONS — applied identically to every cell

Frozen in `spec.METRICS`; reproduced verbatim here.

| metric | definition |
|---|---|
`matched_retained_usd` | Σ over events of `matched_shares·(1 − pair_cost)`, where `matched_shares = min(up, down)` of the fills **this cell took** and `pair_cost = VWAP_up + VWAP_down` of those same fills, ex fee. Reconciles to the audit's matched component on `BASELINE`.
`residual_pnl_usd` | Σ over events of (settlement payout on the retained residual) − (VWAP cost of the retained residual); payout = \|residual\| if the residual side is the resolved winner else 0. Scoring only.
`residual_loss_avoided_usd` | `residual_pnl_usd(cell) − residual_pnl_usd(BASELINE)`.
`fees_usd` | Σ `max(0, usdcSize − price·size)` over the fills this cell took.
`net_cash_ex_incentives_usd` | `matched_retained_usd + residual_pnl_usd − fees_usd`. Rebates/rewards $0.
`turnover_retained_usd` | Σ `usdcSize` over the fills this cell took.
`turnover_retained_frac` | ÷ the same quantity on `BASELINE`.
`events_touched` | events in which the cell took ≥1 fill.
`events_per_observed_day` | `events_touched / 22` **observed** days. The May–July coverage hole is a hole, not a zero; nothing divides by calendar span.
`worst_drawdown_usd` | most negative (cumulative net − running peak) over events ordered by first-fill timestamp.
`suppressed_action_frac` | `1 − fills_taken / fills_available`.
`suppressed_extend_frac` / `suppressed_complete_frac` | the same, restricted to the available EXTEND / COMPLETE fills.
`net_bp_of_turnover` | `10000 · net / turnover_retained_usd`, so a cell cannot look good merely by trading less.

## 7. STRATA — no new regimes are invented

Reported for every cell: `FULL_CORPUS`; the three eras of 3fb8fccb
(`E1_GENUINELY_PRE_REBATE`, `E2_EARLY_REBATE`, `E3_INCENTIVE_ERA`); the
primary regime partition already detected in `analysis/l5/CANON_REGIMES.md`
(change points `['2026-04-25','2026-06-23','2026-08-19']` — within this
corpus only `2026-04-25` and `2026-08-19` fall inside coverage, so the
partition degenerates to two occupied segments and this is reported as a
degeneracy, not patched); and the settlement strata
`SETTLEMENT_RESOLVED` (2,000 events) / `SETTLEMENT_UNRESOLVED` (218).

**The unresolved stratum is never silently pooled.** 212 of the 218 are
one-sided, where a $0 settlement is the *correct* outcome of a lost one-sided
bet. The other 6 carry **1,829.86 matched shares** whose complete-set
redemption of **$1,829.86** is arithmetically guaranteed and simply absent
from the feed. Pooling that as a loss would misattribute a coverage hole to
the strategy.

## 8. DECLARED IN ADVANCE — what would count as a win, and what would not

- A cell "turns covered ex-incentive cash positive" only if
  `net_cash_ex_incentives_usd > 0` on `FULL_CORPUS` **and** the sign is not
  produced by a single stratum.
- A cell that reaches positive by **suppressing almost everything is not a
  win.** Declared thresholds, fixed now: a positive cell retaining
  < **50%** of baseline turnover, or suppressing > **50%** of L5's actions, or
  retaining < **50%** of `matched_retained_usd`, is reported as
  **GUTTED_ENGINE** and explicitly refused as a candidate.
- Positive aggregate replay cash with materially different behaviour is **not**
  evidence of replication (`CLAUDE.md`, validation standard).

## 9. OUT OF SAMPLE — held, not run

No OOS test is run under this protocol. The split is named in `FRONTIER.md`
and left **unrun**. Nothing here is a go decision; nothing here changes a
live spec, a frozen fingerprint, `deploy/**` or `scripts/l5_exec*`.

## 10. What this protocol cannot answer

- **Maker/taker role is UNAVAILABLE** (`/activity` carries no liquidity flag),
  so no cell can claim a rebate, a queue position, or that a refused fill
  would have been a maker fill.
- **Suppression is not counterfactual execution.** Refusing a fill L5 took
  removes that fill's cash; it does not model what the book would have done,
  what the counterparty would have done, or what L5 would have done next. A
  cap is studied as an *accounting* bound on an observed sequence, not as a
  re-run of the market.
- **The May–July coverage hole.** 22 observed days across two disjoint
  clusters (2026-03-19→04-21 and 08-28→08-31). `E2` is **5 events on one
  day**. No per-day figure generalizes.
