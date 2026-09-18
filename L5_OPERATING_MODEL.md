# L5 Operating Model — Durable Strategy Handoff

**As of:** 2026-09-03  
**Primary coordination:** GitHub issue #167  
**Trader:** Polymarket `l5zn1bwom8etsk`  
**Purpose:** durable source of truth for the current L5 behavioral thesis, clone rules, promotion gates, and anti-drift constraints. This file is not a realized-P&L ledger and does not override source-accounted execution/audit artifacts.

> If conversation context and this file disagree, re-establish current GitHub/AWS state first, then update this file from source-backed evidence. Do not silently revive superseded hypotheses.

## 0. Phase order and anti-drift rule (OWNER 5556232035, 2026-09-06)

**REPLICATE L5 → PROVE FIDELITY → RUN L5 → MEASURE REALIZED RESULTS → THEN OPTIMIZE PROFITABILITY.**

* Phase 1 is **L5 replication**, not profitability optimization. The reference action is the reconstructed L5 sequential policy (side, price, size, timing, aggressiveness, inventory trajectory at the exact market state). It is never silently replaced by a generic pair/complement bot, arbitrage optimizer, sports heuristic, fill-rate optimizer or P&L optimizer.
* Safety/execution constraints may override an L5-shadow action; the override and its reason are logged per decision (`divergence`, `refusal`), and the safe live action and the stochastic L5 action stay separately visible.
* Candidate ranking and execution are not changed to maximize completed-pair probability/profit if that reduces L5 fidelity (ranking = L5-state fit; `pair_score` is a logged diagnostic only until Phase 2).
* The complement wire-price fix (`be431842`) is execution correctness, not strategy.
* **L5 fidelity gate** = `analysis/l5/l5_fidelity_gate.json` (frozen) evaluated by `src/l5_fidelity_gate.py`: action distribution (relation, side), post-fill COMPLETE gap, price/aggressiveness, size, timing/cadence, inventory trajectory, maker/taker, off-distribution rate; evidence = held-out historical + realtime shadow + bounded live; declared owner overrides (taker disabled, size cap, residual cap) are reported, never excused.
* Label: **L5 replication in validation** until the gate is met, whatever the P&L; **L5 operational** once met; only then Phase 2 profitability adaptations, each measured against the L5 baseline.
* Every report starts with **L5 fidelity status → execution status → accounting status → only then P&L.**

## 1. Core objective

Reverse-engineer L5's sequential decision process and reproduce it only where executable conditions support positive realized expectancy.

Canonical pipeline:

**L5 public activity → normalized history → sequential replay → behavioral model → realtime shadow → action-by-action comparison → bounded live probe → settled/realized audit → controlled scaling**

The core question remains:

> At this exact market state, what causes L5 to choose the next side, price, size, and timing?

Do not reduce the strategy to “buy both sides below $1” or generic arbitrage. Arbitrage/pair economics are an output and safety constraint, not a complete behavioral model.

## 2. Current behavioral thesis

The best-supported current model is:

**L5 is a persistent incremental inventory trader / paid-liquidity-provider, not a directional forecaster.**

Common sequence:

**OPEN → EXTEND → EXTEND / WAIT → COMPLETE or SWITCH → continue working inventory**

Key observed behaviors:

- repeated same-side / nearby-price actions are real and predictive;
- `last_rel` is the strongest missing next-action feature found in held-out work;
- temporary inventory imbalance is tolerated rather than immediately neutralized;
- opposite-side completion is often delayed until pricing improves;
- residual outcome selection has no demonstrated directional skill (~50% winner rate);
- mature behavior is maker-heavy early and more aggressive late where maker/taker is actually measurable;
- incentive-era behavior must not be copied blindly onto an account that is not receiving equivalent incentives.

The live/sports implementation must therefore distinguish:

1. **behavioral policy** — what L5 would likely do next;
2. **venue/execution shell** — what is actually executable/safe here;
3. **economic gate** — whether the action remains positive expectancy without unsupported rebates/rewards.

