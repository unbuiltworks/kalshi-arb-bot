# Two-sided shadow validation — implemented, deployed, measuring

**Status: the change is live in shadow and the comparison is running.
No live promotion. No policy change. The campaign remains expired and
disarmed and the live executor is byte-for-byte unchanged.**

Scope of this document: what was built, what requirement 1 answers, and
what the comparison can and cannot yet say. The economics are NOT yet
decidable — see "When this can be read".

---

## 1. Requirement 1 first: how many refusals were genuinely addressable

This needed code, because the old counter could not answer it. The
`already_resting` gate fired BEFORE the side was decided, so a refusal was
recorded without recording whether the policy wanted the side we were
already resting or the other one. The gate now computes the wanted side as
a diagnostic and tallies three buckets.

**Production, `ARGMAX@ONE_TICK_BEHIND` (the one-sided control), classified
refusals since 2026-10-24T20:06Z:**

| bucket | count | share |
|---|---|---|
| `already_resting_OPPOSITE_SIDE` | 17 | **22.08%** |
| `already_resting_SAME_SIDE` | 60 | 77.92% |
| `already_resting_SIDE_UNKNOWN` | 0 | 0.00% |
| classified | 77 | |

**Only the OPPOSITE_SIDE bucket is addressable by two-sidedness.** A
SAME_SIDE refusal is a re-quote of a side already resting — the one-order
limit was right to refuse it, and a two-sided arm refuses it too.

So the 66,044-refusal figure that motivated this work is **not** the
opportunity. On this sample the addressable share is a little over a fifth
of it. Scaling naively, ~14,600 of the 66,966 lifetime refusals, not
66,966.

**N = 77 is small and this rate is provisional.** The lifetime bucket
(66,966) was counted since 2026-10-19; the split only since today's
deploy, so the two have different denominators and the share must not be
read against the lifetime figure. The line reports
`split_covers_whole_bucket: false` for exactly this reason.

**The mechanism is confirmed working.** On the two-sided arm the same
counter reads OPPOSITE_SIDE **0** and SAME_SIDE 76: two-sidedness removes
the opposite-side refusal entirely while correctly keeping the same-side
one.

---

## 2. What the arm actually does — narrower than I previously implied

`ARGMAX@TWO_SIDED` runs the BASELINE policy at the SAME one-tick offset as
`ARGMAX@ONE_TICK_BEHIND`, so a difference between them is attributable to
two-sidedness alone. The line asserts that precondition itself and returns
`ARMS_NOT_CONTROLLED` if the two ever stop sharing an offset.

**But it PERMITS a second resting side; it does not seek one.** Measured,
not reasoned: from flat with no fill, eight ticks produce one quote and
seven SAME_SIDE refusals. The policy decides from inventory, so while
inventory is flat it wants the same side every tick and the arm never
holds two legs at once. Two sides rest only once inventory is non-flat and
the policy wants the other one — extending while a complement rests, or
completing while an extend rests.

Those are real and frequent states, and freeing them is the smallest safe
change. **It is not the "rest both legs simultaneously at the planned
width" hypothesis** from the bid-resting analysis. Quoting both legs from
flat needs a counterfactual second price for inventory we do not hold,
which is a new policy rather than a placement. Nothing here measures it
and a positive result from this arm would not be evidence for it. L5's
use of fixed-offset simultaneous quoting remains a hypothesis with no
direct evidence.

---

## 3. Requirement 2: what was preserved

- **Per-side order state** keyed by `FM.UP`/`FM.DOWN`. A re-quote on a side
  replaces by assignment, so there is never more than one order per side.
- **Fills clear only the filled side**; the opposite leg keeps resting,
  which is the entire point.
- **Cancellation at window close** clears the whole map and counts BOTH
  unfilled sides, because counting one would flatter a two-sided arm's
  fill rate.
- **`sc["resting"]` keeps its original single-dict shape.** I first changed
  it to a per-side map and twelve tests failed on `rest["price"]` — a
  breaking change to persisted, observable state. The per-side map now
  lives beside it under `resting_sides` and legacy single dicts migrate on
  read.
