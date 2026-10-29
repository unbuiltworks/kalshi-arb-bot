# Segmented habitat census #1 — where the fills actually came from

**Scope.** Item 5 of the owner's priority order: "results segmented by spread,
touch depth, flow/trades per minute, TTE, probability band, resting time,
repricing frequency, EXTEND/COMPLETE state, and adverse movement after fills."

**Status.** EVIDENCE ONLY — model B (venue/economic). Nothing here is
reachable from the L5 policy, the pricing, the sizing or any bound, and no
number below has changed one. No line here is realized P&L.

**Sources.** 261 shadow cycles (soaks #1-#3, `continuous_v1.jsonl`), 9 live
window records (`continuous_run_*.json` — seven that ran, two C8 dispatches
refused before the latch that spent nothing), the venue's own order records,
the venue's own slot-reconciliation rows, and the venue's own public print
tape. Read-only over preserved evidence; no evidence
file was rewritten and no venue call was made to produce this.

Reproduce: `python3 -m src.gemini_execution.segment_report`
Output: `analysis/gemini/segment_census.json`

---

## 0. A correction to the fill record first

I previously reported the live windows as *"C4 129 s / 0 fills, C5 282 s / 1,
C6 207 s / 1, C7 136 s / 0, C8B 278 s / 1"* and drew a correlation between
entry TTE and fill probability from it. **That table was wrong and the
correlation does not exist.** The venue's own order records say:

| window | entry TTE | PLACEs | fills | filled at | resting to fill |
|---|---|---|---|---|---|
| C4  | 129.1 s † | not recorded | **2** | NO 0.14, YES 0.92 | **0.009 s**, 27.45 s |
| C5  | 275.9 s | 2 | **1** ‡ | NO 0.46 | 63.68 s |
| C6  | 199.7 s | 4 | **1** | YES 0.21 | 62.67 s |
| C7  | 125.3 s | 3 | 0 | — | — |
| C8B | 278.3 s | 6 | **1** | YES 0.31 | 57.80 s |

† C4 predates the entry-band scheduler; 129.1 s is the window's start
against the venue's own expiry, and its two fills sit at TTE 120.5 s and
114.1 s by the venue's `expiryDate` minus its `createdAt`.

‡ **C5's fill landed 57 seconds after its window had already stopped**, on an
order the window left resting. Its terminal read at 21:05:33 showed two
orders open and zero fills; the venue recorded the fill at 21:06:29.459Z. It
is confirmed in `reconcile_slot_20260918T214547Z_c5s0.json` and it settled
against us.

**Five fills, not two.** The two earliest entries are 3.8 seconds apart —
C7 at 125.3 s filled **nothing** across 12 cycles, C4 at 129.1 s filled
**twice** — and the two latest, C5 at 275.9 s and C8B at 278.3 s, filled once
each. There is no monotone relationship between entry TTE and fills in this
record, and the tightest pair in it straddles the largest gap in outcome.

The P1 entry band remains what the owner authorised it as — experimental
scheduling — and now has no supporting correlation behind it at all.

**A terminal snapshot is not a fill count.** Four of the nine windows ended
with one of our orders still resting, so their fill counts are a **floor**.
The census marks each such window `fills_are_a_floor: true` and reads
post-window fills from the venue's own reconciliation rows, keyed by the
venue order id the window's own PLACE returned — never by price.

## 1. What this census refuses to say

Nine dimensions. Five fills. A per-segment conversion rate over that ratio
would print as `0/12, 1/9, 0/31` and read as knowledge.

`segments.MIN_FILLS_FOR_A_RATE = 10`. Below it **no dimension states a rate**,
every cell carries the reason, and `rates_stated` is `false` in the artifact.
What is reported instead is the **census** — how many cycles the venue put in
each bucket — beside **where the five fills landed**, as counts.

Two further refusals, both load-bearing:

- **A refused book is not a narrow spread.** 25 of 261 cycles carry a crossed
  or absent quote. They are counted on their own line and contribute to no
  price-derived segment. Bucketing a −0.30 "spread" as `<=0.05` would report
  the venue as the tightest it has ever been using precisely the readings the
  controller threw away.
- **No book capture is not a failed read.** C4, C5 and C6 filled before
  book-state capture existed. Those fills read `NOT_PRESENTED_AT_DECISION`, not
  `UNREADABLE`, and are not dropped — dropping them would make the record look
  as though the only fills we have are the ones we happen to have books for.

---

## 2. Spread — the fill landed in the modal bucket

Over the 151 readable cycles:

| YES spread | cycles | share | fills |
|---|---|---|---|
| `<=0.05` | 1 | 0.4% | 0 |
| `0.06-0.10` | 11 | 4.2% | 0 |
| `0.11-0.20` | 62 | 23.8% | 0 |
| **`0.21-0.30`** | **107** | **41.0%** | **1** (C8B, 0.27) |
| `>0.30` | 55 | 21.1% | 0 |
| refused | 25 | 9.6% | — |
| not captured | — | — | 4 |

The one fill we have a book for landed in the bucket the venue offers most
often. That is consistent with "spread did not matter" and equally consistent
with "we only ever quoted in that regime". It distinguishes nothing.

**The number that does matter here is the denominator:** this venue's touch
spread is 21–30 cents about two cycles in five and above 30 cents in another
one in five. Only **4.6%** of cycles offered a spread of 10 cents or less —
the same share at n=173 and at n=261.

## 3. Touch depth — and an open question about queue priority

| bid touch size | cycles | share | fills |
|---|---|---|---|
| `2-10` | 105 | 40.2% | 0 |
| `11-100` | 3 | 1.1% | 0 |
| `101-500` | 90 | 34.5% | 1 (C8B, **483**) |
| `>500` | 38 | 14.6% | 0 |
| refused | 25 | 9.6% | — |
| not captured | — | — | 4 |

Our own resting size is **flagged, never subtracted** (`touch_was_ours_cycles`).

**The open question this raises, stated as a question.** C8B joined a bid with
**483 contracts already showing at 0.31** and filled 0.01 after 57.8 s — while
the public tape over comparable windows shows almost no prints at all (§4). If
the venue ran strict price-time priority over a queue of 483 resting client
orders, that fill needs ~483 contracts of selling to reach us, and the tape
does not show it. Three readings are open and I cannot yet distinguish them:

1. the 483 is venue/AMM liquidity that we are not queued behind;
2. the ladder read and the actual matching queue differ;
3. the queue did drain and the public tape read misses those prints.

The read that would settle it is cheap, public and read-only: record
`GET /v1/trades/{symbol}` during live windows as the soak already does, and
check for prints at our price between placement and fill. **That reader is
now in the live path** (`book_state.flow`, §10.1), so the question is
answerable by the next live window and is unanswerable from the five fills we
already have. It stays an open question, not a finding.

## 4. Flow — this is the binding constraint

Measured between adjacent reads of the same symbol (never from an assumed
contract duration):

| public prints | adjacent-cycle pairs | share |
|---|---|---|
| **0 / min** | **221** | **88.8%** |
| 0–2 / min | 1 | 0.4% |
| 2–6 / min | 0 | — |
| 6–15 / min | 26 | 10.4% |
| >15 / min | 1 | 0.4% |
| not measurable (first cycle of a window, ×12) | 12 | — |

Non-zero rates when they occur: median 6.8/min, max 19.6/min. The share was
87.9% at n=165 pairs and is 88.8% at n=249 — it did not move.
**Longest unbroken run of zero-print cycles on a single contract: 21
consecutive cycles spanning 198.5 s** (`GEMI-BTC05M2609190300-UP`).

On these contracts, for stretches of two and a half minutes, *nothing trades
at all*. A passive maker cannot be filled by flow that does not exist, and no
amount of patience, repricing or price improvement creates a counterparty.
This is the single most decision-relevant habitat number in the record, and it
is a fact about **model B — the venue** — not about the L5 policy.

No fill is segmented by flow: the live windows do not record the public tape.

## 5. TTE — flat

| TTE at decision | cycles | share | fills |
|---|---|---|---|
| `<=60s` | 5 | 2.9% | 0 |
| `60-120s` | 39 | 22.5% | 1 (C4 YES 0.92) |
| `120-180s` | 50 | 28.9% | 1 (C4 NO 0.14) |
| `180-240s` | 51 | 29.5% | 1 (C6 YES 0.21) |
| `>240s` | 28 | 16.2% | 2 (C5 NO 0.46, C8B YES 0.31) |

At least one fill in each of four adjacent buckets. With the corrected record
in §0, **TTE separates nothing.** C4's TTEs are derived from the venue's own
`expiryDate` minus the venue's own `createdAt`, not reconstructed.

## 6. Probability band

| YES bid | cycles | share | fills |
|---|---|---|---|
| `0.00-0.10` | 52 | 19.9% | 0 |
| `0.11-0.25` | 49 | 18.8% | 0 |
| `0.26-0.50` | 51 | 19.5% | 1 (C8B @ 0.31) |
| `0.51-0.75` | 48 | 18.4% | 0 |
| `0.76-0.90` | 35 | 13.4% | 0 |
| `0.91-1.00` | 1 | 0.4% | 0 |
| refused | 25 | 9.6% | — |
| not captured | — | — | 4 |

**This is the one dimension soak #3 moved, and it moved a claim I had made.**
At n=173 I reported "over half of all cycles (51.4%) present a YES bid at or
below 0.25". At n=261 that is **38.7%**, and the 0.76–0.90 bucket went from
4.0% to 13.4%. The tail-heaviness was a property of the first two soaks, not
of the venue — 173 cycles over two 25-minute stretches was not enough to
characterise a distribution and I should not have described it as if it were.
What survives is weaker and still worth saying: the four middle buckets are
close to flat, so the policy's OPEN rung follows the bid into the tails often
enough to matter, where a 0.01 quote is cheap and also nearly worthless to a
counterparty.

## 7. Resting time and repricing — the one association worth reporting

15 rungs have full PLACE→CANCEL records (C4's mutations predate that
instrumentation, so its two fills are absent from this section).

A rung that filled stops resting at its **fill**, not at the window's stop —
both fills were still nominally live when their window ended, so measuring
them to the stop overstated the only two resting times that have a fill
attached (68.7 s and 66.3 s reported to the stop; 57.8 s and 62.7 s to the
fill).

| resting | rungs | fills |
|---|---|---|
| `1-10s` | 6 | 0 |
| `10-30s` | 3 | 0 |
| `30-60s` | 2 | 1 (C8B, 57.80 s) |
| `>60s` | 4 | 2 (C6 62.67 s, C5 63.68 s) |

| leg repricing | rungs | fills |
|---|---|---|
| on a leg that was repriced | 10 | **0** |
| on a leg placed once and left alone | 5 | **3** |

All three mutation-recorded fills came from a rung that was **placed once,
never repriced, left resting 57.8–63.7 s, at the prevailing bid**. The ten
rungs on a repriced leg filled zero times, and so did all nine rungs that
rested under 30 s. Across C5, C6, C7 and C8B the pattern is identical: one
leg is placed once and left; the other is chased (0.42→0.57→0.62→0.65→0.67 in
C8B alone) and never fills.

**Three reasons this must not become a controller change:**

1. **It is confounded on its face.** The leg that gets repriced is the leg
   whose price is moving. "Repriced rungs do not fill" and "the moving leg
   does not fill" are the same observation in this data.
2. **Neither condition is sufficient.** C7's YES rung was placed once at
   **0.01**, never repriced, and rested **72.1 s** — and did not fill. A penny
   bid left alone is still a penny bid.
3. **n = 5.**

A terminal cancel is counted as a **withdrawal, not a reprice** — counting it
otherwise put C7's rung, the clearest counterexample, on the wrong side of the
table.

## 8. EXTEND / COMPLETE — n = 1, and it never reached the venue

Across every live decision ever taken (31 on record):

| L5 state | decisions |
|---|---|
| `OPEN` | 26 |
| `NO_ACTION` | 4 |
| **`EXTEND+COMPLETE`** | **1** |

Shadow **cannot** contribute here and never will: with nothing resting there
are no fills, inventory stays flat, and `classify()` derives the action from
the residual alone. All 261 shadow cycles are `OPEN` (231) or `NO_ACTION`
(30) — three more soaks would not change that, and nor would three hundred.

The single post-fill decision is **C8B cycle 9** (TTE 202.8 s, residual 0.01):
`EXTEND YES @ 0.12` + `COMPLETE NO @ 0.69`. The window terminated at the
mutation budget before either order was sent, so **the entire EXTEND/COMPLETE
evidence base is one decision, never executed.**

Two things about that decision are worth the owner's eye, as observations:

- `COMPLETE NO @ 0.69` against the filled `YES @ 0.31` is a pair cost of
  **exactly 1.00** — a pair completed at par, which is zero gross before fees.
- `EXTEND YES @ 0.12` is 19 cents below the 0.31 we had just filled at.

Neither is proposed as a change. Per the owner's standing instruction there is
no pair-cost ceiling unless it is later introduced explicitly as a venue risk
overlay separate from L5 fidelity.

## 9. Adverse movement after fills

Mark-to-market on the presented mid. **Not realized, not a P&L line, and never
summed with matched-pair economics.**

| fill | post-fill cycles on record | mid vs fill |
|---|---|---|
| C4 YES 0.92 | 0 | no book — window ended |
| C4 NO 0.14 | 0 | no book — window ended |
| C5 NO 0.46 | 0 | no book — it filled 57 s after the window stopped |
| C6 YES 0.21 | 1 | book refused at that cycle |
| **C8B YES 0.31** | 1 | **−0.095** (mid 0.215, ~9 s later) |

The one measurable observation is adverse: we bought YES at 0.31 and the
presented mid was 0.215 nine seconds later. **One sample.** It is the shape
adverse selection takes and it is not evidence of adverse selection.

C4's pair, for the record: YES 0.92 + NO 0.14 = **1.06**, a matched pair
bought **above par**. That is the outcome the later pair-completion discipline
exists to prevent, and it is the only matched pair this programme has ever
completed.

## 10. Book refusals are not a constant

| soak | window | cycles | refused | share |
|---|---|---|---|---|
| #1 | 02:46–03:07Z | 86 | 2 | **2.3%** |
| #2 | 04:21–04:45Z | 87 | 20 | **23.0%** |
| #3 | 05:50–06:09Z | 88 | 3 | **3.4%** |
| all | | 261 | 25 | 9.6% |

Three soaks, three different rates, all inside four hours. **The refusal rate
is not a property of the venue that can be budgeted for as a constant**, which
matters directly for how many usable cycles a live window actually has.
Sizing a live envelope against any one of 2.3%, 23.0%, 3.4% or the 9.6%
pooled figure would have been wrong on at least two of the three.

Within soak #3 the refusals were not even spread across its own windows:
three of four refused **nothing**, and one refused 3 of 22 (13.6%).

Soaks #1 and #2 were recorded before `continuous_probe.crossed` existed, so
every one of their rows carries `book_readable: true`. This census re-reads
the **preserved, unmodified** book with the current gate and reports the
re-gating explicitly (`regate.rows_regated: 22` of 261). No evidence file was
altered. **Soak #3 ran with the gate live and refused at the source** — its
three refused cycles produced `l5_action []` and zero mutations, which is the
gate working rather than a reconstruction of it.

---

## What this changes, and what it does not

**Does not change:** the L5 controller, prices, sizing, pair-cost behaviour,
the risk envelope, maker-only execution, the coherence gate, terminal cleanup,
or any bound. Nothing in §1–§10 is a tuning input.

**Does change what I can honestly claim:**

- the TTE/fill correlation I reported earlier is withdrawn (§0);
- the binding constraint on this venue is **flow**, not spread, not patience,
  and not price (§4);
- the EXTEND/COMPLETE half of the L5 policy has **one** decision of live
  evidence and **zero** executions behind it (§8);
- usable-cycle count per live window cannot be planned from a fixed refusal
  rate (§10);
- **and the probability-band distribution I reported at n=173 did not hold at
  n=261** (§6). The correction is in that section, and it is why every number
  here now carries its sample size.

**Recording gaps** (all public, read-only, behaviour-neutral):

1. **CLOSED.** `GET /v1/trades/{symbol}` is now recorded beside every live
   decision, exactly as the soak already does it, under
   `book_state.flow`. Without it no live fill could ever be segmented by
   flow and §3's queue question could not be asked. The reader is optional,
   wrapped, and proven byte-equivalent against a run with no observer at all —
   including when it raises — by
   `test_the_flow_read_changes_nothing_about_the_window` and
   `test_a_flow_reader_that_RAISES_still_changes_nothing`. `envelope_sha`
   `b3bff654…aa17` and `executable_sha` `50996ee0…59bd` are unmoved;
   `bundle_sha` moves to `82ad2166…a6fe`, which is what a bundle member
   gaining evidence capture is supposed to do.
2. **OPEN, and proposed rather than built.** Keep sampling the presented book
   for ~60 s after any fill: four of five fills have no post-fill book at all,
   and C5's filled after the window had stopped reading entirely. Unlike (1)
   this changes a window's shape — it extends the run past the mutation
   budget — so it belongs in the live envelope the owner approves, as a
   bounded read-only observation phase that begins only after terminal
   cleanup has proven the book clear. It is described in the envelope
   proposal and is **not** implemented here.
