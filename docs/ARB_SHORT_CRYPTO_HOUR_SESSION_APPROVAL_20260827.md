# Gemini BTC5m Inventory-Maker One-Hour Session Approval — 2026-08-27

User explicitly agreed to proceed with the bounded one-hour live test after the proposed limits were stated.

Approval is conditional on the new disarmed hour gate passing cleanly after authoritative reconciliation of the unresolved v3 campaign.

Authorized feature checkpoint for the one-hour supervisor: `7faabda1e9cedf00e3e93b1238ee5ed0cc5ee99a` on `feature/gemini-short-crypto-live-q1`.

Authorized AWS live launcher: `deploy/aws/run_gemini_btc5m_inventory_hour_live.sh` on `aws-migration-foundation`.

Approval ID: `h1-20260827-2057z`

Hard scope:
- Gemini BTC 5-minute only;
- q=1 maximum per outcome;
- maker-first inventory strategy v3;
- maximum 60 minutes runtime;
- maximum 4 executed/settled inventory campaigns;
- maximum cumulative settled loss of $1.00, then hard stop;
- exactly one unresolved campaign at a time;
- no new campaign before prior zero-fill release or authoritative Gemini settlement reconciliation;
- any client-ID/outcome mismatch, ambiguous local state, reconciliation mismatch, or child exception hard-stops;
- no overnight authorization;
- no size scaling;
- only Gemini authoritative settled `netProfit` counts as realized P&L.

Important incident immediately before this approval:
- First live inventory-maker v3 campaign executed one-sided inventory and later showed zero active venue orders/positions but retained its AWS-local reservation pending authoritative settlement reconciliation.
- A combined settlement/overnight shell gate falsely printed `SETTLEMENT_RECONCILED_AND_RESERVATION_RELEASED` because a `docker run ... python - <<'PY'` invocation omitted Docker `-i`; the Python heredoc was never delivered to the container. The following audit correctly showed `local_open_reservation=true` and blocked live continuation.
- The gate was fixed to use `docker run -i` and to assert the local reservation file is actually absent before printing release success.
- The dedicated one-hour settlement/preflight gate also includes this fix and cannot place/cancel/sell venue orders.

The one-hour live launcher must not run unless the dedicated hour gate finishes with `HOUR_Q1_READY_FOR_FRESH_APPROVAL` / a clean-start result and the existing reservation is proven cleared.
