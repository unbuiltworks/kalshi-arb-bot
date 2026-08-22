# Basis-risk q1 canonical ledger boot validation

This checkpoint intentionally triggers a fresh Railway source deployment after the service start command was changed to a non-trading database diagnostic.

The diagnostic exits successfully only if psycopg is installed, DATABASE_URL resolves, the canonical public.trade_events table is reachable, and there are zero submitted Gemini/Kalshi crypto trades lacking TRADE_REALIZED.

No trading module is invoked and the basis-risk arm remains disabled during this validation.
