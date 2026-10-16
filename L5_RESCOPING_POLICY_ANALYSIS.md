# The contradiction, and what a legitimate rescoping would actually require

**Not implemented.** This is the analysis you asked for.

## First: you caught a real error in how I framed it

Both of my statements were true, but I put them together in a way that was
misleading, and the misleading part was mine.

- **The backstop (`max_unresolved_unknown_usd`) is purely additive.** It sits
  after the account-wide ceiling and can only ever *refuse*. It cannot permit
  anything. Proven, eleven tests.
- **"Rescoping through that backstop is the path to trading"** — this was
  sloppy. The backstop is *not* the path. It does no permitting and never
  could.

**What would actually permit trading is a separate change: feeding
`cumulative_realized` from a verified-L5-scoped figure instead of the
account-wide one.** The backstop is a guardrail I argued should be bolted on
*alongside* that change — not the mechanism that opens the door.

And I should be blunt about the consequence, because "retaining account-wide
protections" can be made to sound costless and it is not: **the rescoping's
entire function is to stop the account-wide realized loss from gating new
orders.** That is a relaxation. The question is whether it is a *legitimate*
one, and of what exactly.

## What is actually in play — four separable controls

The account-wide realized ceiling is one of four, and rescoping touches only
that one:

| control | source | would rescoping relax it? |
|---|---|---|
| account-wide **realized loss** (`max_cumulative_loss_usd` on `realized_today()`) | the ledger | **YES — this is the whole point** |
| account-wide **exposure** (`max_total_exposure_usd` $5, `max_usd_per_market` $5) | positions + resting orders, manual and L5 alike | **No.** Reads positions, not realized. Untouched. |
| **available cash** (`INSUFFICIENT_AVAILABLE_CASH`) | venue `/v1/account/balances` | **No.** Untouched. |
| **run-scoped cash** (`max_cash_deployed_usd` $1, `NO_RECYCLING`) | the run | **No.** Untouched. |

So the honest scope of the relaxation is narrow: one of four, and only its
*scope*, not its existence. That is the strongest thing that can be said for
it. It is not nothing.

## The legitimate distinction — and why it is weaker than it sounds

There *is* a principled line here, and it is not "L5 vs manual." It is this:

> **A loss that predates the decision point cannot be re-caused by a new
> order. Gating new orders on it is not risk control — it is a permanent
> lockout. The loss that a new order can influence is the loss *since* a
> verified baseline.**

That argues for measuring the account-wide **delta since the baseline epoch**
rather than the lifetime total. `l5_baseline` already exists for this:
`baseline_t 2026-10-16T02:45:12.565947+00:00`.

> **SUPERSEDED BY THE INSPECT RUN — see the correction at the end of this
> document.** The split below is the *uncorrected* one. Once the 47-leg
> correction is applied the post-baseline figure flips to a **profit**, and
> the delta approach becomes the enabler rather than a dead end. I have left
> the original reasoning in place because the correction only makes sense
> against it.

**I checked the split before recommending it, and on UNCORRECTED data it does
not help:**

| | | share |
|---|---|---|
| account-wide realized total | −$233.98540000 | |
| **pre**-baseline | −$79.01000000 | 33.8% |
| **post**-baseline, not attested | **−$154.97540000** | **66.2%** |

(Reconciles exactly: −79.01 + −154.9754 = −233.9854.)

**Two-thirds of the loss happened *after* the baseline was struck**, from
unattributed activity. So a delta-based control gates on −$154.98 and refuses
at every sane ceiling:

| ceiling | delta loss $154.9754 |
|---|---|
| $1 / $5 / $20 / $25 | **REFUSE at all four** |

The "historical versus new" distinction is real, and on this account it buys
almost nothing. I would rather tell you that than present a design that looks
principled and quietly fails to deliver permission.

## The one asymmetry that *is* genuine

Attribution is impossible backwards and trivial forwards.

`verified_l5_realized` is 0 not because the strategy earned nothing, but
because nothing is attributable: all 49 legs are `UNKNOWN_OWNER`,
`l5_owned_fills: 0`, `our_client_order_ids: 1`, and the venue does not record
who placed a historical fill.

But our `client_order_id` is **derived, not random** —
`"l5f-" + sha256(f"{action_key}|{attempt}")[:24]`. **Every future order is
attributable by construction.** So a prospective scope is sound in a way a
retrospective one can never be.

That is the defensible version of what you are asking for: not "exclude the
historical losses," but **"gate new activity on new activity, which we can
prove we caused, while leaving every control that bounds *forward* risk
exactly where it is."**

## The actual code change, named precisely

Five changes. **None implemented.**

1. **`InventoryStore.account_wide_realized_since_baseline()`** — a new read
   returning `realized_today() − account_realized_at_baseline`, or **`None`**
   if the baseline snapshot lacks an authoritative account-wide figure.
   `None` must be a refusal, never a zero. *(The baseline currently stores
   `pre_baseline_realized_total`; whether that is a sufficient anchor needs
   checking before anything is built — I have not verified it is immutable
   and source-backed to the standard this would require.)*

2. **`ValidationGates.__init__`: add `max_account_wide_delta_loss_usd`** —
   a new ceiling, *alongside* `max_cumulative_loss_usd`, not replacing it.

3. **`refuse()`: add the delta check** positioned with the other account-wide
   checks, with `ACCOUNT_WIDE_DELTA_UNAVAILABLE` on a missing figure. It must
   be additive in the same way the backstop is.

