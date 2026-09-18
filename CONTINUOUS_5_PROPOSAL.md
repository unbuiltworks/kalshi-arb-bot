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

## Something you should decide before authorizing

**There is no pair-cost gate anywhere in this path.** `would_breach` bounds
imbalance, per-leg size and total spend. Nothing bounds what a `COMPLETE`
costs relative to the leg it completes.

In the replay above, the corrected controller emits `COMPLETE` for YES at
**0.91** against a NO held at **0.14** — a **1.05 pair**, still above $1.00.
The `<= 0.96` discipline is policy in `CLAUDE.md`, not code here; the only
`0.96` in the tree is a measured statistic about PMUS books in
`lane_gates.py`, which nothing imports.

So the fix makes the controller **see** the inventory and **label** the action
correctly. It does **not** stop it completing a pair above $1.00. CONTINUOUS-4's
outcome could recur with the controller fully informed.

Adding a gate is a risk-limit change and is **deliberately out of scope** here.
A test now pins the absence so it cannot be discovered by another live window.
**This is a decision for you, not an engineering default.**

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
