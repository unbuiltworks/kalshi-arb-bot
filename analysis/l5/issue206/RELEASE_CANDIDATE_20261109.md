# L5 release candidate — `integration/l5-safe-release-candidate-20261109`

**Mode: offline. Executor disarmed. Issue #206 deployment freeze intact and
verified against the running service.** Nothing in this document was
deployed, merged, armed or re-configured. No Railway variable changed, no
risk limit touched, no order placed.

---

## 1. What the candidate is

| | |
|---|---|
| branch | `integration/l5-safe-release-candidate-20261109` |
| owner's integration commit | `8b1d3db7402c976e545af4294ca03433a31fdb81` |
| candidate HEAD | see §2 |
| deployed SHA (untouched) | `6576964e667e5b40ae7e969c3a4b59180dbb2be0`, deployment `c300a6c4` |

The append-only ledger of #210 was already present: `src/l5_two_sided_shadow.py`
is blob `ebc18c8220d159321308b31dcaf8a6ffd25348e9` and
`tests/test_l5_shadow_ledger_retention.py` is blob
`8b30aea24b104e18cd713411aa3f15b6256632e8` on both branches. **No cherry-pick
was needed and none was made.** The one commit of PR #208 that the owner's
integration branch did not yet carry — the adaptive completion depth — was
brought across.

---

## 2. Commits added on top of `8b1d3db74`

