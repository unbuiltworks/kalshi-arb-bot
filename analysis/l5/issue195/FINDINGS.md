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

### What the mandate widens — stated, because its own label understated it

A mandate is the only thing that exempts an order from the maker-only overlay,
and auditing `pair_cost_refusal` against my change showed it does **two more
things** for that one order:

```python
cap = (DEF.DEFENSIVE_PAIR_CEILING if _mand else TS.max_pair_cost())
...
if not _mand:
    _resc = self.completion_pair_rescue(...)   # three conditions
else:
    _resc = None                               # skipped
```

So a `TAKER_COMPLETION` mandate:

1. exempts the maker-only overlay (the intended, documented effect);
2. raises the pair ceiling from **0.96 to 1.20** — *above* the $1.00
   certain-loss line;
3. skips `completion_pair_rescue`'s three conditions.

**That is the intended shape for a rescue, not an accident.** 0.96 is an
*entry* ceiling for spread capture; a leg that would otherwise settle worthless
is the one case where paying above a dollar is the *smaller* loss. The
campaign's own completed pair cost **0.97** and its commit called it "a RESCUE,
not spread capture". And (3) is safe only because
`completion_taker_execute` enforces the two directional conditions itself —
`COMPLETION_WOULD_INCREASE_EXPOSURE`, `COMPLETION_WOULD_REVERSE_EXPOSURE`, both
against the store's *and* the venue's positions.

What bounds a dear rescue is therefore **not a pair-cost line** but the owner's
`L5_TAKER_MIN_WORST_IMPROVEMENT_USD` floor plus the $1.50 per-cross and $3.00
per-window outlay caps. My first version of the mandate's own label said it was
"not a change to the 0.96 pair ceiling" — true of every *other* order and
misleading about this one. Corrected in the code and pinned by five tests.

### Recommended values, chosen from the replay rather than from taste

**`L5_TAKER_MIN_WORST_IMPROVEMENT_USD = 0.25`**
**`L5_MAX_COMPLETION_EV_GIVEUP_USD = 0.05`**

Measured distributions over the recorded books:

| | 0300z (117 actionable) | 0315z (96 actionable) | OOS (387) |
|---|---|---|---|
| improvement min / median / max | 0.005 / **0.60** / 0.79 | 0.01 / **0.19** / 0.35 | 0.01 / **0.35** / 1.72 |
| EV give-up min / median / max | 0.0138 / **0.025** / 0.04 | 0.015 / **0.025** / 0.03 | — |

EV give-up is **essentially the taker fee** — the complement is bought near fair
value, so the only expected value surrendered is the commission.

Candidate pairs, applied to the two windows that actually lost money:

| floor / EV cap | 0300z | 0315z |
|---|---|---|
| 0.10 / 0.05 | 110 opportunities, first t+115 s @ 0.65 | 69, first t+124 s @ 0.35 |
| **0.25 / 0.05** | **94, first t+115 s @ 0.65** | **18, first t+124 s @ 0.35** |
| 0.25 / **0.02** | 86, first @ 0.49 | **0 — window lost entirely** |
| **0.50** / 0.05 | 81, first @ 0.65 | **0 — window lost entirely** |

Why these two numbers and not others:

* **The floor must be ≤ 0.35**, because 0315z's *best* opportunity all window
  was +$0.35. A floor of 0.50 discards that window completely.
* **The EV cap must be ≥ 0.05**, because 0315z's complement at own 0.63 carries
  a 2-cent fee and a cap of 0.02 refuses it — losing the window for the sake of
  a cent.
* **0.25 captures both windows at their first and best moment** (+$0.65 and
  +$0.35) while cutting the marginal tail hard: 0315z drops from 96 nominal
  opportunities to the 18 that are materially worth the scarce budget, and OOS
  from 387 to 244 (63 %).
* **The $2.00 lifetime budget affords only 3–5 attempts, ever**, charged at the
  worst case (~$0.37 at own 0.35, ~$0.65 at own 0.63). With so few shots the
  floor has to be selective, which rules the lower candidates out on
  budget-allocation grounds even though they admit more nominal opportunities.

