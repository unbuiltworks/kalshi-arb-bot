# Reward-eligible sports markets: scan for review, 24 Nov 2026 23:30Z

Read-only, public venue data (`scan_20261124/`). 787 sports markets live or
starting within 30 hours; 393 carry a reward programme; 217 were in a
paying period. Reward figures are MODELLED shares under the venue's
published scoring rule for bids joined at both touches. They are not
payments, and the model is known to be unreliable (see "Serie A" below).
A day's reward under $1 is not paid.

## By competition (markets paying now, with a two-sided book)

| Competition | Markets | Pool per hour | Median contracts at the touch | Modelled $/hour, 1 contract | 10 contracts | International price | Approved |
|---|---|---|---|---|---|---|---|
| Table tennis, Setka Ukraine | 28 | 0.83 | 1 | 0.165 | 0.52 | no | no |
| CS2 (in play) | 1 | 26.67 | 76 | 0.131 | 1.25 | no | no |
| Table tennis, Setka Moldova | 1 | 0.83 | 0 | 0.028 | 0.19 | no | no |
| UTR tennis | 33 | 1.67 (live 16.67) | 1 | 0.017 | 0.15 | no | no |
| ATP doubles | 11 | 1.67 | 20 | 0.004 | 0.04 | partly | no |
| ATP | 11 | 6.67 (live 61.33) | 1,443 | 0.003 | 0.03 | yes | yes |
| NHL | 1 | 5.50 (live 350 pool) | 27,949 | 0.0001 | 0.001 | yes | yes |
| Champions League | 27 | 0.09 now; day-of pool 160, live pool 1,280 | 11,000 | 0.00001 | 0.0001 | not configured | no |
| E-soccer (three leagues) | 96 | 0.33 | 0 | ~0 | ~0 | no | no |
| NBA / NFL | pre-game | no pre-game period (live pools 30 / 45) | | 0 | 0 | yes | yes |

Shares are concave in thin books (Setka: 1 contract 0.165, 10 contracts
0.52 an hour: ten times the capital for three times the reward) and linear
but tiny in deep books (ATP, NHL, Champions League).

## Concentrating $5 in fewer, thinner markets

No. In thin books the first contract takes most of what one quoter can
get, so the modelled reward per dollar is highest at ONE contract spread
over many thin markets, and falls as size is concentrated. In deep books
reward is proportional to size and negligible at any size $5 can buy:
reaching the $1 minimum on a Champions League market before kickoff would
take on the order of thousands of contracts.

## What the thin markets actually paid and cost (14-18 Nov, confirmed)

| Competition | Markets | Rewards paid | Trading | Net |
|---|---|---|---|---|
| Table tennis, Setka Ukraine | 37 | +6.30 | -8.06 | -1.76 |
| Table tennis, Setka Moldova | 10 | +1.92 | -0.33 | +1.59 |
| Dota 2 | 10 | +2.29 | +0.10 | +2.39 |
| CS2 | 32 | +3.10 | -6.28 | -3.18 |

Paid reward per market-day was about $0.17 in Setka Ukraine, against a
trading loss of about $0.22. Fills there were marked 3 to 4 cents against
us within 30 seconds, and there is no international price, so the
controller's fair-value test has nothing to work with: under the current
build these markets get no bid at all. Quoting them means relaxing the
international-price requirement, which is one of the owner's standing
controls, and the owner has ruled table tennis and Dota 2 rewards "not
sufficient evidence to enable those markets".

## Serie A, 22 Nov: correction

The $1.30 was earned IN PLAY, not pre-game. The window was 14:05-14:50 UTC
and both matches kicked off at 14:00 UTC. Two-contract quotes for 45
minutes on two markets earned $0.52 and $0.78. The touch was thin in play
(the log shows 0.1 to 5.7 contracts at the touch), and soccer live pools
are large (Champions League: $1,280 per market). The pre-game model above
would have predicted almost nothing, so it does not describe this case.
What paid was a small quote near the touch in a thin in-play soccer book.
The cost was jump risk: the same window lost $2.68 on one unpaired leg.

## Upcoming soccer

| Competition | Next fixtures (UTC) | Markets | Status |
|---|---|---|---|
| Champions League | Wed 25 Nov, 17:45 and 20:00 | 27 (9 matches) | not approved; international three-way price not configured for it (existing setting) |
| Europa League | Thu 26 Nov | 54 | not approved |
| Serie A | Fri 27 Nov 19:45; Sat 28 Nov 14:00, 17:00, 19:45; Sun 29 | 30 | approved |
| Premier League | Fri 27 Nov 20:00; Sat 28 Nov 15:00 (x2), 17:30; Sun 29; 1-2 Dec | 57 | approved |
| La Liga | Fri 27 Nov 20:00; Sat 28 Nov 13:00, 15:15, 17:30; Sun 29, 30 | 36 | approved |
