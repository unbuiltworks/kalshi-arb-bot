# Autonomous policy evaluation from the continuous shadow — 2026-10-29

Evidence: **228 `L5_SHADOW_SETTLED` records, 38 distinct BTC15m markets, 6
policies, 12:04Z–21:16Z**, plus **401 `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`
frames**. All BASE scenario. Production **disarmed** (`PMUS_Q1_LIVE=NO`).
No new service, branch or deployment.

## Two corrections to my own earlier reporting

1. **I previously said the shadow forms "0 matched pairs".** That was from a
   single window (1830z) and was wrong as a general claim. Over 38 markets
   the policies form matched inventory in **78 of 228** policy-windows.
2. **The `pair_cost` field is not the figure comparable to the 0.96 cap.**
   `l5_shadow_controller:480` states it: *"the cost per pair is
   `1 - pair_net/matched`. That is the figure comparable to the pair cap;
   `pair_net` alone is not."* Reading the raw field gave a median of 1.5008;
   the correct computation gives **0.7625**. Everything below uses the
   correct one.

## Total settled net P&L per policy — not pair surplus

| policy | wins | n | matched | net resid | pair_net | **resid_net** | **NET** | net/window |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ARGMAX** | 16 | 38 | 38.00 | −12.00 | **+10.1837** | −13.3237 | **−3.1400** | **−0.0826** |
| RESIDUAL_URGENCY | 17 | 38 | 26.00 | 0.00 | +6.6767 | −13.0767 | −6.4000 | −0.1684 |
| PAIR_AVAILABLE | 8 | 38 | 29.00 | +10.00 | +3.5275 | −10.6175 | −7.0900 | −0.1866 |
| ARGMAX@ONE_TICK_BEHIND | 8 | 38 | 13.00 | −11.00 | +3.3417 | −15.2217 | −11.8800 | −0.3126 |
| ARGMAX@TWO_SIDED_LADDER | 11 | 38 | 33.00 | +3.00 | +7.4847 | −25.5547 | −18.0700 | −0.4755 |
| ARGMAX@TWO_SIDED | 8 | 38 | 26.00 | +9.00 | +6.9838 | −26.1338 | −19.1500 | −0.5039 |

**Every policy is net negative.** Pair surplus is positive for all six
(+$3.34 … +$10.18); residual settlement loses 2–4× more. Reporting pair
surplus alone would have shown a profitable system. It is not one.

## Matched-pair economics are not the problem

Cost per matched pair, `1 - pair_net/matched`, n=78:

| | |
|---|---|
| min / p25 / **median** / p75 / max | 0.4400 / 0.6904 / **0.7625** / 0.8900 / 0.9967 |
| at or below 0.9532 | 75/78 = **96.2%** |
| at or below 0.96 | 76/78 = **97.4%** |
| at or below 1.00 | 78/78 = **100.0%** |

Median by policy: RESIDUAL_URGENCY 0.7058, ARGMAX@TWO_SIDED 0.7083,
ONE_TICK_BEHIND 0.7163, **ARGMAX 0.7213**, TWO_SIDED_LADDER 0.7583,
PAIR_AVAILABLE 0.8650.

**When these policies match, they match well** — median $0.2375 locked per
unit. Pair selection and pair pricing are not the constraint.

## Venue-specific net economics — 0.9532 is NOT the break-even

From `src/pmus_fees.py` and the deployed fee provenance:

- **The PMUS schedule has no maker-fee term at all.** Maker fee is
  **$0.0000**, provenance *"CONFIRMED zero over 293 actual maker fills on
  this account."*
- A maker **rebate** exists, θ=0.0125, but is *"UNCREDITED: never observed to
  credit at q=1 on this account; not accrued until the venue pays it"*, and
  `pmus_fees` *"never subtracts it from a cost."*
- Taker fee θ=0.0695 applies only if we cross: $0.0146 at p=0.30, $0.0172 at
  p=0.45, $0.0174 at p=0.50.

