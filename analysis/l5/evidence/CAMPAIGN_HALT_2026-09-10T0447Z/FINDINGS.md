# Campaign halt 2026-09-10T04:47:28Z — my defect, 6h43m dead

**Status: the live PMUS probe is DOWN and has been since 04:47:29Z.** It did
not stop because a risk rule fired. It stopped because code I wrote in
`e7720b75` raised on its first execution.

## What the venue and the logs say

Deployment `d1e9bf12-695d-4c0b-bb58-425ebb4fafbe`, commit `e0fdd13a`, service
`pmus-spring-l5-probe`. Last three rows:

```
04:47:28  session_end   slug aec-itfwo-hinino-vaiadk-2026-09-10
                        status HALT
                        reason "UnboundLocalError:cannot access local
                                variable 'dec' where it is not associated
                                with a value"
04:47:29  fidelity_verdict ...
04:47:29  campaign_end  status CAMPAIGN_STOP:HALT  sessions 86
                        settled 28  pending 17  filled_markets 0
                        next "none: this status is an owner decision or a
                              stop, and is never auto-restarted"
```

Railway service metrics since: `CPU_USAGE` 0 and `MEMORY_USAGE_GB` 0 across 61
consecutive samples. The container is gone, and it is gone *by design* —
money-moving sessions fail closed and never self-relaunch. That rule worked.
The thing it was protecting against was my own bug.

## Position at the halt

```
slug        aec-itfwo-hinino-vaiadk-2026-09-10
up          14.97      dn 0        matched 0      residual 14.97
avg_up      0.4358316633266533066132264529
spent       $6.5244    unmatched_usd $6.5244
```

One-sided, unhedged, inside the $7.50 per-market cap. Worst branch −$6.5244,
best branch +$8.4456. Held to settlement per policy — that part is the
designed behaviour, not the incident.

## No orphan order

`run_session`'s `finally:` calls `q1.cancel_all_deadman(t, slug)` on **every**
exit path including the exception path that produced this HALT, guarded only
by `reached_order_stage`. The last two orders before the crash were
`spring_complete_1` and `spring_complete_2` previews at 04:47:27; the only
order id in the tail, `CD9PP1N6CSJZ`, is logged `ORDER_STATE_REJECTED`.

So this did not become the situation the owner ruled out — nobody has to open
the app and cancel anything. I am asserting the code path, not a venue read:
confirming against `/v1/orders/open` needs the credentialed process, which is
the process that is down.

## Root cause

`src/pmus_spring_l5_session.py`, the combined-loss gate:

```python
if relation in ("open", "extend") and risk_reduction_only:
    mark_noop(dec, f"combined_loss_stop:{_combined}<={daily_loss_stop()}", ...)
```

`dec` is bound at two sites, both *below* this gate — the entry-band branch
and the spend-cap branch. The action loop starts at line 2711; between there
and the gate, `dec` is never assigned. The first iteration to reach the gate
therefore raises.

It survived 84 sessions because reaching it needs `risk_reduction_only`, which
needs the combined budget tripped. Once it tripped, the gate was reached and
the process died on the spot.

The silent version is worse than the crash. On a *later* iteration `dec` is
still bound from the previous pass, so the gate would have annotated the wrong
decision row — a refusal attributed to a trade that was not refused.

## Why 16 tests missed it

`tests/test_combined_loss_budget.py` has 16 tests. Every one calls a helper:
`guaranteed_settlement_floor`, `unsettled_floor_total`, `combined_loss`,
`combined_budget_tripped`, `reduces_risk_only`. The arithmetic was correct and
is unchanged. Not one test executed the branch that consumes it.

Correct primitives, unreachable wiring, full green suite.

## The fix

Bind the row the way every other `mark_noop` site in that loop does, inside
the gate, immediately before the annotation.

`tests/test_combined_loss_gate_wiring.py` adds 9 tests. Reverting the one-line
binding fails 7 of them, including both parametrisations of an end-to-end
session driven through the gate — so this reproduces the live crash rather
than describing it. Three are a structural guard over the *whole* action loop,
because `dec` was not special: any name read at the top of that loop and
assigned only in a lower branch has the same shape.

## Also down while the process is down

The settlement auditor runs inside this campaign. With it dead, the 17 pending
markets are not being reconciled, and the two `AUDIT_UNAVAILABLE / HTTP 429`
rows at 04:45:24 are not being retried.

## Not done, deliberately

The fix is committed and **not pushed**. `src/pmus_spring_l5_session.py` is a
watched path: pushing it deploys, and deploying restarts a campaign whose
`PMUS_SPRING_LIVE` / `PMUS_SPRING_ARM` handshake is still armed. Restarting a
live money-moving campaign is an owner decision, and the owner's standing rule
is that a stopped probe is never automatically relaunched.

## Open question this raises about the budget itself

`unsettled_floor_total` scores a one-sided residual at `min(up, dn) − cost`,
so 14.97 UP / 0 DN contributes its entire $6.52 of cash as an unsettled loss.
For a matched pair above par that floor really is guaranteed. For an unmatched
residual it is the worst branch of a coin flip, not a guaranteed loss. That
choice is what tripped the budget here. It is defensible as a worst-case stop
and it is *not* what the owner's wording ("unsettled guaranteed losses")
describes. Flagged, not changed.
