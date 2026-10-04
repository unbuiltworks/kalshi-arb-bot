# CELL_C_HOLD_NO_REENTRY_V1 — DEVELOPMENT result

Controller frozen in `PREREGISTRATION_CELL_C_HOLD_NO_REENTRY_V1.md`, committed
at `060de7a4` **before** this number existed. Runner:
`scripts/pmus_hold_no_reentry.py`. Data:
`analysis/l5/hold_no_reentry/hold_no_reentry_development.json`.

**OOS is NOT in this file.** The post-development tape on the Railway volume is
still unretrieved; the blocker is authorization, documented in
`analysis/l5/oos/OOS_RETRIEVAL_STATUS.md`. DEVELOPMENT and OOS are reported
separately and are never pooled.

## Preregistered verifications — all three passed

1. **Purchase-leg identity.** All 387 purchase legs (28 ENTRY + 359 EXTEND)
   are byte-identical between Cell C and HOLD_NO_REENTRY, in order, on
   (t, side, px, shares, fee). The counterfactual changed nothing about entry
   or extension. This is asserted at runtime; a mismatch aborts the run.
2. **Zero sell sleeve.** HOLD_NO_REENTRY: 0 exit events, 0 exit legs, $0.00
   sale cash.
3. **Five-term identity.** Max absolute residual 0.000000 across all 56
   controller-windows.

The run also reproduces the frozen Cell C exactly: total net **-$1.3861**
against the previously recorded -$1.3858/-$1.3854, and 387 purchases + 279
exit **events** = 666 actions, 387 + 289 exit **legs** = 676 — the resolved
676-vs-666 discrepancy, unchanged.

## Both controllers, DEVELOPMENT (28 eligible, 28 traded, $1 frozen alloc)

| | frozen CELL_C | HOLD_NO_REENTRY_V1 |
|---|---|---|
| actions / window | 23.79 | **13.82** |
| ENTRY / EXTEND / EXIT events | 28 / 359 / 279 | 28 / 359 / **0** |
| exit legs | 289 | 0 |
| purchase cash | $386.6737 | $386.6737 |
| sale cash | $327.3736 | $0.0000 |
| settlement cash | $69.3342 | $359.6085 |
| **gross before fees** | **+$10.0341** | **-$27.0652** |
| fees, modeled 4-dp | $11.4202 | **$5.9165** |
| fees, venue-cent rounded | $11.1800 | **$5.7700** |
| **net, modeled** | **-$1.3861** | **-$32.9817** |
| **net, venue-cent** | **-$1.1459** | **-$32.8352** |
| gross / traded window | +$0.3584 | -$0.9666 |
| net / traded window (modeled) | -$0.0495 | **-$1.1779** |
| net / traded window (venue-cent) | -$0.0409 | -$1.1727 |
| median net (modeled) | -$0.0485 | **+$0.9868** |
| positive % (net, modeled) | 39.3% | **57.1%** |
| LB1 / LB3 net | -$0.0225 / +$0.0186 | -$0.8166 / -$0.1011 |
| worst window | -$0.7785 (10-02 1800z) | **-$10.9325** (10-02 2030z) |
| peak capital (mean) | $3.3085 | $14.0210 |
| peak residual (mean) | 3.7502 sh | 12.7700 sh |
| capital turnover (deployed / peak capital) | 4.174x | 0.985x |

Turnover above is **deployed cash over peak capital tied up**. The previously
reported "turnover = 30.24" was remaining acquisition cost basis per window
and is not a turnover figure; it is not reused here.

Window-level bootstrap, 20,000 resamples, seed 20261004:

| | net / window | 95% CI | P(mean > 0) |
|---|---|---|---|
| CELL_C | -$0.0495 | [-$0.1627, +$0.0629] | 0.198 |
| HOLD_NO_REENTRY_V1 | -$1.1779 | [-$2.9243, +$0.4528] | 0.083 |

## Windows reaching $5, by capital envelope

Linear scaling of the $1-alloc replay by peak capital. This is an **upper
bound**: the capacity study measured impact as flat with size only to about
$100, and scaling the winners scales the losers identically.

