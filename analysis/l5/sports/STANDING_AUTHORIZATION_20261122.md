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

## 25. Final release candidates deployed locked — 24 Nov, about 03:35 UTC

Owner: configure the non-secret watchdog values "Yes"; watchdog
`09e94cc9d` "Prepare for deployment with channel-loss detection enabled";
sports `12d5d3daf` "Adopt as the final combined release"; operator setup
"Reduce to the two secrets". Two further requirements, both met before
asking: on a channel timeout the bot must also cancel resting
exposure-increasing orders (it already did; now proven by a test), and a
404 on the marker must not be taken as "no marker" when it may be an
access failure (watchdog `f6a5900eb`, which supersedes `09e94cc9d`: a 404
counts as absent only when a known file beside the marker is readable).

Asked with exact commits and settings. Answers:

- **Watchdog: "Deploy and set the five values".** Done: source
  `release/l5-halt-watchdog-f6a5900eb`,
  `f6a5900eb831ab752209d9ae7c39dabf42fc3e30`; set
  `L5_EMERGENCY_HALT_URL`, `L5_EMERGENCY_HALT_PROBE_URL`,
  `L5_EMERGENCY_HALT_ACCEPT=application/vnd.github.raw`,
  `L5_EMERGENCY_HALT_SERVICE=pmus-spring-l5-probe`,
  `L5_EMERGENCY_HALT_CHANNEL_MAX_DOWN_S=120`. No secret was set by this
  session; the mechanism stays off until the operator sets
  `L5_EMERGENCY_HALT_SECRET` and `L5_EMERGENCY_HALT_BEARER`.
  Rollback: `release/l5-halt-watchdog-b79ad4a8b`.
- **Sports: "Deploy locked, 12-hour ceiling".** Done: source
  `release/pmus-sports-rn1-12d5d3daf`,
  `12d5d3daf78efcc919f0060c63a922561cb0dc80`; set
  `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`, `PMUS_RN1_PAUSED_RELEASE_S=600`,
  `PMUS_RN1_RESELECT=1`, `PMUS_RN1_GAP_HOLD_S=20`,
  `PMUS_RN1_KEEP_DEEPER_TICKS=1`,
  `PMUS_TWO_SIDED_SHARED_HALT_MAX_AGE_S=120`,
  `PMUS_TWO_SIDED_WINDOW_MAX_S=43200`. Rollback:
  `release/pmus-sports-rn1-e6e8b4e60`.

Not authorised: any order, arming, either drill, a halt reset, any limit
change, a campaign. A connection drop interrupted three of the four calls
on the first attempt; each was repeated once and confirmed.

## 26. Drill 1 (bot process death) authorized — 24 Nov, about 04:03 UTC

Owner, typed: "Authorized: drill 1, 05:00–05:10 UTC 24 Nov (fallback
05:30–05:40), on these terms."

Terms as put to the owner:

| Term | Value |
|---|---|
| Window | 05:00:00–05:10:00 UTC; fallback 05:30:00–05:40:00 only if the redeploy is not live by 04:55 |
| Sports build | `12d5d3daf78efcc919f0060c63a922561cb0dc80` |
| Watchdog build | `f6a5900eb831ab752209d9ae7c39dabf42fc3e30` |
| Market | `aec-atp-andmar-radpap-2026-11-23`, YES only (pre-game, scheduled start 08:00 UTC) |
| Order | one maker bid, 1 contract, three ticks behind the best bid (0.44 at 0.47 / 0.48), never outside 0.30–0.70 |
| Bot exit | 60 s into the window, no cleanup; down until 60 s after it |
| Pass | venue shows that exact order cancelled by the watchdog within 90 s of the exit; no fill; no other order |
| Worst case | the bid fills first: at most $0.70 |
| Stop | production pre-flight must show the market open, pre-game, selected, start not within 30 min of the window end, shared-halt reading fresh; otherwise nothing is armed, no retry |
| After | bot relocked, settings restored; shared halt stays latched until a separate owner-approved reset |

Temporary settings (restored afterwards): pin, 1 contract (back to 2),
three-tick depth (back to 1), slate both (back to live), ATP added to the
permitted list (back to NFL and NBA), both unpaired caps 0.60 (back to 3
and 6). Market count left at 2: the carried-over paired tennis market holds
one slot and the pin allows it no new exposure.

Drill 2: owner chose the watchdog halt variable as an interim validation,
"not a substitute for the fast emergency stop"; it needs its own exact
authorization after drill 1's halt reset. The signed-marker stop must be
validated "before unattended 12-hour operation".

## 27. Drill 1 result — 24 Nov, 05:00–05:10 UTC (written 11:25 UTC)

Sports `12d5d3daf`, watchdog `f6a5900eb`. Source: production logs of the
sports bot, the watchdog and the ledger.

| Time (UTC) | Event |
|---|---|
| 04:06 | locked production pre-flight: market selected, pre-game, YES edge passes, only the lock blocking |
| 04:15 | account audit complete, ok |
| 04:52 | venue: market open, not closed, start 08:00, 0.50 / 0.51 |
| 05:00:01 | bot reads shared halt PERMITTED (first read on this build) |
| 05:00:02 | one order, YES 1 @ 0.47 (three ticks behind 0.50), id ending `55P1` |
| 05:01:00 | bot exits, no cleanup |
| 05:01:12 | watchdog: bot unreachable, using its last ownership read (12 s old), 1 open order |
| 05:01:32 | watchdog selects the order (executor unreachable), sends the cancel with no error, latches the halt `UNMANAGED_RESTING_ORDER`; its immediate re-read still showed the order NEW with 0 filled |
| 05:01:52 | watchdog: 0 open orders at the venue |

Result: **pass, with one gap in the evidence.** The order left the venue's
open orders between 33 and 53 s after the exit, inside the 90 s limit. No
fill: filled quantity 0 at the re-read, buying power $320.1638 before,
during and six hours after, venue positions 0 at the 11:17 reconcile. No
other order. The gap: the watchdog's re-read came before the venue showed
the cancel, so no log line carries the order's terminal state; "cancelled"
rests on the open-order list, the zero fill and the unchanged cash.

Afterwards: the bot restarted at 05:11 into the expired window (locked).
The session's worker was down from about 05:03 to 11:12, so the drill
settings were restored only at 11:12 (deployment `8a6b6afe`, plain lock,
pin off, 2 contracts, depth 1, live slate, NFL and NBA, caps 3 and 6,
death drill off). No order was possible in between. Halt latched since
05:01:32; latch digest at 11:17 `4004b056…3021693`; reset needs the
owner's separate approval.

## 28. Halt reset after drill 1 — 24 Nov, 11:28 UTC

Owner, typed: "Approved: reset the halt latched at 05:01:32 UTC 24 Nov.
Keep the sports controller locked throughout. Defer the interim
Railway-variable drill. Prioritize fast emergency-stop validation and
preparation of the 12-hour live campaign. No additional development unless
a critical defect is discovered."

Done by the existing procedure (watchdog startup hook), deployment
`03361a74`. Digest re-read at 11:23:58, unchanged. `L5_HALT_BOOT_RESET`
cleared at 11:28:21.01 UTC: prior latch digest `4004b056…3021693`, reason
`UNMANAGED_RESTING_ORDER`, reset id `halt-reset-20261124-d1-7c3e9a41b2f6`
(spent). Machine-verified at the clear: three registries readable, 0 live
orders, submission disabled on each; venue 0 working orders, no positions.
Sweeps at 11:33 attest halt false, 0 open orders. Reset settings disarmed
afterwards without a further restart. Sports bot locked throughout.

Drill 2 by the watchdog variable: deferred by the owner. Still required
before unattended 12-hour operation: the signed-marker stop, which needs
the operator's two secrets (marker mechanism still reads "configured
false" at the 11:28 boot).

## 29. Fast-stop credentials set; wrong-signature check passed — 24 Nov, 14:50 UTC

Operator set the two watchdog values (deployment `572232d3`, 14:46 UTC,
same build `f6a5900eb`). Boot line: marker "configured true", read
credential set, service `pmus-spring-l5-probe`, timeout 3 s. I saw neither
value.

Wrong-signature check (no order, sports bot locked): test marker committed
to `ops/halt-marker` at 14:49:00 (`af39fe36d`), signed with a throwaway
key. Watchdog at 14:49:17, 17 s later: `MARKER_SIGNATURE_INVALID`,
`acted false`, repeated each 20 s poll. Marker removed 14:50:32
(`c2208c581`). Sweeps at 14:50:52 and 14:51:12: halt false, 0 open orders,
no channel alarm. This proves the watchdog reads the private file with the
token and rejects a bad signature. Not yet proven: a valid marker halting
and cancelling (the signed-marker drill, needs its own authorization).

## 30. Signed-marker drill authorized — 24 Nov, about 14:55 UTC

