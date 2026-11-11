# FAILED CLOSED before the first order — 2026-09-23

Authorization `OWNER-2026-09-23-GEMINI-Q1-AB-1` was **NOT installed**,
`AUTHORIZED_BUNDLE` was **NOT set**, no ledger was granted, no workflow was
dispatched, no order was submitted. Tree clean, `armed() == False`.

The pre-order verification found seven mismatches between the approved manifest
and the actual live dispatch path. Three are consequential, and one is an error
in the manifest I wrote.

| # | approved / claimed | actual | evidence |
|---|---|---|---|
| 1 | Probe A is a fresh 2-window run | **A's spec has already traded 11 windows** under COHORT-2, which the workflow marks **SPENT** | `COHORT-2026-09-22-BTC15M-COHORT2-W01..W11` all consumed; workflow names run 35721894867, stopped on drawdown 5.29/5.00, peak +1.47, 11 of 20 windows |
| 2 | $18 ceiling is **structural** via ledger exhaustion | **9 unused ledgers remain** on A's chain (W12–W20). The bound would be the `--max-windows` CLI flag, not exhaustion — up to 9 × $4.00 = $36 if that flag were wrong or dropped | `discover_chain` resolves the chain from what exists on disk |
| 3 | **BUNDLE_SHA stays `47ff1676…`** | **Granting fresh chains moves it.** `discover_chain` reads `spec.budget_cohort`, i.e. the `live_cohort` field in `live_probe_spec.py` — **a bundle member** | my manifest's "outside the bundle because no source file changed" was **wrong** |
| 4 | drawdown bound **remains disabled** | workflow passes **`--max-drawdown-usd 5.00`**, and it fired on the last run | workflow line 664 |
| 5 | economic-loss stop **$10.00** | workflow passes **`--max-loss-usd 8.00`** | workflow line 663 |
| 6 | Probe B runs 4 BTC5m windows | **no armed BTC5m cohort step exists.** The only armed cohort step hardcodes `COHORT_SPEC: btc15m_q1_cap4_w60_880_c2` and `--max-windows 20` | workflow lines 629–668 |
| 7 | expiry supplied at run time | both armed steps hardcode `GEMINI_CANARY_AUTH: "NO-AUTHORIZATION-IN-FORCE"` and **expiries already in the past** (2026-09-22T02:00Z, 2026-09-22T20:00Z) | workflow lines 558/567, 650/655 |

What did verify clean: the checkout ref is this branch; both spec hashes and
`BUNDLE_SHA 47ff1676…` are unchanged on a clean tree; all 15 bundle members
normalize; `armed()` is False.

## The three that matter

**Item 3 is my mistake.** I told you twice that a window budget is "granted
budget, not a parameter" and therefore outside both `spec_sha` and the bundle.
The first half is right — it is outside `spec_sha`. The second half is wrong:
the chain head lives in `live_probe_spec.py`, which is a bundle member, so
pointing a probe at a fresh chain necessarily moves `BUNDLE_SHA` off the value
you authorized. Your authorization is bound to `47ff1676…`; it cannot cover the
run it was written for.

**Item 2 follows from it.** Because a fresh chain would move the hash, the only
way to keep `47ff1676…` is to reuse COHORT-2's chain — which has nine unused
ledgers. The $18 ceiling would then rest on a CLI flag, not on the structural
exhaustion I described. That is exactly the "advisory, not structural" property
I told you it was not.

**Item 6 blocks Probe B outright.** There is no armed BTC5m path. Running it
needs a new workflow step, which is a change to the live dispatch path and was
not in what you approved.

## What I did NOT do

No authorization installed. No `AUTHORIZED_BUNDLE`. No ledgers granted. No
workflow edited. No dispatch. Nothing armed, nothing submitted.

## Minimal corrected path, for your approval — not executed

1. Grant **fresh** chains: `COHORT-2026-09-23-BTC15M-AB-A-W01..W02` (2) and
   `COHORT-2026-09-23-BTC5M-AB-B-W01..W04` (4). Fresh chains restore the
   structural ceiling: two ledgers cannot fund three markets.
2. Point each spec at its new chain head. **This moves `BUNDLE_SHA`**; I would
   compute the new value and bring it back for a fresh authorization.
3. Edit the workflow armed steps: install the authorization id, set a future
   expiry, `--max-windows 2` (A) and `4` (B), `--max-loss-usd 10.00`,
   `--max-drawdown-usd 0` to honour "drawdown remains disabled".
4. Add an armed BTC5m cohort step for Probe B, or accept four separate
   single-window dispatches through the existing generic armed step.
5. Re-verify and present: new `BUNDLE_SHA`, both `spec_sha` values (unchanged),
   the $18 arithmetic, and the ten arming prerequisites.

**A note on item 4 worth your attention:** honouring "drawdown remains
disabled" makes the run *less* protected than the last one, which stopped on
exactly that bound. I will set it to 0 because you specified it, but if you
would rather keep the $5.00 peak-to-trough stop, say so and I will leave it.
