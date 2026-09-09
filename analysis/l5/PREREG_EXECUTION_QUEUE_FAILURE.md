# PREREGISTRATION — execution / queue failure diagnosis (A/B/C/D/E)

**Frozen: 2026-09-09, before any classification was computed and before the
order population was pulled.** Written from the record schema alone, which was
already known from committed code (`src/l5_complete_no_fill_forensics.py`) and
the 64-row `analysis/l5/r4_orders.jsonl` extract. No distribution over the
categories below has been seen.

Owner GO: attack the **38% execution/queue bucket** of the R5F loss
attribution. *"Reconstruct every real complement order after a first fill and
determine which failure actually occurred... using the actual orders that lost
our actual money, not shadow opportunities."*

Target, unchanged: **P(acceptable completion | first leg filled) 64.29% →
69.94%**, i.e. recover ≈ **5.66 acceptable completions per 100 first-filled
markets** on these cohort economics.

---

## 1. The evidence ceiling, stated first

**The order record contains no trade prints.** Every field available per order
is: `px`, `side`, `relation`, `stage`, `qty`, `filled_qty`, `end_reason`,
`rest_s`, `order_id`, `slug`, `tick`, `touch_join`, `l5_touch_px`,
`l5_touch_reached_by_opposite_ask`, `fresh_opp_ask`, `rejection_class`,
`rejection_margin_ticks`, and

```
rest_book = { min_opp_ask, max_touch, polls, last_cf }
```

`rest_book` is an **aggregate of quotes** over the resting window — the best
the opposite ask ever got, the highest L5's touch ever got, and how many polls
that aggregate is built from. It is **not** a time series and it is **not** a
trade tape.

**Consequence, and it is a hard limit on the owner's question A.** "Nobody
ever traded at our complement price" is *not observable from this evidence*.
We rest as maker; a trade at our price needs a taker to cross down to us,
which can happen without the resting opposite ask ever moving. What we can
observe is whether the opposite **quote** reached our price.

Category A below is therefore named for what is actually measured. Calling it
"nobody traded there" would be the eighth-failure pattern again: a plausible,
populated, wrong number.

**Two directional bounds follow, and they are asymmetric:**

* **B is a LOWER bound.** We only see crossings that a poll happened to catch.
* **A is an UPPER bound.** Any unsampled crossing belongs in B, not A.

Sampling cadence (`rest_s / polls`) will be reported with the result so the
size of that gap is visible rather than assumed away.

---

## 2. Population

Every order with `relation == "complete"` submitted by the live R5F campaign
in a session where a first leg (`open` or `extend`) **actually filled**.

* Real submitted orders only. No shadow orders, no modelled orders, no
  fill-assumed counterfactuals.
* A session qualifies on `filled_qty` of a prior `open`/`extend` order in the
  same `session_id` being non-zero.
* Orders excluded for failing that test are **counted and reported**, never
  silently dropped.
* Deduplication by `(order_id, stage)`, matching `l5_completion_economics.all_orders`.

---

## 3. The five categories — mutually exclusive, in strict priority order

Each order receives exactly one label. Priority order is part of the frozen
rule, because an order can satisfy more than one condition.

| # | label | test | fix it implies |
|---|---|---|---|
| 1 | **D — venue/technical** | `end_reason` starts with `rejected` | fix the plumbing |
| 2 | **E — insufficient evidence** | no `rest_book`, or `min_opp_ask` missing, or `polls < 5` | instrument, do not conclude |
| 3 | **B — queue / priority / liveness** | `min_opp_ask <= px` | queue positioning |
| 4 | **C — executor ended it early** | `min_opp_ask > px` **and** `rest_s < R50` | our own deadline rule |
| 5 | **A — quote never reached our price** | `min_opp_ask > px` **and** `rest_s >= R50` | complement pricing / aggressiveness |

`R50` := the **median `rest_s` of FILLED complete orders in this same
population**, computed by this frozen procedure at analysis time.

`R50` is deliberately *derived*, not chosen. Hand-picking a seconds threshold
is exactly the knob that would let me tune the answer, so the rule fixes the
procedure and accepts whatever number the filled population yields. The p75
equivalent (`R75`) will be reported as a **sensitivity only** and cannot
change the verdict.

**Why B outranks C and A.** If the opposite ask reached or crossed our resting
bid and we did not fill, the venue either matched someone ahead of us or our
order was not live. That is a fact about our execution, and it is not
weakened by anything the deadline did afterwards.

**Why C outranks A.** If we cancelled before filled orders typically fill, we
never learned whether our price was reachable. Calling that "our price was
unrealistic" would blame pricing for a timeout.

---

## 4. Sufficiency bars — set from the effect the decision needs

The pre-entry-drift test failed with a power bar (30 opportunities) too weak to
separate its own threshold from the null. That defect is not repeated here.

* **N1 — volume.** At least **200** orders classified into A/B/C/D (E excluded
  from this count). Below that, report the deficit and **no dominance claim**.
* **N2 — dominance.** A category is called **dominant** only if the lower bound
  of its 95% Wilson interval **exceeds the point estimate of the
  second-place category**. A bare plurality is not dominance.
* **N3 — evidence integrity.** If **E ≥ 40%** of the population, the study
  cannot answer the owner's question. The deliverable then becomes an
  instrumentation change (record trade prints during rest), **not** a strategy
  change, regardless of how the remaining 60% splits.

---

## 5. What each verdict commits us to, decided in advance

| dominant | what it means | next step |
|---|---|---|
| **A** | our complement quote sat outside the book | measure the price improvement needed for a fill; pricing/aggressiveness proposal |
| **B** | the book came to us and we still missed | queue positioning becomes P0 |
| **C** | our own deadline killed live orders | the deadline/re-quote rule is the candidate change |
| **D** | plumbing | rejection classes, ties into open task #93 |
| **no dominance under N2** | the bucket is heterogeneous | report the split honestly; no single fix is warranted |

No result here authorises a live order, a cap change, a gate change, or a
money movement. Any change reaching the live path requires separate owner
approval.

---

## 6. Kill condition

If the classification cannot be computed under N1, or E breaches N3, this is
reported as **UNDERPOWERED** or **EVIDENCE-LIMITED** and the categories are
**not** re-cut, re-thresholded, or re-prioritised to manufacture a verdict.
`R50` moves only as its frozen procedure dictates.

Under the owner's standing cadence — *measure → falsify → keep/kill → next
largest discrepancy* — an evidence-limited outcome kills the *analysis*, not
the bucket, and the honest next step is instrumentation.

---

## 7. Accounting

Nothing in this study is a fill. These are historical submitted orders whose
outcomes are already settled and already counted in the R5F realized record.
No number here restates P&L. Standard unchanged: **filled → settled → realized.**