| envelope | CELL_C gross>=$5 | CELL_C net>=$5 | HOLD gross>=$5 | HOLD net>=$5 |
|---|---|---|---|---|
| $25 | 11 / 28 | 3 / 28 | 5 / 28 | 4 / 28 |
| $50 | 15 / 28 | 5 / 28 | 14 / 28 | 12 / 28 |
| $100 | 18 / 28 | 6 / 28 | 15 / 28 | **15 / 28** |
| $200 | 22 / 28 | 10 / 28 | 16 / 28 | 16 / 28 |

HOLD reaches net >= $5 in 15 of 28 windows at $100 while its **mean stays
negative**, because the left tail scales too. A majority of windows clearing
$5 is not a profitable strategy.

## The economic answer

> Does eliminating flatten/re-entry convert the architecture from paying
> repeatedly for realized reversals into a positive-expectancy hold/extend
> process?

**No. On development it makes it materially worse, and it identifies the exits
as the source of the gross rather than the drag.**

The fee half of Grok's decomposition reproduces almost exactly. Removing the
sell sleeve cut modeled fees from $11.4202 to $5.9165 — **-48.2%**, against
Grok's "EXIT ~= 48% of base fees." That part of the diagnosis was right.

But the saving is swamped. Gross went from **+$10.0341 to -$27.0652**, a
$37.10 swing against a $5.50 fee saving. The mechanism is visible directly in
the cashflows:

- **HOLD** pays **$386.67** for inventory that settles at **$359.61** — a
  **7.0% shortfall**. The entry/extension rule systematically overpays
  relative to settlement value.
- **Cell C** turns the same $386.67 of purchases into $327.37 of sale cash
  plus $69.33 of settlement = **$396.71**. The gross came from **selling into
  momentum**, not from the inventory being underpriced.

That is the finding, and it inverts the hypothesis' premise. Cell C buys the
favorite every 60 s as it strengthens; that inventory is, on average, dearer
than its settlement value. The trail exit was monetizing the move before
settlement took it back. Deleting it leaves a pure accumulate-the-favorite
process with **negative raw edge before any fee at all** — gross per window is
-$0.9666. No fee reduction can rescue a negative gross, so no execution
improvement (maker entries, cheaper exits, wider trails) addresses this.

**Scaling cannot reach $5 net/traded window.** It is not a capital question:
the per-dollar expectancy is negative, so the defensible ~$100 envelope scales
a loss. Reporting the scaling anyway, as required: at $100 the point estimate
is roughly -$8 to -$12 per traded window.

## What this does and does not establish

**Does:** removing churn is insufficient. The deficit is not execution
friction; it is the absence of an entry/extension edge. Any further work on
this architecture needs a stronger entry or extension signal, not cheaper or
less frequent trading.

**Does not:** this is DEVELOPMENT only, and it is not statistically decisive
in isolation. The 95% CI for HOLD spans zero ([-$2.92, +$0.45]) and LB3 is
-$0.1011, i.e. dropping the three worst windows brings it near breakeven.
Settlement is one binary draw per window, so there are ~28 effective
observations and the mean is tail-driven. **This is a reason to distrust the
magnitude, not a licence to drop windows** — no window was excluded, and none
will be.

The structural fact is more robust than the mean: purchase cash exceeding
settlement cash by 7.0% is a statement about where the inventory was bought,
and it points the same way as the point estimate.

Per the preregistered decision rule, nothing is tuned on this sample, and
nothing will be tuned on OOS. HOLD_NO_REENTRY_V1 and frozen Cell C both run
unchanged on the OOS tape when it is retrieved.

## Caveat carried forward

The taker coefficient's effective date is **unresolved**: the code applies
0.0695 from 2026-09-17T04:00Z (CFTC notice) while current PMUS docs apparently
say Oct 1. Development windows fall on 09-30 and 10-02, so **every window here
is in the disputed range**. Both fee columns are reported, but both use the
Sep 17 date. If Oct 1 is authoritative, the 09-30 windows are over-charged.
This does not change the sign of either result — HOLD's gross is negative
before any fee.

No live strategy orders arise from this experiment.
