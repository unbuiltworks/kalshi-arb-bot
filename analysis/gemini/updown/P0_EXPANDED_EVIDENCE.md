# P0 — EXPANDED EVIDENCE + VENUE STRUCTURE

Descriptive only. No policy fitted, no threshold swept, no candidate
proposed, no live orders. The six sealed OOS windows are excluded **by
name** from every measure below, not merely from outcome reads.

Artifacts: `scripts/gemini_btc5m_structural.py`,
`analysis/gemini/updown/STRUCTURAL_EXPANDED.json`.

## 1–2. Job state — one job survived by 10 seconds, two did not

**The long recorder (37325663925) was CANCELLED at 20:34:33Z**, short of
its ~23:42Z horizon. It committed its tape at **20:34:23Z — ten seconds
earlier** — so 5h52m of collection (5.86 MB, commit `9992c06f`) survived.
That was luck, not design: the recorder has no periodic flush, so a
cancellation a minute earlier would have destroyed the entire run.

**Both lead/lag runs were cancelled after exactly 15 minutes** (37368059222
at 20:25:17Z, 37368695533 at 20:40:20Z). Nothing self-imposed ended them:
the workflow allows 90 minutes, the step 70, the collection horizon 3600 s.
Job logs return 404 for cancelled jobs, so the cause is not recoverable
from them. Concurrency is `cancel-in-progress: false` on all three
workflows, so my own pushes did not cause it. A snapshot job ran 89
minutes to success at 20:49Z, so this is not a blanket account block —
**I cannot identify the cause from available evidence and am not going to
invent one.** The job has been re-dispatched byte-identical (`eb6af232`);
only the request-file timestamp changed.

**Phase A lead/lag is therefore NOT reported.** Every quantity requirement
6 asks for — external→book, external→LRRR, PMUS→book, latency p10/50/90,
residual price/depth at 200 ms…5 s — requires layer-1 external collection
that has never completed a single run. The container itself cannot
establish admissibility: the egress proxy answers 403 for all four
external venues, which is why collection must run in CI.

## 3–4. Census of all newly committed data

Corrected authoritative `window_poll` strike logic, all five requirements
per window:

| Tape | Complete windows | Chaining check |
|---|---|---|
| long recorder (14:42–20:34Z) | **72** | 73/74 agree to the cent |
| snapshot new (19:26–20:49Z) | 15 | 16/16 |
| snapshot old (first-leg study) | 16 | 17/17 |
| **union (deduplicated)** | **75** | |

| Classification | Windows |
|---|---|
| union of genuinely complete | **75** |
| used by the first-leg study | 16 (10 discovery + **6 sealed**) |
| **new since that study** | **59** |
| probe-contaminated (live orders) | 10 |
| **new, clean, not sealed** | **53** |
| **usable for descriptives here** | **61** |

The one chaining disagreement (BTC05M2610051435: strike 86281.16 vs prior
settlement 86626.78) is a 345-point gap, not a rounding artifact —
consistent with a missing intermediate window in settlement coverage
rather than a broken mechanism, though I cannot fully exclude a genuine
break from this tape alone. The agreeing 73 match to the cent
(86311.74 vs 86311.739).

## 5. Structural descriptives — 61 independent windows, 24,520 book observations

**Spread.** Note this proxy is algebraically exact: since synthesized
DOWN ask = 1 − UP bid, UP+DOWN−1 **is** the UP instrument's bid-ask.

| | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| spread | 0.01 | 0.01 | **0.24** | 0.96 | 0.97 |
| simultaneous pair cost | **1.01** | 1.01 | 1.24 | 1.96 | 1.97 |

**The simultaneous pair never once costs less than $1.00 in 24,520
observations.** The earlier 0/17 was not a small-sample accident.

**Depth.** Both sides executable: q1 **100%**, q5 68.2%, q10 68.0%,
q25 61.7%. Pair cost barely degrades with size (median 1.24 → 1.28 at
q25), so when depth exists it is not the expensive part.

**Time to first sub-$1 sequential complement**, fixed pre-existing
first-leg definitions, measured reaction latency 0.197 s:

| First leg | Reach | t to first (p10 / median / p90) | Open seconds | Episodes | Best lock |
|---|---|---|---|---|---|
| UNDERDOG (median $0.55) | **45.9%** (28/61) | 75 / **117.0** / 216 s | 100 s | 1 | $0.74 |
| FAVORED (median $0.75) | **50.8%** (31/61) | 40 / **113.0** / 221 s | 90 s | 2 | $0.91 |

