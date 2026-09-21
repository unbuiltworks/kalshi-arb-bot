> **FOR REVIEWERS (Grok/Claude/ChatGPT): Read this file first, then
> independently verify material claims against the cited code/data. This
> document is a navigation/status layer, not evidence by itself.**

# Gemini L5 — Current State

*Last updated 2026-09-20T20:00Z.*

> **WHERE THIS FILE LIVES.** Branch
> `claude/gemini-btc-economic-viability-h3u7zl`. It is **not on `main`**, so a
> reviewer browsing the repository's default branch will not see it. Use
> `?ref=claude/gemini-btc-economic-viability-h3u7zl` or check out that branch.
>
> **OPEN P0 — VENUE-SEMANTICS MISMATCH (flagged 2026-09-20, not fixed).**
> Gemini's published schemas represent an account's holding as **one signed
> position per instrument** (positive = yes, negative = no), which our
> `Inventory(yes_qty, no_qty)` can over-represent. Evidence, including the
> falsification of the "order is rejected"/"cash out first" readings, is in
> [`EVIDENCE_PACKET_003_VENUE_SEMANTICS.md`](EVIDENCE_PACKET_003_VENUE_SEMANTICS.md).
> **No longer latent as of 2026-09-20T22:59:42Z**: the first COMPLETE fill in
> the programme closed a BTC5m window holding matched inventory
> (`windows_ending_matched: 1`), which is the state a signed-scalar venue
> cannot represent. Nothing was changed in response; the experiment runs
> unmodified. The deciding read-only venue test is BUILT but BLOCKED on
> credentials and one permission; the metric-by-metric validity matrix is in
> [`EVIDENCE_PACKET_004_METRIC_VALIDITY.md`](EVIDENCE_PACKET_004_METRIC_VALIDITY.md).
>
> **HEADLINE STATUS: the faithful-transfer question is UNRESOLVED.** The
> experiment has been running since 2026-09-20T19:42Z and no prospective
> contract has yet produced a fill. Nothing here claims Gemini works, and
> nothing here claims Gemini failed.

## 1. Objective

Test whether L5's **evidence-supported continuous inventory behaviour**
transfers economically to Gemini BTC5m/BTC15m.

Gemini is an **adaptation and test venue, not the source of L5 behavioural
truth.** L5's behaviour is established from his own history on Polymarket;
nothing measured on Gemini may be promoted into the canonical L5 model.

The primary outcome is **terminal net economics after settlement** under
executable assumptions — not markouts, not pair basis, not price
availability, not intra-window matched inventory.

## 2. CURRENT Experiment — the faithful prospective test

| field | value |
|---|---|
| service | `gemini-l5-faithful` (`0b919741-0fcc-46ce-9f92-0960e8765e78`) |
| deployment | `3b408c95-6de0-46b7-95d7-e03d7155bfbd`, SUCCESS |
| serving SHA | `f689e6b` |
| branch | `claude/gemini-btc-economic-viability-h3u7zl` |
| policy | **`FROZEN_L5_NATIVE`** |
| cohorts | BTC5m (entry band 240–280s TTE), BTC15m (840–880s) |
| state root | `/state/gemini-l5-faithful` |
| volume | `gemini-l5-faithful-state` (`333f9aab`), mounted `/state` |
| status | **SHADOW**. No session is constructed; no `Driver.place`/`Driver.cancel` reference exists on this path |
| mutations | `mutations_sent: 0` on every heartbeat, both cohorts |
| started | 2026-09-20T19:42:02Z |
| operational (point-in-time) | 19:58:38Z: BTC5m `GEMI-BTC05M2609202000-UP`, `windows: 2`, `cycles: 73`, last rollover `GEMI-BTC05M2609201955-UP` COMPLETED 19:54:39Z. BTC15m `GEMI-BTC15M2609202000-UP`, `windows: 0`, `cycles: 85`, first window not yet closed. `errors: 0`, `restarts: 0`, `mutations_sent: 0` |
| collection-alive evidence | two `L5F_ACTIONS` lines emitted, both BTC5m. **This is a point-in-time observation, not a standing claim of health**; liveness must be re-confirmed from current logs before any result is read from this service |

