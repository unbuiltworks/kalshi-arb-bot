# Separate finding: why a mandate refusal surfaces as `CAMPAIGN_STOP:HALT`

**Asked by:** owner, 2026-09-16 — "investigate why
`MandateRefusal:max_concurrent_markets_exhausted` becomes `CAMPAIGN_STOP:HALT`
… do not change that behavior unless the existing semantics and tests
demonstrate the safe correction; report it separately if it requires another
material change."

**Recommendation: do not change it.** Reported here rather than patched.

## The chain

1. `GuardedTransport.request()` raises `MandateRefusal`
   (`src/pmus_live_mandate.py:648`). It subclasses `Exception`.
2. `pmus_spring_l5_session.run_session()` catches `Halt` specially →
   `SPRING_SAFE_REFUSAL`. `MandateRefusal` is **not** a `Halt`, so it falls to
   the generic `except Exception` (`src/pmus_spring_l5_session.py:3836`) →
   `status: "HALT"`, `reason: "MandateRefusal:<why>"`.
3. The campaign's stop-set (`src/pmus_l5_continuous_session.py:3867` for a
   resumed market, `:4072` for a new one) contains `"HALT"` →
   `status = "CAMPAIGN_STOP:HALT"`, `break`.

So this is the **generic unexpected-exception path**, not a purpose-built
mapping. The status field says "something went wrong we don't understand"
where the truth is "the mandate correctly declined this submission." The
reason string does carry the real cause, and `q1.ledger_append({"kind":
"halt", …})` records it durably, so nothing is actually lost — but the label
is imprecise.

## Why it stays imprecise

The only reclassification the existing semantics offer is `SPRING_SAFE_REFUSAL`,
and it is **strictly less safe**. `SPRING_SAFE_REFUSAL` appears nowhere in
`pmus_l5_continuous_session.py` — it is in no stop-set — so it lets the campaign
**continue**. Routing mandate refusals there would mean:

* `max_total_markets_exhausted` no longer ends the campaign;
* `max_total_spend_exhausted` no longer ends the campaign;
* `max_daily_realized_loss` no longer ends the campaign;
* `consumption_unreadable` — the fail-closed case — no longer ends the campaign.

That is precisely the 2026-09-16 breach shape: **authorization to start read as
authorization to continue.** The current behaviour is fail-closed and correct
in direction; only its name is wrong.

`_read_halt()` — the one path that lets a `HALT` continue as a bounded wait —
requires `READ_HALT_MARK` (`GET_RETRIES_EXHAUSTED`) in the reason, so a mandate
refusal cannot take it. Verified, not assumed.

A third option — a new `CAMPAIGN_STOP:MANDATE_REFUSED` status — would be
cosmetic only, and would need a new constant threaded through both stop-sets,
the supervisor, the alert paths and their tests. That is a material change with
no safety gain, which is why it is reported rather than made.

**Note on the underlying trigger:** with the concurrency accounting corrected,
`max_concurrent_markets_exhausted` no longer fires spuriously. If it fires now
it means the campaign genuinely tried to open a second market while one was
still live — a real state inconsistency, and stopping is the right response.

## Pinned, so it cannot drift

Three tests in `tests/test_mandate_concurrency_lifecycle.py`:

* `test_a_mandate_refusal_is_not_a_halt_and_cannot_take_the_continue_path`
* `test_a_mandate_refusal_never_qualifies_as_a_bounded_wait_read_halt`
* `test_the_campaign_stops_on_halt_and_would_not_stop_on_a_safe_refusal`

The third extracts both stop-sets from the **AST** (the campaign discusses both
statuses in prose; a substring scan cannot tell a stop-set from a comment about
one) and asserts non-vacuously that they were found. If `SPRING_SAFE_REFUSAL`
is ever added to a stop-set, the tradeoff this finding rests on has changed and
the test says to re-open the classification.