## 2a. FIDELITY CONCLUSION — L5 is an inventory oscillator, not a flat-completion controller

**SCOPE: BTC15m ONLY.** This statement is NOT promoted to the general L5 model
until other cohorts support it. On BTC15m the evidence is strong.

Source: `analysis/l5/residual_exit/RESIDUAL_EXIT_FORENSICS_V1.md` ·
`analysis/l5/residual_exit/forensics.py` · 5,546 windows, 693,916 fills, **zero
sells**, 38,180 reconstructed residual episodes, chronological TRAIN/OOS.
Residual P&L reconciles exactly to the authoritative window-level figure
(−$144,064.99997 vs −$144,065 in `analysis/l5/btc15m/decompose.py`).

* **84.3%** of imbalance episodes end by OVERSHOOTING through zero (`FLIPPED`).
  **1.2%** end on it (`COMPLETED_FLAT`). **Exactly one of 5,546 windows ended
  flat.**
* Flat completion is also his MOST EXPENSIVE exit: median episode pair cost
  0.9950 against 0.980–0.986 for flips, median attainable pair at that instant
  **1.01**, and summed matched P&L **negative**.
* It was being abandoned chronologically: 15.4% (Mar) → 0.32% (Aug); 0.24% OOS.
* Median residual carried into settlement is **141.8 shares = 7.7% of all
  shares traded in the window** (p90 462). Worth **−$144,065** across the sample.

**Consequences for the model.** `OPEN → EXTEND → EXTEND / WAIT → COMPLETE or
SWITCH` in §2 is right about the actions and wrong about the terminal state:
there is no completion controller driving inventory to zero. The oscillation
IS the process, and matched inventory is its by-product.

**Predictors of the next action, chronologically validated.**

* P(next action = `EXTEND`) = **0.66** unconditionally.
* The only feature that separates it AND keeps its bin ordering out of sample
  is **his own same-side run length** (Spearman 1.00; run 4+ → 0.733 OOS).
  This supersedes `last_rel` as the concrete form of "repeated same-side
  actions are predictive".
* **Directional momentum is falsified.** The Δp-over-60s relation replicates
  (ρ = 1.00) but is SYMMETRIC — 0.672 for Δp > +0.05, 0.663 for Δp < −0.05,
  lowest (0.622) when flat. **|Δp| predicts extension; sign(Δp) does not.**
  Movement direction must not be used as a favored-side signal.
* **Favored-side accumulation is falsified.** P(next fill on the favored side)
  = 0.4919 TRAIN / 0.4847 OOS, and it FALLS to 0.4707 OOS when attainable pair
  cost exceeds 1.02. Conditioning on where the residual already sat collapses
  the apparent effect to run persistence (0.64 favored / 0.34 against, flat
  across every pair-cost bin).
* **Carried residual lost money on both sides**, favored included: +$1,631
  TRAIN but **−$12,971 OOS**. The high favored-side win rate is priced, not an
  edge. This extends "no demonstrated directional skill" in §2 from "~50%
  winner rate" to "negative expectancy after cost basis, out of sample".

**Replication and monetization now separate (OWNER 2026-09-17).**

* **FIDELITY** — reproduce the observed flip/extension process.
* **ADAPTED** — determine how much of that process is actually necessary to
  harvest the matched economics WITHOUT inheriting the negative residual
  economics. This is the central optimization question, worked in
  `analysis/l5/exploratory_v2/`.

The useful frontier is **maximum matched economics retained per dollar of
residual risk removed**. A gate that removes residual by removing the inventory
cycling that creates matched inventory is not a win and must be reported as the
matched-volume loss it is.

**Unobservable and never imputed:** the historical book at any instant; queue
position; L5's unfilled, resting or cancelled orders — so *"L5 waited"* and
*"L5 quoted and was not hit"* are the same observation in this data; maker vs
taker per fill; per-market incentive credits.

## 2b. ADAPTED conclusions — EXPLORATORY_V2, FROZEN 2026-09-17