A matched UP/DOWN pair redeems exactly $1.00. With zero maker fee, the
**accounting break-even for a maker-only matched pair is pair cost
< $1.0000**, with no fee drag.

**0.9532 is a completion-versus-carry decision threshold, not a venue fee
break-even, and must not be used as one.** It compares completing now
against carrying the residual to settlement, so it is price- and
probability-dependent. The correct form, for a held leg at own cost `p` with
`P` the chance the residual settles in our favour:

```
complete at pair cost c  iff  (1 - c)  >  (P * 1.00 - p)
                         iff   c       <  1 - P + p
```

At P=0.5 and p=0.45 that is c < 0.95; at p=0.30 it is c < 0.80. A single
constant cannot express it.

**The 0.96 policy ceiling should nonetheless stay.** `CLAUDE.md`: near-$1
pair tolerance *"belongs to later incentive-era L5 behavior and stays locked
unless equivalent economics are source-confirmed for our account."* Our
rebates are uncredited, so the economics are not confirmed. The headroom
between 0.96 and 1.00 is not ours to take.

## Realistic maker-fill probability and queue position

From 401 residual frames, for a **q=1** complement joining the opposite
touch:

| | |
|---|---|
| shares ahead of us (`queue_ahead_bound`) | median **586.67**, p90 1,955.46, max 21,015.66 |
| as a multiple of our need | median **316.53×**, p90 1,390×, max 7,005× |
| `order_resting` | **False in all 401** — the shadow places nothing |

Stated as the field itself does: this is *"a bound and not a position"* —
the venue publishes no per-order queue. But a median of ~587 shares ahead of
a 1-share need means a maker complement at the touch should be assumed
**unlikely to fill within a 15-minute window**, which is exactly the
residual the settled records then price.

### A measurement gap that blocks the obvious next question

`pair_if_maker`, `pair_if_maker_behind_touch` and `pair_if_taker_walk` are
**null in all 401 frames** (`why: "no_first_leg_price"`). The shadow holds
modelled inventory without a first-leg price, so it cannot price a
completing pair. **The dwell question — does the complement become reachable
at <=0.96 after 47s / 120s / 300s — is not answerable from the current
records.** That is a 1-field instrumentation gap, not a research project.

## The $0.60 guard against the autonomous policies

Applying the real `TS.first_leg_entry_refusal` to the dearer leg of every
shadow opening:

| policy | kept | kept NET | **refused** | refused NET | guard delta |
|---|---:|---:|---:|---:|---:|
| ARGMAX | 34 | −3.1400 | **0** | 0.0000 | **0.0000** |
| ARGMAX@ONE_TICK_BEHIND | 28 | −11.8800 | 0 | 0.0000 | 0.0000 |
| ARGMAX@TWO_SIDED | 38 | −19.1500 | 0 | 0.0000 | 0.0000 |
| PAIR_AVAILABLE | 35 | −7.0900 | 0 | 0.0000 | 0.0000 |
| RESIDUAL_URGENCY | 34 | −5.7400 | 1 | −0.6600 | +0.6600 |
| ARGMAX@TWO_SIDED_LADDER | 35 | −15.9800 | 3 | −2.0900 | +2.0900 |

**The guard refuses 4 of 228 and removes −$2.75 of losses — and has
precisely zero effect on ARGMAX, the best policy.** It remains correct on
L5's 849-window out-of-sample curve, and it is **not the lever for the
autonomous path.** These policies are not opening dear; they are failing to
complete.

## Comparison with L5's historical inventory behaviour

| | L5 | our best policy (ARGMAX) |
|---|---|---|
| windows ending with residual | **99.0%** (2,193/2,216) | 27/38 = 71% |
| deployed per window | ~$1,095 | $12 cash cap |
| legs per window | median **107** | 1 live order, <=3 contracts |
| maker share | 77.3% | maker-only |
| residual role | tolerated, *"EXTEND is a first-class action"* | pure cost: **−$1.11 per net residual unit** |
| matched economics | median pair cost 0.9714 | median **0.7213** |

