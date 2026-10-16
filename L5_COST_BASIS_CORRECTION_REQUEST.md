# P0 — Cost-basis correction: prepared, and blocked on a verb that does not exist

**Status: PREPARED, NOT APPLIED.** Nothing was mutated. HALT stays latched,
`L5_SHADOW_ONLY` stays set, no gate, limit or threshold was touched.

## The finding you need first

**The protected mechanism you authorized me to use cannot carry this
correction.** I did not force it through and I did not go around it.

`CostStore.correct_settled_market` (`src/l5_live_execution.py:1738`) restates a
settlement **outcome**. It recomputes `pnl = redeem − cost` where `cost` is
read from `row["cost_total"]` — and it **never writes `cost_total`**. There is
no other correction verb: `repair_orphaned_market_key` remaps a key, and
nothing else in the codebase restates a cost.

So two separate things block this, and only the second is the one you already
knew about:

1. **`NO_COST_CORRECTION_VERB`** — no existing protected method can apply a
   cost-basis correction at all.
2. **`CORRECTION_WOULD_RELAX_LOSS_HALT`** — a *lower* cost yields a *higher*
   `settlement_pnl`, so `delta > 0`, which that method refuses by design. The
   guard is correct. A cost correction cannot avoid tripping it, because
   moving realized toward zero is exactly what a corrected overstatement
   does.

## What the correction contains

From the production audit on deploy `22327f62` (`activity_walk: 107 pages,
ended eof, complete true`):

| | |
|---|---|
| legs in scope | 49 |
| **to correct** | **47** |
| **preserved UNKNOWN** | **2** |
| cost before (established legs) | **$291.05** — $1/share upper bound |
| cost after (measured from venue fills) | **$81.3862** |
| measured fees inside that | reported per leg |
| **realized delta** | **+$209.6638, direction TOWARD_ZERO** |

The two preserved legs both fail `QUANTITY_MISMATCH`, and both are the
gross-versus-net artefact — the venue reports twice the ledger quantity:

| leg | venue qty | ledger qty |
|---|---|---|
| `cpc-btc-updown-15m-2026-10-16-0130z\|UP` | 2.0000 | 1.0000 |
| `cpc-btc-updown-1d-2026-10-15-2100z\|DOWN` | 4.0800 | 2.0400 |

Their cost stays **UNKNOWN** — not zero, and not the placeholder promoted to a
measurement.

## Ownership is unchanged

Every one of the 49 legs reads `UNKNOWN_OWNER` with `l5_owned_fills: 0`.
**Correcting a cost attributes nothing to anyone.** After this correction
`verified_l5_realized` stays `0` and `manual_realized` stays `null` with
`UNATTRIBUTED_RESIDUAL_NONZERO`. No unknown ownership becomes verified manual.

## A field that must not be "fixed"

Each venue fill row carries a `cost` that is **not our cost**. Example:
`qty 1.0000, price 0.8800, cost 0.12` — that is the complement
(`1 − 0.88`), not `qty × price`. The audit correctly derives our cost from
`lastShares × lastPx` and ignores the row's `cost`. A future reader
"correcting" the extraction to use `cost` would book the opposite side's
outlay on every leg.

## The evidence artifact

`correction_request()` in `src/l5_cost_basis_audit.py` generates this as
structured, immutable before/after evidence, derived from the same audit that
read the venue. It records per leg: `cost_before_usd` with provenance
`PLACEHOLDER_ONE_DOLLAR_PER_SHARE_UPPER_BOUND`, `cost_after_usd` with
`MEASURED_FROM_VENUE_FILLS`, the `fill_trade_ids` it was measured from,
`delta_usd`, the ownership counts verbatim, and `action` of `REPLACE_COST`,
`PRESERVE_UNKNOWN` or `NONE`. It sets `applied: false` and reports both
`blocked_by` reasons. 11 regression tests pin it (section 20).

It is a generator, not a transcript: it runs against the live audit so the
artifact can never drift from the venue data it claims to describe.

---

# The exact approval request

Two decisions. Both are yours; neither is implied by the P0 authorization,
which covered preparing and submitting, not building a new write path.

## Request 1 — authorize a new protected verb

`CostStore.correct_cost_basis(market, side, *, correction_id, expect,
measured_cost, fees, evidence)`, built to the same discipline as
`correct_settled_market`:

- **idempotent** by `correction_id` in a new `cost_basis_corrections` list;
- **refuses** unless the stored row still matches `expect` field for field
  (`LEDGER_DOES_NOT_MATCH_EVIDENCE`);
- **never overwrites** — the original row is copied verbatim into `superseded`
  and never edited, so the placeholder stays readable beside the measurement;
- **refuses a cost it was handed**: `measured_cost` must reconcile to the sum
  of the named `evidence` trade ids, or `COST_NOT_RECONCILED`. The caller
  cannot assert a cost;
- **refuses an upward correction** (`COST_CORRECTION_MUST_REDUCE`), so this
  verb can only ever replace an upper bound with a measurement;
- **refuses a quantity mismatch** (`VENUE_QTY_NE_LEDGER_QTY`), which is what
  preserves the two unknown legs by construction;
- **re-evaluates the loss gate after writing** and leaves HALT latched
  regardless, so a correction can never itself unlatch a halt.

## Request 2 — accept that realized moves toward zero

This is the substantive decision. Applying the correction changes:

```
account_wide_realized   -233.98540000  ->  -24.32160000
unknown_unattributed    -233.98540000  ->  -24.32160000
verified_l5_realized               0   ->             0   (unchanged)
manual_realized                 null   ->          null   (unchanged)
```

`-24.3216` is `-233.9854 + 209.6638`. It is a **less negative** number, and it
is less negative because the original was an upper bound, not because any loss
was erased. The $209.6638 was never a loss — it was the difference between
`$1/share` and what the account actually paid.

I am flagging the risk on this plainly: it is the correction that makes the
account look better, so it deserves the most scepticism. Three things argue
for it and one against.

**For:** the walk is complete (`ended: eof`); every leg reconciles to named
venue trade ids; the quantities match the ledger exactly on all 47.
**Against:** every leg is `UNKNOWN_OWNER`, so we are correcting the cost of
activity we cannot attribute. That does not make the measured cost wrong, but
it means this corrects the *account-wide* line and tells us nothing new about
L5's own performance.

**What it does not do:** it does not clear the loss gate. `verified_l5` stays
`0`, `manual` stays `null`, `identity_holds` stays `false`, and the 49 legs
stay unattributed. The submission gate stays `SHADOW_ONLY_LOCK` with HALT
latched. A smaller account-wide loss is not authorization to trade.

## What I need from you

Reply with either:

- **"Build the verb and apply the 47-leg correction"** — I implement
  `correct_cost_basis` to the contract above, test it, deploy it read-only
  first to emit the request, then apply it in one audited transaction and
  report the before/after from `realized_lines()`.
- **"Build the verb, do not apply yet"** — I implement and test it, and it
  stays unused until you say otherwise.
- **"Leave it"** — the $294.09 stays labelled a placeholder and the
  overstatement stays reported but unapplied.

No order, limit or gate changes under any of the three.