**SCOPE: BTC15m ONLY.** Source: `analysis/l5/exploratory_v2/EXPLORATORY_V2.md`,
hashes in `analysis/l5/exploratory_v2/FROZEN.json`. Frozen by owner direction;
no further historical tuning. These are behavioral conclusions, not a policy.

1. **BTC15m FIDELITY is inventory oscillation, not flat completion.** 84.3% of
   imbalance episodes end by overshooting through zero; 1.2% land on it; one of
   5,546 windows ended flat.
2. **Same-side run length predicts L5's behaviour but is NOT a stable control
   variable.** It is the strongest replicating predictor of `EXTEND`
   (Spearman 1.00 OOS), yet the profitable cutoff MOVED — total per share
   crosses zero at run position ≈9 in training and ≈4 out of sample — and every
   run-cutoff gate is net-negative in training. **Predictor ≠ control.**
3. **|Δp| predicts more subsequent opposite-side opportunity, and also worse
   residual economics.** P(opposite fill within 60 s) rises monotonically in
   both periods, but the attainable pair with that fill is 0.9900 in EVERY bin —
   more opportunity at an unchanged price — while residual P&L per share
   deteriorates monotonically. Net value-destroying past |Δp| ≈ 0.02 OOS.
4. **Favored-side momentum is NOT supported.** P(next fill on the favored side)
   is 0.4919 TRAIN / 0.4847 OOS and FALLS when pair cost exceeds 1.02;
   conditioning on where the residual already sat collapses the effect to run
   persistence. `sign(Δp)` is not a signal and is used nowhere.
5. **Instantaneous pair cost is NOT equivalent to eventual effective pair
   economics.** Scored by what the shares actually earned, the 1.00–1.02
   instantaneous band is the BEST in both periods (+98 / +61 bp) and ≤0.96 is
   NEGATIVE in both (−28 / −119 bp); median window pair cost is near-identical
   across every band. This does not disturb the window-level `effective pair
   ≤0.96 → +600 matched bp/$` finding — different object, different measurement.
6. **August BTC15m trading economics were extremely thin and cannot be rescued
   with unattributed incentives in our clone.** August carried 59% of all
   BTC15m spend and returned 3.7 bp, matched (+$104,065) and residual
   (−$102,687) almost exactly cancelling, while ACCOUNT-LEVEL incentives that
   month were $61,022 — 44× the month's BTC15m trading P&L and 4.0× it across
   the sample. Those credits are account-wide and are never attributed to a
   market, an episode or our clone.

**Standing disposition for prospective work.**

* `ADAPTED_V2E` (matched-inventory preservation) — **leading hypothesis for
  prospective testing. NOT promoted to live.**
* `ADAPTED_V2A` (proposed-residual cap) and `V2F` (hybrid) — secondary
  challengers, preserved.
* `V2C` (same-side run cutoff) — **rejected as a control rule.** Retained as
  evidence, never as a gate. This supersedes any earlier treatment of a
  run cap as the ADAPTED control.

## 2c. EPISTEMIC CORRECTION — the matched/residual split is not a fact

**OWNER 2026-09-17.** Source: `analysis/l5/causal/MATCHED_SHARE_LINEAGE_V1.md`,
hashes in `analysis/l5/causal/FROZEN_LINEAGE.json`.

> **Matched vs residual P&L attribution is ACCOUNTING-METHOD DEPENDENT.
> TOTAL CASH P&L is authoritative.**

On BTC15m the two conventions reconcile to the same cash and disagree
enormously about the split:

| | matched | residual | **total** |
|---|---|---|---|
| average cost (authoritative for cash) | +$162,257 | −$144,190 | **+$18,067** |
| FIFO lineage | +$57,344 | −$39,277 | **+$18,067** |
| **reallocation** | **−$104,913** | **+$104,913** | **0** |

**$104,913 of disagreement on an $18,067 total.** The mechanism is understood:
L5 buys the falling side, so the last shares on the heavy side are the
cheapest; FIFO leaves those as residual while average cost charges the residual
the mean price. **Average cost overstates BOTH legs.**