**Why this experiment exists.** Every previous Gemini service overrode the
native L5 policy with `GEMINI_RECYCLE`, which vetoes the `EXTEND` the frozen
policy asks for as soon as any residual exists. `shadow_soak --policy`
*defaults* to `FROZEN_L5_NATIVE`; the override was explicit in each service's
start command. Since L5's own evidence puts `P(next action = EXTEND)` at 0.66,
that override suppressed the dominant action of the process under test. **The
faithful continuous L5 process had never been run on Gemini until this
service.**

## 3. Canonical Implementation

Reused as-is. No new strategy framework was written for this experiment.

| layer | module | label |
|---|---|---|
| behavioural surface | `src/l5_surface/engine.py` → `desired_surface` | `L5_PASSIVE_SURFACE_ENGINE_V1` |
| continuous inventory engine | `src/gemini_execution/continuous_probe.py` → `plan`, `classify`, `window_phase` | `GEMINI_L5_CONTINUOUS_PROBE_V1` |
| policy selector | `src/gemini_execution/tape_replay.py` → `desired` | `FROZEN_L5_NATIVE` (`POLICY_A`) |
| inventory accounting | `continuous_probe.Inventory` (`matched`, `residual`, `imbalance`, `run_leg`, `run_len`) | — |
| queue / fill model | `src/gemini_execution/hypothetical.py`, `tape_replay.size_ahead`, `Quote.take` | `QUEUE_SYMMETRIC`, rows stamped `CORRECTED_QUEUE_V1` |
| replay (per contract) | `tape_replay.replay_window` | walks one window print-by-print to settlement |
| settlement / reconciliation | `src/gemini_execution/metadata.py` → `resolution_for`, `_strike_check` | venue-published `resolutionSide`, N-read, strike-checked |
| shadow runner | `src/gemini_execution/shadow_soak.py` | emits `L5F_ACTIONS`, `L5_COHORT_HB` |
| hard bounds | `continuous_probe.risk_gates`, `would_breach` | see §6 |

## 4. L5 Behavioural Evidence Being Tested

Source: `L5_OPERATING_MODEL.md` §2a — 5,546 windows, 693,916 fills,
chronological TRAIN/OOS split. **Polymarket history, not Gemini.**

### SUPPORTED

| finding | value | verified |
|---|---|---|
| continuous inventory, not pair-and-stop | no completion controller exists; matched inventory is a by-product of oscillation | §2a |
| EXTEND frequency | `P(next action = EXTEND) = 0.66` unconditional | §2a |
| same-side runs | expected run ≈ `1/(1−0.66)` ≈ 2.9 actions; run length is the **only** feature separating the next action that keeps bin ordering OOS (Spearman 1.00; run 4+ → 0.733 OOS) | §2a |
| overshooting balance | **84.3%** of imbalance episodes end by overshooting **through** zero | §2a |
| rarity of exact-flat stopping | 1.2% land exactly on zero; **one window of 5,546 ended flat** | §2a |
| no canonical hard $1 pair-cost ceiling | **42.75%** of L5's 33,765 COMPLETEs were above $1.00; owner ruling 2026-09-18 states the Gemini live path takes no pair-cost ceiling, and any future ceiling must be labelled a risk overlay carrying its stated cost | `L5_OPERATING_MODEL.md` §2d |
| carried residual is a known-negative leg | +$1,631 TRAIN but **−$12,971 OOS**; median residual carried into settlement worth −$144,065 across the sample | §2a |
| time-to-settlement behaviour | the lane set's own `TTE_policy` is `[0, 300]` with `withdrawal_policy WITHDRAW_ON_TTE_FLOOR`, `tte_floor_s = 0` — i.e. L5's surface stands to expiry | `artifacts/l5_surface/`, quoted in `continuous_probe.py` |

### FALSIFIED — these are prohibitions, and the engine must not use them

