# Read-only sports Pushover alerts

Pipeline: sports scanner -> qualification gate -> material-change dedupe -> Pushover.

The alert path is notification-only and contains no order-entry transport. Qualification uses fee-inclusive modeled profit, executable size, quote freshness and skew. Repeat alerts are suppressed unless modeled locked profit improves materially or executable size increases substantially.

Real opportunity alerts fail closed unless the settlement confidence is `FULL_RULES_AUDITED_EXECUTABLE`. Positive price discrepancies that still require rule or settlement audit remain telemetry only and never trigger a phone alert.

Sportsbook-involved alerts are explicitly labeled as requiring manual sportsbook action. Rule and settlement status are included in every message so unaudited opportunities are never presented as execution-ready.