**Forward rules.**

* Do **not** use the average-cost matched/residual decomposition as causal
  proof of anything.
* Preserve **both** average-cost and FIFO where each is useful, and label which
  is in use.
* **Never confuse either attribution with venue cashflow.** Only the total is a
  cashflow. This is the same distinction as §2d below and as
  `MATCHED_REALIZED_UNCONFIRMED` in the live scoreboard: the venue nets, and
  what it reports is a total.
* Earlier statements of the form *"every dollar of BTC15m profit comes from the
  matched leg and the residual gives most of it back"* are **true under average
  cost and materially weaker under FIFO**. They are not retracted, they are
  **qualified** — and they must carry the convention from now on.

**What survives unaffected.** The lineage findings characterise **acquisition
and complement trajectories** — time-to-complement, TTE at acquisition,
reversal count, run position — which are measured from the tape and do not rest
on the accounting split. Those remain the useful output.

**The lineage cutoffs are NOT gates.** No `TTE<60`, no
`complement_timeout=120`, no `run cap=N` is created from these historical
observations. Whether the relationships survive is decided by the preregistered
prospective residual-episode diagnostics at 5/15/30/60/120/300 s.

## 2d. OWNER DECISION 2026-09-18 — NO pair-cost ceiling on the Gemini live path

**Ruling (owner, 2026-09-18).** For CONTINUOUS-5 the controller behaviour is
preserved unchanged. A `COMPLETE` above $1.00 is **permitted** when the existing
L5 controller chooses it and all existing absolute risk limits pass. **No 0.96,
0.98, 1.00, 1.05 or other pair-cost ceiling is added.** `<= 0.96` is classified
as a **RISK OVERLAY**, not strategy fidelity.

**Evidence the ruling rests on** — `analysis/l5/complete_boundary_report.json`,
`analysis/l5/complete_boundary_choice.json`, written up in
`L5_COMPLETE_BOUNDARY_FINDINGS.md`. Independently reproduces
`l5_completion_ceiling.json` (n, p10, p25, p50, p75, p99, max all identical).

* **33,765 historical COMPLETE actions** analysed, 2,216 windows, 2026-03-19 → 08-31.
* **42.75%** of L5 COMPLETEs were **>$1.00**.
* **33.60%** were **>$1.05**.
* In **cheap-held-leg** states shaped like ours, expensive completion is
  *especially* common: held leg 0.10–0.25 → median pair **1.0939**, median
  completing price **0.90**, 66.1% above 1.05.
* In the **narrow comparable state** (held 0.08–0.22 and completing price
  0.82–0.97, n=1,125), **78.76%** were **>$1.05**; p10 is already above $1.00.
* **BTC-5m**, our exact venue analogue, shows substantial >$1.05 completion
  behaviour: **40.46%** of its completes, and only 51.7% at or below $1.00.
* Expensive completes were **usually followed by further fills** (97.21%) and
  **usually ended with improved final pair economics** (96.78% of windows end
  better than the expensive complete itself).
* **Therefore preventing a 1.05 COMPLETE would materially alter the behaviour we
  are attempting to validate.** At 0.96 the gate would forbid roughly four
  completes in five in this configuration, leaving inventory one-sided and
  carrying directional residual.
* **This does NOT establish that >$1 COMPLETEs are profitable on Gemini.** That
  is precisely what live evidence must measure. Fidelity to L5 and profitability
  on our venue are separate claims and neither implies the other.

**Corroboration from §2b(5).** Scored by what the shares actually earned, the
1.00–1.02 *instantaneous* band was the BEST in both periods (+98 / +61 bp) while
`<= 0.96` was NEGATIVE in both (−28 / −119 bp). An instantaneous pair-cost
ceiling is not the same object as the window-level effective-pair finding, and
the instantaneous version does not survive scoring.

**Scope, stated precisely so it is not over-read.**

