# COMPLETE FILL CONVERSION FORENSICS

**Read-only.** No production change, no taker, no band/cap/admission/sizing/
concurrency change, `392b5c0e` still deferred, probe still serving `75edc4bd`,
`PMUS_WS_STATE_MAX_MARKETS` still 1500.

---

## 0. The finding

**We cancel 47% of our own COMPLETE orders before they have had time to fill,
and we do it because two correct mechanisms contradict each other.**

The completion price is bounded by pair economics. In 47.4% of sessions the
ceiling binds and puts our price **below our own side's touch** — by design,
and the code says so:

> `L5 fidelity: touch pricing, GATED at completion_pair_ceiling — the re-quote
> follows the touch only inside the ceiling, then stops chasing and preserves
> the residual`

The `touch_moved` re-quote, armed for COMPLETE via
`PMUS_SPRING_COMPLETE_REQUOTE`, then cancels on exactly that condition:

```python
if relation_ in requote_relations() and touch != px_:
    return f"touch_moved:{touch}"
```

When the ceiling has deliberately put `px` below `touch`, `touch != px_` is
true at the **first poll** and every poll after. The order is cancelled, the
next attempt re-derives the same ceiling-bound price, and the loop repeats.
It is deterministic, not probabilistic:

| population (rested orders) | n | fills | **fill rate** | median rest | single-poll |
|---|---:|---:|---:|---:|---:|
| priced **AT** our own touch | 76 | 15 | **19.7%** | 6.44 s | 4 (5%) |
| priced **BELOW** our own touch | 135 | 1 | **0.74%** | 2.01 s | **134 (99.3%)** |

**134 of 135 saw the book exactly once before we cancelled them.** 134 of them
ended `repriced:touch_moved`; the 135th filled. A 27× fill-rate difference
between the two halves.

---

## 1. Corpus and instrument

| | |
|---|---|
| sessions | 132 (pre-cutover live tape, 2026-09-13) |
| COMPLETE attempts | **260** across **8** markets |
| filled | 16 (**6.15%**) — all full fills, **zero partials** |
| median observed fill latency | **3.19 s** (min 0.95, p75 8.67, max 27.6) |
| corroborating corpus | 190 post-cutover attempts, 4 markets, 5 fills (2.6%) |

Every threshold below is measured on this corpus, not assumed. Tick is read
from the venue where declared (7 of 8 markets) and otherwise inferred from the
market's own price grid; grid inference agrees with the declared tick in all 7
markets where both exist.

Per attempt we carry: posted maker price, best bid/ask, spread, ticks from our
own touch, whether our price became touch or crossed, time at price, poll
count, closest complement ask, whether it reached us, required complement
quantity, held quantity, pair economics, fill/no-fill and latency, repeat
structure, and end reason. Executable depth is present only post-cutover.

---

## 2. Ranked failure decomposition

| rank | mode | attempts | share |
|---:|---|---:|---:|
| 1 | **M3 we cancelled before it could fill** | **122** | **46.9%** |
| 2 | M1 complement never reached our price | 52 | 20.0% |
| 3 | R0 rejected before resting | 49 | 18.8% |
| 4 | M6 market moved past us | 9 | 3.5% |
| 5 | M5 repeat attempt, unchanged price and book | 9 | 3.5% |
| 6 | M7 UNKNOWN, insufficient evidence | 3 | 1.2% |
| — | filled | 16 | 6.2% |
| — | **M2 reached our price, no fill** | **0** | **0.0%** |
| — | **M4 size exceeded available flow** | **0** | **0.0%** |

Co-occurring signals (a row carries all that apply):

| signal | rows |
|---|---:|
| priced behind our own touch at post | **134** |
| repeat attempt at the same price and book | 89 |
| cancelled early (secondary to another primary) | 3 |

Precedence is `M4 → M2 → M6 → M3 → M1 → M5 → M7`. Two rules matter:

- **M3 outranks M1.** "The complement never reached our price" cannot be
  concluded from a window we closed ourselves after two seconds. Claiming it
  would turn our own cancel into a fact about the market.
- **M6 requires the touch to have risen *during* the rest.** An order posted
  below its own touch starts behind the market by construction; `max_touch >
  px` on such a row is the state we created, not a move the market made.
  Labelling it "the market moved past us" would launder a pricing decision
  into a market event.

