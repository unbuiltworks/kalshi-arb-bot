# Ledger: bot-owned sports settlement from the sports fill feed (27 Nov 2026)

Status: **release candidate, not approved, not deployed.** Development only.

- Code: `dev/l5-ledger-sports-settlement` @ `37afdff9a` (base: production ledger release `c111abd67`)
- Release candidate: `release/l5-pmus-live-sports-settle-37afdff9a` @ `9b5f4995a` (code plus one `DEPLOY_TRIGGER` line)
- Flag: `L5_SPORTS_SETTLE_FROM_FEED=1`. It is off by default, and any other value also means off.

## What went wrong (live 27 Nov, 01:44–01:48 UTC)

The sports bot traded `aec-atp-timleg-karpol-2026-11-25`. It bought YES 4 at an average of 0.485 and NO 4 at an average of 0.47, which is four completed pairs. The venue's net position was 0. The market settled YES=1.

Here is the production log path, read-only:

1. **Stale adoption.** An earlier reconcile adopted one venue net snapshot, `UP 3.0000`, at the $1/share bound. After that, every reconcile reported drift `ours 3.0000 / venue 0`, `gross_net_agrees: false` and a risk floor of 4.0000.
2. **The pre-settlement check refused.** At 01:47:00.85 `L5_OTHER_STRATEGY_COST` refused with `FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION` (side UP, held 3, fills 4). `attest_other_strategy_cost` requires the feed to equal what the ledger *held* on each side, and the held figure was a stale net.
3. **Booked at the bound.** At 01:47:00.91 `L5_LIVE_SETTLED` booked UP 3 at a cost of 3.00, with `settlement_pnl 0` and `UNKNOWN_COST_UPPER_BOUND`. The row appeared in `unresolved_cost_basis`, the account-risk record went `readable:false` with `UNRESOLVED_COST_BASIS:1`, and the bot logged `ACCOUNT_RISK_SAYS_UNREADABLE`.
4. **The sibling form was late, not missing.** The post-settlement sibling form is on in production: the heartbeat shows reasons tagged `...:SIBLING:...`. Its walk did start at 01:49:08. But a sports slug carries no time, so `Walk.floor` is None and the walk can only finish by reaching the end of the feed. It was still walking at 104 pages at 01:55 (`monotonic: false`). The restatement could only land once that full walk finished, roughly 18 minutes or more later.

The true result is 4 − (1.94 + 1.88) = **+0.18** before fees. The bound booked 0.00 and marked it unresolved.

## What changed

### At settlement

This is `InventoryStore.settle_from_sibling_feed` (src/l5_live_execution.py:6152), called from `Runner.settle_sports_from_feed` (src/l5_pmus_live_runner.py:6837) inside `sweep_settlements` (src/l5_pmus_live_runner.py:2918).

It applies only when all of these hold: the flag is on, the market is in the sports controller's registered series (`aec-`/`atc-`), the ledger holds it at the bound, and the existing sports scope applies (`L5_SPORTS_FILLS_URL` and `L5_SPORTS_ATTEST_FLOOR_UTC_DAY`).

Every one of these must agree:

- **The feed.** It must be readable, have strategy `SPORTS`, and be stamped no more than 60 s ago (5 s of future skew is allowed). Every order in this market must be `CLOSED`, and venue order ids must be unique.
- **Our own `/v1/order/{id}` read of every feed order.** The order must be in the same market and terminal (`leavesQuantity 0`). `cumQuantity` must equal the feed's filled quantity. The average price, converted to own-outcome terms, must equal the feed's cost (tolerance 0.0001 per share). The commission must be readable.
- **The venue's net position for the market.** This comes from our own `/v1/portfolio/positions` read; an absent row counts as 0. It must equal the feed's UP − DOWN.
- **The venue's binary settlement and resolution.** These are checked the same way `retire_settled_market` checks them.
- **The ledger's own book.** There must be no own-strategy live or pending order and no own fill provenance in the market, and no held side whose basis the ledger already knew.

If all of that holds:

