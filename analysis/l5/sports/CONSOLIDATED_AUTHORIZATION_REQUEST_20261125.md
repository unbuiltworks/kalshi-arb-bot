# Consolidated authorization request — 25 Nov 2026, 06:20 UTC

Nothing below is deployed, set or armed. State now: sports bot on
`766a965ba`, locked, zero open orders; ledger on `f5a5434d6`; watchdog
unchanged; halt clear.

## Part A — coordinated locked deployment (two services)

| | Sports bot `pmus-spring-l5-probe` | Ledger `l5-pmus-live` |
|---|---|---|
| Release SHA | `bc3a5b6b95218d0b3b0c9c42a57c929fbdec97b9` | `c70e244a21bd0034c04b7ac610e6b19357cf2da1` |
| Branch | `release/pmus-sports-rn1-bc3a5b6b9` | `release/l5-pmus-live-bot-total-split-c70e244a2` |
| Running now / rollback | `766a965ba` | `f5a5434d6` |
| Regression | 5,642 passed, 24 failed: the same 24 test ids as the baseline, none in sports code | 21,754 passed, 72 failed, 26 errors: the same counts as before the change, none in the changed area; 131 new tests pass |
| Effect of deploying alone | lock stays on; no new setting on | split off; no number changes |

Why the sports SHA changed from `f14675417`: one added commit, the
cricket exclusion. The scan found that five of the six cricket
competitions on the venue (`sheffield`, `wbbl`, `smat`, `csafc`, `pro50`)
were not caught by the bot's rule. They are now, and any competition the
venue itself labels cricket is excluded as the slate is read. Nothing else
differs. `f14675417` is superseded, not withdrawn; it is the same build
without that fix.

Ledger source review: all four original commits and the reader module
were read. One material gap was found and fixed (fifth commit): an
attested manual order that had not been read was counted as zero, which
could loosen the bot limit if that order was a gain. It now refuses until
every attested order is measured.

Order of work: ledger first (restart, confirm figures unchanged), then the
sports bot locked (confirm SHA, lock, zero orders, clean audit, watchdog
healthy). Either failing: roll that service back and stop.

## Part B — risk configuration (ledger settings; each restarts the ledger)

