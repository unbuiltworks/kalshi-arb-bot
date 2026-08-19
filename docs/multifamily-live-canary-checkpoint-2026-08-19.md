# MLB multifamily live-canary checkpoint — 2026-08-19

- Isolated from the running Hits canary.
- Families: Home Runs, Total Bases, Pitcher Strikeouts.
- Guard tests passed 6/6 before live arming.
- Read-only live discovery found 182 exact current Home Run keys, zero ambiguity, zero fee blockers; TB/K had no current exact live joins at that snapshot.
- One contract per leg, one submission attempt for the whole service, $5 hard service ceiling.
- Minimum modeled after-fee profit is $0.03; quote skew <=50ms; books <=100ms old; exact limits/depth must survive 50ms revalidation.
- Authorization expires 2026-08-19T21:15:00Z and Railway restart policy is NEVER.
- Classification remains LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB.
