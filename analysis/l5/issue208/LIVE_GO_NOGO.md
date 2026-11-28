# Live GO / NO-GO for a capped depth-2 probe

**This is a readiness report, not an authorization and not a request to be
granted one by me.** Clearing the shadow lock, the absolute deadline and
the campaign approval is the owner's decision.

## 1. Commit and what actually changed

Branch `fix/l5-reopening-decision`. Code changes since the deployed
`9a8b514dd`:

| file | change |
|---|---|
| `src/l5_probe_preflight.py` | **new.** Executable preflight + post-settlement verdict. Read-only; holds no transport. Not imported by the entrypoint, so no image change. |
| `src/l5_live_execution.py` | reporting only: `unknown_post_baseline_only`, `identity_precondition_met`, and the identity's precondition stated |
| `src/l5_pmus_live_runner.py` | `held_qty > 0` guard on fill detection; provisional-side repair behind `L5_PROVISIONAL_FORCED_SIDE` (**default OFF**) |
| `src/l5_completion_latency.py` | `detection_to_first_decision_s` added |
| `src/l5_fill_calibration.py` | four eligibility false positives closed |
| `src/l5_book_completion.py` | `stream_age_s` carried on every decision |

**No limit, gate, cap or authorization was changed anywhere.**

## 2. Priority 1 — the execution blocker, answered

**`PROVISIONAL_PRICE_UNREADABLE` does prevent otherwise-valid simultaneous
maker orders — both legs of them — on the basis of a price that never
reaches an order.**

The traced path:

```
book -> maybe_controller_open
     -> controller_provisional_price(UP), (DOWN)     <- the gate
     -> refuse if EITHER is None                     <- the defect
     -> dec = {side, price}                          <- ADVISORY ONLY
     -> executor.handle(controller={"decision": dec})
     -> probe_plan -> TS.build_orders(bid, ask, tick, min_qty, depth_ticks)
     -> exactly two post-only intents, or a terminal refusal
```

`build_orders`'s signature carries **no side and no price**. `dec["side"]`
is read once downstream, only to record what was superseded. The gate
returns *before* the only submission door, so neither leg is ever offered
to the builder that would have priced both from the same book.

`controller_provisional_price` returns None when `touch - tick <= 0`: UP
once the ask is a single tick, DOWN once the bid is within a tick of 1.00.
At bid 0.00 / ask 0.01 the UP price is None and DOWN is 0.99.

**This also corrects my own earlier caution.** I withheld the repair partly
because "the forced side would be DOWN at own 0.99 — a poor use of the
probe's one entry". That was wrong: the forced side does not choose the
placed leg. The remaining reason the flag exists is only that enabling it
changes *when* the controller opens, which is a deployment decision.

**Recommendation:** leave `L5_PROVISIONAL_FORCED_SIDE` unset for the probe.
It occurred 9 times and the counter froze, so it is not the binding
constraint on this window; evaluate it separately.

## 3. Priority 2 — depth-2 execution, verified

**The configuration is `L5_TS_DEPTH_TICKS`, default 2. No new switch.**

On a 0.62/0.63 book, `build_orders` emits exactly two post-only MAKER
legs:

| leg | own price | wire price | rests |
|---|--:|--:|---|
| UP | 0.60 | 0.60 | below the 0.62 bid |
| DOWN | 0.35 | 0.65 | above the 0.63 ask |

`pair_cost 0.95`, `edge_per_contract 0.05`, `net_if_maker 0.05584375`,
`requires_post_only true`. Neither leg crosses.

**And the correction that matters most: depth 0 and depth 1 are not live
options at all.**

| depth | pair | verdict |
|--:|--:|---|
| 0 | 0.99 | **refused — `PAIR_COST_ABOVE_CAP`** |
| 1 | 0.97 | **refused — `PAIR_COST_ABOVE_CAP`** |
| 2 | 0.95 | ok |
| 3 | 0.93 | ok |

