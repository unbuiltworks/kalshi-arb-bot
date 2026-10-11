# CANDIDATE V2 — book state does NOT explain SWITCH. KILL the model class.

Spec fingerprint fixed before scoring. Historical replay on third-party
public data; no order sent; **nothing here is realized profit for our
account.** No live authorization sought. No V3 threshold fishing.

## Power, stated before any result

The overlap is **27 markets / 3,073 fills**, and fills per market run
**max 390, median 13, min 1**. The independent unit is the **market**, not
the decision row. A chronological 70/30 split would leave **~9 OOS
markets**, which is **not a defensible out-of-sample test — so none is
claimed.** V2 uses **grouped leave-one-market-out cross-validation** over
all 27 markets. Book coverage is **98.43%** of decision points (1,566 of
1,591), so this is not a coverage failure.

## The three state variables the brief named as decisive

**Mint sum, `bid(own) + bid(complement)` — first-class, and uninformative:**

| bin | n | markets | P(SWITCH) |
|---|---|---|---|
| < 0.95 | **4** | 3 | 0.5000 |
| 0.95–0.98 | **16** | 9 | 0.4375 |
| **0.98–1.00** | **1,463** | 24 | **0.3185** |
| ≥ 1.00 | 83 | 17 | 0.3614 |

**93.4% of all decisions sit in one 2¢ bin**, where P(SWITCH) = 0.3185
against a base rate of **0.3225** — i.e. exactly the base rate. The
apparent ordering lives entirely in bins of n=4 and n=16.

This is an important refinement of my own earlier framing. I reported the
mint sum as "the mechanism". It survives the tautology check (exactly
1.0000 in only 0.8% of instants, against 97.8% for `bid+ask`) — but
because L5 trades a **1¢ book**, `bid+bid` is mechanically ~0.98 almost
always. **It is variable enough not to be tautological, and not variable
enough to be informative.** The mint explains *how* 31.6% of his fills
execute at or above the ask; it does not predict *when* he switches.

**Marginal pair cost if switching now — not monotone:**
0.3421 (<0.96) → **0.2751** (0.96–1.00) → 0.3040 (1.00–1.05) → 0.3259
(≥1.05). Range 0.275–0.342, straddling the base rate with no ordering.

**Inventory imbalance — monotone, and pointing the wrong way:**
mild 0.3432 → balanced 0.3352 → one_sided 0.2759 → **heavy 0.1842**.
P(SWITCH) *falls* as imbalance rises. A completion-seeking policy would do
the opposite. L5 does not hurry to complete an imbalanced book.

## Rare-action discrimination — the primary metric

| | value |
|---|---|
| **PR-AUC (average precision) for SWITCH** | **0.3555** |
| SWITCH base rate | **0.3225** |
| **lift over base rate** | **+0.0330** |
| precision at the untuned base-rate threshold | 0.3495 |
| recall at that threshold | 0.5129 |
| n scored (grouped CV) | 1,566 |

**A PR-AUC of 0.3555 against a 0.3225 base rate is not discrimination.**
V1 without the book predicted 0 of 17,096 held-out switches; V2 with
98.4% book coverage adds **3.3 points of average precision**. The book is
not the missing information.

## Why — and this is the finding that reframes the question

**71.6% of multi-fill bursts contain BOTH outcomes.** Grouping fills into
bursts of consecutive trades ≤5s apart: 637 bursts, 218 with ≥3 fills, and
**156 of those 218 (71.6%) are filled on UP *and* DOWN inside five
seconds**. Inter-fill gap median 3.0s, p10 1.0s.

**So the "SWITCH decision" we have been trying to predict is largely not a
decision.** L5 holds standing two-sided interest and is filled on whichever
side a counterparty arrives against. Which outcome fills next is the
*counterparty's* choice, not his.

That single reading explains every result in this programme that otherwise
looked like a modelling failure:

* **88.3% of his fills are maker** — he is resting, not taking;
* **the touch falls toward his order** (61.6% beyond −4 ticks at T−30, only
  12.7% at T−1) — the market comes to him;
* **adverse selection ≈ −$0.0041/fill, CI spanning zero** — he is not
  paying to cross;
* **switch timing is unpredictable from every observable state** — because
  the arrival process, not his policy, determines it.

## Decision: KILL the behavioural model class

The brief's GO condition — material out-of-sample SWITCH discrimination —
**is not met**, and the gap is not a tuning gap. Predicting a decision L5
does not make cannot be fixed by more features or a better classifier, and
no V3 threshold search is warranted or proposed.

**What remains absent is order ownership.** We observe L5's *fills*; we
have never observed his *resting orders*. No public archive attributes
orders to a trader — the book-acquisition search confirmed this holds for
every route including the paid vendors. Without it we can model the
arrivals against his ladder but not the ladder itself, and the ladder is
the policy.

That is a different and much narrower research object than a sequential
action model: **what two-sided ladder, resting over what horizon, at what
offsets from the touch, generates this fill distribution?** It is
answerable only where we know which orders are ours — i.e. prospectively,
on a venue where we place them. It is not answerable from any historical
archive.

## What survives from V1/V2

* **Higher-price EXTEND must be permitted** — 90.6% of L5's real EXTENDs
  are unreachable under the 5¢ gate; permitting them lifts reproduction
  from 9.43% to 99.71%. Still the single best-supported change, still an
  **authorization** matter rather than an edit.
* **The edge is fill quality, not sequencing** — −$0.0041/fill against
  Gemini's −$0.2380, intervals not overlapping.
* **The residual is the leak** — −$30,654 against +$80,652 matched.
* **Imbalance does not drive completion**, which contradicts the
  inventory-urgency assumption in our own model.

## Not done

No V3. No threshold sweep. No shadow architecture built — the GO condition
for that was explicitly conditional on V2 discriminating, and it does not.
No live orders.
