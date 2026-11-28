# Gemini BTC5m/BTC15m economic viability — state audit before any change

Written **2026-09-20**, before a line of challenger code exists. Everything in
section A was read from Railway and from the running process, not from a
handoff. Where the handoff and the runtime disagree, the runtime wins and the
disagreement is stated.

---

## A. Branch, service, serving SHA

| | |
|---|---|
| repo | `unbuiltworks/kalshi-arb-bot` |
| Gemini lineage branch | `claude/l5-execution-instrumentation-io6nzi`, HEAD **`539d3838`** |
| Railway project | `arb-ev-wallet-shadow` (`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`) |
| service | **`gemini-maker-shadow`** (`a15785aa-a33d-4184-9514-7b6c75e1e163`), env `production` (`f3642d27`), state **live** |
| **serving SHA** | **`a44de245cb051ab5b080c16233c031dfe3e965f0`**, deployment `a2c84494`, SUCCESS 2026-09-20T00:56:08Z |
| start command | `python3 -m src.gemini_execution.shadow_soak --cohorts btc5m,btc15m --policy GEMINI_RECYCLE --windows 0 --cycles 110 --interval 8.0 --state-root /state` |
| volume | `gemini-btc15m-state`, 50 GB at `/state`, `us-east4-eqdc4a` |

**The serving SHA is six commits BEHIND the branch HEAD, and that is not drift.**
`539d3838`, `64137f8a`, `64033a82`, `b8b26b4f`, `ffc8f3f4` and `767ac0a7` each
produced a deployment with status **SKIPPED**: they touch PMUS/sports paths and
the service's `watchPatterns` cover only the Gemini execution modules, the L5
surface and its artifacts. The Gemini control has therefore been genuinely
frozen at `a44de245` since 00:56Z while the sports work continued on the same
branch. This is the mechanism that has been preserving the control, and it is
worth keeping.

**Arms live in that process**, from `shadow_soak.py`:

- `CORRECTED_QUEUE_V1` — the control. `sp=None`, i.e. the frozen Model A
  exactly as `plan` defaults to it, under the `GEMINI_RECYCLE` adapter.
- `L5_ADAPTED_GEMINI_BTC15M_V2` — BTC15m only, band `[0, 900]`, corrects
  `L5_ADAPTED_GEMINI_CRYPTO`.
- `EXEC_A_PLUS_B` — both cohorts, `improve=True`, `preserve=True`.

**Runtime counters, read at 2026-09-20T11:54Z:**

```
BTC5m    windows 132  cycles 3603  readable 3088  refused 515
  CONTROL              first 0  COMPLETE 0  matched_pairs 1   residual 0
  EXEC_A_PLUS_B        first 1  COMPLETE 0  matched_pairs 21  zero_ahead 24  preserved 2

BTC15m   windows 43   cycles 3750  readable 3372  refused 378
  CONTROL              first 0  COMPLETE 0  matched_pairs 0   residual 0
  L5_ADAPTED_..._V2    first 0  COMPLETE 0  matched_pairs 2   zero_ahead 2
  EXEC_A_PLUS_B        first 0  COMPLETE 0  matched_pairs 1   zero_ahead 0

mutations_sent 0 on every arm, every cohort.   errors 0   restarts 0
```

**The 1.13 basis is real, prospective, and on the current build.** From
`GEMI-BTC15M2609201145-UP` at 11:44:33Z:

```
ARM_BAND  ... L5_ADAPTED_GEMINI_BTC15M_V2 tte=300-600 rested=54 COMP=1 pairs=1 basis=1.13 fee+=0
WINDOW_ARM ... L5_ADAPTED_GEMINI_BTC15M_V2 band=[0,900] first=1 complete=1 matched=0.01 basis=1.12
```

`fee+=0` — zero fee-positive pairs. The lane completed a pair at a basis of
1.13 against a $1.00 settlement, before fees. **That is a loss booked as a
success**, and it is the single clearest statement of why this session exists.
It is the preregistered negative baseline for everything below.

**Confirmed shadow.** `mutations_sent: 0`, `shadow_soak` never constructs a
session, and `arming.EXPECTED_AUTH_ID` is the constant
`OWNER-2026-09-17-GEMINI-CANARY-1`, already spent and `REJECTED` — this build
cannot arm by environment variable.

