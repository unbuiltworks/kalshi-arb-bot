# Sports K/P certification-candidate probe — 2026-08-21

Purpose: keep the direct Kalshi/Polymarket q=1 service fail-closed for execution while making an empty approval registry operationally useful.

Production behavior after this change:

- Exact K/P identity joins are observed read-only when there are no approved contracts.
- A row is emitted as `SPORTS_KP_CERTIFICATION_CANDIDATE` only after fresh q=1 books satisfy the existing >= $0.03 after-fee lock, <= 50 ms quote skew, and persistence gates.
- Candidate rows are explicitly `CERTIFICATION_CANDIDATE_NOT_EXECUTABLE` and require `FULL_RULE_FINGERPRINT_REQUIRED` before they can enter the execution path.
- No approval means no order entry. Existing live env + one-use arm + fresh revalidation gates remain unchanged for approved rows.
- Kalshi WebSocket HTTP 429 reconnects now back off to at least 15 seconds and exponentially to 30 seconds instead of retrying at sub-second cadence.

Observed before the 429 backoff deployment: 33 hot contracts, 12 exact K/P observation contracts, 6 Polymarket books subscribed, 0 approved/executable contracts. Kalshi WebSocket initialization was blocked by HTTP 429 responses.

This probe does not establish an executed or realized trade.
