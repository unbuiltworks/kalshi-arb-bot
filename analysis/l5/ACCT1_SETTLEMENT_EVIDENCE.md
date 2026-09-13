# ACCT-1 — what Polymarket US actually exposes for a fully matched position

Read-only investigation. **No status invented, no label changed, no code changed.**
Sources: `src/pmus_settlement_auditor.py` at `8606c87f`, and 15,422 venue trade rows
across 103 markets captured in `PMUS_L5_CAMPAIGN kind=settlement` records
2026-09-05 → 2026-09-13.

## 0. The whole account feed

`GET /v1/portfolio/activities` is account-scoped and carries exactly five types.
Observed histogram over one 440-row walk (2026-09-06 → 2026-09-08):

```
ACTIVITY_TYPE_TRADE                417
ACTIVITY_TYPE_POSITION_RESOLUTION   22
ACTIVITY_TYPE_TRANSFER               1
ACTIVITY_TYPE_ACCOUNT_DEPOSIT        -
ACTIVITY_TYPE_ACCOUNT_WITHDRAWAL     -
```

`ACTIVITY_TYPE_TRANSFER` is **not** a settlement event. Its payload is
`accountBalanceChange` — `transactionId`, `status`, `amount`, `paymentMethod`.
The single one in that walk is the $10 goodwill remediation credit for the
sep5 outage, already quarantined as non-performance cash. Deposits and
withdrawals are the same shape.

**There is no settlement/redemption activity type other than
`POSITION_RESOLUTION`.** The auditor reads TRADE and POSITION_RESOLUTION and is
not missing a type.

---

## A. What authoritative event proves the contracts settled?

Two, both real, both already implemented:

1. **`ACTIVITY_TYPE_POSITION_RESOLUTION`** for the market — `positionResolution`
   carrying `side`, `beforePosition`, `afterPosition`, `tradeId`, `updateTime`.
2. **Public resolution + account flat** (`flat_at_resolution`) — the public market
   is `MARKET_STATUS_RESOLVED` with a `settlementPx` and a `settlement_set_time`,
   AND the account has no row under `positions`, AND none under
   `availablePositions`, AND every trade in the market is non-busted and in a
   final state.

Path 2 is sound and sufficient **for the contracts**. It is guarded against the
failure modes that matter: an empty book proves nothing, elapsed time proves
nothing, and a short activity walk raises `AUDIT_UNAVAILABLE` rather than
returning a confident "not settled".

## B. What authoritative event proves the account received settlement value?

**Exactly one: `positionResolution.afterPosition.realized`.** There is no other
source anywhere in the system or the feed.

And the venue emits `POSITION_RESOLUTION` **only for a position it still carries
at resolution**. 2026-09-13, nine terminal markets, zero exceptions:

| residual at resolution | n | POSITION_RESOLUTION | realized |
|---|---|---|---|
| non-zero | 5 | yes | published |
| exactly 0.00 | 4 | **no** | **no event at all** |

For a fully matched position there is **no authoritative event proving value was
received**. Not a null field — no event.

## C. Does POSITION_RESOLUTION exist for this product/path?

**For the product: yes.** 24 rows since 2026-09-05, every one with `realized`
populated. The mechanism works.

**For the fully-matched path: no, by construction.** A netted-flat book has no
position to resolve.

**And there is a second confirmation route in the code that waits for an event
Polymarket will never emit.** `flat_at_resolution` promotes to `REALIZED` when
every trade carries its own `realizedPnl`:

```python
confirmed = trade_info.get("realized_pnl_rows") == len(trades) and \
    trade_info.get("realized_pnl_sum") is not None
```

Measured across the full corpus:

```
trade rows inspected                                  : 15422  (103 markets)
state histogram                                       : {'TRADE_STATE_NEW': 15422}
rows whose venue payload CONTAINS a 'realizedPnl' key :     0
rows with non-null realizedPnl                        :     0
```

`realizedPnl` is not null on these rows — **the key is absent from every one**.
Every trade is `TRADE_STATE_NEW`; the module already documents at 15:46Z that
this is "the venue's normal, final state; no CLEARED transition is exposed".

**That branch is unreachable.** Therefore `SETTLED_REALIZED_UNCONFIRMED` is not a
transient state that will later resolve. It is the **permanent** outcome for
every fully-matched market, and the name says "pending" about a terminal fact.

This is compounded by `unsettled_slugs()`: a slug leaves the unsettled set on a
`settled` row alone.

```python
elif kind in ("settled", "realized"):
    settled.add(slug)
return [s for s in filled if s not in settled]
```

So the market is never audited again. Even if the venue did populate the field
later, we would never look. **Same shape as P0.2** — an absorbing state with no
re-check path — though here re-checking would not help, because the event does
not come.

## D. Is position disappearance + resolution sufficient settlement evidence?

**For the contracts: yes.** That is what path 2 already asserts, correctly.