**Branch base note.** The designated branch `claude/gemini-btc-economic-viability-h3u7zl`
pointed at `main`. `main` and the L5 lineage are **unrelated histories** (git
refuses to merge them); `main` is a thin root carrying the CLAUDE.md skeleton
and CI workflows, and none of `src/gemini_execution`. The work branch is
therefore rebased onto `claude/l5-execution-instrumentation-io6nzi`, which is
where the code and the running control actually live. No unique work was
discarded — the branch had none.

---

## B. What already implements each requested part

| # | ask | status | where |
|---|---|---|---|
| 1 | entry economic justification | **features yes, admission rule no** | `preentry.py`, `habitat_selection.py`, `segments.py`, `large_prints.py`, `window_volume.py`, `bursts.py` |
| 2 | first fill = state transition | **partial** | `continuous_probe.Inventory` / `WindowGuard` / `recover()`; `GEMINI_RECYCLE` already suppresses EXTEND while residual exists |
| 3 | hard economic completion veto | **MISSING** | — |
| 4 | event-driven execution | **transport yes, lane no** | `src/l5_shadow/gemini_ws.py` exists and is unused by the lane |
| 5 | BTC5m ≠ BTC15m | **partial** | `CORRECTION_ARMS`, `L5_ADAPTED_GEMINI_BTC15M_V2` band `[0,900]` |
| 6 | `[0.20,0.80]` not a behavioural truth | **already clean** | `ELIGIBLE_LO/HI` appear only in `rebate_program.py` and `segment_report.py` |
| 7 | verify Gemini economics | **DONE, and well** | `REBATE_APPLICABILITY_1.md`, `REBATE_ECONOMICS_1.md`, `rebate_program.py` |
| 8 | opposite-fill hazard model | **dataset only, nothing fitted** | `hazard_report.py` — 22 features, `MIN_PER_CELL=30` |
| 9 | L5 fidelity as a separate axis | **exists and works** | `model_a_wanted` / `vetoed_by_adapter` per cycle per arm |
| 10 | scoreboard | **partial** | `L5_COHORT_HB`, `WINDOW_ARM`, `ARM_BAND` |
| 11 | safety | **structurally sound** | separate service, spent auth constant, no session |

Three of these deserve more than a row.

**#3 is the root cause of the 1.13 pairs.** `Rung.max_acceptable_price` in
`src/l5_surface/engine.py` reads as an economic ceiling and is not one — it is
computed geometrically, `px = bid - (offset + skew) * tick`, and **never
consults the held average cost of the leg it would be completing**. There is no
function anywhere that answers "given I hold 0.01 Up at 0.63, what is the most
I may pay for Down?". Fee arithmetic exists (`ab_arms._fee`, maker
`0.0175·q·p·(1−p)`) but is applied in reporting, not in admission. A pair at
1.13 is not a bug in the queue model; it is the absence of this test.

**#7 is done and carries a finding that must not be lost.** The published
programme is `0.30 × 0.07 × C × P × (1−P)`, capped at 5% of notional, eligible
band $0.20–0.80, **rounded DOWN**, paid daily at 17:00 ET. Seven confirmed
`AdminCredit` rows, purpose `"Predictions maker rebate"`, total **$0.51**, each
with its own venue `eid`; maker notional `$52.2573` cross-checked two
independent ways to within `$0.0031`; effective **≈97.6 bp**. The finding that
matters here: **at our probe size (0.01 contracts) the per-fill rebate rounds
DOWN to $0.00.** Rebates cannot rescue a negative pair at this size — by
arithmetic, not by estimate. The challenger's veto must therefore be computed
with rebate excluded, and the accrual reported beside it, never inside it.

**#6 needs no work, only protection.** The band is already confined to rebate
eligibility and reporting. The challenger must not introduce it as a behavioural
gate by the back door.

---

## C. Exact missing pieces

1. **Economic completion veto** — max acceptable complement price from actual
   held average cost, verified fee regime, configured minimum edge and expected
   slippage. Rebate accrued separately and never netted into the admission test.
2. **Entry admission** — an independent economic justification for a first
   fill, scored causally at the candidate moment. Today the one pre-entry read
   (`preentry.read_snapshot`) is deliberately recorded and then ignored.
