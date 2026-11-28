# L5 one-window live diagnostic — deploy-ready package

**Status: DEPLOY-READY CANDIDATE. Not deployed, not armed, no order placed.**
Production is still on `c300a6c4` running `6576964e6`. Issue #206's freeze is
intact. No Railway variable was changed, no limit moved, no budget reset.

The owner's conditional approval covers **one** BTC15m maker-only diagnostic
at $1 aggregate exposure with a $5 daily halt, taker disabled, watchdog on,
no automatic continuation. This document is what that approval was made
conditional on.

---

## 1. Release artifact

| | |
|---|---|
| branch | `integration/l5-safe-release-candidate-20261109` |
| candidate SHA | **`240b1faf03c109b61e0162d630ef2e970472365c`** |
| builds from | `deploy/l5_pmus_live/Dockerfile` (51 explicit `COPY src/l5_*` lines) |
| Railway service | `l5-pmus-live` (`87f1834e-530e-4fc4-a310-91c8d3add196`) |
| currently deployed | `c300a6c4` / `6576964e6` — **untouched** |

**No Dockerfile change is required.** Seven `src/` modules changed against the
deployed SHA and six are already in that COPY list:
`l5_book_completion`, `l5_campaign_approval`, `l5_fill_calibration`,
`l5_live_execution`, `l5_pmus_live_runner`, `l5_two_sided_probe`. The
seventh, `l5_probe_preflight`, is the **review tool** and is deliberately not
in the container — it runs locally against the heartbeat. No new module was
added, so nothing is missing from the image.

Integrated from PR #212 by merge, not by cherry-pick: #212 is based on the
owner's `8b1d3db74` and touches files disjoint from this candidate's executor
work, so there was no conflict and none was resolved away.

---

## 2. Order-event evidence and exact-market authorization

These were the two preflight weaknesses the owner would not bypass.

### 2a. Market mismatch — refused at the ENFORCEMENT boundary

The runner calls `AUTH.authorize_arming(market=t.market_slug, ...)` — the
transport's **active** slug — and `campaign_window_is_usable(t.market_slug)`
before it. So the executor cannot arm on a market other than the one it is
actually on, and the campaign's `window_starts` is an exact-match allowlist
refusing anything else `WINDOW_NOT_IN_AUTHORIZED_SET`.

`tests/test_l5_arming_market_mismatch.py` (9 tests) asserts all four of the
owner's conditions, for the previous window, the next window, and the shadow
lock over a correct window:

1. arming is **refused**;
2. **no authorization claim is consumed** — the claim ledger stays empty and
   the approved window is still exactly as available as before (`would_reserve`
   compared before and after). This is how 2045z was lost;
3. **no campaign window is spent**;
4. **no order is submitted** and the transport is never armed — its `submit`
   raises if anything tries.

It also pins that #212's review-time `exact_window_consistency` and the
enforcement boundary refuse the **same** mismatch, since a divergence between
those two readers is the entire defect.

**A defect these tests found.** `reserve_window` spends one of the owner's
windows and never asked whether the market was one the approval covers. Called
with the previous window's slug it returned `ok: True`, `reserved_now: True`,
and the single authorized window was gone. `opening_refusal`'s own docstring
says *"ONE DEFINITION, TWO CALLERS … because 'may this window be opened' and
'may this order be sent into it' must never be able to disagree"* — and the
third caller, the one that consumes the budget, was not asking. It asks now,
through the same function. Not reachable through the runner today (it reserves
only after `authorize_arming` returns, which already refuses the mismatch), so
this was a latent hole rather than a live loss — but the protection stood
entirely on one caller's ordering.

### 2b. Recorder durability — a read-back, not a counter

#212 closed the first half: `orders > 0` alone could pass with
`events_appended` zero or missing. The rest is a property of the counter.
`CalibrationRecorder.appended` is initialised to 0 in `__init__` and
incremented only in `_append`, so it counts what **this process** wrote and is
zeroed by every restart. A gate reading it can be satisfied by a process whose
writes nothing has read back, and refused by a process holding a fully durable
ledger it has just reloaded.

