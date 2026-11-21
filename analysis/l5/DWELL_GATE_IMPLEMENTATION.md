# The dwell gate, wired — and why it is not enabled

**Verdict: implemented, tested, inert. The policy does NOT demonstrably improve
net expectancy out of sample, so per the standing instruction it is not
deployed. `L5_DEFENSIVE_DWELL` stays off. The shadow lock stays on.**

Starting point: `7e0aaacb`. Authorization: code, tests and shadow validation
only. No live submission, no capital change, no new service.

---

## 1. Why the gate was not wired

`l5_defensive_completion.decide` has accepted `stranded_since_t` since
`13a7e203`. Both live call sites — `l5_live_execution.py:4564` (AUTHORIZE) and
`:5364` (PRECHECK) — share one `_dkw` dict, and that dict never carried the
field. The cause was one level down: **nothing in `InventoryStore` recorded when
a market went off flat.** The gate was complete and unreachable — it had no
anchor, so there was no dwell to measure. That is the whole of the gap.

## 2. The anchor, persisted

`InventoryStore` now keeps `stranded_since: {market -> epoch}`, maintained in
`apply_fill` — the single writer of `positions` on the fill path, driven from
the venue's own `cum_qty`. The residual is read either side of that one write,
so the stamp is a consequence of an authoritative fill and never of a clock read
elsewhere.

* the fill that takes a market off flat sets the stamp;
* an **EXTEND does not restart it** — the dwell measures from when the
  imbalance opened, or an accumulating position would never mature;
* returning to flat clears it; crossing through flat re-stamps;
* it is in `state`, so `save()`/`_load()` carry it across restarts. A restart
  mid-window does not re-arm the dwell.

**Fail-closed throughout.** Inventory with no stamp — a store predating the
field, or a position adopted from the venue — returns `None`, which `decide`
reads as "use the arm window": the pre-existing behaviour byte for byte.
Nothing invents a stranding time it cannot source from a fill.
`reconcile_stranded()` runs inside `reconcile_from_venue` after the venue has
overwritten our positions: it drops stamps for markets the venue says are flat
and **reports** markets holding inventory with no stamp rather than giving them
one.

## 3. Two flags, deliberately

`L5_DEFENSIVE_COMPLETION` arms the exit. **`L5_DEFENSIVE_DWELL` arms its
timing**, and defaults off. With it off the executor passes
`stranded_since_t=None` and live timing is unchanged, so turning this policy on
is one explicit authorization rather than a side effect of unlocking
submission. Both phases read the same `_dkw`, so precheck and the authorizing
verdict cannot disagree about the anchor.

Every hard risk limit is untouched: the 0.96 maker ceiling, the 1.20 defensive
pair ceiling, `DEFENSIVE_LOSS_BUDGET_USD`, the depth test, the one-outstanding-
order rule, the cancel-confirm handshake, the loss halt.

## 4. The 0300z coordination defect, fixed

`maybe_ts_deadline` retired a resting leg **at** the binding terminal. The
defensive sequence needs `DEFENSIVE_MIN_EXECUTION_S` (15s) **before** that
terminal to cancel, re-read and submit. So a complement still resting at the
terminal was retired at the exact moment the exit's own window had already
shut — structurally impossible to act, which is 0300z.

`TSP.complement_retire_from_start(t_start, runway_s)` pulls the deadline to
`terminal − runway`, making the ordering structural. The runway is a parameter,
not an import, so neither module has to import the other to agree; an
unreadable runway is **zero**, never a later deadline. Retiring earlier is a
tightening, in the same direction `cancel_margin_s` may only widen.

**Honest limit: this fix alone does not rescue 0300z.** On that window ARM and
ARM+C behave identically (§5) because the failure was the evaluation cadence
missing a 45-second interval, not the retirement instant. The fix removes a
structural impossibility; it is a correctness change whose economic value this
replay cannot price, because the replay does not model the cancel-confirm
handshake that the runway exists to protect.

## 5. The two live windows, authenticated book, actual sizes

| window | HOLD | ARM | ARM+C | DWELL | actual |
|---|---|---|---|---|---|
| 0230z | −0.0819 | −0.0492 @T+544 | −0.0492 @T+544 | **+0.0057** @T+318 | −0.0468 |
| 0300z | −0.3580 | −0.3580 (never fired) | −0.3580 (never fired) | **−0.1224** @T+394 | −0.3580 |
| total | −0.4399 | −0.4072 | −0.4072 | **−0.1167** | −0.4048 |

**A correction to the previous report.** `STRANDED_LEG_RECOVERY.md` quoted the
campaign replaying to **+$0.0202**. That used an absolute T+180 offset, not the
30-second dwell that is actually implemented. With the shipped gate 0300z exits
at T+394 for **−0.1224**, not +0.0145: at T+361 a profitable completion at pair
**0.95** was reachable, the gate correctly declined to cross, and the price then
gapped ten cents in 33 seconds. The correct figure is **−$0.1167 against the
actual −$0.4048**, a +$0.2881 swing on n=2 — an illustration, not evidence.

## 6. Full net P&L per eligible window, with confidence intervals

