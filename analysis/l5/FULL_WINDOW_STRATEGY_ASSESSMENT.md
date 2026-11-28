# Full-window continuous accumulation: what exists, what is missing

Inspection only. No behaviour changed, nothing enabled, shadow lock engaged.

## First, the honest part: T+6 to T+8 is my artifact

It is not a strategy design. It falls out of the *probe*'s shape -- place
once, hold to a single terminal deadline, cancel once. Because
`rest = terminal - placement` and the watchdog caps an order's age at 300s,
widening the terminal margin to get watchdog headroom necessarily collapsed
the placement band to about two minutes. I turned a **per-order** constraint
into a **strategy-wide** one.

Worse, it leaks: `l5_live_execution.py:4253` calls `TS.may_place(...)`, so the
probe's band -- including the placement floor I added for the mandate --
throttles the **accumulation engine** as well. That coupling is mine and
should be undone.

## What already exists (and does not need building)

**Multiple sequential pair cycles.** `FaithfulExecutor.acc_max_rounds()`:
default **8** pairs per market, env `L5_ACC_MAX_ROUNDS`, hard cap 64,
unparseable reads as 1. "One round is one UP+DOWN pair... it is a HARD bound:
when the rounds for a market are spent, the engine refuses rather than
continuing."

**Correct cycle ordering.** `probe_round()` walks rounds from zero and returns
the first with an unspent leg, so "a one-sided round is FINISHED before a new
pair is started -- the opposite leg of an existing position always takes
priority over opening another."

**Per-round re-planning.** `probe_store_plan(market, plan, rnd)`: "Each round
now plans against the book and the inventory as they are, while the two legs
WITHIN a round stay byte-identical across a restart."

**Risk re-evaluated every round.** "Cash, the worst-case deficit,
account-wide exposure, unmatched contracts and the realized-loss halts are
all still evaluated per round, and each round is sized against the state the
PREVIOUS rounds left behind."

**Single-use claims on four axes.** `accumulation_sid(exp, market, rnd, leg)`
= `acc1.<exp>.<market>.<rnd>.<leg>`, read from disk, and "unreadable = treat
as spent."

**Continuous inventory.** UP/DOWN quantities, average cost, matched quantity
and directional residual are already tracked, and `settle()` already splits
`pair_cost / pair_net / residual_cost / residual_net / capital_deployed /
avg_yes_cost / avg_no_cost` on an AVERAGE_COST basis.

**Pre-rebate economics gate.** `build_orders` refuses unless
`both_positive_if_taker` -- profitable on both outcomes charging the TAKER fee
on both legs and crediting no rebate. `rebate_upside_usd` is reported on its
own line and never justifies an order.

**A per-order age deadline primitive.** `maybe_qm_deadline` cancels at
`min(T_ack + 120s, T_end - 180s)` measured from the VENUE's own
acknowledgement, through `deadman` (cancel by venue id, proof by re-read,
`still_open`/`unverified` mark the store UNCERTAIN), recovery-first after a
restart, and HALT rather than guess when the durable record cannot be
trusted. Its docstring names the gap exactly: "Every other cancellation route
in this runner fires on a WINDOW or a STATE... **None of them is a per-order
age deadline**."

## The three real gaps

**Gap 1 -- the per-order deadline is scoped to the queue-measurement
experiment.** `maybe_qm_deadline` returns immediately unless `QM.active`.
Accumulation legs therefore have no per-order age deadline, and their only
cancellation is window- or state-based. That is precisely what forces
`rest = terminal - placement`. Give accumulation the same deadline and an
order's life stops being the strategy's life. *Smallest change: one scoped
deadline hook plus its call site, mirroring the QM one rather than inventing
a second pattern.*

**Gap 2 -- accumulation shares the probe's placement band.** Fix by scoping
the floor to the probe and giving accumulation its own two cutoffs (Gap 3's
policy), so it evaluates from market-ready rather than from T+6.

**Gap 3 -- repricing spends a leg claim.** `probe_legs_claimed` treats the
claim file's presence as "the leg was submitted, so a restarted process
cannot re-place a leg whatever its in-memory state says." That is what makes
restart-safety work, and it also means a leg cancelled UNFILLED cannot be
re-placed in the same round: repricing costs a whole round. Across a
fifteen-minute window of active quoting, eight rounds would not last.
*Smallest change: add an attempt axis --
`acc1.<exp>.<market>.<rnd>.<leg>.<attempt>` -- so every individual placement
stays single-use and restart-safe, bounded by its own hard cap, instead of
each reprice consuming a pair cycle.*

## Can the authorization architecture support it safely?

**Mandate and window claims: yes, unchanged.** One approval, per-window
single-use claims, nothing auto-renews.

**Multiple pair cycles: yes, already.** Eight per market by default.

**Repeated replacement: not as-is.** It needs Gap 3. Every placement must
remain a single-use claim -- that property is why a restart cannot
double-submit -- so the fix adds an axis rather than relaxing the rule.

**But the real binding constraint is capital, not claims.** At a $5 envelope
and one tick behind a 0.49/0.51 book, `build_orders` sizes 3.37 contracts per
leg and reports `cash_gate_obligation_usd 4.9876`. The FIRST pair consumes
essentially the whole envelope, so round two is refused on cash long before
round eight. Full-window multi-cycle accumulation at $5 is therefore a
one-pair-at-a-time exercise in practice: cycles become available only as
earlier pairs matched and released, and "eight rounds" is a ceiling the
money will not reach. Raising it is a capital decision, not a code one, and I
am not proposing it.

## Late-window cutoff and residual policy (requirement 6)

Replace the single arbitrary terminal with two economic cutoffs plus the
existing hard deadline:

* **open cutoff** -- stop opening NEW first legs once too little window
  remains for a complement to fill at a price that satisfies
  `both_positive_if_taker`. This is an economic test, not a clock: a first
  leg that cannot be completed is a directional bet the engine never
  intended.
* **complete cutoff** -- keep quoting complements against existing residual
  after the open cutoff, because completing is what removes risk.
* **terminal** -- the existing hard deadline, cancel by venue id, then carry
  any residual to settlement rather than crossing to flatten. The Oct 19
  numbers say crossing pays the taker fee against a 1-4c gross and is
  strictly worse.

## Recommended order of work

1. Scope the placement floor to the probe (undo my coupling). Smallest, and
   it stops the full-window engine being throttled by a probe constant.
2. Give accumulation a per-order age deadline, mirroring `maybe_qm_deadline`.
3. Add the attempt axis to accumulation leg claims, with a hard cap.
4. Implement the open/complete cutoffs as economic tests.
5. Validate the whole thing in SHADOW across complete windows -- the shadow
   controllers already run every tick of every window -- before asking for
   any live authorization.

Steps 1-4 are code changes to the money path and want a full regression and
the owner's go-ahead. Step 5 needs no authorization at all and is where the
evidence for full-window economics should come from.
