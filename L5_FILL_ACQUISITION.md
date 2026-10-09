# How L5 acquires fills — on-chain channel decomposition

Scope: the 33 BTC15m markets where our chain backfill of L5's own logs overlaps
the Zenodo L2 book archive. 3,166 L5 fill legs, 2,872 distinct transactions,
77,773 shares purchased. Books read 1s before each fill using real block
timestamps, never interpolated ones.

Artifacts: `analysis/l5/historical_l2/mint_contribution.py`,
`analysis/l5/historical_l2/mint_contribution.json`.

## 1. A retraction first

An earlier transaction-level test over the 760,817-fill activity corpus found
**zero** transactions carrying L5 on both outcomes and I concluded he never
acquires both legs at once. **That conclusion was wrong.** The Polymarket
activity API reports only *one leg* of an atomic two-leg transaction: for all
145 transactions the chain shows as two-sided here, the corpus carries exactly
one leg and one `outcomeIndex`. The API cannot answer the question. Only the
chain logs can, and they answer it the other way.

## 2. Two separable acquisition channels, not one

| channel | shares | share of purchases | avg price | confirmed? |
|---|---|---|---|---|
| `RESTING_BID` maker, at/below prevailing bid | 45,043 | 57.9% | 0.4829 | yes (book-consistent) |
| `MINT_FEASIBLE` maker, at/above prevailing ask, complement bid reached par | 18,776 | 24.1% | 0.4969 | **no — feasible only** |
| `ATOMIC_PAR_PAIR` taker on both outcomes, one transaction | 12,738 | 16.4% | 0.5027 | **yes — both legs are his own** |
| `TAKER_SINGLE` | 459 | 0.6% | 0.7311 | yes |
| `UNCLASSIFIED` (no book in carry distance) | 757 | 1.0% | — | — |

`AT_ASK_NO_MINT` is **zero shares**: every maker fill priced at or above the
prevailing ask had a complement bid that reached par. There is no residual
bucket of at-ask maker fills that the mint mechanism fails to explain.

Attributed to *matched* inventory (proportional on the scarcer side; the venue
does not record fill-to-fill pairing): resting bid 54.4%, mint-feasible 27.8%,
atomic par pair 17.0%.

## 3. The confirmed finding: atomic complete-set purchase at exactly $1.00

145 of 2,872 transactions (5.0%) carry L5 as **taker on both outcomes of the
same market in one atomic transaction**. In every single one of the 145, the
size-weighted pair price is **exactly $1.00** — histogram `{"1.00": 145}`, no
other value. 131 of 145 have exactly equal leg sizes.

**96.5% of L5's entire taker volume sits inside these transactions**
(12,803 of 13,264 shares). Zero maker fills occur inside them.

So L5's taker activity is not aggression in the ordinary sense. It is a
complete-set acquisition at par: pay $1.00, receive one of each outcome,
redeem $1.00. On a zero-trading-fee venue that is an **economically free
residual-flattening operation** — it moves inventory to matched without
costing or earning anything. It is not a profit source, and it must never be
counted as one.

This reframes the behavioural model: the EXTEND/COMPLEMENT sequence we trained
on is the *profit* channel (maker buys averaging 0.483–0.497), and par-pair
taking is a separate *balance-sheet* channel that the activity API renders
invisible by dropping one of its legs.

## 4. Why PMUS has no equivalent (item 6, answered from source, not assumed)

Checked against the captured venue spec
`analysis/gemini/sell/prediction-markets.openapi.20261004.yaml` (5,465 lines,
31 endpoints). There is no mint, split, merge, complete-set or redeem endpoint.
The only multi-leg primitive is **combos**, and it is not equivalent for three
independent reasons:

1. `CreateComboRequest.legs` requires "two to six **distinct underlying
   contract** legs" with a canonical key like `101:Yes|202:No`. YES and NO of
   the *same* contract is not a legal combo.
2. A combo is a **conjunction** — a new binary contract that pays 1 only if
   every leg resolves to its required outcome. A complete set pays 1
   unconditionally. These are different instruments.
3. The spec states the endpoint "is not currently enabled in production."

Order entry on PMUS is limit/market per contract, and PMUS charges fees. A
par-priced two-leg purchase there is a guaranteed loss of the fees, not a free
operation. **The channel supplying 16.4% of L5's purchases and 17.0% of his
matched inventory does not exist on PMUS and cannot be approximated.**

## 5. Complementary fills: reached, not taken (item 3)

Setting the par-pair channel aside, L5's directional acquisition is 97%+ maker
(63,819 of 65,663 non-par-pair shares). Complementary inventory therefore
arrives overwhelmingly because **price moves to his resting orders**, not
because he crosses for it. The only crossing he does is the par-pair
operation, which is price-indifferent by construction.

## 6. What this does and does not explain about 80 fills/market vs our 1–3

Accounted for so far:

- **venue-dependent, not reproducible:** 16.4% of share acquisition via a CTF
  primitive PMUS lacks (§4).
- **venue-dependent, not reproducible:** 24.1% via being the passive side of
  third-party mints — which also requires the same primitive to exist for the
  *counterparty*.
- **in principle reproducible:** 57.9% via resting bids below the touch. This
  is the only channel PMUS can host, and it is the one our shadow attempts.

That split is itself the headline: **roughly 40% of L5's share acquisition
runs through a venue primitive PMUS does not have.** Even a perfect
replication of his resting-order policy addresses at most the 58% remainder,
and does so against PMUS fees and flow rather than Polymarket's.

Still open, and needed before the ranked §6 answer is complete: the PMUS-tape
placement-policy comparison (best-bid resting vs deeper resting vs one-tick
improvement vs continuous two-sided inventory-aware quoting), with
queue-cleared fills and actual opposite-side flow. That measures how much of
the 58% remainder PMUS flow can actually deliver.

## Labels

Nothing here is realized profit. `MINT_FEASIBLE` means a complete-set mint
*could* have paid for a fill at the complement's posted bid; proving it needs
the counterparty's legs, which our backfill does not store. `ATOMIC_PAR_PAIR`
is confirmed because both of its legs are L5's own. No order is attributed to
any trader other than L5, and `complement_best_bid` is a public price level.
