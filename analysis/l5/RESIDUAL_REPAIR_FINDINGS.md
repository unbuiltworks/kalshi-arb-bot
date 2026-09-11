# Residual repair: findings

Run against `analysis/l5/RESIDUAL_REPAIR_PREREGISTRATION.md`, committed at
`dd2e2637` before the reconstructor existed. 17 residual markets, **359
post-imbalance decision states**, no settlement field read — enforced by a
test that walks the module's AST for field reads rather than for words.

**Headline: the repair hypothesis, as a decision rule, is falsified. Two of
the three pre-registered falsifiers fired. What replaced it is better
supported and more actionable.**

---

## 1. We never missed an available repair. Not once.

```
states where a floor-improving COMPLETE was available inside the pair
ceiling and we did not complete:   0   of 359
```

56 states offered a compliant, floor-improving COMPLETE. The engine acted
`complete` at **56 of 56**. Pre-registered falsifier §8-1 fires: *"residual
losses are set at entry, not by post-imbalance management."*

## 2. The floor metric I pre-registered cannot discriminate, and that is
arithmetic, not data

Completing `q` units at price `p` costs `pq` and lifts `min(up,dn)` by `q`,
so the floor always moves by `q(1−p)` — positive for every `p < 1.00`. All
**220/220** priced completes "improve the floor". I registered this in §5 as
though it would separate cases; it cannot. Recorded as a deviation. The
binding constraint is the ceiling, and the tests now pin the arithmetic so
nobody re-derives the same dead metric.

## 3. Three completes in four were blocked by the pair ceiling

Of 220 states with a priced complete:

| | n |
|---|---:|
| improves the floor | 220 |
| **within the pair ceiling** | **56** |
| ceiling-blocked | **164 (75%)** |

Pair cost the complete would have left, on the blocked 164:

```
min 1.0001   p25 1.0436   median 1.0747   p75 1.1477   max 1.3005
within 1.05: 67     within 1.10: 94
```

Pre-registered falsifier §8-2 fires, for three quarters of states: by the
time we were imbalanced, flattening cost more than \$1 per pair. **The loss
was locked at entry.**

## 4. When we did try, we mostly did not get filled

```
all COMPLETE actions in residual markets:  209   filled 22   (10.5%)
  of those, ceiling-compliant states:       56   filled 13   (23.2%)
EXTEND at reparative states:                62   filled 22   (35.5%)
```

Completes fill at **a third the rate of extends**. This is a maker-execution
problem, not a decision problem.

## 5. L5 does demonstrate the behaviour we are missing — §8-3 is falsified

At these post-imbalance states, L5's own empirical distribution (state_n ≥ 20
on 314 of 359 states, so this is not a thin-bucket artifact):

```
median persist_extend_p   0.673
median switch_complete_p  0.327
```

**L5 persists with EXTEND about twice as often as it switches to COMPLETE.**
The frozen model recommended `extend` at 211 of 359 states.

### The faithfulness test

Selection is **stochastic** on all 347 decisions carrying a mode — the engine
draws from L5's distribution rather than taking the mode, so `acted ≠
v2_recommended` is by design and is *not* evidence of drift. The correct test
is whether the draw is faithful in aggregate:

```
expected COMPLETEs under faithful execution (Σ switch_complete_p):  150.0
observed COMPLETEs:                                                 209
                                                              z = +6.8

