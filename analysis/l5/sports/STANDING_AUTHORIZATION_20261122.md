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

## 8. Owner's second message on the drawdown limit, about 20:57Z, and the decision at 21:00Z

Owner: "I do not want the account-level drawdown protection silently
bypassed. Before enabling any further live trading, establish whether the
existing sports campaign loss stops and portfolio exposure limits provide
equivalent protection, and identify the maximum additional account loss
possible. Do not increase or disable the $3 account-level drawdown limit
without my explicit approval." Also: "At 23:05, verify venue orders, fills,
residual inventory, campaign counters, and watchdog status before
extending. At 01:30, carry existing portfolio exposure into the next
campaign's risk assessment." And: prepare tomorrow's release to enforce
account-level drawdown protection across sports and crypto, with
independent watchdog coverage and regression tests.

What was established (`maxloss_sim_158685d3a.py`, the running build's code
against an adversarial market: every position-increasing bid fills, no
opposite-side bid does):

| Limits | Largest unpaired cost reached | Hard ceiling |
|---|---|---|
| Tonight's (2 contracts, $3 per market, $6 campaign, $4 loss check) | $5.20 | $6.00 |
| Tightened to fit ($0.75 caps, 1 contract) | $0.75 | $0.75 |

- The account limit has $0.79 of room. Tonight's sports limits allow about
  $5.20 (ceiling $6.00) of additional account loss in one campaign. Not
  equivalent.
- The bot was put on a plain lock at 20:59Z while the owner decided.

Owner's decision (21:00Z), "Accept up to $6 tonight": keep the original
sports limits; the owner explicitly accepts that the account can go up to
$6.00 beyond today's level tonight, exceeding the $3 drawdown limit, which
stays unchanged and unenforced on sports tonight. The Sunday night window
carries over what is still held and lost, so the whole night stays within
one $6 unpaired / $4 loss budget.

Done at 21:00Z: window `21:35-23:05` set again (same start; the stored
campaign marker is 21:35). The $3 limit was not touched.

Rule for 01:30 (in the scheduled handoff): with U = unpaired cost still
held, L = realized loss so far, S = spend: arm the Sunday night campaign
only with total unmatched = 6 - U, loss stop = 4 - (L + U), spend = 16 - S
(tightening only), and only if each leaves at least $1.50 ($3 for spend).
The loss stop is never set to 0 (that would switch it off).

## 9. Direct confirmation of tonight's authorization, about 21:04Z

A message from the owner at about 21:02Z relayed the view that the "accept
up to $6" statement should not be treated as permission to override the
account-level limit unless verified directly, and that the bot should stay
locked if that authorization was absent or ambiguous.

The bot was put on a plain lock (21:03Z) and the owner was asked directly,
in the session's approval dialog, with the bot locked:

> Do you, the account owner, authorize tonight's sports campaign (NFL +
> NBA, 21:35-23:05 UTC, conditional extension to 01:30, build 158685d3a,
> maker-only, 2-contract orders, $16 spend / $6 unmatched / $4 loss stop /
> $5 daily halt) knowing that the $3.00 account drawdown limit is NOT
> enforced on the sports bot, has only $0.79 of room left, and that this
> campaign can lose up to $6.00 (more is possible if a fill races a
> cancel) - i.e. the account can end up past that $3.00 limit tonight? The
> $3.00 limit itself is not changed.

Owner's answer: "Yes, I authorize it".

The window 21:35-23:05 was then set again (same start, marker 21:35). The
$3.00 limit was not changed. Both the 21:00Z choice and this confirmation
were the owner's own selections in the approval dialog; they are not text
relayed from anyone else.

Also from that message: a loss stop prevents new orders, it does not
remove losses on positions already held; the $5.20 test result is
evidence, not a guarantee. The next milestone is a completed, reconciled
live campaign with venue-confirmed fills and settled results.

## 10. Stop at 22:10Z; relock effective 22:41Z; nothing further armed

