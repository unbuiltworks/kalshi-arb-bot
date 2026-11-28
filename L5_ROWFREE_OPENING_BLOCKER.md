# (a) row-free maker opening — the extraction is not available, and why

No code written. Service **disarmed**. `ac855ce3…` expired and unused.

I went to implement (a) and stopped at a hard structural finding. Reporting
it instead of silently picking a violation.

## What I confirmed is genuinely easy

| | |
|---|---|
| `handle()` size | 1,804 lines, but the row is used in only **4 places** |
| `dec` field surface | `{side, price, l5_size, relation}`; `handle` reads **3** |
| `tr` field surface | `handle` reads **7** (`depth, mode, ok, price, qty, reason, slippage`) |
| the probe already overwrites | `dec["side"] = leg["side"]` (`:6360`), `tr = dict(tr, …)` (`:6373`) |
| sequence grant | **`claim_accumulation_leg(market, rnd, leg, …)`** already exists |
| class budgets | OPEN 24 / COMPLETE 32 / REPLACE 16 / total 72, already graded |
| idempotency design | a deterministic controller key per (market, round, leg) would collapse repeated ticks onto one `coid` → `DUPLICATE_SUPPRESSED`, and can never collide with `row["action_key"]` |

So the authorization, the budgets, the duplicate prevention and the
decision content were all fine, exactly as the comparison said.

## The blocker: the translation layer is row-keyed by design

`FT.pmus_translate(decision, row, authenticated, want)` reads **three**
things from the row, and they are not incidental:

```python
bbo = ((row.get("pmus") or {}).get("bbo")) or {}   # the book it prices against
age = row.get("pmus_snapshot_age_s")               # the staleness gate
got = authenticated.get(row.get("action_key"))     # the maker-class lookup
```

So the book, the `STALE_MAX_S = 2.0` gate and the maker-class strictness
check are all keyed on the PMUS snapshot **embedded in the L5 row** — not
on a book passed in separately. A row-free caller cannot reach them.

### Every route out violates one of your explicit requirements

| route | what it breaks |
|---|---|
| **(a) synthesize a row-shaped dict** | **Requirement 5** — "never use fabricated L5 events". It also makes our order indistinguishable from L5's in the tape, ledger and provenance chain: the exact error this programme has guarded against from the start |
| **(b) build `tr` directly, skipping `pmus_translate`** | **Requirement 2** — "preserve every risk gate". Loses the 2.0 s snapshot-staleness gate, the no-book gate, and the maker-class strictness check, and creates a second copy of three rules that can drift from the enforced ones |
| **(c) add a row-free sibling to the translation module** | **The freeze guard.** `tests/test_faithful_rule_frozen.py` pins the **sha256 of the whole file** at commit `030d8bf`, so even a purely additive function breaks it |

I checked (c) hoping it was additive-safe. It is not: the guard hashes the
file, not the functions.

### Correction to my own last message

I said (a) was "pure extract-method". That was wrong. I had verified
`build_orders` derives legs from the authenticated book — true — but not the
translation layer beneath the submit path, which is row-keyed. The
extraction I recommended does not exist.

## The decision, which is yours

Route **(c) with a deliberate re-freeze** is the only one that neither
fabricates a row nor duplicates a gate: add
`controller_translate(decision, book, maker_class, want)` beside the frozen
pair, carrying **the same three gates**, and update `FROZEN` to the new
hash in the same reviewed commit.

That is the designed way through a pinned guard — the same pattern as
`CLAIM_GATED_SUBMIT_SITES` and the capability-construction test, both of
which are meant to be edited as an explicit act rather than worked around.
But the freeze was set deliberately at `030d8bf` and its test says the rule
is frozen, so re-freezing it is your call, not mine.

If you prefer not to touch the freeze, the honest alternative is that
autonomous openings are **not reachable** without either fabricating a row
or duplicating three gates, and the L5-followed path remains the only way
inventory gets opened.

## What is unchanged

Maker-only, defensive crossing disabled, `EXECUTION_MODE = MAKER_ONLY`,
every limit as before, service disarmed on three preflight counts, no new
service, no new authorization system, nothing armed, expired approval
untouched.

**Can the system independently generate valid maker orders today? No** —
and the obstacle is the row-keyed translation plus its freeze, not
authorization, budgets, provenance or duplicate prevention, all of which are
ready.