### M2 and M4 are zero, and that is the answer to the queue question

**In all 205 windows carrying a rest book, the complement ask reached our
price exactly 0 times.** So there is no observation in this corpus of the book
coming to us and passing us over.

**Queue position is therefore UNKNOWN.** Not "fine", not "not a problem" —
unknown. We cannot lose a queue race we never entered. And the reason we never
entered one is mode M3: 67% of the rest books are a single poll, because we
cancelled before a second observation existed.

M4 is zero for a stated reason: **far-side resting depth bounds a *taker*
execution and says nothing about a maker order being hit.** A maker fill needs
arriving flow, which depth does not measure. The only evidence that would
support M4 is a partial fill, and there are **zero partial fills in 260
attempts** — every fill took the whole clip.

### Per market

| market | M3 | M1 | R0 | M6 | M5 | M7 | attempts |
|---|---:|---:|---:|---:|---:|---:|---:|
| `aec-mlb-lad-mia` | **96** | 9 | 6 | — | 8 | 2 | 125 |
| `aec-cs2-mzp-ezt` | 10 | 13 | 1 | — | — | — | 26 |
| `atc-epl-cov-bha` | 4 | 14 | 7 | 3 | — | — | 32 |
| `aec-nfl-buf-hou` | 1 | 9 | 20 | — | — | 1 | 31 |
| `asc-lal-lev-fcb` | 7 | 2 | 3 | 3 | 1 | — | 19 |
| `aec-atp-jayfri-maxbas` | 1 | 2 | 6 | 1 | — | — | 10 |
| `aec-setkameua-sapole-salvas` | 3 | 2 | — | 2 | — | — | 9 |
| `aec-mlb-sea-ath` | — | 1 | 6 | — | — | — | 8 |

`lad-mia` alone contributes 96 of the 122 M3 rows.

### The rejections are their own problem

49 of 260 (18.8%) never rested at all. 40 are
`post_only_cross_within_1_tick_of_lagging_ask` — posting **at** the touch
against a lagging book breaches post-only. That is the price of the half that
actually fills: `px == touch` earns 19.7% conversion **and** all 49
rejections. A separate defect, not addressed here: 9 are classed
`implementation bug candidate: rejected_at_non_crossing_price`.

---

## 3. Repeat structure (mode 5, measured as a sequence)

Across 252 consecutive COMPLETE attempts in the same market:

- **189 (75.0%)** re-post at the **identical price** as the previous attempt
- **147** face an **identical book** (`yes_bid`, `yes_ask` unchanged)

`lad-mia` price runs, in order: **23 attempts at 0.575**, then 0.225, 0.005,
0.52×2, **23 more at 0.575**, … four separate runs of 22–23 attempts at one
price. `cribuc-biaand` in the post-cutover cohort: **104 attempts, 0 fills**,
almost every one ending `repriced:touch_moved`.

Cancelling and re-posting the same price against the same book is not
repricing. It is churn that destroys whatever time-priority the order had.

---

## 4. L5's process, from public fills only

**What public activity establishes:** time, price, size, outcome, and the
venue's own maker/taker label (maker 55,647 / taker 11,068 / unknown 5,269 —
**77.3% maker**). 71,984 fills, 2,216 windows, 21,739 side switches.

**What it does NOT establish, and is reported as UNKNOWN, not modelled:**
queue position, cancels, reprices, resting quotes, orders that never filled,
and the book he quoted against. No order-book or maker/taker data is invented
anywhere below.

| L5, public fills | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|
| seconds to the opposite-side fill (**maker only**) | 1 | **3** | **7** | 15 |
| seconds to the opposite-side fill (all) | 1 | 4 | 12 | 29 |
| same-side run length (fills) | 1 | 2 | 3 | 6 |
| consecutive fills at one price+side | 1 | **1** | 1 | 2 |
| switch clip (contracts) | 6.0 | **17.0** | 48.0 | 50.0 |
| switch price | 0.23 | 0.49 | 0.75 | 0.89 |

Same-side price step when it changes: p50 **1 cent**, p75 3 cents.
Alternating A-B-A fill triples: 11.7% — he is not ping-ponging.

### Our COMPLETE process against the minimum process consistent with those fills

