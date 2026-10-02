# Gemini BTC5m — decision gate on fresh event-level evidence

**Read-only. No live orders. No PMUS/SOL changes. No thresholds, quote
placement, timing or cancellation rules tuned against this cohort.**

> **Gate:** does fresh event-level evidence show enough defensibly capturable
> BTC5m maker flow to justify proceeding to an L5-style sequential inventory
> shadow?

# **NO.**

The measurement debt is cleared, the better data **confirms** the earlier
answer rather than overturning it, and it adds an independent structural
reason the lane cannot work. **Recommendation: stop Gemini BTC5m maker
strategy work.**

---

## 1. THE MEASUREMENT DEBT IS CLEARED

This was the one reason not to close the lane from the old tapes. It no
longer stands.

| | legacy ~5 s cohort | fresh event-level cohort |
|---|---|---|
| book feed | `@depth5@100ms` snapshots, written at ≤1 Hz | **`@depth` diff stream, every event preserved** |
| observable book events/min | ~12 (a **sampling ceiling**) | **p50 100.2**, max 109.1 |
| in-window coverage | median **0 s**; 57 of 379 ≥240 s | **p50 299.0 s**; 35 of 39 ≥240 s |
| book age before a trade | p50 2.23 s, max 5.08 s | **p50 0.447 s**, max 2.17 s |
| book desync | not checkable (no sequence ids) | **0.0%** — chain verified on `u`→`U` |
| trade↔book ordering | not establishable | venue event-time ns on **84.0%**; 16.0% labelled UNKNOWN |

Reconstruction is independently validated: on a 73,941.98-contract bid at
0.21, consecutive trades decrement the displayed size by **exactly** their
quantity (73942.0 → 73939.6 → 73937.2, deltas 2.40, 2.40).

## 2. FLOW IS REAL — THAT WAS NEVER THE PROBLEM

35 well-covered BTC5m windows, **250 trades / 51,287.97 contracts**.

| per window | p50 | p90 | max |
|---|---|---|---|
| contracts | **233.4** | 2,628.9 | 23,522.3 |
| trades | **4.0** | 11.8 | 65 |

50.2% of trades land exactly on a displayed touch, 33.1% inside the spread,
11.9% through a touch.

## 3. DEFENSIBLE CAPTURE IS ~1% AND THE BETTER DATA DID NOT MOVE IT

Three bounds, never averaged, never blended:

| tier / clip | fills | contracts | % of flow | % of eligible | of which *through* | windows w/ any fill | rest/fill p50 |
|---|---|---|---|---|---|---|---|
| **DEFENSIBLE** q1 | 30 | 30.00 | **0.06%** | 0.06% | 26.00 | 18/35 | 1.0 s |
| DEFENSIBLE q5 | 30 | 150.00 | 0.29% | 0.29% | 130.00 | 18/35 | 1.0 s |
| DEFENSIBLE q10 | 32 | 279.00 | 0.54% | 0.54% | 244.00 | 18/35 | 1.0 s |
| DEFENSIBLE q25 | 33 | 624.00 | **1.22%** | 1.22% | 559.00 | 18/35 | 1.0 s |
| PLAUSIBLE (pro-rata) q1→q25 | 30→33 | 30.00→624.00 | 0.06%→1.22% | — | 24→509 | 18/35 | 1.1 s |
| *ZERO_QUEUE* (unreachable) q1→q25 | 87→93 | 87.00→1,548.26 | 0.17%→3.02% | — | 12→238 | 27/35 | 1.3 s |

**Against the legacy cohort's corrected DEFENSIBLE 0.08%→1.80%, the
event-level cohort gives 0.06%→1.22%.** Eight times the book fidelity, full
window coverage, verified ordering — **and the same answer.** The thin
capture was never a sampling artifact.

Pro-rata allocation adds essentially nothing over FIFO, because the queue is
too large for the allocation rule to matter.

## 4. THE MECHANISM: QUEUE PRIORITY IS UNATTAINABLE

Displayed size at the price where trades actually execute:

| q_ahead at execution price | p50 | p75 | p90 | max |
|---|---|---|---|---|
| contracts | **721.0** | 48,694.0 | 67,231.2 | 73,942.0 |

One participant rests ~74,000 contracts at a single price while takers nibble
**2.4 contracts at a time**. Joining that queue is not a strategy. At the
touch where we would rest, `queue_ahead` is p50 25 but p90 561.6 and max
52,605 — the cheap levels are cheap because nothing trades there.

## 5. THE DECISIVE NEW FINDING: THE ONLY CAPTURABLE FLOW IS THE TOXIC FLOW

Adverse move after DEFENSIBLE fills, **fixed** 5/15/30 s horizons, no
threshold fitted. Positive = the mark moved **against** the maker.

| clip | 5 s | 15 s | 30 s |
|---|---|---|---|
| q1 | **+0.0300** (n=26) | **+0.0350** (n=27) | **+0.0850** (n=27) |
| q5 | +0.0300 (n=26) | +0.0350 (n=27) | +0.0850 (n=27) |
| q10 | +0.0300 (n=28) | +0.0300 (n=29) | +0.0750 (n=29) |
| q25 | +0.0300 (n=29) | +0.0200 (n=30) | +0.0800 (n=30) |

**All 12 cells are adverse**, and the move grows with horizon: ~3 cents at
5 s to ~8 cents at 30 s, on a contract worth at most $1.

This is **structural, not incidental**. DEFENSIBLE fills are dominated by
**through-prints** — 26 of 30 contracts at q1. A through-print means someone
swept *through* our resting price. Being swept through **is** adverse
selection, by definition. Because queue priority at the touch is
unattainable (§4), the at-price fills mostly do not happen, so the residue
we can defensibly claim is precisely the sweep flow that hurts us.

**The lane's problem is not that capture is small. It is that the small
capture is selected for toxicity.** Making execution more realistic cannot
fix that: it is what realistic execution *selects*.

## 6. ECONOMICS — NOT COMPUTED, AND WHY

Per instruction, economics are not extrapolated from a handful of fills.
**30–33 defensible fills is not a sample for a P&L claim and none is made
here.** No verified rebate cash, published-formula rebate, or hypothetical
rebate has been combined with anything in this document.

What can be said as an **order-of-magnitude screen, explicitly not an
economic result**: compensation would have to exceed the observed ~3–8 cents
per contract of adverse movement to make a filled contract worth taking. The
preserved prior evidence put the trading cost at **~$5.54 per $1 of modeled
compensation**. Nothing in this cohort moves that ratio toward $1 — realistic
execution reduces the *number* of fills by orders of magnitude while leaving
each surviving fill adverse, so the ratio gets worse, not better, and at this
fill count it is not estimable at all.

A defensible economic verdict would need on the order of **hundreds of
defensible fills**. At this cohort's rate — ~30 defensible fills per 35
windows — that is roughly **350–700 well-covered windows, i.e. 30–60 hours**
of continuous event-level collection. That is a cost worth naming before
anyone pays it, given §5 says the sign is already known.

## 7. DATA COVERAGE (exact)

- **Collector:** `analysis/gemini/microstructure/lane_recorder.py` —
  lane-isolated, public market data only, unauthenticated, no order path.
  Subscribes `@depth` (every diff), `@trade`, `@depth20` (snapshot/resync);
  5 s `volume` poll. Every frame preserved verbatim.
- **Segments** (`data/gemini_hifi/`, raw gitignored per repo convention, `.gz` tracked):

  | tape | duration | trades | depth updates | vol polls | BTC5m symbols | errors |
  |---|---|---|---|---|---|---|
  | `hifi_20261002T132054Z` | 13.1 min | 21 | 499 | 344 | 7 | 2 |
  | `hifi_20261002T141213Z` | 116.7 min | 239 | 4,862 | 4,933 | 29 | 2 |
  | `hifi_20261002T160909Z` | 57.1 min | 44 | 2,174 | 1,968 | 17 | 2 |

  Segment 1 was truncated by **egress proxy port rotation**, not by anything
  about Gemini; the recorder now re-reads the proxy before every connect.
  **Coverage is therefore NOT contiguous** — there is a ~39 min gap between
  segment 1 and 2 (13:33Z→14:12Z) and a ~3 min gap between 2 and 3.
