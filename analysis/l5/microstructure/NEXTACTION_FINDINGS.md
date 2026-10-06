# Can the BTC5m representation *predict* L5's next passive action?

**Run strictly to `PREREGISTRATION_NEXTACTION.md`, committed before the
predictor existed.** No feature was added, no rule introduced outside the
preregistered list, no threshold tuned, no phase boundary moved. No S1 change,
no S3, no live orders.

## The short answer

> **Mostly no.** Of five prediction targets under leave-one-window-out, exactly
> **one** beats its baseline: **which outcome he acts on next**, and only on
> **BTC5m**, where it lifts **1.58×**. Price, action type, timing and clip
> ceiling show **no lift at all** on either horizon. The 1.68× descriptive lift
> does **not** carry over into prospective prediction except through side.

## BTC5m — leave-one-window-out, 23 folds, 177 decision points

| target | result | best baseline | **lift** |
|---|---:|---:|---:|
| **outcome (side)** | **0.8413** covered | 0.5325 majority | **1.58** |
| price, exact tick | 0.1111 | 0.1040 touch | 1.07 |
| joint side + price | 0.0952 | — | — |
| action type | 0.7966 | 0.7966 majority | **1.00** |
| timing (MAE) | 14.38 s | 14.38 s | **1.00** |
| clip ceiling ≈80 | 0.7403 | 0.7403 base rate | **1.00** |

Coverage **81.8%** (abstained where the book was unresolvable or the predicted
side had no bid); side accuracy over *all* non-terminal decisions is 0.6883,
reported so abstention cannot flatter the headline.

**Rule selected in 23 of 23 folds: `S-ALT` — he alternates outcome.** The
persistence baseline is **0.1883**, i.e. L5 almost never takes the same side
twice in a row on BTC5m.

**Price is a failure, not a weak success.** Median error **8 ticks**, mean 17.2,
p90 43. `P-TOUCH` was selected every fold yet beats the raw touch baseline by
0.7 percentage points. The level he goes to next is not predictable from the
book.

**Action type collapses to one class.** `A-EXPOSE` was selected in all 23
folds and predicts EXPOSE for everything: all 23 WITHDRAWs and all 13
MAINTAINs are missed. The preregistered withdrawal rule `A-FROZEN` was **never
selected** — on training folds it did not beat always-EXPOSE. **We cannot
predict when he stops.**

## BTC15m — the same predictor, unchanged, 12 folds, 1,936 decision points

| target | result | best baseline | **lift** |
|---|---:|---:|---:|
| outcome (side) | 0.6363 | 0.6357 **persistence** | **1.001** |
| price, exact tick | 0.1284 | 0.1779 touch | **< 1** |
| action type | 0.9179 | 0.9179 | 1.00 |
| timing (MAE) | 4.10 s | 4.09 s | 1.00 |
| clip ceiling | 0.8430 | 0.8430 | 1.00 |

**The side behaviour is *opposite* by horizon, and this is the central
cross-horizon result:**

| | persistence baseline | rule selected | lift |
|---|---:|---|---:|
| BTC5m | **0.1883** — he **alternates** | `S-ALT` (23/23) | **1.58** |
| BTC15m | **0.6357** — he **repeats** | `S-PERSIST` (12/12) | 1.001 |

On BTC15m the predictor is also **worse than the touch baseline on price**,
which is the honest shape of a model that has nothing.

## BTC15m lifecycle phases — preregistered boundaries, not searched

| phase | n transitions | base | accuracy | **lift** | bits gained | meets n≥50 **and** lift≥1.10 |
|---|---:|---:|---:|---:|---:|---|
| WHOLE | 2,028 | 0.5676 | 0.5734 | 1.010 | 0.104 | — |
| **OPENING 0–60 s** | **136** | 0.5294 | 0.6129 | **1.158** | **0.530** | **YES** |
| EARLY 60–300 s | 563 | 0.5844 | 0.5935 | 1.016 | 0.056 | no |
| MID 300–600 s | 622 | 0.5740 | 0.5770 | 1.005 | 0.050 | no |
| LATE 600–840 s | 673 | 0.5542 | 0.5582 | 1.007 | 0.072 | no |
| EXPIRY 840–900 s | 34 | 0.5882 | 0.6667 | 1.133 | 0.568 | **no — n=34 < 50** |