- The market's book is replaced by the feed's gross quantities on each side, at the venue-confirmed cost plus the venue's positive commission.
- The one existing writer, `retire_settled_market`, then settles it: `result = Σ qty × payout − Σ cost − fees`, with each pair redeeming at $1.
- A negative commission (a venue credit) is recorded on the row but is not included in the result.
- The row is tagged `SIBLING_FEED_ATTESTED_AT_SETTLEMENT`, with origin `SPORTS` and basis `AUTHENTICATED_FILLS`. The evidence is stored with it: order ids, each order read, venue net, feed `as_of_utc`, fees, the held quantities and costs before, and the unknown keys it cleared.
- The row is **never listed as unresolved.**
- If retirement itself refuses, the book is restored exactly as it was.

**Any miss writes nothing.** That covers a stale or unreadable feed, an open order, a missing or foreign order read, a quantity, price or fee mismatch, a net mismatch, and an exception. The market then settles exactly as today: at the bound, unresolved, fail closed. The existing venue pass restates it later.

### Afterwards: the venue pass confirms but never re-applies

This is `Attestor._confirm_feed` (src/l5_venue_cost_attestation.py:829) and `confirm_sibling_feed_settlement` (src/l5_live_execution.py:6454).

The correction pass never treats a feed-settled row as a correction candidate, because its provenance is not the bound, and `attest_retired_market_cost` refuses it with `NOT_AT_THE_UPPER_BOUND`. Once the activity walk has read the market, the pass compares trade by trade:

- **Confirmed.** Every venue fill names an attested order, and quantities match order by order and side by side. The trade ids are then consumed once, in a zero-delta `SIBLING_FEED_SETTLEMENT_VENUE_CONFIRMED` record, so the existing double-spend guard sees them.
- **Contradicted.** The walk finds a fill of an order the feed did not name (a manual or foreign order, including a hand-placed balanced pair the net check cannot see), a sale, a quantity difference, or a trade id that was already consumed. The row then gets `venue_contradicted`, and `unresolved_bound()` lists it again, so publication fails closed until the owner reviews it. Manual trades are never attributed to the bot, and no trade id is consumed.
- **Unreadable or short walks** each spend one bounded attempt (`MAX_CONFIRM_ATTEMPTS = 3`) and leave the row as booked.

The confirmation runs whether or not the flag is currently on. It adds no venue load, because these rows would otherwise have been bound rows walked by the same pass. The heartbeat gains `sibling_feed_settlements` with counts for `PENDING`, `CONFIRMED` and `CONTRADICTED`.

### Why a new flag instead of reusing the sibling switch

`L5_SIBLING_LEDGER_ATTESTATION` is already on in production. If this change were placed behind it, deploying would change how settlements are booked without any owner action. The new switch also keeps the existing sports scope (feed URL plus owner floor day) as a second requirement.

## Activation (owner action; nothing has been set)

1. Deploy the release candidate. With the flag unset, behaviour is identical to `c111abd67`.
2. Set `L5_SPORTS_SETTLE_FROM_FEED=1` on `l5-pmus-live`. `L5_SPORTS_FILLS_URL` and `L5_SPORTS_ATTEST_FLOOR_UTC_DAY` must stay set; the production log shows the pre-settlement sports check firing, which means both are set now.
3. On the next bot settlement, check that the logs show:
   - `L5_SPORTS_FEED_SETTLEMENT retired=true`;
   - `L5_LIVE_SETTLED`, with the row's `pnl_provenance` as `SIBLING_FEED_ATTESTED_AT_SETTLEMENT`;
   - the account-risk record still `readable:true`;
   - and later, the heartbeat `sibling_feed_settlements.CONFIRMED` incrementing.

## What happens to the timleg-karpol row that is unresolved now

This change **does not touch it**, because the row is already retired and the new form only acts at retirement.

The existing sibling form should restate it once its full walk ends. The expected restatement is `VENUE_FILLS_EQUAL_SIBLING_LEDGER`, from 0.00 to about +0.18, on condition that the venue's fill fees read zero, that the venue order set equals the feed's, and that the row settled after the last re-anchor. If the sibling form refuses (for example `FEE_NOT_ZERO`), the row stays unresolved. The owner's existing remedy is then `L5_ACCEPT_BOUND_MARKETS=aec-atp-timleg-karpol-2026-11-25`, which changes no figure. Check the heartbeat `still_upper_bound.reasons` before using it.

## Tests

`tests/test_l5_sports_feed_settlement.py` has 36 tests. They cover:

