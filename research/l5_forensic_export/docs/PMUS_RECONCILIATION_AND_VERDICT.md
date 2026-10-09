# PMUS reconciliation, attribution audit, and the single execution change

Scripts: `../scripts/E1_attribution_audit.py`,
`../scripts/E2_inventory_control.py`.
Results: `E1_ATTRIBUTION_AUDIT.json`, `E2_INVENTORY_CONTROL.json`.

**Kalshi probe stays withdrawn. Nothing armed. No new service, no new
strategy workstream, no live orders. The 40 negative Kalshi configurations
stand as a NO-GO and were not rerun.**

One earlier result of mine **reverses** below (item 6).

---

## 1. Audit of the +$6,077 / −$5,628 / +$2,413 decomposition

**The suspicion I tested:** FIFO pairing consumes the *earliest* lots, so
the unpaired residual is composed of the *latest* lots — and taker fills
are late by construction (median 144 s to settlement vs 441 s for maker).
FIFO might therefore assign residual to the taker mode mechanically. The
symptom was visible: taker residual (143,582 contracts) exceeded maker
residual (75,656) despite four times fewer taker fills.

Same fills, three attribution rules:

| rule | TAKER residual P&L | TAKER win rate | MAKER residual P&L | MAKER win rate |
|---|---|---|---|---|
| FIFO (earliest paired first) | **+$2,426** | 0.501 | **−$5,628** | 0.124 |
| LIFO (latest paired first) | +$2,172 | 0.501 | −$8,684 | 0.125 |
| PRO_RATA (order-free, neutral) | **+$520** | 0.512 | **−$16,963** | 0.134 |

**Ruling: ROBUST on sign, NOT on magnitude.** Both signs hold under all
three rules, so the qualitative finding survives. But the magnitudes move
3×–6×, so the point estimates must not be quoted alone:

* **maker residual loses between $5,628 and $16,963** depending on rule
* **taker residual is between +$520 and +$2,426** — i.e. roughly
  break-even, and under the neutral pro-rata rule it is nearly nil

**The robust core is the win rates, which barely move: the passive residual
wins 12.4–13.4% of the time; the aggressive residual is a coin flip at
50.1–51.2%.** That is the finding worth carrying.

**Reconciliation to cashflow:** the identity
`window P&L == locked matched profit + residual settlement P&L` holds
**exactly** under all three rules — maximum absolute error **0.0** across
every window. The components do reconcile.

**Residuals contributed by both modes** are handled as follows, and this is
the question that makes the rule choice matter: when both maker and taker
fills sit on the residual side, FIFO assigns the residual to whoever bought
*last*, LIFO to whoever bought *first*, and pro-rata splits it by each
mode's share of quantity bought on that side, at that mode's own VWAP.
Pro-rata is the only order-free rule and is the one I would quote.

**Boundaries and fees.** Scope is the 72,550 labelled fills in the
takerOnly dense window (2026-08-28..31). COVERAGE_UNKNOWN fills are
replayed as real inventory but are not a mode — they carry 15,646–16,892
residual contracts and −$858 to −$3,056, left unattributed. **All figures
are GROSS:** Polymarket per-fill fees are absent from the corpus, and
account-level rebates/rewards carry an empty slug so they cannot enter a
per-mode decomposition at all.

## 2. The sequential inventory path

Over 12,977 windows that form matched inventory:

| | value |
|---|---|
| median point in window at **matched peak** | **0.823** of the window |
| median point when **residual first exceeds matched** | **0.266** of the window |
| windows where residual exceeds matched | 11,435 |

**Residual dominance begins early (27% through the window); matched
completion arrives late (82%).** Immediately after residual first exceeds
matched, L5:

* buys the **complement** — 52.5%
* **adds to the residual side** — 37.7%
* **stops** — 9.8%

So it self-corrects about half the time and deepens the losing side more
than a third of the time. There is no closing rule.

## 3–4. PMUS P6/P3 — the comparison cannot be made, and here is why

`P3` and `P6` are pre-registered section labels from
`analysis/l5/cross_shadow/PREREGISTRATION.md`: **P3 = economic
decomposition, P6 = L5 fidelity.** (The labels are reused with different
meanings in `residual_cap/`, `P24_PMUS_BTC15M.md` and elsewhere; I pinned
these to the cross-shadow spec because that is the one with the economic
decomposition the comparison needs.)