| claim | why falsified |
|---|---|
| directional momentum picks the side | the Δp-over-60s relation replicates (ρ = 1.00) but is **symmetric**: 0.672 for Δp > +0.05 against 0.663 for Δp < −0.05. `|Δp|` predicts extension; `sign(Δp)` does not |
| favored-side accumulation | `P(next fill on the favored side)` = 0.4919 TRAIN / 0.4847 OOS — a coin flip — and falls to 0.4707 OOS when attainable pair cost exceeds 1.02 |

### UNRESOLVED

**Which side to quote when flat.** Both candidate rules above are falsified and
nothing in the canonical analysis determines a replacement. The surface quotes
both outcomes and lets the market choose. That is the neutral implementation,
**not a finding**, and it must not be reported as one.

## 5. PREVIOUS ADAPTATION evidence — Gemini drift (not canonical L5)

Everything in this section is **PREVIOUS ADAPTATION evidence**. It was
produced under `GEMINI_RECYCLE` or under the econ arms, **not** under the
faithful policy, and it is therefore not evidence about L5 transfer. **None of
these is canonical L5.** They are preserved as experiments and history, and several
still run in `gemini-econ-shadow`, but they are not the current faithful test.

- `GEMINI_RECYCLE` (`POLICY_B`) — vetoes EXTEND once residual exists
- pair-completion priority / PAIR_COMPLETE-and-stop
- same-side EXTEND suppression
- pair-economic floors and hard pair-cost ceilings
- flow thresholds (`PP_F0/F1/F2/F5` ladder, `FLOW_REVERSIBLE`, `NOT_CONCENTRATED`)
- toxicity filters (`TOXICITY_V1`)
- YES-first / leg-asymmetry hypotheses — **UNRESOLVED**, not reproduced by the later cohort (`EVIDENCE_PACKET_001_LEG_ASYMMETRY.md`)
- fixed maximum spread gates
- max-one-naked-residual as *behavioural* logic

## 6. Current Safety Overlays

Every non-L5 constraint still acting on the faithful shadow. **The last column
is the one that matters:** a bound that can block an L5-requested action must
never be read as L5 declining to act. `L5F_ACTIONS` reports blocks split by
`OSCILLATOR` (behaviour), `ENVELOPE_MAX_IMBALANCE`, `ENVELOPE_MAX_LEG` and
`END_OF_WINDOW` (all safety).

| overlay | value | why it exists | can it block an L5-requested action? |
|---|---|---|---|
| `ACTION_SIZE` | 0.01 | the venue's own `quantityMinimum` / `quantityIncrement` | No — it sets size, not permission |
| `MAX_IMBALANCE` / `MAX_RESIDUAL` | 0.04 | bounds the directional leg; = 4 × action size, sized from L5's own run-length evidence (modal run 2.9, validated regime run 4+) so it admits the oscillator rather than capping it at one | **YES** — blocks a widening EXTEND at run 4. Reported as `ENVELOPE_MAX_IMBALANCE` |
| `MAX_YES` / `MAX_NO` | 0.10 each | per-leg exposure cap | **YES** — reported as `ENVELOPE_MAX_LEG` |
| `MAX_TOTAL_SPEND` | 0.15 | total cash bound | **YES, but as a whole-turn stop, not a per-action refusal** — see PL-2 |
| `NO_NEW_OPEN_EXTEND_TTE_S` | 60.0 (= 2 × `minimum_rest_time`) | an OPEN/EXTEND with under two rest cycles left can only manufacture residual | **YES** — reported as `END_OF_WINDOW`. **Deviates from L5 fidelity**: the lane's own `TTE_policy` is `[0, 300]`, `tte_floor_s = 0` |
| `WITHDRAW_ALL_TTE_S` | 30.0 (= `minimum_rest_time`) | nothing can meaningfully rest below one cycle | **YES** — same caveat |
| `DEADMAN_S` | 20.0 | withdraw rather than quote into a stale book | **YES**, on stale state only |
| `MAX_ACTIONS` | 12 | per-window action budget | **NO — currently inert.** See PL-1 |

