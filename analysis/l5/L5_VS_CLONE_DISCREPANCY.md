# L5 vs clone: behavioural discrepancy evidence

Clone benchmark frozen at **`49799e99`** (Batch-2 OOS, 18 windows). No strategy
logic changed by this analysis.

## GATE FIRST: state-matched alignment is NOT possible on current data

The requested form -- "at this exact market state, what did L5 do next vs the
clone" -- cannot be produced legitimately today, for two independent reasons:

1. **No temporal overlap.** L5's harvested activity covers 2026-03-19..04,
   2026-08, **2026-09-01..09-10** and **2026-09-22..09-23**. The PMUS recorder
   archive covers **2026-09-27..09-30**. The intersection is empty, so no PMUS
   window we have replayed has any L5 action in it.
2. **No same-venue market state.** We hold no Polymarket order-book/print tape
   at L5's timestamps (`data/l5/raw/markets.jsonl` is market metadata). So L5's
   actions cannot be placed against a reconstructed book even on his own venue.

Anything labelled "the state L5 faced" would therefore be fabricated. What
follows is a **distributional** comparison on identical metric definitions,
explicitly NOT state-matched, plus the concrete path to the real thing.

## What the public feed can and cannot support

| dimension requested | status |
|---|---|
| time unmatched before next action | DIRECT |
| next side: complement vs same side | DIRECT |
| next price, distance from prior fill | DIRECT |
| next size, dependence on imbalance | DIRECT |
| same-side basis-improving EXTEND frequency | DIRECT |
| complement completion frequency/latency | DIRECT |
| inventory imbalance tolerated, duration | DIRECT |
| repeated cycling after matching | DIRECT |
| change as settlement approaches | DIRECT |
| behaviour after adverse vs favourable move | **PARTIAL** -- needs a price path we lack on L5's venue |
| WAIT / persistence / replenishment vs repricing | **INFERRED ONLY** -- the feed has fills, never orders, cancels or requotes |
| maker vs taker | **NOT DETERMINABLE** -- no role field; the entire history holds 4 MAKER_REBATE and 3 TAKER_REBATE rows |

Methodological note: rows sharing (timestamp, slug, outcome, price) are fill
FRAGMENTS of one order and are grouped into one ACTION. Ungrouped, every timing
and count statistic is inflated.

## L5 measured, two independent periods

| metric | Sep 01-10 | Sep 22-23 |
|---|---|---|
| markets | 861 | 147 |
| actions | 105,302 | 12,381 |
| actions per market | ~122 | ~84 |
| OPEN / EXTEND / COMPLETE / REOPEN | 861 / 53,152 / 51,264 / **25** | 147 / 6,228 / 6,005 / **1** |
| same-side share of follow-ups | 0.672 | 0.667 |
| median gap between actions | **2 s** | 3 s |
| median abs residual BEFORE an action | **140.4** | 194.9 |
| p90 / max residual | 369 / 1,659 | 492 / 1,804 |
| median size | 18.3 | 29.4 |
| EXTEND that improves basis | **0.451** | 0.521 |
| actions taken while already holding matched inventory | 50,623 | 5,725 |

Both periods agree on every qualitative feature, so these are stable properties.

## The discrepancies, ranked by magnitude

| # | behaviour | L5 | clone (`49799e99`) | gap |
|---|---|---|---|---|
| 1 | **same-side EXTEND** | ~50% of all actions (53,152) | **structurally impossible** -- `allow_extend` defaults False, so Batch 1/2 performed ZERO extends | categorical |
| 2 | **tolerated imbalance** | median 140, max 1,659 contracts | `residual_cap=2`, observed max residual **1** | ~100x |
| 3 | **size** | median 18.3, max 320 | `clip=1` | ~18x |
| 4 | **action rate** | ~122 actions/market, median gap 2 s | ~13 fills/window | ~9x |
| 5 | **basis discipline on EXTEND** | only 45-52% improve basis | clone blocks any extend that does not improve basis (`extend_would_not_improve_basis`) | clone forbids over half of L5's extends |
| 6 | **cycling vs one continuous book** | 25 REOPENs in 861 markets: ONE continuous inventory per market | cycles per banked pair | categorical |
| 7 | size vs imbalance | 18.28 imbalanced vs 22.73 flat -- essentially independent | n/a (fixed clip) | L5 does NOT size down on imbalance |

## This explains the $0.36-$0.40-per-$1-flatten finding

Our clone's adverse-selection signature is measured as the cost of a
HYPOTHETICAL unwind. The evidence says **L5 never pays it**: 2 SELL rows in
78,000, 25 REOPENs in 861 markets, and residual held in the hundreds of
contracts. He does not exit adverse inventory, he keeps acquiring on the same
side -- over half the time at a WORSE basis -- and carries it to settlement.

So the flatten shadow may be measuring a cost that is an artifact of our
clone's exit-oriented frame rather than a real economic drag. That is a
HYPOTHESIS, stated as such, and it is testable without any policy change.

## Smallest set of rules that could explain the largest discrepancies

Ranked by discrepancy magnitude, and NOT yet implemented:

1. **Permit same-side EXTEND unconditionally on price** (drop the
   basis-improving requirement) -- addresses discrepancies 1 and 5, the only
   categorical ones.
2. **Raise tolerated imbalance by orders of magnitude** -- addresses 2.
3. **One continuous inventory per market instead of per-pair cycling** --
   addresses 6.

Frequency before size, per the operating model: rule 1 is the only one that is
free of capital increase, and it is also the largest gap.

## Required to produce the real state-matched report

Harvest L5 activity for **2026-09-27..09-30** (the harvester already windows by
`start`/`end`; the API's offset cap is why it must). That yields wall-clock
overlap with the PMUS archive, permitting WINDOW-LEVEL alignment by 15m epoch on
the same underlying -- still cross-venue, which must be stated every time, but no
longer fabricated. Book-level alignment would additionally require recording
Polymarket L2 going forward.