- **Span:** 2026-10-02 13:20Z → 17:06Z. **~187 min of event-level coverage.**
- **BTC5m:** 39 windows with in-window book events · **35 with ≥240 s
  coverage** (p50 299.0 s) · 250 trades / 51,287.97 contracts · 287 trades
  reconstructed against a pre-trade book at 0% desync.
- ETH5m collected concurrently; not analysed here.
- Dedup on `trade_id`. Nothing forward-filled. Window bounds from venue
  `expiryDate`/`startTime` metadata, never from symbol parsing.
- **Legacy cohort preserved and separate:** `data/policy_d_port/` and
  `analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz` unmodified.

## 8. `volume` FIELD — SEMANTICS SETTLED (item 5)

Measured against the nanosecond trade stream over 35 windows:

- **Units: CONTRACTS.** `final_poll / final_WS_cumulative` p50 = **1.000**
  (p25 0.466, p75 1.028). A notional reading gives 0.536 and is wrong.
- **Cadence: coarse.** Polled every 5 s, the value changes only every
  **p50 12.8 s** (p90 64.2, max 160.6).
- **Lag: material.** When it does change, it reports volume the trade stream
  had already shown **p50 24.1 s earlier** (p90 87.0 s, max 132.1 s).

**Therefore: a lagging, coarse lower bound — never authoritative for a
window's total and never a substitute for `@trade`.** This is why the
historical capture ratios exceeded 1: the field had not caught up, and
volume concentrates in a window's final seconds. Earlier text calling it
"authoritative" or "venue-authoritative" is withdrawn.

## 9. UNRESOLVED UNCERTAINTIES

1. **Queue position is still not provenanced.** `q_ahead` is *displayed*
   size. Hidden, iceberg or refreshing queue is unobservable from public
   data. §4's conclusion would only strengthen if hidden size exists.
2. **Cancel position is an assumption, not an observation.** Whether a cancel
   sat ahead of or behind us is unknowable; DEFENSIBLE assumes behind,
   PLAUSIBLE pro-rata. They barely differ here, so the assumption is not
   load-bearing — but it is still an assumption.
3. **16.0% of trades have UNKNOWN trade↔book ordering**, and 41.1% have
   UNKNOWN disappearance cause (no following diff at that price, or no
   displayed level). Not scored as fills either way.
4. **Adverse selection rests on n=26–30 fills.** The sign is consistent
   across 12 independent cells, but the magnitude is not tightly estimated.
5. **Coverage is ~187 min and not contiguous**, across a single UTC
   afternoon. No time-of-day, weekday, or volatility-regime segmentation is
   supportable; BTC only.
6. **The 300/1,390 cohort is still not reproducible.** Its queue rule is
   identified (`continuous_inventory.py`), but its data went to the Railway
   volume `/state/gemini-5m` and no `gemini-5m-collector` service remains in
   the projects enumerated. **I did not prove it is permanently gone.**
7. **25.8% of trades are TRADE_PLUS_CANCEL** — more size leaves the level
   than traded. Quote fragility is real and unquantified; it can only hurt a
   resting maker, so it is not a source of upside.

## 10. WHAT WOULD REOPEN THIS

Only a *different* claim, not more of the same measurement:

- Evidence that Gemini maker compensation per contract **exceeds** the
  observed adverse move — which would have to come from verified credited
  cash, not a formula.
- A fill source that is **not** sweep flow: genuine queue priority from being
  early at a level, which §4 says is unattainable at these sizes, or quoting
  **inside** the spread, which forfeits the spread the strategy exists to
  earn and is where 33.1% of flow already executes against competitors.

Neither is a measurement debt. **The measurement debt is paid, and the answer
did not change.**
