# Completion extension vs L5_COMPLETION_EXTENSION_GATE_v1

Gate sha `fd6e31ddfbc42b40` — **NOT_YET_ENOUGH_EVIDENCE**

| | window | treated only |
|---|---|---|
| pairing_rate | 0.5 | 0.0 |
| complete_orders | 21 | 5 |
| complete_rejected | 8 | 1 |
| complete_filled | 6 | 1 |

Sessions: 9 — of which the extension fired in **1**

## Sample minimums not met

| minimum | have | need |
|---|---|---|
| forward_hours_on_the_extension | 0.3 | 48 |
| sessions_that_opened_a_residual | 8 | 40 |
| sessions_where_the_extension_fired | 1 | 25 |
| settled_venue_confirmed_markets | 0 | 15 |

Below the frozen sample minimums this reports counts and nothing else -- no provisional verdict, no direction of travel. An early read is the failure mode the gate exists to prevent, so it is structurally impossible here rather than merely discouraged.

A rate over the post-deploy WINDOW is not a rate of the CHANGE. Sessions that never passed the opening budget are controls; the two are reported separately and never pooled.