We select *better* pairs than L5 and complete *far less often*. L5 absorbs
residual across ~107 legs; we carry it on 1 order slot. `CLAUDE.md` is
explicit that *"residual directional exposure"* has no assumed predictive
skill — our data agrees: it is a cost, not an edge.

## Best existing policy, and the smallest change

**Best: `ARGMAX`** — best NET (−$0.0826/window), highest pair surplus
(+$10.18), least-bad residual (−$13.32), most winning windows (16/38),
median pair cost 0.7213.

**Finding worth banking immediately, at zero cost:** the two-sided variants
are strictly worse. `ARGMAX@TWO_SIDED` doubles residual loss
(−13.32 → −26.13) while *reducing* pair surplus (+10.18 → +6.98). So
`L5_TS_TWO_ORDER_PAIR=NO` is correct and must stay off, and
`L5_TS_ROUND_ALLOCATIONS` should not be raised to fund a second
simultaneous opening.

**The arithmetic of flipping ARGMAX positive.** Measured unit economics:
matched earns **+$0.268/unit** (10.1837/38); residual costs **−$1.110/net
unit** (13.3237/12). ARGMAX runs 38 matched : 12 net residual = **3.17:1**.
Break-even needs 1.110/0.268 = **4.14:1**.

> **ARGMAX needs ~31% more matched units per unit of residual to break even.
> Nothing else — not sizing, not opening price — changes the sign.**

Scaling size down (e.g. the $0.80 notional cap) shrinks both terms and the
worst case, but leaves the ratio and therefore the sign unchanged. The
$0.60 guard does not touch ARGMAX at all. The only lever is **complement
fill rate**.

**Smallest change: record the first-leg price in the residual sampler** so
`pair_if_maker*` is computable. One field through the existing sampler,
read-only, no new service. It converts the unanswerable dwell question into
a measured curve on existing infrastructure, and that curve is what decides
whether a dwell/TTL change can deliver the needed 31%.

Economic justification for the one deployment it needs: without it the
autonomous path cannot be evaluated at all, and the live adapter below
cannot be justified or refused on evidence.

---

# Implementation plan — the controller-intent-to-live-execution adapter

**This is a plan, not a build.** It is gated on the evidence above (see
"When to build it"). Production stays disarmed.

## It is far smaller than "build an adapter"

The path is already constructed up to the final send, and the one missing
piece is named in the code itself. `submit_controller_intent` says it:

> *"everything that can be checked without touching the venue IS checked,
> and then it stops and says which single thing is missing"* —
> `what_is_missing: "a send that spends claim_submission in its own module,
> for CONTROLLER_GENERATED provenance"`

### Already exists — reuse, do not rebuild

| component | state |
|---|---|
| `IntentGate.evaluate` | the full 12-gate chain, provenance checked first |
| `prepare_controller_submission` | runs every gate, builds the **wire payload**, returns `prepared: True` |
| `controller_coid(intent)` | a provenance-distinct client order id |
| duplicate guard | `store.already_submitted(coid)` → `DUPLICATE_INTENT` |
| `REQUIRED_STORE_API` check | → `STORE_API_INCOMPLETE` |
| `ControllerLiveAuthorization` | capability object with `.covers(intent)` / `.describe()` |
| `_mark_uncertain` | already wired for an ambiguous POST |
| order registry | `l5_order_registry_server` serves `l5/live_orders.json` from `InventoryStore.live_orders()` |
| cancel path | `l5_cancel_mechanism.py`, already claim-gated |
| the one-send invariant | `tests/test_l5_one_submission_claim.py` **scans `src/` and fails if a third send site appears** |

## The six steps

**1. New module `src/l5_controller_submit.py` — one function, the send.**
In this order, and the order is the safety property:

```
submit(prepared, *, transport, store, mark_uncertain) -> result
  a. assert prepared["provenance"] == CONTROLLER_GENERATED   # never L5_NATIVE
  b. claim_submission(coid)        <-- SPENT IN THIS MODULE'S OWN BODY
  c. store.record_submit(coid, intent, ...)   BEFORE the POST
  d. POST through the existing PmusTransport  (no new transport, no new creds)
  e. on ACK:        store.attach_venue_id(coid, venue_order_id)
  f. on ambiguity:  mark_uncertain(...) and STOP; do not retry, do not
                    re-enter. An ambiguous POST halts re-entry pending
                    authoritative reconciliation.
  g. return the same result shape the L5 path returns
```

Step (c) before (d) is what makes an ambiguous POST recoverable: the coid is
durable before the wire call, so reconciliation can ask the venue about it.

**2. Registry and watchdog — verify, change nothing.** The registry already
derives from `InventoryStore.live_orders()`, and the watchdog additionally
requires the venue's own row to report `AUTOMATIC` and the same market.
A controller order submitted through the same transport is `AUTOMATIC`, so
**the existing watchdog can already cancel it** once `attach_venue_id` has
run. Deliverable here is a test, not a code change.

**3. Reconciliation — verify, change nothing.** `live_orders_pending_terminal`,
`live_orders_released_unacked` and `live_orders_adopted` key on the coid held
in the store. Add a test that a `controller_coid` survives a
restart → reconcile cycle and is adopted or released by the same rules, so a
controller order can never become an orphan the reconciler ignores.

**4. Register the new send site, deliberately.** Add the module to
`CLAIM_GATED_SUBMIT_SITES` *and* update
`tests/test_l5_one_submission_claim.py` to expect exactly three. That test is
the authority, not the tuple; editing it is the explicit act of admitting a
third send site and must be its own reviewable change.

**5. The capability, sourced from the owner — not an env var.** The Runner
must construct a `ControllerLiveAuthorization`, and the whole point of a
capability object is that a copied variable cannot forge it. Smallest safe
form: build it **only** from an explicit grant inside the owner-signed
campaign approval document (e.g. a `provenance: ["CONTROLLER_GENERATED"]`
field the `ApprovalGate` already validates and clamps), so the owner's signed
document remains the single source of live authority and a stray
`L5_*=1` cannot create one.

**6. Tests that must pass before it is armed.**
- provenance never appears as `L5_NATIVE` in tape, ledger or registry for a
  controller order, and no L5 row is ever fabricated;
- the 12-gate `GATE_ORDER` is unchanged and every gate still refuses in the
  same order for controller provenance;
- duplicate coid → `DUPLICATE_INTENT`, no second POST;
- simulated ambiguous POST → `mark_uncertain` set, re-entry refused;
- the watchdog cancels a controller order by venue id;
- a restart mid-flight leaves the order reconcilable, never orphaned;
- maker-only still holds: `DEF.enabled()` false keeps
  `maker_only_forbids_defensive_crossing` true.

## What is explicitly NOT in this plan

No new service. No new transport or credential. No taker path. No new action
type. No limit raised: exposure $20, `max_live_orders` 1, cash $12.00,
deficit $3.00, residual cap 3, daily $5, account-wide $18.6325, halt latch
and campaign budget all stay exactly as they are, and all of them run
**upstream** of the new send.

## When to build it — the economic gate

**Not yet.** Every policy is net negative today, and ARGMAX needs **~31%
more matched units per unit of residual** to break even. An adapter that
routes a losing policy to the venue converts modelled losses into realized
ones.

Build it when, and only when, the shadow shows on PMUS:

1. a policy with **positive total settled net** (pair surplus *minus*
   residual settlement), not pair surplus alone; and
2. a matched:residual unit ratio above **4.14:1** at the measured unit
   economics (+$0.268 matched / −$1.110 residual); and
3. that result over a sample large enough to separate it from the current
   spread — the six policies span −$0.08 to −$0.50 per window, so a handful
   of windows cannot distinguish a fix from noise.

The prerequisite for measuring any of this is step 5 of the evaluation
above: **record the first-leg price in the residual sampler** so
`pair_if_maker*` stops being null.
