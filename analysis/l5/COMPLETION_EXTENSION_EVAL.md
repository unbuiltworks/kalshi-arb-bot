# Completion extension vs L5_COMPLETION_EXTENSION_GATE_v1

Gate sha `fd6e31ddfbc42b40` — **NOT_YET_ENOUGH_EVIDENCE**

| | window | treated only |
|---|---|---|
| pairing_rate | 0.1172 | 0.1 |
| complete_orders | 881 | 53 |
| complete_rejected | 165 | 7 |
| complete_filled | 127 | 6 |

Sessions: 497 — of which the extension fired in **10**

## Sample minimums not met

| minimum | have | need |
|---|---|---|
| forward_hours_on_the_extension | 2.35 | 48 |
| sessions_where_the_extension_fired | 10 | 25 |
| settled_venue_confirmed_markets | 5 | 15 |

Below the frozen sample minimums this reports counts and nothing else -- no provisional verdict, no direction of travel. An early read is the failure mode the gate exists to prevent, so it is structurally impossible here rather than merely discouraged.

A rate over the post-deploy WINDOW is not a rate of the CHANGE. Sessions that never passed the opening budget are controls; the two are reported separately and never pooled.
