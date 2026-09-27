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

---

# P0: maker-fill realism (three explicit bounds)

The queue-ahead defect flagged above is fixed. `ahead` is no longer
whole-ladder `bid_shares`/`ask_shares`; it comes from the displayed quantity
**at our exact price level**, taken from the ladder in venue terms (a UP clip
rests a BID at the bid, so it queues behind `bids[bid]`; a DOWN clip rests a
SELL of YES at the ask, so it queues behind `offers[ask]`).

ECONOMIC_COMPLETE is byte-identical under all three treatments: θ, the q grid,
the continuation variants and the completion gate are untouched. Only the
acquisition fill inference differs.

**Shared causal precondition, never relaxed in any treatment:** observed trade
volume in the interval AND a print at or through our resting price in our own
terms. A price merely touching our level is never a fill.

- **OPTIMISTIC** — no queue; our clip is at the front. The pre-existing rule,
  retained unchanged so the audited result stays comparable.
- **QUEUE-AWARE** — `ahead` initialises from the displayed size at our exact
  level when we join, and decrements only by volume attributable to our side and
  price (the print test supplies the attribution; aggregate `sharesTraded`
  alone never consumes queue). Re-quoting to a new level sends us to the back of
  that level's queue. If our price is not displayed, queue position is UNKNOWN
  and there is no fill — never an empty queue.
- **PESSIMISTIC** — the pre-existing conservative bound (whole displayed book
  clears first). Retained as a floor, though it is mis-specified as a queue
  measure.

The independent cashflow harness reconciles **90/90** configurations across all
three models. Suites still 29/29.

## Results (θ=0.96, variant B, per window, n=2)

| fill model | q | acq fills | completed con | matched net | residual P&L | TOTAL net |
|---|---|---|---|---|---|---|
| optimistic | 25 | 21.0 | 175.0 | 8.8050 | 0.0000 | **+6.5201** |
| optimistic | 50 | 21.0 | 350.0 | 17.3057 | 0.0000 | **+12.7336** |
| queue_aware | 25 | 8.5 | 62.5 | 4.0000 | −10.1250 | **−6.9442** |
| queue_aware | 50 | 8.5 | 125.0 | 7.9300 | −20.2500 | **−13.9588** |
| pessimistic | 25 | 1.0 | 0.0 | 0.0000 | −1.3750 | **−1.3750** |
| pessimistic | 50 | 1.0 | 0.0 | 0.0000 | −2.7500 | **−2.7500** |

Full q grid in the session record; the sign is constant in q within each model,
and the queue-aware loss scales roughly linearly with q.

## Correction to an earlier claim

"EC carries zero residual, so it is not directionally exposed at settlement" was
an artifact of the optimistic fill model, **not** a property of EC. Under
queue-aware fills, 1830z ends **50 contracts net DOWN** (125 UP acquired, 75 UP
retired by completions, 100 DOWN acquired) and, because the window settled UP,
that residual is a total loss of **−$20.25**.

EC retires only the *held* leg, so it reduces |residual| at the moment it fires
but cannot stop later complement acquisitions from pushing the book net the
other way. **EC does not guarantee a flat book.**

## Break-even maker-fill rate

From the audited optimistic result, thinning each modelled passive acquisition
to probability f (400 seeds, mean net/window):

| f | q=25 | q=50 |
|---|---|---|
| 1.00 | 6.5201 | 12.7336 |
| 0.98 | 5.7776 | 11.3377 |
| 0.96 | 5.0928 | 10.0362 |
| 0.95 | 4.8631 | 9.6207 |
| 0.85 | — | 6.4377 |
| 0.80 | — | 5.4623 |
| 0.78 | — | 5.1678 |
| 0.76 | — | 4.8272 |

**q=25 needs f ≈ 0.96; q=50 needs f ≈ 0.77** to hold $5/window on the mean.

Dispersion matters more than the mean: at f=0.96 only **54.8%** of seeds clear
$5 at q=25 (87.8% at q=50). The $5 threshold is not robustly met once any fill
uncertainty is admitted. Below f≈0.7 the curve is non-monotone — fewer fills
means less residual, and with both windows settling UP thinning sometimes helps
— so the break-even figure is only meaningful in the high-f region.

