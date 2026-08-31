# Four-Trader Panel: What Actually Pays on Polymarket Crypto

Date: 2026-08-31. Per-trader details in `analysis/traders/<name>/workup.json`
and `analysis/l5/`. Coverage caveat applies to all rows: API offset caps mean
fills are a recent-months sample (windowed walks) while incentive rows span
further back; derived trading P&L covers only harvested windows. Directionally
robust, not audit-grade.

| | Universe | Style | Maker/Taker ($) | Median pair cost | Derived trading P&L | Incentives (obs.) | Merges |
|---|---|---|---|---|---|---|---|
| **L5** | BTC 15m (+5m taker book) | passive symmetric maker, defensive late completion | 69/31 | $0.971 | ≈ breakeven (+$174) | ~$2.7k/day maker-rebate + reward | — |
| **justdance** | 4h + 15m, BTC/ETH/SOL/XRP | pair-sweep & MERGE | 52/48 | $1.004 | **+$12.1k** | $64.3k, taker-rebate-led ($40.7k) | 9,044 / $5.24M |
| **bonereaper** | BTC/ETH 5m/15m (97% of activity) | aggressive pair-chaser | 50/50 | **$1.061** | **−$14.6k** | $7.7k | 35 / $39.6k |
| **0xf705** | daily BTC/ETH strike ladders | cross-strike merge arb | n/a (outside windows) | n/a | n/a | $29.5k, taker-rebate-led | 1,388 / $2.57M |

## What the panel establishes

1. **Incentives are the engine for every profitable operator observed.** Three
   different profitable archetypes (passive maker, pair-sweeper, ladder
   merger), one common denominator: rebate/reward income dwarfs or rescues
   trading P&L. Nobody in this panel wins on raw trading alone at scale.
2. **Pair-cost discipline is the winner/loser separator.** justdance pays
   $1.004 for pairs and nets +$12k trading; bonereaper pays $1.061 in the SAME
   5m/15m universe and bleeds −$14.6k, with too little rebate income to cover
   it. Six cents of discipline per pair is the entire difference.
3. **TAKER rebates matter as much as maker rebates** on international
   Polymarket crypto (justdance $40.7k, 0xf705 $27.6k taker vs smaller maker
   lines). The sweep-and-merge pattern (buy both sides ≈ $1, MERGE to
   collateral instantly, recycle capital, keep the rebate) is a distinct,
   repeated, working archetype.
4. **Implications for our Kalshi lane**: (a) on KXBTC15M/ETH15M there is no
   subsidy, so only genuine selection edge (frozen policy D) justifies
   quoting — which is what the shadow run is testing; (b) on the incentivized
   series (KXCRYPTOLEAD15M, commodity 15m launches) the panel's playbook
   applies: disciplined two-sided presence where the reward pool, not the
   spread, is the margin — with bonereaper as the standing warning about what
   happens when pair cost slips above par without a subsidy behind it.