Added `verify_readback()` — it re-opens the file and reports events, distinct
order identities, identities with the full `REQUIRED_CAPTURE` set, unreadable
lines, and a sample of coids with event counts so a caller can assert the
identity it wrote is the one that came back. Added `replayed`, the count of
events a **previous** process wrote that this one recovered: the only figure
here that cannot be non-zero without a surviving write. Two new fail-closed
preflight rows read them, and an **absent** readback is UNKNOWN rather than
clean.

33 tests in `tests/test_l5_recorder_durable_readback.py`: a unique coid
written, reopened through a fresh instance, identity **and** contents compared
field by field; duplicate event lines and duplicate fills; a partial final
line; a line with no identity; absent and unreadable ledgers; the restart
straddle, and that reloading alone invents no boundary.

**A defect these tests found — a boot crash.** `_fold` reads `ev.get("coid")`
and `_load` handed it whatever `json.loads` returned, so a ledger line holding
a JSON list, string or number raised `AttributeError` out of `_load`, out of
`__init__`. The recorder is constructed in `Runner.__init__`, so **one** such
line in the durable ledger took the whole process down at boot. Non-mappings
are now counted and skipped at both the load and the live fold.

### 2c. What production will report, and the ordering it forces

The deployed recorder already writes in shadow and its records already survive
restarts — `fill_calibration` on `c300a6c4` reports `orders: 18,536`,
`events_appended: 2,466` (this process), `errors: {}`. Most of the 18,536 came
back off the volume, so `events_replayed_from_disk` will be large and
positive and **both new gates are satisfiable disarmed, before any money.**

`usable_for_calibration: 0` across those 18,536 is **expected and is not a
defect**: a shadow record has no `terminal_state` and no `fee_accounting`
because no real order ever terminated or paid a fee. It is not a gate here.

**This forces the sequence.** The `readback` block only exists once the
candidate is running, so the two gates cannot be evaluated against today's
heartbeat. The order is therefore: deploy disarmed → read the heartbeat →
confirm both rows pass → *then* consider arming. There is no way to satisfy
them first.

---

## 3. Regression

`pytest tests/ -q -p no:randomly`, baseline in a worktree at the deployed SHA.

| tree | failed | passed | skipped | xfailed | errors |
|---|--:|--:|--:|--:|--:|
| baseline `6576964e6` | 20 | 18,540 | 28 | 1 | 14 |
| candidate | 20 | **18,714** | 28 | 1 | 14 |

**Failing and erroring ID sets identical — 34 IDs, `diff` clean. Zero
introduced failures.** The counts alone were not the check.

**One failure WAS introduced and is fixed.** The full suite caught what the
focused runs could not: `test_the_executor_refusal_inventory_is_still_complete`
counts the refusal sites in `l5_live_execution.handle` and requires
`l5_intent_gate.EXECUTOR_GATES_NOT_EVALUATED_HERE` to list every one, so the
reviewed inventory cannot silently go stale. `PRESEND_BEHIND` was a 50th site
against an inventory of 49. Registered with its reasoning, and the suite is
clean at 21 → 20. This is recorded rather than quietly absorbed: the parity
claim is only worth anything if the near-misses are reported too.

Targeted suites, all green: simultaneous two-sided submission and venue
acknowledgement; no fills / partial fills / completed pairs; stale
opening-plan refresh; resting-order repricing; cancel/fill race; unmatched-leg
completion; rollover; disconnect, restart and durable event recovery;
exact-window campaign authorization; $1 aggregate exposure over open orders
**and** held inventory; the $5 daily halt; settlement with actual fees.

---

## 4. Effective production configuration (unchanged)

From the service's own `L5_EFFECTIVE_RUNTIME_CONFIG`, not from variable text.

`max_live_orders` 2 · `aggregate_exposure_usd` 1.00 ·
`max_worst_case_deficit_usd` 1.00 ACTIVE · `daily_realized_loss_halt_usd` 5 ·
`max_cash_deployed_usd` 12 `NO_RECYCLING` · `ts_opening_leg_max_contracts` 1 ·
`ts_round_allocations` 1 · `ts_entry_depth_ticks` 2 ·
`book_completion_depth_ticks` 0 · first-leg band 0.30–0.60 ·
`maker_pair_ceiling` 0.96 · `dual_quote_enabled_now` true ·
`dual_max_placements_per_pass` 2 · `dual_replenish_enabled_now` false ·
`taker_crossing_reachable` **false** · `shadow_only` **true** ·
`armed` **false** · `ts_order_ttl_s` 120 · `ts_entry_cutoff_s` 480 ·
`ts_complete_cutoff_s` 600 · `cancel_margin_s` 300.

