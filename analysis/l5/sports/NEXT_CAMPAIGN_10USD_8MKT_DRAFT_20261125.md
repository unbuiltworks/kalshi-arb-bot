# Next live configuration — $10 / eight markets (draft for authorization) and $25 / 16 (configuration for review)

25 Nov 2026. Nothing here is set, deployed or armed. Each step needs the
owner's explicit authorization.

## Step 1 — locked deployment of the release (needs approval)

| | |
|---|---|
| Release | `bc3a5b6b95218d0b3b0c9c42a57c929fbdec97b9`, branch `release/pmus-sports-rn1-bc3a5b6b9` (f14675417 plus the cricket exclusion fix) |
| Service | `pmus-spring-l5-probe` only |
| State | `PMUS_TWO_SIDED_SUBMISSION_LOCK=1` before and after; no setting below switched on |
| Checks after deploy | build SHA in the startup line; lock reported; zero open bot orders; first account audit clean; watchdog reads the bot as healthy; heartbeat continues through the hourly audit |
| Rollback | `release/pmus-sports-rn1-766a965ba` |

## Step 2 — settings for the next campaign

Changed from tonight's profile:

| Setting | Tonight | Next | What it does |
|---|---|---|---|
| `PMUS_RN1_RANK_SHARE` | 0 | 1 | rank by our own modelled reward per dollar |
| `PMUS_RN1_JOIN_TOUCH` | 0 | 1 | rest at the best bid when the spread is two ticks or more and the price is calm; one tick back otherwise |
| `PMUS_RN1_BALANCED_IS_OPEN` | 0 | 1 | equal YES and NO is a fresh entry |
| `PMUS_TWO_SIDED_FILL_LIST_POLL` | 0 | 1 | batched fill detection |
| `PMUS_RN1_SLOTS_COUNT_INVENTORY` | 1 | 0 | carried inventory does not hold a quoting slot |
| `PMUS_RN1_REFILL_COOLDOWN_S` | 60 | 10 | per side, from that side's last fill |
| `PMUS_TWO_SIDED_MAX_MARKETS` | 4 | 8 | |
| `PMUS_TWO_SIDED_MAX_TRACKED` | 8 | 16 | |
| `PMUS_RN1_TOTAL_SPEND_USD` | 30 | 60 | campaign spend |
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` (ledger) | 5 | 10 | account limit; restarts the ledger |

Unchanged: one contract (`PMUS_RN1_QTY=1`); unpaired caps $6 campaign and
$3 a market; $8 a market; pair-cost limit 0.97; price band 0.30–0.70;
10-contract depth gate; campaign loss stop $4; daily loss halt $5; at-risk
budget $16; maker-only, post-only; no taker; international price not
required (the guard still applies where a price exists); fair-value test
off; every reconciliation, reservation, halt and watchdog control.

Left off: `PMUS_TWO_SIDED_ESPORTS_UNKNOWN_FIRST_MAP` (esports stays refused
until settlement terms for best-of-1 and best-of-2 are verified). Cricket
excluded.

Capital: eight markets rest about $7.9; $2.1 remains for inventory, so the
account limit binds after about two unpaired legs. That is the designed
brake at this stage.

## Prerequisites for $10 / eight markets

| # | Prerequisite | Status | Who |
|---|---|---|---|
| 1 | Release deployed locked and checked | prepared; needs approval | owner approves, I deploy |
| 2 | Account figure back under the limit. The owner's manual NHL purchase lowered the account's worst-case value by about $20, and the figure the bot reads is anchor minus that value. It stood at $21–27 against $5; at a $10 limit the bot would still be refused everything. | open — see "Manual orders" below | owner decides |
| 3 | Venue compatibility of batched fill detection (do the open-order list rows carry a filled quantity?) | being checked from production logs; otherwise proven in the first minutes of the campaign by the `fill_list_row_keys` line and the `fill_poll` heartbeat counter. If the rows do not carry it, the bot reads every order as today and eight markets exceed the request budget: the campaign then runs at four markets. | me |
| 4 | Owner's own gate for $10: "sufficient paid-reward evidence and verified emergency stop" | neither met. Paid evidence is $1.30 (22 Nov); rows for 23–25 Nov not posted. Signed emergency stop needs the owner to sign one marker (about 10 minutes in a browser terminal), then one authorized drill and halt reset. | owner: satisfy or waive in the authorization |
| 5 | Competitions in scope | to be named in the authorization. Modelled reward is concentrated in Setka table tennis and UTR; `ebf…` markets rank high but their sport and settlement terms are not yet confirmed and they are not proposed. | owner |
| 6 | Window | to be named; the bot relocks itself at the end | owner |

Disclosures the authorization must carry: no independent price check;
joining the best bid and the 10-second cooldown have no live evidence and
take fills sooner, adverse ones included; signed emergency stop unverified
unless item 4 is done; reward figures are modelled and post one to two
days late.

Authorization line (exact, with the blanks filled):

> Authorized: $10 / eight-market campaign on release bc3a5b6b9, account
> limit $10, campaign spend $60, competitions ___, ___ UTC to ___ UTC
> 25/26 Nov, on these terms.

## $25 / 16 markets — configuration for review

As above, with: account limit 25; `MAX_MARKETS` 16; `MAX_TRACKED` 32;
campaign spend 150; at-risk budget 16 → 24. Resting capital about $15.8.

Prerequisites beyond those for $10:

| # | Prerequisite | Status |
|---|---|---|
| 1 | Owner's gate: "consistent profitability across markets" | not met; needs settled results and posted reward rows from the $10 stage |
| 2 | Request capacity shown live at eight markets (modelled 0.38 a second at 8, 0.55 at 16, budget 1.0) | needs the $10 stage |
| 3 | At-risk budget raised 16 → 24 | a risk setting: needs approval |
| 4 | Lane cap: $20 today (the account exposure ceiling divided by the number of lanes). 16 markets with inventory can exceed it. Raising the ceiling also raises what the crypto lane is given unless the split changes. | needs a decision that does not increase the crypto controller's capacity |
| 5 | Campaign unpaired cap stays $6: at most about 12 one-contract legs unpaired across all 16 markets. | by design; no action |
| 6 | Enough eligible markets at one time. At 02:56 UTC, 72 paying markets were priced 0.25–0.75; modelled reward beyond the best 16 is small. | scope decision |
| 7 | Loss stop and daily halt at this size | owner's decision |

## Manual orders — accounting without weakening the limit

How the limit works today (ledger, `l5_portfolio_drawdown`): the account's
worst-case value is buying power plus guaranteed pair redemptions, with
every unpaired contract valued at zero. The figure is the owner-set anchor
(`L5_PORTFOLIO_ANCHOR_USD`) minus that value. It cannot tell a bot order
from a manual one, so a manual purchase reads as a loss of its full cost
until it settles, and stays a loss if it loses. That is why the bot was
refused from 02:19 UTC.

Reporting (done from now on, no change needed): bot trading P&L is built
from the bot's own order ids only; manual fills, their settlement and any
promotion credit are listed separately and excluded.

Enforcement — three options:

| | What | Protection | Cost |
|---|---|---|---|
| A | Leave as is. Manual trades consume the shared limit; the bot stops while they are open, and stays stopped if they lose. | strongest: one limit covers everything on the account | the bot cannot trade after a manual purchase larger than the room left, until the anchor is corrected |
| B | Treat a manual trade the way the design already treats a deposit or withdrawal: with the owner's explicit approval each time, move the anchor by the manual trade's cost, and by its proceeds when it settles. Recorded in the authorization file with the venue figures. No code change. | the bot's loss limit is exactly as strict as before; the manual position itself is no longer inside the limit (it is the owner's own decision and risk) | a manual step per manual trade; done wrongly it could hide a bot loss, so the adjustment must equal the venue-confirmed manual cash flow and nothing else |
| C | Ledger change: publish two figures — total account (everything, against a total-portfolio limit) and bot-attributable (own order ids, against the bot limit); the bot is refused if either is exceeded. | both protections kept, automatic | a release of the shared ledger, which the crypto lane also reads; days, not hours |

Recommendation: B now, for this one trade, once it has settled and the
venue's figures are in hand; C only if manual trading on this account
becomes regular. B is an explicit, recorded anchor correction, not a
silent rebase, and it needs the owner's approval with the exact amount.