* This governs the **Gemini continuous-inventory live path**, which contains no
  pair-cost gate and is not to be given one.
* `CLAUDE.md`'s spring discipline — "prefers materially cheaper pair completion
  (roughly `<= 0.96` **where the venue implementation uses that target**)" — is
  unchanged and uncontradicted: the Gemini venue implementation does not use
  that target. The PMUS spring path is untouched by this ruling.
* This is **not** permission to complete at any price. The absolute bounds still
  apply and still fail closed: `max_residual 0.04`, `max_imbalance 0.04`,
  `max_yes/max_no 0.10`, `max_total_spend_usd 0.15`, `action_size 0.01`,
  12-slot envelope tightened to the cohort's 6.
* Any future ceiling must be **labelled a risk overlay**, set as a bound rather
  than a behavioural claim, and carry its stated cost.

**Pinned by test.** `tests/test_gemini_cohort_and_cycle.py::
test_no_pair_cost_gate_exists_in_this_path` asserts the absence of every
enumerated ceiling, so a gate cannot reappear silently.

## 3. Measured historical evolution

Artifact: `analysis/l5/l5_ramp_analysis.json`  
Implementation/report commit: `ef1da90a4ea5954a67e7248beba9993bbd9ad267`

### Mar 19–25 — early era

Small, mostly one-sided activity. Not the mature strategy.

### Mar 26 — systematic-era changepoint

The measured spring activity-scale break occurs **Mar 26**, not Apr 9. This is the first major transition into systematic behavior.

### Mar 27–Apr 5 — developing systematic behavior

Baseline for the subsequent patience improvement.

### Apr 6–10 — the patience upgrade

This period is **not** an activity scale-up.

Compared with Mar 27–Apr 5:

- trades/window fell roughly **23.6 → 14.9**;
- median size stayed roughly **10**;
- opposite-leg wait median increased **16s → 24s**;
- p75 opposite-leg wait increased roughly **30s → 46s**;
- final pair cost improved roughly **0.9851 → 0.9496**;
- within-window pair-cost drift improved from roughly **-0.0016 → -0.0176**;
- complement fill mean fell roughly **0.516 → 0.447**;
- `P(EXTEND | EXTEND)` rose roughly **0.572 → 0.621**;
- same-side run p90 increased **4 → 5**;
- imbalance repair half-life increased roughly **94s → 214s**.

Interpretation: **fewer, more patient actions at better completion prices**. L5 learned to tolerate imbalance longer and price the wait rather than force immediate completion.

### Apr 21 — second measured boundary / first credited maker rebate

A second boundary appears around **Apr 21** in both size and EXTEND persistence. This is also the date of the first credited `MAKER_REBATE` in the accounting ledger.

This is timing evidence, **not proof of causality**.

Later credited incentive milestones:

- first maker rebate: **2026-04-21**
- first taker rebate: **2026-06-20**
- first reward: **2026-08-08**

### Aug 28–31 — mature current behavior

Current-mature behavior adds capabilities not present in spring:

- same-side runs are longer (mean around **3.11**, p90 around **7**);
- `P(EXTEND | EXTEND)` around **0.695**;
- much larger peak imbalance but faster repair;
- opposite-side wait can fall to only a few seconds because flow/liquidity brings the complement quickly;
- final pair cost can approach **~0.995**;
- concurrency reaches multiple simultaneous markets (mean ~3.1, max 8 in measured sample);
- maker share falls sharply near settlement where labels are measurable.

These behaviors all postdate the incentive era and must remain **locked** for our clone unless equivalent economics are source-confirmed on our account.

## 4. Important corrections to earlier working hypotheses

