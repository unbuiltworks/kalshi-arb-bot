# Issue #195 — root causes, fixes, and what the replay proves

Nothing in this change arms anything, deploys anything, or enables live taker
execution. Every new switch is default-OFF. The deployed service is untouched.

---

## 1. The ~93-second window-rollover stall

### Root cause

`Runner.roll` is the first call in every tick and is synchronous, so until it
returns the window cannot evaluate an opening or a completion. On 2026-11-06
it returned after ~93 seconds on every window for hours:

```
L5_LIVE_TICK_STAGES {"total_s":93.651,"slowest":"roll","slowest_s":93.653}
L5_LIVE_TICK_STAGES {"total_s":93.244,"slowest":"roll","slowest_s":93.242}
L5_LIVE_TICK_STAGES {"total_s":92.979,"slowest":"roll","slowest_s":92.978}
```

PR #196 added per-stage timing but was never deployed, so the blocking call was
located instead by bisecting `roll`'s own emitted lines against their platform
timestamps. For the 12:00z roll:

| platform time | line | meaning |
|---|---|---|
| 12:00:02.031 | `L5_MODEL_WINDOW` | end of `model_close` |
| 12:00:02.200 | `L5_SHADOW_WINDOW_CLOSE` | end of `shadow_close` |
| 12:00:02.388 | `L5_CAMPAIGN_ROLLING` | end of `emit_window_accounting` |
| *96.7 s with nothing emitted* | | |
| 12:01:39.066 | `L5_COST_BASIS_AUDIT` | `{"activity_walk":{"pages":133}}` |
| 12:01:39.067 | `L5_LIVE_GATES` | the last line `roll` writes |

**The blocking operation was `L5_COST_BASIS_AUDIT`** — a read-only diagnostic
(`"writes":"NONE"`) that walks the venue's whole account-activity feed.
133 paginated GETs through the process's one shared limiter at
`PMUS_RPS = 1.5` is `133 / 1.5 = 88.7 s` of sleeping, plus round trips.

Confirmed independently from the platform's DNS stream: `api.polymarket.us`
resolved every **0.666 s** — exactly `1 / 1.5` — continuously across the gap.

Not a cancellation, not a settlement, not a reconciliation, not an order.

### A second defect found in the same place

The audit's own comment says it "reads the account activity feed ONCE"; the
apply beneath it says "Boot only, so it cannot run twice in a session". `roll`
fires every 15 minutes, so **both ran 96 times a day** — about 12,800 venue
reads a day for a diagnostic that writes nothing. Three other optional
read-only blocks sat on the same critical path: `L5_DEPTH_COMPARISON`,
`L5_TAPE_RECOVERY` and `L5_ESTABLISH_BASELINE`.

### Fix

`src/l5_roll_diagnostics.py` (new) — a scheduler, and nothing else. It holds
no transport and no store and cannot submit, cancel, arm or price anything.

* `roll` **enqueues** the four diagnostics instead of running them.
* The queue latches on the job NAME, so each runs **exactly once per process** —
  the cadence all four documented and none of them had.
* `tick` drains at most **one** job, with a **5 s wall-clock budget**, only
  **after** every trading stage (`maybe_ts_completion`, `maybe_book_completion`,
  `maybe_controller_open`, `maybe_completion_requote`) and only outside the
  window's entry-critical phase (default: ≥240 s in, no resting order, no held
  inventory). Deferring is not skipping: a job stays queued until it reports done.
* `CBA.walk_activities` is now **resumable** (`cursor`, `rows`, `pages_done`,
  `next_cursor`) and `CBA.audit` accepts a `walk` it did not make. The same
  pages, same order, same accumulated list — what changes is only how much wall
  clock one call may spend. 12 pages per step keeps any single call under ~8 s.
* `roll` measures itself and emits `L5_LIVE_ROLL_OVER_BUDGET` above
  `L5_ROLL_BUDGET_S` (default 10 s). **Reported, never enforced**: the closes
  and the accounting are not interruptible work, and a timeout that cut one
  short would trade a latency problem for an accounting one.