- **The live case.** It books +0.18, the publication stays readable, and the result survives a restart.
- **Exact shapes.** Pairs only, an asymmetric residual on the winning and on the losing side, a partial fill of a cancelled order, fees inside the result and a credit outside it, and two markets settled in one sweep through the runner.
- **Every fallback writes nothing.** A feed that is stale (old or future), unreadable, another strategy's, has an open order, is missing an order, or has no fills. An order read that is missing, in a foreign market, not terminal, has a different quantity, a different price, no price, or no fee. A net mismatch from a manual trade. A non-sports market. A resolution for another market. A side whose basis was known. A retirement refusal, after which the book is restored exactly.
- **A delayed fill.** The venue has a fill the feed does not, so it falls back to the bound. The existing sibling pass then restates it exactly once.
- **The venue pass.** A restart between settlement and the walk, then confirmation exactly once with zero delta. No re-apply, and a second pass writes nothing. A manual balanced pair is caught by the walk, giving `CONTRADICTED`, unresolved again and publication unreadable. A short walk spends a bounded attempt.
- **The runner path.** With the flag off it makes no venue reads and books the bound. The flag without the sports scope also books the bound. The runner books timleg from the feed. The venue net is read from a positions row, and a disagreeing row falls back. An unreachable feed falls back.

The existing ledger, settlement, attestation, unresolved and runner-settlement modules all pass: 790 tests in the related set.

Full-suite parity was run sequentially from clean worktrees with `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE`. Results are in PARITY below.

## Risks

- **No trade-level evidence at settlement time.** The sports feed publishes orders, not trades. Completeness at settlement rests on the venue's net position plus a venue read of every feed order. A manual trade set whose net is exactly zero, such as a hand-placed pair, is invisible at that point. Its pair result, a few cents either way, is missing from the account-wide figure until the walk contradicts the row. The row is then unresolved again, which fails closed.
- **Residual markets after settlement.** If the venue drops a resolved position row with a non-zero net, the net reads 0 and the market falls back to the bound, which is today's behaviour. Markets made only of pairs (net 0) are unaffected.
- **Positions read size.** The positions read is a single GET. If the venue paginates and the market is not on the first page, the net reads 0. The effect is the same as the previous point: a fallback for residual markets.
- **Fees in the result.** Settlement rows elsewhere exclude fees. Here a positive commission is included as cost, as specified, so its effect is conservative. The cash re-anchor reconciles either way.
- **Commission field.** The form requires `commissionNotionalTotalCollected` on the order row. Wherever the venue omits it, the form falls back, which is safe but means it will not fire.

## Related finding (report only): null `portfolio_drawdown` → `ACCOUNT_DRAWDOWN_UNREADABLE`

**Cause.** `Runner.equity_risk_lines` publishes `portfolio_drawdown` with a null value whenever `orders_uncertain` is set (`why = ORDER_STATE_UNCERTAIN`). The only exception is `uncertainty_confined_to_other_strategy()`, together with `L5_PUBLISH_VALUE_DURING_SIBLING_ADOPTION=OTHER_STRATEGY_POSITION_ONLY`. That exception tests `ORIGIN.strategy_of_market` (src/l5_live_execution.py:2243), which covers the BTC series only, so a sports (`aec-`/`atc-`) divergence never qualifies.

**Production logs, 26–27 Nov.** Each refusal episode matches a reconcile that raised the uncertain flag because of a **sports gross/net divergence**. Each episode cleared at the next triggered reconcile. There were exactly 4 account-wide episodes:

| Uncertain from | Cleared at | Divergence |
|---|---|---|
| 23:52:43 | 23:53:00 | `kobvit-khaser` |
| 00:48:22 | 00:49:16 | `timleg-karpol`, `paryur-stoole` |
| 00:59:21 | 00:59:39 | `kuzdmy-kurole`, `savvol-hryevh` |
| 01:04:55 | 01:05:05 | `kuzdmy-kurole` |

**Not fixed by this change.** The one-line fix would be to use `ORIGIN.ledger_owner_of_market` at :2243. But `l5_origin.LEDGER_OWNER_SERIES` documents that it is deliberately kept out of that decision for sports markets. So the fix is an owner decision, should be gated behind its own flag, and is kept out of this release. Until then the behaviour stays fail-closed.

## PARITY

| Worktree | Failed | Errors | Passed |
|---|---|---|---|
| `c111abd67` | 72 | 26 | 21785 |
| `37afdff9a` | 72 | 26 | 21821 |

The branch has 36 more passes, which are the new tests. The 98 failing and erroring test names are identical in both runs.
