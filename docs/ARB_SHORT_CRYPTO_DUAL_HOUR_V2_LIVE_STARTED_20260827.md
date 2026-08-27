# Gemini BTC5m + BTC15m dual-hour v2 live session started — 2026-08-27

Status: LIVE SESSION STARTED (not yet evidence of new fills or profit)

Session
- session_id: `dh2-dh2-202608-222429`
- feature commit: `052808d87fee1152486dc13d0bee62034e1550dd`
- launcher: `deploy/aws/run_gemini_btc_dual_inventory_hour_v2_live_resilient.sh`
- container: `arb-gemini-btc-dual-hour-v2-live`
- approval_id consumed: `dh2-20260827-1816et`
- live start: approximately 2026-08-27 22:24:29 UTC / 18:24:29 ET

Hard live bounds
- Gemini BTC 5m + BTC 15m only
- q=1 max per outcome
- serialized: at most one unresolved campaign across both horizons
- max 2 executed campaigns per horizon
- max 4 executed campaigns total
- 60-minute order-entry window
- cumulative settled trading-loss stop: -$1.00
- maker rebate excluded from in-session trading P&L
- any ambiguous state/reconciliation/child exception hard-stops

Pre-live proof from launcher
- 25 safety tests passed
- clean account: 0 active short-crypto orders, 0 active short-crypto positions
- local reservation absent
- startup market-readiness probes are informational/nonfatal
- BTC5m startup readiness: not ready at that instant (`NO_COMPLETE_ELIGIBLE_BOOK_OR_EDGE`)
- BTC15m startup readiness: ready, market `BTC15M2608272230`, observed modeled edge `0.12`, approximately 330.9 seconds to expiry
- final clean gate passed before approval consumption
- live detached container launched successfully

Important accounting context before this session
- Prior matched BTC5m inventory-maker campaign (`GEMI-BTC05M2608272040-UP`) was authoritatively reconciled from exact distinct client IDs and Gemini event resolution:
  - YES fill 1 @ 0.44
  - NO fill 1 @ 0.43
  - raw cost 0.87
  - maker fees 0.02
  - payout 1.00
  - realized trading P&L +0.11
- Gemini maker-rebate payout snapshot separately showed one PAID rebate record of $0.01 covering 3 fills / $1.23 volume. Do not attribute that entire rebate to the single matched trade without fill-level reward attribution.

This checkpoint does not assert that the dual-hour v2 session has executed any campaign yet. New realized P&L must only be recorded after authoritative fill and settlement reconciliation.