1. **Apr 9 is not the scale changepoint.** Mar 26 is the first measured systematic activity break; Apr 6–10 is a patience/quality upgrade.
2. **“Smaller but more often” is not an across-epoch rule.** Per-fill size generally rose across epochs. Smaller action size appears as a **concurrency effect inside August** (e.g. size falls as simultaneous-window count increases).
3. **Concurrency was not part of the spring strategy.** Spring max concurrency was 1. Multi-market concurrency was added much later.
4. **Spring maker/taker cannot be measured honestly.** Pre-Aug canonical rows lack maker/taker labels; do not proxy or infer them as fact.
5. **Near-$1 pairs are incentive-era behavior.** Spring patience targeted materially cheaper pair completion (~0.95–0.96 range). Do not tolerate ~0.995 pair economics without source-confirmed incentive support.
6. **Directional skill remains unsupported.** Residual-on-winner is approximately coin-flip across regimes. Strengthened 2026-09-14: in 5m the one-sided motifs are *significantly negative* per window (t = −2.63 BTC5m, −3.11 ETH5m) with p05 = −10,000 bp.
7. **Fees ARE recoverable from the public tape; they were not missing.**
   `usdcSize` exceeds `price × size` on a BUY by exactly `0.07 · p · (1 − p)`
   per share or by nothing at all — bimodal at 0.00/0.07 over 761,020 fills.
   `usdcSize` is the authoritative cash, and using it makes the
   matched/residual decomposition reconcile to realized cash exactly. This does
   **not** overturn item 4: the tape distinguishes fee-bearing from fee-free,
   which is not the same as maker from taker, and that attribution stays
   uninferred. Evidence: `analysis/l5/motifs/MOTIFS_5M.md`.
8. **A missing REDEEM row is a loss, not missing data.** Redemption is complete
   whenever it pays (`redeem_qty / winning-side qty` = 1.000 at p05/p50/p95, at
   $1.00/share), so a long-settled window with no REDEEM row is one where the
   held side lost. Dropping those windows keeps only winners and inflated the
   one-sided 5m motifs from +723 to +3,194 bp/$. Only windows inside the 24 h
   capture tail are genuinely indeterminate.


## 4b. Regime architecture — RESEARCH LABELS, NOT PRODUCTION RULES

The behavioral model is explicitly **not** one policy. Cohorts are carried as
separate regimes so that evidence from one is never silently spent on another.
Nothing in this section gates, sizes, admits or prices anything.

| cohort | label | what it rests on |
|---|---|---|
| **BTC15m** | **continuous inventory-management candidate** | 98.9% market coverage, 13 s entry delay, 128 fills / 30 switches, 0.9067 matched, 88.6% of market life; switch signal OOS AUC 0.674 on n=446 with 196 switches; **15.4%** of fills fee-bearing |
| **BTC5m / ETH5m** | **short-sequence candidate** | fills p50 7 and 5, windows p50 237–257 s; motif structure stable, switch signal underpowered (1 and 4 OOS switches); **100%** of fills fee-bearing |
| **ETH15m** | **insufficient evidence** | 580 windows, 5 OOS switches; below the pre-fixed ≥40-row / ≥30-per-class threshold |

Three rules follow, and they are about method, not money.

1. **The BTC15m switch result is not a universal L5 switching rule.** It is
   BTC15m-only. 1,484 of 1,652 observations are BTC15m; the aggregate must not
   be read onto 5m. Where a cohort is underpowered the label is INSUFFICIENT
   EVIDENCE, never a pooled number.
2. **A 5m answer must come from 5m evidence.** See
   `analysis/l5/motifs/MOTIFS_5M.md` and `src/l5_5m_coverage/recorder.py`.
3. **The regimes differ at the venue, not just in the fit.** L5 pays the
   `0.07·p·(1−p)` per-share fee on 100% of 5m fills and 15.4% of BTC15m fills.
   That is a measured fee (§4.7), not a maker/taker attribution.

## 5. Persistence-v2 model

Commit lineage: `b5db1985...` before ramp-analysis commit `ef1da90a...`.

The frozen v1 action model used price-band × TTE × imbalance but did **not** condition on `last_rel`.

Persistence-v2 adds:

- base state + `last_rel` + `last_size`;
- shrinkage/backoff to avoid sparse-state overfit;
- explicit `persist_extend_p` vs `switch_complete_p`;
- proposed relation, side, maker price zone, and reevaluation cadence;
- side-by-side comparison with the frozen v1 and pair gate.