**A trap worth naming: a fixed-dollar EV cap is an implicit size cap.** The fee
is linear in quantity — 1 contract ≈ $0.02, 2 ≈ $0.04, 5 ≈ $0.08, 10 ≈ $0.16 —
so $0.05 permits a 1–2 contract cross and silently refuses a 5-contract one. At
the validation sizes proposed that is the correct behaviour and a useful second
bound, but it must be raised deliberately if size ever grows, or it will read as
an unexplained refusal.

### Why worst-case improvement alone is a weak gate

A matched pair caps the worse outcome at `1 - q - p - fees` and holding leaves
`-q - fee`, so the improvement is about `1 - p` less the extra fee. Measured on
a 1 UP leg at own 0.05:

| complement own px | 0.90 | 0.95 | 0.98 | 0.99 |
|---|---|---|---|---|
| improvement | 0.09 | 0.04 | 0.01 | 0.00 |

So *almost any* complement below ~0.99 "improves the worse outcome". That makes
`worst_after > worst_before` a necessary and badly insufficient condition, and
it is why the improvement floor and the EV give-up budget carry the real weight.
Pinned by `test_a_completion_that_reduces_no_exposure_is_refused_at_execution`.

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

---

## A separate defect found while verifying the durable ledger: `finalized` is unbounded

Found while checking the owner's Phase A item "the watchdog and durable
ledger remain healthy", and worth recording because it was very nearly
mistaken for the cause of the residual rollover cost.

`ShadowLedger.state` has two halves. `pending` is bounded — `max_pending = 16`,
oldest first out, so a stuck window cannot starve new ones. **`finalized` has
no bound at all.** Every window ever scored is retained in full, cells
included, and `save()` rewrites the WHOLE file on every `park` and every
`finalize` — which are called in `two_sided_close` and `two_sided_settle`,
exactly the two stages carrying the 6-7 s.

So the obvious hypothesis was that the growing roll cost (6.400 s, 6.606 s,
7.437 s across windows 1-3) was this file growing, not the log line. Measured
rather than assumed:

```
one scored report serialises to 84,376 bytes

 N finalized |  file MB | save() s
           0 |     0.00 |    0.000
          50 |     4.22 |    0.052
          96 |     8.11 |    0.075
         200 |    16.89 |    0.136
         480 |    40.53 |    0.333
         960 |    81.05 |    0.684
        2000 |   168.86 |    1.623
```

**It is not the cause.** At a realistic 96 windows the save costs 0.075 s,
which is 90x short of the 7 s observed. The log-line explanation survives this
test; this one does not. Recorded because a plausible hypothesis that fails a
measurement is worth keeping, and because the next person to look at those two
stages will have the same idea.

**It is still a defect, and a slow-motion version of the one just fixed.**

* growth is ~8.1 MB a day at 96 windows a day, so the 50 GB volume is not at
  risk for years — this is NOT the 2026-09-26 observer volume failure
  repeating.
* the cost is linear in session age. At 2,000 windows, about three weeks, the
  save is 1.6 s and it runs TWICE a window. Far enough out to not be urgent,
  certain enough to not be ignorable.

**Deliberately NOT bundled into the Phase A2 deploy.** That deploy exists to
test one hypothesis — bound the log line, see whether roll falls below 2 s. Two
changes at once and a pass would not say which one worked. The retention fix
belongs in its own change, after A1 is settled either way.

Proposed shape when it is taken: bound `finalized` the way `pending` already
is, keeping the most recent N scored reports whole and reducing older ones to
their economics via the digest `bounded_for_log` already has. Scoring reads the
recent ones; the older ones only need their P&L.

---

## A1 root cause, CONFIRMED from the volume: `ShadowLedger.save` on a 270 MB file

Two hypotheses were tested and falsified before this one. Recording all three,
because the sequence is the finding.