**Unchanged and undeferred:** `model_close`, `close_window`, `shadow_close`,
`two_sided_close`, `emit_window_accounting`, the transport and executor
rebuild, the cancel-ownership oracle, the gates, settlement, reconciliation
and exact-order cancellation. `test_roll_still_does_every_thing_that_is_not_a_diagnostic`
pins each one.

### A consequence worth stating rather than discovering

`entry_critical` is true while **any** order is resting or **any** inventory is
held in the live market. So during an armed campaign that holds inventory
continuously, the deferred diagnostics may not run at all for the duration —
they are deprioritised indefinitely, not merely delayed.

That is the correct ordering (a read-only diagnostic must never outrank a
completion decision) and it is the safe direction, but it does mean
`L5_COST_BASIS_AUDIT` will not emit during an active campaign the way it used
to. In the current flat, disarmed state it runs normally. Anyone looking for
that record mid-campaign should expect `roll_diag:deferred:ENTRY_CRITICAL` in
`stats` instead, which is counted precisely so a queue that never drains is
visible rather than silent.

---

## 2. `maybe_ts_completion` reached `WOULD_SUBMIT` and submitted nothing

### Root cause

```python
if dec.get("ok") and authorized and t.armed and pol.get("authorized"):
    rec["crossed"] = "WOULD_SUBMIT"
    rec["submission_route"] = "deliberately not wired"
```

It reached that string **43 times in 0300z** and **22 times in 0315z**, every
one with `ok: true, cross: true`, while both residuals settled for −$1.00.

### Fix

`Runner.submit_ts_completion` → the **existing** `executor.handle(None, book,
controller=...)` re-entry that `maybe_book_completion` already uses. No new
transport, no second order path, no new authorization.

Inside `handle`, a new `FaithfulExecutor.completion_taker_execute` mirrors the
defensive unwind's order of operations, **because that order is the safety
property**:

```
switches → uncertain-ledger check → one-mandate-per-window → durable claim
→ cancel exactly the named orders → PROVE by the venue's own re-read
→ refuse if anything filled during the cancel → re-read OUR inventory
→ clamp size to min(authorized, confirmed deficit) → refuse if it would
increase or reverse exposure → cross-check the VENUE's own position
→ charge the budget (fsynced) → write the claim → issue a mandate naming
that one order
```

Every failure returns `ok: False` and leaves the position exactly as it was.

It **shares** the defensive path's single mandate slot, single
`defensive_charge` budget, and the single maker-only exemption point — so
"one crossing order per market per window" stays **one** invariant rather than
two that could each be satisfied while two orders went out. The mandate carries
an `origin`, and `defensive_mandate_covers` gates a `TAKER_COMPLETION` mandate
on the taker policy's own switches and a defensive one on `DEF.enabled()`.
An unrecognised origin reads as defensive — the stricter reading.

### Two costs of the claim, accepted deliberately

The durable claim is written **before** the wire and is **never** released by a
retry, because issue #195 requires exactly that: *"Never duplicate a hedge
after partial fill, retry, uncertain ACK or restart."* Two consequences follow
and neither is a surprise to be discovered later:

1. **A burned claim is not re-earned.** If `handle`'s remaining gates refuse
   after `completion_taker_execute` has claimed — cash, the unmatched cap, the
   cumulative-loss ceiling, the worst-case deficit — then that (market, side,
   window) can no longer be completed even though **no order ever reached the
   venue**. The alternative is releasing the claim on a refusal we believe was
   pre-wire, and `_bc_capture` cannot reliably tell a pre-wire refusal from an
   uncertain acknowledgement. Fail-closed is the required direction.

2. **Cancelled and not replaced.** The resting complement is cancelled and
   venue-confirmed gone *before* the verdict that could submit. If anything
   after that refuses, the window ends with no complement resting. This is
   byte-for-byte the shape `defensive_execute` already has and its own
   docstring accepts: *"the complement either still rests (cancel refused) or
   is gone and nothing replaces it, which is the state the window was heading
   for anyway."*

