# E1 LIVE COHORT — fresh session, owner GO 2026-10-08

Owner authorization: "OWNER GO — FRESH E1 LIVE COHORT", all seven preflight
checks passed, "for the corrected E1 implementation only."

| field | value |
|---|---|
| session id | `E1-COHORT-20261008T005816Z-051888d8` |
| latch stem | `E1-COHORT-20261008T005816Z-051888d8.0cb842db28f9a1af` |
| SHA | `4cff4c8d32d10af8f24abbd97ec4eff7e64dc590` |
| fingerprint | `b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1` |
| invariants | 163 |
| series gate | `cpc-btc-updown-15m` |
| service | `l5-live-probe-oneshot` `84aa9999-6d7a-4980-9a87-96694d8178dd` |
| project / env | `50fb7357-...` / `f3642d27-...` (production) |
| deployment | `74f404b1-4b51-4d9a-aa25-2d08930e2f47` |
| start command | `python3 -m scripts.l5_e1_cohort_run` |
| restart policy | `NEVER` — money-moving sessions never self-relaunch |

## Pre-arming verification, all source-read

- 343/343 package tests green on this SHA.
- Working tree clean; local HEAD == `origin/claude/august-l5-forensic-replay-788cml`
  == Railway pinned `commitSha`. Three independent readings of the same SHA.
- `spec.assert_frozen()` passes; `E1_FINGERPRINT` equals the owner's value.
- Frozen limits read back from the booted spec and matched one by one against
  the owner's list: qty `0.13`, first leg `<= 0.60`, fee-inclusive pair cost
  `<= 0.96`, 20 attempts/window, 12 windows, `1.56`/window, `5.00` cohort-wide
  forward-looking ceiling including unresolved residual exposure.
- Maker/post-only: `maker_first`, `post_only_participate_dont_initiate`, and
  E9 requires the price strictly below the live ask so post-only cannot take.
- Fail-closed verification before submission: `main()` refuses with
  `FINGERPRINT_NOT_PINNED_TO_THIS_BUILD` (exit 2) and `SERIES_GATE_NOT_SET`
  (exit 2); `eligible()` adds `E1_FINGERPRINT_NOT_PINNED_TO_THIS_BUILD`;
  `assert_frozen()` runs at every decision.

## Fresh cohort, both prior cohorts preserved

The progress and authorization ledgers are single append-only files whose rows
carry `session_id`, and the latch is scoped by a hash stem of the session id.
A new session id therefore reads zero rows (0/12) and claims fresh slots,
while every row of `E1-COHORT-20261007T1530Z` and
`E1-COHORT-20261007T1849Z` stays in place, unmodified. Nothing was reset and
nothing was cleared; `reset_path_exists` is false and no code here may clear a
slot. The stem was checked distinct from both prior sessions and from XR2W's
`xr2w-a6-2026-10-07-3095633d`.

## Namespace isolation

Only the five `PMUS_E1_*` keys were set. Q1, EXEC1, CANARY and CASE1 were not
touched and remain blank. `boot()` calls
`assert_no_foreign_executor_authorization()` before the arming gate, so a
foreign armed executor would refuse the boot outright.

## Launch observation — the process did not stay up

`74f404b1` armed and reached the network: `api.polymarket.us` resolved three
times (`NOERROR`), three HTTPS flows to `104.18.38.40:443` between
01:01:23.38Z and 01:01:24.53Z. A disarmed run does none of that. All network
then stopped at 01:01:24.53Z and logging stopped at 01:01:39Z with Railway
dropping 5702 messages at its 500 logs/sec limit, so the decisive
`ARMED`/final-report lines are not retrievable from the log stream. No output,
no DNS and no flows afterwards.

Ground truth is therefore the durable volume, not the logs. Read read-only
after the process had exited.

**Nothing in this file is a claim of profit.** No fill, settlement or realized
P&L is asserted here; the labels remain
historical replay -> shadow -> live probe -> submitted -> filled -> settled ->
realized.
