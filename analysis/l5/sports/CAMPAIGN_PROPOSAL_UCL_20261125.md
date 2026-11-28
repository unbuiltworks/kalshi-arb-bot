# Campaign proposal for approval — Champions League, Wednesday 25 Nov 2026

> **NOT APPROVED (owner, 23 Nov).** A $1.14 reward target against a $63
> worst case does not line up. Superseded by `CAMPAIGN_REDESIGN_20261123.md`.
> Two statements below are also wrong on later evidence: the payout minimum
> is not established, and no pre-game reward has ever been observed.

Prepared 23 Nov 2026, 03:00 UTC. **Nothing here is approved, deployed or
armed.** Production is unchanged: sports bot on `158685d3a`, locked, no
resting orders. This is one campaign, stated so it can be approved or
declined as a whole.

## The question it answers

Can pre-game resting quotes earn a credited reward that exceeds what the
fills cost? It is sized to be the smallest campaign whose predicted reward
clears the venue's roughly $1 payout minimum, so a credit either appears or
the prediction is shown wrong.

## Why this day

- Liga MX has no fixture in the next four days. The Champions League has
  nine matches on Tuesday and nine on Wednesday, each market with a $160
  pre-game pool (Liga MX: $180) and a 10,000-contract target.
- Wednesday leaves Tuesday for the two things that must come first: the
  watchdog release with its production drill, and a read-only measurement of
  Tuesday's Champions League pre-game books, which replaces the assumed
  reward rate below with a measured one for the same programme.

## Exact markets

One market per match, as the engine requires. Each is inside the 25c to 75c
band and can be priced by the international guard. All kick off 20:00 UTC.

| Match | Market | Bid / ask now |
|---|---|---|
| Club Brugge v Liverpool | `atc-ucl-bru-liv-2026-11-25-liv` | 0.59 / 0.60 |
| Inter v Stuttgart | `atc-ucl-int-stu-2026-11-25-int` | 0.69 / 0.70 |
| Lille v Bayern | `atc-ucl-lil-bay-2026-11-25-bay` | 0.60 / 0.61 |
| Paris Saint-Germain v Roma | `atc-ucl-psg-rom-2026-11-25-psg` | 0.69 / 0.70 |
| Sporting v Manchester United | `atc-ucl-spo-mnu-2026-11-25-mnu` | 0.36 / 0.37 |

Not used: Sabah v Barcelona, Atlético v Viking and Shakhtar v Fenerbahçe
(no international price to check quotes against); Slavia Praha v Villarreal
(17:45 kick-off, kept out so one window covers every market); and the draw
markets, which sit below the 25c band.

If a market has left the band or lost its international price by the
morning, it is dropped, not replaced.

## Terms

| Item | Value |
|---|---|
| Window | 14:00 to 20:00 UTC, 25 Nov: the whole pre-game reward period |
| Phase | Pre-game only. Every order in a market is cancelled at 19:55; the window relocks at 20:00 |
| Order size | 20 contracts a side |
| Markets at once | 5 |
| Quotes | Maker-only, both sides, each side on its own marginal test; pair cap 0.97; taker off |
| Hysteresis | Off |
| Capital committed at most | $97 (5 markets × 20 × $0.97) |
| Portfolio cap on resting orders plus unpaired inventory | $100 |
| Loss stop | $4 at-risk, unchanged. One unpaired fill of 20 contracts exceeds it, so new quoting stops after the first unpaired fill |
| Loss if one bid fills and that side loses | $7 to $14 |
| Strict worst case | $63: the dearer side of all five markets fills before the stop acts, and all five lose |
| Daily halt | $5, unchanged |
| Unpaired fills after 20:00 | Held to settlement. No in-play orders in this campaign |

## Reward-credit target

- **Target:** a credited pre-game reward of at least $1.00 for 25 Nov in the
  Champions League pre-game programme.
- **Prediction:** $1.14 at 0.19c per contract-hour, which is today's books
  cut to a third to allow for the extra depth on match day.
- **Range:** $0.60 if the rate is like Liga MX's team markets on 22 Nov
  (0.10c), up to about $3.30 at today's books uncut.
- **Go or no-go on the day:** a scan at 13:30 UTC on the actual books. Under
  $1.25 predicted, the campaign does not run; the size is not raised to
  compensate.
- **What is reconciled afterwards, separately:** credited reward against
  predicted; qualifying resting time; fills and their markouts; settled
  result of any inventory. All counted by phase in the new build.

## What the evidence says to expect

- Pre-game Liga MX prices moved about one tick an hour and about 1,300
  contracts an hour traded against 10,500 resting at the best price, so
  fills should be few. Each one is a position of about $7 to $14.
- A fill held to settlement is close to a fair-odds bet with a small
  adverse tilt. Its result is large next to a $1 reward: one losing fill
  costs more than the campaign can earn in rewards. The campaign measures
  whether fills are rare enough for that not to matter.
- At best this earns about 1% of committed capital in a day. It tests the
  reward mechanism; it is not yet a route to the daily target.

## The decision this needs

The account limit as it stands counts committed capital as lost: $1.67 of
$3.00 is used and $1.33 is left. This campaign commits $97. It cannot run
without an explicit decision by the owner on the account's risk budget, for
example a committed-capital cap for this one window alongside the unchanged
limit on money actually lost. No such change exists in the code today and
none is assumed.

Also needing explicit approval: the Champions League as a permitted
competition; the order size of 20; the per-market and campaign caps that
size implies; and deployment of the two release candidates.

## Gates before arming

1. Watchdog release `release/l5-halt-watchdog-ce26e2a9e` deployed, its two
   settings on, and the production death drill passed (one small resting
   order, the bot killed for the drill window, the watchdog cancels it).
2. Sports release `release/pmus-sports-rn1-1b9f4325f` deployed locked, with
   a locked dry read confirming the five markets are priced and ranked.
3. Tuesday's read-only measurement of Champions League pre-game books.
4. The 13:30 UTC scan on Wednesday.

Any gate failing means the campaign does not run.
