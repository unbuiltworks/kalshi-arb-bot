# Is L5's 96.24¢ emergent inventory VWAP, or individual pair acquisition?

**Answer: emergent.** It is a running VWAP that declines with accumulation, not
a price he acquires pairs at.

**527 BTC15m windows, 62,020 fills.** Cost basis from `usdcSize`, never
price×size. Pair cost is the running VWAP sum
`(UP spend / UP qty) + (DOWN spend / DOWN qty)`, undefined until both sides are
held.

## 1. He never sells

**66,949 BTC15m trades, every one a BUY. Zero sells.** Positions are only ever
redeemed at settlement. So the matched VWAP can move in exactly one way — by
buying *more*, at better prices. The mechanism is literally averaging down a
two-sided book, and no individual pair is ever "acquired at" the final number.

## 2. At acquisition the economics are NOT cheap

| first formation | |
|---|---|
| median pair cost | **0.9900** |
| at fill # | 3 (p10 2, p90 9) |
| TTE | 851 s of 900 — about **49 s into the window** |
| within our 0.96 ceiling | **141 / 506 = 27.9 %** |

**A gate requiring ≤ 0.96 before either quote may rest rejects 72 % of L5's own
entries.** That is precisely what R/S/D enforced, and why it never entered the
mechanism.

## 3. The cost declines monotonically with accumulation

Sampled at deciles of **each window's own fill count**, so every point is the
same population — this is the composition-stable view:

| % of that window's fills | median pair cost | median imbalance |
|---|---|---|
| 10 % | 0.9968 | 0.359 |
| 20 % | 0.9929 | 0.230 |
| 30 % | 0.9936 | 0.167 |
| 40 % | 0.9901 | 0.137 |
| 50 % | 0.9845 | 0.103 |
| 60 % | 0.9843 | 0.102 |
| 70 % | 0.9777 | 0.094 |
| 80 % | 0.9740 | 0.086 |
| 90 % | 0.9655 | 0.083 |
| **100 %** | **0.9603** | 0.098 |

Imbalance falls with it — 0.359 → 0.083 — then ticks **up** to 0.098 at the
end, consistent with late one-sided accumulation.

## 4. The decline is real, not a change of population

The raw fixed-checkpoint table looks flat at ~0.99 through fill 100 and then
drops. That is partly composition: `after_100` covers 290 windows and `final`
covers 506. **Paired within the same windows** (n = 290 with ≥ 100 fills):

| | |
|---|---|
| median at fill 100 | 0.9829 |
| median at final | **0.9599** |
| median delta | **−0.0177** |
| improved | **218 / 290 = 75.2 %** |

Nor is the cheap final an artifact of short windows — final pair cost by window
length: <20 fills **0.9716**, 20–59 **0.9541**, 60–119 **0.9595**, 120–249
**0.9596**, ≥250 **0.9639**. Every bucket except the shortest lands at ~0.96.

## 5. Same-side persistence, preserved

| over 61,493 consecutive fill pairs | |
|---|---|
| **next fill is the SAME side** | **69.2 %** |
| next fill repairs imbalance | 48.3 % |
| next fill is the cheaper-running-VWAP side | 57.5 % |

Same-side persistence is strong and is **not** a rebalancing rule: repairing
imbalance is a coin flip (48.3 %). The cheaper-VWAP tilt is real but weak
(57.5 %). Whatever selects the next side, it is dominated by persistence, not
by pair repair.

## What this means for the failed arms

R/S/D is falsified as a mechanism and stays frozen. It required an UP quote and
a DOWN quote to jointly clear 0.96 *before either could rest*. L5's own entries
clear that bar 27.9 % of the time. The arms therefore refused the entry that
starts the process, and the pairs they did form (1.085 / 1.098) were formed
under a different regime entirely.

**The 0.96 ceiling is not mistuned. It is applied at the wrong point in the
path** — at the origin, where L5 pays ~0.99, rather than at the destination,
which he reaches by accumulating.

## Not yet done

The control rule (what selects side and size at each step) is measured only at
the three marginal rates above. Arm I is **not** built, and nothing here
authorizes widening 0.96, changing depths, adding signals, or any live money.
