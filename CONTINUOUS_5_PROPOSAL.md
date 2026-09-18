# CONTINUOUS-5 proposal — fill → refresh → changed action

**Not a transport canary.** Whether Gemini accepts an order is settled. The
sole research question is:

> **When a real Gemini fill changes inventory, does the L5 controller observe
> it and change its next action correctly?**

## Hashes

```
executable_sha  3194e6912c343a41c4aefe975f8ac7f5c975be5bb6cbb7bfe5ae9f535ac3abf1   NEW
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   UNCHANGED
bundle_sha      48f7f1f44ed7537f1baf5c73c91b0b4a4f9293fc9758dea3bad0259ad44ca56f   NEW
```

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

## What the window will and will not capture

The objective is `REAL FILL → AUTHORITATIVE REFRESH → CHANGED INVENTORY →
CHANGED L5 ACTION`, so what the artifacts hold decides whether it can be
answered at all. Measured against the ten required per-cycle items:

| required per cycle | captured? | where |
|---|---|---|
| authoritative YES/NO inventory before decision | **yes** | `refreshes[when="pre_decide"]`, `turns[].inventory` |
| working orders | **count only** | `refreshes[].resting`, `refreshes[].working`; full list at terminal `working_orders` |
| new fills since prior cycle | **derivable** | `refreshes[].n_fills` delta + `changed`; identities only in `after` |
| **L5 OPEN/EXTEND/COMPLETE/HOLD classification** | **NO** | — see below |
| prices and quantities | **placed only** | `wire[]`, ledger rows; the decided-but-held surface is dropped |
| effective matched-pair cost | **yes** | `turns[].inventory.effective_matched_pair_cost` |
| mutation plan | **counts only** | `turns[].plan` = `{keep, place, cancel, mutations}` |
| KEEP/CANCEL/PLACE results | **place/cancel yes** | `wire[]` acks + append-only ledger; KEEP is a count |
| remaining cohort budget | **yes** | `turns[].budget_left` |
| risk state | **inputs only** | inventory carries residual/imbalance/spend; `would_breach` strings on held rungs are dropped |

**The gap is real and it sits on the load-bearing field.** `decide()` builds the
turn — `actions` carrying `OPEN`/`EXTEND`/`COMPLETE` with leg, price, qty, and
`holds` carrying `HOLD` with `held_by` `OSCILLATOR`/`ENVELOPE`/`END_OF_WINDOW`
and, for `ENVELOPE`, the literal `would_breach` reason. `run_window` records
`_plan_summary(out["plan"])` — four integers — and discards `out["turn"]`.
`WindowGuard.record()`, which would have logged each decided action, has no
caller in the live path (only `continuous_preflight`), so `guard.log` holds
nothing but STOP events.

**What that costs.** The label is *reconstructible* for actioned rungs:
`classify()` is pure in the inventory residual, the residual is recorded per
cycle, so `classify(leg, recorded_inventory)` is exact. What is **not**
recoverable is why a cycle did nothing — OSCILLATOR suppression, an ENVELOPE
breach, or the end-of-window floor — and the prices of rungs that were decided
and then held.

**Why it is not fixed in this proposal.** Both fixes move a hash that has been
pinned for verification:

* recording `out["turn"]` inside `ws_continuous.run_window` moves
  `executable_sha` **and** `bundle_sha`;
* wrapping `driver.decide` with a transparent observer in the invoker — the
  same already-returned-value pattern that captured the place acks — leaves
  `executable_sha` and `envelope_sha` untouched and moves **only**
  `bundle_sha`.

The second is about ten lines and changes no control flow. It is offered, not
taken: changing a hash the owner pinned is the owner's call, and the standing
resolution is that evidence capture never edits the authorized executable.

## Unarmed production-shaped verification

`analysis/gemini/continuous_unarmed_20260918T191316Z.json`, real credentials
and readers, cohort path exercised end to end.

| required | result |
|---|---|
| recovery | `recovered: true` |
| readability | `order_history readable: true`; `active ok: true, status 200` |
| current inventory | `yes 0 / no 0 / n_fills 0` — authoritative, on a fresh contract |
| current working orders | `venue authoritative: 0 resting` |
| risk | gates evaluated against reconciled inventory, preflight `agrees: true, discrepancies: []` |
| budget | cohort `6`, consumed `0`, remaining `6` |
| cohort | loaded, predecessor **intact** (`89cf9de6…`, 12 consumed) |
| authorization is the sole blocker | `why_not` lists only `REQUIRED_AUTH_ID is None`, `AUTHORIZED_SHA is None`, transport arm |
| zero socket mutation | `wire: null`, `session: null` — no session constructed |
| zero new latch | `latch.consumed: false`; **5** latch files, no sixth |

Terminal state `READY_TO_MUTATE_BUT_UNAUTHORIZED`. 6322 tests pass.

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
> bundle SHA `48f7f1f44ed7537f1baf5c73c91b0b4a4f9293fc9758dea3bad0259ad44ca56f`,
> and mutation cohort `COHORT-2026-09-18-C5` with its bounded budget of 6
> slots. Its sole purpose is to observe real fill → authoritative refresh →
> changed inventory → changed L5 action. No second window, rollover, budget
> reset, scaling, SELL, IOC/FOK, taker crossing, forced flatten,
> strategy/controller changes, or risk-envelope changes without new approval.
> The historical 12/12 ledger stays byte-for-byte and CONTINUOUS-2 through -4
> remain permanently spent.

**No CONTINUOUS-5 will run without that statement.**