Owner, typed: "Authorized: signed-marker drill, 16:30–16:40 UTC 24 Nov, on
these terms. Prepare the exact 12-hour campaign authorization in parallel.
Once the drill passes and the halt is reset, prioritize starting the
campaign without further delay. No additional engineering or strategy
experiments unless a critical defect appears."

Terms: sports `12d5d3daf`, watchdog `f6a5900eb`; market
`aec-atp-chdp-leosto-2026-11-24`, YES only (pre-game, start 19:25 UTC);
one maker bid, 1 contract, three ticks behind the best bid, inside
0.30–0.70; window 16:30:00–16:40:00 UTC; the operator publishes a validly
signed marker 2 to 7 minutes in; pass = order gone at the venue within
90 s of the commit, no fill, no order after the halt, latch still present
after the bot restarts; worst case $0.70; no arming and no retry if the
locked pre-flight fails; afterwards relocked, settings restored, halt
latched until a separate reset approval. The campaign itself is not
authorized by this.

## 31. Signed-marker drill: market changed — 24 Nov, 15:52 UTC

The locked pre-flight at 15:29 refused `aec-atp-chdp-leosto-2026-11-24`
(`no_international_price_yet`; none exists for it) and nothing was armed.
My error: the market was chosen from the venue price list without the full
selection check. Five alternatives were then run through the full check.

Owner, typed: "Authorized: same signed-marker drill, market
aec-atp-benwil-matalv-2026-11-23, 16:30–16:40 UTC on 24 Nov. All
previously approved limits remain unchanged. Prepare the exact 12-hour
campaign authorization now. Once the drill passes and the halt is reset,
proceed directly to requesting campaign approval. No additional tests or
development unless a critical defect appears."

Market at 15:32: open, pre-game, start 19:30 UTC, international price
present, 0.55 / 0.56. Pin and the 16:30–16:40 window set at 15:53 in one
change (the bot stays locked until 16:30; the pre-flight is read in that
locked period).

## 32. Signed-marker drill stood down before the window — 24 Nov, 16:03 UTC

Owner at about 16:00: no terminal available to sign the marker; "If the
current drill cannot proceed, let the window expire safely and keep the
controller locked." The window was removed before it opened: plain lock
and normal settings restored at 16:03 (deployment `08e6dfae`). No order
was placed. The drill authorization of sections 30–31 is spent; a new
window needs a new authorization. Open: a browser-only way for the
operator to sign (GitHub Codespaces proposed; nothing built).

## 33. 12-hour supervised campaign authorized — 24 Nov, about 16:25 UTC

Owner, typed: "Authorized: 12-hour supervised campaign, 17:00 UTC 24 Nov
to 05:00 UTC 25 Nov, on these terms, with the signed emergency stop
unverified. Preserve all existing risk limits and automatic shutdown
protections. Prioritize profitable inventory accumulation, sustained
liquidity provision and reward capture. Report confirmed realized net
profit, paid rewards, inventory and capital utilization. No additional
development or strategy experiments during the campaign. Stop if any
existing safety control fails." The signed-marker drill was deferred by
the owner beforehand ("Defer the signed-marker drill").

Terms: sports `12d5d3daf`, watchdog `f6a5900eb`, no build change. Window
17:00:00 UTC 24 Nov to 05:00:00 UTC 25 Nov, relocks itself. ATP, Serie A,
Premier League, La Liga, NBA, NFL, NHL; pre-game and in-play; 1 contract;
up to 4 markets; maker-only; pair cap 0.97; campaign spend 16 / unpaired
6; per-market spend 8 / unpaired 3; campaign loss stop 4; daily halt 5;
account limit 3.00 enforced; depth 10, international price, band
0.30–0.70, stale-audit block; quoting as deployed (pause hold 600 s,
reselect, gap hold 20 s, keep deeper 1 tick).

Disclosed and accepted: signed emergency stop unverified and no signed
marker held; manual stop is the plain lock (1 to 15+ minutes); watchdog
proven once on process death; supervision by this session at +5 min and
every 30 min, not guaranteed. Lock without waiting on: reconciliation or
audit failure, safety latch, order above 1 contract, taker order, order
outside the competitions, unexplained cash, watchdog error.

Baseline before the window: buying power $320.1638; account figure
$1.3144 of $3.00; zero open orders; zero positions (11:17 reconcile);
halt clear. Settings set at 16:26.

## 34. Campaign stopped: account-limit check defect — 24 Nov, 18:49 UTC

Plain lock set 18:49:01, effective 18:50:16 (deployment `e7f1b743`).
Watchdog 18:50:36: 0 open orders, no halt. Reason: owner's rule "Stop if
any existing safety control fails."

Defect (confirmed in logs and code): the bot's account-limit verdict is
cached for 10 s (`PMUS_TWO_SIDED_ACCOUNT_RISK_TTL_S`), and its capacity
figure includes only the bids resting when the verdict was computed. Each
new opening bid is checked against that cached capacity alone, so several
bids placed within one cache period each pass. From 18:27:47 the bot
looped about every 25 s: reading ok with nothing resting → place two to
four opening bids → next reading over the limit → pull them → repeat.
Worst-case totals (account figure + resting opening bids) on the over
readings: 3.06, 3.36, 3.17, 3.34, 3.33 (repeatedly), 3.23, against 3.00.
The account figure itself never exceeded 3.00 (peak 2.2644). Had two such
bids filled inside one period, it would have.

Campaign 17:00–18:50 UTC, venue-confirmed: 6 fills, all 1 contract, maker,
ATP. Two pairs (0.55+0.39, 0.57+0.39): +$0.10 in cash, both markets
settled. Two unpaired NO legs open at the lock: Juagom–Bengeo 1 @ 0.44,
Samher–Yanale 1 @ 0.61 ($1.05); they ride to settlement (the plain lock
also stops completions). Cash $319.2138 at 18:36 against $320.1638 at the
start. No oversize, taker or out-of-scope order; no latch; audits at
17:44 clean.

Not changed: any limit. Proposed $5 / $30 stage: not activated.

## 35. Account-limit correction prepared — 24 Nov, 19:10 UTC

Owner, about 18:55: "Proceed immediately with the account-limit
correction" (reserve each submitted order immediately including in-flight
and uncertain; no reuse of cached capacity; release only after confirmed
cancellation, rejection or reconciliation; regression-test simultaneous
submissions, concurrent fills, partial fills, stale readings; eliminate
the place-and-cancel cycle; preserve everything else). "Prepare the
corrected build for locked deployment and provide the exact SHA and
regression results. Prepare the $5 account limit / $30 campaign spend
configuration for separate authorization, but do not activate it.
Reconcile the remaining inventory through settlement." Owner's correction
to my figures: campaign trading result ranges from −$0.95 to +$1.05
including the settled +$0.10.

Done: release candidate `c2e4e0b3fe9cef43690f104cbaef636b03422e74`
(`DEPLOY_REQUEST_SPORTS_c2e4e0b3f.md`). Not deployed; needs the owner's
approval of that exact build. Inventory at 19:07 (public prices, not
settled): Juagom–Bengeo YES 0.84/0.85, Samher–Yanale YES 0.98/0.99; our
two NO legs are marked near $0.15 and $0.01 against $1.05 paid.

## 36. Corrected build deployed locked; first reward credit — 24 Nov, 19:35 UTC