What bounds the second cost is the **maker-patience gate**, which runs before a
proposal exists: `TCP.authorize` refuses `MAKER_NOT_ATTEMPTED` unless a maker
quote was actually posted on the completing side, and `MAKER_PATIENCE_NOT_SPENT`
until it has rested `L5_TAKER_MAKER_PATIENCE_S` (default 90 s) unfilled. So a
freshly-posted hedge cannot be cancelled out from under itself.

### The shared loss budget is tested, not merely charged

Found by reviewing this method against the one it mirrors.
`InventoryStore.defensive_charge` **only accumulates** — it refuses nothing.
The defensive path is bounded because `DEF.decide` tests
`spent + exit_downside > loss_budget_usd` *before* `defensive_execute` charges.
`completion_taker_execute` does not call `DEF.decide`, so charging without
testing left the campaign-wide **$2.00** budget unenforced on the completion
route — and worse, a completion could quietly exhaust the budget the defensive
unwind depends on, since the two share it on purpose.

The per-cross (**$1.50**) and per-window (**$3.00**) outlay caps in
`l5_taker_completion_policy` bounded any single attempt either way, so this was
never unbounded; it was unbounded **across the campaign**, which is exactly the
dimension that budget exists to bound. Now tested against the same constant,
read from the same place, before the charge — and an unreadable budget reads as
exhausted, which `defensive_spent` already delivers.

### The switches, all default-OFF

| env | what it authorizes |
|---|---|
| `L5_TAKER_COMPLETION_POLICY=YES` | the bounded route exists |
| `L5_TS_COMPLETION_CROSS=YES` | live taker execution |
| `L5_TAKER_LOSS_REDUCTION=YES` | crossing where the best outcome is still a loss |
| `L5_TAKER_MIN_WORST_IMPROVEMENT_USD` | the improvement floor (unset = refuse) |

Only a literal `YES` enables anything; `1`, `true`, `on` do not.

---

## 3. One inventory-aware decision process

`src/l5_inventory_decision.py` (new, pure, no I/O). Three separate things used
to decide what to do about an unmatched leg, each with its own economics and
none aware of the others: `complement_order` (maker, 0.96 ceiling),
`completion_decision` (taker, worst-outcome) and `DEF.decide` (unwind, 1.20
ceiling, separate budget and clock).

Four alternatives, priced on one arithmetic: `HOLD`, `MAKER_COMPLETE`,
`TAKER_COMPLETE`, `TAKER_PARTIAL`.

### "Reduce the original exposure" is not a fifth alternative on this venue

The book is quoted in YES terms and a BUY of DOWN is wired as a SELL of YES:

```
sell  1 UP at b          ->  net  b - pu
buy   1 DOWN at (1 - b)  ->  net  1 - pu - (1 - b)  =  b - pu
```

The complement buy **is** the exposure reduction.
`test_reducing_and_completing_are_the_same_order_on_this_venue` asserts the
identity rather than leaving it as a claim. Selling a matched pair back is not
priced either: a matched pair redeems $1.00 either way, so unwinding it pays
the spread to give that up and can never rank first.

### Why it is NOT ranked by expected value

The first version was, and it chose a maker quote with a **0.24 %** fill
probability over a certain completion. The reason is structural: this programme
has no view on outcomes, so the expected value of holding an unmatched leg is
about zero and the expected value of completing it is that zero *minus the
fee*. **An EV ranking prefers carrying every unmatched leg, always** — it is a
machine for accumulating directional risk.

The ranking is therefore:

1. available (inputs readable, book fresh, deployed cash and worst-case
   ceilings satisfied — read here, never computed);
2. **strictly** better worst case if it fills;
3. EV give-up against HOLD within `max_ev_giveup_usd` — the completion-loss
   budget, which is what stops worst-case reduction being bought at any price;
4. highest **expected worst case**: `p_fill·worst_after + (1−p_fill)·worst_before`;
5. ties → less cash, then higher fill probability.

`test_a_tiny_worst_case_trim_that_costs_the_whole_upside_is_refused` shows why
step 3 is not optional: a leg bought at 0.10 has a worse outcome of only
−$0.11, and completing at 0.97 trims that by two cents while giving up $0.655
of expected value.

### Adverse selection, with the sign derived

