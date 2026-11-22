# Daily-crypto reward test - proposed $300-capped configuration (DISARMED)

Status: PROPOSAL. Not deployed, not armed, not authorized. Owner set $300 as a
maximum test allocation. Implementation and deployment belong to the sports
session on `pmus-spring-l5-probe`; no new service. Evidence and gaps:
`CRYPTO_REWARD_MECHANISM_AUDIT_20261122.md`.

## Question the test answers
1. Does the venue credit a liquidity reward that names a crypto market?
2. How is the $150 pool divided between ETH, SOL and XRP?
3. What do fills cost us (venue cash, pair completion, residual at settlement)?

## Proposed parameters
| Item | Value | Why |
|---|---|---|
| Markets | current ETH, SOL, XRP daily up/down (one each) | the paying programme; BTC daily never paid and needs ~$8,700 |
| Order size | 100 contracts per side per market | smallest size that models above the $1/day payout floor on every market one tick back |
| Price | one tick behind the best bid on each side, never improving the spread | at the best price share is ~3x higher but so is pick-off; two ticks back falls under $1 |
| Execution | maker only; no taker completion, no taker exit | taker is not authorized |
| Resting capital | about $100 per market, $300 total | = owner maximum |
| Worst lone-leg cost | about $50 per market, $150 total | one side fills, market settles against it |
| Crypto loss stop | proposed $30 venue-cash loss across the test, latched, cancel all crypto orders | owner to set; the $5 sports halt cannot bound a 100-contract leg |
| After a fill | keep the opposite quote resting (pair pays $1 at once); no new same-side order until paired or settled | bounds each market at one lone leg |
| Cutoff | stop quoting 2 h before the market ends (existing behaviour) | final-hours pick-off |
| Duration | 3 market days, then stop and reconcile | credits post within ~7 business days |
| Combined ceiling | sports limits unchanged; crypto counted separately under its own $300; one explicit combined total enforced in code | sports must not inherit crypto room |

Modelled reward at these settings: about $1.8 (ETH), $5.0 (SOL), $1.9 (XRP)
per day - roughly $9/day in total IF the pool is split evenly. That is a
scenario. Confirmed crypto reward income to date: $0.

## Code required first (config alone cannot do this on `3e2cc9002`)
- crypto branch in `step_market` ahead of the RN1 branch;
- crypto-only quantity, depth, per-market cap, total cap and loss stop
  (today these are shared with rewarded sports);
- loss stop and P&L from venue cash, as in the sports pilot reconciliation;
- include `daily_event` in the periods the crypto path accepts;
- explicit bypass of the sports open-EV gate and international guard for
  crypto, replaced by the caps above;
- crypto results reported separately: fills, pairs, residual at settlement,
  venue cash change, and reward credit rows naming the market.

## Pass / fail
- PASS: a credit row names a crypto market AND venue cash change plus credited
  rewards is positive over the test.
- FAIL: no credit after the venue's payment window, or cash loss exceeds
  credited rewards. Stop; do not scale.
