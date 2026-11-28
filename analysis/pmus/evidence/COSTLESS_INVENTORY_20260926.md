# Costless inventory: four defects, $1.09230, and the fixes

**Date** 2026-09-26 · **Branch** `claude/gemini-btc-economic-viability-h3u7zl`
**Arm** `l5cont-20260926T2049Z-btc15m-a9a936`, SHA `12a30e2`, armed 20:50:49Z,
disarmed 21:0xZ. **Status: DISARMED. Not re-armed.**

## What happened

Three BTC15m windows traded. Two settled against inventory that had cost
real money and showed none.

| window | book at settlement | settled | truth |
|---|---|---|---|
| 2045z | `dn_qty 2.2500`, `dn_cost 0` | `settlement_pnl 0E-8` | 2.25 DOWN shares, worthless (UP won) |
| 2100z | `up 5.67 @ 5.1627`, `dn 18.89 @ 2.3244` | `settlement_pnl -1.81710` | correct — the control case |
| 2115z | all zeros | `settlement_pnl 0` | 3.25 DOWN shares costing 0.6600, worthless |

### The account is the authority

Both reads flat — zero positions, zero open orders, zero unsettled,
key fingerprint `d51b44a3fa83`:

```
programme start   2026-09-26T15:49:54Z   61.69765
before this arm   2026-09-26T20:49:29Z   64.33535
after disarm      2026-09-26T21:35:58Z   61.42595
```

```
actual cumulative cash P&L   -0.27170
the book claimed             +0.82060
overstated by                 1.09230   <- the cost that settled off-ledger
```

$1.09230 is the cost of 5.50 DOWN shares (2.25 + 3.25) that settled
worthless without ever appearing in `leg_cost`.

## The four defects

### P0.1 — the adopted side word (2115z)

An order adopted from the venue kept the venue's own word for its side.
`apply_fill` builds its ledger key as `market|side`, so 3.25 shares and
$0.66 of cost went into `...2115z|ORDER_SIDE_SELL` — a key that
`leg_cost()`, `risk_exposure_usd()` and `retire_settled_market()` never
read, because those read only `|UP` and `|DOWN`. Visible in every reconcile
from 21:28:48Z:

```
drift {"...2115z|ORDER_SIDE_SELL": {"ours":"3.25","venue":"0"},
       "...2115z|DOWN":            {"ours":"0",   "venue":"2.5800"}}
```

Nothing anywhere decided `ORDER_SIDE_SELL` meant DOWN. `own_outcome_price`
asked `side.upper().startswith("U")`, which is False for `ORDER_SIDE_SELL`
and therefore happened to price it as a short — **and equally False for
`ORDER_SIDE_BUY`**, so an adopted buy would have been priced at `1 - p`
*and* filed under the wrong key at once. That half never fired. It was luck.

**Fix.** `canonical_side()` and `venue_row_side()` in `l5_live_execution.py`:
one table, sourced from authoritative `/v1/order` rows, mapping every word
the venue has been observed to use. A row states its side up to three ways
(`outcomeSide`, `intent`, `side`) and they must agree once mapped. An
unmapped word raises `UnknownSide` rather than defaulting. `apply_fill`,
`own_outcome_price`, `inventory_class`, `risk_exposure_usd`, adoption and
the gross/net split all go through it; the raw venue word is preserved
beside the entry as `venue_side_raw` for audit and is read by nothing.

Also fixed in the same class: `poll_orders` passed `market=t.market_slug`,
the transport's *current* window, rather than the order's own market. Two
2045z orders appear in the 21:01:21Z lifecycle events under market 2100z.
They had filled nothing, so nothing was misbooked.

### P0.2 — quantity with no cost (2045z)

`book_violations` caught `COST_WITHOUT_QUANTITY` (the 1815z defect) and not
its mirror. 2.25 DOWN shares with zero cost gave `per_share = 0/2.25 = 0`,
inside `[0,1]`, so every check passed: the shares priced at zero risk and
settled at zero P&L.