Illustrative populated state from the delivered model:

- price ~45–55¢;
- imbalance 1–10;
- previous relation = `extend`;
- next relation roughly **67.1% EXTEND / 32.9% COMPLETE**.

This is the first model that directly encodes the repeated-increment behavior rather than merely recording it.

Sports TTE remains incomplete until event/end-time metadata is wired accurately; off-distribution states must remain explicit.

## 6. Spring-discipline rules for the penny → incremental clone

These are the current behavioral operating rules unless new L5 evidence overturns them.

### R1 — One market at a time first

Spring concurrency was 1. Do not introduce multi-market live concurrency before the one-market behavior is clean and settled repeatedly.

### R2 — Mid-band, maker-first, no directional thesis

Favor the L5-like mid-band states (historically ~45–55¢ modal spring entry zone) and maker-first execution. Do not buy a side because we believe it will win.

### R3 — Persist in small flat increments

Same-side extension is a first-class action. Prefer increasing **frequency before size**.

### R4 — Do not force completion; price the wait

Apr 6–10's defining improvement was longer waiting for materially cheaper complements. Persistent residual management should outlive an arbitrary 300-second shell when risk and time-to-settlement allow it.

However, public/theoretical BBO availability is not proof of an executable fill. Preserve that distinction.

Waiting is not the same as a **ceiling**. On the Gemini continuous-inventory path the owner has ruled that no pair-cost ceiling exists and a `COMPLETE` above $1.00 is permitted when the controller chooses it and the absolute bounds pass — see **§2d**. R4 prices the wait; it does not forbid paying up.

### R5 — Bounded imbalance

Temporary residual is allowed; unlimited residual is not. Inventory must remain inside explicit dollar/quantity caps and must block unrelated new first legs under the current one-market stage.

### R6 — Rebate-era behaviors stay locked

Do **not** enable merely because mature L5 does them:

- near-$1 pair tolerance;
- 3–8 concurrent markets;
- aggressive late taker completion;
- economics dependent on unconfirmed rebates/rewards.

Unlock only when equivalent incentives are actually credited and source-accounted for our account, followed by replay/shadow validation.

### R7 — Historical scaling order

**PATIENCE → FREQUENCY → SIZE → CONCURRENCY**

This is the default promotion order for our clone.

## 7. PMUS sports/props transfer architecture

Do not focus on one game manually. The intended system is:

**full-slate discovery → normalized per-market state → rotating realtime hot set → per-market L5 state machine → opportunity ranking → global risk allocator → bounded executor**

The full-slate discovery fix proved the previous “no opportunities” state was partly a coverage artifact:

- live discovery has observed well over 100 sports markets with the majority being props;
- old scanning only exposed a narrow subset / subscription cap;
- full-slate eligible-market reporting and rotation/warm-start are now part of the transfer path.

A game/event is a useful context cluster, not the only market the system may watch.

## 8. MICRO-LIVE execution rules

Purpose: obtain real order/fill/queue/settlement evidence with penny-scale exposure while strategy fidelity is still being learned.

The canonical micro executor must have exactly one implementation after reconciliation. Required properties:

- separate `PMUS_MICRO_*` arming keys; micro arming must never arm q=1;
- venue `minimumTradeQty` + tick read live;
- target roughly **$0.01 per action**;
- venue-minimum exception only when bounded; no silent q=1 fallback;
- **≤$0.02 worst-case notional on either possible binary leg**;
- **≤$0.05 cumulative first-leg notional per market** during the current learning stage;
- Decimal quantity preserved end-to-end;
- partial first/complement fills become exact residual inventory, never rounded to 0 or 1;
- account-wide preflight: no open orders and no unrelated nonzero positions;
- preview + fresh BBO checks before mutation;
- mutation calls are never automatically replayed;
- market-scoped cancel/deadman;
- one market/campaign, no auto-repeat for the initial micro probes;
- fees/rebates/rewards tracked separately;
- `realized_pnl` remains null until authoritative settlement/cashflow evidence supports the label.

