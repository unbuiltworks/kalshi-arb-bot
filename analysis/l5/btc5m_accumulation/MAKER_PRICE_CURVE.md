# Buying the favorite, tested properly -- and why the maker space is closed

Owner hypothesis, 2026-11-14: "buying the favorite and hedging the opposite
side, managing inventory". Tested on the 189-window tape
(`pmus_btc5m_quotes_20261112.jsonl`), 186 windows resolving cleanly.
MAKER ONLY, fill rule identical to the straddle shadow's: a resting bid at
p fills when that side's own price falls TO p.

This was the right thing to test and had NOT been tested. Every result
before it was the 8c longshot straddle, a different trade; the
breakeven-8.70%-pair-rate arithmetic says nothing about favorite entry.

## 1. The result

OPEN at p, COMPLETE opposite so the pair costs <= target, else hold to
settlement. 24 cells, entries 0.55..0.90 x targets 0.92/0.96/0.98:

**All 24 negative**, from -3.41 to -30.10 c/share, t from -2.36 to -9.13.

The hedge was NOT the problem. Completion succeeded 80% of the time.

## 2. Why -- the number that explains it

The favorite we actually get filled on does not win at the rate we paid for:

| maker bid at | needs to win | REALIZED | EV c/share | t |
|---|---|---|---|---|
| 0.05 | 5% | 6% | +1.49 | +0.82 |
| **0.08** | 8% | 11% | **+2.75** | +1.21 |
| 0.12 | 12% | 13% | +1.44 | +0.57 |
| 0.20 | 20% | 19% | -0.65 | -0.22 |
| 0.30 | 30% | 28% | -2.04 | -0.62 |
| 0.40 | 40% | 38% | -2.37 | -0.66 |
| 0.50 | 50% | 41% | -9.14 | -2.53 |
| 0.60 | 60% | 39% | -20.75 | -5.78 |
| 0.70 | 70% | 43% | -26.99 | -7.41 |
| 0.80 | 80% | 45% | -35.38 | -9.68 |
| 0.90 | 90% | **44%** | -45.91 | -12.58 |
| 0.95 | 95% | **44%** | -50.91 | -13.95 |

**The realized win rate SATURATES at ~44% above 0.50.** It tracks the
price only below ~0.15.

The mechanism: a resting bid at 0.90 fills only when someone is DUMPING
the favorite down to 0.90, and a contract being dumped is no longer a 90%
proposition. We are filled by the person who knows the price is stale.
Held (uncompleted) cases won **0/30, 0/43, 0/57, 1/68** -- non-completion
happens precisely when our side collapsed.

So the favorite is not the best place to apply this; it is the WORST. The
adverse selection is identical in probability and 11x larger in dollars.

## 3. EXTEND does not rescue it, and the per-share view lies

The L5 sequence is OPEN -> EXTEND -> COMPLETE, so averaging down was
tested too. Per share it looks like it helps. In TOTAL DOLLARS:

| open | extends | shares | $ total | $/share | t |
|---|---|---|---|---|---|
| 0.60 | 0 | 171 | -12.36 | -0.072 | -3.87 |
| 0.60 | 2 | 429 | +5.82 | +0.014 | +1.39 |
| 0.60 | 3 | 564 | +7.58 | +0.013 | +1.37 |
| 0.70 | 0 | 180 | -24.62 | -0.137 | -5.80 |
| 0.70 | 3 | 628 | -38.38 | -0.061 | -3.28 |
| 0.80 | 0 | 183 | -40.56 | -0.222 | -7.69 |
| 0.80 | 3 | 691 | **-95.84** | -0.139 | -5.74 |

At 0.80 the PER-SHARE figure improves from -0.222 to -0.139 while the
TOTAL LOSS MORE THAN DOUBLES, -40.56 -> -95.84. That is the martingale
illusion stated numerically: EXTEND buys more shares exactly when the
position is going against us, so the average improves while the account
gets worse. Any future report of averaging down must be in total dollars.

Only p=0.60 with 2+ extends is positive, t +1.39 against sqrt(2 ln 12) =
2.23 for the twelve cells examined. **Does not clear.**

## 4. The space is CLOSED, not merely unexplored

On a binary market, selling UP at 0.90 IS buying DOWN at 0.10: the same
contract, and a resting offer at 0.90 fills on exactly the event a resting
bid at 0.10 fills on (UP rising to 0.90 == DOWN falling to 0.10).

**So the table in section 2 is not the buy side. It is every maker
strategy at every price on both sides.** There is no untested
sell-the-favorite variant behind it. The curve is the whole space, and it
is non-negative only at p <= 0.12, peaking at 0.08 -- which is the
straddle already run, at t +1.21, which does not clear either.

## 5. One sentence