Since the causally defensible queue-aware model produces roughly **8.5 fills
against 21 modelled (f ≈ 0.40)**, the required rate is far above the supported
one at both clip sizes.

## Ladder-freshness sensitivity (analysis only, both default OFF)

Collector cadence is `HEARTBEAT_S = 30.0` with 2s polling, so the justified
bound is **32s** (one heartbeat plus one poll). Observed max staleness was 31.2s,
so a 32s bound is **non-binding** — it changes nothing. Nothing is interpolated.

`respect_live_bbo` drops ladder levels priced better than the contemporaneous
BBO touch, so a stale favourable ladder cannot be used when a newer BBO
contradicts it:

| maxAge | respect_live_bbo | q=25 net | q=50 net |
|---|---|---|---|
| none | False | 6.5201 | 12.7336 |
| 32s | False | 6.5201 | 12.7336 |
| 32s | **True** | **5.1818** | **10.1489** |
| 16s | False | 10.6010 | 20.8955 |
| 8s | False | 6.3142 | 12.6283 |

Applying the hygiene costs **$1.34/window at q=25** and **$2.58 at q=50**. Under
queue-aware it stays negative throughout (32s + respect_live_bbo: q=25 −7.8599,
q=50 −15.8222).

The 16s row scores *higher*, which is path noise at n=2, not evidence that
fresher is better. Selecting 16s because it pays more is exactly the error the
freshness bound exists to prevent. The preregistration-justified bound is 32s.

Status unchanged: **AUDITED_PRELIMINARY**, n=2, both windows settled UP.
Checkpoint 25 remains the preregistered economic checkpoint.

---

# P0: is the frozen L5/B' policy represented correctly around completion?

## The hypothesis under test is FALSIFIED

Premise: "acquisitions after completion rebuild/flip residual inventory" via
stale passive orders left working on the opposite side. Audited at every
queue-aware completion in 1830z and 1845z:

| | |
|---|---|
| Outstanding passive orders immediately after each completion | **none, at every completion** |
| Residual attributable to PRE-existing orders | **0 contracts, $0** |
| Residual attributable to NEW post-completion decisions | **50 contracts at q=25 (100%)** |

**Our simulator does not leave stale passive orders working.** There is no
persistent order object at all: the queue position is created and consumed
inside the evidence-bearing states, because the causal precondition (volume plus
a print through our price) gates the *joining* of the queue as well as the fill.
So answer to "is the simulator treating a completed pair as merely reducing one
side while leaving stale orders on the other?" is **no** — it reduces the held
side and holds no orders at all.

Consequently **ATOMIC_RECYCLE is an exact no-op** on this tape:

| treatment | q | matched net | residual P&L | total net | completed con | cancellations | resid PRE | resid NEW |
|---|---|---|---|---|---|---|---|---|
| queue-aware base | 25 | +4.0000 | −10.1250 | −6.9442 | 62.5 | 0 | 0 | 50 |
| **ATOMIC_RECYCLE** | 25 | +4.0000 | −10.1250 | **−6.9442** | 62.5 | **0** | 0 | 50 |
| queue-aware base | 50 | +7.9300 | −20.2500 | −13.9588 | 125.0 | 0 | 0 | 100 |
| **ATOMIC_RECYCLE** | 50 | +7.9300 | −20.2500 | **−13.9588** | 125.0 | **0** | 0 | 100 |

There are no stale orders to cancel, so cancelling them changes nothing. This is
a state-consistency *negative*: the state was already consistent.

## What the canonical L5 corpus says (measured, not invented)

From `src/l5_complement_response_curve.py` — the Complement Response Curve over
**71,354 inventory-changing fills with a defined next action**, canonical L5
sequence, systematic regime onward:

| move of the side we hold | n | SAME | OPP | median delay |
|---|---|---|---|---|
| −5..−2c | 6,503 | 66.7% | 33.3% | 4.0s |
| 0..+1c | 21,129 | 51.3% | 48.7% | **0.0s** |
| +2..+5c | 6,736 | 32.3% | **67.7%** | 4.0s |
| +5..+10c | 3,087 | 36.5% | **63.5%** | 10.0s |
| ≥ +10c | 2,503 | 43.3% | 56.7% | 18.0s |