Owner, typed: "Deploy c2e4e0b3fe9cef43690f104cbaef636b03422e74 locked."
(verify locked boot, reservations, watchdog, zero open orders; prepare the
$5 / $30 authorization; "Do not arm or increase limits until separately
authorized.")

Deployment `5b34f7aa`, created 19:26:34, booted 19:27:51 on
`c2e4e0b3f`. Verified 19:28–19:33 (read-only): locked (new exposure and
completions), no order or fill since boot, no traceback, no reservation
lines, safety latch clear. First audit 19:31:30: reconciled, no
mismatches, one venue position (Juagom–Bengeo NO 1, cost $0.44), no open
orders. Watchdog 19:32:45: 0 open orders, no halt, no read error, all
three registries OK. Cash $320.5138.

Campaign of 17:00–18:50, venue records: two pairs +$0.10 (settled);
Samher–Yanale settled, lone NO lost $0.61; Juagom–Bengeo NO 1 @ 0.44
still open. Settled trading result so far −$0.51; −$0.95 if the open leg
loses, +$0.05 if it wins.

Reward rows at 19:29:59: PAID 122 / $24.09 (was 120 / $22.79). The two
rows for 22 Nov, Serie A (Bologna–Udinese $0.52, Sassuolo–Genoa $0.78),
moved from PENDING to PAID: **$1.30 credited**, matching the bot's
otherwise unexplained cash of +$1.30. The two 21 Nov rows were SKIPPED
($0.04). No rows yet for 23 or 24 Nov. The account drawdown figure fell
to $0.9644 with the credit (limit $3.00, room $2.04).

## 37. Reservation-release gap closed in a new candidate — 24 Nov, 19:45 UTC

Owner, about 19:38: "Close the remaining uncertain-order reservation gap
immediately. Require definitive venue order and position reconciliation
before releasing reserved capital. Cover the fully filled unanswered-order
case with regression tests. Keep the correction narrowly scoped and
prepare the locked release immediately." Then: a one-hour live validation
at $3, a separately authorized $5 / $30 campaign if it holds, reward
reconciliation of the whole $24.09, scaling path to $10, $25, $50.
"Obtain explicit authorization for deployment, resumption and capital
increases."

Prepared, not deployed: `766a965ba03330dcb1dbb05db517b59d8131e33a`
(see `DEPLOY_REQUEST_SPORTS_c2e4e0b3f.md`, last section). Production
stays on `c2e4e0b3f`, plain lock. Reward reconciliation updated in
`analysis/l5/rewards/SPORTS_REWARD_RECONCILIATION_20261122.md` section 9.

## 38. `766a965ba` approved for locked deployment — 24 Nov, about 19:50 UTC

Owner, typed: "Deploy 766a965ba03330dcb1dbb05db517b59d8131e33a locked.
Verify production health and prepare the earliest possible one-hour live
validation authorization at $3. Following successful validation,
immediately prepare the $5 account limit / $30 campaign spend
authorization." Also: do not increase unpaired inventory caps; verify the
49-of-49 losing residuals against venue settlements; investigate the
daily bonus; "Obtain explicit authorization for live trading and capital
increases." Scaling gates restated by the owner as evidence-based, without
fixed waiting periods ($3 corrected enforcement shown live; $5 repeatable
positive reward-adjusted economics; $10 sufficient paid-reward evidence
and verified emergency stop; $25 consistent profitability across markets;
$50 scaling without deteriorating returns).

Source switched to `release/pmus-sports-rn1-766a965ba` with the plain lock
on. This session's worker was down from about 19:50 to 20:53 UTC; the
platform created the deployment (`4d714f1b`) at 20:53:45. Verification and
an independent audit of the unpaired-residual settlements are running.
The validation window is NOT authorized by this.

## 39. `766a965ba` verified locked; campaign of 24 Nov closed — 21:00 UTC

Deployment `4d714f1b` SUCCESS on `766a965ba`, booted 20:54:54, plain lock
(new exposure and completions). No order, fill, traceback or reservation
line. Audit 20:58:28: reconciled, no mismatches, venue positions 0, no
open orders. Watchdog 20:59:38: 0 open orders, no halt, no read error.
Safety latch clear. Cash $320.5138. Account figure $0.9644 of $3.00 (room
$2.0356).

Campaign 17:00–18:50 UTC, final, venue records: two pairs +$0.10;
Samher–Yanale NO 1 @ 0.61 settled at 0 (−$0.61); Juagom–Bengeo NO 1 @
0.44 settled at 0 (−$0.44). **Trading result −$0.95.** Rewards for 24 Nov
not posted. Reward rows unchanged (PAID 122 / $24.09). The hourly
post-campaign check is switched off.

## 40. One-hour validation authorized — 24 Nov, 21:05 UTC

Owner, typed: "Authorized: one-hour validation, 21:30–22:30 UTC 24 Nov, on
these terms. Continue the opening-fill versus completed-position economic
analysis in parallel. If capital enforcement passes, immediately present
the $5 account limit / $30 campaign spend authorization for review."

Terms: build `766a965ba`; window 21:30:00–22:30:00 UTC, relocks itself; $3
account limit (room $2.04 at 21:00); 1 contract; up to 4 markets; spend
16 / unpaired 6; per-market 8 / 3; loss stop 4; daily halt 5; maker-only;
pair cap 0.97; ATP, Serie A, Premier League, La Liga, NBA, NFL, NHL;
pre-game and in-play; no strategy change during the test. Pass: account
figure plus resting and reserved bids never reads above 3.00; no
place-and-pull cycle; reservations released only on venue reconciliation;
audit and reconcile clean. Disclosed: signed emergency stop unverified;
manual stop is the plain lock; session supervision not guaranteed. The
$5 / $30 stage is not authorized by this. Baseline: cash $320.5138.
Window set at 21:05:47.

## 41. Validation hour result — 24 Nov, 21:30–22:30 UTC (written 22:40)

Build `766a965ba`, $3 account limit. Source: production logs read at
21:37, 21:54, 22:14, 22:18 and 22:35.

| Criterion | Result |
|---|---|
| Account figure + resting opening bids | max 2.9644, never above 3.00 (8 limit readings, 60 heartbeats sampled) |
| Bids placed beyond the limit | 0; 193 refused at placement for lack of room |
| Place-and-pull cycle on opening bids | none (re-placements 52 s or more apart, on price moves) |
| Reservations | 12 held (all sends deferred by the bot's own request budget), 12 released on venue order and position reconciliation, 17 to 35 s each; none outstanding |
| Audit / reconcile | 22:18:00 reconciled, no mismatches, cash explained exactly |
| Orders | 99, all 1 contract, maker, ATP or NBA |
| Safety latch, errors, watchdog | clear; none; healthy, 0 open orders after the window |
| Relock | by itself at 22:30:00 (window_ended); plain lock set 22:34, effective 22:35:10 |

Not clean:
- Two readings (21:46:58, 21:56:16) showed 3.41 and 3.39 because a fill was
  counted both in the ledger figure and as "not yet in the ledger" for a
  few seconds. True figures 2.96 and 2.88. Each made the bot pull and
  re-place its opening bids once. Conservative, costs quoting time.
- Two "account figure unreadable" blips (21:49:29, 21:59:44) did the same.
- The completing bid on Axenef–Alarub was re-placed 45 times at the same
  price: each two-tick jump marks fair value unsettled, the quote decision
  drops the completion, and it returns about 13 s later. 50 of the 99
  orders were completions.
- The hourly audit took about ten minutes under traffic and no heartbeat
  was logged while it ran (22:08–22:18); the loop and orders continued.

Trading: 2 fills, 0 pairs. Axenef–Alarub NO 1 @ 0.45 (market now YES
0.93/0.94); Jiecui–Benhas YES 1 @ 0.51 (market 0.52/0.53). Both unpaired
and now without completing bids (locked). Cash $319.5538 = 320.5138 −
0.96. Nothing settled. Owner's provisional view at 21:57: capital
enforcement passes; the double count is conservative, not dangerous.

## 42. Strategic direction changed by the owner — 24 Nov, 23:35 UTC

Owner, typed: "Strategic direction has changed. We want to actively trade
live sports events without requiring international reference prices. Use
the existing inventory-aware controller. Do not build another trading
system." Asked for a venue-native configuration ("Removes international
pricing as a mandatory entry requirement, subject to explicit release
approval"), reconsideration of table tennis, esports and tennis "based on
actual net economics rather than international pricing availability", a
bounded $5 / $30 configuration, targeted regression tests and the exact
authorization request. "Do not arm or deploy money-capable changes without
explicit authorization." This supersedes, for a profile the owner
approves, the earlier rulings that table-tennis and Dota 2 rewards were
not sufficient to enable those markets and the 22 Nov requirement of an
international fair value for every bid. "No cricket!" is not superseded.

Prepared, not active: `VENUE_NATIVE_CONFIG_20261124.md` (settings only on
build `766a965ba`). Nothing changed in production.

## 43. Venue-native validation authorized — 24 Nov, 23:55 UTC

Owner, typed: "Authorized: venue-native validation, $5 account limit, $30
campaign spend, table tennis (Setka Ukraine and Moldova), UTR tennis and
Dota 2, 00:30–03:30 UTC 25 Nov, on these terms. Keep same-side
accumulation and continuous replenishment enabled. Prioritize live
reward-eligible events. Preserve all existing capital limits, loss stops,
reconciliation and watchdog protections. Report actual trading activity,
accumulated inventory, reward-qualifying resting time and settled
economics. If the proposed start time has passed or insufficient
preparation time remains, request authorization for a revised window."

Terms (`VENUE_NATIVE_CONFIG_20261124.md`): build `766a965ba` unchanged;
window 00:30:00–03:30:00 UTC 25 Nov, relocks itself; international price
not required (guard still on where a price exists); fair-value test off;
same-side additions without a fair value on; competitions setkameua,
setkamemd, utr, dota2; account limit $5.00 (ledger); campaign spend $30;
1 contract; up to 4 markets; in-play and pre-game; unpaired caps $6 / $3;
per-market spend $8; loss stop $4; daily halt $5; maker-only; pair cap
0.97; 10-contract depth gate; band 0.30–0.70; all other controls
unchanged. Disclosed: no independent price check; ranking by pool size;
signed emergency stop unverified; rewards post one to two days later.
Settings set at 23:57 UTC (ledger limit and sports profile), 33 minutes
before the window. To be restored and relocked afterwards; the account
limit stays at $5 only until the owner decides otherwise at the close-out.

## 44. Manual NHL order confirmed by the owner — 25 Nov, about 02:56 UTC

Owner, verbatim: "FYI I spent $25 on the nhl game in order to get the free
$5 reward" and "I confirm that the manual NHL order was mine."

- The order on `aec-nhl-veg-edm-2026-11-24` seen from about 02:19 UTC is
  the owner's own manual activity (venue promotion: trade $25 in a day for
  a $5 bonus). Not bot trading. The watchdog had already marked it
  manual-exempt.
- Instruction: "Complete the current campaign reconciliation and
  distinguish manual account activity from bot trading P&L. Preserve
  total portfolio exposure protections."
- Consequence observed, by design: the account figure the ledger publishes
  moved with the manual cash change (to $21–27 against the $5 limit), so
  the bot refused all new exposure from 02:19 UTC. No limit was changed to
  work around it. The $5 bonus, if credited, is a promotion credit and is
  not trading P&L or a liquidity reward.
- Same message: add props, spreads, totals and combos to research and
  market discovery (read-only scan started); prepare $10 / $25 / $50
  configurations; "obtain explicit authorization before deployment or
  capital increases." No deployment, arming or limit change is authorized
  by this message.

## 45. Venue-native validation result — 25 Nov, 00:30–03:30 UTC (written 04:00)

Build `766a965ba`, $5 account limit, $30 spend, one contract, Setka and
UTR (Dota 2 refused by the esports format rule throughout). Read from
production logs by a read-only pass; figures marked (c) are computed.

Bot trading only:

| | |
|---|---|
| Fills | 15 (14.00 contracts, $6.61 (c)); 14 on campaign markets, 1 completing an ATP leg carried from the validation hour |
| Pairs | 5 on UTR Traemm–Zylame at 0.96, 0.93, 0.91, 0.92, 0.90: +$0.38 locked; 1 ATP pair at 0.97: +$0.03 (belongs to the validation hour) |
| Settled unpaired | Setka Korvol–Kozden YES 0.63 lost: −$0.63 |
| Open at close | UTR Carjem–Caltes NO ×2 at 0.45, cost $0.90, unsettled (worst −$0.90, best +$1.10) |
| Campaign result | −$0.25 settled and locked; −$1.15 to +$0.85 once Carjem–Caltes settles |
| Same-side additions while unpaired | 2 (Traemm–Zylame 02:16, Carjem–Caltes 03:26); 3 fresh entries after a completed pair |
| Orders placed | 153 (108 opening, 27 extension, 18 completing); 15 filled |
| Fees / rebates logged | 0 / 0 |
| Able to quote new exposure | about 127 of 180 minutes (c); refused 02:19–03:10 by the account limit after the manual purchase |
| Two-sided resting, minutes (c) | 70, 59, 30, 9, 5, 5, 4 across seven UTR/Setka markets |

Safety: largest order 1 contract; every order post-only; new opening
orders only in UTR and Setka; no safety latch, halt, HTTP 429 or
reconciliation mismatch; watchdog 602 sweeps with no action; loss stop not
reached. Relocked itself at 03:30:00 (`window_ended`), all own orders
cancelled by 03:30:04, zero open bot orders from then. Plain lock set at
03:46 UTC (restart; boot shows new exposure and completions locked).
Not proven either way: whether a fifth market briefly held an order at
00:32–00:34 while the carried ATP completion rested beside four new
markets.

Manual activity, kept separate (owner's own, section 44):

- 02:18:46 UTC: 35.46 contracts bought at 0.69 on
  `aec-nhl-veg-edm-2026-11-24` (about $24.47).
- 03:10:32 UTC: account value rose $31.56 = 35.46 × 0.89; consistent with
  the position being sold at 0.89 (about +$7.09). Inferred from the value
  change; no fill line was read.
- 02:20 UTC: account value rose exactly $5.00; unexplained by the logs
  (possibly the venue's promotion credit; not confirmed).

Account figure the bots read (ledger): 1.92 at 00:30; 2.61 at 02:18; 27.61
at 02:19; −9.93 at 03:10; −9.03 at 03:30. **It is now negative: the manual
gain and the $5 step sit inside the same figure as the bot's results, so
the $5 limit would currently allow about $14 of bot loss.** The bot is
locked, so nothing is at risk now; this must be corrected before any
window opens (ledger change in preparation: separate bot-only and
total-account figures).

Reward rows for this window: not posted yet.
Left as they are pending the owner's decision: venue-native profile
settings (locked), account limit $5.

## 46. Venue-native validation — final settled result (read 11:50 UTC 25 Nov)

UTR Carjem–Caltes settled against the 2 NO held at 0.45: −$0.90. Nothing
is open. Venue-reconciled result for the 00:30–03:30 UTC window (the
bot's own P&L line, fills reconciled against the venue): 14 fills, 13
contracts, $6.15 spent; 5 pairs +$0.38; 3 unpaired contracts lost −$1.53;
fees 0; **realized net −$1.15**. Rewards for the window: none credited yet
(venue total credited still $24.09 at 05:48 UTC).

Bounded campaigns since 21 Nov, bot trading only: −$2.10, −$0.42
(validation hour), −$1.15 = −$3.67 realized; rewards credited +$1.30;
net −$2.37.

Owner direction 25 Nov (later messages, recorded for scope): C2 selected
for preparation ($13 bot limit, $35 total-account limit, eight markets,
$60 spend); feature development frozen; market-expansion branch kept
separate from the production release; priority is the two locked
deployments, manual-order accounting, emergency-stop verification and the
$13 / eight-market campaign. None of these messages contained a
deployment, settings or live-trading authorization line; nothing has been
deployed or set.

## 47. Coordinated locked deployment authorized — 25 Nov, about 12:10 UTC

Owner, typed: "I authorize deployment of ledger
c70e244a21bd0034c04b7ac610e6b19357cf2da1 with the split off, then sports
bc3a5b6b95218d0b3b0c9c42a57c929fbdec97b9 locked. Verify both deployments,
watchdog health, unchanged risk limits and zero open orders. Do not arm or
increase capital yet. Freeze additional feature development."

Scope: the two deployments only. No setting is changed: the ledger split
stays off (no total limit set), the bot limit stays $5, the sports lock
stays `1`, none of the new sports settings is switched on. Rollbacks:
ledger `f5a5434d6`, sports `766a965ba`.

Carried out (section 47), 25 Nov:

- Ledger `l5-pmus-live`: deployment `24ba89e8`, commit `c70e244a2`,
  SUCCESS 12:04 UTC. Heartbeats 12:05 and 12:06: value 330.5058, anchor
  321.4782, limit 5, figure −9.0276, room 14.0276, no refusal, no `split`
  block — identical to before the deploy.
- Sports `pmus-spring-l5-probe`: deployment `10cf4c50`, commit
  `bc3a5b6b9`, SUCCESS 12:14 UTC. Boot: lock `locked` (new exposure and
  completions), refill cooldown 60, depth 1, 4 markets, 1 contract — the
  earlier settings, none of the new ones on. New code confirmed running
  (`CRICKET_LEARNED` for `sheffield` and `testcr` at start). No order
  line.
- Watchdog sweeps 12:14–12:15: all three registries OK, sports lane OK,
  0 open orders, no halt, no read error.
- Limits unchanged: bot limit $5, lane cap $20, at-risk $16, daily loss
  $5, unpaired $6 / $3, campaign spend $30.
- Not yet seen at the time of writing: the sports bot's first hourly
  account audit on the new build.

## 48. Owner's receipt for the manual NHL trade — 25 Nov, about 12:20 UTC

Owner supplied the venue app's receipt: Golden Knights vs Oilers, bought
$25 at 69%, sold at 89%, profit +$6.55, payout $31.55, dated 24 Nov (local
date). It agrees with the production records (35.46 at 0.69, $24.9974
with fee; sold at 0.89, $31.5594; +$6.56). The receipt shows no order id.
Owner: "Use these details to locate the original order through
authenticated venue execution or transaction history. Preserve the
fail-closed accounting protections."

Finding: the buy order id exists in the venue's account activity feed
(each trade row carries our side's execution record with its order), which
the sports bot reads every hour; no running build prints it. I have no
signed access of my own, and forking the production environment to run a
read would start second copies of the money-capable services, so that was
not done.

Prepared, not deployed: sports release
`8ec438d52ed0e4e914aa8e3060d12bf4c5ccb616` (branch
`release/pmus-sports-rn1-8ec438d52`, code `86c5bf455`) = the running
`bc3a5b6b9` plus one read-only log line (`MANUAL_ORDER_IDS`) listing the
order ids behind manual sports trades from that existing walk. No order
path, no venue call of its own. Regression 5,643 passed, same 24
unrelated failures. Needs deployment approval (locked).

## 49. $13 / eight-market campaign authorized — 25 Nov, 13:28 UTC

My request (12:50 UTC) ended with one line: "Authorized: set the anchor to
333.0402 and the account limit to $13. Then run the eight-market campaign
on the deployed build for six hours starting 30 minutes after this
message: one contract, $60 campaign spend, Setka and UTR,
join-the-best-bid, 10-second replenishment, share ranking,
balanced-as-fresh-entry, batched fill detection, separated inventory
slots, two-hour completions-only grace after the window. Emergency stop
unverified; paid-reward gate waived for this campaign."

Owner's reply, typed, in full: "Authorized". Received 13:28 UTC.

Read as approval of exactly that line. Window: 13:58:00–19:58:00 UTC
25 Nov (30 minutes after the reply, six hours), relocks itself;
completions-only until 21:58 UTC. Terms as in
`CONSOLIDATED_AUTHORIZATION_REQUEST_20261125.md`, "Final form". Gate
before anything opens: the ledger must read about $2.53 after the anchor
change; otherwise the bot stays locked and the owner is told.

Carried out (section 49), 25 Nov:

- 13:30 UTC ledger: anchor 333.0402, limit 13 (restart). Heartbeats 13:32
  and 13:33: value 330.5058, figure **2.5344**, room 10.4656, no refusal —
  exactly the reconciled expectation.
- 13:35 UTC sports settings set (one restart, deployment `64a59ac8`):
  8 markets / 16 tracked, spend 60, join-touch 1, share ranking 1,
  balanced-is-open 1, batched fill detection 1, slots-count-inventory 0,
  cooldown 10, completion grace 7200, competitions setkameua, setkamemd,
  utr, lock `window:2026-11-25T13:58:00Z/2026-11-25T19:58:00Z`.
- Boot 13:36:00: lock `window_not_started`, window and grace as set,
  cooldown 10, spend 60, 8/16 markets, competitions as set, 1 contract.
- Scheduled checks re-enabled at :05 and :35.

## 50. Owner direction — inventory accumulation is the strategy (25 Nov, about 14:25 UTC)

Owner, verbatim extracts: "Inventory accumulation and management is the
strategy. Rewards and pair completion are secondary." "Our primary
strategy is active inventory accumulation and intelligent inventory
management." "Liquidity rewards are supplemental income. Pair completion
is incidental." "Identify which existing settings and controller
behaviors prevent this strategy from operating effectively. Prepare the
necessary configuration changes for immediate authorization. Do not build
another controller. Preserve hard risk limits." "...recognizing that
accumulating more inventory at unfavorable prices will increase losses."
"Profitability across the entire inventory portfolio is the objective."

Strategic direction; not an authorization. The earlier rule "Do not
increase unpaired inventory caps" (24 Nov) is in tension with it and is
changed only if the owner approves specific new caps. Campaign of section
49 continues unchanged until then.

## 51. Mid-campaign configuration change authorized — 25 Nov, about 14:35 UTC

Owner, typed: "Authorized for the remainder of this campaign: two
contracts before the match and in play, four simultaneous markets, add ATP
and WTA, depth filter 3. Keep mandatory external reference-price tests
off. Preserve same-side accumulation across all approved competitions.
Keep competitive quoting, intelligent cancellation, dynamic sizing,
10-second replenishment and inventory management active. All existing
capital limits, exposure caps, loss stops and watchdog protections remain
unchanged. Measure inventory acquisition costs, same-side fills, exposure
and settled net profitability. No further development."

Differs from my request in one point: the margin test for same-side
additions stays OFF (`PMUS_RN1_EXTEND_EV` unchanged at 0), so additions
remain price-blind in every competition, bounded by average cost + 2c and
the caps. Set: `PMUS_RN1_PREGAME_QTY=2`, `PMUS_RN1_LIVE_QTY=2`,
`PMUS_TWO_SIDED_MAX_MARKETS=4`, `PMUS_TWO_SIDED_MAX_TRACKED=8`,
`PMUS_TWO_SIDED_MIN_TOUCH_QTY=3`, competitions
setkameua,setkamemd,utr,atp,wta. Window (to 19:58 UTC), $13 account
limit, $3 / $6 unpaired caps, $8 a market, $60 spend, $4 loss stop, $5
daily halt unchanged. Largest order is now 2 contracts.

Carried out (section 51): settings set 14:32 UTC, one restart (deployment
`1f816930`). Boot 14:33:40: window open (new exposure and completions
unlocked), pre-game and in-play size 2, depth filter 3, 4 markets / 8
tracked, competitions atp, setkamemd, setkameua, utr, wta, margin test
off, cooldown 10, caps and limits as before. Carried inventory at the
restart: UTR Dolhey–Monmic YES 2 / NO 1 (last fill YES 0.30 at 14:32:43).

## 52. Mid-campaign review — 25 Nov, 15:20 UTC (no change made)

- Settled P&L reconciled: bot realized −$0.86 (3 markets settled, all
  losses from unpaired legs); ledger cash 330.5058 (13:58) → 329.6459
  (15:17) = −$0.86; unexplained $0.00. Rewards: $0 modelled above payout.
- ATP/WTA not selected because no selection ran after the 14:33 restart:
  all four slots were held by pre-game UTR markets with resting bids. Only
  one ATP market (pausan-thicig) would have been eligible.
- 278 jump/volatility pauses: real moves (e.g. Dolhey 0.18→0.01, Viebru
  0.43→0.14; 24 of 139 jumps were spread-only); opening time lost ≈ 0
  because the slots were full anyway. Protections not weakened.
- Champions League today (scan `rewards/scan_20261125/ucl_1515.json`):
  pools $160 day-of / $1,280 live per match, target 10,000, but 5,000 to
  100,000 contracts at each touch; our modelled reward is $0.00–0.01/hour
  even at 20 contracts and even with the live pool. Not a reward
  opportunity at our size; the earlier suggestion is withdrawn.

## 53. Ladder release authorized — 25 Nov, about 16:30 UTC

Owner, typed: "I explicitly authorize deployment of SHA
5425d94d336d7ff13c7dafd55e55d64e7b369aa5 for the remainder of today's
authorized campaign ending at 19:58 UTC. Enable three ladder levels, two
ticks apart, with two contracts per level and in-play markets only. All
other settings, capital limits, loss stops, maker-only restrictions and
watchdog protections remain unchanged. ... Verify aggregate worst-case
exposure across all ladder orders, including in-flight and uncertain
orders. Do not extend the campaign or increase capital without separate
authorization."

To do: deploy `5425d94d3` (sports only; rollback `bc3a5b6b9`); set
`PMUS_RN1_LADDER_LEVELS=3`, `PMUS_RN1_LADDER_STEP_TICKS=2`,
`PMUS_TWO_SIDED_SLATE=live`. Size stays 2 (pre-game and in-play settings
unchanged). Window unchanged (ends 19:58 UTC; completions-only grace to
21:58 as already authorized in section 49).

Carried out (section 53): ladder settings set 16:36 UTC without restart;
release `5425d94d3` deployed (deployment `27ec5dec`, live 17:03:49 UTC;
the build started at 17:02 after a session worker restart). Boot 17:03:53:
slate live, window open to 19:58 UTC with 2 h completion grace, size 2,
4 markets, depth 3, five competitions, caps unchanged. New code confirmed
(`ladder` field in RN1_QUOTE). Carried in at the restart: ATP
Joagui–Lucamb YES 2 at 0.42 (unpaired $0.84, completion NO bid 0.55);
ATP Matalm–Carzar paired 1/1.

## 54. New exposure stopped — term breach (25 Nov, 17:11 UTC)

At 17:07:59–17:08:07 UTC the bot placed five opening orders (YES 0.56,
0.54; NO 0.41, 0.39, 0.37; 2 contracts each) on
`aec-wta-martre-vererj-2026-11-25`, logged `phase: pregame`. The owner's
terms (section 53) were in-play markets only. Cause (inferred): the market
was already being tracked from before the restart; `PMUS_TWO_SIDED_SLATE=
live` governs which markets are newly selected, not whether an already
tracked pre-game market keeps quoting. No fills; every cap held (worst
case: market unpaired $2.34, campaign $3.18, account 4.20 + 5.09 resting
= 9.29 of 13).

Action, under the standing instruction to stop if a term is not held:
`PMUS_TWO_SIDED_SUBMISSION_LOCK=window:2026-11-25T13:58:00Z/2026-11-25T17:11:00Z`
— new exposure ends now; opening orders are cancelled by id; maker
completion bids may continue for the already-authorized 2-hour grace
(to 19:11 UTC, inside the authorized 21:58). Not resumed without the
owner's decision.

Confirmed 17:13 UTC: lock state `window_completions_only` (new exposure
locked, completions allowed to 19:11 UTC); watchdog open_orders 1 — the
ATP Joagui–Lucamb completing NO bid 0.55 × 2; all opening orders gone.

Fix prepared, not deployed: release `0abb52a385d639e3c779f66a4de57f23af1869f9`
(branch `release/pmus-sports-rn1-0abb52a38`, code `24aff0435`) =
`5425d94d3` + under the live slate a pre-game market gets no opening or
extension bid (completions continue). Regression 5,651 passed, same 24
unrelated failures; test added for the exact case.

## 55. Resume authorized on release a46a41b6a — 25 Nov, about 18:15 UTC

Owner, typed: "Authorized: deploy a46a41b6a and resume the campaign until
19:58 UTC on the same terms: in-play only, ladder 3 levels 2 ticks apart,
2 contracts per level, maker-only, all existing limits unchanged. Keep the
average-cost allowance at 0.02 for this campaign. ... Do not optimize for
pair count. No further strategy changes during this campaign."

Release `a46a41b6a244e8907592cb893f83de5cd499433b` (= `5b4584db4` + issue
#252 opt-in, left OFF). Lock back to
`window:2026-11-25T13:58:00Z/2026-11-25T19:58:00Z` (grace 2 h as before).
`PMUS_RN1_EXTEND_MAX_UP` unchanged at 0.02. Rollback: `5425d94d3`.

Carried out (section 55): lock set back to the 19:58 window (no restart),
release `a46a41b6a` deployed (deployment `621db2a0`, live 18:17:21 UTC).
Boot 18:17:27: new exposure and completions unlocked, window to 19:58 UTC,
grace 7,200 s.

## 56. Locked deployment of 6755062ad authorized — 25 Nov, about 19:55 UTC

Owner, typed: "Authorized: deploy 6755062ad1e4ccdd9299982205f223e2a686ff37
locked after the existing campaign and authorized completion period
conclude. ... Do not arm, increase capital or change production risk
limits without separate authorization."

To do after 21:58 UTC: close out; set `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`;
deploy `6755062ad` (rollback `a46a41b6a`); verify locked, zero open
orders, watchdog healthy, limits unchanged. Settings stay as they are;
`PMUS_RN1_FIT_QTY` and `PMUS_RN1_PULL_THREATENED_ONLY` stay off;
`PMUS_RN1_LOSS_STOP_COUNTS_RESTING` is on by default in the build.

## 57. Close-out of the $13 campaign and locked deployment of 6755062ad — 25 Nov, 22:13 UTC

Done as authorized in section 56. Nothing armed; no limit changed.

- 21:58:40 the window lock moved to `window_ended`; the completing bid
  (Garcar–Borvic NO 0.29 × 2) was withdrawn. Watchdog 22:05: 0 open orders.
- 22:06 `PMUS_TWO_SIDED_SUBMISSION_LOCK=1` (no redeploy).
- 22:10 deployed `6755062ad1e4ccdd9299982205f223e2a686ff37` (branch
  `release/pmus-sports-rn1-6755062ad`); deployment `71075263`, SUCCESS
  22:12. Boot: `SUBMISSION_LOCK_STATE` new exposure and completions locked;
  limits unchanged (per-market unpaired 3, per-market 8, campaign unpaired 6,
  spend 60, daily loss 5, at-risk 16, lane 20, account limit 13). Watchdog
  22:12–22:13: 0 open orders, lane healthy. Rollback `a46a41b6a` not needed.

Campaign 13:58–19:58 UTC (completions to 21:58), latest PILOT_PNL 21:21:

| | |
|---|---|
| Fills | 15 (18.97 contracts): 10 open (14), 1 extend (1), 4 complete (3.97); one deeper-ladder fill (Garcar YES 2 @ 0.64, same second as 2 @ 0.66) |
| Spent | $10.0199 |
| Pairs | 3.97 at average 0.9675, locked edge +$0.1291 |
| Settled residual (unpaired) | −$1.5790 |
| Realized (bot) | **−$1.4499** |
| Open | Garcar–Borvic YES 4 @ 0.65 ($2.60), venue-reconciled |
| Fees / rebates | 0 / 0 |
| Cash | 326.4559 vs baseline 330.5058: −4.0499, all explained by the campaign; unexplained 0.000000 |
| Rewards | REWARD_CHECK 21:19: 187 rows, $24.09 credited (122 PAID), $4.00 skipped (65); unchanged since 15:35; no rows yet for 23–25 Nov |
| Account figure | $6.58 of the $13 limit (22:12) |

Defect found in review (fixed in `7a0535450`, not deployed): while a block
was in force, ladder levels behind a completing bid (openings) were still
placed — NO 0.27 / 0.25 rested behind the completion during the loss stop.
`6755062ad` refuses them at the loss stop through the final gate.

## 58. Revised Campaign B authorized — 25 Nov, 23:26 UTC

Owner, typed: "Authorized: deploy 7a053545094473432cca296ec7bfb0d284228b36
locked now, then revised campaign B for two hours, 23:45 to 01:45 UTC 25–26
Nov (or, if the deploy checks are not complete by 23:45, from the next
quarter hour for two hours), with exactly the settings and limits in "FINAL
TERMS" of CAMPAIGN_B_NET_20261125.md (ledger account limit $20; loss stop
and daily halt $10; sports lane $20 unchanged; esports off); crypto
unchanged and disarmed."

Done:
- 23:27 deployed `7a0535450` locked (deployment `2e3cc3b9`, SUCCESS 23:28).
- 23:28 ledger `L5_PORTFOLIO_MAX_DRAWDOWN_USD` 13 → 20 (deployment
  `055ecc76`, SUCCESS 23:29). Bot reads limit 20, figure 6.5843,
  capacity 13.4157.
- Watchdog 23:30–23:31: 0 open orders.
- 23:32 applied the FINAL TERMS settings, with
  `PMUS_TWO_SIDED_SUBMISSION_LOCK=window:2026-11-25T23:45:00Z/2026-11-26T01:45:00Z`
  and grace 7,200 s.

Crypto and watchdog not touched; esports bypass off.

## 59. New exposure stopped after 4 minutes — negative-net quoting (25 Nov, 23:49 UTC)

The window opened at 23:45. Markets carried over from before the window
(UTR `quioli-kimeli`, ATP `olibon-luimig`), both flat, quoted openings at
a negative estimate (−$0.07 and −$0.10 an hour). The block applied only to
markets holding inventory; a flat market quoted until its 120 s release.
`quioli` filled YES 1 @ 0.46 and 1 @ 0.45 at 23:47. The owner's rule is
"Do not force negative-EV trades", so new exposure was stopped: 23:49 the
lock was set to `window:2026-11-25T23:45:00Z/2026-11-25T23:49:00Z`
(completions only until 01:49).

Before the stop, the rotation worked as designed. Negative markets were
passed over, and the two positive markets were selected and quoted
before their match: Setka `kimdmy-elfiva` (+$0.33/h, YES 0.49/0.47,
NO 0.48/0.46) and ATP doubles `delanedelane-falcksuarez` (+$0.61/h,
YES 0.34/0.32, NO 0.63/0.61). Those bids were cancelled at the restart
(`cancelled_all_own`, still_open []).

After the stop, completions: `quioli` NO 1 @ 0.49 and 1 @ 0.46 filled
(23:50, 23:51), so it is paired 2 / 2 at 0.93 a pair. Garcar–Borvic
completion NO 0.32 × 2 rests. Watchdog 23:52: 1 open order, ours.

Fix: code `0511865ab`, release `afd8a611d650a18603ab4e833278cd806c6331f5`
(branch `release/pmus-sports-rn1-afd8a611d`). With net ranking on, no
opening or extension in any market whose fresh net is not positive, flat
or not. Such a flat market is still released; completions continue. Test
added. Regression: 5,706 passed, the same 24 unrelated failures. Not
deployed; resuming needs the owner's authorization with new times.

## 60. Resume authorized on afd8a611d — 26 Nov, about 00:00 UTC

Owner, typed: "Authorized: deploy afd8a611d650a18603ab4e833278cd806c6331f5
locked now, then resume revised campaign B for two hours, 00:10 to 02:10
UTC 26 Nov (or, if deployment checks are not complete, from the next
quarter hour for two hours), with exactly the settings and limits in FINAL
TERMS of CAMPAIGN_B_NET_20261125.md. Ledger account limit $20; campaign
loss stop and daily halt $10; sports lane $20 unchanged; esports off; crypto
unchanged and disarmed. Verify the corrected opening-order gate before
arming."

- 23:59 deployed `afd8a611d` (deployment `3bc08412`, SUCCESS 00:00).
  Completions only (old window); latch null; 1 open order, ours
  (Garcar–Borvic completion); ledger figure 6.4443 of 20, capacity
  13.5557.
- Gate check before arming: the net-rank tests run against the exact
  deployed commit pass (13), including "a flat market with no positive net
  gets no openings". The lock check comes before the net block in the
  quoting step, so the live check is made in the first passes of the
  window: no opening in a market whose latest `RN1_NET_PLACEMENT` net is
  not positive.
- 00:02 lock set to `window:2026-11-26T00:10:00Z/2026-11-26T02:10:00Z`
  (grace 7,200 s unchanged); all other FINAL TERMS values unchanged.

## 61. Deploy b8693dcf4 into the running Campaign B window — 26 Nov, about 00:45 UTC

Owner, typed: "I explicitly authorize deployment of
b8693dcf4bc269f2d4c8576f99e123377e6fabbf into the existing Campaign B
window, ending at 02:10 UTC on November 26, with the existing
completions-only grace period unchanged. Preserve exactly the FINAL TERMS
settings, capital limits, loss stops and independent watchdog protections.
Maker-only. Esports off. Crypto unchanged and disarmed."

Release = afd8a611d + dev/chatgpt-rn1-net-read-guard (16aecb0fb) + own-order
book-lag guard (code 2618113e7). Variables unchanged.

Done (section 61):
- 00:48 deployed `b8693dcf4` (deployment `6420b7e2`, SUCCESS 00:49:58).
- Boot 00:50:04 matches FINAL TERMS: qty 2, sizes 1/2, net rank on,
  minimum touch 0, slate both, band 0.20–0.80, 2 markets / 4 tracked,
  unpaired $5 per market / $10 campaign, $12 per market, spend $60, at-risk
  $20, daily $10, lane $20, competitions as listed, esports fail-closed.
- Lock: window 00:10–02:10, grace 7,200 s, open.
- Account: ledger figure 8.0343 of 20, capacity 11.9657. Latch null; no
  429s.
- Watchdog 00:51: 2 open orders, both ours; nothing overdue or foreign.
- Last venue reconcile 00:07 clean (the next is hourly).
- Fills since 00:37 (on `6042cb94`, before the restart): Setka
  `slomyk-fasiva` YES 1.68 @ 0.50 (open) and 1.5 @ 0.50 (extend), pre-match,
  markouts +0.5 to +1.5c at 5–300 s; $1.59 unpaired.
- Issue #251 integration comment posted.

## 62. Campaign C (single event) authorized; owner's manual UConn trade — 26 Nov, about 02:15 UTC

Owner, typed: "Authorized: after 02:10 UTC deploy
1b496656c45c2b38664166a912ee518073fc781a, then single-event campaign C for
two hours, 02:30 to 04:30 UTC 26 Nov, with FINAL TERMS plus exactly the
changes in CAMPAIGN_C_SINGLE_EVENT_20261126.md (one market, tracking 6,
hold 300 s, release 600 s, three ladder levels); all capital and loss
limits unchanged; esports off; crypto unchanged and disarmed. Also I placed
a manual $25 trade on UConn cbb. Don't let that interfere"

Done:
- 02:16 deployed `1b496656c` (deployment `86899366`, SUCCESS 02:17:45).
  Boot: Campaign B settings; lock completions-only (B grace); latch null.
- Watchdog 02:19: 3 open orders, all ours; nothing foreign or overdue.
- 02:20 set `MAX_MARKETS` 1, `MAX_TRACKED` 6, `PMUS_RN1_NET_HOLD_S` 300,
  `PMUS_RN1_RELEASE_UNQUALIFIED_S` 600, `PMUS_RN1_LADDER_LEVELS` 3, lock
  `window:2026-11-26T02:30:00Z/2026-11-26T04:30:00Z`. All other FINAL
  TERMS values and every capital and loss limit unchanged.

Manual trade:
- What the venue shows: `aec-cbb-duke-uconn-2026-11-25` NO (DOWN) 469
  contracts. Ledger reconcile 02:12 shows "venue holds a market we never
  booked", adopted, with new exposure refused on the crypto lane, which is
  disarmed anyway.
- Effect on the bot: the shared account figure rose to 34.45 (02:08) and
  24.55 (02:19) against the $20 limit, so the bot refuses all new openings
  (fail-closed).
- Watchdog: never cancels a venue-MANUAL order; this one is filled, not
  resting.
- Bot: skips any market with a foreign position.
- The plan, per the "Manual orders" option B design and the owner's
  instruction that the trade must not interfere:
  1. Take the exact cost from the venue activity feed (`MANUAL_FILL_STUDY`
     / `MANUAL_ORDER_IDS` at the next hourly audit, about 02:55).
  2. Raise `L5_PORTFOLIO_ANCHOR_USD` by exactly that cost; nothing else
     changes.
  3. When the market settles, lower the anchor by its proceeds.
  4. Record every step here with the venue figures.
- The account limit ($20), the loss stops and the daily halt are
  unchanged. The manual position is excluded from bot P&L by order id.
  Until the correction, the bot stays refused (no interference with
  safety, but no campaign quoting).

### Campaign B results, 00:10–02:10 UTC 26 Nov

| | Old build (00:10–00:50) | New build `b8693dcf4` (00:50–02:10) |
|---|---|---|
| Median order lifetime | 17.7 s | 79.8 s |
| Markets released while quoting | 3 | 0 |
| Resting $ / room | 1.95 / 12.18 (16%) | 2.90 / 9.07 (32%) |
| Qualifying / resting contract-s, pregame | 7,903 / 9,216 | 24,884 / 25,237 |
| Qualifying / resting contract-s, live | 0 / 2,329 | 306 / 8,270 |
| Fills | 2 (`slomyk` YES 3.18 @ 0.50) | 4 (`jaskub` YES 2 @ 0.43 and 2 @ 0.41, in play; `jaskub` NO 1 @ 0.55, completion; `delanedelane` NO 0.34 @ 0.54) |

After 02:10, the `slomyk` NO 1 @ 0.47 completion filled (pair 0.97).

PILOT_PNL 01:55: realized +0.03 (1 pair at 0.97); 6 fills; 8.52 contracts;
spent $4.00; outstanding $3.03; unexplained 0. `jaskub` has since settled:
4 YES lost, 1 NO won, about −$1.23 on the market; the settled figure comes
from the next PILOT_PNL.

REWARD_CHECK 01:52: unchanged ($24.09 credited, $4.00 skipped); no rows yet
for 25–26 Nov.

The read-out counted 5 openings or extensions placed while the latest net
estimate was not positive. These were not checked one by one. The two
flagged at the 01:30 check were ordering artifacts: the estimate was
recomputed positive just before each order.

### Flagged negative-net openings, checked one by one (owner request, 26 Nov)

Method: both deployments (6042cb94, 6420b7e2), complete per-slug pulls,
every event joined on its own JSON time "t". The in-force estimate is the
latest RN1_NET_PLACEMENT for the slug before the order, which is logged on
every sign change.

Result: 131 openings and extensions 00:10–02:10 (91 open, 40 extend). In-force
net > 0: **131**. ≤ 0: **0**. None without a prior estimate. The preceding
quote had no block on all 131.

The earlier flags (2 at the 01:30 check, 5 in the full-window read) were
ordering artifacts: log emission time lagged the event time. The gate fired
as designed at slomyk 00:48:30 (−0.0113 → `net_not_positive`, no order
followed).

Thin margins: several slomyk orders went out at +$0.0005 to +$0.0015 an
hour. They pass on sign but are break-even within model error.

## 63. Manual-trade accounting correction and replacement Campaign C window — 26 Nov, about 03:15 UTC

Owner, typed: "I authorize the exact manual-trade accounting correction:
change L5_PORTFOLIO_ANCHOR_USD from 333.0402 to 312.4102, excluding the
venue-confirmed $20.63 manual UConn loss from bot-specific risk accounting.
Preserve the manual loss in total account P&L. ... I also authorize
replacing Campaign C with the 03:30–05:30 UTC November 26 window on release
1b496656c, with unchanged capital limits, loss stops, maker-only execution
and a two-hour completions-only grace period. Focus on one reward-eligible
event ... Select an event expected to finish within the authorized window.
Do not automatically extend the campaign. Esports off. Crypto unchanged and
disarmed. No capital increases."

Venue figures (account activity, audit 02:25):
- `DYSS79F015P6`, 02:07:50: buy NO 469 @ 0.05 (YES 0.95), −$25.00
  including fee.
- `DYSW1R1G15P6`, 02:14:01: sell NO 469 @ 0.01 (YES 0.99), +$4.37 net.
- `realized_usd` −20.63. The position is closed (venue reconcile 02:48 /
  02:53 lists no `duke-uconn`).

Done:
- 03:16 ledger `L5_PORTFOLIO_ANCHOR_USD` 333.0402 → 312.4102 (deployment
  `0cd0cd0d`, SUCCESS 03:17). The manual loss stays in total account P&L
  (cash and the account audit are unchanged); it is excluded only from the
  bot's drawdown figure.
- 03:18 sports: lock
  `window:2026-11-26T03:30:00Z/2026-11-26T05:30:00Z` (grace 7,200 s
  unchanged) and `PMUS_RN1_PIN=aec-setkameua-slomyk-fasiva-2026-11-26`.
  This is the single event. It is a Setka table-tennis match starting
  03:45, typically 30–45 minutes, so it finishes inside the window. It has
  a reward programme, and the bot already holds 2.18 YES there. Pinning
  only restricts selection; nothing else changes.

### 63a. Stopped before the window: ledger realized-loss figure corrupted by the manual trade (03:18–03:20 UTC)

After the 03:17 ledger restart, the sports bot's account check reads
`utc_day_realized_usd −471.0552`, `lifetime_realized_usd −515.8966`,
reason `ACCOUNT_DAILY_LOSS_HALT` (daily 50, ceiling 75). At 02:13 the same
fields read −3.98 and −48.82.

This is a ledger accounting fault, not a trading loss:
- The ledger's own reconcile already showed `realized_today −514.93` at
  02:48, before the anchor change.
- Its 02:12 reconcile had adopted the manual `duke-uconn` 469 NO "over our
  book" with the basis unknown, priced at the $1 upper bound (`risk_floor_usd`
  `aec-cbb-duke-uconn-2026-11-25: 469.0000`, still present at 02:53).
- Booking the 02:14 sale against a $1 basis gives about
  469 × (0.01 − 1.00) ≈ −464. Together with the real figures that matches
  the new total.
- The venue's own record of the trade is −$20.63.

Action: 03:19 `PMUS_TWO_SIDED_SUBMISSION_LOCK=1` (owner rule: stop and relock
when reconciliation fails); the replacement window was not opened. The 04:35
and 06:35 campaign tasks are disabled.

Verified 03:20–03:23:
- Release `1b496656c` running; lock `locked`.
- Watchdog: 0 open orders, nothing foreign.
- Bot open inventory $3.69 (Garcar–Borvic 4 YES at $2.60, plus the
  remainder of `slomyk`), venue-reconciled.
- The ledger anchor is 312.4102 as authorized. The drawdown figure is not
  published while the daily halt is in force.

Nothing in the ledger was changed beyond the authorized anchor.

## 64. Ledger release 086380d88 authorized — 26 Nov 2026

Owner, typed: "Authorized: deploy ledger release
086380d88687b52ce81dcdf03e17284aed7c8c6d to l5-pmus-live with trading
locked. No limit changes. Crypto unchanged and disarmed." The owner then
asked for a full reconciliation of account equity against the venue, with
bot, manual and combined P&L, and an inventory of the risk gates that rely
on reconstructed P&L. "Do not increase limits or reset genuine losses."

12:49 UTC: source of `l5-pmus-live` set to `086380d88` (the session worker
was restarted between authorization and deployment). Sports remains
`PMUS_TWO_SIDED_SUBMISSION_LOCK=1`.

## 65. Ledger e69e20ac7 and Campaign D authorized — 26 Nov 2026

Owner, typed: "Authorized: deploy ledger release
e69e20ac71fcd084183e25c14e8f28f836abc487 to l5-pmus-live. Once the UConn
correction, account-risk record and loss halts are verified against the
venue, run single-event Campaign D on sports release 1b496656c with the
Campaign C settings for two hours from the next quarter hour after
verification, with a two-hour completions-only grace. All existing limits
unchanged. Maker-only. Esports off. Crypto unchanged and disarmed. ...
Separately, prepare the existing bot-versus-total account risk split ...
Keep the bot's $20 limit unchanged. Do not activate the split or increase
account-wide limits without separate approval. Do not delay Campaign D for
unrelated development."

Ledger source set to `e69e20ac7`. Sports remains locked until the checks
pass.

### 65a. Verification failed — Campaign D not started (15:05 UTC)

Ledger `e69e20ac7` deployed SUCCESS 14:50:30. At 15:01 the published
record still lists four unresolved bound rows: UConn (−469), NHL veg-edm
(0), UTR gaskan-hanbra (0) and test-cricket afg-zim (−1). Day −471.52;
lifetime −516.36; `would_halt` true.

Cause: release `086380d88`'s reader had already spent all three
round-trip attempts on these markets (`TRADE_REALIZED_PNL_UNREADABLE`) and
stored them as final. The corrected reader in `e69e20ac7` therefore never
re-read them. Venue: cash 312.2135; drawdown 0.1967 against the anchor
312.4102; no orders.

Fix: code `b5f96107b` counts attempts per reader version. Release
candidate `8cfb39d37666577c30bee01507cb0f6cf308b285`, branch
`release/l5-pmus-live-reader-8cfb39d37`. **Not deployed; needs the
owner's authorization.** Sports stays `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`.

## 66. Ledger 8cfb39d37 and bound-row acceptance authorized — 26 Nov 2026

Owner, typed: "Authorized: deploy ledger release
8cfb39d376777c30bee01507cb0f6cf308b285 to l5-pmus-live, then proceed with
Campaign D as previously authorized once verified. Accept bound rows
aec-utr-gaskan-hanbra-2026-11-17 and aec-testcr-afg-zim-2026-11-13 as
booked ($0 and −$1 respectively), provided both are confirmed closed and
no additional exposure remains. Preserve their audit records and existing
loss accounting. Verify the corrected UConn loss against venue data,
reconcile account equity, and confirm all safety protections before
arming. ... All existing trading limits remain unchanged."

The typed sha is 38 characters and differs after the ninth from the
proposed release. Its first nine characters `8cfb39d37` resolve uniquely
to `8cfb39d37666577c30bee01507cb0f6cf308b285`
(`release/l5-pmus-live-reader-8cfb39d37`), the only release proposed;
that is what is deployed.

Acceptance applies through `L5_ACCEPT_BOUND_MARKETS` only after both
markets are confirmed closed with no open position or order. It changes
no figure.

### 66a. Ledger 8cfb39d37 verified except one reconciliation defect — not armed (16:40 UTC)

- 15:52 deployed `8cfb39d37`; 16:15 `L5_ACCEPT_BOUND_MARKETS` set after the
  venue showed zero non-zero positions and no orders account-wide
  (sports `INVENTORY_RECONCILE` 15:24; ledger live orders 0).
- Round-trip corrections against the venue's own trade rows: UConn
  −469.00 → −20.63; NHL veg-edm 0 → +6.56; test-cricket afg-zim −1.00 →
  −0.21; CFB duke-mia −5.00 → +0.10. UTR gaskan-hanbra was held to
  settlement (booked 0) and is accepted by name. Unresolved rows: 0.
- Day −23.1524 (halt $50, not tripped). Venue cash 312.2135; drawdown
  0.1967 against anchor 312.4102.
- **Defect:** duke-mia settled 14 Nov, before the 16 Nov cash re-anchor,
  which already contains its real result. Lifetime moved +5.10:
  −55.5438 published, −60.6438 correct. `lifetime_basis.reconciled` is
  false. The other forms refuse such rows; the round-trip form did not.
- Fix: code `7b60b56b1`, release candidate
  `cdfa68f4d260e21f4e0623fe5913c73296133a2b`
  (`release/l5-pmus-live-anchor-cdfa68f4d`). The row stays restated, the
  re-anchor residual absorbs the delta, and the existing record is repaired
  once. Not deployed. Sports remains locked.

## 67. Ledger cdfa68f4d authorized; Campaign D to proceed — 26 Nov 2026

Owner, typed: "Authorized: deploy ledger release
cdfa68f4d260e21f4e0623fe5913c73296133a2b to l5-pmus-live once full-suite
regression parity is confirmed. Once venue reconciliation passes, proceed
with Campaign D as previously authorized. All existing safety and trading
limits remain unchanged. Prepare commit 9b28e6bfe from
dev/pmus-rn1-marginal-ev for the next sports release, including the
effective configuration needed to enable PMUS_RN1_MARGINAL_EV and
PMUS_RN1_FIT_ALL_LEVELS. Do not deploy or activate those changes yet.
Campaign D must establish actual two-sided inventory accumulation,
replenishment and capital utilization throughout the event."

### 67a. Ledger cdfa68f4d deployed; day figure made unknown — not armed (18:09–18:25 UTC)

- 18:09 deployed `cdfa68f4d` (deployment `ddb519fe`) after full-suite
  parity. Lifetime is now −60.6438 and `lifetime_basis.reconciled` is
  true. Unresolved 0; venue cash 312.2135; drawdown 0.1967; no orders.
- **Defect:** the repair put −5.10 into `realized_unattributed`. A
  nonzero undated residual makes `utc_day_realized_usd` unknown, which
  fails closed, so the account-risk record refuses and the sports bot
  cannot open.
- Fix: code `a4bb80f87`, release candidate
  `c111abd67e3de543c6eacb4ddc1f8345006239a5`
  (`release/l5-pmus-live-anchor2-c111abd67`).
  - Like the other forms, the round trip refuses
    `SETTLED_BEFORE_THE_LAST_REBASE`.
  - The duke-mia restatement and the residual offset are reversed once:
    the row is restored and the record kept for audit.
  - Expected result: residual 0, day −23.15 readable, lifetime −60.64,
    reconciled true.
- Not deployed; needs the owner's authorization. Sports remains locked.
