# L5 sequential-decision comparison — decisions, not fills

READ-ONLY over `data/l5_master/btc15m_sequences.jsonl`. **No fill model is
used anywhere in this analysis**, so nothing in it is a fill, a modelled
fill, or executable profit. It compares CHOICES at matched states, which is
the one comparison the corrected UNKNOWN_QUEUE semantics leave fully
available: whether our engine would have *decided* the same thing does not
depend on whether our order would have filled.

5,546 windows, **693,711 L5 decisions**. Each fill is classified against the
inventory that existed immediately before it, FIFO.

## What L5 did

| action | count | share |
|---|---|---|
| **EXTEND** (adds to a side already held) | 355,429 | **51.2%** |
| **COMPLETE** (pairs against the opposite side) | 332,285 | **47.9%** |
| OPEN (both sides empty) | 5,997 | 0.9% |

Only **0.9%** of his decisions are first legs. His behaviour is
overwhelmingly *working existing inventory*.

## What our deployed policy would have decided at those same states

| our verdict | count | share |
|---|---|---|
| **EXTEND → REFUSE** (we never add to a held side) | **355,429** | **51.2%** |
| COMPLETE → REFUSE, pair > 0.96 | 191,759 | 27.6% |
| COMPLETE → ADMIT | 140,526 | 20.3% |
| OPEN → ADMIT | 5,299 | 0.8% |
| OPEN → REFUSE, outside the 0.35–0.65 band | 698 | 0.1% |

**Decision agreement: 21.02%** — we would take 145,825 of his 693,711
actions.

## The largest divergence, and it is a design choice of ours

**Same-side EXTEND: 51.2% of everything he does, and we refuse all of it.**
Our engine quotes both sides and never adds to a held side, so his single
most common action is structurally unavailable to us. `CLAUDE.md` is
explicit that this is wrong in principle — *"Same-side `EXTEND` is a
first-class action, not an error to flatten immediately"* — and the measured
frequency puts a number on the gap for the first time.

Second: his **median COMPLETE pair cost is 0.98**, above our 0.96 gate
(p10 0.82, p90 1.14). Only **43.41%** of his COMPLETEs are at pair ≤ 0.96.
So the gate refuses 27.6% of his total actions.

Third, on size: **557,434 of 693,711 fills (80.4%) exceed our 4.84-share
clip.** That is a capital constraint, not a behavioural one.

On price he agrees with us: **88.4% of his OPENs fall inside our 0.35–0.65
band**, so the entry band is not where we diverge.

## What this does and does not license

It does **not** say enabling EXTEND would earn money. §90.1 already tested
the nearest causal version — take every OPEN/EXTEND as he did and refuse
dear COMPLETEs — and it is **negative out of sample** (DEV +1.7735%,
HOLDOUT −1.7798%), because refusing the dear completion strands the exposure
the extends created. Frequency of divergence is not evidence of profit.

It does say the divergence is **concentrated in one action type**, is
measured rather than inferred, and is the first thing a faithful replication
would have to address — before size, and before anything that depends on a
fill model.

**Explicitly not claimed:** no fill rate, no matched-pair profit, no
executable expectancy. 21.02% decision agreement is not 21% of his P&L.
