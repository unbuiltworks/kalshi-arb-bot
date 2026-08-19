# Sports trade settlement reconciler checkpoint — 2026-08-19

- Reads live sports fills from ARB Command Center.
- No order-entry credentials or code path.
- Requires two recorded fills and definitive YES/NO settlement at both Gemini and Kalshi.
- Unknown/LFMP/ambiguous venue resolution remains open and is never counted as realized P&L.
- Actual gross/net P&L is computed from recorded fill capital, recorded fees, and actual venue payouts.
- Emits exactly one idempotent `TRADE_REALIZED` event per trade.
- Railway runtime is configured for continuous 20-second reconciliation with restart policy ALWAYS.
