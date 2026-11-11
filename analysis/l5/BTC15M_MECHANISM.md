# How L5 gets to ~96 % two-sided BTC15m at ~0.962 — and why our clone cannot

**527 BTC15m windows, 506 of them two-sided (96.0 %).**

The short answer, and it inverts the assumption the clone was built on:

> **L5 does not wait for a cheap pair. He goes two-sided almost immediately at a
> pair cost of about 0.99 — a price our ceiling forbids — and then grinds the
> average down to 0.962 over a median of 121 further fills.**

Our clone refuses to form the pair at 0.99, so it never enters the mechanism at
all. The 0.96 ceiling is not mistuned; it is applied **at the wrong point in
the path**.

---

## 1. The pair cost is a path, not an endpoint

| | median |
|---|---|
| pair cost **at first formation** | **0.9900** |
| pair cost **at window end** | **0.9624** |
| change | **−0.0229** |

63.8 % of windows improve; 34.4 % get worse. It is a process with variance, not
a ratchet.

| within our 0.96 ceiling | |
|---|---|
| at formation | **141 / 506 = 27.9 %** |
| at window end | 245 / 506 = 48.4 % |

**A clone that requires ≤ 0.96 to form the pair rejects 72 % of L5's own
entries.** Every earlier statement of "his median pair cost is 0.9624" was
measuring the *destination*; the gate we apply sits at the *origin*.

## 2. He enters early and pairs fast

| | p10 | median | p90 |
|---|---|---|---|
| TTE at first leg (of 900 s) | 783 | **887** | 894 |
| seconds to complement | 3.0 | **26.0** | 120.0 |
| complement at fill # | 2 | **3** | 9 |
| extends before complement | 0 | **1** | 7 |

He is in within ~13 s of the window opening, and two-sided within ~26 s, on his
third fill. **The p10 of 3 s is the interesting tail** — a complement three
seconds after the first leg is very hard to explain by reacting to the first
leg, and is the strongest single piece of evidence for pre-resting quotes
(tested separately in P2).

## 3. And then he trades a great deal more

**Median 121 further legs after formation** (p90 256, max 457). Windows with
≥ 3 further legs improve by a median −0.0231; the three windows with no further
legs improve by exactly 0.

This is not a three-order sequence. It is a continuous market-making operation
across the whole window, and **the 0.962 figure is the running average of that
operation**, not a price he ever paid.

## 4. The pair cost is CONTROLLED, not emergent

The discriminator, over 506 formations:

| signal | value | reading |
|---|---|---|
| pair-cost spread | 0.0753 | |
| spread if the two legs were independent | 0.1562 | |
| **tightening ratio** | **0.4817** | less than half as wide as chance |
| **first-leg vs complement correlation** | **−0.6499** | cheaper first leg → dearer complement |

**CONTROLLED.** He is steering the pair, not collecting it. A cheaper first leg
is systematically followed by a dearer complement, which is what a trader
working to a pair budget does and is not what two independently attractive
maker fills produce.

But note *what* he steers: the **running average over ~121 legs**, not the
entry. Both facts are needed, and either alone misleads.

**This does not mean he targets 0.96.** The median at formation is 0.99 and the
median at the end is 0.9624; a target read off either number would be reading
our own assumption back out of the data. What the evidence supports is that
there is a control loop, not what its set point is.

## 5. Why the clone cannot reproduce any of it

| | L5 | our clone |
|---|---|---|
| entry pair cost | ~0.99 | **refused above 0.96** |
| fills per window | ~124 | one quote per observed L5 fill |
| complement timing | median 26 s, p10 3 s | ≥ 15 s observation lag, then a quote |
| pair cost management | running average over the window | no pair constraint at all (arm R) |

Four independent blockers, and the first one alone is sufficient: **the clone
declines the trade that starts the mechanism.**

## 6. What this does and does not license

- It does **not** license widening the ceiling. It is evidence about *L5's*
  economics, not about ours, and the ceiling stays frozen at 0.96 as an
  `adapted_executable_pair_ceiling`.
- It **does** establish that 0.96-at-formation and 0.96-as-fidelity are
  different rules, and that the second one is now measurably wrong: it censors
  72 % of the behaviour it claims to define.
- The honest next questions are P2 (can the 3-second complements be explained
  without pre-resting?) and P4 (what would *our* settled economics be at each
  ceiling, measured, with no policy change).

## Provenance

- `analysis/l5/pair_formation/btc15m.py` · `tests/test_l5_btc15m.py` (13 tests,
  including a negative control that makes the controlled/emergent
  discriminator able to return either answer)
- source: `analysis/l5/l5_canonical_sequence.csv.gz`, 527 BTC15m windows
- our 0.96 ceiling is reported for comparison throughout and filters nothing