No taker crossing is ever used to flatten. Residual that survives is
**carried** and labelled directional exposure.

## 7. Known Defects / Limitations

### Mandatory pre-live items (see `PRE_LIVE_CHECKLIST.md`)

**PL-1 — `MAX_ACTIONS = 12` is inert in this shadow collector.**
`WindowGuard.record()` increments `actions_taken` and trips the stop;
`shadow_soak` never calls it, so the counter stays 0 and the budget never
binds. Acceptable **only** for a zero-mutation shadow whose purpose is to
observe the oscillator uninterrupted — a budget that cannot be spent cannot be
breached. **Mandatory fix before live.** Deliberately not fixed during this
run.

**PL-2 — `would_breach` does not enforce `MAX_TOTAL_SPEND`.** It checks
imbalance and the per-leg caps only. Spend is enforced elsewhere, by
`risk_gates`, as a whole-turn stop rather than a per-action refusal. **A live
runner must not rely on `would_breach` alone to bound spend.** Mandatory
live-path audit before q=1.

### Historical measurement defects

| defect | affected period / data | fix SHA | faithful run affected? |
|---|---|---|---|
| **NO queue-ahead asymmetry** — `size_ahead` returned `None` for every NO leg and `Quote` turned that into **zero** size ahead, so NO rungs filled from the front of a queue they never cleared while YES cleared the whole ladder. Nine consecutive BTC15m windows ended long NO | all Gemini shadow fills before 2026-09-20T00:04Z | `1e3159e` | **No** — fix is an ancestor of `f689e6b`; symmetry is pinned by 83 inversion assertions in `tests/test_gemini_fill_model_side_swap.py`, incl. a test reconstructing the defect |
| **Quantity/unit error** — `ECON_FUNNEL`'s `net=` is an absolute **total** at q=0.01, not per pair; the same error averaged absolute pair economics against per-contract residuals | econ-arm reporting and the derived break-even figure | `b54f774` | **No** — econ arms disabled (`GEMINI_ECON_ARM=0`) |
| **Correlated fills treated as independent** — residuals on one contract share one binary outcome; a row bootstrap produced the degenerate CI `[0.4856, 0.4856]` | all econ-arm expectancy intervals before the fix | `04d9bb0` | **No** — econ-arm analysis only |
| **Persistence gaps** — the fill-sequence ledger was memory-only under `restartPolicyType: ALWAYS`, so redeploys destroyed cohorts (the 48→24 / 140→0 cohort is gone and can only be quoted) | econ-arm cohorts before 2026-09-20T18:39Z | `89d4d28` | **No** — econ-arm structure |
| **Toxicity timestamp-join selection bias** — the label join keyed on `t_candidate` (admission) against a capture stamped at fill time, within 12s, so only fills arriving within ~1.5 cycles of admission were ever labelled: a cohort **enriched for toxicity by construction**. Plus a nearest-match fallback that could mislabel, and a map never refreshed so rows could be relabelled | every `ECON_TOX` `toxic_share` before the fix; rows carry `join_method: LEGACY_TIMESTAMP_HEURISTIC` | `c9789b9` | **No** — econ-arm only |
| **Markout horizon aliasing** — one late `observe()` writes the same value into every unfilled horizon, so 8/30/60/120s are then not independent and "markout deteriorates with horizon" is void on such a row | all econ-arm markout series; now flagged per row via `aliased_with` | recorded `ac0e459` | **No** — econ-arm only |
| **Step 11 never executed** — `arm.finish()` was called without a settlement, so residual settlement value was always `None` | all econ-arm residuals before the fix | `89d4d28` | **No** — econ-arm only |
| **Stale docstring** — `tape_replay.py`'s header still says a NO rung's queue is "treated as zero, which is OPTIMISTIC". That describes pre-`1e3159e` behaviour; the code no longer does it | documentation only | — | documentation defect, not behaviour |

