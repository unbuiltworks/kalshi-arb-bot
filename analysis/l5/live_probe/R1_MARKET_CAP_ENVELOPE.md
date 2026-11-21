# One envelope, and the acceptance probe is inside it

OWNER 2026-09-16 P0. `prove_complement_when_flat()` placed a real, money-moving
BUY that `spring_cumulative_notional()` did not count. The stage allow-list was
written for R5F, where $0.01 against a $7.50 envelope is noise; at R1's $0.05
the same order is 20% of the envelope, so **"max $0.05 per market" was not
true — the worst case was $0.06.**

This is an accounting and risk-bound correction. The advertised envelope was
NOT raised to $0.06. The probe still runs, still joins the bid, is still
post-only at the venue minimum, and is still not an L5 decision.

## What changed

**One counter, one comparison.** `counts_against_market_cap(stage)` is the only
membership rule and `cap_admits(slug, proposed, already_spent=None)` is the only
pre-submission test — `already_spent + proposed <= cap`. Both money-moving call
sites go through them, so the probe and the session cannot drift apart.

COMPLETE legs still do not count, deliberately: a complement *reduces* the
position it completes rather than adding surplus spend, and the matched pair is
separately bounded by the `<= 1.00` invariant.

**The probe now writes the canonical ledger shapes.**

| row | when | why it matters |
|---|---|---|
| `submitted` | immediately after the venue accepts, **before** we wait on it | until a terminal row exists this counts at FULL notional, so a crash here cannot leak budget |
| `order_terminal` | every exit path, including a zero fill | converts the submission from "full notional" to "what actually filled" — this is what returns the remainder of a partial |
| `filled` | only when `got > 0` | **kind is `filled`, not the old bespoke `order_filled`** |

That last one was a live accounting hole. `ledger_qty_total()` — what the
venue-vs-ledger quantity check and the settlement repair assert on — counts
`filled` rows. The old kind was read by nothing, so an acceptance fill was a
real venue position that our own reconciliation could not see. Our ledger would
have run short by the probe amount against the venue's, silently.

**The session path is unchanged in behaviour.** `cum` is read from the ledger at
session start, so it now carries the probe's spend and the OPEN/EXTEND path
receives only the remaining budget; the existing check already downgrades to
COMPLETE or STOPs (`stop_reason: spend_cap_reached`) rather than exceeding. It
just routes through `cap_admits` now so there is one comparison instead of two.

**`reconcile_submissions()` accepts the probe stage.** The probe writes its own
terminal row, so this only matters when a crash lands between submit and
terminal — but then the submission counts at full notional *forever*, which
would silently retire the market. Now it can be closed like any other.

## The boundary, stated on purpose

The probe's **dollars** are inside the risk envelope. Its **position** is still
not an L5 decision: `held_inventory_from_ledger()` keys on `spring_` and is
deliberately untouched. Feeding the probe into the state lookup would change
inventory, average cost and `last_rel`, which are decision inputs — that would
be a strategy change, and this is not one. Pinned by
`test_5b_the_acceptance_fill_never_enters_the_l5_behavioural_inventory`.

## The six acceptance items

`tests/test_market_cap_envelope.py`, one test per numbered item:

1. **acceptance + session fills can never exceed $0.05** — probe $0.0039 plus
   an open of $0.043 leaves $0.0031; the next $0.01 leg is refused, a leg that
   exactly reaches the cap is admitted (`<=`, not `<`), one hundredth of a cent
   over is not. Plus `1b`: the probe does not merely fail the arithmetic, it
   **does not submit** — `placed == []`, `skipped: market_cap_exhausted`.
2. **partial fill reduces remaining budget correctly** — a $0.04 submission
   counts in full until the venue answers, then a 0.025 fill drops it to $0.01
   and returns $0.04 of headroom. `2b`: a zero fill returns all of it.
3. **a filled acceptance order stays in final market accounting** — end to end
   through the real probe: `submitted` + `order_terminal` + `filled` land,
   `order_filled` does not, `ledger_qty_total` reports `0.01`, and the fill is
   in the envelope exactly once.
4. **restart cannot reset the counter** — it is read from the volume, so a
   module reload reproduces it exactly. `4b`: an unreconciled submission
   survives restart at full notional.
5. **settlement/reconciliation includes the acceptance position** — a crashed
   probe's submission is reconcilable, and closing it returns the budget.
6. **one-market stop unchanged** — R1 still `$0.05` / concurrency 1, and
   neither new function can see `markets_allowed`, `concurrency` or
   `rung_markets`. `6b`: neither can place, size or price anything.

## The artifact gate

The commit-label gate is **retired** and replaced by `SOURCE_LINEAGE_ACCEPTED`.

It churned: a Railway variable change rebuilds from branch HEAD and cannot be
pinned to a commit, so the label moved on every deploy — twice — and both times
**all 36 COPY-set blobs were byte-identical**. The pin was red while the thing
it protected was perfect, and a gate that fails for a reason nobody acts on is
one people learn to wave through.

It also never checked what it claimed. A commit id says which tree the builder
was handed; it says nothing about whether executable content changed.

**And the replacement does not rubber-stamp arbitrary future commits.** Every
allow-listed delta now pins the exact reviewed blob. Edit a reviewed file again
and `verify()` fails as `CONTENT_DRIFT` until someone re-reviews it and
refreshes the pin with a new reason. `pin_shas()` exists to produce those ids
and is deliberately **never called by `verify()`** — a pin that refreshes itself
is not a pin.

The session file's delta is now classed `RISK_BOUND_TIGHTENING`, not
`OBSERVABILITY`, because it gates a submission. It is still not an execution
class: it can only ever *refuse* an order, computes no price, size or side,
contains no order verb, and leaves every L5 decision input alone.