A resting bid is filled by someone who knows the price is about to move,
and the loss scales with what was paid: 8c at 8c, 90c at 90c, on the same
~44% coin flip.

## 6. What is NOT ruled out

This closes MAKER BUY-AND-HOLD, which is the entire strategy family the
engine can currently express. It does not rule out:

- **Taker entry** -- paying the spread to choose the moment instead of
  being chosen. Currently disabled by owner policy, and the 4.87c adverse
  selection against a ~1c spread is the reason it was disabled.
- **A 15-minute window**, where a collapsed leg has 3x as long to recover
  and lone legs win 18.0% against the 5m's 3.8% (z = 3.42). Gated in
  DECISION_20261114.md.
- **Credited maker rebates**, which are additive and must never be assumed
  before they are credited and source-accounted.

---

# The taker swing: adverse selection confirmed and quantified, edge still absent

Section 6 named taker entry as the one structural escape, because every
failure traced to being FILLED rather than CHOOSING. Tested on the same
tape. Read-only.

    maker buy  UP = bid    DOWN = 1 - ask   (filled when price comes to us)
    taker buy  UP = ask    DOWN = 1 - bid   (we pay up, at a moment we pick)

## 1. Adverse selection is real, large, and now measured

Realized win rate against the price paid:

| price | maker realized | taker realized | needs | maker gap | taker gap |
|---|---|---|---|---|---|
| 0.50 | 41% | 51% | 50% | -9 pts | +1 pt |
| 0.60 | 39% | 56% | 60% | -21 pts | -4 pts |
| 0.70 | 43% | 69% | 70% | -27 pts | -1 pt |
| 0.80 | 45% | 77% | 80% | -35 pts | -3 pts |
| 0.90 | **44%** | **83%** | 90% | **-46 pts** | -7 pts |
| 0.95 | **44%** | **93%** | 95% | **-51 pts** | -2 pts |

**Taker win rates TRACK the price; maker rates saturate at ~44%.** The
hypothesis was right: adverse selection, not mispricing, is what killed
every maker strategy. It is worth 27-51 points of win rate at favorite
prices and it nearly vanishes once we choose the moment.

This also says the venue is roughly EFFICIENTLY PRICED. The taker gaps
scatter around zero within a few points, which is what a fair market looks
like.

## 2. And that is exactly why there is no edge

An efficient market has nothing left once the spread and fee are paid.
Median spread 0.0100; fee = 0.0695 * p * (1-p) per share.

ONE entry per window (the bucketed view above inflates n ~12x because all
observations in a window share one outcome), held to settlement, n=186:

| entry | taker c/share | t | maker c/share | t |
|---|---|---|---|---|
| <= 0.05 | +0.50 | +0.32 | +2.45 | +1.35 |
| <= 0.08 | +2.62 | +1.18 | **+3.60** | **+1.58** |
| <= 0.10 | +1.70 | +0.73 | +3.55 | +1.47 |
| <= 0.15 | +0.96 | +0.37 | +4.38 | +1.58 |
| <= 0.20 | -0.32 | -0.11 | +1.54 | +0.53 |
| <= 0.50 | -0.92 | -0.26 | +2.28 | +0.65 |

Buying the favorite as a taker (enter first time a side >= threshold):
-4.97 / -2.82 / -2.56 / -4.11 c/share at 0.60 / 0.70 / 0.80 / 0.90,
t -1.42 / -0.85 / -0.88 / -1.70. All negative, none significant.

Bar for 8 targets: sqrt(2 ln 8) = 2.04. **Nothing clears it, either mode,
either end of the price range.**

At the cheap end the taker is WORSE than the maker: a 1c spread on an 8c
position is 12.5% of the trade, and adverse selection at 8c can only cost
8c. That is why the maker straddle was the best thing found all along --
not because it has an edge, but because it is where adverse selection is
cheapest in absolute terms.

## 3. The closure

    Maker entry loses to adverse selection.
    Taker entry loses to spread plus fees.
    There is no gap between them.

That is a complete statement about buy-and-hold on BTC5m, at every price
on both sides (by the sell/buy identity in section 4 above), in both
execution modes. The best cell on the entire board is the 8c maker entry
at +3.60 c/share, t +1.58 on 186 windows, which does not clear its bar and
is consistent with zero.

## 4. What this does NOT close

- **The 15-minute window.** Lone legs win 18.0% vs the 5m's 3.8%,
  z = 3.42. That difference is about WINDOW LENGTH, not price or execution
  mode, so nothing here touches it. Gated in DECISION_20261114.md.
- **Credited maker rebates.** Additive, and the maker side is where the
  rebate would land -- but never assumed before it is credited and
  source-accounted, and at +3.60 c/share with t +1.58 a rebate would be
  decorating a result that is not yet distinguishable from zero.