**Standing limitation.** `HypotheticalBook` is an explicit **UPPER BOUND**:
queue position is the displayed size at placement, and our own presence would
have changed the tape being replayed. Replay cannot resolve that in either
direction.

## 8. Evidence Status

| level | what exists on Gemini |
|---|---|
| **HISTORICAL** | L5's own behaviour, from Polymarket: 5,546 windows, 693,916 fills. **Not Gemini.** |
| **SHADOW** | Everything this programme has produced on Gemini: hypothetical fills from `HypotheticalBook` against the public tape. All funnel, markout, pair-basis and terminal-payoff figures. |
| **SUBMITTED** | Yes. Venue-acked maker-or-cancel orders under owner authorizations `CONTINUOUS-2/-4/-6/-8` (e.g. two orders on `GEMI-BTC05M2609181435-UP`, both ACCEPTED). |
| **FILLED** | Yes. The venue's own `/v1/tradevolume` reports **61 maker fills / $52.2573 maker notional** across GEMI crypto symbols, 2026-08-27 → 2026-09-18. |
| **SETTLED** | Those contracts expired, but **no Gemini position has been carried to settlement and reconciled to the account by this programme.** One settled cycle exists on **Polymarket**, not Gemini (`analysis/l5/canary_f7a3e224/FIRST_SETTLED_CYCLE.md`, realized −$2.7322). |
| **REALIZED** | The only venue-confirmed Gemini cash is **+$0.51** of credited maker rebates (7 `AdminCredit` rows, `POST /v1/transfers`, 97.59 bp of maker notional). **Gemini trading P&L has never been reconciled and is unquantified — not zero.** |

**No shadow economics are ever to be called realized.**

## 9. Current Money Question — STATUS: UNRESOLVED

> **Does `FROZEN_L5_NATIVE` continuous inventory produce positive terminal
> SHADOW economics on Gemini BTC5m/BTC15m under the corrected symmetric
> execution model?**

**Status: UNRESOLVED pending prospective evidence.** No prospective
contract has produced a fill yet, so none of outcomes A–D in §10 is
supported. Any earlier Gemini number is PREVIOUS ADAPTATION evidence (§5) and
does not answer this question.

Required outputs, **BTC5m and BTC15m separately, never pooled**:

- independent settled contracts
- contracts traded (≥1 fill)
- EXTEND requested / rested / filled
- COMPLETE requested / rested / filled
- safety-blocked actions by reason (`ENVELOPE_MAX_IMBALANCE`, `ENVELOPE_MAX_LEG`, `END_OF_WINDOW`, `OSCILLATOR`)
- overshoots through zero
- recycling cycles
- terminal residual
- fees
- terminal P&L **excluding rebates**
- capital-minutes
- opportunity frequency

## 10. Decision Gate

| outcome | meaning |
|---|---|
| **A** | L5 requests actions but Gemini cannot execute/fill them |
| **B** | Safety overlays materially prevent faithful behaviour |
| **C** | Faithful behaviour executes but terminal economics are negative |
| **D** | Faithful behaviour executes and prospective terminal economics are positive |

**No automatic strategy modification follows any result.**

Positive shadow evidence → review for a bounded q=1 probe. Negative faithful
transfer → consider **ending Gemini L5 work** rather than inventing another
Gemini-specific strategy.

## 11. Pre-Live Blockers

**Do not check an item merely because code exists. Require evidence.**

- [ ] PL-1 working action-count safety (`guard.record()` called on every transmitted action; test drives past 12 and asserts the stop)
- [ ] PL-2 total-spend protection verified on the live call path
- [ ] deadman/cancel protection verified against a real resting order, by `orderId`
- [ ] authoritative inventory reconciliation verified
- [ ] settlement/cash reconciliation verified on a real Gemini settled position
- [ ] hard residual-dollar cap verified
- [ ] concurrent-market cap verified
- [ ] daily realized-loss stop verified
- [ ] serving SHA verified against the authorization
- [ ] explicit owner approval for q=1

## 12. Current Next Action