So depth 2 is not a preference over depth 0 — it is the **shallowest
admissible depth** under a cap we already enforce, and the replay's
depth-0/1 cells describe entries the engine would never have placed. My
earlier "depth 0 gives a 65% pair rate" framing is withdrawn.

Verified on the real controller methods with a real `InventoryStore`:

| requirement | status |
|---|---|
| simultaneous UP/DOWN quoting | two passes of `executor.handle`; `dual_max_placements_per_pass` 2 |
| both order slots | `max_live_orders` 2, slot ceiling is the executor's own |
| first-leg fill recognition | `record_submit` + `apply_order_progress` → position 1, residual 1 |
| inventory update | `leg_cost` booked in **own** terms from the venue's wire `avg_px` |
| second-leg maker completion | `COMPLETE` on a 0.75 pair, carrying `stream_age_s` |
| adaptive repricing | `REPRICE` when our own resting order is worse |
| `ALREADY_RESERVED` | an already-better order is left alone, not churned |
| economically justified holding | `HOLD` / `PAIR_ABOVE_REDEMPTION` at pair 1.09 |
| rejection of unprofitable crossing | inverted book refused; a COMPLETE *at the touch* asserted **not** to be a cross |

**Own/wire boundary, verified end to end** because I mis-read it twice:
`build_orders` emits own → `TS.wire_price` converts for the venue → the
venue reports `avg_px` in wire → `apply_fill` stores `own * qty`. A
completed depth-2 pair books at exactly **0.95**.

**No partial arming:** `TS.active()` requires *both*
`L5_TS_EXPERIMENT_ID` and `L5_TS_MARKET`.

## 4. Priority 3 — the probe is auditable, executably

`src/l5_probe_preflight.py`, 27 tests, driven against the gate values
actually observed on the running deployment.

`preflight()` runs before the window and checks the gate state, halt
latch, worst-case-deficit check, cash policy, unmatched cap, daily loss
room, completion reserve, book health, plan admissibility, leg count,
post-only/MAKER mode, expected depth, pair ceiling, fee floor, market
pre-registration, and recorder readiness.

`audit()` runs after settlement and returns a **verdict**:

- **MEASURED** — every filled leg has a known basis, market absent from
  the placeholder set
- **UNINTERPRETABLE** — basis unknown or upper-bounded, so the figure is a
  bound, not a result
- **INCOMPLETE** — nothing filled

Two properties I deliberately built in: **an UNKNOWN is never a pass**,
and the **pre-registered freshness refusal is separate from the
economics** — a p95 book age above 2.0 s fails its own named check while
the verdict may still be MEASURED, so freshness is a named blocker rather
than a contamination of the P&L.

`preflight()` returning ok explicitly disclaims being an authorization.

## 5. Priority 4 — economics of the actual probe

**Replay is the hypothesis, not proof.** For *one* depth-2 entry of one
contract per leg, OOS pessimistic (PROVEN, queue-independent):

| outcome | probability | value |
|---|--:|--:|
| completed pair at 0.95 | 52.5% | **+$0.0500** |
| one leg stranded | 42.3% | **−$0.0236** carry |
| nothing fills | 5.3% | $0 (a submission) |
| **expected pre-rebate (rounded outcome table)** | | **+$0.0163** |
| **historical credited-rebate assumption (not guaranteed prospectively)** | | **+$0.0107** |
| **expected net including historical rebate assumption** | | **+$0.0270** |
| **bounded worst case** | | **−$1.00** (`worst_case_deficit_check` ACTIVE) |

**Read that honestly: the probe's modeled pre-rebate expectation is about 1.6
cents, rising to about 2.7 cents only under a historical credited-rebate
assumption that is not guaranteed for a future probe, against a bounded
downside of a dollar.** It cannot and must not be
run to prove profitability — the gap to profitable matched-pair execution
is +0.0651/pair, 6.5 ticks, and no 1–2-window probe resolves that.