| Setting | Now | Proposed |
|---|---|---|
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` (bot limit) | 5 | 10 |
| `L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD` (total-account limit) | unset | 35 |
| `L5_PORTFOLIO_ANCHOR_USD` | 321.4782 | 326.4782 (+5.00 bonus) |
| `L5_MANUAL_ATTESTED_ORDERS` | unset | the NHL buy order id and the sell order id |

Evidence:

- **$5.00**: the venue's own activity feed (read by the ledger) carries a
  row of type `activity_type_referral_bonus` at 02:19:04 UTC; the
  account's buying power rose 293.8664 → 298.8664 between 02:18:59 and
  02:20:01. The venue calls it a referral bonus. The row as logged has no
  amount; the $5.00 is from the balance. Order holds, fees and a second
  order were checked and ruled out. It is not a liquidity reward.
- **NHL sell**: order id ending `15P8` (full id in the production logs),
  created 10 s after the buy, filled 03:10:20 UTC at 0.89, +$31.5594, no
  fee.
- **NHL buy**: 35.46 at 0.69 at 02:18:46 UTC, $24.9974 including a $0.53
  fee. **Its order id was never logged by any service.** I cannot
  retrieve it. It is in the venue's order history on the owner's account.
- Manual result: +$6.56 (the bot's own study line shows the same figure).

Expected figures once all four settings are in (to be read and reported
before any window opens):

| | |
|---|---|
| Total-account figure | about −$4.03 against $35 |
| Manual net (gain) | −$6.56 |
| Bot-only figure | about $2.53 against $10 |
| Bot room | about $7.47 |

Two things to know:

1. **Without the buy order id the split refuses.** With only the sell
   attested, the bots are charged the worst case (about $31.56) and are
   refused at any limit under about $23. That is the safe direction.
2. **The ledger failed to read that sell order earlier**
   (`FOREIGN_ORDER_UNREADABLE` at 03:09 UTC, while it was resting). If the
   by-id read fails again, the split refuses (the fix above) and the bots
   stay blocked until it is resolved. This can only be found out by
   switching it on; the bot stays locked while it is checked.

## Part C — eight-market campaign

Capital check first: the bot-only figure starts near $2.53 (losses since
the anchor plus the open $0.90 position), so a $10 limit leaves about
$7.47. Eight markets rest about $7.9. **At $10 the bot can quote about
six or seven markets with almost no room to hold inventory.** Options:

| Option | Limit | Markets | Room for inventory |
|---|---|---|---|
| C1 | $10 | 6 | about $1.6 |
| C2 | $13 | 8 | about $2.6 |
| C3 | $10 | 8 | none: bids refused as soon as the eighth market is quoted |

Terms (all options): release `bc3a5b6b9`; one contract; maker-only,
post-only; competitions Setka (`setkameua`, `setkamemd`) and UTR;
`PMUS_RN1_RANK_SHARE=1`, `PMUS_RN1_JOIN_TOUCH=1`,
`PMUS_RN1_BALANCED_IS_OPEN=1`, `PMUS_TWO_SIDED_FILL_LIST_POLL=1`,
`PMUS_RN1_SLOTS_COUNT_INVENTORY=0`, `PMUS_RN1_REFILL_COOLDOWN_S=10`;
campaign spend $60; unpaired caps $6 campaign and $3 a market; $8 a
market; pair-cost limit 0.97; band 0.30–0.70; depth gate 10; campaign loss
stop $4; daily halt $5; at-risk budget $16; no international price
required; fair-value test off; esports bypass off; cricket excluded; the
bot relocks itself at the end of the window.

First ten minutes: confirm `fill_list_row_keys` and that the heartbeat's
`fill_poll` shows reads being saved. If not, drop to four markets (the
request budget cannot carry more on per-order reads) and report.

Disclosures: no independent price check; joining the best bid, the
10-second cooldown, share ranking and batched fill detection have no live
evidence; joining takes fills sooner, adverse ones included; reward
figures are modelled, and the venue's wording that a pool is "shared
across the program's markets" may mean they are overstated; reward rows
post one to two days late.

## Part D — two items only the owner can close

1. **Emergency stop: not verified, and I cannot verify it.** It needs one
   marker signed with the owner's secret. It can be done with the bot
   locked, no trading window: sign in a browser terminal (GitHub
   Codespaces on branch `dev/l5-watchdog-marker-auth`), publish to
   `ops/halt-marker`, I confirm the watchdog latches, then a separately
   approved halt reset. About 15 minutes in all.
2. **Paid-reward gate for $10**: $1.30 paid; no rows yet for 23–25 Nov.

## Batched fill detection — verification status

Verified from production logs: the venue's open-order list rows carry the
order id, market, remaining quantity and manual flag; the list returned
every order in the largest case seen (5). Not verified: a filled-quantity
or original-quantity field on those rows. The bot needs one of them; if
neither is there it reads every order exactly, as today. Only a live run
settles it (first ten minutes, above).

## Props and combos — findings

- Best new candidate: full-game main-line spreads and totals in NBA, NFL
  and NHL: ordinary order books, heavily traded, large live pools.
- Not proposed: player props, period markets, team totals (near-empty
  books; void rules settle at "last fair market price"); combos (a
  quote-request product outside the controller).
- The bot's discovery sees only each event's primary market, so none of
  these are visible to it today; no rule knows when a quarter or period
  market ends; nothing caps dollars per event across its markets.
- Reward pools are "shared across the program's markets"; our model treats
  each market as having its own pool. Only credited reward rows can show
  how the sharing works.
- Detail: `analysis/l5/props/PROPS_COMBOS_SCAN_20261125.md`.

## The authorization, in one reply

> Authorized: (A) deploy ledger c70e244a2 with the split off, then sports
> bc3a5b6b9 locked. (B) Ledger settings: bot limit $__, total-account
> limit $35, anchor +$5.00, attested orders: sell ending 15P8 and buy
> ________. (C) Campaign option C_, Setka and UTR, ____ UTC to ____ UTC,
> on these terms. (D) Emergency stop: [I will sign the marker first /
> proceed unverified]. Paid-reward gate: [waived for this campaign / wait].

Parts can be approved separately. A alone is safe to do now. B needs the
buy order id. C needs A and B done and the reconciled figures reported.

## Update 06:40 UTC — owner selected C2; buy order id; expansion work

**C2 as prepared** (nothing set):

| Service | Setting | Value |
|---|---|---|
| ledger | `L5_PORTFOLIO_MAX_DRAWDOWN_USD` | 13 |
| ledger | `L5_PORTFOLIO_TOTAL_MAX_DRAWDOWN_USD` | 35 |
| ledger | `L5_PORTFOLIO_ANCHOR_USD` | 326.4782 |
| ledger | `L5_MANUAL_ATTESTED_ORDERS` | NHL sell id, NHL buy id |
| sports | `PMUS_TWO_SIDED_MAX_MARKETS` / `MAX_TRACKED` | 8 / 16 |
| sports | `PMUS_RN1_TOTAL_SPEND_USD` | 60 |
| sports | `PMUS_RN1_RANK_SHARE`, `PMUS_RN1_JOIN_TOUCH`, `PMUS_RN1_BALANCED_IS_OPEN`, `PMUS_TWO_SIDED_FILL_LIST_POLL` | 1 |
| sports | `PMUS_RN1_SLOTS_COUNT_INVENTORY` | 0 |
| sports | `PMUS_RN1_REFILL_COOLDOWN_S` | 10 |
| sports | `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | setkameua,setkamemd,utr |
| sports | `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `window:<start>/<end>` |

Everything else as in Part C. Expected bot room at $13: about $10.47
(eight markets rest about $7.9, leaving about $2.6 for inventory), to be
confirmed from the ledger's own figures before the window.

**NHL buy order id.** It can be recovered from authenticated venue
history: the account activity feed the sports bot already reads every
hour returns each trade with its execution record, and that record
contains the order. No running build prints it (the manual-trade study
logs totals and field names only), and I have no signed access of my own.
Two ways to get it: the owner reads it from the venue's order history
(minutes), or a one-line addition to that existing read logs manual order
ids (a further sports build; not needed for Part A).

**Market expansion** (NBA, NHL, NFL main-line spreads and totals;
secondary-market discovery; per-event exposure accounting; shared-pool
reward model): in development on the existing controller, every part off
by default, separate from the releases in Part A. Not part of this
request.
