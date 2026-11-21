# CP1 probe — forensic reconstruction of all 13 orders

**Read-only.** No execution parameter, model, gate or mandate was changed to produce this.
`l5-r1-probe` remained HALTED throughout (`d3845a53` / `7f3eb490a50c`, `SHUTDOWN_VERIFIED`).

**Sources.** Raw Railway deploy logs for deployment `51114cbc` (commit `7944bfa6`,
market 1) and `9bd90901` (commit `84974f38`, markets 2–3); the `session_end`
`orders[]` and `decisions[]` arrays, which carry per-order `end_reason`, `rest_s`,
`rejection_class`, `authenticated_trim` and `recorder_book`; and the venue's own
`PMUS_ORDER_REJECTED` field dumps. Historical comparison from
`analysis/l5/cross_shadow_live/cohort.jsonl` (1,268 observations, 2026-09-14 → 2026-09-16).

---

## 1. The headline correction

The probe was previously reported — by me, in this session's records and watch
texts — as **"13 post-only maker orders, all no_fill_cancelled."** That is wrong
in a way that matters.

**Five of the thirteen never reached the book at all.** They were `ORDER_STATE_REJECTED`
by the venue at insertion. The `outcome` field does read `no_fill_cancelled`, which
is what I reported, but the `end_reason` on those same five records reads
`rejected:ORDER_REJECTED_ZERO_FILL:…:ORDER_STATE_REJECTED`, and the venue's own
field dump shows `leavesQuantity=0`, `cumQuantity=0`, and
`insertTime == lastTransactTime` to within 2 ms.

So the probe did not produce "13 orders that rested and did not fill." It produced
**8 orders that rested and did not fill, and 5 that were refused on submission.**
Any fill-rate reasoning over all 13 is reasoning over a contaminated denominator.

`rest_s` on the rejected five (0.877–1.948 s) is the harness's submit→terminal
round trip, not resting time. True book residency was ~0.

---

## 2. Per-order reconstruction

Prices are the venue's. `bid/ask` is the `open_side` book the decision priced from
(the gateway view). `trim` is whether the authenticated cross-check was applied.

| # | Market | Stage | Side | Px | Qty | Book at submit (bid/ask) | Dist. from touch | Trim | Rest (s) | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | dota2-yes-tm | open_1 | YES | 0.45 | 0.22 | 0.4500 / 0.4900 | 0 (at gw touch) | **not applied** `stale:11.2` | ~0 (rej) | REJECTED |
| 2 | dota2-yes-tm | open_2 | YES | 0.45 | 0.22 | 0.4500 / 0.4900 | 0 | not applied `stale:11.2` | ~0 (rej) | REJECTED |
| 3 | dota2-yes-tm | open_3 | YES | 0.45 | 0.22 | 0.4500 / 0.4900 | 0 | not applied `stale:11.2` | ~0 (rej) | REJECTED |
| 4 | dota2-yes-tm | open_4 | YES | 0.45 | 0.22 | 0.4500 / 0.4900 | 0 | not applied `stale:11.2` | ~0 (rej) | REJECTED |
| 5 | dota2-yes-tm | open_5 | YES | 0.45 | 0.22 | 0.4500 / 0.4900 | 0 | not applied `stale:11.2` | ~0 (rej) | REJECTED |
| 6 | dota2-yes-tm | open_6 | YES | **0.44** | 0.22 | 0.4500 / 0.4900 | 1 tick behind | **applied** (age 0.2 s) | 1.911 | rested → `repriced:touch_moved:0.45` |
| 7 | lib-lqu-pal | open_1 | YES | 0.27 | 0.17 | 0.2700 / 0.2800 | 0 (joined bid) | n/a `already_non_crossing` | 30.948 | rested → `deadline` |
| 8 | lib-lqu-pal | open_2 | YES | 0.27 | 0.17 | 0.2700 / 0.2800 | 0 | **`unreadable:slug_not_in_status_document`** | 32.310 | rested → `deadline` |
| 9 | lib-lqu-pal | open_3 | YES | 0.27 | 0.17 | 0.2700 / 0.2800 | 0 | unreadable (as above) | 31.985 | rested → `deadline` |
| 10 | lib-lqu-pal | open_4 | YES | 0.27 | 0.17 | 0.2700 / 0.2800 | 0 | unreadable | 31.372 | rested → `deadline` |
| 11 | lib-lqu-pal | open_5 | YES | 0.27 | 0.17 | 0.2700 / 0.2800 | 0 | unreadable | 31.301 | rested → `deadline` |
| 12 | lib-lqu-pal | open_6 | YES | 0.27 | 0.17 | 0.2700 / 0.2800 | 0 | unreadable | 31.489 | rested → `deadline` |
| 13 | county-nor-der | open_1 | YES | 0.37 | 0.19 | 0.3700 / 0.4000 | 0 (joined bid) | n/a `already_non_crossing` | 31.152 | rested → `deadline` |

