# K/G/P sports micro-live execution

The first automated prediction-market sports lane is intentionally tiny and fail-closed.

- Venues: Kalshi, Gemini Predictions, Polymarket US only. Sportsbooks are never automatic.
- Initial quantity: exactly one contract per leg.
- Hard capital ceiling: $10 per paired execution; q=1 normally uses less than $2.
- Minimum modeled and recomputed after-fee locked profit: $0.03 per paired contract.
- Maximum cross-venue quote skew: 50 ms.
- Maximum book age at final planning gate: 100 ms.
- Settlement confidence must be `FULL_RULES_AUDITED_EXECUTABLE` on the opportunity and both current quotes.
- Every executable pair requires an exact market-ID/native-orientation rule approval with a SHA-256 rule fingerprint.
- `NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE` / NOT-GUARANTEED rule-risk alerts can never enter this path.
- Final execution pricing must come from explicit live books: Gemini depth10@100ms, Kalshi depth-1 orderbook, or Polymarket market book.
- The approvals set remains empty until a real venue/contract pair clears the full lifecycle audit.
- Order transports must use immediate execution semantics and hard-halt on unresolved ambiguity or one-sided fill.
- The first live authorization is a one-use canary token; the service cannot mint its own authorization.
