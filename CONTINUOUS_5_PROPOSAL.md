# CONTINUOUS-5 proposal — fill → refresh → changed action

**Not a transport canary.** Whether Gemini accepts an order is settled. The
sole research question is:

> **When a real Gemini fill changes inventory, does the L5 controller observe
> it and change its next action correctly?**

## Hashes

```
executable_sha  3194e6912c343a41c4aefe975f8ac7f5c975be5bb6cbb7bfe5ae9f535ac3abf1   UNCHANGED
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   UNCHANGED
bundle_sha      61c7abf3f82d2c2fee30b544fa7ac23b0e4550a5a46cf5b4229743d61dec8c61   NEW
```

The bundle moved once more, and **only** the bundle: the decision capture
lives in `run_continuous.py`, which is a bundle member. `48f7f1f4…a56f` is
superseded.

`envelope_sha` is unchanged because **no bound moved**. The driver hash moved
for one reason: `Driver` gained `max_actions`, which a cohort may use to
**tighten** the envelope and never to extend it. The bundle moved because
`cohort.py` joined it — a file that grants budget carries a safety property.

Prior lineage `4d9f2e4c` / `a902bd4a` was verified intact before any change.

## Cohort

```
cohort_id             COHORT-2026-09-18-C5
budget                6   (of the envelope's 12)
consumed / remaining  0 / 6
ledger                analysis/gemini/cohorts/COHORT-2026-09-18-C5/ledger.jsonl
predecessor           analysis/gemini/continuous_ledger.jsonl
predecessor sha256    89cf9de6d949cf8838afe1aca18ff7c6d1cacf3472e4562c25439c2064ea40a7
predecessor consumed  12   (CONTINUOUS-2, -3, -4 — permanently spent)
predecessor intact    true
```

Six is chosen for the question, not for comfort: two places to get a fill,
a refresh, the changed action, and a withdraw. It is half the envelope.

**No rewrite** — the historical ledger is hashed and never opened for writing;
the invoker refuses *before the latch* if it ever differs. **No rollover** —
the budget is fixed at creation; unused slots never carry, spent ones never
follow. **No automatic creation** — `create()` refuses a second call rather
than no-opping, and a test asserts nothing in the package calls it.

A cohort asking for more than `CP.MAX_ACTIONS` is **refused, not clamped**:
silently granting less than was asked for leaves a caller believing it holds
budget it does not.

## The exact corrected action at the CONTINUOUS-4 state

Inventory replayed exactly: `no_qty 0.01`, `residual −0.01`, `imbalance 0.01`,
`spend 0.0014`, `avg_no_cost 0.14`, `n_fills 1`. The book is **reconstructed**
(the 17:28:05 book was never recorded) and is held **identical** across both
runs, so inventory is the only variable.

| leg | price | size | what shipped (flat 0/0) | **corrected (reconciled)** |
|---|---|---|---|---|
| YES | 0.91 | 0.01 | `OPEN` | **`COMPLETE`** |
| NO | 0.07 | 0.01 | `OPEN` | **`EXTEND`** |

Same prices, same sizes — **the L5 action classification changes**, which is
the behavioural model itself. `classify()` takes *no price argument*: a
controller handed a fictitious flat book cannot classify correctly however
good its pricing is. That is why the reconciliation fix is the whole story.

## The pair-cost question: RULED ON 2026-09-18 — no ceiling

**There is no pair-cost gate anywhere in this path, and by owner decision none
is added.** `would_breach` bounds imbalance, per-leg size and total spend.
Nothing bounds what a `COMPLETE` costs relative to the leg it completes, and
that is now deliberate rather than merely unexamined.

The corrected controller emits `COMPLETE` for YES at **0.91** against a NO held
at **0.14** — a **1.05 pair**. It is permitted.

Why, in one line each — full evidence in `L5_COMPLETE_BOUNDARY_FINDINGS.md`,
recorded durably in `L5_OPERATING_MODEL.md` §2d:

* 33,765 canonical L5 COMPLETEs analysed; **42.75% above $1.00**, **33.60%
  above $1.05**.
* Cheap held leg (0.10–0.25) → median pair **1.0939**, median completing price
  **0.90**.
* Our narrow comparable state (n=1,125) → **78.76% above 1.05**; p10 already
  above $1.00.
* BTC-5m, our venue analogue → **40.46%** of completes above 1.05.
* Expensive completes were **usually worked down**: 97.21% followed by more
  fills, 96.78% in windows ending better.