The eight operational checks passed against runtime output at 19:58:38Z —
serving `f689e6b`, `policy=FROZEN_L5_NATIVE`, econ arms absent from the
heartbeat (only `EXEC_A_PLUS_B` on BTC5m; `L5_ADAPTED_GEMINI_BTC15M_V2` plus
`EXEC_A_PLUS_B` on BTC15m, against six arms in `gemini-econ-shadow` over the
same minutes), both cohorts bound and cycling, isolated state root and volume,
`mutations_sent: 0`, `errors: 0`, `restarts: 0`, control untouched at
`a44de245` — and two BTC5m `L5F_ACTIONS` lines have printed
(`requested={'OPEN': 46}`, `rested={'OPEN': 46}`, no fills, nothing blocked,
`policyVetoed={}`). **That is a point-in-time liveness observation, not a
standing guarantee: re-confirm from current logs before reading any result.**
BTC15m has not yet closed a window. The experiment is frozen; accumulate
prospective settled independent contracts on both cohorts and make no
strategy, threshold, cap, TTE or policy change while collection runs. The next
substantive report answers §9 against §10, BTC5m and BTC15m separately, and
until then the faithful-transfer question stays **UNRESOLVED**.

## 13. Change Log

*Append-only, newest first.*

| when | SHA | what | why | DGP changed? | prior evidence comparable? |
|---|---|---|---|---|---|
| 2026-09-20T20:00Z | `(pending)` | Branch-location note, UNRESOLVED headline, CURRENT/PREVIOUS labelling, fresh 19:58Z operational reading | Reviewer could not find the file on `main`; status wording tightened so liveness is not read as a standing claim | **No** (docs only) | Yes |
| 2026-09-20T19:57Z | `13f56a3` | Canonical current-state handoff created | Single entry point for independent reviewers | **No** | Yes |
| 2026-09-20T19:43Z | `3862722` | `PRE_LIVE_CHECKLIST.md` — PL-1, PL-2 + four more | Record inert/absent controls before they reach live | **No** (docs only) | Yes |
| 2026-09-20T19:39Z | `f689e6b` | Action census: `would_be_action` on holds, `bound` on envelope holds, `L5F_ACTIONS` | Separate MODEL ACTION from EXECUTED SHADOW ACTION so a safety overlay is never read as L5 behaviour | **No** — labelling and counting only; `classify()` reads inventory alone | Yes |
| 2026-09-20T19:34Z | `b86bc05` | `gemini-l5-faithful` service; `--policy FROZEN_L5_NATIVE`, `GEMINI_ECON_ARM=0` | The faithful continuous L5 process had never been run on Gemini | **YES — this is the experiment.** Not comparable with `GEMINI_RECYCLE` runs | **No** — different policy |
| 2026-09-20T19:24Z | `ac0e459` | Markout measurement metadata + aliasing flag + row-level reconciliation table | Two fields the audit required were unrecorded; one was a defect | **No** — econ-arm measurement | Yes |
| 2026-09-20T19:11Z | `c9789b9` | Toxicity join moved to exact `first_fill_t`; `fill_seq_id`/`join_method`; `reconcile()` | The two stores never measured the same rows | **No** — labelling | Prior `ECON_TOX` `toxic_share` **not** comparable |
| 2026-09-20T19:03Z | `04d9bb0` | Bootstrap clustered by contract | Residuals on one contract are one draw | **No** — statistics | Prior intervals **not** comparable |
| 2026-09-20T18:49Z | `b54f774` | Per-leg funnel, breakouts, terminal economics, per-contract unit fix | Denominators and units were wrong | **No** — reporting | Prior per-pair figures **not** comparable |
| 2026-09-20T18:37Z | `89d4d28` | Step 11 settlement labelling; fill-ledger persistence | The residual's settlement value had never been recorded once | **No** | Prior cohorts destroyed, quotable only |
| 2026-09-20T00:04Z | `1e3159e` | `size_ahead` NO-leg queue symmetry | NO rungs filled from a queue they never cleared | **YES** | Pre-fix Gemini fills **not** comparable |
