# Promotion gates for the L5 execution engine

**This document is not authorization to trade.** It is the definition of what
would have to be true first, written down *before* any of it is true, so that
"ready" cannot be decided after the fact by whoever is looking at the numbers.

Every gate is a **measurement with a threshold and a source**. A gate that
cannot be failed is not a gate. Where a threshold is provisional it says so and
says what would set it.

The engine advances one stage at a time and **never skips**:

```
SHADOW -> EXECUTABLE SHADOW -> BOUNDED LIVE PROBE -> FILLED -> SETTLED -> REALIZED
```

A stage is entered only when **every** gate below it is green, and any gate
going red sends the engine back a stage. Gates are evaluated per venue:
Polymarket and Gemini are promoted independently, because their economics
differ (see G3).

---

## Stage 1 — SHADOW → EXECUTABLE SHADOW

### G1. Prospective behavioural evidence

| # | gate | threshold | source |
|---|---|---|---|
| G1.1 | frozen model, sealed before the outcome exists | seal verifies on 100% of rows; 0 tampered | `l5_fidelity.store.load` |
| G1.2 | resolved prospective sample | **n >= 500 resolved** rows on ONE model version | `lineage()` |
| G1.3 | beats its own preregistered baselines | strictly better than `always_ACT`, `always_WAIT`, `repeat_previous_outcome`, `cohort_empirical_frequency` on the 15 s and 30 s horizons | `l5_fidelity` scoreboard |
| G1.4 | not tuned against its own test set | the scoring version is byte-identical to the version frozen before the first scored row; any substantive change becomes a NEW version scoring forward only | content-hash `version_of` |
| G1.5 | lookahead excluded, not argued away | every `NOT_PROSPECTIVE` row excluded from scoring and counted separately | `anchored.resolve_anchored` |

**Status 2026-09-15: RED at G1.2.** v0.2 is accumulating and must not be touched
while it does.

### G2. Executable-shadow coverage

| # | gate | threshold | source |
|---|---|---|---|
| G2.1 | both cohorts observed continuously | BTC5m and BTC15m, >= 24 h of unbroken observation | `l5_shadow` digest |
| G2.2 | both venues scored by ONE evaluator | 100% of intents carry a PMUS **and** a GEMINI evaluation with the same denominator | `exec_shadow/report.py` |
| G2.3 | mapping verified per window, never guessed | 0 routed intents with `mapping_confidence != "exact"` | `gemini_mapping.resolve` |
| G2.4 | fees from the venue, never remembered | 0 evaluations priced against an undeclared fee (the evaluator must refuse) | `terms.pm_terms` / `gemini_terms` |
| G2.5 | unknown never reads as absent | every unreadable book produces `no_market` with an UNKNOWN reason, and 0 `BookParseError` rows silently counted as empty | `depth.parse_book` |

### G3. Venue economics, measured not assumed

| # | gate | threshold | source |
|---|---|---|---|
| G3.1 | all-in cost distribution per venue | p50 and p95 of `effective_cost` reported at each ladder size | `report.venues` |
| G3.2 | capacity distribution per venue | `executable_qty` quantiles; the q=1/2/5/10/20 completion rate | `report.ladder_complete_rate` |
| G3.3 | venue minimums respected | 0 intents counted executable below a declared `orderMinSize` | evaluator gate |
| G3.4 | cross-venue divergence quantified | gap quantiles and which venue is cheaper, per cohort and TTE band | `report.cross_venue_gap` |
| G3.5 | **zero-concession executable rate** | the share of intents executable at or under L5's own achieved price, per venue. Measured today: Polymarket 45.8 %, Gemini 23.3 % | `report.price_slack` |
| G3.6 | **no ceiling widening** | the ceiling policy at promotion is the same one the evidence was gathered under. A cent of concession costs 62.9x L5's per-dollar edge, so raising the executable rate by loosening the ceiling is a different strategy, not a readier one | `price_slack.vs_l5_edge` |

---

## Stage 2 — EXECUTABLE SHADOW → BOUNDED LIVE PROBE

### G4. Executable-shadow FILL evidence

The gate that matters, and the one it is easiest to cheat. A modelled fill is
not evidence.

| # | gate | threshold | source |
|---|---|---|---|
| G4.1 | maker quotes decided from LATER public prints only | 0 outcomes decided from prints at or before placement | `maker.evidence_for_quote` |
| G4.2 | queue is modelled conservatively | we join behind 100% of resting size; cancels and hidden size are never assumed | same |
| G4.3 | decided sample | **n >= 200 decided** maker placements per venue we intend to trade | `report.maker.decided` |
| G4.4 | evidenced fill rate | **>= 0.20** evidenced fills among decided placements, per venue. PROVISIONAL: it is set from L5's own observed completion behaviour and is re-derived once 200 are decided | `report.maker.evidenced_fill` |
| G4.5 | undecided is reported, never counted as either | `undecided_no_prints` published beside the rate, and excluded from the denominator of G4.4 | report |
| G4.6 | realtime replicability stated, not assumed | median `l5_event_to_book_s` published per cohort; a cohort whose lag exceeds its own median inter-fill interval is labelled OBSERVABILITY_LIMIT and may not be promoted on event-time fidelity alone | `clocks` block |