Answering the five questions:

1. **Does L5 immediately resume symmetric acquisition?** He resumes essentially
   immediately (median delay 0.0–4.0s), but *not* symmetrically. Side choice is
   conditioned on how the held side moved; it is near 50/50 only in the no-move
   bucket.
2. **Does he preferentially acquire the side just depleted?** **No — the
   opposite.** A completion fires precisely because the held side *rose*, and
   after a rise of +2..+10c L5 takes the **complement 63.5–67.7%** of the time.
   Our simulator buying DOWN after retiring risen UP is therefore **L5-faithful**.
3. **Does he stop extending the opposite side until inventory rebalances?**
   **No evidence for any such gate.** Conditioned on imbalance ratio the response
   is non-monotone (0–25% same-side 49.6%, 75–95% 42.0%, 95–100% 61.0%), and OOS
   the imbalance feature *hurts*: adding B to A+C scores **−0.28%**.
4. **Does completion/reset change subsequent side choice?** The driver is price
   movement, not the completion event. OOS logloss gains: movement-only **+11.63%**,
   pair-cost-only +1.62%, imbalance-only +0.32%; adding movement to the others
   **+10.79%**. The module states outright that this *contradicts* our frame:
   "Our shell treats COMPLETE as an inventory/cap event … L5's own history says
   the complement decision is a PRICE-MOVEMENT response and barely an inventory
   response at all."
5. **Stale opposite-side orders?** Answered above: none exist in our simulator.

## The residual loss is L5-expected, not a representation defect

L5's own price-band decomposition (same module) gives residual P&L per unit:

| band | residual units | resid $/unit |
|---|---|---|
| 10–25c | 55,973 | −0.1584 |
| **25–45c** | **59,603** | **−0.2487** |
| 55–75c | 38,951 | +0.0508 |
| 75–90c | 36,515 | +0.1382 |

"Cheap residuals lose heavily and dear residuals win … his unpaired leg is
systematically the side the market had already decided — adverse selection on
what fails to pair."

Our 1830z queue-aware residual was **50 DOWN acquired at ~0.36–0.37 — squarely
the 25–45c band — losing −$0.405/unit** against L5's measured −$0.2487/unit in
that same band. Same sign, same order of magnitude, ours from a single residual
episode against L5's 59,603 units. The corpus also finds maker fills are
adversely selected (after a maker fill the next print favours him only 43.2% of
the time, median +0.0000).

So the mechanism is: the frozen policy correctly leans to the complement after a
favourable move (L5-faithful), that complement is cheap, and cheap unpaired legs
lose — in L5's history and in ours. **This is a known measured property of the
strategy family, not a simulator bug and not something ATOMIC_RECYCLE addresses.**

## Verdict on ATOMIC_RECYCLE

**PMUS adaptation. Not L5-corresponding, and not verifiable against L5.** The
corpus is a *fill* feed; the module is explicit that order-level semantics cannot
be settled by it: "WHAT WOULD ACTUALLY BE NEEDED to test pre-positioning:
order-level data — placement and cancellation timestamps, order identifiers, and
book snapshots at those timestamps. None of it is derivable from a fill feed."

Independently, the inventory-rebalancing *motivation* for ATOMIC_RECYCLE is
contradicted by the corpus, where inventory imbalance is the weakest signal and
actively harmful once movement and pair cost are present. It would require OOS
validation before any status beyond diagnostic — and on this tape it has no
effect to validate.

## Settlement diversity (the two-UP problem)

Authoritative settlements retrieved for every completed window:

| window | state | settlementPx | outcome |
|---|---|---|---|
| 1830z | EXPIRED | 1.0000 | UP |
| 1845z | EXPIRED | 1.0000 | UP |
| **1900z** | EXPIRED | **0.0000** | **DOWN** |
| **1915z** | EXPIRED | **0.0000** | **DOWN** |
| 1930z | EXPIRED | 1.0000 | UP |

3 UP / 2 DOWN. Tape scrape and scoring for 1900z/1915z/1930z is **outstanding**.
Because the 1830z residual was DOWN and lost only because UP won, a DOWN-settling
window should flip the residual P&L sign — that is the decisive test of whether
the queue-aware loss is structural or directional, and it is not yet run.
