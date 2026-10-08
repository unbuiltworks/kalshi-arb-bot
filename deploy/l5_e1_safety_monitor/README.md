# E1 independent read-only safety monitor

One job: while a bounded E1 probe is running, notice if an order is left
unmanaged and tell a human. It never cancels anything.

## What it detects

| alert | meaning |
|---|---|
| `A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER` | an order is still open in the probe market at or past handover. The dangerous state, and the consequence of executor failure whatever the cause. CRITICAL |
| `A2_EXECUTOR_PRESUMED_FAILED_WITH_EXPOSURE_OPEN` | the probe's own deadline passed while an order or a position is still open. CRITICAL |
| `A3_POSITION_IN_THE_PROBE_MARKET` | inventory exists. Expected after a fill; never unwound by the monitor |
| `A4_MONITORING_STALE_OR_BLIND` | our own reads are failing. Absence of an alert is not evidence of safety while this fires. CRITICAL |
| `A5_UNATTRIBUTED_ORDER_IN_THE_PROBE_MARKET` | an open order in the market that is not one of E1's ids. Potentially relevant, never ours, never cancelled |

## Configuration

| variable | meaning |
|---|---|
| `E1_MONITOR_MARKET` | the probe's exact market slug. Required; the monitor refuses without it |
| `E1_MONITOR_KNOWN_ORDER_IDS` | comma-separated E1 order ids, owner-supplied. A Railway volume attaches to one service and E1's is on the executor, so the monitor cannot read E1's tape. Ids it is not given are reported as unattributed rather than assumed to belong to someone else |
| `E1_MONITOR_HANDOVER_UNIX` | the window's handover time. An order open at or after this is CRITICAL |
| `E1_MONITOR_EXECUTOR_DEADLINE_UNIX` | when the probe should have finished |
| `E1_MONITOR_ALERT_WEBHOOK` | HTTPS endpoint to POST alerts to. Refused if it is plain HTTP or names the venue host |
| `E1_MONITOR_POLL_S` / `E1_MONITOR_MAX_RUNTIME_S` | poll interval (default 10s) and lifetime (default 3600s) |
| `E1_MONITOR_SELF_TEST=YES` | emit one labelled synthetic alert at boot and report its delivery status |

Credentials are set by **reference** (`${{service.VAR}}`) so no value is
ever read, copied or logged. The venue offers no separate read-only key, so
least-privileged access is the same key inside an image that cannot form a
non-GET request.

## Without a webhook there is no alert coverage

With `E1_MONITOR_ALERT_WEBHOOK` unset, alerts are **log lines only** and the
monitor says so in `E1_MONITOR_CONFIG.alerting.MISSING_SETUP`. Nothing
reaches a person. To close that gap the owner supplies one HTTPS incoming
webhook URL (Slack, Discord, or any endpoint that answers 2xx).
