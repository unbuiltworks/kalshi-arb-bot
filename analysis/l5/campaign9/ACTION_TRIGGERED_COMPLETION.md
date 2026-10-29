# Does a maker completion need a new L5 action? Yes. Does that cost money? Not provably.

Reproduce with `analysis/l5/campaign9/blind_completion_replay.py`. Read-only,
no production change.

## 1. The mechanism — confirmed from code, not inferred

**Yes. A maker completion for inventory we already hold is evaluated once per
admitted L5 action and at no other time.**

`TS.complement_order` is called from `l5_live_execution.handle(row, ...)`
(`:6378`). `handle` takes an L5 **action row**, and its only hot-path caller
is the ingest loop — `self.handle(r, now)` at `l5_pmus_live_runner.py:2450`,
immediately after `ingest:admitted` increments. There is no other call site
that can price or reprice a completion.

Neither timer path substitutes:

| path | period | why it cannot stand in |
|---|---|---|
| `maybe_ts_completion` | 10 s | decides whether to **cross**. Needs `L5_TS_COMPLETION_CROSS` **and** an armed transport; in MAKER_ONLY production it is a logged proposal. Across seven live Oct-24 windows its 70 evaluations are 40 `ALREADY_MATCHED`, 18 `WORST_CASE_CEILING_UNKNOWN`, 12 `COMPLETION_PRICE_OUT_OF_BAND`, with `authorized_to_cross: false` in **every** one. |
| `maybe_completion_requote` | per tick, flag-gated | reprices an **existing resting** completion. It cannot create one where none rests. |

So when a completion is refused at the instant an action arrives, nothing
rests and nothing is reconsidered until the next action — a median ~34 s
later, and longer when L5 is quiet.

## 2. The replay the brief asked for cannot be completed on existing data

The four-part test needs, simultaneously: real inventory with cost basis,
the engine's real refusals, the book sampled **between** actions, and real
order state. No tape has all four.

| tape | real inventory | real refusals | book between actions | order state |
|---|---|---|---|---|
| Oct-24 live windows (7) | **yes** | **yes** | **no** — `L5_LIVE_DECISION` fires *per action*, so the blind interval leaves no book record | yes |
| Oct-26 residual sampler (3) | no — **modelled**, shadow variants | no | **yes**, every 5 s, `driven_by: UNMATCHED_INVENTORY_NOT_ORDER_STATE` | **no** — a shadow never rests an order, so `order_resting: false` is structural, not evidence |

**That is itself the finding.** The blind interval is unobservable precisely
because the engine is blind in it, and the shadow that does see it has no
orders and no real inventory. The instrument that would close this is the
Campaign 9 decision tape, which records every evaluation with its refusal
class.

## 3. What the 5-second book tape does show — and it argues the other way

Filtered to one shadow variant (`RESIDUAL_URGENCY`), outside the 180 s
cancellation margin, while unmatched:

| window | eligible samples | blind intervals (34 s) | intervals with a better price inside | mean improvement | **queue-reachable** |
|---|---|---|---|---|---|
| 2100z | 18 | 3 | **0** | — | **0 of 3** |
| 2115z | 31 | 6 | 5 | 4.8 ¢ | **3 of 6** |

Two things kill the missed-opportunity reading:

**The improvement is monotone.** In 2115z the complement's own price fell
0.33 → 0.22 → 0.19 → 0.11 → 0.08 → 0.06 → 0.05 → 0.04 across ~356 s. In a
monotonically improving market an engine that evaluates 34 s **later** gets a
**better** price, not a worse one. The 4.8 ¢ "forgone" is the gap between the
interval's start and its best point — which is only a loss if the price then
reversed, and it did not. Action-triggering cost nothing here; it gained.

**Almost nothing is reachable.** Queue ahead at the displayed touch runs
35× to 1,130× the quantity needed. 2100z is 0 of 3 intervals reachable even
on the generous ≤50× test. A displayed touch is not a fill, and the venue
publishes no per-order queue position.

**A correction to my own first run of this script:** it did not filter the
six interleaved shadow variants (`RESIDUAL_URGENCY`, `ARGMAX`,
`ARGMAX@TWO_SIDED`, `ARGMAX@TWO_SIDED_LADDER`, `PAIR_AVAILABLE`, unnamed),
so the interval walker compared one variant's book against another's and
manufactured 32–86 ¢ of "forgone" price out of interleaving, with a
193-cent total. Those numbers were an artefact. The table above is after
the fix.

## 4. The one real instance where this mechanism cost money — and it is a different defect

`l5_pmus_live_runner.py:4965-4983` records window 1945z on 2026-10-25: the
maker complement route refused **twelve** times on
`COMPLEMENT_PAIR_ABOVE_CAP`, nothing was ever posted, all 121 residual
samples carry `order_resting: false`, the executable exit was worth
**−$0.0144 at 204 s**, and the engine's first legal opportunity was at
**600 s** by which time the best exit was **−$0.5348**. About **$0.52**.

That is a **gate** problem, not a trigger problem. The engine was blind
because nothing passed `COMPLEMENT_PAIR_ABOVE_CAP`, not because no action
arrived — and the fix already shipped: the MAKER-phase bail was relaxed where
its own rationale does not apply (nothing resting), which is the code that
comment sits in. (These figures exist only in that comment; I found no
independently reproducible artefact for them in the repo.)

## 5. Action-triggered vs book-triggered completion

| | current (action-triggered) | book-triggered |
|---|---|---|
| evaluations while unmatched | ~1 per 34 s | ~1 per 5 s, ~7× more |
| measured benefit on this evidence | — | **none established**; the one moving window improved monotonically, and 6 of 9 intervals were not queue-reachable |
| submission budget | 32 of 72 used in C8 | more evaluations → more requotes; each requote **resets queue priority**, and the dwell verdict already found no benefit to longer rests (break-even 0.9532 vs achieved 0.9530) |
| residual risk | residual carried under existing controls | unchanged, but more chances to complete **dearer** — and completing above 0.9532 is negative at every price (−$0.0877 at 0.96, −$0.6005 at 1.00) |

## Recommendation

**Do not change the completion trigger on this evidence.** The mechanism is
real and worth knowing, but the measured cost of it is zero in the only
window with real complement movement, the queue makes most touches
unreachable, and a faster trigger's most likely effect is more requotes at
worse pair prices — the one thing already measured to be negative.

**What would settle it, at no incremental risk:** the Campaign 9 decision
tape. It records every evaluation and refusal class against real inventory
and real order state, which is exactly the join no existing tape provides.
That remains blocked on authorization installation, and it is now the second
distinct question blocked on the same thing.

**Flagging, because it bears on all of this:** `WHAT_L5_ACTUALLY_IS.md` puts
L5's lifetime at trading **+$15,707** against incentives **+$71,551** — 82 %
of the result is incentives — and splits trading by pair band: ≤0.90 earns
**+553.6 bp** on turnover, 0.90–1.05 is **~28 bp** ("incentives are the
business"), >1.05 is **−460 bp**. Our 23 settled pairs average **0.9296**,
with 39.2 % of turnover in the ≤0.90 band against L5's 12.3 %. We are better
placed than L5 on band mix and we receive **$0.0000** of the incentive that
carries his middle band. That is the economic question worth the next
investigation, and it is not a throughput question.
