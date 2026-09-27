# ECONOMIC_COMPLETE — conservation/causality audit

Windows audited: `1830z`, `1845z` (the two usable BTC15m windows of 2026-09-27),
at q=25 and q=50, θ=0.96 variant B, both settled UP (`settlementPx 1.0000`,
`MARKET_STATE_EXPIRED`).

Harness: `scripts/audit_economic_complete.py`. It re-walks the tape with its own
bookkeeping and derives net **purely from cashflows + settlement payout**. It
does not call `Book.settle()`, `run_window()` or any strategy P&L aggregation.

## Defect found and fixed

**The completion taker fee was charged twice in `net`.** `lock` subtracted the
fee, `lock` went into `self.locked`, and `net = matched + resid + locked - fees`
subtracted `self.fees` — which already carried that same fee. The independent
reconstruction disagreed with the reported net by *exactly* the reported fee, to
the cent, on all four audited configurations, while the B' baseline (no
completions, no fees) reconciled exactly.

Second, smaller defect in the same path: `add()` recomputed the fee from the
**blended** average fill price, whereas `marginal_complete()` returns the correct
**per-level** sum. The fee is convex in price, so these differ (observed
0.00007–0.000247 per window).

Fix: `locked` is now gross of the completion fee, `self.fees` carries the
per-level fee once, and the reported edge stays the net marginal edge.
Decisions are untouched — eligible=75, attempted=70, executable=12,
completed_qty=350, missed=0 at q=25/θ=0.96/B both before and after. No
threshold, clip grid, variant, split or completion gate changed.

The three focused suites still pass 29/29; they did not cover this path.

## Invariants after the fix

| # | invariant | result |
|---|---|---|
| 1 | no matched inventory recycled/reused in P&L | **PASS** — `acquired == retired + still_held` exactly, every config |
| 2 | no displayed ladder qty consumed twice at one observed state | **PASS**, one flag (below) |
| 3 | passive acquisition satisfies the preregistered causal fill inference | **PASS** structurally, one flag (below) |
| 4 | completion uses only the contemporaneous ladder | **PASS** on causality (ladder is never from the future), one flag (below) |
| 5 | qty never exceeds contemporaneous displayed depth | **PASS** — no violations |
| 6 | fees charged on every taker completion | **FAILED → FIXED** (was double-charged) |
| 7 | inventory conservation action-by-action | **PASS** — inventory never negative, conserved at every action |
| 8 | cashflow + settlement independently reconstructs net/window | **PASS** — 56/56 configurations exact |
| 9 | the median completion edge is economically real, not an artifact | **PASS as stated**, but **mis-attributed** (below) |

## Flags (quantified, not silently changed)

- **Ladder snapshot reused across states (INV2).** One event: the 18:38:06
  snapshot served completions at 18:38:08 and 18:38:36, 25 contracts each. The
  top level alone displayed 117, so no level was over-consumed. Within depth,
  but it assumes depth persisted for 30s.
- **Ladder staleness (INV4).** Never negative (no future data): max 31.2s on
  1830z, 14.4s on 1845z. But the ladder price differs from the contemporaneous
  bbo touch — **9 of 12 completions priced better than the live touch, 1 much
  worse**, netting **+$0.43/window at q=25** (~7% of EC net). Two-sided, so it
  is staleness noise rather than lookahead bias. A conservative refinement would
  cap the completion price at the contemporaneous touch; not applied, because it
  changes qualification economics and needs sign-off.
- **Queue position (INV3).** The reported runs use `queue_aware=False`, the
  *optimistic* bound — it assumes we are at the front of a queue with ~20,000
  shares displayed. The pessimistic bound yields **zero matched contracts**.
  Neither bound is right: `ahead = bid_shares` is a latent defect, because
  `bid_shares` (~20,000) is the **whole-ladder sum**, not the touch-level size
  (~131–1,591), which is what a queue-ahead measure requires. Not changed — it
  does not affect any reported number, and correcting it alters the fill model.

## INV9 — the edge is real cash, but it is not a completion edge

The ~13.67¢ figure reconciles to cashflow and is not a price-convention
artifact. The own-price mapping was proved self-consistent: paying `n*(1-bid)`
and collecting `n*$1` for the retired pair is *identically* `n*(bid - basis)` —
i.e. **ECONOMIC_COMPLETE as implemented is selling the aged leg into the bid**,
not buying a complement.

That fixes the interpretation:

- Buying **both** legs fresh at any completion instant costs **1.0100–1.0300** —
  never below $1. There is no instantaneous pair arbitrage anywhere in the tape.
- **Every** completion occurs with the aged leg already in profit
  (`mkt_now - basis`: min 0.0210, median 0.0963, max 0.3650).
- Booked completion edge correlates **0.87** with the aged leg's unrealized
  directional gain over n=20 completions with a two-sided book.
- The edge decays monotonically within a window (0.167 → 0.137 → 0.086 → 0.065)
  as the move is exhausted.

So the θ≤0.96 gate is functioning as a *filter for an aged leg that has moved in
our favour*, and the "completion edge" is realized directional carry. It is real
money, but it is not fee-safe pair economics and must not be labelled as such.

Also: the pooled median across all 24 completion events is **8.57¢**, not 13.67¢.
13.67¢ is 1830z's median; the earlier pooled figure was a median-of-medians.

## Corrected numbers (θ=0.96, variant B, per window, n=2)

| q | B' net | EC net | delta | matched con | completion con | matched gross | fees | residual P&L | max residual |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.1950 | 0.2636 | +0.0686 | 14 | 7 | 0.3550 | 0.0914 | 0.0000 | 2 |
| 5 | 0.9750 | 1.3181 | +0.3431 | 70 | 35 | 1.7750 | 0.4569 | 0.0000 | 10 |
| 10 | 1.9500 | 2.6362 | +0.6862 | 140 | 70 | 3.5500 | 0.9138 | 0.0000 | 20 |
| 25 | 4.8750 | 6.5201 | +1.6451 | 350 | 175 | 8.8050 | 2.2849 | 0.0000 | 50 |
| 50 | 9.7500 | 12.7336 | +2.9836 | 700 | 350 | 17.3057 | 4.5721 | 0.0000 | 100 |
| 100 | 19.5000 | 22.6731 | +3.1731 | 1377 | 653 | 31.0296 | 8.3565 | 0.0000 | 200 |
| 150 | 29.2500 | 31.8846 | +2.6346 | 2052 | 953 | 43.9817 | 12.0971 | 0.0000 | 300 |

**The fix reverses the earlier sign.** Pre-fix, EC lost to B' at every q; that
was the double-charged fee. Corrected, EC beats B' at every q, delta peaking at
q=100.

θ sweep at q=25 (per window): 0.96 → **+6.5201**; 0.98 → +2.5079;
1.00 → +0.7130; 1.02 → −2.1136; 1.05 → +0.4543. Fees still dominate as θ loosens.

## Standing caveat

n=2, and **both** windows settled UP. B' net remains largely unhedged
directional P&L (1830z: 78.3% residual; 1845z: matched P&L −$17.50 at q=25,
positive only via +$16.75 residual). EC's own net carries **zero** residual, so
it is not directionally exposed at settlement — but its edge source is
directional carry on the aged leg, which two same-direction windows cannot
separate from luck.

Status: **AUDITED_PRELIMINARY**. Conservation and causality hold. Not an
economic result. Checkpoint 25 remains the preregistered economic checkpoint.