**Falsified #1 — the log line.** The full `L5_TWO_SIDED_SHADOW` report arrived
TRUNCATED at the platform's 81,920-byte limit at exactly the 6 s stage
boundaries, while a 29,109-byte line cost 0.002 s. Bounding it was deployed
(`7e3d1e1b` at `a31fe8fd4`). The reduction worked — the live line carries
`cells_reduced {"cells":27,"full_bytes":155943,"budget_bytes":48000}` and every
cell is `_reduced`, revealing the reports were really ~156 KB and had been
losing about half to truncation all along. The latency did not move: 6.511 s
against a 6.882 s eight-window mean. **Correlation, not cause.** The bound is
kept anyway: the record now reaches the log intact.

**Falsified #2 — building and scoring the report.** Measured on the live
window's exact shape, 189 ticks / 1,735 prints / 27 cells: 0.030 s for all 189
`step()` calls, and `report()` + `score_report()` effectively zero.

**CONFIRMED #3 — the ledger write.** Instrumented (`84909719` at
`0d2449fbbe`) rather than assumed, because the previous unmeasured hypothesis
was wrong. The 18:00z roll, from the service's own output:

```
18:00:08  park      last_save_s 6.7426  bytes 270,438,265  finalized 2173
18:00:08  roll      6.979 s                       <- the save is 96.6% of it
18:00:52  finalize  last_save_s 6.5145  bytes 270,438,813  finalized 2174
18:00:44  settle    6.561 s                       <- the save is 99.3% of it
```

`finalized` is unbounded, every scored report is ~156 KB, and `save` rewrites
the **entire 270 MB file** on every `park` and every `finalize` — twice a
window, on a network-attached volume. The attribution is near-exact in both
stages, which is what distinguishes this from the two guesses before it.

An earlier note in this file dismissed this on a local-disk measurement at a
guessed 84 KB record. That dismissal was unsound on both counts and is
superseded here.

### A deadline, not just a defect

At 6.7426 s for 2,173 records the cost is **3.10 ms per record**, and
`L5_ROLL_BUDGET_S` is 10 s:

```
records at the 10 s budget   3,223
windows remaining            1,049   =>  ~10.9 days at 96 a day
```

So A2 — currently passing with zero over-budget lines — begins failing in
about eleven days if nothing changes. The retention fix has a date, and it is
gated on the backup procedure in LEDGER_BACKUP_AND_RECOVERY.md.

### Does it block trading, or only historical settlement?

**The work is historical. The placement is on the trading path.** Both answers
are true and the distinction is what matters.

`park` stores the window that just closed; `finalize` scores one the venue has
already settled. Neither is a new-market trading decision. But `roll` is tick
stage **1** of 15, so everything after it waits — including
`maybe_ts_completion` and the entry path before `maybe_preflight`.

Measured against the trading schedule (`WINDOW_S` 900, `ENTRY_CUTOFF_S` 600,
`COMPLETE_CUTOFF_S` 720, `TS_COMPLETION_EVERY_S` 10.0):

| | |
|---|---|
| first decision delayed by | 6.98 s = **1.16%** of the 600 s entry window |
| in completion intervals | **0.70** of one 10 s interval |
| `two_sided_settle` | stage 13, *after* completion — delays only the next tick, once a window |
| roll + settle combined | 13.5 s = **1.50%** of the window |

Compare the original defect: 93 s was 15.5% of entry time and **nine** missed
completion intervals, and the first completion decisions of 0300z and 0315z
landed at +98.5 s and +97.5 s. That is why it mattered and why this does not.

**What cannot be measured from the two recorded windows, and why.** The
earliest residual-book sample in 0300z is t+115 s and in 0315z t+124 s — there
is no early-window data at all, because the 93 s stall prevented sampling then.
Any statement about what the first seven seconds of those windows was worth
would be reading a selection effect created by the very defect being fixed. So
the impact above is argued from the schedule and the stage order, which are
measurable, and not from an improvement curve that does not exist.

**Conclusion: trading is responsive.** The sub-2-second target is worth
reaching and now has a known cause and a dated deadline, but it is not a
blocker for a bounded live validation.
