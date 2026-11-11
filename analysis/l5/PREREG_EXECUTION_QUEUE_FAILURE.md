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

---

## 8. AMENDMENT — 2026-09-09, owner clarification, **before any distribution existed**

Dated and recorded here because it is an amendment made **before** the order
population was pulled and **before** any category count was computed. The
Railway extraction was still running when this was written. Had it come after a
result it would not have been permissible.

Owner, verbatim: *"Claude **cannot pretend that the market touching our price
means we would have filled**. If the existing evidence can't distinguish
'nobody traded' from 'people traded but we lost queue priority', the correct
result is **UNOBSERVABLE**, not another modeled answer masquerading as fact."*

### 8.1 No category may be converted into a fill counterfactual

Whatever the split, this study may **not** produce a number of the form "N of
the 5.66 missing completions per 100 were recoverable." Not for B, not for C,
not for any category.

The reason is arithmetic, not caution. Converting a classification into
recovered completions requires P(fill | condition), and every term of that is
unmeasured here: we have no prints, no depth, no queue position, and no fill
IDs. Any such number would be fill-assumed at source — the exact defect already
recorded against the `no_legal_maker_price` counterfactual (+$7.69), which is
labelled fill-assumed and was rejected for that reason.

`recoverable_completions()` in `src/l5_execution_failure.py` therefore **raises**
rather than returning a caveated estimate. The number is unavailable, not
discouraged.

### 8.2 Category B is itself internally UNOBSERVABLE

This goes further than the owner's wording and is the sharper form of the same
point.

`min_opp_ask <= px` means the opposite quote reached or crossed our resting
price while we did not fill. At least three distinct things produce that
observation:

1. **queue priority** — a trade occurred and someone ahead of us took it;
2. **order liveness** — our order was not actually working at that moment;
3. **observation artifact** — the crossed book is a stale or asynchronous
   snapshot and no trade occurred at all.

These imply *different fixes* — reprice/queue strategy, order-lifecycle
plumbing, and data quality respectively. **Without trade prints and fill IDs
they cannot be separated.** So a B-dominant result does **not** license
"queue positioning becomes P0"; it licenses only "something on our side failed
while the book was crossed, and the evidence cannot say which."

The §5 table is amended accordingly: **B dominant → the deliverable is the
instrumentation change in §8.3, not a queue fix.** Rows A, C and D are
unchanged, because each of those is decidable from what we recorded.

### 8.3 The instrumentation spec, if this study returns UNOBSERVABLE

Named in advance so the fallback is a deliverable rather than a shrug. To
answer the queue question directly, the next campaign must record, per resting
complement:

* **trade prints while our order rests** — timestamp, price, size, aggressor
  side; the single missing field that makes A and B decidable;
* **BBO and depth at our price level** — how much size sat ahead of us;
* **complete order lifecycle** — accepted, working, amended, cancelled, with
  venue timestamps, so liveness is provable rather than inferred;
* **fill IDs matched to venue trade IDs** — so our fills and the tape reconcile.

That is the whole list. It is cheap, it is read-only, and it converts this
permanently-unanswerable question into a directly measurable one.

### 8.4 What does not change

P1/P2/P3-equivalents (N1, N2, N3), the strict priority order, the derived R50,
and the kill condition are all **unchanged**. This amendment adds a refusal and
a fallback deliverable; it loosens nothing and it cannot rescue a failed bar.

---

## 9. AMENDMENT — resumed sessions were excluded by a logging artifact

**Disclosed in full, including what I had already seen when I made it.**

### What I had seen

Before this fix the analyser had run twice and returned **UNDERPOWERED** both
times: 65 of 200 classified, counts `A 51 / B 0 / C 8 / D 6 / E 14`. So I had
seen a partial distribution with **A leading**, and no dominance test had been
run or could have been.

### The defect

`session_end` records emitted after a mid-session **resume** contain only the
LATER orders of that session. The `open`/`extend` that created the inventory
sits in an earlier record. Requiring the filling order in the same record
therefore excluded **66 of 98 sessions** — on a logging artifact, not on
economics.

The excluded records prove their own inventory. Example, verbatim:

```
slug aec-mlb-wsh-lad-2026-09-05
inventory {"up":"0.06","dn":"0.04","matched":"0.04","spent":"0.046200"}
filled_actions 1     submitted 6     status SPRING_RESIDUAL_HOLD
relations ['complete' x6]     filled_qty ['0','0.02','0','0','0','0']
```