* So a `<= 0.96` gate would forbid roughly four completes in five here — a
  **fidelity regression**, and a **RISK OVERLAY** if imposed anyway.

**This does not claim >$1 completes are profitable on Gemini.** Fidelity and
profitability are separate claims. Measuring the second is what the live window
is for. `tests/test_gemini_cohort_and_cycle.py::
test_no_pair_cost_gate_exists_in_this_path` pins the absence of every
enumerated ceiling — 0.96, 0.98, 1.00, 1.05 — so one cannot reappear silently.

## What the window captures — the gap is closed

`DecisionObserver` in `run_continuous.py` wraps three seams and changes none
of them. Each wrapper makes the **same call with the same arguments, returns
the same object** (asserted by identity, not equality), propagates every
exception untouched, and adds no decision, no frame and no bound. A failure to
*record* is written into the row rather than raised, because a recorder that
can stop a live window is worse than no recorder.

| required per cycle | where it now lands |
|---|---|
| cycle / timestamp / TTE | `decisions[].cycle`, `.utc`, `.tte_s` |
| authoritative pre-decision YES/NO inventory | `decisions[].inventory_before_decision` — read at the call, after the `pre_decide` refresh |
| matched inventory and directional residual | same record: `matched_qty`, `directional_residual` |
| imbalance and total spend | same record: `inventory_imbalance`, `total_spend_usd`, plus `effective_matched_pair_cost` |
| current working orders | `decisions[].working_orders_before` — full rungs, not a count |
| newly reconciled fills | `authoritative_reads[].new_fills` (**multiset** difference) and `.new_fill_order_ids` |
| desired rung side / price / quantity | `decisions[].actions[]` — `leg`, `outcome`, `price`, `qty`, `rung_index`, `ticks_behind_bid` |
| exact OPEN / EXTEND / COMPLETE | `decisions[].actions[].action` and `.classification` |
| every HOLD with price and literal reason | `decisions[].holds[]` — `held_by` ∈ `OSCILLATOR` / `ENVELOPE` / `END_OF_WINDOW`, and for `ENVELOPE` the verbatim `would_breach` sentence |
| resulting KEEP / CANCEL / PLACE plan | `decisions[].plan` — itemized, not counted |
| post-cancel authoritative refresh | `authoritative_reads[]` with the driver's **own** `when` label joined on |
| inventory used for any subsequent PLACE | `mutations_observed[].inventory_at_call`, read *before* the inner call |
| mutation slots consumed / remaining | `budget_before_decision`, `budget_after_decision`, `budget_at_call`, `budget_after_call` |

**Why `at_call` is read before the call and not after.** The driver spends the
slot *before* the frame goes out, so a budget read afterwards is the budget
after the spend, and inventory can move under a fill in between. Both readings
are kept.

**Why the phase label is joined, not inferred.** The read order could be used
to infer `pre_decide` / `post_cancel`, but the driver already states it, so the
observer copies the authoritative label across. When the two lists do not line
up, `refresh_link` says so instead of guessing.

**Unreadable is still not empty** in the capture: a failed read records
`fills_readable: false` and `new_fills: null`, never `[]`.

### The regression that makes it evidence

`tests/test_gemini_decision_observer.py` — 17 tests. The load-bearing one runs
the **same scripted window twice**, once observed and once not, and compares
all three durable surfaces **byte for byte**: the driver's own report, the
frames that reached the session, and the append-only mutation ledger. Only
uuid4 client-order-ids and wall-clock stamps are canonicalised, renamed in
first-appearance order so that how many orders, in what order, at what prices
still has to match. The run is a real one — it cancels and places, and spends
2 slots.