**P3 was never populated.** The cross-shadow result is **zero supported
crosses in all 16 cells** over 260 real production COMPLETE decisions. No
row ever reached the price test. First-stop reasons:

| first stop | n | share |
|---|---|---|
| no authenticated quote age recorded | 146 | 56.2% |
| observed ask depth not recorded | 48 | 18.5% |
| authenticated quote stale (>5 s) | 65 | 25.0% |

**0 of 589 order rows carry depth.** P6 explicitly makes no claim.

So **item 3 has nothing to compare against** — there is no PMUS
matched-to-residual ratio, because the P3 decomposition has zero rows. And
**item 4 cannot be computed**: a full-inventory P6/P3 hypothetical requires
the executability evidence that does not exist, and the CF BRTI settlement
reconstruction cannot supply quote age or depth. Reconstructing settlement
does not make an unexecutable row executable.

**What PMUS *does* say, and it is the reconciliation you asked for.** The
populated residual-cap study (`analysis/l5/residual_cap/RESIDUAL_CAP.md`)
reports, at every cap from 6 to UNBOUNDED:

| class | cap=6 | cap=8 | cap=10 | cap=12 | UNBOUNDED |
|---|---|---|---|---|---|
| **A — later opposite fill created matched inventory** | **0** | **0** | **0** | **0** | **0** |
| B→C — opposite opportunity appeared, no fill, settled directional | 7 | 10 | 14 | 17 | 28 |

**Matched contracts Δ = 0.00 in every arm.** Zero class A at every cap.

**This is a direct contradiction of the L5 mechanism, and it is decisive.**
On Polymarket, matched inventory forms readily and is where the money is
(677,011 pairs, +$6,077 locked in four days). On PMUS, matched inventory
**never forms at all** — 30 extra extends produced zero matched contracts.
The component that earns on L5's venue is exactly the component PMUS cannot
produce; the component that loses (unmatched passive residual) is exactly
what PMUS produces exclusively.

## 5. Inventory-control improvements, no lookahead

**Exit-side controls are UNTESTABLE here and were not simulated.** Selling
the residual, or cancel-and-replace, needs a book to know whether the exit
fills, at what price, and whether it fills at all. The corpus has a book
for 4 days and 65 of 17,046 markets. Simulating an exit on the other 99.6%
would mean inventing fills, including the unfilled-exit-order case you
specifically asked be accounted for. Only **entry-side** controls below.

Train = Mar–Jun, out-of-sample test = Jul–Aug. Full inventory carried,
every window settled, each policy may only *refuse* a buy.

| policy | OOS mean | OOS CI95 | OOS total | Δ vs baseline | verdict |
|---|---|---|---|---|---|
| BASELINE | 2.978 | [2.001, 3.937] | $43,047 | — | — |
| **ONE_SIDED_STOP_200** | **3.386** | [2.635, 4.083] | **$48,948** | **+0.408** [−0.115, 0.916] | INDETERMINATE |
| RESID_CAP_200 | 3.379 | [2.679, 4.099] | $48,840 | +0.401 [−0.151, 0.886] | INDETERMINATE |
| ONE_SIDED_STOP_50 | 3.070 | [2.686, 3.492] | $44,382 | +0.092 | INDETERMINATE |
| RESID_CAP_50 | 2.966 | [2.597, 3.351] | $42,881 | −0.012 | INDETERMINATE |
| TIME_STOP_60 | 2.633 | [1.733, 3.514] | $38,063 | −0.345 | INDETERMINATE |
| TIME_STOP_120 | 2.268 | [1.436, 3.114] | $32,790 | −0.710 [−1.323, −0.157] | **HURTS** |
| ENTRY_BAND_25_75 | 1.850 | [0.989, 2.776] | $26,738 | −1.128 [−1.860, −0.422] | **HURTS** |

**At zero fees nothing significantly improves.** Two policies significantly
hurt — and one of them, the 0.25–0.75 entry band, is close to the deployed
`L5_BAND`, which is further evidence against it.

**But fees change the ranking decisively**, because caps cut contract
volume while keeping most of the P&L:

| policy | OOS total @ $0.000 | @ $0.001/contract | @ $0.002/contract |
|---|---|---|---|
| BASELINE | $43,047 | $30,925 | **$18,803** |
| **ONE_SIDED_STOP_50** | $44,382 | $35,789 | **$27,195** |
| ONE_SIDED_STOP_200 | $48,948 | $37,617 | $26,287 |
| RESID_CAP_200 | $48,840 | $37,513 | $26,186 |
| RESID_CAP_50 | $42,881 | $34,352 | $25,823 |