Every `filled_qty` is `0`. Total notional at risk had all filled: ~$0.94.

### Fields that were NOT captured — for any of the 13

These are absent from the instrumentation, not merely absent from this analysis.
I am not estimating them.

* **Trades or volume at our price while resting** — no `volume`, `last_trade`,
  `tradedVolume` or equivalent field exists in any probe record. Zero occurrences.
* **Queue position.** Aggregate depth at a price was sometimes captured; our rank
  within it never was. Arrival order is unknown, so depth is an *upper bound* on
  what sat ahead of us, not a queue position.
* **Whether the market traded through our price** — not directly observable.
  Inferable only from top-of-book movement, and only where that was captured.

---

## 3. What happened on each market

### Market 1 — `aec-dota2-yes-tm-2026-09-16`: a diagnosed placement defect

The decision priced from a **gateway** book reading `yes_bid 0.45 / yes_ask 0.49`.
The venue's **authenticated** book had the ask at **0.45**. A post-only BUY YES at
0.45 therefore crossed, and the venue refused it. Five times.

The system detected and labelled this itself, on every one of the five records:

```
rejection_class : "implementation bug candidate:rejected_at_non_crossing_price;
                   authenticated_book_crossed"
disagrees_with_gateway : true
fresh_opp_ask   : 0.4900        rejection_margin_ticks : 4.00
```

The 4-tick "margin" is measured against the wrong book — the gateway's 0.49. Against
the real ask of 0.45 the margin was **zero**.

**The causal mechanism is the staleness gate, and it inverted the source ranking.**
`authenticated_trim` on orders 1–5 reads `applied: false, why: "stale:11.2"`. The
accurate source (status_url recorder, which read `0.4300 / 0.4500` and agreed with
the venue) was **11.2 s old and was discarded for being stale**, falling back to a
fresher but wrong gateway book.

Order 6 is the control, and it is decisive. At 21:12:54 a fresh authenticated read
arrived (`age 0.2 s`); the trim applied; `px_in 0.45 → px_out 0.44`; the order was
**accepted and rested**. Same market, same size, same second — the only variable that
changed was book freshness, and the outcome flipped from rejection to a live resting
order.

### Market 2 — `atc-lib-lqu-pal-2026-09-16-pal`: observationally dark

Six orders at 0.27, joining a `0.2700 / 0.2800` bid — correct one-tick maker
placement, all accepted, all rested ~31 s, all ended `deadline`.

But from decision 2 onward the authenticated cross-check returned
**`unreadable:slug_not_in_status_document`**. The reported book is byte-identical
(`0.2700 / 0.2800`) across all six decisions spanning 2 m 42 s. That is either a
genuinely motionless book or a carried-forward value, and **the captured evidence
cannot distinguish the two.** No depth at our price was recorded; no
`cross_shadow_observation` is attached to any of these six order records.

This market contributes six orders and **no usable fill evidence.**

### Market 3 — `aec-county-nor-der-2026-09-15`: the market left

One order at 0.37, joining a `0.3700 / 0.4000` bid, rested 31.2 s, `deadline`.

