# PMUS BTC15m late-favorite / opposite-longshot — CLOSED: NOT PROMOTED

**Status: `NOT_PROMOTED` at discovery stage. Closed 2026-10-02 by owner decision.**

## What this status means, precisely

The hypothesis **failed the discovery-stage promotion test**. It is **not**
proven unprofitable, and this file must not be cited as such. What was
established is narrower and stated exactly:

- On 19 independent discovery markets the calibration residual **shrinks** as
  horizon-scaled distance grows (+0.086 at `|z|` 1.0–1.5 → +0.010 at `|z| ≥ 2.0`),
  which is the opposite of the direction the hypothesis requires.
- The binding constraints are **structural, not statistical**: the venue's 0.99
  price cap leaves a maximum gross edge of 1.0 c and a taker breakeven true hold
  rate of 99.07%; 39% of late states have no executable favorite price at all;
  and the opposite-longshot expression is payoff-, fee- and queue-identical to
  the maker-favorite order, so no cheaper route to the exposure exists.
- The discovery sample is far too small to prove or disprove the underlying tail
  probability. **The question is left open, not answered.**

## Decisions recorded

| | |
|---|---|
| promote to validation | **NO** |
| consume clean OOS windows | **NO — explicitly declined** |
| begin dedicated 31–114 day accrual for this hypothesis | **NO — explicitly declined** |
| branch, preregistration, states, results | **PRESERVED** |
| active PMUS experiment, its completeness gate, its reserved windows | **UNTOUCHED** |

No dedicated data accrual, service, variable, deployment or subscription was
created for this hypothesis, and none should be. Further dedicated validation is
**low priority absent materially new evidence**, because the structural evidence
above would still bound the economics even if the sample were large.

## Preserved artefacts

| file | what it preserves |
|---|---|
| `PREREGISTRATION.md` | the frozen protocol: checkpoints, `z` definition, `|z|` buckets, fill standards, anti-overfitting rules |
| `windows.json` | window classification + the reserved denylist (enforced by name and by tape membership) |
| `states_nooutcome.json` | candidate states **hashed before** settlement was joined, sha256 `fbe773a7a4d0680de…` |
| `states_joined.json` | the same states with settlement joined afterwards |
| `PHASE0_SOURCE_AUDIT.md` | source/provenance table, dedup-losslessness proof, data gaps |
| `FINDINGS_DISCOVERY.md` | all Phase 1–4 results, every number labelled DISCOVERY |
| `scripts/pmus_late_fav_*.py` | reproduction path for every table |

## Reopening criteria

Reopen only if **materially new evidence** appears. Any one of these qualifies;
none of them is a reason to start accrual on its own schedule:

1. **A venue change that lifts the 1-cent ceiling** — a finer tick in the tails
   (as Kalshi does, tapering to 0.001), a higher maximum price, or a fee change
   that moves the 99.07% taker breakeven materially.
2. **Evidence that PMUS maintains separate per-side books with independent
   queues.** This is the single cheap check still outstanding: it is the only way
   the opposite-longshot expression could be genuinely cheaper rather than the
   same order. One read of the current full-L2 recorder output settles it.
3. **Clean cap-state windows arriving as a free by-product** of another lane's
   capture — never a dedicated collection effort — reaching ≥651 independent
   markets, which is the ~0.998-hold-rate test.
4. **A measured cap-state hold rate durably above ~99.5%** on BRTI settlement
   with sub-minute reference across ≥20 days.
5. **Evidence that maker fills at the cap are not adversely selected**, which
   requires a sample containing actual losses; the discovery sample had none, so
   `ADVERSE_SELECTION_DELTA = +0.0000` is **unanswered, not favourable**.

If reopened, the preregistration above is binding unchanged, or a new block is
opened and says so.

## Where the evidence pointed instead

Phase 4 is the more important result and is carried forward as the primary line:
L5's own canonical history shows no fills in the final 60 s, same-side additions
going to the **cheapening** side, 98.4% pair completion, and a run-length signal
that is ~92% price with a chronologically unstable sign. Rated at the PMUS fee
schedule his tape is negative pre-rebate and positive only once the maker rebate
is credited. That points at **continuous inventory accumulation plus maker
compensation**, not directional prediction — and those figures are themselves
now subject to source verification before anything is built on them.