**For the cashflow: no.** It proves the contracts are gone. It does not prove
what was paid for them. "A matched pair redeems at $1.00" is a venue rule I
believe to be true, but it is **our** arithmetic applied to the venue's state,
not the venue's own statement of what it paid.

## E. What additional evidence would be required to call it REALIZED?

One of:

1. a venue-published settlement payout for a netted-flat book — **does not exist
   in any endpoint or activity type the system reads, and none of the five
   activity types carries it**;
2. an attributable cash delta — see F;
3. an endpoint not yet examined.

**Correction to my own first draft of this section.** I listed `/v1/transfers`
as an unexamined Polymarket candidate. It is not a Polymarket endpoint —
`/v1/transfers`, `/v1/balances`, `/v1/mytrades`, `/v1/notionalvolume` and
`/v1/prediction-markets/rewards|rebates|liquidity-rewards` all live in
`src/gemini_maker/subsidy_verify.py` and are **Gemini** paths on a different
venue. They have no bearing on ACCT-1.

The complete PMUS surface the system actually speaks, from `src/pmus_*.py` and
`src/polymarket_l5/`:

```
/v1/markets/{slug}/bbo          /v1/orders            /v1/order/preview
/v1/markets/{slug}/book         /v1/orders/open       /v1/order/{id}
/v1/market/slug/{slug}          /v1/orders/open/cancel /v1/order/{id}/cancel
/v1/portfolio/positions         /v1/account/balances  /v1/ws/markets
/v1/portfolio/activities        /v1/incentives        /v1/incentives/earnings
```

Books, BBO, market metadata, order placement and cancellation, positions,
balances, activities, incentives. **Not one of these is a per-market settlement
payout endpoint.** `/v1/incentives*` is the incentive stream and is already
separately accounted, never mixed into trading P&L.

So E(3) is closed as far as the system's own surface reaches: within what the
code speaks today, no such endpoint exists. What remains genuinely unexamined is
whether Polymarket US publishes a settlement or redemption endpoint the system
has never called. That is a venue-documentation question, not something I can
settle from this repository, and it is the one open thread in ACCT-1.

Nothing here weakens the definition. Under the current definition these markets
are **settled with no realized number obtainable**.

## F. Can payout be reconciled deterministically from before/after account state?

**Not in general, and not today.**

`/v1/account/balances` returns an account-wide scalar (`buyingPower`,
`availableBalance`, `cashBalance`). It is moved simultaneously by every fill,
every resting order's margin, every incentive credit, transfers, and every other
market's settlement. With 2–3 risk-bearing markets concurrent — the actual state
all day today — a delta cannot be attributed to one market without inference,
which is exactly what the standing rule forbids ("balance changes without
sufficient source identity").

There is a narrow deterministic case: one market settling inside a window with no
fills, no resting orders, no incentive credit, no transfer and no other
settlement. It is rare, fragile, and **the system does not sample balances around
settlement at all**, so no such evidence exists even retrospectively.

---

## Conclusion: the venue has two concepts and our state machine collapses them

| venue concept | provable? | how |
|---|---|---|
| the contracts settled | **yes, always** | POSITION_RESOLUTION, or resolved + account flat |
| realized cashflow for this market | **only when a residual was carried** | `positionResolution.afterPosition.realized` |

We express both through one terminal `settled` ledger row and one `status`
string. The collapse produces a status that describes a permanent condition in
the vocabulary of a temporary one.

**This does not need a third status.** It needs the reconciliation expectation
fixed. Minimum change, in order of confidence:

1. **Remove or neutralise the dead confirmation branch** in `flat_at_resolution`
   that waits on per-trade `realizedPnl`. 15,422/15,422 rows lack the key. Keeping
   it implies a pending promotion that cannot occur. *(Code change, safe, no
   behavioural effect — the branch never fires.)*
2. **Say the terminal condition is terminal.** The existing status renamed to
   carry "the venue publishes no realized number for a netted-flat position",
   with the two settlement proofs and the absent-event fact recorded on the row.
   **This is a relabel of an existing status. I do not make relabels. OWNER call.**
3. **Keep the DERIVED matched contribution beside it, still DERIVED.** Already
   true in `settlement_breakdown` and unchanged.
4. **E(3) is closed against the system's own surface** (see E). The one open
   thread is whether Polymarket US documents a settlement/redemption endpoint
   the code has never called. That is a venue-documentation question and it
   should be answered before (2) is decided, because a real endpoint would make
   the relabel unnecessary.

## The option I am naming in order to recommend against it

The engine could hold a 1-contract residual into every resolution. The venue
would then emit `POSITION_RESOLUTION` and publish `realized` for the whole
market, and every completion would become venue-confirmable.

**Do not do this.** It carries real directional risk to the settlement boundary
to obtain an accounting artifact, and jayfri cost $3.9506 today demonstrating
what an uncompleted residual is worth when it goes the wrong way. Recorded so the
idea is refused explicitly rather than discovered later.