> **Yes, the poor aggregate hides a phase.** The opening minute carries
> **0.53 bits** and a **1.158×** lift against the whole window's 0.10 bits and
> 1.010×. Everything from 60 s to 840 s is flat.

EXPIRY looks similar (1.133×, 0.57 bits) but has **34 transitions against a
preregistered floor of 50**, so it does **not** meet the bar and is reported as
suggestive only. The boundaries were not moved to rescue it, and no alternative
slicing is reported.

## Rule labels

| rule | evidence | **label** | PMUS executable? |
|---|---|---|---|
| **Side alternation** (`S-ALT`) | 5m: 23/23 folds, lift 1.58. 15m: behaviour **reverses** (persistence 0.636), lift 1.001 | **5m-only** | yes mechanically, but **not promoted** |
| **Side persistence** | 15m persistence baseline 0.636, but lift over it 1.001 | **15m-only, and not predictive** | n/a |
| **Opening-phase structure** | 15m OPENING lift 1.158 (n=136); 5m whole-window lift 1.68 | **cross-horizon (direction only)** | partially — see below |
| **Move-level dominance** | 87.1% (15m) / 52.5% (5m) of transitions | **cross-horizon description, zero predictive lift** | yes, but specifies nothing |
| **80-share clip ceiling** | `C-BASE` selected everywhere; no lift | **cross-horizon description only** | yes as a size cap |
| **Withdrawal rule** (`A-FROZEN`) | never selected on either horizon | **unsupported** | n/a |
| **Atomic $1.00 taker pairs** | separate class; 9.7% / 28.8% of volume | **cross-horizon, separate action class** | **NO — taker on both legs** |

**The 5m-only label on side alternation is binding.** It is not promoted into
the PMUS BTC15m implementation, exactly as preregistered — and the reason is
stronger than a failed threshold: on BTC15m the behaviour *reverses*, so
porting it would be actively wrong, not merely unsupported.

## Executability under PMUS mechanics

PMUS places **maker-or-cancel** orders at tick offsets with a rung count.

* **Atomic $1.00 pairs — not executable.** Taker on both legs in 100% of 140
  pairs. Preregistered as the expected failure and confirmed. They are also
  zero-gross-margin and negative after the model-implied fee, so there is no
  case for pursuing them even if they were executable.
* **Side alternation — executable but 5m-only**, therefore excluded.
* **Opening-phase structure — only partially executable.** A 1.158× lift says
  behaviour is *more ordered* in the first minute; it does not say what to
  place. Nothing actionable follows without a rule that survives, and none did.
* **Move-level dominance and the 80 ceiling — executable as constraints**
  (re-quote rather than top up; cap exposure near 80) **but they carry no
  predictive content**, so they shape an implementation rather than justify one.

## The deployment-relevant answer

> **Which L5 mechanisms discovered cleanly in BTC5m survive unchanged in
> BTC15m and are executable under PMUS mechanics?**

**None.** The single mechanism that identifies cleanly on BTC5m — side
alternation, 1.58× across 23 held-out windows — **reverses sign on BTC15m**.
The classes that do appear on both horizons are either descriptive with zero
predictive lift (move-level dominance, the 80 ceiling) or structurally
inexecutable under maker-or-cancel (the atomic taker pairs).

**No candidate follows from this work and none is proposed.** The honest
position is that BTC5m and BTC15m L5 behave differently in the one dimension
where BTC5m is predictable, and that the 15m horizon — the one PMUS targets —
remains unpredicted outside its opening minute.

## What would move this forward

1. **The opening minute is the only place 15m shows structure.** A study
   confined to 0–60 s, with its own preregistration, is the next honest step —
   not an extension of this predictor.
2. **A 5m phase decomposition was deliberately NOT run.** It is not in the
   preregistration, and cutting 5m into phases *after* seeing that 15m's
   opening is special would be the exact post-hoc slicing the protocol
   forbids. It should be preregistered separately.
3. **Nothing here removes the unfilled-order limit** (`LIMITS.md` §1). Side is
   predictable on 5m; *where he rests and how much he exposes* remain
   unobservable, and no amount of this data changes that.