- **Aggregate exposure is unchanged**: per-side ORDER state is not per-side
  INVENTORY. One inventory object per scenario still backs the residual
  cap, so it is evaluated over the whole book and not twice over halves.
- `modelled_fill` keeps its old shape; the full list rides alongside as
  `modelled_fills`, so existing readers are unaffected while the tally
  cannot under-report a tick where both sides fill.

**A real bug this exposed.** The shadow emit throttle was keyed per
POLICY, and three arms share the policy name `ARGMAX`. It never bit while
the two ARGMAX arms differed in `target_price`, but the new arm sits at the
same depth, so its key collided tick after tick and whichever decided
first MUTED THE OTHER ENTIRELY — the controlled comparison would have
silently stopped existing. Now keyed per variant.

---

## 4. Requirements 3-6: the comparison, and why it cannot be read yet

Both arms run in the same process on the same ticks, so requirement 3 is
satisfied by construction. The comparison is emitted once a minute as
`L5_SHADOW_TWO_SIDED`, restricted to windows BOTH arms settled, matched on
the venue's own slug.

**Current read (CONSERVATIVE headline):**

| | control | treatment |
|---|---|---|
| common settled windows | 1 | 1 |
| net over common windows | 0.0000 | 0.0000 |
| matched units | 0 | 0 |
| residual units | 0 | 0 |
| capital deployed | 0 | 0 |

`verdict: INSUFFICIENT_COMMON_WINDOWS`. The one common window traded
nothing. **There is no economic result yet and none should be inferred.**

Lifetime execution counters, which cannot be restricted to common windows
and are labelled as such everywhere they appear:

| | control | treatment |
|---|---|---|
| quotes placed | 1,345 | 6 |
| modelled fills | 972 | 2 |
| fill rate | 0.7594 | 1.0000 |
| adverse rate | 0.9167 | 1.0000 |
| adverse per fill | -0.0827 | **-0.1100** |
| swept share | 0.9805 | 1.0000 |
| controller errors | 0 | 0 |

The treatment arm's two fills are **not** a measurement. But the direction
is the risk I flagged when I first rejected two concurrent orders: two
resting legs double the directions adverse selection can arrive from. If
`adverse_per_fill` stays worse on the treatment arm at useful N, that is
the result, and more matched pairs will not rescue it.

**Why the comparison turns on money.** The verdict reads
`incremental_net`, not matched count and not completion rate; a test pins
that one more matched pair bought too dearly returns
`TWO_SIDED_NOT_BETTER`. Pair acquisition cost is reported per pair as the
exact identity `1 - pair_net/matched` — directly comparable to the 0.96
cap — because `pair_net` alone scales with volume.

**Queue assumptions are conservative and binding.** `FILL_RULE` is
`QUEUE_AWARE_THREE_SCENARIO`; an unknown queue fills only when a print
sweeps the level, and 98.05% of control fills came from a sweep. A touch
is never credited as a fill and no price crossing is treated as a
guaranteed maker fill.

### When this can be read

The treatment arm's durable state started empty today, so common windows
accrue at one per 15 minutes. **200 common windows lands about
2026-10-26T22:00Z (~50 hours).** Requirement 1's rate firms up much
sooner, since it accrues per tick.

No offline shortcut is available: the only local btc15m book tape covers
two windows on 2026-09-27, and the production tape lives on the service
volume.

---

## 5. Requirement 9

**Not met, and not claimed.** A bounded live validation proposal is
conditional on two-sidedness improving conservative expected net returns,
and that is not yet measured. Nothing in this document authorizes a
promotion, a capital change, or a rearm.

---

## Accounting labels

Every figure above is **MODELLED** by the queue-aware fill model under the
CONSERVATIVE scenario. None of it is submitted, filled, settled or
realized. The only venue-confirmed numbers in this program remain Stage
1's: **-$3.7194 realized net over 8 windows**, six venue-confirmed fills.