| | L5 (public fills) | our clone | verdict |
|---|---|---|---|
| **cadence** — time before the opposite side fills | maker p50 **3 s**, p75 **7 s** | we cancel at p50 **2.01 s**; 87.4% of our requote-cancels occur inside our own 3.19 s median fill latency | **we cancel inside the window his own fills occupy** |
| **persistence at a level** | p50 1 fill per price, p90 2 — he does not camp | we re-post the **same price 75%** of the time, in runs up to 23 | we camp on a price we cancel and re-post, which is the worst of both |
| **price stepping** | p50 **1 cent** when it moves | ceiling-capped orders re-derive the **same** price; median gap to our own touch is **7 cents**, p75 **29.5 cents** | we do not step; the ceiling pins us far from the touch |
| **clip size** | switch clip p50 **17.0** contracts | COMPLETE clip p50 **1.39** contracts (p75 1.80, max 2.45) | ~12× smaller; consistent with the learning-stage bound, not a defect |
| **repeated alternating fills** | 11.7% of triples alternate | 0 observed alternating COMPLETE fills | not reached |
| **queue behaviour** | **UNKNOWN** | **UNKNOWN** | no comparison possible, and none is asserted |

The minimum process consistent with L5's public fills is: **rest at a price
for something like 3–7 seconds, step ~1 cent when you move, and take the fill
when it comes.** Our median time at price on the ceiling-capped half is 2.01
seconds with a single book observation. On cadence alone we are outside the
process his fills imply, and we put ourselves there.

---

## 5. Economic sensitivity — and it does not say what the question assumed

Method, stated so it cannot be misread: this is arithmetic on observed inputs.
**Not realized P&L, not an assumed fill, not a forecast.**

Two inputs make it honest:

1. **The marginal pair cost, not the average.** Our fills land at pair p50
   **0.9490**; our no-fills sit at pair p50 **0.9952**. The contracts a higher
   fill rate would add are the *expensive* ones.
2. **A hard ceiling: a COMPLETE can only match inventory we already hold.**
   Total terminal residual across the corpus is **32.77 contracts**. Without
   that cap the arithmetic "completes" hundreds of contracts we never owned.

| COMPLETE fill rate | contracts those fills want | can actually match | matched margin | + settled residual (−$1.3408) | positive? |
|---:|---:|---:|---:|---:|:--|
| 6.15% (observed) | — | — | — | −$1.3408 | no |
| 10% | 14.30 | 14.30 | $0.0682 | −$1.2726 | no |
| 20% | 51.47 | **32.77** (saturated) | **$0.1563** | −$1.1845 | no |
| 30% | 88.65 | 32.77 | $0.1563 | −$1.1845 | no |
| 50% | 163.00 | 32.77 | $0.1563 | −$1.1845 | no |
| 75% | 255.93 | 32.77 | $0.1563 | −$1.1845 | no |
| **100%** | 348.87 | 32.77 | **$0.1563** | −$1.1845 | **no** |

**The answer to "how much fill-rate improvement turns the strategy positive"
is: none does.** The benefit saturates at a ~20% fill rate — because at that
point there is no residual left to complete — and the maximum obtainable
matched margin is **$0.1563**, against a settled residual loss of $1.3408 and
an account settlement line of −$12.5082.

### The variable that does bind

If the entire 32.77-contract residual were completed, the pair cost required
to clear each loss is:

| to cover | loss | required pair cost | we quote |
|---|---:|---:|---:|
| the settled residual loss | $1.3408 | **≤ 0.9591** | **0.9952** |
| the account settlement line | $12.5082 | **≤ 0.6183** | **0.9952** |

**The binding variable is the pair cost we are willing to complete at, not the
COMPLETE fill rate.** And the separation is already visible in the tape:

| sessions with a COMPLETE order | filled | no fill |
|---|---:|---:|
| n | 13 | 65 |
| pair at chosen price, p50 | **0.9490** | **0.9952** |
| **capped by the pair ceiling** | **0 (0%)** | **37 (56.9%)** |
| fee-safe | 12 (92.3%) | 23 (35.4%) |

**Zero of 13 filled sessions were capped by the pair ceiling; 37 of 65 no-fill
sessions were.** The same variable that pins our price below the touch — and
so triggers the self-cancel loop — is the one that makes the fill worth
almost nothing when it happens. Conversion and economics fail together, for
one reason.

Settled-market decomposition (identity `matched margin + residual P&L =
venue realized` checks to $0.0000 in all three):