`analysis/l5/residual_exit/dwell_validation.py`. A window is **eligible** when a
leg became unmatched and the profitable completion was not already reachable at
that moment — the states this policy exists to manage. Policies may act only on
ticks that exist, so an irregular cadence can miss a narrow interval exactly as
production did. Execution assumptions, all adverse to the policy: the cross
sells our side at the bid (price − 1 tick), PMUS fees `θ·p·(1−p)`, no rebate
assumed on any cross, settlement pays $1/$0.

### 15-minute windows (our venue's length) — 555 eligible, 388 train / 167 OOS

| | mean net/unit | 95% CI | median | p05 | exit% | **opp. cost** | <−0.30 |
|---|---|---|---|---|---|---|---|
| **OOS** HOLD | −0.06711 | [−0.124, −0.011] | −0.1974 | −0.5963 | 0% | 0 | 29.9% |
| **OOS** ARM | −0.06271 | [−0.112, −0.014] | −0.1505 | −0.5088 | 27.5% | +0.0348 | 21.0% |
| **OOS** ARM+C | −0.06117 | [−0.110, −0.012] | −0.1466 | −0.5088 | 28.1% | +0.0348 | 20.4% |
| **OOS** DWELL | **−0.04699** | [−0.065, −0.029] | −0.0323 | −0.1397 | 92.8% | **+0.1422** | **1.8%** |

Paired differences (positive = first better):

| | TRAIN | | OOS | |
|---|---|---|---|---|
| | mean [CI] | t | mean [CI] | t |
| DWELL − ARM | +0.03497 [+0.006,+0.064] | **+2.40** | +0.01572 [−0.030,+0.062] | +0.67 |
| DWELL − ARM+C | +0.03870 [+0.011,+0.067] | **+2.69** | +0.01418 [−0.031,+0.060] | +0.61 |
| DWELL − HOLD | +0.01474 [−0.021,+0.050] | +0.81 | +0.02012 [−0.034,+0.074] | +0.73 |
| ARM+C − ARM | −0.00372 [−0.009,+0.002] | −1.27 | +0.00153 [−0.001,+0.005] | +1.00 |
| ARM − HOLD | −0.02023 [−0.044,+0.004] | −1.67 | +0.00441 [−0.024,+0.033] | +0.30 |

**Significant in TRAIN, not out of sample.** DWELL beats the deployed gate by
+0.035/unit in training (CI excludes zero) and by +0.016/unit OOS with a CI
that straddles zero, over 167 eligible windows, better in 57.5% of them.

### 5-minute windows — 1,652 eligible: the dwell is *harmful*

DWELL − ARM is **−0.01474 [−0.026, −0.003], t = −2.50 (TRAIN, significant)** and
−0.01659 [−0.033, +0.000], t = −1.93 OOS. DWELL − HOLD is −0.02859, t = −4.03.
`DEFENSIVE_DWELL_S = 30` is calibrated for 15-minute windows only and must not
be applied elsewhere without its own measurement.

## 7. The exact economic reason it is not deployed

**(a) The out-of-sample effect is not distinguishable from zero.**
+0.01572/unit, 95% CI **[−0.030, +0.062]**, n=167. Per-window sd is 0.302, so
the standard error is 0.023 — detecting a true effect of this size at 80% power
needs **≈2,900 eligible windows**. We have 167. The training estimate
(+0.035, needing ≈527) was detectable; the OOS estimate is not, and the OOS
estimate is the one that counts.

**(b) The opportunity cost is 9× the gain.** DWELL forfeits **0.1422/unit** of
opportunity to net **+0.0157/unit** over the deployed gate — it exits 92.8% of
eligible windows and gives up every recovery those positions would have had.
**88.4% of DWELL's exits happen in windows where the profitable completion
became reachable later**, against 43.5% for the arm window. The policy is
mostly selling positions that were about to be completable at a profit. The two
effects nearly cancel; what survives is inside the noise.

**(c) No recovery policy makes stranded-leg management profitable.** Every
policy loses money per eligible window on 15-minute OOS: HOLD −0.0671, ARM
−0.0627, DWELL −0.0470 per unit. At our campaign's ~1.5 units that is **−$0.07
to −$0.10 per eligible window** whatever we choose. The choice only changes how
much is lost.

So the gate is a genuine improvement in the *distribution* — the tail below
−0.30/unit falls from 21.0% to 1.8% — bought with opportunity cost that leaves
the mean statistically unchanged. That is worth having for risk control; it is
not an expectancy improvement, and it is not deployed on this evidence.

## 8. Remaining blocker to live profitability

**Entry geometry, not exit management.** The eligible-window mean is negative
under every policy because the position is already EV-negative when it becomes
stranded. The chain is already measured elsewhere in this repo:

* the 0.96 pair ceiling forces `depth_ticks >= 2` at the 1-tick spread that
  holds **99.40%** of the time;
* at depth 2 the complement fills **14.4%** of the time (n=868) — depth 2 *is*
  the unfilled median distance;
* so ~86% of first legs strand, and `RESIDUAL_REPAIR_FINDINGS` already showed
  we never missed an available repair (0 of 359 states): **residual losses are
  set at entry, not by post-imbalance management.**

