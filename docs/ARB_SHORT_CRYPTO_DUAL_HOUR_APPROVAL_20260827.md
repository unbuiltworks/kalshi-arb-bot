# Gemini BTC5m + BTC15m Dual-Horizon One-Hour Approval — 2026-08-27

User requested a real live one-hour session covering both BTC 5-minute and BTC 15-minute Gemini Prediction Markets.

Authorization is conditional on the dedicated disarmed dual-horizon gate passing cleanly.

Feature checkpoint: `840dc158ff6e75b1a3b63e45d6c1473293453c93` on `feature/gemini-short-crypto-live-q1`.

AWS gate: `deploy/aws/wait_gemini_btc_dual_inventory_settlement_then_dual_hour_preflight.sh`.
AWS live launcher: `deploy/aws/run_gemini_btc_dual_inventory_hour_live.sh`.
Approval ID: `dh1-20260827-1657et`.

Hard scope:
- Gemini BTC 5-minute and BTC 15-minute prediction markets only;
- maker-first temporal inventory strategy;
- q=1 maximum per outcome per campaign;
- serialized execution: never more than one unresolved campaign across both horizons;
- max 60-minute order-entry window;
- max 2 executed campaigns per horizon / 4 executed campaigns total;
- shared cumulative Gemini-settled loss stop at -$1.00;
- exact Gemini settlement reconciliation before another campaign;
- any ambiguous local state, client-ID/outcome mismatch, reconciliation mismatch, or child exception hard-stops;
- no overnight authorization;
- no size scaling;
- only authoritative Gemini settled `netProfit` counts as realized P&L.

This approval is single-use and must not be reused after a launch attempt. The live launcher consumes the approval ID on disk before arming the session.
