# Credentialed PMUS incentive re-audit — 2026-10-07T03:02:51Z

Ran on `pmus-readonly-recovery` (deployment `d1e64a5b-7c40-4791-9d6c-92cf480d501b`,
pinned to commit `b5a1de5ad5a2b2876671b3e462329414f7a5915e`), GET-only image
with **no order path**. No `PMUS_Q1_*` value was read, written or cleared.

## FIRST, AN EVIDENCE-HANDLING FAILURE I CAUSED

The runner wrote its full JSON to **`/tmp/PMUS_INCENTIVES_REAUDIT.json`
inside a container with no mounted volume**, so the detailed artifact
**died with the container**. What survives is the runner's stdout verdict,
captured in Railway's deploy log and transcribed verbatim below.

That is the same class of mistake as the lost S3 cohort artifact, and I
made it after writing the fix for that one. The stdout verdict carries the
headline numbers but **not** the per-row detail, the
`measurement_window` block, or the `activities.ended` / `rows_detail_truncated`
flags the runbook says to check. **Any absence claim below is therefore
unsupported**, and a re-run that persists the artifact is required before
these figures are treated as a closed measurement.

## The captured verdict, verbatim

```
"observed_credits_earnings_endpoint": "102 rows, total 10.5100000000000001"
"venue_credited_on_our_fills_usd": "10.3600"
"venue_charged_us_on_our_fills_usd": "2.8600"
"our_fills_with_a_credit": 825
"our_fills_at_or_above_rebate_floor": 944
"observed_credit_rows_in_activities": 8
"trade_rows_carry_fee_or_rebate_fields": true
"published_programs_covering_our_markets": 0
"rebate_at_our_executed_size_after_rounding": "0.00"
"reading": "only observed / venue-confirmed credits are realized;
            published programs and formula estimates are labelled and
            never counted"
```

## Delta against the prior window — two labelled rows, never summed

| window | maker credits on our fills | earnings-endpoint total | fills with a credit |
|---|---|---|---|
| 2026-08-14 → 2026-09-14T22:59:21Z | $9.14 | $7.04 rewards (44 PAID rows) | 737 of 1,174 |
| **lifetime → 2026-10-07T03:02:51Z** | **$10.36** | **$10.51 (102 rows)** | **825 of 944 above floor** |

**The second row is LIFETIME, not the three-week increment.** The runner
computes cumulative totals from the activity walk, and the
`measurement_window` block that would have let me subtract cleanly is in
the lost file. Read as a cumulative delta the three weeks added roughly
**+$1.22** of credited maker rebate and **+$3.47** on the earnings
endpoint — stated as arithmetic on two cumulative figures, **not** as a
measured increment.

**So the three-week hole is narrowed, not closed.** It is still not a zero.

New line the prior audit did not have: **`venue_charged_us_on_our_fills_usd`
$2.8600.** Net venue-confirmed fee position on our fills is therefore
**+$10.36 credited − $2.86 charged = +$7.50**, and that netting is mine,
not the venue's — the two are separate lines and the audit keeps them so.

## A6 RECONCILIATION — order `CYKE4XSQ0YGW`

The audit does not join by order id, so this is reconciled by mechanism
rather than by a row for that id:

**`rebate_at_our_executed_size_after_rounding`: `$0.00`.**

Attempt 6 filled **0.01 shares**. The PMUS maker rebate is
`round_half_even(0.0125·C·p·(1−p), $0.01)`, and at 0.01 shares the raw
amount is far below half a cent, so it **rounds to exactly zero**. The
audit states the floor directly: a rebate is **$0.00 below 1.62 shares**
after cent rounding. Independently, 825 of 944 fills carry a credit —
i.e. **119 fills earned nothing**, which is what the floor does to small
clips.

This closes the question the fill left open, and the answer is
unflattering:

- **liquidity role: still UNRESOLVED** — no `isAggressor`, no execution
  objects, and the audit adds nothing on it;
- **commission: $0.00**, consistent with the fill record's `fees_usd 0`;
- **maker rebate earned: $0.00, structurally**, not by accident;
- **settlement / cash confirmation: NOT ESTABLISHED.** The lost artifact
  is where a settlement join would have been, and I will not infer one.

**Nothing from Attempt 6 is realized.** The 0.01-share DOWN position's
outcome remains unobserved by us.

And the economics point stands harder than before: at our minimum clip the
rebate is **identically zero**. The incentive lever does not exist at
0.01 shares — it starts at 1.62. Every breakeven calculation that assumed
a per-fill rebate at probe size was assuming a payment the cent floor
forbids.

## Truncation, explicitly

The MLB program-list scan reports **"400 pages walked, TRUNCATED — a
sample, which cannot support an absence claim; and the list is an
ACTIVE-ONLY view, so it is what is running now and says nothing about
what has run before."** So `published_programs_covering_our_markets: 0`
is **not** evidence that no program covers our markets. I raised
`PMUS_PROGRAM_LIST_MAX_PAGES` to 400 and it still truncated at 40,000
entries.

Whether the **activity** walk truncated is **unknown**, because that flag
was in the lost file. Until a persisted re-run says otherwise, treat the
credit totals as a **lower bound**.

## Restoration proof

| | fingerprint |
|---|---|
| before | `caff3d1ff6f2f6920ec8583f85a3a081c46d6b779ed4649c98db39dcb3097836` |
| after | `caff3d1ff6f2f6920ec8583f85a3a081c46d6b779ed4649c98db39dcb3097836` |
| identical | **yes** |

Fingerprint covers source (repo, branch, `commitSha: null` — branch-following
restored, the pin removed), build (builder, watch patterns, build
environment, Dockerfile path), deploy (start command, restart policy,
runtime, region/replicas), the original variable **names**, and volume
mounts.

**Residual difference, disclosed rather than hidden:** two extra variable
rows now exist, `PMUS_PROGRAM_LIST_MAX_PAGES` and
`PMUS_PROGRAM_LIST_PAGE_PAUSE_S`, both set **empty** so the recovery
script reads neither. The tool sets variables and cannot delete rows;
emptying is the same disarm convention used for the owner values. Deleting
the two rows is a dashboard action if wanted.
