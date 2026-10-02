# Gemini BTC5m — executable flow and realistically capturable maker volume

**Lane:** parallel L5 microstructure / executability research. Read-only.
**No live orders. No PMUS code or data touched. No strategy thresholds fitted.**

Scripts: `p0_flow_census.py`, `p0b_inwindow.py`, `p0c_print_vs_book.py`,
`p1_queue_shadow.py` (this directory). Raw inputs untouched in
`data/policy_d_port/`.

---

## THE ASAP ANSWER

> *Is there enough real Gemini BTC5m flow and realistically capturable maker
> volume to justify continuing this lane?*

**There is real flow. There is essentially NO realistically capturable maker
volume. On this evidence the lane is NOT justified as a compensated-maker
strategy — and the blocker is not the one we assumed.**

Gemini BTC5m is not quiet in contract terms: well-covered windows trade a
median of **133 contracts** and up to **8,310**, and the venue's own
cumulative-volume feed independently confirms it. But across **57** BTC5m
windows with genuine in-window coverage:

| maker policy (q = clip size) | fills | contracts captured | share of observed flow |
|---|---|---|---|
| STATIC (place once, never reprice) — q1 / q5 / q25 | **0 / 0 / 0** | 0.00 | **0.00%** |
| REQUOTE (follow touch, cancel+new forfeits queue) — q1 | 3 | 3.00 | **0.01%** |
| REQUOTE — q5 | 3 | 15.00 | 0.04% |
| REQUOTE — q25 | 3 | 75.00 | 0.22% |
| *OPTIMISTIC_UPPER* (queue_ahead forced to 0 — **physically unreachable, never realized**) — q1 | 92 | 90.22 | 0.26% |
| *OPTIMISTIC_UPPER* — q5 | 92 | 407.37 | 1.20% |
| *OPTIMISTIC_UPPER* — q25 | 92 | 1,607.01 | **4.72%** |

**Even a maker that is always first in queue with zero latency captures under
5% of flow.** That bound is unreachable by construction, and the realistic
policies capture ~0.

---

## P0 — IS GEMINI ACTUALLY BUSY?

### Coverage first, because it invalidates the naive reading

Two independent instruments, deliberately never merged:

- **Venue-reported volume** — `kind:"vol"`, cumulative traded volume per
  window from the Gemini events REST endpoint, polled ~20 s. Independent of
  our websocket coverage.
- **WS-captured prints** — the public `<symbol>@trade` stream: individual
  executions with `trade_id`, `qty`, `buyer_is_maker`. Complete **only while
  subscribed.**

Of 379 BTC5m windows seen, **344** carry venue expiry metadata, but only
**57** have ≥240 s of in-window book coverage. The prior canonical study
already attributed this to our collector (median in-window coverage 0 s,
median max gap 875.8 s), not to the venue — and this confirms it. **All
figures below use the 57-window denominator.** Anything computed over all
379 is measuring our subscription schedule, not Gemini.

### On the 57 well-covered BTC5m windows

| metric | p10 | p50 | p90 | max |
|---|---|---|---|---|
| prints in window | 0 | **3** | 9 | 38 |
| contracts in window | 0 | **132.9** | 1,365.5 | 8,310.2 |
| trades / min | 0 | **0.60** | 1.80 | 7.60 |
| contracts / min | 0 | **26.6** | 273.1 | 1,662.0 |
| venue volume delta in window | 6.0 | **129.6** | 785.3 | 5,393.0 |
| displayed spread | 0.10 | **0.20** | 0.30 | 0.375 |
| displayed depth, min side | 100 | **100** | 254 | 319 |
| BBO changes / min | 4.4 | **7.3** | 9.7 | 10.3 |

**46 of 57 (81%)** well-covered windows had at least one execution. Totals:
**241 prints / 34,071 contracts**. `buyer_is_maker` splits **121 / 120** —
aggressive flow is almost exactly two-sided.