At the terminal report the book read **bid 0.3700 / ask 0.8400**, `bid_qty 111.02`,
`ask_qty 110.00`, depth `OBSERVED`, authenticated age 3.6 s. The next decision
(22:18:26) read `yes_ask 0.8800` and the controller **held**, classifying the state
`no_fee_safe_complement`.

So during our rest the **ask moved 0.40 → 0.84–0.88** while the bid stayed at 0.37.
The spread widened from 3 ticks to ~47–51. We were never traded through; the market
moved away from us. A fill would have required a seller to cross a widening
47-cent spread into a resting queue of **at least 111 contracts** (our own order was
0.19). No fill was reasonably possible.

---

## 4. Aggregate classification

| Class | Orders | n |
|---|---|---|
| **QUOTE-PLACEMENT FAILURE** | 1–5 | **5** |
| **NO OPPORTUNITY TO FILL** | 6, 13 | **2** |
| **UNRESOLVED** | 7–12 | **6** |
| QUEUE-PRIORITY FAILURE | — | 0 |
| REST-TIME FAILURE | — | 0 |
| MARKET-SELECTION FAILURE | — | 0 |

**QUEUE-PRIORITY is returned as 0 confirmed, not as "none occurred."** Market 13 sat
behind ≥111 contracts, which is consistent with queue priority mattering — but the
simultaneous 47-cent spread blow-out is a sufficient explanation on its own, and we
have no queue position for any order. Asserting queue-priority failure here would be
inventing the information the instrumentation did not capture.

**REST-TIME is 0 confirmed.** Order 6 died at 1.9 s, which is rest-limited, but it
was also a tick behind the touch, so rest time is not separably the binding cause.
Orders 7–13 ran the full ~31 s policy; nothing in the evidence says 31 s was the
constraint rather than the absence of counterparty flow.

**MARKET-SELECTION is 0 confirmed.** Market 3's spread blow-out is suggestive, but
n = 1, and market 2 — the only multi-order rested sample — is UNRESOLVED.

### The three markets do not agree, and must not be pooled

| Market | Orders | Placement | Rested? | Diagnosis |
|---|---|---|---|---|
| 1 — dota2 | 6 | at gw touch (wrong book) | 5 never; 1 for 1.9 s | placement defect, fully diagnosed |
| 2 — lib-lqu-pal | 6 | at touch, 1-tick spread | all ~31 s | UNRESOLVED, observationally dark |
| 3 — county-nor-der | 1 | at touch, 3-tick spread | 31 s | no opportunity; market left |

A pooled "0/13 fill rate" conceals three unrelated stories. Market 1 is an own-goal.
Market 2 is unmeasured. Market 3 is a market that walked away. **No single execution
dimension explains all three,** which is the central result of this reconstruction.

---

## 5. Comparison with existing PMUS sports execution evidence

From `cohort.jsonl`, n = 1,268 historical observations:

| Outcome | n | share |
|---|---|---|
| `repriced:touch_moved` | 967 | **76.3 %** |
| `deadline` (rested, no fill) | 219 | 17.3 % |
| `filled` / `filled_on_cancel` | 73 | **5.8 %** |
| other | 9 | 0.7 % |

Two things follow.

**A 0-fill outcome over 8 rested orders is unremarkable.** At a 5.8 % historical
per-order fill rate, the probability of zero fills in 8 independent rested orders is
about 62 %. **The probe's zero-fill result is entirely consistent with the
historical base rate and is not, on its own, evidence of any new defect.**

**But the probe's outcome mix is anomalous.** History is 76 % reprice / 17 %
deadline; markets 2–3 were 0 % reprice / 100 % deadline (7/7). Historically an order
is four times more likely to be cancelled because the touch moved than to sit out its
deadline. Seven consecutive deadlines with no observed touch movement is a real
departure — and it coincides exactly with market 2's book being unreadable. That
points at the observation path, not at the resting policy.

**The book-availability failure is chronic and pre-existing, not new:**