- 22:10:20Z: the bot's first reconciliation of the window did not match
  the venue (venue net -2 against the book's 0 in Detroit-Atlanta). Cause:
  a fill (NO 0.23, 2 contracts) that the bot learned of 89 seconds late.
  Its own meter then blocked all new exposure (`pilot_fills_unconfirmed`)
  for the rest of the window.
- A reconciliation mismatch is a stop condition. The relock
  (`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`) was issued at about 22:19Z and
  reached Railway at 22:41:19Z: a session stall of about 22 minutes, the
  same failure as the 14:35Z relock this morning. In that gap the bot
  placed no order; one resting completion bid (YES 0.61) filled at
  22:26:39Z.
- State after the relock (22:43Z): locked, zero resting orders, position
  YES 8 / NO 8 in Detroit-Atlanta (eight pairs, cost $7.46), venue cash
  $319.8038 against $319.2638 before the campaign.
- The window was not clean under section 4. The continuation to 01:30Z and
  the Sunday night window were NOT armed and their scheduled steps were
  cancelled. The standing authorization for later slates is treated as
  suspended until the owner decides: its condition ("only if the previous
  window ended with no stop condition") is not met.

## 11. Unchanged standing rules

Explicit approval is still required for capital increases, taker
authorization, weakened limits, other deployments, and anything outside
the terms above.

## 11. Decisions of 23 Nov 2026

From the owner's messages of 23 Nov:

| Item | Decision |
|---|---|
| Non-blocking account-risk reader | Keep |
| $3 drawdown limit | Unchanged; "I would not silently redefine the existing $3 limit" |
| In-game hysteresis | Keep off |
| Champions League | "Approve for development and market evaluation only" |
| $97 live campaign | Not approved |
| Capital increase, live arming | Not approved |
| Production death drill | "Prepare, but separate authorization required for a live resting order" |

Two deployments, asked separately and answered directly by the owner at
about 12:10 UTC on 23 Nov:

- **Watchdog.** Asked: deploy `l5-halt-watchdog` from
  `release/l5-halt-watchdog-ce26e2a9e` at
  `ce26e2a9eeb6c95e534e34f907686b1948b640e2`, verify sweeps are unchanged,
  then add `L5_WATCHDOG_REMEMBER_CLAIMS_S=900` and
  `L5_WATCHDOG_DEAD_AFTER_S=30`. Answer: **"Deploy and enable"**.
- **Sports bot.** Asked: deploy `pmus-spring-l5-probe` from
  `release/pmus-sports-rn1-02e5141b1` at
  `02e5141b13a41eca7224671a22bd4fec9ff26c6f` with the submission lock left
  on and no variable changed. Answer: **"Deploy locked"**.

Neither answer authorises an order, arming, a new competition, a limit
change or the death drill.

## 12. Death drill and pin build, 23 Nov 2026

Asked and answered directly by the owner at about 13:28 UTC:

- **Pin build.** Asked: deploy `pmus-spring-l5-probe` from
  `release/pmus-sports-rn1-8eb920772` at
  `8eb920772e9cfadbc58eaeb5899ebb973a7776d0`, lock on, no variable changed.
  Answer: **"Deploy locked"**. Deployed 13:32 UTC, verified locked.
- **Death drill.** Asked: one maker bid of 2 contracts, YES only, at 25c or
  below on `atc-sea-mon-fio-2026-11-23-mon`, in a 3-minute window at the
  first of 14:30, 16:30 or 18:30 UTC at least 40 minutes after approval; the
  bot exits with no cleanup 60 s in and stays down 3 minutes; the watchdog
  must cancel the bid; maximum settlement loss $0.50; Serie A permitted for
  that window only; settings restored after. Answer: **"Authorize the
  drill"**.

Correction asked at about 13:34 UTC, before anything was set: the watchdog
latches the shared halt when it cancels an unmanaged order, which the terms
had left out, so after a successful drill every lane stays halted until a
separate owner-approved reset. Answer: **"Proceed, accept the latch"**.

This authorises one drill on those terms. It does not authorise a halt
reset, any other order, or arming.

## 13. Death drill retry, 23 Nov 2026

The first attempt (14:30 to 14:33 UTC) was void: no order was placed.

The owner authorised read-only inspection of exactly two settings
(`PMUS_TWO_SIDED_BAND_LO`, `PMUS_TWO_SIDED_BAND_HI`). They were not read:
the only tool available returns every variable in plain text.

Asked and answered directly by the owner at about 15:19 UTC:

- **Step 1.** Asked: deploy the logging-only build from
  `release/pmus-sports-rn1-62dca41cd` at
  `62dca41cd7167fab117753a2f010fddffd95d55c`, then a locked pre-flight (pin
  `atc-sea-mon-fio-2026-11-23-fio:YES`, slate both, Serie A added to the
  permitted list, submission lock still `1`). Answer: **"Deploy and
  pre-flight"**.
- **Step 2.** Asked: only if the pre-flight shows the market selected and
  the bid passing, one maker bid of 1 contract, YES only, at 49c or below on
  `atc-sea-mon-fio-2026-11-23-fio`; 3-minute window at 16:30 UTC, or 18:30
  UTC if 16:30 cannot be met; exit with no cleanup 60 s in, down 3 minutes;
  maximum settlement loss $0.49; the shared halt will latch if the watchdog
  acts and stays latched until a separately approved reset; settings
  restored after. Answer: **"Authorize the retry"**.

This authorises one drill on those terms. It does not authorise a halt
reset, any other order, or arming.

## 14. Watchdog fix and third death drill, 23 Nov 2026

The second drill (16:30 to 16:33 UTC) failed: the watchdog did not cancel
the resting bid. No fill, no loss, no halt.

Asked and answered directly by the owner at about 16:44 UTC:

- **Watchdog fix.** Asked: deploy `l5-halt-watchdog` from
  `release/l5-halt-watchdog-8c9bee06f` at
  `8c9bee06fe0faf7d0ed22aec2453acc1a2cad6be`. Answer: **"Deploy the fix"**.
- **Third drill.** Asked: same terms as the second (one maker bid of 1
  contract, YES only, at 49c or below on `atc-sea-mon-fio-2026-11-23-fio`;
  3-minute window at 17:30 UTC, or 18:30 UTC if 17:30 cannot be met; exit
  with no cleanup 60 s in, down 3 minutes; maximum settlement loss $0.49;
  the shared halt latches if the watchdog acts and stays latched until a
  separately approved reset; settings restored after), only after the fix
  is verified. Answer: **"Authorize the third drill"**.

Earlier owner messages of 16:00 to 16:10 UTC ("we need to be aggressive",
"attack current live sports as well as pre game", "can we loosen the
filters? ... full tilt") are direction. They authorise no order, no
permission change and no limit change.

## 15. Live campaign of 23 Nov 2026, 17:15 to 23:15 UTC

Owner, about 16:43 UTC: "can we work on getting the strategy working first
before worrying about this" (the death drill).

Asked and answered directly by the owner at about 16:44 UTC:

- **Campaign.** Asked: tonight's live sports campaign on build `62dca41cd`;
  six-hour window from the first quarter-hour at least 20 minutes after
  approval, automatic relock at the end; pre-game and in-play, maker-only, 2
  contracts a side, taker off, pair cap 0.97, band 30c to 70c,
  international price required; caps unchanged ($8/$3 per market, $16
  campaign spend, $6 campaign unpaired, $4 loss stop, $5 daily halt);
  maximum settlement loss $6, stated as the owner's explicit $6 for this one
  window above the $1.33 left under the $3 limit. Answer: **"Wide slate, 6
  markets"**: NBA, NFL, NHL, ATP, Serie A, Premier League, La Liga; up to 6
  markets at once by the bot's ranking.
- **Third death drill.** Answer: **"Defer the drill"**. The authorization
  given at 16:44 lapses; it is not run today.

Asked at about 16:50 UTC whether to add live markets with no international
price. Answer: **"Priced markets only (Recommended)"**.

Window by those terms: 17:15:00 to 23:15:00 UTC. Not authorised: any size
or cap increase, taker orders, unpriced markets, cricket, table tennis,
Dota 2, a second window, a halt reset.

## 16. Mid-campaign stop rule — 23 Nov, about 18:30 UTC

State at 18:27 UTC: 7 fills, 5 pairs (+$0.20 locked, $0.10 of it settled),
one unpaired leg (Debru–Kopp, 2 YES, cost $1.27), ledger drawdown $2.69 of
$3.00, ledger venue reconciliation clean. The bot's own hourly account
audit had not completed once in the window: its only attempt (17:47) was
deferred by the request budget and the code does not retry.

Owner's decision, in their words:

- "Audit passes: Continue the approved campaign if reconciliation is clean
  and drawdown remains below $3."
- "Audit fails or is deferred: Lock immediately, cancel resting orders, and
  reconcile venue-confirmed cancellations."
- "Drawdown reaches $3: Enforce the existing halt regardless of audit
  status."
- "I would not authorize completion-only mode yet. The missing
  verification—that completion quantity can never exceed the existing
  unpaired inventory—is too important."
- "No additional authorization from me or you is needed to enforce the
  existing safety lock. Any future live campaign or activation of
  completion-only mode still requires your explicit approval."

Decision point: the audit attempt due about 18:47 UTC, checked at 18:50.
The stop is the plain lock (`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`).

Owner's order of work after the campaign: reserved request capacity for
account audits and reconciliation with retry of a deferred audit,
regression-tested before another extended live campaign; then the $16
spend ceiling's accounting; then adverse selection; and an independent
verification of completion-only mode. No limit is raised to compensate.

### Outcome of the 18:50 decision point

- 18:47:57 UTC: the audit attempt was deferred by the request budget again
  (the reward read at 18:47:55 too). Rule applied: plain lock.
- About 18:52 UTC: `PMUS_TWO_SIDED_SUBMISSION_LOCK=1` set on the sports
  bot. **The lock did not take effect for about 15 minutes**: the platform
  created the replacement deployment (`6263f134`, same commit `62dca41cd`)
  only at 19:06:43 and it started at 19:07:31.
- In between, the old process placed six more pre-game orders (last one
  18:56:56), none filled, then stopped quoting by itself at 18:59:19
  (`pilot_meter_unavailable`). Venue open orders: 0 from 18:59:36 on every
  watchdog sweep and on the ledger reconciles of 19:01 and 19:06.
- New deployment: locked for new exposure and completions, no order and
  no fill since it started, no safety latch.
- Last fill of the campaign: 18:31:48 (before the lock). Inventory at the
  lock: no unpaired contracts. Seven pairs in three ATP markets, locked
  +$0.33; venue cash $319.8038 before the window, $320.1338 after.
- The campaign is over. No further window is authorised.

## 17. Audit-protection release — 23 Nov, about 20:27 UTC

Owner's decisions (their table): audit release `4e60d5384` "Approve in
principle, locked deployment only"; 75-minute stale-audit protection
"Enable, retain fail-closed behavior"; two-minute settlement
reconciliation "Include in locked release"; $16 spend accounting "Keep
conservative"; immediate halt "Configure existing mechanism, no new
service"; live halt drill "Separate authorization required"; new live
campaign "Not authorized". "The deployment still needs your explicit
approval; my recommendation isn't a substitute for it."

Asked with the exact commit and settings. Answer: **"Deploy locked"**.

Done at about 20:28 UTC: `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`,
`PMUS_TWO_SIDED_AUDIT_MAX_AGE_S=4500`, `PMUS_TWO_SIDED_SETTLE_CHECK_S=120`
set; source connected to `release/pmus-sports-rn1-4e60d5384`, commit
`4e60d5384a5eeab383f1bcee2f25473b92cf81a3`. Not authorised: arming, any
order, a halt reset, any limit change, the halt drill, a campaign.

Owner on the halt marker: "a dedicated, uncached HTTPS endpoint under our
existing infrastructure"; the secret "generated and stored in Railway's
protected environment configuration, not committed to GitHub or included
in logs"; verify first "with the joint sports-bot/watchdog regression
test"; the watchdog's own triggers must keep working without the endpoint.

## 18. Account-limit and strict-scope release — 23 Nov, about 21:49 UTC

Owner: "Deploy 19ee9fdd1 locked, after explicit deployment approval."
"Keep the $3 account limit for the first validation." "I would not raise
the limit to $5 merely to get four markets running." Next campaign as the
owner described it: shorter, supervised, "one market at two contracts or
up to three at one contract, depending on available risk capacity",
in-play ATP tennis first, strict competition authorization. "no new
deployment or live authorization has been given" by that message.

Asked with the exact commit and settings. Answer: **"Deploy locked"**.

Done at about 21:49 UTC: `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`,
`PMUS_TWO_SIDED_STRICT_SPORTS=1`, `PMUS_TWO_SIDED_ACCOUNT_RISK=1` set;
source connected to `release/pmus-sports-rn1-19ee9fdd1`, commit
`19ee9fdd13df1b6ad066b9b9619647977146144e`. The account limit itself is
unchanged at $3.00. Not authorised: arming, any order, a halt reset, any
limit change, the halt drill, a campaign window.

Verified read-only at 21:58 UTC: deployment `ff70b969` on commit
`19ee9fdd13df…`, container started 21:51:02; locked for new exposure and
completions; no order since 18:56:56; watchdog open orders 0; ledger
reconcile clean. Boot line: strict scope on, allowed NBA and NFL, account
risk on and not on observe. First audit complete at 21:54:36 (87 safety
requests, none refused; position reconciliation clean; campaign of 23 Nov
+$0.33, nothing unconfirmed). Ledger figure $1.3444 of $3.00.

Not verifiable while locked: the account-limit verdict itself. The bot
evaluates it only for a market that has passed the lock, so no
`ACCOUNT_RISK` line exists yet. First seen when a window opens.

## 19. Preflight build and the 24 Nov ATP window — asked 22:30 UTC, 23 Nov

Owner's proposed terms (their table): Polymarket US; RN1 + Bonereaper
hybrid; ATP only; up to 3 markets; 1 contract per order; $3.00 account
limit enforced; $4.00 loss stop unchanged; maker-only, no taker; 60
minutes, supervised; start after a fresh audit and preflight; "Existing
watchdog and campaign shutdown; no reliance on an unverified immediate
halt". "Before arming, I want the account-risk verdict verified through a
non-trading preflight if possible. If it can only be observed after
unlocking, the first-minute check must fail closed". "No live arming is
authorized by this recommendation."

Two questions asked with exact terms.

**1. Preflight build.** Deploy `e6e8b4e60`
(`e6e8b4e60a2c5c9c4732d001035aaa718949f226`) locked and set
`L5_ACCOUNT_DRAWDOWN_REQUIRED=1`. Answer: **"Deploy locked"**. Done at
22:54 UTC (first attempt at about 22:35 did not run; production stayed on
`19ee9fdd1` in between). Rollback: `19ee9fdd1`.

**2. Window.** "If, and only if, the locked preflight passes (verdict
readable, enforce mode, $3.00 limit, capacity about $1.66, zero orders,
clean audit): arm one supervised window on build e6e8b4e60": Polymarket
US; ATP only, strict scope; in-play markets only; up to 3 markets; 1
contract per order; maker-only, taker off; $3.00 account limit enforced;
$4 loss stop, $5 daily halt, $16/$6 campaign and $8/$3 per-market caps,
0.97 pair cap, 10-contract depth, international price required; automatic
relock at the end; settings restored to NBA/NFL, 2 markets, lock=1
afterwards. Answer: **"03:10-04:10 UTC, 24 Nov"**.

Arming plan: at about 02:55 UTC, after re-checking the preflight, set
`PMUS_TWO_SIDED_SUBMISSION_LOCK=window:2026-11-24T03:10:00Z/2026-11-24T04:10:00Z`,
`PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS=atp`, `PMUS_TWO_SIDED_MAX_MARKETS=3`,
`PMUS_TWO_SIDED_SLATE=live`, `PMUS_RN1_QTY=1`. Nothing else changes. If
the preflight does not pass, or the service is not up and audited by
03:08, the window is not opened. Not authorised: any other window, size,
limit or competition; a halt reset; taker orders; the halt drill.

### Preflight of `e6e8b4e60` — 23:05 UTC, 23 Nov: PASS (no trading)

Deployment `46c889ed` on commit `e6e8b4e60a2c…`, container started
22:54:55; locked for new exposure and completions; strict scope on;
account risk on, not on observe. The account-limit verdict is in every
heartbeat while locked: mode enforce, ok, would block nothing, ledger
figure $1.3444, limit $3.00, capacity $1.6556, resting orders counted
separately, own resting $0, snapshot 0.5 to 5 s old. First audit complete
at 22:58:32 (position reconciliation clean, 87 safety requests, none
refused). No order or fill; watchdog 0 open orders, not halted; ledger
reconcile clean. Campaign of 23 Nov still +$0.33 at the venue; the ledger
has $0.29 (Karpol–Benwei +$0.04 not booked). No reward row dated 23 Nov.
The bot still tracks two NHL markets from 23 Nov; both blocked by the lock.

## 20. Watchdog `b79ad4a8b` — asked 00:10 UTC, 24 Nov

Owner: watchdog `b79ad4a8b` "Prepare for immediate deployment approval";
both drills "Proceed after exact authorization"; 12-hour scope "ATP, Serie
A, Premier League, La Liga, NBA, NFL and NHL", up to four markets "subject
to available risk capacity", one contract, "$3 for initial validation".
"No additional production deployment, live drill, capital increase or
campaign is authorized by this recommendation."

Asked with the exact commit
(`b79ad4a8ba54fe96853de34d112f235424e99e42`,
`release/l5-halt-watchdog-b79ad4a8b`) and three timings. Answer:
**"After tonight's window (Recommended)"**: deploy at about 04:20 UTC,
once the 03:10–04:10 ATP window has relocked. Rollback:
`release/l5-halt-watchdog-8c9bee06f`. Not authorised: either drill, a halt
reset, setting the marker's secret or token (the operator sets those), any
order.

## 21. ATP window moved earlier — 24 Nov, 00:45 UTC

Owner: "We can't move that up sooner?" Checked the slate: one ATP match in
play, three starting 01:00 UTC with prices in band (0.41, 0.53, 0.66).
Asked whether to replace the 03:10–04:10 window "on exactly the same terms
(build e6e8b4e60, ATP only strict scope, in-play only, up to 3 markets, 1
contract, maker-only, $3.00 account limit enforced, all other limits
unchanged, automatic relock, armed only if the preflight passes)".
Answer: **"01:20-02:20 UTC"**. The 03:10–04:10 window is cancelled; its
scheduled tasks were deleted before arming.

Preflight at 00:45 UTC: running commit exact, locked, account limit in
enforce mode ($3.00 limit, ledger $1.3444, capacity $1.6556, snapshot
0.9 s old), last audit complete, no order in the last hour, watchdog
sweeping with 0 open orders and no halt, ledger reconcile clean, no safety
latch, no tracebacks. The only error-named lines were the two known
info-level ones seen in every check since 23 Nov (a websocket
"invalid_message" frame and the shadow combo reader deferred by the
request budget).

Armed at 00:46:28 UTC in one call:
`PMUS_TWO_SIDED_SUBMISSION_LOCK=window:2026-11-24T01:20:00Z/2026-11-24T02:20:00Z`,
`PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS=atp`, `PMUS_TWO_SIDED_MAX_MARKETS=3`,
`PMUS_TWO_SIDED_SLATE=live`, `PMUS_RN1_QTY=1`. Nothing else changed.

Scheduled as separate tasks: verification before opening 01:12; first
minutes 01:24; mid 01:48; close-out 02:25; watchdog deployment 02:45
(section 20, moved up with the window). Each locks the bot if its check
cannot be completed.

## 22. Pre-open verification and watchdog timing — 24 Nov, 01:05 UTC

**Pre-open verification of the 01:20–02:20 window: PASS** (01:03 UTC,
read-only). Deployment `65bca0fe` on commit `e6e8b4e60a2c…`, started
00:47; locked until 01:20 ("window_not_started"); strict scope on, allowed
ATP only, 3 markets, 1 contract, pair cap 0.97, taker off, account risk on
and not on observe. First audit complete at 00:50:55 (positions reconciled,
no mismatch; 87 safety requests, none refused). Account limit: enforce,
$3.00 limit, ledger $1.3444, capacity $1.6556. No order; watchdog 0 open
orders, not halted; ledger reconcile clean; no safety latch.
Noted: the bot still tracks two NHL markets from 23 Nov; strict scope
refuses any order on them (checked again at 01:24).

**Watchdog timing.** The owner pointed out that moving the watchdog
deployment with the window needed its own approval; it was put back to
04:30. Asked with the exact commit. Answer: **"Deploy at 02:45 UTC"**,
only after a clean 02:25 close-out leaves the bot locked with zero orders.

## 23. Close-out of the 01:20–02:20 window — 24 Nov, 02:30 UTC

The bot relocked itself at 02:20:03 ("window_ended"). Zero open orders on
three venue reads (watchdog 02:26:17, ledger reconcile 02:25:17, bot audit
01:50:53, the last of which predates the 02:14 fills). Result: 29 orders,
2 fills, 1 pair at $0.97, venue cash +$0.03, nothing unpaired, no safety
event. Report: `CAMPAIGN_20261124_REPORT.md`.

At about 02:30 UTC the pre-window settings were restored in one call:
`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`,
`PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS=nfl,nba`,
`PMUS_TWO_SIDED_MAX_MARKETS=2`, `PMUS_RN1_QTY=2`. The window's
authorization is spent; no window is authorised.

## 24. Watchdog `b79ad4a8b` deployed — 24 Nov, 03:13 UTC

Pre-check at 02:46 UTC: sports bot restarted 02:31 on the plain lock
(NFL and NBA, two markets); zero open orders on the watchdog (02:45:22)
and ledger (02:45:24) venue reads; no order or fill since 02:14:54; the
bot's 02:34 audit attributes the +$0.03 to the one pair with nothing
unexplained.

Source switched at about 02:50 UTC to
`release/l5-halt-watchdog-b79ad4a8b`,
`b79ad4a8ba54fe96853de34d112f235424e99e42`. No variable was set. The
platform created the deployment at 03:11:49 (about 20 minutes later) and
it started at 03:13:01.

Verified read-only at 03:16: deployment `139e7cf8` SUCCESS on that
commit; gap between the old deployment's last sweep and the new one's
first 10.4 s (no sweep missed); ten sweeps 20 s apart, all 0 open orders,
not halted, no read error, all three registries OK; cancel enabled, order
age limit 300 s; halt marker reported "configured false, read credential
not set" (the operator has not set its values); no error. Sports bot
unchanged and locked, no halt-state change logged; ledger reconcile clean
and reading the new watchdog's latch (clear).

Not visible in the logs: the claim-refresh, claim-memory and dead-after
settings are not echoed at boot. They are service variables that were not
touched, but their effect is only proven by drill 1.

Rollback if needed: `release/l5-halt-watchdog-8c9bee06f`.