No exit policy can fix that. The next question worth asking is whether there is
any entry configuration on BTC15m with positive expectancy at our fill rate —
and the honest prior from §6 is that if the complement only fills 14.4% of the
time at the only depth the ceiling permits, there may not be one. That is a
question about whether to open at all, not about how to recover.

## 9. What was changed

| file | change |
|---|---|
| `src/l5_live_execution.py` | `stranded_since`/`_note_stranding`/`stranded_since()`/`reconcile_stranded()`; stamp maintained in `apply_fill`; reconciled in `reconcile_from_venue`; `_dkw` carries the anchor behind the flag |
| `src/l5_defensive_completion.py` | `DEFENSIVE_DWELL_ENV`, `dwell_enabled()` |
| `src/l5_two_sided_probe.py` | `complement_retire_from_start()` |
| `src/l5_pmus_live_runner.py` | `maybe_ts_deadline` uses it, emits `terminal` and `defensive_runway_s` |
| `analysis/l5/residual_exit/dwell_validation.py` | the validation above |
| 3 existing test files | boundary moved 720→705; two source-text guards restated as arithmetic invariants |

## 10. Shadow operation, verified

Deploy `d1cead39` (commit `d31a9fc5`) built and reached SUCCESS. The deploy
guard is `watchPatterns: ["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`, so the two
preceding commits were SKIPPED by design and the trigger file had to be touched
-- code changes never auto-deploy on this service.

```
13:21:00Z  L5_LIVE_SHADOW_LOCK {"locked":true,"env":"L5_SHADOW_ONLY",
                                "blocked":["submit"],"cancel_still_permitted":true}
13:24:44Z  heartbeat: shadow_lock true, armed false,
           submission_gate.blocked true, halt true, approval_state approval_stale
```

**The dwell gate is firing in the read-only shadow.** First cell, window
`cpc-btc-updown-15m-2026-10-23-1315z` at 13:24:03Z:

```
timing_gate "DWELL"      stranded_for_s 31.677, 34.819, 37.961, 41.089, 44.345
defensive_evals 5        defensive_would_act 5
```

Before this change every cell reported `defensive_evals: 0`, because
`_defensive_eval` returned early on `arm_window_open` and the shadow could not
see the decision it exists to measure. One window now yields five evaluations,
all five of which would act. That is the collection mechanism for the ~2,900
eligible windows §7(a) says the out-of-sample estimate needs.

`L5_DEFENSIVE_DWELL` is absent from the environment, so none of this changes a
live decision.

## 11. Test status, and a correction to `81b931f1`'s message

**The figure in commit `81b931f1` -- "2,133 passed" -- is not a characterisation
of this suite and should not be read as one.** It came from a `-k`-filtered
subset run with `-p no:randomly`. Run as the project normally runs it, the full
suite reports **58 failed, 12,292 passed, 27 skipped, 23 errors**.

Two errors in my own checking, recorded so neither is repeated:

* A HEAD-vs-baseline comparison that returned "0 failures on both sides" was
  **vacuous**. Without `--ignore` for the three `httpx`-missing gemini modules,
  pytest aborts at collection and runs NO tests; both sides were empty, so the
  diff meant nothing.
* `-rf` lists failures but not errors, so even a valid run would have
  undercounted by the 23.

What is established:

* This suite has substantial **pre-existing inter-test pollution**. The 58
  appear under `pytest-randomly`'s reordering and concentrate in modules with
  module-level state and env monkeypatching (`test_gemini_terms_gate` 17,
  `test_pmus_status_live_handoff` 14, `test_gemini_request_transport` 9,
  `test_spread_scanner` 9).
* Of the four cached failures in files this change touches, **three are stale
  node IDs** that no longer exist after the boundary tests were
  re-parametrized (`[587-False]`, `[719.0]`, `[720.0]`), and the fourth,
  `test_l5_runtime_config_verify.py::test_the_build_must_deploy_with_defensive_
  execution_disabled`, **passes in isolation** (29/29) and is therefore
  order-dependent.
* The suites covering every module this change touches pass: 42 in
  `test_l5_dwell_gate_wiring.py`, 59 in `test_l5_stranded_leg_recovery.py`,
  89 across the three updated boundary/wiring files.

A correctly scoped comparison -- both commits, deterministic ordering, the three
collection errors ignored, `-ra` so errors are listed -- is the only thing that
settles how much of the 58 is attributable here, and no attribution is claimed
until it has run.

## 12. Limitations

* OOS is 167 eligible 15-minute windows over three weeks of a regime in which
  L5's own per-fill edge had already fallen 45×.
* Path and outcome come from the Polymarket corpus (same instrument family);
  fees, ceilings and ticks are PMUS's. Polymarket fill economics, rebates and
  queue positions are not transferred.
* The replay does not model the cancel-confirm handshake, so §4's fix is not
  priced.
* Maker-completion fills are credited on the path reaching the level; the
  queue-aware variant is stricter and was reported in the previous pass.
* Two live windows are an illustration. n=2.
