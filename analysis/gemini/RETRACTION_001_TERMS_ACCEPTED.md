# Retraction 001 — "terms_accepted = False" was a provenance error

**Status: RETRACTED.** The claim is withdrawn. It is preserved here rather
than deleted, because the trail of what was believed and why is the point.

## What was claimed

On 2026-09-21 I reported, from a readiness dry run, that the venue's preflight
said `terms_accepted expected True, got False`, and told the owner their
Gemini account needed a terms/account step. The owner performed that step.

## What was actually true

The dry run executed **locally**, where no Gemini credentials exist:

```
credentials present: False   (key len 0, secret len 0)

terms_status() -> {"readable": false,
                   "verdict": "NO_CREDENTIALS",
                   "blocks_orders": true,
                   "why": "run where the Gemini credentials live"}
```

The preflight compares fresh reads against frozen assumptions and rendered an
**unreadable** read as `got False` in its discrepancy line. I repeated that
summary as if it were the venue's answer about the account.

## The correct chain of provenance

```
local unauthenticated read -> NO_CREDENTIALS -> terms state UNRESOLVED
```

Not FALSE. Never FALSE. `UNREADABLE` and `NO_CREDENTIALS` are not evidence
about the account; they are evidence about where the process ran.

## Why this is the codebase's own documented trap

"Unreadable is not empty" appears throughout this package -- in
`WorkingOrders.reconcile`, in `metadata.positions`, in
`no_existing_canary_exposure` ("an unread order book is not an empty one").
The identical substitution, one layer up, in a summary line. I walked into the
trap this repository exists to avoid, and it cost the owner a real-world
action taken on my wrong report.

## Standing rule from this

A credential-dependent result must carry its provenance and must distinguish
at minimum **TRUE / FALSE / UNREADABLE / NO_CREDENTIALS**. `UNREADABLE` and
`NO_CREDENTIALS` must never be rendered or summarized as `FALSE`. No account
action may be requested of the owner unless an **authenticated** run on the
credentialed runner explicitly establishes that it is required.