Real inventory, a real fill, six complements — and no first leg in the record.
A complement cannot exist without inventory, which is what made the 251-order
exclusion visibly wrong rather than merely large.

### The correction

`had_a_first_fill()` now qualifies a session on a filled `open`/`extend`
in-record **or** on the session's own inventory evidence (`spent`/`up`/`dn`/
`matched` > 0, or `filled_actions` > 0). The positional "prior" test still
applies when the filling leg is in-record; when inventory was carried in
through a resume it existed before the record's first line, so every
complement in it is after the first fill by construction.

This identifies the **same fact** the prereg names — the session had a filled
first leg — through fields that survive resumption. §2's definition is
unchanged. Nothing was loosened: sessions with no inventory evidence are still
excluded and counted.

### Why this is not tuning

The honest test is direction of travel. Before the fix A led 51 to 0 at n=65.
Stopping there and declaring A dominant would have been the tuned outcome. The
fix instead **quadrupled** the sample to 330 orders, any of which could have
overturned the leader. Expanding a sample that already favours the current
answer is the opposite of choosing a threshold that flatters it.

The direction of the fix was forced by the defect — a complement without
inventory is impossible — not selected from options.

### Coverage limitation, stated plainly

The recoverable archive holds **368 complete orders**. The earlier
`complete_no_fill_forensics` study reached **868** from a larger log set that
no longer exists in this environment. This study therefore runs on a
**subset**, and N1 is met within that subset, not across the full historical
population.

---

## 10. RESULT — 2026-09-09 — **A DOMINANT. Our complement quote sat outside the book.**

N1 met: **221 classified of 200 required**. 330 complements after a first fill,
across 114 qualifying sessions. Median sampling cadence 1.144 s per poll.

| category | k | share of decided | |
|---|---|---|---|
| **A — quote never reached our price** | **162** | **73.3%** | Wilson 95% **[0.671, 0.787]** |
| D — venue rejection | 48 | 21.7% | runner-up |
| C — executor ended early | 11 | 5.0% | |
| **B — crossed book, no fill** | **0** | **0.0%** | |
| E — insufficient evidence | 109 | 33.0% of population | under the 40% N3 ceiling |

**Verdict: DOMINANT (A).** The leader's Wilson lower bound 0.6711 clears the
runner-up's point estimate 0.2172 with room to spare. Implied fix per §5:
**complement pricing / aggressiveness.**

### The zero is real, and it was checked before it was believed

B = 0 is the shape of every silent failure in this campaign, so it was tested
three ways before being reported:

1. **Gap distribution.** Across 186 rows with a comparable book, the gap
   `min_opp_ask − px` runs **min 0.5 ticks, p25 1, median 4, p75 11, max 48**.
   The closest the opposite quote ever came was **half a tick**, and it never
   reached us. 63 of 186 came within one tick.
2. **Filter check.** Removing the after-first-fill population filter entirely
   and testing **all 213** completes with a comparable book still gives
   **zero** B. The population filter is not hiding them.
3. **Cross-check against the prior study.** `complete_no_fill_forensics`
   reported `share_at_or_inside_our_price = 0.0168` — 8 of 475 unfilled
   completes. Those 8 lie in the larger 868-order log set that no longer
   exists here. **Our subset genuinely contains none of them; the prior
   figure is not contradicted, it is out of reach.**

So B is absent *in this subset*, not proven absent in history.

### What this licenses, and what it does not

**Licensed:** the dominant failure is that our complement rested where the book
never came. That is a pricing/aggressiveness question, and it is decidable
from what we recorded, so §8.2's instrumentation detour does **not** apply —
that clause governs a B-dominant result, and B did not dominate.

**NOT licensed, per §8.1:** any statement that pricing N ticks better would
have recovered N of the 5.66 missing completions per 100. 63 orders sat within
one tick of the book, and it is tempting to read that as 63 near-misses.
It is not. Moving our quote to that price does not mean filling there — it
changes queue position, and the fill depends on a taker arriving, which we
never recorded. `recoverable_completions()` raises rather than returning that
number.

### The second finding, unranked but material

**D = 48 orders, 21.7%** — better than one in five complements after a first
fill was rejected outright by the venue. That is open task **#93** (the venue
rejecting non-crossing extend/complete orders), and it is now sized: it is the
second-largest execution failure in the record, not a curiosity.

### Coverage

368 complete orders recoverable against the prior study's 868. N1 is met
within this subset. The result should be re-run if the wider log set is ever
recovered.
