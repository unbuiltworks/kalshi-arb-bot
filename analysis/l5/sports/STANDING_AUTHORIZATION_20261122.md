# Owner authorizations for live sports windows — given 22 Nov 2026

Recorded so a later session works from the owner's own terms. These were
chosen by the owner in this session's approval dialogs (about 19:58Z and
20:08Z on 22 Nov 2026). Nothing here raises a limit.

## 1. Tonight, first window (armed 19:59Z)

"Arm, NFL + NBA": 21:35–23:05 UTC, sports bot `pmus-spring-l5-probe` on
`158685d3ab0b446ac31387bf3ba32bb7bc5659fd`, allow list `nfl,nba`.

At 20:12Z the owner wrote "I approved starting asap". The window's start
was moved to 20:16 UTC (end unchanged, 23:05 UTC); nothing else changed.

At 20:32Z the start was put back to 21:35 UTC. Reason: the bot's campaign
marker had stayed at 21:35, so the campaign spend cap, unmatched cap and
loss stop would not have counted anything traded before then. In the 16
minutes it was open the bot sent one order (Baltimore–Carolina YES 0.37,
2 contracts, post-only), which the venue rejected; there were no fills.
The defect is fixed on the dev branch (`47099e532`), not deployed.

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
- Campaign accounting is verified (owner, 22 Nov about 20:45Z: the next
  window is conditional on "a clean close-out and verified campaign
  accounting, rather than automatically proceeding if any safety
  discrepancy remains"): the campaign marker equals the window's start for
  the whole window; every order of the window is at or after it; the
  bot's campaign spend, unmatched and market count agree with its own
  ORDER and FILL lines; the cash change equals what the meter expects.

If any one fails: do not arm the next window; stay locked; report; wait
for the owner.

## 5. Owner instructions of 22 Nov, about 20:45Z

- Keep the 21:35 UTC start; do not move the window again tonight.
- Verify the marker and campaign counters before opening.
- No new build tonight. The marker correction is to be deployed while
  locked, after tonight's campaigns, on the owner's approval of the exact
  release SHA.

## 6. Continuation of tonight's campaign — authorized about 20:50Z

Owner, in their own message: "I explicitly authorize extending tonight's
existing L5 NFL/NBA live campaign with a continuation window from
2026-11-22 23:05 UTC through 2026-11-23 01:30 UTC, subject to the safety
conditions below."

Terms:
- Same build `158685d3a`; NFL and NBA only; maker-only; 2-contract orders;
  $8 and $3 per market; $16 campaign spend; $6 campaign unmatched; $4
  campaign loss stop; $5 daily halt; watchdog and all other protections.
- "The continuation does not authorize additional capital or reset any
  cumulative limits. The $16 campaign ceiling and other campaign-level
  protections must apply across both periods together."
- "If the fix requires a new money-capable production deployment, stop and
  request my separate explicit deployment approval." "Do not change the
  currently running build mid-campaign without that approval."
- At the 23:05 boundary, before configuring: campaign reconciled; all
  orders and positions accounted for; watchdog healthy; no safety latch or
  halt; campaign accounting carries forward without resetting; original
  exposure and loss limits still enforced; no unsafe gap or bypass. "If any
  condition fails, remain locked and report the exact blocker."
- Sunday night window 01:30-04:30: "Prepare a clean transition into that
  window, subject to its existing authorization and a successful safety
  handoff. Do not silently reset account-wide or daily loss protection at
  the boundary."
- Report after each window: SHA and mode; campaign boundaries; orders
  submitted, accepted, rejected, cancelled, filled; YES/NO inventory;
  capital committed and unmatched; campaign spend and loss counters;
  venue-confirmed settled P&L; confirmed rewards and rebates; total
  realized net; watchdog and reconciliation; any blocker.

How it is done (proof: `proof_window_extension_158685d3a.py`, run on the
running build's code):
- The continuation is ONE window with the SAME start:
  `window:2026-11-22T21:35:00Z/2026-11-23T01:30:00Z`. The campaign marker
  stays 21:35, so first-period orders stay in the campaign: committed
  spend, unmatched, the every-step worst-case loss check and a latched
  loss stop all carry over. No new build is needed.
- It must NOT be configured as a window starting 23:05: the marker would
  move and the first period would drop out of the counters.
- The Sunday night window is a new window and therefore a NEW campaign
  with its own $16 / $6 / $4 counters (its original authorization). A
  latched campaign loss stop still blocks it. The $5 daily halt is per UTC
  day and starts a new day at 00:00Z by design.

## 7. Account drawdown limit — owner decision about 20:55Z

Finding (from code): the account ledger's $3.00 portfolio drawdown limit
($2.21 used) is enforced on the ledger's own BTC orders and published to
the BTC 5-minute lane. The sports bot never reads it; its account check
covers only the daily and lifetime loss figures and runs in observe mode.
The limit values unpaired contracts at zero, so about $0.80 of unpaired
sports inventory exceeds it.

Owner chose "Trade under campaign limits": tonight the sports bot is
bounded by its own campaign limits, and the $3.00 account drawdown figure
does not gate it and will read as exceeded while it holds unpaired
inventory. This covers the 21:35 window and the continuation to 01:30 if
every other check passes at 23:05.

Open item for a later build, on the owner's approval: enforce an
account-level limit on the sports bot, with an allowance the owner sets.

## 8. Unchanged standing rules

Explicit approval is still required for capital increases, taker
authorization, weakened limits, other deployments, and anything outside
the terms above.