| Condition | Historical incidence |
|---|---|
| `first_stop == no_authenticated_book` | 299 / 1268 = **23.6 %** |
| `executable_depth_status == STALE` | 692 / 1268 = **54.6 %** |
| `executable_depth_status == OBSERVED` | 277 / 1268 = 21.8 % |
| authenticated age > 5 s | 17.6 % |
| authenticated age > 10 s | **12.7 %** (median 0.3 s, p90 10.9 s, max 734 s) |

Market 1 failed at `age 11.2 s` — squarely in that 12.7 % tail. Market 2 failed with
the slug absent from the status document — the same family as the 23.6 %
`no_authenticated_book` condition. **Both probe failures are instances of a
long-standing, quantified observation-path weakness**, not novel behaviour.

### A depth signal I checked and am NOT relying on

A first cut suggested filled orders sit at far thinner touches (median depth 179)
than unfilled ones (5,150). That comparison was wrong: it read `obs_bid_qty`
regardless of whether we were actually resting at the bid. Restricted to rows where
depth is genuinely at our own price (`maker_px == obs_bid` or `== obs_ask`), the
sample collapses to **n = 67** (17 filled), the distributions overlap heavily
(filled p75 = 1,509 vs unfilled p25 = 166), and fill rate by depth bucket is
non-monotonic on 3–23 observations per bucket. **Underpowered. Not a basis for a
recommendation.** Recorded so it is not rediscovered and trusted later.

---

## 6. Recommendation

The evidence splits cleanly, and so does the answer.

### For the 5 rejected orders — cause identified

**Recommendation: gate order submission on authenticated-book freshness. Change
exactly one execution dimension — the precondition for submitting — and nothing else.**

When the authenticated book is stale or unreadable, the current behaviour falls back
to the gateway book and submits anyway. On market 1 that produced five orders priced
through the real ask. The correct behaviour is to **skip the action** (a WAIT, which
the model already supports) rather than submit a price derived from a book known to
be unreliable.

This is not a tuning knob. It is the condition under which order 6 already succeeded
while orders 1–5 failed, in the same market, seconds apart.

**Falsifiable pre-registered predictions** for the next bounded probe:

1. `rejection_class: rejected_at_non_crossing_price` occurrences → **0**.
   *Falsified if any rejection at a non-crossing price occurs with the gate active.*
2. Submitted-order count **falls** relative to actions attempted, with the gap
   accounted for by logged skips citing book staleness.
   *Falsified if submissions do not fall while rejections vanish — that would mean
   the gate never bound and the fix was inert.*
3. Of orders that are submitted, the share reaching the book (accepted, `rest_s` > 1 s)
   → **100 %**. *Falsified by any accepted-then-instantly-rejected order.*

Expected cost: with `age > 10 s` at 12.7 % and `no_authenticated_book` at 23.6 %
historically, roughly a **quarter to a third of actions would be skipped**. That is
the intended trade — fewer submissions, none of them refused.

### For the 8 rested orders — cause NOT identified

**INSUFFICIENT_EXECUTION_EVIDENCE.**

Six of the eight (market 2) have no live book, no depth at our price, and no trade
data. One (market 13) is explained by the market leaving. One (order 6) rested 1.9 s.
There is no queue position and no trade-at-our-price record for any of them, and the
zero-fill result is within the historical base rate.

**Do not change rest time, size, price aggressiveness, pair ceiling, ACT/WAIT, k, or
market selection on this evidence.** Any such change would be tuning against a cause
that has not been established. Specifically: the 30 s rest policy is **not**
implicated — 7/7 orders ran it to completion, and history says the binding constraint
is usually touch movement, which we did not observe here because we largely could not
see the book.

The fill question is not yet askable. 38 % of this sample never reached the book, and
75 % of the rested sample was observationally dark. Fixing placement is a
**prerequisite** for measuring fills, not an alternative to it.

---

## 7. Accounting

Unchanged and unaffected. Zero fills means nothing to settle and nothing to realize.
`pmus-legacy` net **−$44.4898** across 56 markets / 34 venue-confirmed. R1 cohort
venue-confirmed realized **−$0.0916**. The 13 orders contribute **$0.00** to every net.