| market | matched | pair | matched margin | residual | residual P&L | venue realized |
|---|---:|---:|---:|---:|---:|---:|
| `lad-mia` | 6.08 | 0.4134 | $3.5668 | 4.05 | +$2.4366 | **+$6.0034** |
| `sapole-salvas` | 2.79 | 0.9606 | $0.1099 | 3.49 | −$1.1878 | **−$1.0779** |
| `buf-hou` | 0 | 0.3918 | $0.0000 | 6.61 | −$2.5896 | **−$2.5896** |

`lad-mia`'s $3.57 came from a pair of **0.4134** — a deep-tail acquisition,
not a completion at the ceiling. It is the exception that shows the rule.

---

## 6. Ranked conclusions

1. **M3 — we cancel our own COMPLETEs before they can fill — 46.9%**, and on
   the ceiling-capped half it is deterministic: 134 of 135 cancelled after one
   book poll at a median 2.01 s.
2. **M1 — the complement never reaches our price — 20.0%**, concentrated where
   we did wait (median rest 6.2–31 s, ≥2 polls).
3. **R0 — rejected before resting — 18.8%**, 40 of them post-only crosses
   against a lagging book; the cost of the half that does fill.
4. **M6 — the market genuinely moved past us — 3.5%.** Small, once the rows we
   put behind the touch ourselves are removed from it.
5. **M5 — churn at an unchanged price against an unchanged book — 3.5%
   primary, 89 co-occurring, 75% of consecutive attempts.**
6. **M2 (queue) and M4 (size) — 0%, and both are UNKNOWN, not absent.** The
   complement never once reached our price in 205 windows, so no queue race
   was ever observed. There are zero partial fills, so no size pressure was
   ever observed.

---

## 7. The single highest-evidence execution change to shadow-test next

> **Suppress the `touch_moved` cancel for a COMPLETE whose price is pinned by
> the pair ceiling** — i.e. when `capped_by_pair_ceiling` is true and the
> re-quote provably cannot produce a different price.

Why this one:

- **The evidence is deterministic, not statistical.** 134 of 135 rows, one
  poll each. The mechanism is visible in three lines of code and the outcome
  is visible in the end-reason histogram of two independent corpora.
- **It changes nothing that carries risk.** Not a price, not a cap, not the
  band, not size, not concurrency, not maker-only. It removes a cancel whose
  re-quote is a no-op by construction.
- **The comparison is already anchored:** L5's own maker opposite-side fills
  arrive at p50 3 s and p75 7 s. Our cancel lands at 2.01 s.
- **It is falsifiable cheaply.** Primary outcome: book polls per rest and
  whether the complement ask reaches `px` within a 7-second dwell. Secondary:
  COMPLETE fill rate on the capped half.

**And the honest limit on it, stated before anyone runs it.** Whether those
orders *would* have filled is **not answerable from the tape we hold** — we
cancelled them, so the window does not exist. Testing it needs either a replay
against the recorder's own retained book for those markets and timestamps
(read-only, but needs a reader for the recorder volume that we do not have),
or a live arm. It is a **fidelity and conversion** test, not a profit lever:
§5 bounds the entire prize at **$0.1563**.

**The profit lever is elsewhere, and this study located it.** The pair cost at
which we complete is 0.9952 on the attempts that do not fill, and it needs to
be ≤0.9591 merely to cover the settled residual loss. Since
`pair = last_pair − gain_per_unit`, that is a statement about the *position
state we enter*, not about execution mechanism — the same conclusion the
cheap-complement identity and the underwater study reached from two other
directions. The next economic question is not "how do we fill more
COMPLETEs", it is **"how do we acquire first legs that make a ≤0.96 pair
reachable"**.

No change is proposed for deployment here.

---

## 8. Accounting labels

Everything above is **historical replay / forensics**. The sensitivity table is
arithmetic on observed inputs and is never realized P&L. Venue-confirmed
realized appears only in §5's settled-market table and the account bridge
(settlement −$12.5082, confirmed maker rebates +$2.1300, net −$10.3782);
accrued-not-credited rewards ($1.48) and the $10 non-trading remediation
credit are excluded. Unsettled markets are reported as UNSETTLED, never zero.

Reproduce: `python3 analysis/l5/complete_conversion/run.py`
