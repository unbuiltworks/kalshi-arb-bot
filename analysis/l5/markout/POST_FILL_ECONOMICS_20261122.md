# Post-fill economics — RN1 against L5 (22 Nov 2026)

Two read-only studies. Details and tables: `rn1/RN1_MARKOUT_RESULT.md`,
`l5/L5_MARKOUT_RESULT.md`. Issue #251.

Markout = what the bought side was worth a short time after the fill, minus
the price paid. It is a mark, not a result. Settled results are stated
separately and are never added to it.

## 1. RN1 (another trader, another venue; public data; 3 days, 11,064 fills)

| | 5 s | 30 s | 2 min | 5 min |
|---|---|---|---|---|
| Resting (maker) fills, cents per share | +0.24 | +0.15 | +0.33 | +0.24 |
| 95% interval | +0.17 to +0.31 | +0.04 to +0.26 | +0.14 to +0.52 | −0.16 to +0.68 |
| Taking (taker) fills, before a 0.90c fee | +1.20 | +1.44 | +1.67 | +1.71 |

- Its resting bids fill 0.63c below the last price before the fill. Later
  sellers then trade 0.2c to 0.5c lower, and about 0.3c is kept.
- The edge per share is small. It is the same order as the +0.8% per
  dollar found in the 30-day reconciliation. The size of the profit comes
  from volume (about $2M a day across all its markets), not from a large
  edge.
- Same-side adds have the best resting markout (+0.58c at 2 min, +0.93c at
  5 min). The first buy of the opposite side has the worst (+0.14c, −0.44c).
- Its taker fills are followed by a move of 1.2c to 1.7c in its favour:
  it takes when the price is about to move.
- Not settled: an earlier draw of 300 markets gave −0.59c at 5 min for
  resting fills. Three days cannot separate the settled result from zero.

## 2. L5 (our fills, 14–18 Nov, 1,016 fills; estimated from our own quotes)

| | 5 s | 30 s | 2 min | 5 min |
|---|---|---|---|---|
| All fills, cents per contract | −1.4 | −1.3 | −0.7 | +0.6 |
| 95% interval | −2.0 to −0.8 | −2.0 to −0.5 | −1.4 to +0.1 | −0.4 to +1.6 |
| Coverage of fills | 55% | 51% | 63% | 68% |

By sport at 30 s: college football +0.5c (n 248), NBA −0.1c, CS2 +0.5c;
ATP −2.1c, ITF women −2.7c, table tennis −3.0c, Dota 2 −3.9c.

By period: 14–16 Nov opening fills (one contract, tight markets) +1.3c at
30 s; 17–18 Nov opening fills (five contracts, reward-first, wide markets)
−4.2c, with the mid 5.7c lower two minutes after the bid was placed.

22 Nov, exact (recorded book, 8 fills): opening fills about +1c; the Genoa
add was +2.0c at the venue's fill time and −21.5c thirty seconds later.

## 3. Settled results of the same L5 fills (exact)

| Time from first leg to paired | Cycles | Settled, cents per contract | $ |
|---|---|---|---|
| under 30 s | 59 | +1.61 | +2.61 |
| 30 s to 2 min | 214 | −0.47 | −3.96 |
| 2 to 5 min | 81 | −1.75 | −7.12 |
| 5 to 15 min | 55 | −0.84 | −1.95 |
| over 15 min | 48 | +2.53 | +4.60 |
| never paired | 47 | −43.54 | −34.90 |

All 47 legs that never paired lost. That is not chance: the other side
was bid only at a price that made the pair cost 0.97 or less, which is
reached when the price has moved in our favour. Winners were paired (their
gain capped at a few cents) and losers were kept.

## 4. The new test applied to the old fills (a filter, not a simulation)

`replay/REPLAY_RESULT.md`. The marginal test was applied to the 1,016
historical fills, using the international one-minute price at the fill.
It can only remove fills; it cannot add the ones the new rule would have
had. Only "actual" and "allowlist" are measured; the gated columns are
estimates.

| | Actual | Today's allowlist | Allowlist + test | Test, any priced market |
|---|---|---|---|---|
| Fills kept | 1,016 | 593 | 171 | 328 |
| Dollars spent | 953.91 | 389.99 | 94.42 | 279.18 |
| Settled trading before fees | −40.71 | −14.40 | −20.70 | −39.18 |
| Per dollar, 95% interval | −6.7% to −2.1% | −9.5% to +0.1% | −42.3% to 0.0% | −29.8% to +1.5% |
| Rewards paid on kept markets (upper bound) | 13.82 | 3.89 | 2.72 | 12.14 |

- No difference between the columns is distinguishable from noise. The
  gated columns lean worse, because the test rejects most of the old
  opposite-side fills and a filter puts nothing in their place: first legs
  stay unpaired and five days of one-sided results decide the number.
- Short-horizon check on the same fills, against the international price:
  fills the test accepts are about break-even five minutes later (0.0
  ticks; −1.4 ticks on the allowlist leagues, n 171); fills it rejects
  lose about 2 ticks. The edge it sees at the fill (+$36) is mostly gone
  in five minutes: the one-minute price was stale at the moment of the fill.
- The allowlist alone removed $26 of the $41 loss and $11.70 of the $15.63
  rewards.

So the historical data do not show that the test makes this trading
profitable. They show it removes the fills that lose and keeps fills that
are about flat before any pairing or reward.

## 5. What this says about the controller

- RN1's favourable post-fill movement is a small, retained share of the
  spread on liquid markets where being filled costs little. There is no
  evidence of a large signal in its resting fills. Its taker fills do show
  timing, at a size the fee mostly consumes.
- L5 matched that in liquid sports priced on the international venue and
  lost 2c to 4c per contract in thin sports with no such price.
- So: bid only where a fair value exists; require the fair value to cover
  the cost of being filled; do not buy the other side merely because the
  pair would pay. All three are in the marginal test now on the dev branch.
- Not established: that any of this is profitable. The replay is flat to
  negative, and a fair value that looks favourable at the fill is partly a
  stale price. What our fills do with the test on a 20-second-fresh price
  can only be measured live; every fill now records its own markout.
- The 1c adverse-selection allowance is a setting the first live fills
  should confirm or raise.
- Scale: RN1's edge is about 0.3c a share. At two contracts an order this
  controller cannot earn meaningful money whatever its edge. The purpose of
  a small validation is to measure the edge, not to make the target.
