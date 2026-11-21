# Authenticated-book freshness submission gate

**Authorization:** owner, 2026-09-17 — the single execution change supported by
the completed CP1 forensics. Deployed **HALTED**. No re-arm. No new mandate.

## The rule

A live order is not submitted when the authenticated executable book required
to validate post-only placement is stale, missing, unreadable, or otherwise
outside the already-defined freshness standard (`AUTH_TRIM_MAX_AGE_S = 10.0`,
unchanged). The controller may still produce its desired action; the execution
adapter refuses to send it, fail-closed.

**MODEL WANTED ACTION ≠ EXECUTION COULD SAFELY SUBMIT ACTION.**

## What was actually wrong

`_authenticated_trim` fails OPEN by design. Its docstring said so plainly:
*"A first leg is never blocked by this — the worst case is the rejection we
already get today."* The forensics falsified that premise.

On `aec-dota2-yes-tm-2026-09-16` the gateway book read `0.45 / 0.49` while the
venue's own ask was `0.45`. The authenticated book that would have caught it
was 11.2 s old, so it was **discarded for being stale** and the gateway price
was kept. Five post-only BUY YES at 0.45 crossed the real ask and were refused
at insertion.

Order 6 is the control: a fresh read arrived (age 0.2 s), the trim applied,
`0.45 → 0.44`, and the order rested. Same market, same size, seconds apart.

**Failing open is right for PRICING and wrong for SUBMITTING.** The trim is
untouched — it still fails open and is still arithmetically incapable of
raising a price. What is new is the second half: the order is not sent when the
book that would have validated it was not available.

## The rule is positive, not a blocklist

`authenticated_book_usable()` permits submission only when the authenticated
book was genuinely consulted and returned a **definite verdict**:

* it disagreed and the price was trimmed (`applied`), or
* it agreed the price was already non-crossing.

Everything else is not usable: unreadable, slug absent, stale, slow read,
missing opposite ask, untrimmable, exception — **and any reason invented
tomorrow that nobody remembers to add here.** That last property is tested.

## Measured counterfactual over the real 13 CP1 orders

Real captured trim verdicts, replayed through the real gate function.

| # | Market | Px | Trim verdict | Gate | Was rejected |
|---|---|---|---|---|---|
| 1–5 | dota2-yes-tm | 0.45 | `stale:11.2` | **SKIP** | **REJECTED ×5** |
| 6 | dota2-yes-tm | 0.44 | trimmed from authenticated book | SUBMIT | — |
| 7 | lib-lqu-pal | 0.27 | `already_non_crossing_on_both_books` | SUBMIT | — |
| 8–12 | lib-lqu-pal | 0.27 | `unreadable:slug_not_in_status_document` | **SKIP** | — |
| 13 | county-nor-der | 0.37 | `already_non_crossing_on_both_books` | SUBMIT | — |

**Would submit: 3/13. Would skip: 10/13.**
**All five observed insertion rejections are prevented — 5 of 5.**

The gate is not a blanket refusal: three orders still go through, and none of
them is one that was rejected. Per market: dota2 keeps only its one
fresh-book order, lib-lqu-pal loses 5 of 6 to the unreadable slug, and
county-nor-der is unaffected.

**Opportunity cost, stated not hidden: 10 of 13 actions on this sample.** That
is the honest price of not submitting on a book we could not read. Historically
`no_authenticated_book` is 23.6 % and authenticated age > 10 s is 12.7 %, so a
quarter to a third of actions being skipped is the expected steady-state cost.

## Skips are measurable and cost nothing

Every skip records `execution_gate` on the decision: `submitted: false`, the
condition, the model's action, the price it would have used, the standard
applied, and the full provenance. `report["execution_gate_skips"]` counts them.

`mark_noop` keeps the model's action in `attempted` and sets a refusal string
that **carries the sub-reason**, so `hold_refusals` buckets per condition
rather than collapsing every cause into one number. The behavioural fidelity
layer reads the model's own `sampled` draw and is untouched; only the
executable layer sees the hold. Classified as **venue/execution adaptation** —
never a safety cap, never a model WAIT prediction.

A skip sends nothing to the wire, spends nothing, and creates no exposure.

## Provenance

The raw inputs to every freshness decision now survive on the record —
**including on the branches that refuse**, which previously kept nothing but a
`why` string: `source`, `status_utc`, `authenticated_age_s`,
`probe_receipt_utc`, `both_sides_served`, the book, `freshness_standard_s` and
`slow_read_budget_ms`. Every freshness decision is reproducible from the record
alone. Purely additive; no pre-existing key changed value or meaning.

## Tests

`tests/test_authenticated_book_submission_gate.py` — 21 tests covering all
seven required categories: fresh book permits submission; stale book (10.01,
11.2, 60, 734.5 s) sends nothing to the wire; missing/unreadable/absent-slug/
recorder-outage/no-age sends nothing; **fresh gateway does not rescue a stale
authenticated book** (the live failure shape reproduced exactly); fresh and
safely non-crossing permits submission; restart cannot bypass the gate; skips
observable without consuming money or exposure. Plus the counterfactual pinned
as a fixture.

"No wire submission" is proven against a transport that records every POST, not
asserted from intent.

**Four mutations injected — gate disabled, always permits, unknown reasons
treated as usable, `stale:11.2` added to the definite list — all four caught.**

Full suite: **5008 passed**, 27 skipped, 1 xfailed.

## Two things found that were not the change

* Three sibling trim tests sliced `[:4000]` chars of `_authenticated_trim` and
  silently truncated once provenance capture pushed the function to 5,195. The
  properties were all still true. They now slice the whole function — strictly
  more coverage than a magic byte count.
* `tests/test_live_mandate_adversarial.py` hardcoded `"expiry":
  "2026-09-16T23:59:00Z"` — one minute before midnight on the day it was
  written. 13 tests across two files went red at the rollover, with
  `mandate_expired` standing in for whatever they were asserting.
  **Pre-existing and unrelated; confirmed red on clean HEAD.** The expiry is now
  computed from the real clock so it cannot expire again, and the expired path
  is still covered explicitly.

Test harnesses were given a price-neutral fresh book (0.01/0.99, so
`safe = min(gateway, authenticated)` always resolves to the gateway and no
expected price moves). That supplies a missing precondition; it does not relax
the gate.

## Scope limit, stated plainly

The gate sits where the trim already runs: the **OPEN first leg**. That is
where the freshness signal exists and where all 13 observed orders were. EXTEND
is still deliberately excluded from the trim (its rejections remain unexplained,
issue #93) and is therefore not covered by this gate. The complement leg has its
own venue preview. **Extending the gate to EXTEND would be acting on evidence we
do not have**, and was not authorized.

## Unchanged

sizing 0.10/0.125 · k 0.00619 · 30 s rest · maker/post-only · 0.98 fee-safe
ceiling · ACT/WAIT and model logic · market selection · residual limits ·
concurrency · campaign limits · daily loss limit · `FORBIDDEN_CLASSES`.

No live mandate was created. The existing one remains exhausted (3 of 3 markets)
and expires 2026-09-17T20:36:35Z on its own.