excluding every state with a cap binding (293 states):
expected 121.8    observed 144                                z = +2.8
```

We complete **39% more often than L5 would**. Roughly two-thirds of the
excess is attributable to safety caps converting a drawn EXTEND into a
COMPLETE — 43 identified conversions, `spend_cap` 34 and `entry_band_left` 9,
each logged as `divergence_class: "safety-cap override"` with
`would_act_without_safety_cap: extend`. A residual ~2.8σ excess is **not**
cap-explained and is unaccounted for.

**Caveat, stated rather than buried:** those z-scores treat 359 states as
independent. Decisions within a session are strongly correlated, so the
effective n is far closer to 17 markets than 359 states and the nominal
significance overstates the evidence. The raw effect — 209 against an
expected 150 — is what carries weight, not the sigma.

## 6. What this means

We are systematically choosing the action that

- L5 takes about half as often at the same states,
- fills at 10.5% rather than 35.5%,
- and is blocked by the pair ceiling three times in four,

and we are doing it partly because a **spend cap** converts a drawn EXTEND
into a COMPLETE at exactly the states where L5 would have persisted.

That is a far better-supported lever than a new residual decision rule, and
it is the opposite of what "minimize abs(residual)" would prescribe.

## 7. What is NOT claimed

- **No rule is proposed and no promotion is sought.** The pre-registered bar
  (§7) requires a holdout result; nothing here was fit on a split because
  nothing was fit at all. This is descriptive.
- The 2.8σ unexplained excess is **not** yet attributed. It may be further
  safety adaptations not labelled as such, a draw-seeding issue, or state
  bucketing. It needs its own investigation.
- Nothing says holding a residual is good. The two positive-residual markets
  (+\$5.1884, +\$1.4610) remain unexplained by skill, and `CLAUDE.md`'s
  standing constraint applies: do not assume residual exposure has
  predictive skill.
- **No live change follows from this document.** R5F configuration is
  unchanged, as instructed.

## 8. The two questions this leaves, in priority order

1. **Why does a spend cap convert EXTEND to COMPLETE?** Converting "buy more
   of the surplus side" into "buy the deficit side" when budget is tight
   spends *more* cash on the more expensive leg. That looks backwards and it
   is 34 of 43 identified conversions.
2. **Why do maker completes fill at 10.5%?** At that rate the completion leg
   is close to decorative, and it is the leg the whole pair strategy depends
   on. This is the same question as #110/#112 and now has a sharper
   denominator.

Deviations from the pre-registration: §5's floor metric was non-discriminating
(recorded in §2 above). The train/holdout split in §6 was **not used**,
because no rule was fitted; the holdout remains unread and available.

---

# Addendum: the spend_cap EXTEND → COMPLETE conversion

Owner's counterfactual: *"when an EXTEND draw is blocked by `spend_cap`,
should the safe fallback really be COMPLETE, or should it be HOLD? We should
answer that from the pre-registered historical states, not intuition."*

**The evidence does not support switching to HOLD.** That is the opposite of
what I expected, and of what I implied when I called the conversion
"backwards".

## The rule

```python
if relation in ("open", "extend"):
    if cum + (inv.spent() - prior_spent) + spec["entry_notional"] \
            > session_notional_cap():
        relation = "complete" if inv.residual != 0 else "stop"
```

The check sits *inside* the open/extend branch, so the converted COMPLETE
never faces that same arithmetic. It is bounded instead by the completion
budget and the pair ceiling.

## What the 56 conversions actually did

```
spend_cap -> COMPLETE          56
  no_fill_cancelled            45
  filled                       10        17.9%
  unknown                       1
```

**For the 45 that did not fill, COMPLETE and HOLD are economically
identical.** No fill means no inventory change and no floor change. The
conversion costs an action from the six-action session budget and the single
outstanding-order slot — and nothing else.

**For the 10 that filled, every one improved the guaranteed floor**, by
+0.0352 to +9.7032. Nine of the ten were PARTIAL completions (filled quantity
below the residual), so the engine chipped at the residual rather than
flattening it.

So on the pre-registered floor metric, HOLD would have been *identical* in
82% of cases and *worse* in the other 18%.

### One number that must not be misread

The `pair_cost_after` figures above 1.00 in the per-state table price
completing the **whole** residual at that price. Nine of the ten orders were
partial, so those are not ceiling breaches and are not reported as any. I
checked this rather than asserting it, because the same table read carelessly
would have produced a second false alarm about the ceiling in one day.

### And the floor metric is weak evidence, by its own arithmetic

Section 2 of this document establishes that completing `q` at `p` always
improves the floor by `q(1−p)`. So "all ten improved the floor" is guaranteed
arithmetic, not a demonstration of judgement. It is enough to refuse the HOLD
switch — HOLD cannot beat a guaranteed gain — but not enough to call the
conversion good.

## What survives as criticism

1. **An 82% no-op rate** burns the one outstanding-order slot and an action
   from a six-action budget, every time.
2. **It diverges from L5**, whose distribution at these states favours
   persist/extend roughly 2:1.
3. **The price asymmetry is real**: median EXTEND price **0.36**, median
   COMPLETE price **0.62**. When cash is the binding constraint, the rule
   blocks the cheaper action and permits the one about 1.7× dearer.

## The sharper question this reframes

The problem may not be the **fallback**. It may be the **trigger**. If the
constraint is cash, then blocking a 0.36 reparative EXTEND while permitting a
0.62 COMPLETE is the wrong way round — but the remedy is not HOLD, it is
possibly to let the cheaper reparative extend through under a rule that
recognises it as risk-reducing rather than risk-adding.

**That is a hypothesis, not a recommendation.** It changes what the engine
spends money on, so it needs the pre-registered treatment: specified on
TRAIN, read once on the holdout, ≤2 parameters, and shadow evaluation before
any live change. The holdout remains unread.

---

# TRAIN result: the repairing-EXTEND rule is aimed at the wrong target

**STOP. The holdout was not opened and remains sealed.**

Owner's narrow question: *"Can a decision-time 'repairing EXTEND' classifier
materially reduce residual formation below the current baseline without
increasing worst-case loss or destroying paired economics?"*

**No — and the reason is structural, so no amount of parameter fitting or
holdout evidence would change it.**

## The addressable set is small, and then it is empty

TRAIN: 9 markets, 158 post-imbalance states (83 one-sided, 75 two-sided).

```
states where an EXTEND would have run absent a safety cap        25
  (filtered on would_act_without_safety_cap, NOT on the modal
   recommendation -- selection is stochastic and the mode is not
   what would have run; the wrong filter gave 9 and 2 markets)
  spend_cap -> complete                                          16
  safety-cap override -> hold                                     9
  spread across 6 of 9 markets

