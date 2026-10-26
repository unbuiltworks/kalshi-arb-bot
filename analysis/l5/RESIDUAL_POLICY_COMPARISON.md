# Residual-inventory policy comparison (Oct 24-26 venue-confirmed records)

Evidence: 9 settled windows with venue settlement (`VENUE_SETTLEMENT_PX`),
7 from the Oct 24 preserved tapes and 2 from Oct 26, plus the Oct 25 settled
records where cited. Scripts: `analysis/l5/residual_rescue_eval.py`,
`analysis/l5/first_leg_feasibility.py`. Nothing here is deployed.

## Accounting note that changes the reading

The engine's `matched_cost` / `residual_cost` split is **pro-rata over all
contracts of a side**, not the marginal fills. On 2115z that charges the
stranded 1.68 DOWN at 0.4016/contract though it actually filled at own 0.17,
and flatters the pair to 0.7716 though it actually cost 0.95.

Marginal (actual-fill) accounting reconciles **exactly** to the venue's
`settlement_pnl` on both Oct 26 windows, so it is used throughout:

    2100z  pair 1.72 @ (0.61 + 0.21) = 0.82  -> +0.3096
           strand 1.77 DOWN @ own 0.26       -> -0.4602   net -0.1506 ✓
    2115z  pair 2.18 @ (0.37 + 0.58) = 0.95  -> +0.1090
           strand 1.68 DOWN @ own 0.17       -> -0.2856   net -0.1766 ✓

## The record, split as required

| window | pair q | pair | pair P&L | resid q | resid own | resid P&L | net | post-pair? |
|--------|-------:|-----:|---------:|--------:|----------:|----------:|----:|:----------:|
| 1024/0100z | 1.04 | 0.96 | +0.0416 | — | — | 0.0000 | +0.0416 | — |
| 1024/0115z | — | — | 0.0000 | 0.96 | 0.24 | -0.2304 | -0.2304 | — |
| 1024/0130z | 1.27 | 0.85 | +0.1905 | — | — | 0.0000 | +0.1905 | — |
| 1024/0145z | — | — | 0.0000 | 1.03 | 0.35 | -0.3605 | -0.3605 | — |
| 1024/0200z | 1.22 | 0.94 | +0.0732 | — | — | 0.0000 | +0.0732 | — |
| 1024/0215z | — | — | 0.0000 | 1.25 | 0.31 | +0.8625 | +0.8625 | — |
| 1024/0230z | 1.16 | 0.93 | +0.0812 | — | — | 0.0000 | +0.0812 | — |
| 1026/2100z | 1.72 | 0.82 | +0.3096 | 1.77 | 0.26 | -0.4602 | -0.1506 | **YES** |
| 1026/2115z | 2.18 | 0.95 | +0.1090 | 1.68 | 0.17 | -0.2856 | -0.1766 | **YES** |

- completed-pair profit, all 6 pairs: **+0.8051**, every one positive
- residual P&L, all 5 residuals: **-0.4742**
- total realized net (policy A, actual): **+0.3309**

## Action 1 - PROFITABLE COMPLETION

Already happening, and **not gate-limited**. After 2115z stranded, the engine
kept trying to complete for the rest of the window: 15 decisions keyed
`TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP`. Those are **economic** refusals
(the complement cost more than the 0.96 cap allows), not
`PAST_COMPLETE_CUTOFF` or runway refusals.

**Answer to "do cutoffs or runway gates block legitimate late completion":
no.** On these records the blocker was price, not time. The one case where a
cutoff mattered was 0215z, whose cheapest complement appeared at T+884s,
past the T+720 completion cutoff — and completing there would have been
*worse* than holding (see Action 2). No gate needs loosening.

Completion timing when it did work: 21.1, 33.6, 56.8, 124.7, 125.5 s
(mean 72.3 s, n=5 measurable).

## Action 2 - RESIDUAL RESCUE: rejected on the evidence

For every stranded leg, all book observations after the stranding fill and
inside the T+720 cutoff were priced four ways. ORACLE columns take the best
instant **in hindsight** and so bound what any policy could reach.

