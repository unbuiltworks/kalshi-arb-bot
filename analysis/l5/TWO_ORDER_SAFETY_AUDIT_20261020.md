# L5_MAX_LIVE_ORDERS=2 — safety audit and campaign proposal

Prepared 2026-10-20 against deployed commit `f6b67aa2`.
**Status: PROPOSAL ONLY. Nothing changed. Not activated.**

---

## Part 1 — Safety audit: does the implementation support N=2?

**Verdict: YES. The gating model was written for N outstanding orders, not one.**

| Concern | Finding | Evidence |
|---|---|---|
| Concurrency bound | `live_order_count >= self.max_live_orders` — a count comparison, N-agnostic | `l5_live_execution.py:520` |
| Uncountable book | `AT_LIMIT_WHEN_UNCOUNTABLE = 1_000_000` — "I could not count" always reads as at-limit | `l5_live_execution.py:386` |
| **Aggregate cash** | `worst_case_deficit` takes `outstanding: Sequence[...]` and maximises over **all four corners** — nothing fills / only UP / only DOWN / everything fills — returning the worst. Documented "exact rather than merely conservative" | `l5_live_validation_gates.py:296` |
| Liquidity assumption | *"OUTSTANDING ORDERS ARE AN ADVERSARY, NOT AN ASSUMPTION. Each may or may not fill."* | same |
| Outstanding collection | `outstanding_for_gates()` iterates **all** live orders account-wide, returns a list | `l5_live_execution.py:4069` |
| **Partial fills** | charges `remaining`, not original qty — only the still-fillable part is counted | `l5_live_execution.py:4079` |
| Unreadable price | → `UNKNOWN_PRICE_WORST_CASE` | `l5_live_execution.py:4084` |
| Unreadable side | charged as a **short**, worst case $1/share | `l5_live_execution.py:4075` |
| **Cancellation** | exactly one POST route, `/v1/order/{id}/cancel`, regex-anchored `\Z`. No market-scoped cancel, no account sweep — deliberately, because the account is SHARED | `l5_watchdog.py:36,65` |
| Independent reconciliation | `open_orders()` returns a List; sweeps report `still_open`, `overdue`, `unverified`, `not_ours`, `cancelled` as lists | `l5_watchdog.py:173` |
| Fee rounding at N=2 | already reasoned: *"with max_live_orders at 2 the worst unmodelled rounding is a few cents"*, with `fee_extra_per_order_usd` to price it | `l5_live_validation_gates.py:290` |

### The cash obligation does NOT rise at N=2

From the live 2300z plan:

```
cash_gate_obligation_usd : 4.650000
cash_basis : "a resting DOWN leg is a BUY_SHORT and the cash gate charges it
              a dollar a share; sizing uses that basis so the plan the gate
              sees is the plan this built"
```

`0.24 × 3.75 + 1.00 × 3.75 = 0.90 + 3.75 = $4.65`.

The gate **already reserves for both legs of the pair** regardless of whether they
rest together or in sequence. N=2 changes *when* orders rest, not *how much*
cash is committed. This is the single most important finding: the move does not
increase aggregate exposure.

### The one genuinely new state

At N=2 both legs can be working simultaneously, so the second can fill while
the first is still *partially* unfilled — a transient imbalance the sequential
path never produced. The deficit gate's "everything fills" corner does bound
it, so it is bounded rather than open-ended. But it is a state the live system
has never actually been in, and it is the thing a shadow run must exercise.

---

## Part 2 — The comparison cannot be run, and the reason matters

**No local BTC15m recordings exist.** `find data analysis -name "*btc-updown-15m*"`
returns 0. The 1.3 GB in `data/` and 399 MB in `analysis/` hold other series.
The recordings live on production volumes.

**More importantly, the campaign produced no case where N=2 would have helped.**

| Window | Outcome under N=1 |
|---|---|
| 2245z | pair completed, +$0.4368 |
| 2300z | pair completed, +$0.1875 — UP filled 23:02:41, DOWN filled 23:06:32, both inside the window |

N=2 helps only where a first leg fills and the complement never returns before
settlement. **That did not happen once.** Both completed pairs completed under
N=1; 2315z's single order never filled, so it created no exposure to complete.

So there is currently **zero live evidence that N=2 improves completed-pair
profitability**, because N=1 was never the binding constraint on any pair we
completed. The sequential path's 3m42s wait in 2300z was not a cost — it was
the refusal-to-chase that earned the $0.1875.

What N=2 would plausibly buy is better queue position on the complement
(resting from the start rather than joining the queue after the first fill).
In 2300z the complement filled in **9 seconds** once placed, so the headroom
there was negligible. Whether it matters in slower windows is exactly the
unmeasured question.

---

## Part 3 — Proposed campaign (NOT ACTIVE)

Identical to the completed campaign in every financial limit; one execution
parameter differs.

| Parameter | Value | Change |
|---|---|---|
| `L5_MAX_LIVE_ORDERS` | **2** | **the only change** |
| Strategy | TWO_SIDED | unchanged |
| Series | `cpc-btc-updown-15m-` | unchanged |
| Max windows | 4 | unchanged |
| Max total exposure | **$5** | unchanged |
| Daily realized-loss halt | **$5** | unchanged |
| Pair-cost ceiling | **$0.96** | unchanged |
| OPEN / COMPLETE / REPLACE | 8 / 12 / 16 | unchanged |
| Total submission backstop | 28 | unchanged |
| Completion reserve | 2 per obligation | unchanged |
| Duration | 4 hours | unchanged |
| Cash cap | $4.65 (tighter than $5) | unchanged |

Requires a **fresh approval document, fresh nonce, fresh experiment id**. The
completed campaign stays closed, exhausted 4/4, its ledger and claims
preserved. Nothing is reset or extended.

### Recommended sequencing

1. **Shadow first.** Run N=2 shadow-only against live books for a session,
   with the specific objective of exercising the transient-imbalance state in
   Part 1. No capital.
2. Only then a bounded live campaign, if shadow shows the new state handled
   cleanly **and** shows windows where the complement failed to return under
   N=1 — i.e. evidence the change has something to fix.

### My recommendation

**Do not go live on N=2 yet.** The implementation is safe, but "safe" is not
"better". Every pair this campaign completed, completed under N=1, so the
proposed change currently addresses a failure mode we have not observed. Going
live would spend four windows and real capital to test a hypothesis that a
shadow run can test for free.

If you want to proceed to live anyway, the configuration above is ready and
preserves every financial limit.