Live venue evidence has already shown `minimumTradeQty=0.01` on sports markets, allowing real sub-1¢/≈1¢ action notionals.

## 9. Current live-stage promotion policy

Current stage is **bounded MICRO-LIVE learning**, not continuous autonomous scaling.

First persistent incremental live sequence should resemble spring discipline:

**maker OPEN ~1¢ → reassess → optional same-side EXTEND ~1¢ → reassess → patient fee-safe COMPLETE when price improves → settle/audit**

Initial restrictions:

- one market;
- no concurrent live market;
- no unsupported rebate assumptions;
- no near-$1 pair tolerance;
- no automatic late taker just because mature August L5 did it;
- no risk increase after a win;
- account/residual must be source-confirmed clean before a new first leg.

Promotion requires action-by-action behavioral similarity **and** clean executable/settled evidence, not aggregate shadow P&L alone.

## 10. Strict accounting lifecycle

Always preserve:

**historical replay → shadow → live probe → submitted → filled → settled → realized**

Never call any of the following realized:

- theoretical fills;
- stale/public BBO opportunity;
- shadow inventory;
- modeled pair edge;
- unconfirmed rebates/rewards;
- balance movement without sufficient source identity;
- mark-to-market gains;
- an order merely being submitted.

Report separately:

1. matched-pair economics;
2. directional residual P&L;
3. spread capture;
4. maker rebates;
5. liquidity rewards;
6. fees;
7. settlement/redemption cashflows.

## 11. Risk shell

Always bound:

- dollars per action/market;
- directional residual;
- cumulative imbalance;
- daily realized loss;
- concurrent markets;
- total strategy exposure;
- open-order lifetime and deadman cancellation.

A residual must never be hidden by integer conversion. Any nonzero Decimal position is a real position.

## 12. Evidence that remains missing / next P0s

- ~~reconcile PR #169 and transfer-branch micro implementations into exactly one canonical micro executor~~ — **done at `6e0669c1`** (#169 engine + isolated `PMUS_MICRO_*` arming, SKIP semantics, $0.05 durable cap, `micro_eligible`);
- run the bounded `spring_l5` one-market session (`src/pmus_spring_l5_session.py`) per comment 5520504343 once staged DISARMED and reviewed;
- wire accurate sports TTE/event clock into persistence-v2;
- collect realtime persistence-v2 shadow sequences across sports/props;
- validate bucket→executable size/cadence mapping with real micro fills;
- diagnose why real DET/MIN complement execution missed theoretical within-deadline fee-safe windows;
- append newest L5 public activity continuously and run drift checks;
- keep April-ramp conclusions and current-mature behavior separately labeled;
- do not enable intentional multi-market inventory until one-market spring-discipline probes are repeatedly clean.

## 13. Key source references

- Issue #167 — canonical coordination / handoffs.
- `analysis/l5/l5_ramp_analysis.json` — measured ramp/current comparison.
- `analysis/l5/l5_action_model_v2.json` — persistence-aware action model.
- `src/pmus_l5_persistence_v2.py` — persistence-v2 policy/shadow layer.
- `src/pmus_l5_action_shadow.py` — per-market L5 inventory/action state.
- `src/pmus_micro_executor.py` — micro-live executor path (must remain single canonical implementation after reconciliation).
- `src/pmus_settlement_auditor.py` — PMUS settlement/reconciliation authority.
- `L5_STATUS.md` — broader historical pipeline/status; some transfer/live details may lag and should be refreshed against issue #167/current branch before operational use.

## 14. Anti-drift rule

Before adding a new strategy, venue behavior, or risk increase, answer:

> How does this reproduce, validate, deploy, or improve the L5-style engine?

If the answer is unclear, it is probably not the priority.

The target remains **real, executable, settled, source-accounted profit**. The historical $480/day objective is a target to measure against, never an assumption.