**Reach is identical at q1, q5, q10 and q25** for both definitions —
depth is confirmed not binding on 3.6× the evidence.

**Maker/trade activity.** 487 trade prints across 80 windows — **median 4
prints per 5-minute window**, max 48; 46% `buyer_is_maker`. The market is
nearly inert.

## Decision gate

**Limb 1 — causal information lead surviving latency: NOT ESTABLISHED.**
Phase A has never run. This is unresolved, not negative.

**Limb 2 — expanded evidence contradicting the structural finding: NO.
It confirms it.** On 61 windows against 17: reach 45.9% vs 47.1%,
simultaneous sub-$1 still never observed, depth still not binding, and
complement arrival still far outside any reaction budget (median 117 s,
p10 75 s, inside a 300 s window). The median *improved* from 174.6 s to
117 s, which is a real difference and still two orders of magnitude
beyond our 0.197 s latency.

**Therefore: Gemini BTC5m is presently classified UNSUITABLE for L5-style
deployment** — pending Phase A, which is the only open route.

## Which venue/category is structurally closest to L5's mechanics

L5 trades Polymarket, where 312 events carry 9,901 of its own fills
(median 6 per 15-minute event, p90 136). The structural comparison:

| Venue / category | median spread | touch size | window | verdict |
|---|---|---|---|---|
| **PMUS sports moneyline** | **$0.0050** | **~167,000** | hours–days | closest |
| Gemini BTC15M (historical) | $0.0200 | 500 | 15 min | intermediate |
| Gemini BTC5M (now) | **$0.2400** | q1 only 100%, q25 61.7% | 5 min | furthest |

**PMUS sports is structurally closest to L5's observed mechanics** — a
48× tighter spread than Gemini BTC5m with ~330× the touch size, and
hours-to-days of window length against 300 seconds. L5's actual behaviour
(OPEN → EXTEND → patient COMPLETE, median 52 s to first matched increase,
1.4 same-side extends first) **requires** a book where incremental
inventory can be worked and a complement is continuously near fair value.
That is what a tight, deep, long-dated book provides and what a 24¢-wide
5-minute book structurally cannot.

Two honest limits on that recommendation: the PMUS sample is **232
observations** and every single one reads exactly 0.0050, which may be a
tick floor rather than a measured distribution; and Gemini BTC15M's 2¢
spread is **historical** (Aug 2026), not current. Both need their own
census before anything is built on them. I am naming the structurally
closest candidate as asked, not proposing a strategy for it.

---

## ADDENDUM — Phase A lead/lag: the probe ran, and REJECTED all four feeds

The re-dispatched job (`868caa11`, probe at 21:41:31Z) completed its
admissibility stage and committed a verdict. **0 of 4 venues admissible**,
so the preregistered gate stopped the run before collection — fewer than
two admissible venues is not a consensus. This is the design working, not
a crash.

| Venue | Reachable | Failed requirement | Detail |
|---|---|---|---|
| coinbase | yes | **4_book_with_sizes** | bid 85896.13 / ask 85896.14, ISO8601 ts identified, clock offset **1.033 s**, RTT 0.114 s, sequence id 1102756342 published — but **no sizes** |
| kraken | yes | **2 + 3_source_timestamp** | bid/ask **with** sizes (1.000 each), RTT 0.110 s — but **no source timestamp field at all** |
| binance | **no** | **1_reachable** | **HTTP 451** (unavailable for legal reasons) |
| gemini_spot | yes | **3 + 4** | non-numeric timestamp, no sizes |

**The cause is endpoint choice, and it is systematic.** All four URLs are
*ticker* endpoints, which by construction publish either a sized book or a
source timestamp but not both: coinbase and gemini_spot give a timestamp
without sizes, kraken gives sizes without a timestamp. The admissibility
rule is correct and should not be weakened — an unsized touch genuinely is
not an executable book, and a feed whose clock semantics cannot be
established genuinely cannot support a sub-second lead/lag claim.

**What would fix it is a change of endpoint, not of rule** — order-book
endpoints (coinbase `/products/BTC-USD/book?level=1`, kraken
`/0/public/Depth`) publish both, and binance's 451 needs a different host
or to be dropped with its USDT basis caveat. **That is a redesign of the
collector's source list, so I have not done it.** It is the one open
decision blocking Phase A, and limb 1 of the decision gate stays
unresolved until it is made.

Phase B remains unrun and unpreregistered, as before.