We hold UP and want DOWN; buying DOWN as a maker means offering YES, which
fills when YES **falls** — when UP is becoming less likely. A **full** fill is
outcome-independent so adverse selection costs exactly nothing; the cost lands
on a **partial** fill, and a **non**-fill is favourable. Modelled as one shift
conditional on a fill, with the no-fill branch moved by exactly the amount
that preserves the unconditional prior:

```
p_fill * p_given_fill + (1 - p_fill) * p_given_no_fill == p_prior
```

The shift is **reduced** when infeasible, never clamped after the fact — the
first version clamped and silently invented expected value;
`test_the_adverse_shift_preserves_the_unconditional_prior` caught it at
`p_fill = 0.9`. The shift is an **assumption** and is labelled as one.

### Nothing is invented

Maker fill probability needs a **measured** base rate; `None` makes
`MAKER_COMPLETE` unavailable with `MAKER_FILL_RATE_UNKNOWN`. The settlement
probability is the venue's **mid and nothing else**; with no mid the
expectation is `None` and a configured EV budget *refuses* rather than being
waived.

---

## 4. The causal replay — `python3 -m src.l5_issue195_replay`

Read-only; opens three committed archives and nothing else. It replays
**decisions, not executions**, and claims no fill anywhere. Every figure is
priced against **haircut** depth (25 % of displayed).

### Were the residuals preventable from the actual books? **Yes.**

| | 0300z | 0315z |
|---|---|---|
| held | 2 UP @ 0.62, 1 DOWN @ 0.35 | 1 UP @ 0.38 |
| recorded books sampled | 133 | 123 |
| books offering an executable, economic completion | **117** | **96** |
| first actionable | +115.3 s, own 0.3300 | +124.4 s, own 0.6300 |
| worst-case improvement there | **+$0.6500** | **+$0.3500** |
| haircut depth ÷ need | **1452×** | **1194×** |
| actual settled | −$0.59 | −$0.38 |
| counterfactual settled | **+$0.06** | **−$0.03** |
| delta | **+$0.65** | **+$0.35** |

Campaign: actual **−$0.97** → counterfactual **+$0.03**, delta **+$1.00**.
(0245z traded nothing and settled 0.00.)

Depth was never the problem: after discarding three quarters of the displayed
size the book still held more than a thousand times the single contract either
window needed.

### Wiring the route is necessary and **not sufficient**

`DEFAULT_MARGIN_FLOOR_USD` is **+$0.25** and was calibrated on the two
*profitable* completions of the 2026-10-30 campaign (2130z +0.2890, 2145z
+0.2600). A rescue cannot satisfy it **ever**: the first leg's cost is sunk, so
the best achievable worse outcome is still negative (−$0.02 and −$0.05 here).

**With the route wired and the deployed configuration in force, the policy
still refuses both windows** (`WORST_OUTCOME_NOT_POSITIVE`). That is what the
separately-authorized, default-off loss-reduction mode is for, and the replay
prints both verdicts side by side rather than only the flattering one.

### What the replay cannot see

`sample_residual_book` runs after `roll` in the same tick, so **the stall
blocked the sampler too**: the earliest recorded book in either window is
~+115 s. Nothing here can say what the book looked like during the 93 s the
roll held. What *is* measured is that the 0315z opportunity decayed
monotonically across the observable part of the window, from $0.35 at the first
sample to $0.04 at the last — so the time the diagnostic spent was not free
time, and the unobserved first 98 s is **a gap in the evidence rather than a
point in the fix's favour**.

### Out-of-sample — `python3 -m src.l5_issue195_replay oos`

1130z and 1145z, hours later, modelled inventory over live books. No parameter
was chosen with reference to them. **No realized P&L is computed**: the
inventory is modelled, so there is no venue position to compare against.

```
samples 488   chose completion 387   chose HOLD 101
improvement  min $0.0100   median $0.3500   max $1.7200
survivors by improvement floor:
  $0.05 → 346   $0.10 → 317   $0.25 → 244   $0.50 → 136   $1.00 → 47
discriminates: true
```