3. **Opposite-fill hazard estimator** — `P(opposite economically acceptable
   maker fill within remaining TTE | state)`. The dataset exists; the estimator
   and its consumers do not.
4. **Paths C and D, and the action vocabulary.** Every rung in the surface is
   `side="BUY"` by construction, so bounded aggressive complement (C), bounded
   reduce/exit (D) and same-side round-trip (B) have no representation at all.
   `REDUCE` / `EXIT` must be added without corrupting the canonical L5
   `OPEN` / `EXTEND` / `COMPLETE` labels.
5. **Event-driven driver** — wire `gemini_ws` into the lane with debounce, and
   instrument decision-to-order and event-to-order latency. Neither metric
   exists today.
6. **Per-cohort policy split** — separate residual tolerance, post-fill
   toxicity horizon and entry→inventory-management transition for 5m vs 15m.
7. **Scoreboard extension** — economically acceptable complement opportunities,
   maker→maker completion rate, same-side exits, aggressive/rescue exits,
   median first-fill→flat, post-fill markout, capital-minutes, and net cents per
   capital-dollar-minute.

---

## D. Smallest clean diff

The `_Arm` seam in `shadow_soak.py` is already the right shape and was built for
exactly this: *"arms share the tape and nothing else"*, each with its own guard,
inventory and hypothetical book, and `_step_arms` contains a raising arm so a
research arm cannot take down the collector it rides on.

So the diff is:

- **New package** `src/gemini_execution/econ/` — new files only: `veto.py`,
  `entry.py`, `hazard.py`, `actions.py`, `scoreboard.py`, `event_driver.py`.
- **One new arm** `GEMINI_ECON_V1`, registered alongside `EXECUTION_ARMS`.
  The control's code path (`sp=None`, `GEMINI_RECYCLE`) is not touched.
- **`shadow_soak.py` changes only by registering that arm** — the control
  remains byte-for-byte.
- **Event-driven challenger as its own entrypoint**, so the fixed 8s cycle that
  the control depends on is never altered.
- **A new Railway service beside `gemini-maker-shadow`**, on this branch. The
  existing service, its branch and its `watchPatterns` are not modified — which
  is also what keeps its serving SHA pinned.

---

## E. The immutable control

`gemini-maker-shadow` (`a15785aa-…`) on `claude/l5-execution-instrumentation-io6nzi`
at **`a44de245`**, arms `CORRECTED_QUEUE_V1` / `L5_ADAPTED_GEMINI_BTC15M_V2` /
`EXEC_A_PLUS_B`.

Not redeployed, not restarted, `watchPatterns` not widened, and nothing pushed
to its branch from this session. The BTC15M_V2 timing work is preserved and is
compared against, not reinvented.

---

## F. Prospective experiment, preregistered

**Primary metric:** net cents per capital-dollar-minute, reported separately for
BTC5m and BTC15m, challenger against control.

**A sample-size honesty note, stated before any result exists.** The control
produced **1** matched pair in 132 BTC5m windows and BTC15M_V2 **2** in 43
BTC15m windows. Completion events are rare, so any criterion resting on matched
pairs needs days of tape. The primary *prospective* readout is therefore on the
**admission** side, which accrues per cycle rather than per pair:

> count of **economically acceptable** complement opportunities per window —
> price-observable, causal, computed against actual held cost — against the
> count of complement opportunities the control would have taken.

**SUCCESS** (all of):
- zero matched pairs formed at basis above the veto's own ceiling;
- challenger net pair economics ≥ 0 after actual fees, **rebate excluded**;
- net cents per capital-dollar-minute > 0 on the cohort being judged.

**FAILURE, reported as a result and not repaired away:**
- any matched pair formed above the ceiling — that is a **veto leak**, a defect,
  not a result;
- challenger first fills ≈ 0 across a stated number of windows — entry
  admission too strict, reported as a refutation;
- positive economics that survive only when accrued rebate is netted in — that
  is the disguise the veto exists to prevent, and it counts as failure.

**Negative baseline:** the control's own 1.12 / 1.13 basis pairs at `fee+=0`.

Thresholds are fixed before the sample and are not re-chosen from prospective
data.