**Does the displayed book understate activity? Partially — and it does not
help us.** 19.1% of prints (21.1% of contracts) executed **inside the
displayed spread**, so undisplayed or sub-sample liquidity is real. But that
liquidity is *competition quoting tighter than the displayed touch*, not
opportunity. A further 22.8% of prints landed when the book was one-sided.

**Flow is lumpy, not absent:** few prints, large clips (median 75 contracts
at the touch), concentrated near expiry — one window went from 941 to 5,682
cumulative contracts in its final 62 s.

### Hard measurement limit, stated not buried

These tapes sample the book at **~5 s** (observed gap p50 5.09 s; book age
before a print p50 2.23 s, max 5.08 s). Therefore:

- **BBO update rate, depth turnover and price-level turnover are
  SAMPLING-LIMITED and cannot be measured from this data.** The observed
  ~7.3 changes/min sits against a hard ceiling of ~12/min imposed by the
  sampler. These are lower bounds, not measurements.
- Sweep/aggressive-flow sequencing within a 5 s bucket is invisible.

The collector on this branch **already carries the fixes** —
`GEMINI_BBO_RECORD_SECONDS = 1.0`, `GEMINI_SUBSCRIBE_LEAD_S = 330.0`,
`GEMINI_RETAIN_AFTER_S = 30.0`, plus the independent 20 s volume poll — but
**no data from the fixed collector exists.** Every tape here predates it.

---

## P1 — EXECUTABLE MAKER CAPTURE, AND THE ACTUAL BLOCKER

Lifecycle is explicit: `PLACE(price, qty, queue_ahead) → FILL / PARTIAL /
CANCEL / EXPIRE`. Binding rules, all enforced in `p1_queue_shadow.py`:

- **No touch = fill.** A print at our price consumes the queue ahead of us
  first.
- **No invented queue.** `queue_ahead` is the displayed size at our price
  from the last BBO *strictly before* placement — joining a level means
  joining the **back** of it.
- **No zero-latency pegging.** Repricing is CANCEL + NEW and forfeits queue
  position.
- **Side discipline from `buyer_is_maker`**, which names the passive side. A
  print only fills an order on the side that was actually passive.
- Price must match exactly; we never fill on prints through a level we never
  displayed at.

### The blocker is QUEUE POSITION, not flow

Isolating the prints a touch-resting maker could possibly have served — the
passive side matched the displayed touch — gives **92 of 241 prints (38%),
10,075.6 contracts**. Then:

| | |
|---|---|
| touch-eligible prints | **92** |
| print qty at the touch | p50 **75.4**, p90 277.0, max 625.0 |
| displayed queue ahead | p50 **254.0**, p90 500.0, max 3,000.0 |
| **prints whose qty EXCEEDED the queue ahead** | **0 of 92 (0.0%)** |
| overflow reaching a back-of-queue maker | **0.00 contracts** |

The `qty / queue_ahead` ratio is p50 0.373 and **caps at exactly 1.000 from
p75 upward** — takers repeatedly lift precisely the displayed quantity and
never more. **The displayed queue at the touch is never over-consumed in this
sample.** A maker who joins that queue therefore captures nothing, ever —
which is exactly what the simulation returns independently (0 fills STATIC,
3 fills REQUOTE across 2,682 reprices).

Two independent methods agree exactly: 92 touch-eligible prints ≡ 92
OPTIMISTIC_UPPER fills.

### Capacity does not extrapolate

q1 → q25 scales exactly 25× in both REQUOTE (3 → 75) and OPTIMISTIC_UPPER
(90.2 → 1,607.0). That linearity is an artifact of fills being rate-limited
by *eligible print count*, not by size — every fill is clip-capped, never
flow-capped. **It is not evidence of capacity and must not be read as such.**

### ETH5m cross-check (56 covered windows)