`discriminates: false` would mean the ranking fires everywhere or nowhere,
which would make it evidence of nothing. A ranking that *surfaces* a one-cent
rescue is working correctly; *acting* on one is not, and the floor is the
owner's choice — the breakdown is what makes it a choice rather than an
implication.

---

## 5. Tests

New: 36 + 49 + 59 + 33 = **177** tests across
`test_l5_roll_stall_issue195.py`, `test_l5_inventory_decision.py`,
`test_l5_taker_completion_live_path.py`, `test_l5_issue195_replay.py`,
plus one added to `test_l5_cost_basis_audit.py`.

Covered, as issue #195 item 5 requires: the 0300z 2 UP/1 DOWN and 0315z
1 UP/0 DOWN cases from the live records; partial fills; a fill booked during
the cancel; stale and gapped books; order slots and resting hedges; uncertain
ACKs; restart via the durable claim; price and depth movement; the fee, margin
and worst-case caps; venue idempotency; the shared mandate and budget; and the
roll budget.

Four pre-existing source-layout tests were rewritten, each **at least as
strong** as before, because the structure they pinned moved for a reason:

| test | was | now also asserts |
|---|---|---|
| `test_the_runner_hook_is_env_gated_and_runs_after_the_transport_exists` | byte offset of the env read | `roll` calls no walk; the job reads the live transport; nothing is computed from an unfinished walk |
| `test_the_runner_baseline_caller_is_gated_and_reads_before_striking` | byte offset | `roll` never strikes the baseline; the job reads the live transport |
| `test_the_runner_uses_the_proven_parser_not_guessed_key_names` | a 2600-byte window of the file | the parsed source of the step itself |
| `test_every_pre_preflight_call_has_a_mark` | fourteen stages | fifteen, with `roll_diagnostics` declared |

---

## 6. What still requires the owner

1. **Deploying any of this.** The money-capable service is pinned and these
   commits are not deployed.
2. **`L5_TAKER_COMPLETION_POLICY=YES` + `L5_TS_COMPLETION_CROSS=YES`** — live
   taker execution. Without both, `completion_taker_execute` refuses before it
   reads any state.
3. **`L5_TAKER_LOSS_REDUCTION=YES` + `L5_TAKER_MIN_WORST_IMPROVEMENT_USD=<x>`** —
   without these the replay shows the policy refusing both rescues even with
   the route wired. The out-of-sample survivor table above is the evidence for
   choosing `<x>`.
4. **`L5_MAX_COMPLETION_EV_GIVEUP_USD`** — the completion-loss budget in
   expected-value terms. Unset means the constraint is not applied.
5. **A fresh campaign approval.** `approval_state` is `approval_stale` and
   `windows_remaining` is `null`.

### One observation that is an owner decision, not a code change

**Read from the live heartbeat at 2026-11-06T13:21:52Z, not from a commit
message.** The two previous commit records disagreed about the halt — the
install commit says `halt_local_env true`, the campaign-close commit says
`L5_LIVE_HALT IS STILL EMPTY` — and `list-variables` redacts values, so it was
verified from `L5_LIVE_HB` instead. The campaign-close commit is the correct
one:

```
submission_gate  blocked true, reason "NOT_ARMED"
halt_local_env   false          <- L5_LIVE_HALT is NOT asserted
shadow_lock      false          <- L5_SHADOW_ONLY is NOT on
halt_latch       "CLEAR"        <- the independent latch is PRESENT, halt false
owner_arm_token  true           <- the owner's arm token IS in place
armed            false
approval_state   "approval_stale"
windows_remaining null
http_429         0
```

So the **only** thing blocking the wire is `NOT_ARMED`, and arming is blocked
by the stale approval and the empty window ledger alone. The env halt, the
shadow lock and the watchdog latch are all currently **not** asserting, and the
arm token is already present. The independent watchdog is running and
reporting (`kind: PRESENT`), which is the part that is working as designed.

That is a thinner margin than the layering is capable of. Asserting
`L5_LIVE_HALT`, or setting `L5_SHADOW_ONLY`, each needs a variable write and a
redeploy of a money-capable service, so both are left as owner actions and
neither was done here.