### G5. Accounting correctness (must hold BEFORE any money moves)

| # | gate | threshold | source |
|---|---|---|---|
| G5.1 | the invariant holds | every submitted/fill-derived dollar is represented in exactly one state until venue-confirmed settlement closes it | `tests/test_pmus_accounting_invariant.py` |
| G5.2 | flat/matched/absent never erases an unresolved result | a market absent from the risk set still contributes its floor to daily loss | same |
| G5.3 | conservative recomputation agrees | an INDEPENDENT reconstruction of `combined_loss` agrees with the executor's, or the conservative one wins and blocks | `cross_shadow_live/reconcile_stop.py` |
| G5.4 | settlement polling makes progress | bounded round-robin covers the full pending set; `shutdown_idle` shows the cursor advancing | `tests/test_shutdown_settlement_liveness.py` |
| G5.5 | no manufactured P&L | 0 `SETTLED_REALIZED_UNCONFIRMED` rows counted as realized | accounting tests |

**Status 2026-09-15: G5.1-G5.5 green in code; the legacy PMUS executor's
`combined_loss` is -42.44 against a -30.00 limit, so its own budget gate is
correctly CLOSED. That is the accounting working, not the engine failing.**

### G6. Risk shell

| # | gate | threshold | source |
|---|---|---|---|
| G6.1 | real bounds or no shell | `RiskShell` raises rather than defaulting | `l5_risk/shell.py` |
| G6.2 | bounded residual | per-market residual cap enforced before submission, not after | shell |
| G6.3 | daily stop, inclusive boundary | new exposure blocked at `combined_loss <= stop`, boundary inclusive | `budget_tripped` |
| G6.4 | deadman / cancellation | an unattended order is cancelled within the shell's deadman window; proven by test, not by intention | shell tests |
| G6.5 | one market, one outstanding order, maker-first | enforced by the shell for the learning stage | shell |
| G6.6 | fail closed on unknown | unknown depth, unknown fee, unknown settlement -> refuse | evaluator + router + shell |
| G6.7 | **intent validity enforced** | an intent older than `valid_for_s` is declined by the router BEFORE any venue is ranked; default 30 s, set from the measurement that only 17.1 % of L5's intents survive 30 s | `l5_intent/action.py`, `l5_router/route.py` |
| G6.8 | **age budget reported** | every routing decision records `intent_age_at_decision_s`; a cohort whose median age at decision exceeds its validity may not be promoted | shadow route rows |

---

## Stage 3 — BOUNDED LIVE PROBE → FILLED → SETTLED → REALIZED

| # | gate | threshold | source |
|---|---|---|---|
| G7.1 | `filled` only from venue evidence | `AdapterEvaluation` raises if `filled` is set without `fill_evidence` | `l5_adapters/base.py` |
| G7.2 | maker/taker only when the venue says | `liquidity_role` raises without evidence | same |
| G7.3 | settlement reconciled per market | every probe market reaches a venue-confirmed settlement/redemption row before the next probe starts | settlement auditor |
| G7.4 | realized means settled | P&L labelled `realized` only after G7.3 for that market | accounting lifecycle |
| G7.5 | rebates/rewards separated | maker rebates, taker rebates and liquidity rewards accounted apart from trading P&L, and only once CREDITED | ledger C |
| G7.6 | no automatic repeat | a completed or crashed probe does not relaunch itself | probe runner |

---

## What is explicitly NOT a gate

- Positive aggregate replay P&L. Positive P&L with materially different
  behaviour is not evidence of replication.
- A venue looking cheap on a mid. Mids hide one-sided books.
- Depth at the touch. The ladder decides capacity, not the best quote.
- A rebate that has not been credited.
- Anything measured on a sample the model was tuned against.

## Current gate board

| gate group | Polymarket | Gemini |
|---|---|---|
| G1 prospective behaviour | **RED** (n < 500) | **RED** (shared model) |
| G2 executable coverage | **AMBER** (running, < 24 h) | **AMBER** (running, < 24 h) |
| G3 venue economics | **AMBER** (accumulating) | **AMBER** (accumulating) |
| G4 fill evidence | **RED** (0 decided >= threshold) | **RED** |
| G5 accounting | **GREEN in code**, executor budget CLOSED | **N/A until Gemini trades** |
| G6 risk shell | **AMBER** (shell exists, deadman untested live) | **AMBER** |
| G7 settled/realized | **RED** | **RED** |

No venue is promotable today, and nothing in this document authorizes a trade.