At $0.002/contract the one-sided stop at 50 beats baseline by **+$8,392
(+45%)**, and the tighter cap overtakes the looser one as fees rise.

## 6. Two-mode comparison at matched volume — **the earlier result reverses**

Each single-mode arm grown by repeating its *own* fills in order until its
contract volume equals the BOTH arm's:

| arm | mean P&L | CI95 | total | mean spend |
|---|---|---|---|---|
| BOTH | 4.161 | [3.195, 5.151] | $70,917 | $443 |
| **EARLY_PASSIVE (volume-matched)** | **4.677** | [3.063, 6.273] | **$79,697** | $430 |
| LATE_AGGRESSIVE (volume-matched) | 2.878 | [1.288, 4.400] | $49,042 | $409 |

**Verdict: BOTH does not beat both single modes once volume is matched.**
Early-passive alone is *higher* on both mean and total at comparable
exposure. The CIs overlap, so this is not significant either way — but the
direction reverses, which means **the earlier "BOTH beats each mode" result
was the fill-count artefact, exactly as suspected.**

**Retracted:** that the two-mode combination outperforms either mode alone.
It does not, on this test. The two-mode *structure* remains well evidenced
as a description of what L5 does; its *superiority* does not.

Caveat: volume-matching by repeating a mode's own fills reuses its prices,
which is itself artificial. It is the conservative direction for the claim
being tested, but it is not a clean experiment.

---

# The single most promising evidence-backed execution change

**A one-sided residual stop: once unpaired inventory on a side exceeds the
cap, stop buying that side; the complement stays allowed.**

**Why this one.** It is the only change that targets the mechanism the audit
survives: the passive residual wins **12.4–13.4%** of the time and is the
entire drag, while the matched book is the only reliably profitable
component. It is entry-side, so it is faithfully simulable without a book.
And it is the only policy family that is **fee-robust** — it improves as
fees rise, whereas the baseline degrades fastest.

**Full-inventory out-of-sample P&L** (train Mar–Jun, test Jul–Aug, 14,459
test windows, full inventory, every window settled):

| | ONE_SIDED_STOP_200 | ONE_SIDED_STOP_50 | BASELINE |
|---|---|---|---|
| OOS mean per window | **3.386** | 3.070 | 2.978 |
| OOS CI95 | [2.635, 4.083] | [2.686, 3.492] | [2.001, 3.937] |
| **OOS total, gross** | **$48,948** | $44,382 | $43,047 |
| OOS total @ $0.002/contract | $26,287 | **$27,195** | $18,803 |
| Δ vs baseline, gross | +0.408 [−0.115, 0.916] | +0.092 | — |

**Stated honestly: the gross out-of-sample improvement is INDETERMINATE —
its confidence interval spans zero.** The case rests on the fee-sensitivity
column and on the mechanism, not on a significant gross delta. It also
narrows the CI substantially versus baseline ([2.64, 4.08] vs [2.00, 3.94]),
i.e. it reduces variance even where it does not raise the mean.

**The exact remaining blocker to a bounded live test.** Not egress, and not
instrumentation — those are real but secondary. It is this:

> **No accessible venue has been shown to form matched inventory at all.**
> PMUS produced **zero** matched contracts from 30 extra extends at every
> cap tested. Kalshi was negative in all 40 configurations. A residual stop
> is a policy for managing the *unprofitable* half of a mechanism whose
> *profitable* half — matched-pair formation — has never been demonstrated
> anywhere we can trade.

Testing a residual cap live would measure how well we avoid losses in a
strategy with no demonstrated source of gains. **The prerequisite is a
read-only demonstration that matched pairs can form on an accessible venue
at all** — which on PMUS means recording the two depth fields the recorder
already computes and already prints but does not persist
(`pmus_ws_evidence_recorder.py:366`). That is a one-line read-only change
to what gets written down, it unblocks the P3 decomposition that currently
has zero rows, and it is strictly cheaper than any probe.

Secondary blockers, unchanged: Kalshi and Polymarket hosts remain denied by
the environment's network policy (CONNECT 403, retested), which keeps
authoritative settlement and our own maker/taker-flagged fills out of reach.
