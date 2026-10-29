# Book-driven completion: implemented, gated OFF, and the comparison is negative

`src/l5_book_completion.py` (new, 240 lines), `tests/test_l5_book_completion.py`
(28 tests), `analysis/l5/campaign9/book_completion_compare.py`.
**Nothing deployed. No capital. No limit changed.**

## The minimum code change

**One new pure decision module and nothing else.** No executor change, no
runner change, no new service. The reason it is this small is that the
architecture already anticipated this feature:

`src/l5_intent_gate.py` defines `CONTROLLER_GENERATED` provenance beside
`L5_FOLLOWED`, runs both through the identical twelve-gate chain
(`PROVENANCE_KNOWN → CURRENT_WINDOW → SELL_IN_PROBE_SCOPE → OPERATOR_HALT →
SHADOW_ONLY_LOCK → DURABLE_HALT_LATCH → OWNER_ARM_TOKEN → TRANSPORT_ARMED →
ACCOUNT_WIDE_EXPOSURE → ORDER_REGISTRY_OWNERSHIP → RECONCILIATION_CERTAIN →
EXECUTOR_BOUNDARY`), and already has a `ControllerLiveAuthorization`
capability object for the boundary. What was missing was only the **decision
producer**. That is what I wrote.

Against the eight requirements:

| | |
|---|---|
| 1. L5 entry path preserved | Untouched. Not one line of `l5_live_execution.handle` or `Runner.handle` changed. |
| 2. Independent book-driven path for owned inventory | `decide()` takes inventory + book + clock. It needs no L5 action and has no access to one. |
| 3. Net-outcome evaluation, no crossing, no relaxation | Economics come from `TS.complement_order` — **the same function the L5 path calls**, so the two cannot price a completion differently. Then a hard `pair_own_cost ≤ 0.9532` test (the settled break-even). `_would_cross` refuses any price that would take the touch, asserted on the price and never inferred from a mode string. It holds no limit of its own; every number is passed in from the object that enforces it. |
| 4. Multiple cycles per window | `decide()` is stateless and completes the **unmatched remainder** only, so it re-arms after each cycle. `ALREADY_MATCHED` is what stops it becoming a new first leg — the bug that once stopped accumulation dead at one pair. |
| 5. Reuse, no new services | Reuses `TS.complement_order`, `TS.cancel_cutoff`, `TS.complete_cutoff_s`, `TS.MAX_BOOK_AGE_S`, the existing book snapshot shape, the existing intent gate. |
| 6. Idempotent, bounded, safe | Below. |
| 7. Comparison on the recorded sequence | Below — and it is **negative**. |
| 8. Feature-gated OFF | Below — **structurally**, not by flag. |

## Requirement 6, specifically

| risk | guard | test |
|---|---|---|
| stale book | `stream_age_s > MAX_BOOK_AGE_S` (2.0 s) holds; a **missing** age also holds rather than assuming fresh | 3 tests |
| suspected feed gap | `gap_suspected` holds | 1 |
| duplicate orders | `decision_key` is deterministic on market/side/qty/price, and an order already resting at or better holds `ALREADY_RESTING_AT_OR_BETTER` | 4 |
| chasing | repricing only ever goes **cheaper**; never upward | 1 |
| excess residual | completes the unmatched remainder only, never more; `residual_budget_usd` passed to `complement_order` | 2 |
| late-window submission | horizon is the **earlier** of `complete_cutoff_s()` (720) and the cancel margin (~600) — a guard on the later one would arm entirely inside dead time | 2 |
| requote storms | `DECIDE_EVERY_S = 5.0` cadence floor; every requote resets queue priority, and the dwell evidence says longer rests buy nothing | 1 |
| crossing | band sweep over 91 book states asserts **no accepted decision ever crosses** | 2 |
| crashes on bad input | holds rather than raising on unparseable quantities, costs, books, ticks | 1 |

## Requirement 8: the gate is structural, not a flag

`L5_BOOK_COMPLETION` defaults **OFF** and only turns on *evaluation and
recording*. It does not make anything submittable. Reaching the wire needs a
`ControllerLiveAuthorization`, which is a **capability object a caller must
pass** — `Runner` passes none, so in production this path is not disabled,
it is **absent**. `tests/test_l5_intent_gate.py` already asserts no module
under `src/` ever constructs one, and a test here asserts the new module
contains no transport, no submit, no claim and no HTTP.

## Requirement 7: the comparison, and it does not support shipping this

Same recorded sequence, same `decide()`, only the trigger differs: CURRENT
at the measured 34 s action cadence, BOOK at 5 s.

| window | samples | span | CURRENT evals | BOOK evals | **accepted (either)** | queue-reachable |
|---|---|---|---|---|---|---|
| 2100z | 15 | 92.7 s | 3 | 15 | **0** | 0 |
| 2115z | 59 | 382.5 s | 10 | 59 | **0** | 0 |

**Book-triggering produced 5–6× the evaluations and zero additional
accepted completions.** Why, from the refusal counts:

- **2100z** — 8 `PAIR_ABOVE_BREAKEVEN`, 7 `ECONOMICS:PRICE_OUT_OF_BAND`. The
  complement sat at 0.94–0.97 own; with any first leg the pair is well above
  0.9532. There was nothing to catch.
- **2115z** — 36 `WOULD_CROSS`, 23 `ECONOMICS:PRICE_OUT_OF_BAND`. Here the
  complement *had* collapsed to 0.04–0.33, so the economics were excellent —
  and the maker quote behind the touch would have been **through the book**.
  The opportunity was real and **not reachable as a maker**.

So on the only recorded sequence available, the trigger was **not** the
binding constraint. The band and the maker-versus-cross constraint were.
Expected net P&L from this change on this evidence: **$0.00**. Residual
risk change: **none**, because nothing is accepted.

**Honest limits of the comparison.** The tape carries modelled shadow
inventory, not venue positions, so the decision counts describe the book's
opportunity set rather than money we held; the first leg's cost is absent
and is swept across the 0.24–0.46 range our real campaigns opened at (the
result is identical at all four); and 74 usable samples across two windows
is a thin sequence. It is nonetheless the only recorded sequence that
samples the book *between* L5 actions, which is the thing being tested.

## Validation plan

**Stage 1 — record-only, zero capital, next authorized campaign.**
Set `L5_BOOK_COMPLETION=1` with no capability object. The path evaluates
every 5 s against **real** inventory, **real** books and **real** order
state, and emits its decision and refusal class. That produces the join no
existing tape has: the Oct-24 live tapes have real inventory but their book
rows fire per action; the Oct-26 sampler sees between actions but has
modelled inventory and never rests an order. Acceptance: ≥200 decisions
recorded against real inventory, with the refusal histogram.

**Stage 2 — decide on that evidence, not on this.** Ship the submitting path
only if Stage 1 shows accepted, queue-reachable decisions that the
action-triggered engine missed. If Stage 1 looks like the table above, the
right answer is to delete the submitting path and keep the recorder.

**Stage 3 — if and only if Stage 2 clears:** one market, one outstanding
order, existing $20 exposure / $12 cash / $5 daily / $20 cumulative / $3
residual, unchanged. Separate owner authorization, and it needs a caller to
construct the capability object, which is a code change reviewed on its own.

**My recommendation: run Stage 1 and nothing further.** The code is written,
tested and safe, and the evidence so far says the gap is not where we
thought. I would rather find that out for $0 than ship a path the one
available replay scores at zero.

Stage 1 needs the same thing the last two investigations needed: the
authorization installed so a campaign can run. That is now the third
distinct question blocked on it.