| window | qty | side | own | hold (actual) | ORACLE maker | ORACLE taker | ORACLE sell |
|--------|----:|------|----:|--------------:|-------------:|-------------:|------------:|
| 1024/0115z | 0.96 | DOWN | 0.240 | -0.2304 | +0.0384 | +0.0156 | +0.0156 |
| 1024/0145z | 1.03 | DOWN | 0.350 | -0.3605 | +0.0206 | -0.0062 | -0.0062 |
| 1024/0215z | 1.25 | UP | 0.310 | **+0.8625** | +0.0500 | +0.0180 | +0.0180 |
| 1026/2100z | 1.77 | DOWN | 0.235 | -0.4166 | -0.3104 | -0.3339 | -0.3339 |
| **TOTAL** | | | | **-0.1450** | **-0.2014** | **-0.3065** | **-0.3065** |

**Rescue is worse than holding even with perfect hindsight.** It improves the
three losing strands and destroys the one winner: 0215z holds for +0.8625 but
any rescue caps it near +0.05, and that single lost upside exceeds all three
gains combined.

The economics say why. A strand was bought at a market price, so holding it
is EV-neutral with large variance; rescuing it pays the spread plus any taker
fee to remove that variance, which is EV-**negative** by exactly that cost.
The realized -0.1450 over 4 strands is noise around zero, not a systematic
leak. **No post-fill action on a strand creates edge.** Taker rescue is also
the worst of the options and would need separate authorization; it is not
recommended and was not enabled.

## Action 3 - NEW EXPOSURE: this is where the money went

Post-pair openings are the only component that is both controllable and
clearly negative on the record.

- second-round openings observed: **3** — 1024/0100z (cancelled unfilled),
  2100z (stranded -0.4602), 2115z (stranded -0.2856)
- **completed second pairs: 0 of 3**
- Oct 24 ran 6 of 7 windows in accumulation round 0 only, so the baseline
  produced **zero** post-pair strands. `round_allocations` does not appear in
  Oct 24 decisions at all; the mechanism post-dates the baseline.

### Why they strand, and why it cannot be screened at placement

**Planned pair cost is a constant.** Every opening leg on record — paired and
stranded alike — was placed at planned `pair_cost` exactly **0.9500**,
headroom 0.0100 against the ceiling. It carries zero predictive information.

What does predict the outcome is feasibility **at the moment of fill**: the
first book observation at/after the fill, asking whether the complement is
still obtainable at `<= 0.96 - first_leg_own`.

| complement at fill | paired | stranded |
|--------------------|-------:|---------:|
| feasible           | **3**  | 0        |
| infeasible         | 1      | **4**    |

Feasible at fill paired 3 of 3; infeasible paired 1 of 5. This is adverse
selection: a resting cheap maker leg is filled precisely when the market
moves through it, so the complement has already run away by the time the
fill lands. It is a post-fill observation, so it cannot gate placement — but
it does say the pair is usually lost at the fill, not later.

## Policy comparison

| policy | definition | total realized net (9 windows) |
|--------|-----------|-------------------------------:|
| A continuous | actual behaviour | **+0.3309** |
| B first-pair-only | no new first leg after a pair completes | **+1.0767** |
| C inventory-priority | no new first leg while unmatched inventory exists | **+1.0767** |

B and C coincide on this record because every post-pair opening occurred
when the book was flat (the pair had just matched), so "after a pair" and
"while unmatched" select the same two legs. They differ only in a case not
yet observed, and that is worth keeping in mind rather than treating them as
one policy.

Difference vs actual: **+0.7458 over 2 windows**, i.e. the post-pair legs
turned +1.0767 into +0.3309. On Oct 24 alone A = B = C = +0.6581, since no
post-pair leg ever filled there.

**This does not establish that first-pair-only is optimal.** It establishes
that the *current* second-round implementation is 0 for 3 and cost -0.7458.
A second pair that completed would add roughly +0.10 to +0.31. The question
is whether a disciplined second round can complete, not whether to stop.

## Capital and liquidity for $5 net per window

Mean net across the 6 completed pairs: **$0.0874 per contract**.