**Fix.** `QUANTITY_WITHOUT_COST`, gated on `fill_count` so a genuine fill at
$0.00 (real, and attested) is distinguished from quantity whose cost basis
was lost. It blocks risk pricing, submissions (via uncertainty) and — the
one that actually posted the zero — settlement retirement.
`reconstruct_cost_basis(market, side, qty=, cost=, source=)` is the way out:
quantity and cost written in one step, from named evidence, logged.

### P0.3 — the 429 we caused, and the release it triggered

The tick ran at 3s and, on *every* tick, polled each live order (up to 8
GETs) and read a settlement for each market still carrying exposure — which,
thanks to P0.4, included a market that could never leave the book. Over
1 req/s sustained, ~3.5/s with a full envelope. The venue answered
`HTTP Error 429: Too Many Requests`.

Then the fallback: `reconcile_from_venue` released every live order the
venue no longer listed. Absent from the open list **includes FILLED**.
2045z's `CR157F2WJYC3` filled, dropped off the list, its `/v1/order` read
raised, reconcile released it unbooked, and the shares arrived from the
venue's net with no cost.

**Fix, two parts.**

*The release.* A vanished order with a venue id is no longer released — it
moves to `pending_terminal`. It stops consuming the live-order envelope (it
cannot fill again) but still owes the ledger its final `cumQuantity`, the
book stays uncertain until an authoritative `/v1/order` read supplies it,
and `retire_settled_market` refuses while anything is parked. An order with
*no* venue id was never ACKed, has nothing to ask by, and is released — the
1700z phantoms would otherwise wedge the session on an unanswerable
question; if such an order did fill, `QUANTITY_WITHOUT_COST` catches it.

*The rate.* `RateLimiter` in `l5_pmus_transport.py`, on the single
`_request` door every read and write passes through. A token bucket paces
the steady state; a **shared** cool-off, doubling with jitter per
consecutive 429 and capped, gates everyone the moment the venue refuses —
per-caller backoff is no backoff when six callers take turns. Writes skip
the token queue but not the cool-off. It raises `VenueThrottled` *without
sending*, so every path that already fails closed on a venue error fails
closed on this too. Cadence is now bounded per *thing*: `ORDER_POLL_S = 6`,
`SETTLEMENT_POLL_S = 10`, `PENDING_RESOLVE_S = 5`.

Metrics on the heartbeat: `requests`, `http_429`, `throttled_waits`,
`refused_in_cooloff`, `consecutive_429`, `in_cooloff`,
`cooloff_remaining_s`, and `time_to_authoritative_recovery_s`.

### P0.4 — settling the same market forever

From 21:30:43Z the runner re-emitted `L5_LIVE_SETTLED` for 2115z every ~3
seconds, 40+ times, each `retired: true` with all-zero values. Retirement
popped only `|UP` and `|DOWN`; the market's real inventory sat under
`|ORDER_SIDE_SELL`, so `markets_with_exposure` kept naming it. Only the
all-zero book kept it harmless — each pass would otherwise have posted
another P&L and moved the loss halt with it.

**Fix.** A persisted `retired_markets` ledger: a second retirement returns
`ALREADY_RETIRED` and touches no accounting, and it survives restart.
Retirement now clears **every** key of the market across `positions`,
`leg_cost`, `fill_count` and `venue_side_raw`, not the two expected ones.
`markets_with_exposure` excludes retired markets and now *includes* markets
whose only trace is a live or parked order — previously such a market was
invisible to the sweep, so the sweep could not refuse it.

## Accounting repair

Rebase id `costless-venue-net-2045z-2115z`, applied once, matched on the
stored total `+0.82060`.

```
attributed    = 4.8317   (1600z +3.4442, 1815z +1.3875)
              - 1.81710  (2100z, settled from a coherent gross book)
              - 0.66000  (2115z, from four venue fill rows)
              = 2.35460
cash_delta    = 61.42595 - 61.69765 = -0.27170
unattributed  = -2.62630   ( -2.19400 carried forward, -0.43230 new )
realized_total = -0.27170
```

**2115z is a real line.** Four adopted orders, all `ORDER_SIDE_SELL /
OUTCOME_SIDE_NO / ORDER_INTENT_BUY_SHORT`, commission `0.0000` on every one:

| qty | wire | own | cost |
|---|---|---|---|
| 0.91 | 0.79 | 0.21 | 0.1911 |
| 0.53 | 0.80 | 0.20 | 0.1060 |
| 1.72 | 0.80 | 0.20 | 0.3440 |
| 0.09 | 0.79 | 0.21 | 0.0189 |
| **3.25** | | | **0.6600** |

3.25 is exactly what the book held under `...2115z|ORDER_SIDE_SELL`.

**2045z is not reconstructed.** Its 2.25 DOWN shares came from
`CR157F2WJYC3`, whose fill row does not survive in what we hold. The cash
anchor puts the remainder at `-0.43230` (2.25 shares at ~0.192 own terms,
consistent with the 0.79–0.80 wire regime of the adjacent window) — but
derived by difference is precisely what *unattributed* means, so that is
where it stays. **It is not relabelled as a trading line, a fee or a
rebate.** No rebate was credited; a zero commission is not a rebate.

## Migrating the book the defects left behind

The deployed volume still holds `...2115z|ORDER_SIDE_SELL`. Under the new
canonical rules that key is *uncanonical*: counted apart in the gross,
skipped in the net, and therefore a permanent gross/net disagreement — a
book that is uncertain forever and never submits again. Deploying the fix
without a migration would replace a money-losing executor with a silent one.

Two steps, both tied to the same rebase id so they happen exactly once:

1. **`canonicalize_position_keys()`** merges the raw-side key into the
   canonical one — quantity and cost together, which is where `apply_fill`
   should have put them. A word that maps to nothing is left alone and
   named; inventing a side for it would be the original defect again.
2. **`adopt_flat_account(anchor=...)`** closes every market in the book
   **posting no P&L at all**. This is not a settlement. It is the statement
   that the 21:35:58Z read found zero positions, zero open orders and zero
   unsettled funds, and that every window in the book is already in the
   attributed/unattributed lines above — so settling them a second time
   would double-count the rebase. The anchor is recorded verbatim, because
   using this on an account that is *not* flat would silently delete live
   exposure.

## Risk: gate unchanged, cash reported

The `$5` gate still measures **worst-case loss over both settlements** and
is untouched. Gross cash deployed is now reported beside it, never instead
of it, because the two differ by more than people expect:

```
2100z   cash out $7.4871   worst case $1.8171
```

A bound on loss permits a cash outlay several times its own size, because
the legs pay for each other at settlement. That is sound and it is also
worth seeing. Whether cash deployed should get its own cap is an open
decision and has not been made.

Also visible at 2100z and not addressed here: `matched_cost / matched_qty =
5.8604 / 5.67 = 1.0336` — the matched pair was bought **above par**.

## Tests

- `tests/test_l5_costless_inventory.py` — 31 tests, one section per defect,
  plus 2100z as the control, the accounting repair and the live-volume
  migration. **26 of the first 27 fail on the pre-fix tree**; the one that passes is the 1815z mirror, which
  already worked.
- `tests/test_l5_continuous_lifecycle.py` — 8 tests driving the real
  `Runner` tick across four markets: adoption, partial→full fills, an order
  that vanishes under a 429, a venue that refuses everything, settlement,
  retirement, restart mid-flight, and a request-rate bound.
- Four superseded tests in `test_l5_order_lifecycle`,
  `test_l5_potential_exposure`, `test_l5_probe07_concurrency` and
  `test_l5_uncertainty_recovery` were **rewritten, not deleted**: each
  asserted that a vanished order is released and forgotten. The first half
  (it leaves the envelope) is still asserted; the second half is what cost
  $1.09.

Suite returns to baseline. Pre-existing failures, unrelated and unchanged:
`test_chain_ca_fallback::test_no_module_pins_a_bundle_without_a_fallback`,
`test_l5_live_oneshot::test_only_the_oneshot_probe_arms_anything`, and 14
setup errors in `test_pmus_status_live_handoff`.

## Not changed

L5 Native `030d8bf`, λ, maker treatment, sizing, the envelope ($5 worst-case
per market / $5 aggregate / 1 market / 10 residual / 8 live orders / −$5
halt). No predictors, no pair-cost filter, no chasing, no Probe 09. The
joined evidence collector (`deploy/l5_window_collector/**`,
`src/l5_window_collector.py`, `scripts/l5_window_collector_run.py`) is
untouched.
