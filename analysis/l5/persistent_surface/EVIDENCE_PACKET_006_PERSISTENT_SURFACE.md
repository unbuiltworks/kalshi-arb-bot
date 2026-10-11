# Evidence packet 006 — does L5's tape require concurrent resting orders?

P0 test before any further live probe. **No strategy, envelope, sizing,
authorization, Railway or live execution is modified by this packet.**

Sources (existing canonical history; nothing recollected):
`data/l5/raw/activity.jsonl` — 78,000 items across 157 pages, deduplicated to
**71,984 TRADE rows**, 2026-03-19 → 2026-08-31.
`data/l5/raw/trades_takeronly.jsonl` — the `takerOnly=true` feed, 11,810 rows,
2026-08-28 → 2026-08-31.
Artifacts: `persistent_surface_test.json`, `maker_taker_crosscheck.json`.
Code: `scripts/l5_persistent_surface_test.py`.

## The anti-artifact findings come first, because they decide what is answerable

**1. The clock is whole seconds.** All 71,984 rows carry integer
`timestamp`; there is exactly **one** distinct fractional part across the
entire history (`.0`).

> **Requests 1, 2, 3, 4, 6 and 9 as specified cannot be answered from this
> source.** No p10/p25/p50 of inter-fill time below one second exists to be
> measured. Every "≤250 ms / ≤500 ms / ≤1 s" bucket is unanswerable, and the
> illustrative sequence in the brief (`t=0.000 / 0.180 / 0.410 / 0.730`) is
> **not observable** here in either direction. Any such distribution would be
> a property of the clock, not of L5.

**2. Co-occurrence within a second is an upper bound, not a burst.** 13,718
second-buckets hold more than one row. Of those, 1,546 are a *single*
transaction (fragmentation) and the rest span 2–8 distinct transactions that
may be up to a second apart. Reported as a bound; not used as evidence.

**3. Fragmentation is separated, not counted as rungs.** One maker order
consumed by several counterparties yields several rows. Grouping by
`transactionHash` collapses these: BTC15m has only **2** pure-fragmentation
transactions (>1 row, 1 price), so fragmentation is *not* what produces the
multi-level result below.

## The test that does not depend on the clock

A single `transactionHash` is one taker action against the book at one
instant. Therefore:

> if **one transaction** consumed L5's liquidity at **two or more distinct
> prices on the same outcome**, L5 had **two or more orders resting
> simultaneously** at that instant.

This needs no sub-second clock. It is the minimum-concurrency measurement.

### Result, and it is asymmetric

| family | rows | transactions | multi-price tx | max levels in one tx | pure fragmentation |
|---|---|---|---|---|---|
| **BTC15m** | 63,569 | 60,132 | **3,434** | **3** | 2 |
| **BTC5m** | 6,229 | 6,229 | **0** | 0 | 0 |
| ETH5m | 2,069 | 2,069 | 0 | 0 | 0 |
| ETH15m | 117 | 117 | 0 | 0 | 0 |

Examples (always **adjacent ticks** — the signature of a swept ladder):

```
0x4276b144…  Up    0.69 + 0.70   size 100.0    btc-updown-15m-1787903100
0x3523e8ba…  Up    0.68 + 0.69   size  26.84
0x8d4e40a9…  Down  0.28 + 0.29   size 100.0
0x3258c67d…  Up    0.77 + 0.78   size  50.0
```

### The maker/taker discriminator — the inference's load-bearing check

A multi-price transaction proves concurrency **only if L5 was the maker**; as
taker, L5 would be sweeping someone else's book. Inside the taker feed's own
coverage window (2026-08-28 → 08-31), where the check is valid:

```
activity transactions in window        63,278
… also present in the taker feed       11,068     (the feed does match)
multi-price transactions               3,434
… where L5 was the TAKER                    0
… where L5 was the MAKER                3,434     ← all of them
```

**Every multi-price transaction is a maker fill.** All of L5's taker activity
is single-price.

### BTC5m's zero is a finding, not a gap

At BTC15m's multi-level rate (0.0571), 6,229 BTC5m transactions would be
expected to produce **≈356**. Observed: **0**.
`P(0 | same rate) ≈ 8.4 × 10⁻¹⁶⁰`. The two families genuinely differ.

BTC5m fills are also **larger** (median size 50.0 vs 10.49) and fewer.

### One test that proved nothing, stated as such

**Zero** transactions consumed both outcomes — in every family. This is
**weak evidence and must not be used**: a taker transaction buys one outcome
and *cannot* consume the other's liquidity, so zero is what this test returns
whether or not L5 rested both sides. It does **not** argue against concurrent
two-sided resting.

## The question asked

> Would a strictly sequential controller — wait for a fill, then decide, then
> post the next order — be physically capable of generating the observations?