4. **The three protected call sites** (`l5_pmus_live_runner.py` 3372, 3818,
   3850) pass **both** figures: `cumulative_realized` = the delta, and
   `l5_scoped_realized` = `verified_l5_realized`, with **both** ceilings
   configured. Today a test asserts all three read `realized_today()`; that
   test would need rewriting, and rewriting it is exactly the moment to
   review this.

5. **`max_unresolved_unknown_usd` set and enforced** as the precondition.

## The honest weak point in my own proposal

The backstop bound would have to be **≥ $24.33** to permit anything even
*after* the 47-leg correction, and **≥ $233.99** before it. So the backstop
does not meaningfully constrain this case — **it would be set to a number
chosen to clear the figure it is supposed to bound.** That is a guardrail in
name. I am not going to recommend it as the safety story.

## What I actually recommend

**None of the above, yet.** The sequence that makes a decision legitimate
rather than engineered:

1. **Run the INSPECT dry run** (deploying now) and read the real 47-leg plan
   against fresh venue data.
2. **Apply the correction** if the plan is clean. Realized becomes
   −$24.3216 and, critically, **the pre/post-baseline split must be
   recomputed from the applied deltas** — not assumed from today's 33.8/66.2
   ratio, because the corrected legs fall in both periods. That recomputed
   post-baseline figure is the only number on which a delta ceiling can
   honestly be judged.
3. **Then decide**, with that number in hand, between:
   - a delta ceiling above the recomputed post-baseline loss — legitimate
     *only if* that figure is genuinely small;
   - accepting that this account's history blocks live validation until the
     unattributed activity is reconciled by other means;
   - or a fresh account, where attribution is clean from the first order. I
     have not costed this and it may be out of scope, but it is the one route
     that needs no relaxation at all.

I would rather reach step 3 with a real number than argue for a policy now.

## State

Nothing implemented, nothing activated. `L5_MAX_UNRESOLVED_UNKNOWN_USD`
unset, the three protected call sites unchanged and still reading
`realized_today()`, HALT latched, `L5_SHADOW_ONLY` set, all limits unchanged.


---

# CORRECTION — the INSPECT run flips this conclusion

I wrote above that the baseline-delta approach "buys almost nothing." **That
was true of uncorrected data and is false once the correction is applied.** I
had flagged the recomputation as necessary; doing it reverses the finding.

The INSPECT run (deployment `9ab48e4e`, `activity_walk {pages 107, ended eof,
complete true}`) gives the per-leg deltas, so the split is now measured rather
than assumed. **40 of the 47 corrected legs are pre-baseline and 7 are
post-baseline**, and the post-baseline 7 carry the bulk of the overstatement
— they are the `$50`, `$5` and `$5.28` placeholders that were actually
`$1.00`, `$0.40` and `$0.32`.

| | before | **after the correction** |
|---|---|---|
| pre-baseline | −$79.01000000 | **−$30.73620000** |
| **post-baseline (the delta)** | −$154.97540000 | **+$6.41460000** |
| account-wide total | −$233.98540000 | −$24.32160000 |

Correction deltas: $48.2738 pre-baseline + $161.39 post-baseline = $209.6638,
reconciling exactly to the reported total.

**The entire remaining loss is pre-baseline. The period a new order can
influence is net positive.** A delta ceiling would gate on a profit, so it
permits — on the principled ground, not an engineered one.

And one property I did not expect: **both preserved-UNKNOWN legs are
pre-baseline** (`...2026-10-15-1930z|UP` and `...2026-10-15-2100z|DOWN`). So
**the delta carries no unknown component at all.** The figure a delta ceiling
would read is fully established from venue fills, which is a far stronger
basis than the backstop I was reaching for.

## What this does and does not fix

**Fixes:** the "historical lockout" problem. −$30.74 of pre-baseline loss
cannot be re-caused by a new order, and gating on it is a permanent lockout
rather than risk control. The delta is the controllable quantity and it is
clean.

**Does not fix — and this still matters:**

1. **+$6.41 is not our profit.** It is *account-wide* and still unattributed:
   `verified_l5_realized` is 0 and `manual_realized` is null. "The account has
   not lost money since the baseline" is a much weaker claim than "the
   strategy makes money," and only the first is supported.
2. **It depends on applying the correction.** Unapplied, the delta is
   −$154.98 and refuses everywhere. The correction is the enabler; the policy
   change alone does nothing.
3. **−$30.74 of pre-baseline loss remains, $2.04 of it still UNKNOWN.** The
   two preserved legs keep unknown cost. The account-wide total ceiling should
   not be deleted — the pre-baseline figure stays visible and reported.

## Revised recommendation

The sequence is unchanged in order but the destination is now reachable:

1. **Apply the 47-leg correction.** The plan is clean: 47 of 49, 0
   reconciliation failures, every leg reduces cost, 69 distinct venue trade
   ids with no duplicates. This is a prerequisite, not an optimisation.
2. **Then** add `max_account_wide_delta_loss_usd` as a ceiling on the
   baseline delta — enforced *alongside*, never replacing, the existing
   account-wide total ceiling, which stays configured and reported.
3. Keep exposure ($5 total, $5 per market, manual and L5 alike), available
   cash, and the $1 `NO_RECYCLING` run cap exactly as they are. Rescoping
   touches none of them.

**I withdraw the backstop as the safety story.** I said its bound would have
to be set to a number chosen to clear the figure it was meant to bound, and
that remains true. The delta ceiling is better precisely because the number it
reads is established from fills and contains no unknown. `max_unresolved_
unknown_usd` can still be set to bound the *pre-baseline* unknown ($2.04 after
correction), which is a real constraint rather than a cosmetic one — but it is
not what makes the decision legitimate.

**Still not implemented.** This is the analysis; the change needs your
approval and a security review of the delta ceiling's placement.