1. **adaptive completion depth** (cherry-picked from PR #208). Default **OFF**;
   with the flag off the candidate list is `[deployed depth]`, zero extra
   decisions, path byte-identical.
2. **frozen-plan re-pricing + the latency log line** (new here).

Changed files: `src/l5_live_execution.py`, `src/l5_pmus_live_runner.py`,
`src/l5_two_sided_probe.py`, `src/l5_book_completion.py`,
`analysis/l5/issue208/variant_comparison.py`,
`analysis/l5/issue206/unmatched_leg_scorer.py` (new),
`tests/test_l5_release_candidate_lifecycle.py` (new),
`tests/test_l5_frozen_plan_reprice.py` (new),
`tests/test_l5_adaptive_completion_depth.py` (new).

---

## 3. The deployed configuration this was tested against

Read from the service's own `L5_EFFECTIVE_RUNTIME_CONFIG`, not from variable
text. Every row below is what the money path's own accessor returns.

| knob | effective |
|---|---|
| `max_live_orders` | **2** |
| `aggregate_exposure_usd` | **1.00** |
| `max_usd_per_market` | 20 |
| `max_cash_deployed_usd` | 12, `NO_RECYCLING` |
| `max_worst_case_deficit_usd` | 1.00, check ACTIVE |
| `daily_realized_loss_halt_usd` | 5 |
| `ts_opening_leg_max_contracts` | **1** |
| `ts_round_allocations` | **1** |
| `ts_entry_depth_ticks` | 2 |
| `book_completion_depth_ticks` | **0** (the touch) |
| `ts_min / max_first_leg_own_price` | **0.30 / 0.60** |
| `maker_pair_ceiling` | 0.96 |
| `dual_quote_enabled_now` | true, `dual_max_placements_per_pass` 2 |
| `dual_replenish_enabled_now` | **false** |
| `taker_crossing_reachable` | **false** |
| `shadow_only` | **true** |
| `armed` | **false** |

Two of these decided most of what follows.

**`ts_opening_leg_max_contracts` = 1 makes the maker rebate unearnable.** The
config verifier says so in its own words: *"clip 1 -> worst cross outlay 0.99
vs cap 3: COMPLETABLE; rebate floor at p=0.50 is 1.6 contracts, confirmed
floor 2.52 (earns $0.00 below it)"*. Every rebate column in every table below
is therefore reported and **never added**. At the deployed size the confirmed
rebate is $0.00.

**`max_live_orders` = 2, not 1.** `tests/test_l5_e2e_window.py` pins the
former cap of 1 and its docstring concludes "production runs a SEQUENTIAL
strategy ... the simultaneous two-sided quote the module was written for
never happens". That is no longer true, and nothing exercised the two-order
path through the real executor. §4 does.

---

## 4. Order-lifecycle evidence — the real execution path

`tests/test_l5_release_candidate_lifecycle.py` drives the real
`FaithfulExecutor.handle` and the real unbound `Runner.maybe_*` against one
clock, one store and one transport, at the caps in §3. Fills are scripted per
client order id. No network, no threads, no order.

| # | behaviour | verdict |
|---|---|---|
| 1 | simultaneous UP and DOWN submitted | **PROVEN** — both rest, `still_fillable_order_count() == 2`; a third is refused `MAX_LIVE_ORDERS`; both post-only maker |
| 2 | independent acknowledgement and fill | **PROVEN** — distinct venue ids and client order ids; one leg fills while the other keeps resting; both filling books a matched pair with `up == dn` |
| 3 | partial first leg → inventory-aware second leg | **PROVEN** — complement sized to the **fill**, never the intent; the partial row keeps its slot so the cap still binds |
| 4 | unfilled quotes repriced and replaced safely | **PROVEN** — each cancelled at its own TTL by exact venue id; the replacement carries the **current** price (this is the defect fixed in §5); a filled leg is never cancelled or replaced |
| 5 | replenishment after a completed pair | **PARTIAL** — a completed pair frees both slots and the next leg is never swallowed as `NOTHING_TO_MATCH`; but `dual_replenish_enabled_now` is **false** on the deployed service, so replenishment is off in production |
| 6 | cancel/fill races create no duplicate or unaccounted exposure | **PROVEN** — a fill landing mid-cancel is booked once and not re-booked; no duplicate client order id across entry, both legs, TTL and replacement; aggregate worst case counts held inventory **and** open orders against the $1.00 ceiling |
| 7 | rollover no longer blocks ~93 s | **PROVEN IN PRODUCTION, not here** — `roll_elapsed_s_max` = **0.487 s** on the running service |
| 8 | ledger migration, persistence, restart, settlement | **PROVEN** — two live orders survive a restart and neither is re-entered; a restart after one fill resumes the pair rather than opening a round. V9 closed on `c300a6c4`: `FORMAT_2_NATIVE`, `READ_FROM_INDEX`, `LOG_AGREES_WITH_MANIFEST`, `load_s` 0.0272 |
| 9 | watchdog, exact-order cancellation, loss halt, exposure limits | **PROVEN** — cancellation by exact id at two live orders; cancellation still works after the arm switch flips under us; `day_realized` room $5.00, `account_exposure.ok` true |
| 10 | the authorization chain cannot submit while frozen | **PROVEN** — `shadow_only()` returns True for unset and for every value but `"0"`; `submission_gate` reports `blocked: true`, `reason: SHADOW_ONLY_LOCK`; an unreadable gate reads BLOCKED and never OPEN |

**A correction of my own first draft.** Item 10 was first written as
`win2(tmp_path, armed=False)` asserting the executor sent nothing — and it
sent three orders. The e2e harness's `Venue.armed` is carried but its
`submit` does not consult it, so that flag proves nothing. The real gate is
`Runner.submission_gate`, upstream of `handle`, and that is where item 10 is
now asserted.

---

## 5. The funnel, and the defect it exposed

### 5a. Live counters, read with their ages

From the `L5_LIVE_HB` of `c300a6c4` at 05:29:19Z. Ages are what make counters
interpretable; a large counter with a four-hour age is history, not behaviour.

| counter | value | age |
|---|--:|--:|
| `ingest:rows` | 645,800 | 0.6 s |
| `ingest:admitted` | 587 | 82.9 s |
| `controller_open:NOT_AUTHORIZED_MARKET` | 2,976 | **0.9 s** |
| `controller_open:ENTERED` | 220 | 9,973.7 s |
| `controller_open:CAMPAIGN_BUDGET_EXHAUSTED` | 133 | 10,282.3 s |
| `book_completion:NO_INVENTORY` | 1,602 | 0.9 s |
| `book_completion:DECIDE_COOLDOWN` | 1,582 | 4.3 s |
| `shadow_completion:NO_MODELLED_DEFICIT` | 1,154 | 4.3 s |
| `roll_elapsed_s_max` | **0.487** | 855.9 s |

The dominant live refusal is `NOT_AUTHORIZED_MARKET` at age 0.9 s — the
campaign approval expired, so every tick correctly refuses. That is the
freeze working, not a defect. The counters that describe *trading* are hours
stale, which is why the economics below come from replay rather than from
production.

### 5b. The defect: a frozen plan the book has walked away from

Found on the real execution path. With both legs of a round cancelled at zero
fill and the bid then moving 0.42 → 0.46, `probe_plan` prices UP at **0.44**
from the current mirror while the engine sends UP at **0.40** — the frozen
price, four ticks behind, resting six ticks off a 0.46 touch where nothing
fills.

Neither existing guard could see it:

* `plan_executable`'s **crossing** test fires when a leg becomes too
  *aggressive*. A leg left behind is perfectly placeable, never crosses, and
  is never reported.
* its **drift** test (`PRICE_LEFT_BEHIND`) only fires past
  `STALE_DRIFT_TICKS` = 6. Four is under it.
* and above six the only remedy the caller has is **retiring the round** —
  which at the deployed `ts_round_allocations` of 1 makes `probe_round`
  return None and ends the window on `ACCUMULATION_EXHAUSTED`.

So the two guards between them repair a plan that moved *against* us and
leave a plan that moved *in our favour* resting where it cannot fill. That
asymmetry is why it survived: every recorded failure of a frozen plan was a
crossing one.

**The repair.** `TS.plan_drift_ticks` measures the worst unspent leg against
the price `quote_own_price` would emit now; the executor re-prices the stored
plan **in place** when all three of these hold, each fail-closed:

1. the round holds **nothing** — `probe_round_is_safe_to_retire`, the same
   durable-state predicate retirement itself uses. Any answer but `None`
   keeps the frozen plan, so a resting or partly filled round is
   byte-for-byte unchanged and leg two still matches leg one. **Nothing is
   cancelled, so this cannot churn orders: it never runs while an order
   exists.**
2. drift ≥ `replan_drift_ticks()` (2, clamped into 1..6 so it can never sit
   behind retirement);
3. the fresh plan is `ok` — a plan that will not price is never substituted
   for one that did.

The canonical `probe_stored_plan` read is left exactly where it was, so the
atomic pair re-reservation it anchors is unmoved.

### 5c. The telemetry never reached the tape

`bounded_hb` reduces the largest field outside `HB_NEVER_REDUCE` until the
line fits, and `c300a6c4` reports exactly that: `hb_reduced.fields` =
`{shadow_controller: 64618, l5_baseline: 19249, completion_latency: 15234}`.
So the one instrument built to answer *where does the time go* arrives as
`{"_reduced": true, "bytes": 15234}` and the funnel it was written to measure
cannot be measured in production.

It now has its own `L5_COMPLETION_LATENCY` line — the same answer the full
authorization rows got. It was **not** added to `HB_NEVER_REDUCE`: protecting
15 KB of telemetry would push a safety field off the line, which is the
precise failure `bounded_hb` exists to prevent. Telemetry does not outrank
the submission gate.

---

## 6. Conservative economics

### 6a. Variants on identical frames, 13 out-of-sample windows

`analysis/l5/issue208/variant_comparison.py`. Fills from
`queue_scenarios.leg_outcome`; the **pessimistic** arm is the PROVEN verdict —
a queue-independent **lower** bound, no look-ahead, and a touch is never a
fill. Rebates are a separate column and are **$0.00 at the deployed clip**.

| variant | arm | frames | P(pair) | net ex-rebate / frame |
|---|---|--:|--:|--:|
| A sequential at the touch | pessimistic | 652 | **0.0000** | −0.011257 |
| B simultaneous d2 | pessimistic | 627 | 0.3413 | +0.013312 |
| C B + adaptive completion | pessimistic | 627 | 0.3652 | **+0.014833** |
| D C + bounded taker | pessimistic | 627 | 0.3652 | +0.014833 |
| E C + floor waived | pessimistic | 627 | 0.5758 | +0.019587 |
| F C at the code's 0.20 floor | pessimistic | 627 | 0.4354 | +0.016122 |

**A completes zero pairs in 652 frames.** A pair fixed at the touch costs
`1 − spread` = 0.99 on a one-tick book, against a 0.96 ceiling: arithmetically
impossible, not merely unlikely. `book_completion_depth_ticks` is **0** on the
deployed service, so that is the shape the rescue path runs.

**D recovers nothing.** All remaining strands fail the cap again after taker
fees, so keeping taker disabled costs **zero**. The §6c scorer reaches the
same result independently.

**A correction to my own replay.** `variant_comparison` required **both** legs
in the first-leg band for the simultaneous variants. Production calls
`first_leg_entry_refusal` once, on `nxt`, and `nxt` is the **cheaper** leg —
so the ceiling cannot bind a two-legged plan at all (the cheaper of two legs
summing to ≤ 0.96 is at most 0.48) and only the **floor** can. Corrected,
variant B's conservative net is **+0.013312** a frame rather than the
+0.010881 I reported earlier.

### 6b. The largest single funnel loss, and why it is not promotable

With the gate modelled correctly, **312 of 627 frames (49.8%) are refused by
the first-leg floor alone** — plans whose pair cost already clears the
fee-safe 0.96 cap.

L5's own 2,216-window history (`src/l5_residual_state_replay.py`) says where
the floor belongs:

| first-leg own px | n | completion rate |
|---|--:|--:|
| 0.0–0.1 | 95 | **28.4%** |
| 0.1–0.2 | 114 | 67.5% |
| **0.2–0.3** | **109** | **80.7%** |
| 0.3–0.4 | 142 | 80.3% |
| 0.4–0.5 | 315 | 88.9% |
| 0.5–0.6 | 494 | 85.2% |
| 0.6–0.7 | 408 | 76.2% |
| 0.7–0.8 | 257 | 60.7% |
| 0.8–0.9 | 184 | 46.7% |

The deployed floor of 0.30 excludes a bucket that completes **better than
0.3–0.4 and better than the 0.6–0.7 bucket the band permits**. Completion only
degrades below 0.20 and collapses below 0.10. The six 2026-10-23 windows the
0.30 value cites opened at own **0.12 and 0.06**, both below 0.20 — so that
evidence supports a floor at the code's own default of **0.20**, not at 0.30.

**And it is still not promotable.** Break-even sweep on the residual carry
per stranded contract:

| residual carry | C net/frame | F net/frame | E net/frame |
|---|--:|--:|--:|
| 0.0000 | 0.018070 | 0.021467 | 0.028357 |
| **−0.0236 (measured)** | 0.014833 | **0.016122** | 0.019587 |
| −0.0400 | 0.012584 | 0.012408 | 0.013493 |
| −0.0600 | 0.009841 | 0.007879 | 0.006061 |
| −0.1000 | 0.004354 | −0.001180 | −0.008804 |

* **F beats C only while the carry is better than −0.038036/contract.** The
  measured carry is −0.0236 — a margin of 1.61×.
* **E beats C only while the carry is better than −0.043878/contract**, and
  E's strands are a different population: mean own price 0.335 with **50.6%
  below 0.30** and a minimum of 0.020, against C's mean 0.485 and minimum
  0.300. The −0.0236 constant was measured over a mix that looks like C's.
  Applying it to E's cheap-leg-heavy strands is an extrapolation the
  measurement does not support.
* The floor's own cited single-leg losses were **−0.1764** (own 0.12) and
  **−0.0930** (own 0.06) — both worse than either break-even, and both below
  0.20.

So the honest reading: the evidence favours **0.20** over 0.30, the gain is
**+8.7%** of a small number, the safety margin is **1.6×**, and the number
that decides it — the carry of a *cheap* stranded contract — has not been
measured. `L5_TS_MIN_FIRST_LEG_PRICE` is a Railway variable and #206 forbids
changing it. **I am not proposing that change; I am reporting its exact
break-even so the decision is yours and is numerate.**

### 6c. Every disposition of an unmatched leg

`analysis/l5/issue206/unmatched_leg_scorer.py`, conservative arm, **352
unmatched legs**, mean EV per contract:

| disposition | mean EV/contract |
|---|--:|
| **improve one tick (depth 1)** | **−0.015080** ← argmax in 98.6% |
| complement at touch (deployed depth 0) | −0.023239 |
| complement deeper, d4 / d5 | −0.023533 |
| maintain at depth 2 | −0.023600 |
| complement deeper, d3 | −0.023600 |
| reduce by crossing (taker, RESEARCH) | −0.023600 |
| hold to settlement | −0.023600 |

Two findings:

1. **The repricing is already economically intelligent.** The controller
   reaches the best maker option in **99.7%** of legs. The *choice* is not the
   defect.
2. **Every option is negative.** Completing a stranded leg is
   loss-minimisation, not edge. The edge in B/C comes from *pairs that
   complete*, never from rescues. And the deployed completion depth of **0 is
   one tick too shallow** — depth 1 dominates it by **+0.0082/contract** over
   352 legs, which is the same arithmetic as variant A's zero pairs, reached
   independently.

The owner's six options map onto the controller's `COMPLETE / REPRICE / HOLD`
as: maintain → HOLD; improve one tick → REPRICE; cancel-and-replace is the
*mechanism* of the other two and has no separate economics; complementary
quote → COMPLETE; hold-to-settlement → HOLD at terminal. **Reduce exposure is
deliberately absent** — reducing by crossing is `l5_defensive_completion`,
taker, and `taker_crossing_reachable` is false on the deployed service. It is
priced above as research and it is worth nothing.

### 6d. Modelled versus confirmed

| | |
|---|---|
| maker submissions, venue-confirmed, this candidate | **0** |
| venue-confirmed fills, this candidate | **0** |
| completed pairs, venue-confirmed | **0** |
| modelled pairs (variant C, conservative) | 229 over 627 frames, 13 windows |
| confirmed credited rebates | **$2.13 over 293 fills** — historical, and **$0.00 at the deployed clip of 1** |
| accrued-not-credited | $1.48 — counts for nothing |
| account-wide settled realized | **−$15.6109** |
| `verified_l5_realized` | −$3.4103, status `UNPROVEN_LEGACY_ATTRIBUTION` |
| `unknown_post_baseline_only` | −$9.4158, provenance `UNKNOWN_COST_UPPER_BOUND` |
| today's realized | $0, halt room $5.00 |

**Nothing above is realized profit.** Every positive number in §6 is a
modelled replay figure on 13 windows.

---

## 7. Railway health and infrastructure cost

`l5-pmus-live`, deployment `c300a6c4`, SUCCESS, uptime 11,473 s at read.

| measure | 24 h |
|---|---|
| CPU | avg 0.083, max 0.283 |
| memory GB | avg 2.360, **max 4.584** |
| disk GB | 3.029 of a 50 GB volume |
| venue requests | 622 at 0.054 rps against a 1.5 budget; `http_429` **0**; unmet demand 0 |
| ledger | `load_s` 0.0272 (was 13.7232) |

The memory maximum is **4.58 GB against the 7.10 GB** recorded before the
append-only ledger landed — consistent with #210 removing the 292.7 MB
full-document parse from every start and every save.

**Cost.** The project `arb-ev-wallet-shadow` carries **49 services**. The
Railway MCP surface returns resource usage, not billing, so I am not going to
put a dollar figure on it: the invoice is in the Railway dashboard. What I can
say is that the executor itself is cheap — 8% of a core — and that 49 services
in one project is the larger line item and is worth a prune, which is not a
change I would make without you asking.

---

## 8. Remaining blockers

1. **Zero venue-confirmed fills at any depth.** Everything in §6 is modelled
   on an effective sample of **13 windows**. The ranking (A ≪ B < C, D ≡ C) is
   stable across all three arms; the magnitudes are not.
2. **The carry of a cheap stranded contract is unmeasured.** It is the number
   that decides the floor, and it is the difference between a +19% pair-rate
   gain and a loss.
3. **At the deployed clip of 1 contract the rebate is $0.00.** Any plan whose
   economics lean on rebates needs a clip of ≥ 2.52, which needs more
   exposure than the $1.00 ceiling allows.
4. **Replenishment is off** (`dual_replenish_enabled_now` false), so the
   multi-round accumulation the thesis describes is not running.
5. **Carried over, unresolved:** the −$1.27 unreconciled bridge line; eight
   2026-10-14/15 `UNPROVEN_LEGACY_ATTRIBUTION` markets; the $0.0521 defensive
   budget discrepancy; 220 `controller_open:ENTERED` with no matching outcome
   counter (likely a stats-restore artifact across restarts — **not proven,
   and not claimed as a defect**); PR #197 draft; PR #199 must not merge
   unchanged.

---

## 9. GO / NO-GO

**NO-GO for arming, live orders, taker execution, limit increases or budget
resets.** Conservative economics are positive but modelled on 13 windows with
zero venue confirmation, which is the "edge unproven" case #206 says to fail.

**GO, subject to your lifting the freeze, for a DISARMED deployment.** The
case for it:

* the service is `shadow_only: true` and `armed: false`, so deploying moves no
  money;
* the candidate introduces **zero** test failures against the deployed SHA
  (§10);
* of the three changes, one is **reporting-only** (the latency line), one is
  **default-OFF** (adaptive completion depth), and one — the re-price — acts
  only when the round holds nothing, cancels nothing, and passes through every
  existing gate;
* the two diagnostic questions that matter next (does the re-price raise the
  fill rate; does the adaptive depth raise the pair rate) can only be answered
  on live books, and both can be answered **disarmed**.

**That is a recommendation, not an action.** The freeze is yours to lift.

---

## 10. Regression

`pytest tests/ -q -p no:randomly`, baseline in a worktree at the deployed SHA.

| tree | failed | passed | skipped | xfailed | errors |
|---|--:|--:|--:|--:|--:|
| baseline `6576964e6` (deployed SHA, worktree) | 20 | 18,540 | 28 | 1 | 14 |
| candidate | 20 | **18,631** | 28 | 1 | 14 |

**The failing and erroring ID sets are identical — 34 IDs, `diff` clean — and
passed rises by the tests added here. Zero introduced failures.** The counts
alone are not the check; the ID sets were compared directly.

Targeted suites, all green: ledger migration / corruption / restart /
duplicate-finalize / rollover (`test_l5_shadow_ledger_retention.py`), exact
cancellation (`test_l5_cancel_*`), watchdog (`test_l5_watchdog*`), exposure
(`test_l5_exposure_sufficiency.py`), settlement
(`test_l5_settlement_*`), races (`test_l5_race_*`), and the two new files
(59 tests). Focused regression over two_sided / live_execution / e2e / probe /
book_completion / plan / round / latency / depth: **2,679 passed**, with only
the baseline IDs failing.

The three baseline failures nearest this work, named so they are not silent:
`test_l5_shadow_controller_wiring.py::test_the_heartbeat_carries_the_shadow_report`
(the `shadow_controller` block is log-reduced — the same `bounded_hb` mechanism
as §5c, and unchanged here) and two `test_lane_a_probe_v0.py` lineage checks
that compare against a reviewed artifact digest. All three fail identically at
the deployed SHA.

---

## 11. Deployment procedure (for when the freeze is lifted)

**Preconditions, each verified before anything is pushed:**

1. `L5_SHADOW_ONLY=1` — confirmed present and unchanged.
2. `armed: false` and `submission_gate.blocked: true` on the current
   heartbeat.
3. The campaign approval is expired or absent, so no window is authorized.
4. No live orders: `order_progress.live_orders == 0` and
   `account_exposure.account_total_worst_case_usd == 0`.
5. The ledger manifest reads `FORMAT_2_NATIVE` and `LOG_AGREES_WITH_MANIFEST`.

**Steps:**

1. Merge the candidate into the deployment-tracking branch. **Change no
   Railway variable.** The two new switches default to inert:
   `L5_COMPLETION_ADAPTIVE_DEPTH` unset = off,
   `L5_TS_REPLAN_DRIFT_TICKS` unset = 2.
2. Let the service restart on its own. The first start after the deploy does
   **not** re-migrate: the ledger is already format 2.
3. Within one window, confirm on the tape: an `L5_COMPLETION_LATENCY` line
   appears (the §5c fix), `roll_elapsed_s_max` stays under 1 s,
   `submission_gate.reason` is still `SHADOW_ONLY_LOCK`, and
   `L5_EFFECTIVE_RUNTIME_CONFIG` still reports `armed: false`,
   `taker_crossing_reachable: false`.
4. Over the following day, read `two_sided_replan` on the decision line: how
   often a plan drifted ≥ 2 ticks, and what the re-price changed it to. That
   is the measurement the deploy is for.

**Rollback.** Redeploy `6576964e6` from the Railway deployment list — the same
volume, the same ledger format, no migration either way, because format 2 is
forward-compatible and the candidate adds no on-disk field. The two new
switches are read-only env lookups with inert defaults, so a rollback needs no
variable change. Rollback criteria: any `L5_LIVE_HB` showing
`submission_gate.blocked: false`, any order on the venue, any
`latency_line_failed:*` counter rising, or `roll_elapsed_s_max` above 5 s.

---

## 12. One-window maker-only diagnostic proposal

**Not requested here and not authorized.** Stated so the shape is on record
for when it is.

* **Purpose:** obtain the first venue-confirmed fill at depth 2 with both legs
  resting simultaneously — the one fact §8 blocker 1 names.
* **Shape:** one BTC15m window, maker-only, clip 1, both legs at entry depth
  2, `max_live_orders` 2, pair ceiling 0.96, first-leg band unchanged at
  0.30–0.60, taker off, adaptive completion depth **on** so a strand can be
  rescued at a depth that clears the cap.
* **Bounds, all already deployed:** $1.00 aggregate worst case, $5.00 daily
  realized loss halt, one market, one round allocation, 120 s order TTL,
  automatic disarm at the window's end, exact-order cancellation, the
  independent watchdog.
* **Worst case:** one leg fills and the other does not, and the strand settles
  for its own cost. **Not bounded by the 0.60 band ceiling** — §6a establishes
  that the ceiling cannot bind a two-legged plan, because the gate tests only
  the cheaper leg. The binding bounds are the pair cap and the floor: the
  dear leg is at most `0.96 − 0.30` = **$0.66** per contract, so **$0.66** at
  the clip of 1, and the $1.00 worst-case deficit check holds independently.
* **What it decides:** whether a simultaneous depth-2 pair fills at all on our
  queue position. A single confirmed pair would convert the whole of §6 from
  modelled to anchored; a single confirmed *strand* would give the first real
  data point on the cheap-leg carry in §8 blocker 2.
* **What it does not decide:** profitability. One window cannot.

It needs explicit owner approval, a fresh campaign approval document with an
unexpired nonce, and the freeze lifted. None of those exist.
