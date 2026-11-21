# Where `quote_between_tte_s = [0, 300]` came from

**It is not a measurement of L5. I have repeatedly said it was, and that was
wrong.**

## The answer

**Category 4 — an adaptation introduced for Gemini**, and specifically for
**Gemini BTC5m**, where 300 seconds is the *length of the contract*. It then
passed to BTC15m by inheritance when that cohort was added to the same lane,
without being re-derived.

L5's own measured value is **`[0, 900]`** — the whole window.

## The provenance chain

The frozen artifacts say it outright:

| artifact | status | value |
|---|---|---|
| `FIDELITY_OBSERVABLE_V1.json` | `OBSERVED_PROSPECTIVE` | `[0, 900]` |
| `L5_ADAPTED_V1.json` | `OBSERVED_PROSPECTIVE` | `[0, 900]` |
| `L5_ADAPTED_PMUS_SPORTS.json` | `OURS_ADAPTED` | `[0, 86400]` |
| **`L5_ADAPTED_GEMINI_CRYPTO.json`** | **`OURS_ADAPTED`** | **`[0, 300]`** |

The Gemini parameter's own record:

```json
{
  "name": "TTE_policy",
  "status": "OURS_ADAPTED",
  "evidence": null,
  "transferred_from": null,
  "prior_evidence": "OURS for L5_ADAPTED_GEMINI_CRYPTO. Not a finding about
                     this venue and never citable as one.",
  "value": {"quote_between_tte_s": [0, 300]}
}
```

`params.py` defines `OURS_ADAPTED` as *"OUR risk or economic choice. Never L5
evidence, never promotable to an observation about him"*, and
`is_l5_evidence()` returns `False` for it.

### Commit 9b8a1ebe — 2026-09-17 21:37:55Z — where 300 was chosen

`src/l5_surface/lanes.py` was created with a section headed **"WHAT DOES NOT
TRANSFER AT ALL"**:

> `TTE_policy` measured as `[0, 900]` because a BTC15m window is 900s. A
> Gemini BTC5m window is 300s and a sports market runs for hours or days. **The
> NUMBER is about the instrument.**

and the commit message:

> Carrying either to a 300s Gemini window or a sports market that runs for
> days is a category error, not a hypothesis. Each lane declares its own, as
> OURS_ADAPTED.

So 300 was chosen **correctly, for the cohort that existed at the time**:
Gemini meant BTC5m, and `[0, 300]` meant *the entire contract*.

### Commit fdd19d06 — 2026-09-19 14:44:01Z — where it stopped being right

`BTC15m continuous shadow: the contract family is now a parameter` added the
BTC15m cohort **to the same lane spec**. The band was not re-derived, so a
number that meant "the whole window" on a 5-minute contract silently became
**"the last third"** on a 15-minute one.

Two days separate the two commits. Nothing in between re-examined the band.

## My error

I called `[0, 300]` "a MEASURED L5 parameter" in `lanes.py`, in
`contract_cohort.py`, in two test docstrings, in `L5_STATUS.md`, in
`LANES_PROSPECTIVE_1.md`, in several commit messages and repeatedly in
conversation. The artifact carries wording written specifically to prevent
that — *"never citable as one"* — and I cited it as one anyway.

It also propagated into a methodological choice: the BTC5m-vs-BTC15m habitat
comparison was restricted to `TTE <= 300` on the stated grounds that this was
"the only interval Model A can act in **because L5 measured it that way**".
The restriction itself is still correct — the clone genuinely cannot act
outside its band — but the justification was false, and it made an arbitrary
inherited constant look like a finding that the comparison had to respect.

The labels are corrected in code, tests and docs by this change. **The value
`[0, 300]` is untouched**, as instructed.

## Why it matters

L5's measured band is the whole 900s window and the clone quotes only the
final third of it. See `L5_TTE_SEGMENTS_1.md` for what L5 actually does in the
part we refuse to enter.