**BTC15m: No.** A sequential controller has at most one order exposed, so a
single taker transaction can consume at most one of its price levels. 3,434
transactions consumed two, and one consumed three. That is not a timing
inference and no clock resolution weakens it.

**BTC5m: Not ruled out.** Zero multi-level transactions in 6,229. On the
family we are actually cloning on Gemini, this test produces **no evidence
against** sequential execution.

### Minimum concurrency the observations require

* **≥ 2 orders resting simultaneously on the same outcome, at adjacent
  ticks** — 3,433 instances (BTC15m).
* **≥ 3, once** — a single instance.
* **Both sides concurrently: not established by this test**, for the
  structural reason above.

That is the whole of it. **Nothing here supports a 1–99 grid, or any specific
width beyond 2–3 adjacent levels on one side, and only on BTC15m.**

## Provenance of our ~10–20 s cancel/reprice cadence

Traced, and the answer is worse than "arbitrary":

* **There is no cadence constant.** No timer, no interval, no configured
  reprice period exists anywhere on the live path.
* **The cancel driver is `ws_continuous.diff_surface`**, which matches
  resting orders to the desired surface **exactly on `(leg, price, qty)`**.
  Any price change makes the resting rung "not in the desired surface" and it
  is cancelled and replaced. The cadence is therefore emergent — set by how
  fast the book moves and how often the driver re-decides — which is why
  LIVE-2 showed 9 s, 19 s, 11 s rather than any round number.
* **A rest floor exists and is DEAD CODE.** `continuous_probe.stale_orders()`
  enforces `STALE_ORDER_S = 30.0`, sourced from the frozen lane's own
  `minimum_rest_time`. It is called **nowhere in `src/`** — only from three
  tests, which is the worst case: the tests assert the floor works while
  nothing on the live path invokes it.
* **The frozen lane's own `minimum_rest_time` is 30.0 s.** LIVE-2 cancelled
  three of its five NO rungs at **9 s, 19 s and 11 s** — below the frozen
  artifact's own rest floor.

**Classification: IMPLEMENTATION-DERIVED / NOT L5-EVIDENCED.** Nothing in
L5's observable tape motivated it, and it additionally violates a rest
discipline our own frozen lane already specifies.

(`diff_surface`'s docstring states that re-placing a rung "would surrender
queue position" — the harm is documented for the KEEP path, while the cancel
path has no floor at all.)

## The four models

Stated as evidence for, evidence against, and unidentified variables. **No
scoring, no ranking.**

### A — sequential reactive (one order at a time)

*Consistent with:* all BTC5m observations (6,229 single-level transactions,
zero multi-level); all ETH observations; L5's taker activity, which is
uniformly single-price.
*Inconsistent with:* 3,434 BTC15m maker transactions consuming 2–3 distinct
adjacent prices. A single exposed order cannot be hit at two prices at once.
*Unidentified:* whether BTC5m's zero reflects a different policy, thinner
books that are never swept through two levels, or the larger median size
(50.0) sitting at one level by construction.

### B — persistent surface (a standing ladder, reshaped periodically)

*Consistent with:* the adjacent-tick pairs; 2–3 levels swept in one taker
action; maker-only status of every such fill.
*Inconsistent with:* nothing in this dataset directly — but nothing here
establishes *persistence over time* either. A ladder posted and immediately
swept is indistinguishable here from one that stood for minutes.
*Unidentified:* order lifetime, cancel behaviour, and how many levels stood
unfilled — **none of which is observable in a fills-only tape.** Absence of
order-book data is the binding limitation on B.

### C — hybrid: persistent surface modified by inventory

*Consistent with:* the same evidence as B, plus the family asymmetry (BTC15m
laddered, BTC5m not) which a single fixed rule would not produce.
*Inconsistent with:* nothing directly observed.
*Unidentified:* the inventory-response rule itself. This packet measured
concurrency, not what reshapes the surface. Distinguishing C from B requires
order-level data we do not have.

### D — unresolved

*Consistent with:* the honest state for BTC5m specifically, where the
discriminating test returns zero and A, B and C are all still admissible.

## What this changes, and what it does not

**Established:** on BTC15m, L5 had ≥2 (sometimes 3) orders resting
simultaneously at adjacent ticks, thousands of times, as maker. A strictly
sequential controller cannot reproduce that.

**Not established:** anything about BTC5m, which is the family our Gemini
clone trades. Ladder width beyond 2–3 adjacent levels. Two-sided concurrency.
Order persistence, lifetime or cancel discipline.

**Separately established, and actionable independently of any model:** our
cancel/reprice cadence has no basis in L5 evidence, no governing constant,
and violates our own frozen lane's 30 s `minimum_rest_time` because the code
enforcing it is never called.

No rule is derived from this packet. No executor change is made.
