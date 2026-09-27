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

---

## AMENDMENT: the premature-rejection lesson

EXP-003/EXP-004 concluded "no executable path to ≥$5/window." The adversarial
reanalysis (EXP-008) showed that conclusion was **overstated at n=5** because the
residual term was a single observation whose CI spans the target. Two windows of
new data moved q25 from −0.5682 to +0.1067. **The rejection is withdrawn.**

Registry rule added: **a rejection may not be recorded from a term whose
confidence interval spans the decision threshold.** State "not established"
instead.

### EXP-008 — adversarial reanalysis
**Status: COMPLETE.** Amended TARGET_001 to NOT ESTABLISHED OR EXCLUDED; added
MATCHED_001 (allocation-convention band ±$0.80/window at q50) and CAPACITY_001
(q>5.31 is modeled). Confirmed no double counting arithmetically. Found and fixed
a reporting-helper defect that overstated REALIZED-equivalent by the fee term
(`a26269a8`).

### EXP-009 — P0: terminal-residual frequency and severity (SOLE P0)
**Status: OPEN.** Collect and score to **n=25 minimum** under the frozen policy.
Ledger regenerated every firing by `scripts/pmus_window_ledger.py` into
`analysis/pmus/pmus_window_ledger_q{25,50}.json`.

Per-window preserved: terminal residual occurrence · residual contracts ·
residual basis · side · settlement · residual P&L · matched contracts ·
matched gross · fees · expected maker rebate · total net.

Rolling n=5/10/15/20/25 maintained. **Collection does not stop because an
intermediate checkpoint looks good or bad.**

At n=25: **ADVANCE TO BOUNDED LIVE CAPACITY TEST** or **BTC15M ECONOMICS
REJECTED**. Advancement requires a credible path to ≥$5/window with no
directional prediction, no θ tuning, no hypothetical liquidity rewards and no
unsupported linear scaling.

### EXP-010 — capacity experiment (NOT STARTED, required eventually)
q=50 is **9.42×** the largest clip ever filled live (5.31 contracts). Until a
capacity experiment demonstrates fills above ~5 contracts, every q>5 economic
figure is modeled. Design deferred; no orders, no deposit.

## DO NOT RETEST — additions

| idea | verdict | why |
|---|---|---|
| "BTC15m economics are rejected" (as of n=5) | **WITHDRAWN — premature** | residual CI spanned the target; n=7 moved q25 positive. TARGET_001. |
| Treating q25/q50 as demonstrated executable capacity | **FALSE** | max live fill is 5.31 contracts; q25 is 4.71×, q50 9.42×. CAPACITY_001. |
| Quoting matched gross without its convention band | **INCOMPLETE** | ±$0.80/window at q50 between average-cost and FIFO. MATCHED_001. |
| q=25 as a route to $5 | **effectively excluded** | needs residual frequency 0.0615, below the Wilson lower bound 0.1582. P0_RESIDUAL_001. |

### EXP-010 — bounded live capacity/economics probe (PROBE-CAP-02)
**Status: AUTHORIZED, NOT STARTED. $0.00 of $5.00 spent.**
Ledger: `analysis/pmus/PROBE_AUTHORIZATION_LEDGER.md`.

Budget PROBE-CAP-02 ($5.00 realized-loss cap) is separate from RESEARCH-LOSS-01
($0.49320, untouched). Hard stop −$5.00, never raised to finish a sequence.

Blocked on three findings, all established read-only before any arming:
1. No PMUS credentials in this container **or** on the `pmus-q1-executor` service
   (only Railway's 8 built-ins; no sealed variables), so "no orphan orders or
   positions" **could not be verified** — the mandatory precondition.
2. Arming is dual-key operator-only by design; the module says Claude never arms it.
3. **The existing executor is hard-capped at q=1**, $1.00 campaign risk, 1-contract
   residual, $1.00 daily realized-loss stop — constants, not knobs. It cannot
   express a q5→q10→q25 ladder, and its $1.00 stop conflicts with the $5.00
   envelope.

Also: the deployed executor builds from `feature/l5-transfer-backtest` and last
deployed 2026-09-06, predating `1ea8760e` (double-fee fix) and `3a92c583` (queue
model). Any live run should be rebuilt from current HEAD first.

Nothing was armed, no variable was set, no order was placed.

### EXP-011 — read-only live-architecture engineering
**Status: COMPLETE.** `analysis/pmus/EXP010_ENGINEERING_READONLY.md`.
Established the post-breach architecture, verified GuardedTransport covers
COMPLETE/complement by construction, confirmed both 09-16 faults are already
pinned by existing tests (77 passed), found the taker-completion primitive already
exists (`micro_complement_taker_*`, `post_only=False`, gated by
`taker_completion_ok`), and labelled the 5.31 fill as tennis evidence.

## DO NOT RETEST / DO NOT USE — additions

| item | verdict | why |
|---|---|---|
| `src/pmus_l5_ramp_ladder.py` owner-rung mechanism for LIVE authorization | **DO NOT USE FOR LIVE AUTHORIZATION** | fault (1) of the 2026-09-16 breach: persisted historical rung approvals were auto-loaded by a running campaign and silently replaced live limits ($0.05→$7.50, −$1.00→−$15.00). RAMP_LADDER_001. Live authority is the immutable mandate only. |
| PROBE-CAP-02 as an independent fresh $5.00 budget | **FALSE, CORRECTED** | one cumulative state is authoritative: −$4.50680000 of $5.00, **$0.49320 remaining**. BUDGET_001. Rung-A's $1.50 is NOT AUTHORIZED. |
| "matched economics are positive at every clip and fill model" | **FALSE at n=9** | θ gates completions, not passive acquisitions; a whipsaw produced a 1.3500 pair cost at 2030z. MATCHED_002. |
| Treating the 5.31 fill as BTC15m capacity evidence | **FALSE** | ATP tennis markets, 2026-09-07. Zero authenticated live BTC15m fills exist. |
