# Validation campaign, redesigned around maximum settlement loss

23 Nov 2026, 12:20 UTC. **Nothing here is approved, deployed or armed.**
The $97 Champions League proposal (`CAMPAIGN_PROPOSAL_UCL_20261125.md`) was
not approved and is kept only for comparison. The $3 drawdown limit is
unchanged. The Champions League is approved for development and market
evaluation only.

## Three numbers, kept apart

| Term | Meaning |
|---|---|
| Capital committed | cash tied up in resting bids and held contracts |
| Maximum settlement loss | what is lost if every resting bid that can fill does fill, on the losing side, and is held to settlement |
| Loss stop | a threshold after which the bot opens nothing new. It does not undo positions already held, so it is not a cap on loss |

For one market with a bid on each side, the maximum settlement loss is the
dearer bid times the size: if both fill, the pair pays out more than it
cost. For a bid on one side only it equals the capital committed.

## New evidence from the venue's reward records (read 11:44 UTC today)

- **Serie A, 22 Nov, in-play, two contracts a side:** two rewards are now
  pending, $0.52 and $0.78, total $1.30, one for each market traded. That
  campaign's settled trading result was −$2.40. If the rewards are paid it
  nets −$1.10.
- **Since the bot started (14 Nov):** 85 reward rows, $17.58 accrued:
  $15.75 paid, $1.34 pending, $0.49 skipped. All of it was earned in-play
  at about two contracts a side. Most came from table tennis ($8.22),
  Counter-Strike ($3.17) and Dota 2 ($2.69).
- **No pre-game reward has ever been earned by this account's bot**, because
  it has never quoted pre-game. Every pre-game figure in these notes is a
  model output.
- **The payout rule is not established.** I had inferred that a day's
  rewards under $1 are not paid. On 17 Nov tennis rows totalling $0.40 were
  paid while esports rows totalling $0.47 were skipped, and rows of $0.01
  are paid on most days. Whatever decides "skipped" is not simply a $1 daily
  total. Skipped rows still show the amount accrued, so even an unpaid test
  measures the reward.
- **Rows carry no period, share or reason**, so a reward cannot be split
  between pre-game and in-play from the venue's record alone. A campaign
  has to run one phase at a time to attribute it.

## What the Serie A result implies

Two contracts a side commits about $1.94 a market. A reward of $0.52 to
$0.78 a market for one match is 27% to 40% of that in an evening. Pre-game,
the model gives about 1% a day. On this evidence the reward is earned
in-play; the loss is too. The open question is unchanged: whether in-play
fills can be made cheap enough, and the new build's fair-value rules,
price-move pause and kickoff re-check are aimed at exactly that.

## Options, sized against the worst case

Rates for Champions League markets are from today's books, two days before
the matches; match-day depth will be greater, so the low end of each range
is those rates cut to a third.

| | A. In-play and pre-game at the existing limits | B. Pre-game accrual probe | C. Bounded pre-game | D. The $97 proposal |
|---|---|---|---|---|
| Purpose | Repeat the one result that has earned a reward, on the new build, measured by phase | Find out whether pre-game quoting accrues anything at all | Pre-game reward large enough to see clearly | Not approved |
| Markets | two matches, one market each | one market, cheap side only | three markets, cheap thin side only | five |
| Order size | 2 a side | 4, one side | 10, one side | 20 a side |
| Capital committed, at most | $16 (campaign spend cap; about $3.90 resting at a time) | $1.32 | $9.80 | $97 |
| Maximum settlement loss | $6 (the campaign's cap on unpaired exposure, which counts resting bids) | $1.32 | $9.80 | $63 |
| Loss stop | $4, unchanged | not needed: one bid | $4, unchanged | $4 |
| Reward basis | $1.30 pending on 22 Nov at this size on two Serie A markets | model: $0.06 to $0.17 | model: $0.36 to $1.07 | model: $0.60 to $3.30 |
| Worst case per dollar of expected reward | about 5 to 1 | about 8 to 22 to 1 | about 9 to 27 to 1 | about 19 to 105 to 1 |
| Fits the $1.33 left under the $3 limit | No: needs an explicit $6 budget | Yes | No: needs $9.80 | No |
| New code needed | none | a setting naming the exact market and side | the same setting | none |

- **A** uses only limits that have already been run: two-contract orders,
  two markets, $16 spend, $6 unpaired, $4 loss stop, $5 daily halt. Its
  worst case is the $6 cap. On 22 Nov the same exposure lost $2.40 and
  earned $1.30.
- **B** is the only option inside the present limit. It cannot pay
  meaningfully; it answers one yes-or-no question for at most $1.32.
- **C** and **D** rest on a pre-game reward that has never been observed.

## Recommendation

Run **B** first if a pre-game answer is wanted at almost no risk, and treat
**A** as the validation that matters: it is the only design whose reward is
backed by a venue record, and its worst case is $6. A needs an explicit
decision that $6 of maximum settlement loss is authorised for one window on
top of the $1.67 already used. Neither is requested here.

## Checked today, and what is still to confirm

| Item | Status |
|---|---|
| Fixtures | Champions League: nine matches 24 Nov, nine 25 Nov, from the venue's own listing. No Liga MX in the next four days |
| Programme terms | $160 pre-game and $1,280 in-play per market, target 10,000 a side, read per market from the venue |
| International price coverage | 25 Nov: priced for six matches; not for Sabah v Barcelona, Atlético v Viking, Shakhtar v Fenerbahçe |
| Price band | of the 18 priced markets on 25 Nov, 9 are inside 25c to 75c; no draw market is except Sporting v Manchester United |
| Liquidity | read today; must be read again on match day before any market is named |
| Payout rule | not established |
| Pre-game accrual | never observed |

## Before any of these could run

Both release deployments verified; the death drill passed or its risk
explicitly accepted; an explicit risk budget; explicit permission for the
competition; and, for B and C, the one small setting that pins the exact
market and side.