Materially quieter: only **10 of 56** covered windows had any execution, **23
prints / 2,458 contracts** total. Capture is **0.00% under every realistic
policy** (0 fills, q1–q25); OPTIMISTIC_UPPER reaches 4.07% at q25. The prior
finding that ETH is the better *habitat* does not survive contact with
*executability*.

---

## P2 / P3 — NOT RUN, AND WHY

**P2 (adverse selection) is unrunnable.** It measures pre/post-fill mark
movement over a fill population. The defensible fill population is **3 fills
on BTC5m and 0 on ETH5m**. There is no distribution to take, and inventing
one from theoretical fills would violate this lane's rules.

**P3 (economics) is unrunnable, and the question itself is answered
negatively in a way that matters.** P3 asks whether realistic
execution/cancellation can push the historical **~$5.54 trading cost per $1
of modeled compensation** toward $1 without destroying capacity. On this
evidence realistic execution does not make fills *cheaper* — **it makes them
not happen.** Compensation accrues on *filled* maker volume; with ~0
capturable volume the ratio does not improve, it becomes undefined. No
verified rebate cash, published-formula rebate, or hypothetical rebate was
combined with anything here.

---

## RECONCILIATION OWED

The prior simulation reported **300 / 1,390 defensible fills, net ≈ −$99.19
before compensation, ≈ +$17.92 modeled compensation, 57/98 positive
windows**. Those figures are **not reproducible from this branch** and the
artifacts behind them are not present here.

**This analysis does not claim to overturn them.** The two are not
comparable as they stand: different sample (57 BTC5m windows ≥240 s coverage
vs 103 windows), and — decisively — **a stricter queue rule.** This model
joins the **back** of the displayed queue; any model that fills without
exhausting displayed depth first will produce orders of magnitude more fills.
**Which queue assumption the 1,390 figure used is the single most important
open question in this lane**, because the 0-of-92 result says that
assumption, not the flow, determines the entire economic conclusion.

---

## WHAT WOULD CHANGE THE VERDICT

Honest list, none of it fitted to this sample:

1. **Native 1 Hz / tick data from the already-fixed collector.** Everything
   above is conditional on 5 s sampling. The fixes are committed but never
   run. This is the cheapest next step and it is pure measurement.
2. **Queue-position provenance.** The 0-of-92 result is the whole verdict and
   it rests on displayed size as a queue proxy. Real queue position is not
   establishable from a public tape.
3. **Reconciling the 1,390-fill queue rule** (above).
4. **Quoting inside the spread**, where 21% of contracts actually executed —
   but that is price competition, not compensated making, and it forfeits the
   spread the strategy exists to earn.

**None of these is a reason to continue the lane as a maker strategy today.**
Items 1 and 3 are measurement debts worth clearing before the lane is closed
for good; they are not evidence of opportunity.

---

## DATA COVERAGE (exact)

- **Source:** `data/policy_d_port/tape_*.jsonl`, **46 tapes**, collector
  `src/policy_d_port/collector.py` (pre-fix: ~5 s BBO sampling).
- **Span:** 2026-08-30T23:18Z → 2026-09-01T12:12Z (≈37 h wall, not
  contiguous — bounded ~1.5–4.3 h sessions).
- **Gemini rows used:** 204,867 BBO + 692 prints (UP symbols only; no
  UP/DOWN double-count) + 2,106 cumulative-volume polls.
- **BTC5m:** 379 windows seen · 344 with venue expiry metadata · **57 with
  ≥240 s in-window coverage** · 46 of those with ≥1 execution · **241
  in-window prints / 34,071.18 contracts**.
- **ETH5m:** 344 with metadata · **56 with ≥240 s coverage** · 10 with ≥1
  execution · **23 prints / 2,458.20 contracts**.
- Dedup on `trade_id`. Nothing forward-filled; a gap stays a gap. Window
  bounds taken from venue `expiry` metadata, never from symbol parsing.

**Preserved and separate:** the prior canonical dataset
(`analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz`, 182,119 rows /
735 prints) is **unmodified**. Nothing in this lane rewrites it.
