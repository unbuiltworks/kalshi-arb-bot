# L5 EXPERIMENT REGISTRY

**New file** at commit `0bf8f0ee`+; no prior version existed. Append-only ledger
of experiments and the mandatory PRIOR ART CHECK that must precede each one.

Canonical findings: `L5_EMPIRICAL_MODEL.md` / `analysis/l5/l5_empirical_model.json`.

## The PRIOR ART CHECK (mandatory, before any new experiment)

No experiment starts until all six are answered in writing here:

1. **Does a canonical finding already answer it?** If yes, cite the ID and stop.
2. **Has this been run before on this branch or another?** Search the repo and
   the experiment table; a re-run needs a materially new hypothesis, not a
   re-read.
3. **Is the evidence class sufficient to answer it at all?** State the data
   requirement. If the data does not exist, the answer is "not sourceable", not
   a modeled guess.
4. **Does it require a parameter, threshold, cap or lane change?** If yes it is
   not an experiment, it is a proposal, and it needs preregistration first.
5. **What would falsify it?** Predeclared.
6. **What is the labelling discipline?** EXPECTED / ELIGIBLE / CREDITED /
   REALIZED kept separate; modeled numbers never enter net.

## Experiment log

### EXP-001 — ECONOMIC_COMPLETE conservation/causality audit
**Status: COMPLETE.** Commits `1ea8760e`, `3a92c583`, `7c11bc23`.
Found and fixed a double-charged completion taker fee; replaced the queue-ahead
measure; falsified the stale-order hypothesis (ATOMIC_RECYCLE no-op).
Feeds L5_ECON_00x supersessions and PMUS_ECON_001.

### EXP-002 — five-window frozen scoring
**Status: COMPLETE.** Commit `19955307`. n=5, 3 UP / 2 DOWN, 630 configurations
reconciled. Feeds PMUS_ECON_001.

### EXP-003 — L5 advantage decomposition
**Status: COMPLETE.** Commit `0bf8f0ee`. Feeds L5_ECON_001/002/003,
PMUS_ECON_002, RESIDUAL_001/002.

### EXP-004 — PMUS INCENTIVE REALITY CHECK
**Status: PRIOR ART CHECK PASSED — OPEN, evidence gathering.**

Prior art check:
1. **Already answered?** No. PMUS_ECON_003 establishes that *credited* PMUS
   incentives are **$0**, and PMUS_ECON_002 establishes that missing incentives
   are 74.7% of the q25 gap. Neither says what the venue's program *offers* or
   whether we are eligible. That is the open question.
2. **Run before?** No. `D_INCENTIVE_STATUS = "UNTESTED_NOT_SOURCEABLE"` in
   `src/l5_complement_response_curve.py` refers to *Polymarket's* historical
   crypto reward schedule, a different venue and period. Prior PMUS work
   established the $0 credited fact but never characterised the program terms.
3. **Sufficient evidence class?** Requires first-party PMUS documentation, API
   responses, and our own account credit history. Where a term is not published
   or not returned by the API, the answer is recorded as NOT SOURCEABLE.
4. **Parameter change?** No. Read-only characterisation. Any capital increase is
   a separate proposal and is explicitly deferred until the economics are
   returned.
5. **Falsification:** the check fails if published maker-rebate terms plus our
   observed queue-aware turnover cannot reach the ~+$0.57/window (q25) needed to
   clear zero, or if we are ineligible.
6. **Labelling:** EXPECTED (formula × our volume), ELIGIBLE (program says we
   qualify), CREDITED (venue paid), REALIZED (settled cash). Kept separate;
   nothing modeled enters PMUS net.

Constraints: no deposit, no live order, no deployment, no infrastructure change.
Return incentive economics **before** proposing any capital increase.

### EXP-005 — RESIDUAL_002 mechanism diagnostic
**Status: NOT STARTED.** Highest-value open diagnostic per RESIDUAL_002. Should
characterise PMUS residual by acquisition-price band against L5's band
distribution. Blocked on more usable windows (n=1 terminal residual episode).

## Standing frozen items

- θ = 0.96 (THETA_001). The θ=1.02 n=5 reading is recorded, not selected.
- q grid, continuation variants, DEV/OOS split, completion rules: preregistered
  and unchanged.
- ATOMIC_RECYCLE: falsified/no-op, default OFF, not a variant.
- PMUS_Q1_ARM empty. No live orders. Loss budget untouched.
- Checkpoint 25 remains the preregistered economic checkpoint (currently n=5).

### EXP-004 — PMUS INCENTIVE REALITY CHECK
**Status: COMPLETE.** Commit `8bc089b4`, `analysis/pmus/PMUS_INCENTIVE_REALITY_CHECK.md`.
Findings PMUS_INC_001/002. Forced an amendment to PMUS_ECON_003 (credited maker
rebates are +$2.13, not $0) and a scope narrowing of PMUS_ECON_002.

### EXP-006 — RESIDUAL_002 mechanism (supersedes EXP-005)
**Status: COMPLETE.** Resolved analytically from `E = w − b`, no new data needed.
RESIDUAL_002 → RESOLVED; RESIDUAL_003 records the causal mechanism (EC retires the
winning leg). No rule added.

## DO NOT RETEST — falsified or resolved

| idea | verdict | why |
|---|---|---|
| ATOMIC_RECYCLE / cancel stale orders after completion | **FALSIFIED, no-op** | no stale passive orders exist; residual is 100% from new decisions (`7c11bc23`) |
| B′ baseline as the path to ≥$5/window | **FALSIFIED** | net is unhedged directional residual on 3-of-4 episodes; repriced at L5's measured 0.3540 win rate it is −$10.14 (q25) / −$20.28 (q50). BPRIME_001. |
| θ=1.02 (or any θ) selected from the n=5 sweep | **FORBIDDEN** | THETA_001; sign flipped between n=2 and n=5 |
| "PMUS residual per unit is worse than L5's, so our execution is worse" | **RESOLVED, false** | a losing residual loses exactly its basis; decomposes to band concentration + one draw. RESIDUAL_002. |
| "credited PMUS incentives are $0" | **CORRECTED** | maker rebates +$2.13 over 293 fills are credited. PMUS_ECON_003 amended. |
| Applying L5's 0.919%/$ incentive rate to PMUS as achievable | **NOT AVAILABLE** | BTC15m is in no liquidity program; achievable is maker-rebate-only 0.396%. PMUS_INC_001. |
| Simultaneous YES+NO<$1 arbitrage on this tape | **FALSIFIED** | fresh two-sided pair cost is 1.01–1.03 at every completion instant, never below $1 |

### EXP-007 — rolling money accounting to checkpoint 25
**Status: OPEN, passive.** Per-window ledger: matched gross → taker fees →
matched net → terminal residual → credited incentives → REALIZED-equivalent.
Rolling n=5 available; **n=10/25/50 not yet available** (only 5 usable windows
exist). Hourly routine continues under the frozen policy. No action until n≥25.