**Executable volume, not replay volume:** the replay opens an entry on
every frame, which is why its tables show 600+ entries and 22–48
"pairs per window". With `ts_opening_leg_max_contracts 1` and two slots,
a window supports **one outstanding entry at a time**. Treat the probe as
**one entry**, possibly repeated within the campaign cap the owner sets.

Asymmetric depth stays retired — tested, refuted, pinned.

## 6. Remaining blockers, ranked by impact

| # | blocker | kind | impact | resolution |
|--:|---|---|---|---|
| 1 | cost basis still accruing `UNKNOWN_COST_UPPER_BOUND` (four markets through 2026-11-05) | **uninterpretable result** | the probe could spend money and answer nothing | pre-register per-market scoring; run `audit()` after settlement |
| 2 | calibration-eligibility fix not deployed | uninterpretable queue data | a record can read "usable" when it is not | deploy |
| 3 | latency metric wrong in the running image | misleading latency figure | `detection_to_decision_s` reports window age | same deploy |
| 4 | #209 rolled back on the volume | degrading, not probe-blocking | re-scoring from an empty history; whole-file rewrite returning | deploy a branch containing #209 |
| 5 | expectancy | not resolvable by a probe | 6.5-tick gap | out of scope for 1–2 windows |

**No safety blocker found.** Three independent gates still refuse.

## 7. Exact probe parameters, for the owner to authorize or amend

| parameter | value |
|---|---|
| markets | **exactly one** BTC15m window, pre-registered by full slug before arming |
| windows | 1, with a second only after `audit()` returns MEASURED |
| depth | **2** (`L5_TS_DEPTH_TICKS=2`, the default) |
| legs | simultaneous UP + DOWN, post-only MAKER both |
| size | 1 contract per leg (`ts_opening_leg_max_contracts` 1) |
| slots | 2 (unchanged) |
| taker completion | **off** — both switches unset |
| `L5_PROVISIONAL_FORCED_SIDE` | **unset** |
| limits | unchanged: $12 no-recycling cash, $1 worst-case deficit, $1 total exposure, 5 unmatched cap, $5 daily halt |
| required to clear | shadow lock, absolute deadline, campaign approval — **owner only** |

**Required owner authorization:** the three gates above, naming the exact
market slug and the window count. Nothing else is needed, and nothing I
have pushed clears any of them.

## 8. How venue-confirmed fills and exact settled net will be established

1. **Fills** — `poll_orders` re-reads every live order from the venue by
   its exact `venue_order_id`; only `apply_order_progress` moves a fill
   into inventory, and it books the delta so a repeated poll books once.
   `audit()` requires a `venue_order_id` on every fill.
2. **Cost basis** — `apply_fill` stores `own * qty` from the venue's
   `avg_px`. A leg booked through these hooks is priced exactly; one that
   arrives as a venue net position without an order of ours is not, and
   that is the `UNKNOWN_COST_UPPER_BOUND` case.
3. **Settled net** — scored on the probe's **own market only**, never
   account-wide. `audit()` fails closed if the market appears in
   `unknown_basis_keys` or in the unattested set.
4. **Fees and rebates** — kept separate. Only credited rebates count; the
   $1.48 accrued-not-credited counts for nothing.
5. **Labels** — settlement-only realized labelling. A submitted or filled
   order is not realized profit until settlement, and an upper bound is
   never realized profit at all.

## 9. The next action required to reach real execution

**Deploy a branch containing #209 plus this work, shadow-only, with
`L5_PROVISIONAL_FORCED_SIDE` unset.** That clears blockers 2, 3 and 4 in
one step and leaves blocker 1 to the pre-registration. Then run
`preflight()` against the live heartbeat and the depth-2 plan, confirm
`ok: true`, pre-register the exact market slug, and bring the
authorization decision to the owner.

That deployment is ChatGPT's lane. I have not deployed, armed, enabled
taker execution, changed a limit, or submitted anything.
