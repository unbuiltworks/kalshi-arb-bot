# Decision freshness: optimisations 1 and 2

**ACCEPTANCE NOT MET by optimisations 1 and 2 alone. p95 book age at decision
falls from 2433 ms to 1140 ms — a 53% reduction — against a <1000 ms target.
The binding constraint is the transport rate limiter, and raising it is not a
change I will make unilaterally.**

## The measured cause is the rate limiter, not the network

`l5_pmus_transport.RateLimiter` is **one shared token bucket, rps=1.5,
burst=4.0**, and `before()` calls `self._sleep(wait)` **inside the decision
path**. R5 spent 1,112 authoritative status reads over 468 ticks = 2.376
reads/tick, plus one POST per submitting tick.

At 1 read + 1 POST = 2 tokens/tick against rps=1.5, the sustainable tick
period is **1.333 s** before any network time. That is the floor, and it is
what produced R5's 1.39–6.98 s book age.

## Optimisation 1 (parallelise) contributes exactly zero, measured

| variant | median | p95 | max | throttled waits | wall |
|---|---|---|---|---|---|
| BEFORE 2.38 reads/tick, serial | 1740 ms | **2433 ms** | 2500 ms | 1112 | 778.8 s |
| AFTER 1.00 reads/tick (cache), serial | 913 ms | **1140 ms** | 1167 ms | 466 | 348.0 s |
| FANOUT ONLY 2.38 reads/tick, 4 threads | 1740 ms | **2433 ms** | 2500 ms | 1112 | 778.8 s |
| BOTH 1.00 reads/tick, 4 threads | 913 ms | **1140 ms** | 1167 ms | 466 | 348.0 s |

Fan-out is byte-identical to serial. **Concurrency does not create tokens**:
four threads simply queue on the same bucket. `STATUS_FANOUT` is therefore
implemented, tested for correctness and equivalence, and **defaulted to 1**,
so the default does not imply a benefit that does not exist.

Optimisation 2 is the entire gain: reads/tick 2.376 -> 1.0, p95 2433 -> 1140 ms,
window wall time 778.8 s -> 348.0 s.

## What would meet the target (informational; nothing changed)

Holding 1 read/tick:

| rps | median | p95 | verdict |
|---|---|---|---|
| **1.5 (current)** | 913 ms | **1140 ms** | FAIL |
| 2.0 | 740 ms | 980 ms | PASS |
| 3.0 | 587 ms | 807 ms | PASS |
| 4.0 | 500 ms | 730 ms | PASS |
| 6.0 | 420 ms | 647 ms | PASS |

Two ways to pass, both explicitly out of scope for this change:

1. **Raise `rps`.** A venue-policy and safety decision — the limiter also
   carries the 429 cool-off that protects every other read path — so it needs
   explicit authorization and evidence about the venue's real ceiling.
2. **Stop spending a token per tick per order**, i.e. drive authoritative
   lifecycle from the authenticated WS order stream with REST as fallback.
   That is the deferred experiment.

## What was implemented

`src/pmus_native_money_path.py`:

* **`_status_once`** — pass-scoped authoritative read. **Only TERMINAL
  verdicts are cached**, and that is a safety property, not an optimisation
  detail: terminal is monotonic, so reusing it within a pass cannot be stale.
  A WORKING verdict is **never** cached, because a cancel attempted later in
  the same tick may have been applied at the venue even when its transport
  call failed; serving a cached WORKING across that would make us believe an
  order is live when it is not. UNKNOWN is never cached either, so an
  unreadable order stays re-readable.
* `_resolved_this_pass` was **written on line 347 and read nowhere** — a
  fifth dead seam, benign, and exactly the re-read guard optimisation 2 asks
  for. It is now consulted, and covers the cancel pre-check and the
  cancel-failure verdict paths.
* **`begin_pass()`** — called at the top of every tick; the caches live
  exactly one pass.
* **`_read_statuses`** — order-independent fan-out, verdicts applied
  afterwards in registry order so the booking sequence is identical.
* **`timing_report()`** — `book_received`, `decision_start`,
  `status_reconciliation_start/end`, `lock5_start/end`, `mandate_start/end`,
  `preview_start/end`, `post_start/end`, `ack`, plus
  `book_age_ms_at_decision`, `book_age_ms_at_post`, `status_reads` and
  `status_cache_hits`. Missing timestamps report `None` rather than a wrong
  number. Attached to each tick result as `timing`.

## Preserved, and asserted by test

UNKNOWN fail-closed · no replacement while ownership is uncertain ·
authoritative fills booked exactly once (registry high-water mark, increment
only) · partial-fill delta accounting · terminal reservation release ·
mandate/risk invariants · theta 0.96 · variant B · hold 60 s ·
residual_cap_k 2 · PROFIT_LOCK $5 · floor guard OFF · complement bias OFF ·
q10 · the 440c9696 completion-ladder and cancel-recovery fixes.

Full suite: 10,030 passed, 20 failed — exactly the pre-existing 20. No new
failure.

## Live shadow run: NOT YET RUN

The consecutive-window shadow run (median/p95/max book age, hypothetical
submissions, decision-book vs arrival-book PDI crossings, rejection-risk
rate, completion opportunities, residual/matched trajectory, action-by-action
against the pre-fix engine) has **not** been run. Reporting it as done would
be false. It needs a read-only deployment, which is the next step and needs
no new authorization — but it should wait until the acceptance question above
is settled, because a shadow run at p95 1140 ms measures a configuration we
already know fails the target.

## Preserved research finding

**Directional residual is not, by itself, a clone failure.** Both our
canonical sample and the independent reconstruction reject a tight
rebalancing interpretation of L5. `residual_cap_k`, forced underweight
acquisition and trade reversal remain NOT implemented. The ~64% vs ~53%
EXTEND-rate difference is a market-density artifact (our 1–5 fills/market
bucket measures 64.4%) and is recorded for later reconciliation; it does not
block latency work.
