# Owner authorizations for live sports windows — given 22 Nov 2026

Recorded so a later session works from the owner's own terms. These were
chosen by the owner in this session's approval dialogs (about 19:58Z and
20:08Z on 22 Nov 2026). Nothing here raises a limit.

## 1. Tonight, first window (armed 19:59Z)

"Arm, NFL + NBA": 21:35–23:05 UTC, sports bot `pmus-spring-l5-probe` on
`158685d3ab0b446ac31387bf3ba32bb7bc5659fd`, allow list `nfl,nba`.

At 20:12Z the owner wrote "I approved starting asap". The window's start
was moved to 20:16 UTC (end unchanged, 23:05 UTC); nothing else changed.

## 2. Tonight, second window — conditional

"Add Sunday night, if clean": if the 21:35–23:05 window ends with no stop
condition, arm a second window 01:30–04:30 UTC (23 Nov) for
Minnesota–San Francisco and the late NBA games, same limits, without
asking again. If anything is off: stay locked and tell the owner.

## 3. Standing authorization through Sunday 29 Nov 2026

"Standing, while clean": arm a window for every NFL slate through Sunday
29 Nov (Monday night if listed, Thursday 26 Nov, Sunday 29 Nov), only if
the previous window ended with no stop condition, and report after each.

Terms stated to the owner when it was given:
- NFL + NBA only (allow list `nfl,nba`).
- Each window at most 6 hours, relocking itself.
- Limits exactly as tonight: $16 campaign spend, $6 campaign unmatched,
  $8 and $3 per market, $4 campaign loss stop, $5 daily halt, 2-contract
  maker-only orders, at most 2 markets, pair cap 0.97.
- Any change to limits or size still needs the owner's explicit approval.

It does not cover: other sports, crypto, taker orders, larger size, more
markets, a different build, or anything after 29 Nov.

## 4. What "clean" means (all must hold at the end of a window)

- The bot relocked itself and the venue shows zero resting orders.
- No safety latch; reconciliation clean; no watchdog halt.
- Every order was post-only and at most 2 contracts; no taker fill.
- Only `nfl` and `nba` markets were traded.
- Campaign and per-market caps were respected.
- The campaign loss stop and the daily halt did not trip.
- The account ledger's figures agree with venue cash for settled markets.

If any one fails: do not arm the next window; stay locked; report; wait
for the owner.

## 5. Unchanged standing rules

Explicit approval is still required for capital increases, taker
authorization, weakened limits, other deployments, and anything outside
the terms above.
