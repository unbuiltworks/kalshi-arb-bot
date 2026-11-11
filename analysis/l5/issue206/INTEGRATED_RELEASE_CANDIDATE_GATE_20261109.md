# L5 offline release candidate verification — 2026-11-09

**Scope: offline only.** Issue #206 deployment freeze remains controlling. This branch must NOT be merged into a deployment-triggering branch, deployed, armed, or used to place orders without a separate explicit owner approval.

## Provenance
- Candidate starts at `3c8abb176f82da57cadb1b13ddbf9cab289b6129` (PR #208 branch).
- The production deployment `c300a6c4` runs `6576964e667e5b40ae7e969c3a4b59180dbb2be0`; the candidate is NOT deployed.
- The append-only ledger implementation `src/l5_two_sided_shadow.py` has identical Git blob SHA `ebc18c8220d159321308b31dcaf8a6ffd25348e9` on PR #208 and #210 branches.
- Ledger regression test `tests/test_l5_shadow_ledger_retention.py` has identical blob SHA `8b30aea24b104e18cd713411aa3f15b6256632e8` on both branches. No cherry-pick is necessary for those files.
- GitHub combined status and PR-associated workflow runs for candidate head returned no checks; this is **NOT** a passing CI result.

## Required verification before release
1. Run targeted ledger migration, corruption, restart, duplicate-finalize, rollover tests on the exact candidate SHA; run full suite against a matched baseline and enumerate failing IDs.
2. Exercise actual executor integration with simultaneous UP/DOWN orders, one-leg partial fills, second-leg completion, maker repricing, cancel/fill race, reconnection, settlement and exact-order cancellation.
3. Verify held inventory + all outstanding orders' worst-case exposure against the $1 aggregate cap, $5 daily loss halt, watchdog and immutable authorization.
4. Replay candidate and deployed policy on identical out-of-sample books using conservative queue assumptions, actual venue fees, adverse selection and residual settlement; no profitability claim from modeled pairs.
5. Confirm read-only live telemetry, current ledger save latency, order states and settled P&L. Verify one-time migration on a copied ledger, not the production volume.
6. Produce explicit GO/NO-GO, SHA, test counts, maker fill evidence and infrastructure cost. Do not lift issue #206 freeze implicitly.

## Known economics
- Latest documented account-wide settled realized P&L: -$15.6109 before $2.13 confirmed rebates; do not treat this as a newly reconciled figure.
- Maker-fill model sensitivity remains large. Positive modeled P&L is not settled realized P&L.
