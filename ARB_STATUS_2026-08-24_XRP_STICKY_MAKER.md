# ARB Checkpoint — XRP Sticky Maker Fill Hunt — 2026-08-24

## Objective
Increase real Gemini-maker -> Kalshi-FOK fill probability without weakening the 3c post-fill floor or confusing live-probe activity with realized arbitrage.

## Live findings
- Rapid maker repricing created excessive Gemini order-place/order-cancel churn and venue-native notifications.
- Multiple q=1 maker orders at/above the Gemini bid received zero fills.
- The old reprice loop used 4 rounds per cycle with ~15s rests in production.
- A Railway repo config override was causing the old supervisor to run despite a service-level sticky-watch start command.

## Safety/reconciliation
- Maker lane remains LIVE_THRESHOLD_DOMINANCE_INDEX_RISK_NOT_GUARANTEED_ARBITRAGE.
- q<=1, $1 reservation, <=50ms paired-book skew, <=250ms age, first-fill freeze, exact fill economics, Kalshi FOK hedge/flatten logic, and existing $5 cumulative realized research-loss ceiling remain intact.
- Old orphan trade `xrp-firstfill-20260824-1334et-a1-c06-r03` was authoritatively reconciled from Gemini:
  - shared old clientOrderId matched 3 venue orders due a 36-char truncation collision;
  - active orders: 0;
  - filled orders: 0;
  - cancelled/closed matching orders: 3;
  - aggregate filled quantity: 0;
  - realized P&L: $0.
- The stale Command Center $1 reservation for that trade was released only after the Gemini zero-fill proof.

## Fixes
- `src/gemini_kalshi_xrp_first_fill_watch.py` now provides sticky maker behavior:
  - 1 maker order per campaign;
  - up to 240s rest to preserve queue priority;
  - 1c admission cushion (modeled 4c at entry) while the hard live/post-fill floor remains 3c;
  - >330s required to expiry before entry;
  - 20s pause after clean zero-fill campaign;
  - first actual fill or any unsafe terminal stops the supervisor.
- Gemini round trade IDs are now compact deterministic hashes so `-mk` and `-flat` suffixes survive Gemini's 36-character clientOrderId limit and remain unique across rounds/cycles.
- `railway-crypto-maker-live-probe-q1.toml` is pinned to `python -m src.gemini_kalshi_xrp_first_fill_watch`, eliminating the hidden fallback to the churny supervisor.
- Added read-only orphan reconciler and guarded ledger-release one-off for interrupted maker processes.

## Validation
- Corrected sticky deployment predeploy suite: 79 passed.
- Runtime proof for sticky architecture previously showed max_cycles=24; corrected branch now pins the watch in repo config.
- Current fresh live run: `xrp-sticky3-20260824-1358et-a1`.
- At latest check it is armed in `ROLLOVER_WAIT` with order_entry=false, waiting for a viable XRP 15m window rather than generating order churn.

## Profit classification
- Old spammy maker attempts: executed fills 0, realized P&L $0.
- Orphan: filled quantity 0, realized P&L $0.
- Current sticky run: no order yet at checkpoint, realized P&L $0.

## Next promotion evidence
Observe the first sticky maker placement and require:
1. runtime shows one-round/240s sticky behavior;
2. collision-safe compact trade/client id;
3. no overlapping unresolved crypto reservation;
4. any fill immediately freezes maker remainder;
5. actual fill economics >=3c/contract before Kalshi FOK hedge, otherwise flatten/hard halt;
6. authoritative settlement before counting realized arbitrage profit.