Alongside it: `decide` returns the driver's own object by identity; exceptions
propagate untouched (the driver's RECONCILE path depends on seeing them); an
exploding recorder does not stop the window; one `decide` call per cycle and
not one extra frame; and a structural check that the recorder composes no
frame, reads no credential and restates no envelope bound.

The classification test runs the **real** controller at the CONTINUOUS-4 state
— holding NO 0.01 @ 0.14 it records `COMPLETE` for YES 0.90 and `EXTEND` for
NO 0.07, with the inventory the decision actually saw.

## Final unarmed production-shaped verification — the eight expected outcomes

`analysis/gemini/continuous_unarmed_20260918T203645Z.json`, Actions run
35392268587 on `99531517`. Real credentials, real readers, the same entry
point the armed step calls, on `GEMI-BTC05M2609182040-UP` at TTE 194.7s.

| expected | result |
|---|---|
| executable SHA stays `3194e691…abf1` | `driver_executable_sha` matches |
| envelope SHA stays `b3bff654…aa17` | `driver_envelope_sha` matches |
| bundle SHA changes | **`61c7abf3…8c61`**, was `48f7f1f4…a56f` |
| cohort `COHORT-2026-09-18-C5`, 6/6 | budget 6, consumed 0, remaining 6 |
| historical ledger pinned and 12/12 | recorded == current == `89cf9de6…`, 12 == 12, `intact: true` |
| zero mutation frames | no session was ever constructed |
| no C5 latch consumed | `latch.consumed false`; 5 latch files, no sixth |
| terminal `READY_TO_MUTATE_BUT_UNAUTHORIZED` | exactly that |

Refused for three reasons and nothing else: `REQUIRED_AUTH_ID is None`,
`AUTHORIZED_SHA is None`, `GEMINI_CANARY_LIVE is not YES`. Preflight
`agrees: true, discrepancies: []`; pre-latch recovery `recovered: true`,
`budget_left 6`, `checked_before_latch` and `checked_before_authorization`
both true. **6,342 tests pass.**

**What this artifact does NOT show, stated plainly.** It contains no
`decisions` key — and that is correct. The run refuses at the authorization
gate *before* the Driver is constructed, so the observer is never attached and
its absence from an unarmed artifact proves nothing either way. A capture that
is only exercised by unit tests is `apply_fill` again: correct code with no
caller for a whole live window.

So the wiring is proven separately, by an integration test that drives the
**real `_build`** past every gate with fakes and runs the **real Driver**
through a real window at the CONTINUOUS-4 state. It asserts the report
actually carries `decisions`, `authoritative_reads`, `mutations_observed`,
`decision_capture` and `refresh_link`; that cycle 1 saw `no_qty 0.01` and
`directional_residual -0.01`; that the YES rung is labelled **`COMPLETE`** at
0.90; that the read carries the driver's own `pre_decide` label with
`n_fills 1`; and that the mutation recorded `no_qty 0.01` as the inventory in
force at the call.

### Reconciliation with the earlier checks

Checks 4, 5, 6 and 8 from the previous run are re-verified above. Checks 1 and
2 are re-verified and unchanged; check 3 has deliberately moved. Check 7's
pre-latch half is in this artifact; its per-cycle half is still pinned by test
and is what the first authorized window converts to observed.

## The armed step was not ready, and now is

The armed step invoked `run_continuous` **without `--cohort`**. It would have
bound the historical ledger — 12/12 permanently spent — so an authorized window
would have died on budget at the moment it mattered, and the repair would have
been improvised under arm-time pressure. It now names
`COHORT-2026-09-18-C5`.

It remains **unarmable**: one `GEMINI_CANARY_LIVE` assignment in the whole
workflow, a sentinel `--auth-id NO-AUTHORIZATION-IN-FORCE`, and driver
constants still `None`. Two tests pin both facts, including that a preparation
commit never installs an authorization id.

## CONTINUOUS-4 accounting (unchanged, cashflow continues independently)

SUBMITTED → FILLED → **SETTLED**. Cost `-0.0106`, payout `+0.0100`,
**expected settlement P&L −$0.0006**. **REALIZED remains $0.00** — no
attributable cashflow: `/v1/settlements` 404,
`/v1/prediction-markets/settlements` 404, positions 0 rows, no redemption row
in `/v1/mytrades`. This does not block the engineering stage and the
reconstruction is never labelled realized.

## Proposed authorization statement

> I authorize ONE bounded live Gemini BTC5m continuous-inventory window using
> authorization ID `OWNER-2026-09-18-GEMINI-CONTINUOUS-5`, executable SHA
> `3194e6912c343a41c4aefe975f8ac7f5c975be5bb6cbb7bfe5ae9f535ac3abf1`, envelope
> SHA `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17`,
> bundle SHA `61c7abf3f82d2c2fee30b544fa7ac23b0e4550a5a46cf5b4229743d61dec8c61`,
> and mutation cohort `COHORT-2026-09-18-C5` with its bounded budget of 6
> slots. Its sole purpose is to observe real fill → authoritative refresh →
> changed inventory → changed L5 action. No second window, rollover, budget
> reset, scaling, SELL, IOC/FOK, taker crossing, forced flatten,
> strategy/controller changes, or risk-envelope changes without new approval.
> The historical 12/12 ledger stays byte-for-byte and CONTINUOUS-2 through -4
> remain permanently spent.

**No CONTINUOUS-5 will run without that statement.**
