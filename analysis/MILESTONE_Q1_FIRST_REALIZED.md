# MILESTONE — first clean q1 L5-adapted BTC15m probe: **+$0.0600 REALIZED**

Date 2026-09-30. Deployment `9b43faed`, commit
`d9eada78bd3d58d240b7a99ca57da746cd97d199`, service `l5-live-probe-oneshot`
(restart policy NEVER). Market `cpc-btc-updown-15m-2026-09-30-1915z`.

## The chain, complete

**submitted → filled → matched → swept → settled → cashflow confirmed →
REALIZED +$0.0600**

| | |
|---|---|
| maker leg | `CTJ1B3GJTYBQ` OUTCOME_SIDE_YES 1 @ 0.3500, `ORDER_STATE_FILLED` |
| taker leg | `CTJ1Z8JPEYC6` OUTCOME_SIDE_NO 1 @ 0.5700, `ORDER_STATE_FILLED` |
| matched contracts | 1 |
| residual | 0 |
| pair cost | 0.9200 |
| payout (matched pair, either outcome) | 1.0000 |
| gross pair edge | +0.0800 |
| venue commission, total | −0.0200 |
| **REALIZED NET** | **+$0.0600** |
| settlement | `MARKET_STATE_EXPIRED`, settlementPx 0.0000 → winner DOWN |
| open orders after sweep | 0, `sweep_verified: true` |
| positions after settlement | empty |
| cash balance | $250.07425 → **$250.13425** = **+$0.06000** |

Behaviour: `holds 36, requotes 0, cancels 0, takes 1`. The opening maker held
its level and did not chase; completion came from the reactive taker when the
book moved 0.35/0.36 → 0.43/0.44 and made crossing fee-safe.

## Fee schedule: the venue matches `pmus_fees.py`

There was **no schedule miss and no missing rebate**. The two calculations are
different by design, and the error was comparing one against the other:

| leg | unrounded bound | venue cashflow (`cents=True`) | venue actual |
|---|---|---|---|
| taker NO @ 0.57 | 0.01703445 | **0.02** | 0.0200 |
| maker YES @ 0.35 | 0.00284375 | **0.00** | 0.0000 |

`cents=False` is the unrounded coefficient for conservative decision bounds;
`cents=True` applies the venue's statement rounding. Cash economics at a known
quantity must call the model with the WHOLE order quantity and `cents=True` --
rounding per contract and multiplying overstates a small fee (10 contracts at
0.57: 0.17 correct, 0.20 that way).

## What this proves, and what it does not

**Proven: the execution substrate.** The proven serializer
(`l5_order_body.build` + `preview_envelope`, `price.currency`, exactly one NO
inversion), authenticated-WS decisions (24/30 ladder levels, ~80ms book age,
503 book updates), positive terminal proof before any order is forgotten,
partial-fill delta accounting, and a verified post-sweep report. It is no
longer theoretical.

**Not proven: economics.** One pair in one window says nothing about
$5/window. `holds 36 / requotes 0` shows the hold behaviour executed; it does
not show holding beats repricing. The completion was a reactive take, so the
patient maker-complement path is still untested live.

## Defects this run exposed, fixed as accounting-only afterwards

1. Taker metadata lacked `maker: false`, and `reconcile()` defaults a missing
   `maker` to True -- so the crossing leg logged as a maker fill.
2. `rebates_separate` summed the maker rebate over every side holding
   inventory, crediting the taker leg with a rebate it can never earn
   (reported 0.0059 = 0.0028 + 0.0031; only 0.0028 was applicable, and
   0.00 was actually credited).
3. The auditor inferred maker/taker from the venue's reply, but the venue
   returns `participateDontInitiate: null`. It now reads the fact from our own
   durable submission record, and reports UNKNOWN rather than defaulting when
   that record is absent.
4. Fee reporting now carries both `*_bound_unrounded` and
   `*_cashflow_rounded`, and computes them from the EXECUTED price and
   quantity. Venue commission stays authoritative after execution.

## Next

The two-sided continuous controller, in replay/shadow first: rest UP + DOWN →
bank the matched pair → repair the missing side → WAIT vs SELL/flatten when
completion deteriorates → reset → repeat. The substrate above is the fixed
foundation it must reuse, not reimplement.