of those 25:
  one-sided, matched = min(up,dn) = 0                            13
  two-sided, a pair exists to repair                             12   (4 markets)
```

On the **13 one-sided** states "repairing EXTEND" is undefined: there is no
matched pair, so there is no pair cost to improve. An extend there is a pure
directional add and the cap that blocked it was right.

That leaves **12 states in 4 markets**, and they are repeated readings of the
same inventories (`up=14.46 dn=4.11` three times, `up=7.14 dn=16.44` three
times, and so on) — roughly **six distinct decision points in the whole
TRAIN set**.

## The decisive point: a repairing EXTEND makes the residual WORSE

A repairing extend buys more of the **surplus** side. By construction that
*increases* `abs(residual)`. The measured case that motivated the class,
`aec-atp-nikizq-santav-2026-09-08`:

```
after session 1   up 3.78  dn 5      residual -1.22
NO extends 4.31 + 3.90 ->  dn 13.21  residual -9.43   <- imbalance x7.7
then YES completes      ->  13.21/13.21  residual 0
```

The extends pulled `avg_dn` 0.49 → 0.4226 and dug the **pair cost** out from
1.15 to 1.0732. They also took the residual from −1.22 to −9.43. The market
ended paired because the **completes** filled, not because the extends did.

So the mechanism improves **pair cost** and worsens **residual size**. The
owner's target is **residual incidence**. A repairing-EXTEND rule cannot
serve it; it optimises a different quantity in the opposite direction.

## The three requested numbers, honestly

1. **Residual incidence under the counterfactual — not improvable by this
   mechanism**, for the reason above. Two further obstacles, each sufficient
   on its own: the residual cohort is 100% residual *by construction*, so
   incidence is only measurable over the full 52-market settled population;
   and turning a residual market into a paired one requires simulating the
   remaining fill trajectory, which would rest on modelled fills.
2. **Mean residual-market loss — at best improvable on 4 of 9 TRAIN markets
   at ~6 distinct states**, and only where the subsequent completes fill.
   Completes fill at 10.5%.
3. **Paired economics sacrificed or improved — not applicable.** No rule is
   proposed, so nothing is sacrificed and nothing is claimed.

## Where the incidence lever actually is

Not post-imbalance management — §1 of these findings already showed we never
missed an available compliant complete (56 of 56 taken). The two candidates
that remain:

- **Entry.** 75% of post-imbalance completes were already ceiling-blocked;
  the loss was locked when the first leg was taken at a price whose
  complement could never clear $1.00.
- **Completion fill rate.** 10.5% for completes against 35.5% for extends.
  The completion leg is the one the pair strategy depends on.

Both are about *whether a first leg should be taken and whether its
complement can be filled* — not about what to do once stranded.

**The holdout remains unread and available.** It was not spent on a rule the
TRAIN evidence shows to be aimed at the wrong objective.
