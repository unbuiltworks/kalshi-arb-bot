# RUN MARKER — RECOVER-ONLY, Probe 07 journal extraction

`L5_RECOVER_ONLY=YES`, market `cpc-btc-updown-15m-2026-09-25-1945z`, live
authorization cleared, GET only, never armed.

Probe 07 closed itself out cleanly, so there is nothing to reconcile — and
until now that meant no order table either. This run extracts the journal
regardless: nine maker orders with their venue ids, prices, fills and
commissions, one of which is NEGATIVE.

Carries the three fixes Probe 07 exposed: maker prices floored to the venue
tick (never rounded up), a pre-POST refusal treated as terminal-known-no-
order instead of poisoning the session, and gross inventory reconciled
against the venue's net by identity rather than replaced by it.

No order created, cancelled or modified.