**Adaptive maker completion** (`L5_COMPLETION_ADAPTIVE_DEPTH`) is **default
OFF** and the owner's approval allows it "only if verified". With the flag off
the candidate list is `[deployed depth]`, zero extra decisions, path
byte-identical. Turning it on is a Railway variable change and therefore a
separate decision; §7 keeps it out of the first window.

---

## 5. Book freshness

`TS.MAX_BOOK_AGE_S = 2.0`, enforced on the **execution** path in
`build_orders` (`BOOK_STALE` when the age is absent, negative or above 2.0) and
again in `complement_order`. #212 added the matching ≤2 s bound to the review
preflight, so the reviewer and the executor now apply the same number.

Two pre-send guards read the mirror **again** immediately before the wire,
because the planning snapshot is not the book the order lands in:

* `presend_cross_refusal` — declines a leg the book has made too
  **aggressive**, which the venue would reject post-only;
* `presend_behind_refusal` — **new** — declines a leg the book has left too
  **passive**: placeable, never fills, and holding a live-order slot for its
  whole 120 s TTL.

Both fail **open** on every unknown and run before the submission claim is
consumed. The behind guard additionally runs only while the round holds
nothing, so a complement matching held inventory is never declined — doing so
would strand the inventory it was going to match.

---

## 6. Recorder and watchdog readiness

* **Recorder:** §2b/§2c. Verified disarmed, from the heartbeat.
* **Watchdog:** independent service `l5-halt-watchdog`
  (`fb038b8c-c7c1-4599-b06a-54f72bad544f`), separate from the executor
  process. `authorize_arming` refuses `halt_gate_present=False` by name, so a
  process with no independent halt cannot arm at all. The latch digest travels
  on the heartbeat (`halt_latch_telemetry.sha256`) so the owner can obtain it
  without stopping the watchdog — the failure of 2026-10-19.
* **Cancellation is not gated by the arm switch**, proven by a test that flips
  the switch under two resting legs and still cancels both by exact venue id.

---

## 7. The window, and why it is not pinned here

The owner's instruction stands: *"We can select the next available window once
the deployment is verified, but its UTC start time must be explicitly
confirmed."* So this names the **procedure and a worked example**, not a
commitment.

`python3 analysis/l5/issue208/run_probe_preflight.py window --lead-min 25`
is read-only — it generates and arms nothing. Run at 2026-11-09T06:27:17Z it
produced:

```
window start (UTC)  2026-11-09T07:00:00Z
lead time           1962 s
slug                cpc-btc-updown-15m-2026-11-09-0700z
L5_TS_MARKET=cpc-btc-updown-15m-2026-11-09-0700z
L5_TS_EXPERIMENT_ID=probe-depth2-20261109-0700z
```

**That window is illustrative and will be stale by the time a deploy is
verified.** `window_starts` is an exact-match allowlist, so a stale nonce
cannot be reused: any other window is refused
`WINDOW_NOT_IN_AUTHORIZED_SET`. The real window must be generated **after**
the disarmed deploy is confirmed healthy, and its UTC start confirmed by the
owner in writing.

**Lead time required:** at least 25 minutes between generating the approval
and the window start — build and restart, one reconcile pass, a heartbeat read
to confirm both recorder gates, and the arming decision.

### Expected maker-only behaviour in the window

One market. Entry opens after the window start and shuts at T+480. Two
post-only maker legs at entry depth 2, one contract each, pair cost ≤ 0.96,
cheaper leg first and tested against the 0.30 floor. Each leg has its own
120 s TTL; an unfilled leg is cancelled by exact venue id and may be replaced
from the **current** book. A single fill books inventory and the complement is
priced at that moment, not at planning time. Completion is available to T+600;
nothing may rest past the terminal at T+600. No taker cross at any point.
Settlement-only realized P&L labelling.

**Worst case:** one leg fills and the other does not, and the strand settles
for its own cost. **Not** bounded by the 0.60 band ceiling — the gate tests
only the cheaper leg, so the ceiling cannot bind a two-legged plan. The
binding bounds are the pair cap less the floor, `0.96 − 0.30` = **$0.66** per
contract at the clip of 1, with the $1.00 worst-case deficit check holding
independently.