| target | contracts/leg | cash one leg | cash both legs resting |
|--------|--------------:|-------------:|-----------------------:|
| $5 at mean 0.0874 | **57.2** | $57.19 | $114.37 |
| $5 at the 0.96 ceiling (0.04) | **125** | $125.00 | $250.00 |

Against the current hard ceilings — `max_cash_deployed_usd` $6.00,
`MAX_TOTAL_EXPOSURE_USD` $20, opening leg cap 3 contracts — **$5 per window
is 6-21x outside authorization.** At the $20 ceiling the arithmetic ceiling
is roughly **$0.87-$1.75 per window**.

Liquidity is not obviously the binding constraint but is unproven at size:
displayed touch depth on these windows ran 199-2,659 contracts and queue
depths 870-3,872, so 57 contracts is ~2-10% of displayed touch depth.
**Completion probability at that size is unmeasured.** The largest fills ever
observed are 1.72 and 2.18 contracts, both of which completed; two points
above the 1.04-1.27 baseline band rule out an immediate cliff and establish
nothing about 57. Capital was also *not* the binding constraint on Oct 26:
zero `CASH_COMMITMENT` refusals, against 188 of them on Oct 24 under the old
$1.65 envelope.

## Implementability

- **Exact-order cancellation** is demonstrated, per order: 2 individual
  cancels on 2100z (`DBBFS9YG11MM`, `DBBG9B8G51MM`), 5 on Oct 24.
- **Claim/inventory state already exists** at the decision point.
  `first_leg_entry_refusal` is called at `src/l5_live_execution.py:5851`
  and is already handed `legs_claimed` plus the real positions
  `self.store.position(market, UP/DOWN)`.
- **Reconciliation is unaffected.** The candidate change is a pure predicate
  inside a read-only function that already receives its inputs. It adds no
  state, writes no ledger, and touches no settlement path.

### The defect the evidence points at

`src/l5_two_sided_probe.py:444`

    if up != 0 or dn != 0:
        return None                     # managing inventory already held

The exemption is meant for inventory that needs managing. But after a pair
completes, `up` and `dn` are both non-zero **and equal** — there is no
imbalance to manage — and the floor is waived anyway. That is how 2115z
opened a DOWN leg at own **0.17**, below the live 0.20 floor, which then
stranded for -0.2856.

Keying the exemption on the unmatched imbalance `abs(up - dn)` rather than
total holdings restores the floor for post-pair openings while still
permitting genuine completion and inventory management.

Scope check against the record: it refuses exactly one observed leg (2115z's
0.17, which stranded) and **zero** legs that completed a pair — the paired
opening legs were at own 0.28, 0.29, 0.35, 0.37, 0.42. It does not touch
2100z's 0.26 leg, so it recovers +0.2856 of the +0.7458 gap, not all of it.

## Recommendation for the next bounded experiment

**Change one predicate: key the first-leg floor's inventory exemption on the
unmatched imbalance instead of total holdings. Keep `round_allocations=2` so
continuous sequential pairing stays enabled. Run the authorized 8 windows and
measure second-round completion rate specifically.**

Why this and not the alternatives:

- It preserves continuous pairing with later-window opportunities, which
  reverting `round_allocations` to 1 would not.
- It is one line in a read-only predicate that already has its inputs, so it
  is testable as a unit and cannot disturb reconciliation.
- It needs no capital increase, no new service, no taker execution, no gate
  loosening, and no change to the 0.20 floor, the 0.96 ceiling or any
  exposure or loss limit.
- It is the only change with direct venue-confirmed evidence of a leg it
  would have refused and no evidence of a pair it would have blocked.

Deliberately **not** recommended: residual rescue in any form (worse than
holding even with hindsight), taker completion (worst of the four, needs
separate authorization), raising the first-leg floor toward the frozen 0.45
(it would refuse all 6 legs that completed pairs, since those filled at own
0.21-0.42), and any size increase toward $5/window (6-21x outside the
authorized $20 ceiling, with completion probability at size unmeasured).

The honest limit on all of this: **9 settled windows, 6 pairs, 5 residuals,
3 second-round openings.** The strand economics are consistent with noise
around zero, and the 0 for 3 second-round record is the weakest link in the
argument. The recommended change is cheap and reversible precisely because
the evidence does not yet support an expensive one.