---

## 8. Abort criteria

Abort — disarm, cancel by exact id, and stop — on **any** of:

1. `submission_gate.blocked` reading false when it should be true, or any
   order appearing that this window did not authorize;
2. `account_exposure.account_total_worst_case_usd` above **$1.00**, or
   `max_worst_case_deficit_usd` reporting the check INACTIVE;
3. `day_realized.would_halt` true, or realized loss reaching **−$5.00**;
4. any order the venue will not confirm terminal on re-read
   (`orders_uncertain` non-empty) — ambiguous POST halts re-entry pending
   authoritative reconciliation;
5. `halt_latch` reading `HALT`, `UNREADABLE` or `NO_GATE`;
6. a second market acquiring a position or a resting order;
7. `taker_crossing_reachable` reading true, or any `defensive_budget` debit —
   nothing but a bounded taker cross writes that;
8. `roll_elapsed_s_max` above 5 s, or `order_progress.overdue_s` above zero
   and rising;
9. `latency_line_failed:*` or `ledger_write_failed` rising;
10. the window's slug no longer matching the approval.

The watchdog acts independently of all ten. **No automatic continuation**: a
completed or crashed probe does not relaunch itself.

## Post-settlement reconciliation

1. Let the window settle; do not judge P&L before settlement.
2. `run_probe_preflight.py audit` for the verdict — MEASURED /
   UNINTERPRETABLE / INCOMPLETE. Positive-evidence basis checks only: a fill
   count ≥ 1, not at the $1 bound, no `gross_quarantine`, distinct venue ids.
3. Reconcile every order id against the venue's own record: submitted →
   acknowledged → filled → settled, each state named separately. A touch is
   not a fill and an acknowledgement is not a fill.
4. Read `verify_readback()` for the window's own coids and confirm the events
   survived to disk.
5. Report **submitted / venue-confirmed filled / matched pairs / residual
   inventory / actual fees / confirmed credited rebates / settled realized
   net** as separate lines. Rebates count only once credited.
6. Compare the realized outcome against the conservative model. A single
   window cannot establish profitability either way; it establishes whether
   a simultaneous depth-2 pair fills at all on our queue position.

---

## 9. Economics, without rebates

At the deployed clip of **1 contract the maker rebate is $0.00.** The config
verifier says so itself: *"rebate floor at p=0.50 is 1.6 contracts, confirmed
floor 2.52 (earns $0.00 below it)"*. Every figure below therefore excludes
rebates, and the historical $2.13/293 fills is not projected onto this window.

Conservative PROVEN arm, 13 out-of-sample windows, net per frame ex-rebate:
sequential-at-the-touch **−0.011257** with **zero pairs in 652 frames**;
simultaneous depth 2 **+0.013312**; plus adaptive completion **+0.014833**;
plus bounded taker **+0.014833** — taker recovers nothing, so keeping it off
costs zero.

**The opening-band floor experiment is kept separate, as instructed.** L5's
historical completion rates for 0.20–0.30 entries (80.7%, n=109) do **not**
establish profitability: the variant beats the deployed floor only while the
residual carry is better than −0.038036/contract against a measured −0.0236,
a 1.6× margin for an 8.7% gain, and the number that decides it — the carry of
a *cheap* stranded contract — has not been measured. `L5_TS_MIN_FIRST_LEG_PRICE`
is a Railway variable, it is not part of this window, and no change to it is
proposed.

**Confirmed settled realized net: −$15.6109** before $2.13 of historically
credited rebates. `verified_l5_realized` −$3.4103 at status
`UNPROVEN_LEGACY_ATTRIBUTION`. Today $0 with $5.00 of halt room. **Zero
venue-confirmed fills from this candidate.**

---

## 10. What is still required before arming

1. The owner lifts #206's freeze **for this release and this window**.
2. The disarmed deploy of the candidate SHA is confirmed healthy, and both
   recorder gates read true on its heartbeat (§2c — they cannot be evaluated
   before it).
3. The owner confirms the **exact UTC window start** in writing, and sets the
   campaign approval for that window only.
4. The full preflight returns GO for that exact slug, with
   `exact_window_consistency` passing — executor active market, plan market,
   evaluated market and campaign coverage all naming one market.

Items 2–4 cannot be done ahead of time and none of them is a code change.
