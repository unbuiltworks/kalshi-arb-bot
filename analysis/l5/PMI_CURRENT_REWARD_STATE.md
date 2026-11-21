# Polymarket International crypto rewards — current state, measured 2026-09-10

OWNER: "establish the current Polymarket International crypto reward state as
of today, September 10, 2026. Don't infer continuation from August
documentation."

Measured, not inferred. Raw capture:
`analysis/l5/evidence/PMI_REWARD_STATE_2026-09-10/reward_state.json`.

## Verdict: EXPIRED, 21 of 21 families. Zero ACTIVE. Zero UNKNOWN.

**Fetched at 2026-09-10T14:44Z.**

| asset | 5m | 15m | 4h | min qualifying size | max qualifying spread |
|---|---|---|---|---:|---:|
| BTC | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |
| ETH | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |
| SOL | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |
| XRP | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |
| HYPE | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |
| BNB | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |
| DOGE | EXPIRED | EXPIRED | EXPIRED | 50 | 4.5 |

**The markets are alive; the rewards are not.** Every family returns
`closed: false` and is minting new windows on schedule — BTC and ETH 5-minute
markets were being created every 300 seconds during the capture. What is gone
is the rate:

```
CLOB /markets/<condition_id> -> rewards:
  { "rates": null, "min_size": 50, "max_spread": 4.5 }
```

`rates: null`. The qualifying *parameters* survive — minimum size 50, maximum
spread 4.5 — so the reward scaffolding is still configured on every market.
No rate is attached to it.

### Two independent confirmations

1. **Per-market CLOB config**: `rates: null` on all 21 families (7 assets x
   5m/15m/4h), queried individually by `condition_id`.
2. **The rewards-enabled index**: a full paginated sweep of
   `clob.polymarket.com/sampling-markets` returned **17,562 reward-bearing
   markets, of which the number containing `updown` is ZERO.** 121 mention a
   crypto ticker; **none** is a short-horizon (5m/15m/4h) market. Those 121
   are long-dated event markets ("will-bitcoin-reach-160000-by-...") paying
   `rewards_daily_rate` of $0.001–$10.

The August program was published as running "through August". It did not
continue into September, and this is now established from the venue's own
live API rather than from the August announcement.

## The metric that matters: reward $ per $1 of concurrent capital per day

Not turnover, not pool size.

| environment | reward $/day | concurrent capital | **$ per $1/day** | basis |
|---|---:|---:|---:|---|
| **PMI, August (L5 actual)** | 2,430 | 1,830 (mean) | **1.33** | measured from credits + occupancy sweep |
| PMI, August (L5, on peak) | 2,430 | 4,612 (peak) | 0.53 | same |
| PMI, August — REWARD only | 1,079 | 1,830 (mean) | 0.59 | same |
| **PMI, today** | **0.00** | n/a | **0.00** | `rates: null`, 21/21 families |
| Gemini (PRIOR read, unverified) | 11.36 per event | ~10 to qualify | ~1.1 | see caveat |

L5's August environment paid roughly **$1.33 of incentive per dollar of
working capital per day**. That is the number that made $480/day reachable on
four figures of capital, and it is the number that is now zero on this venue.

### The Gemini row is a PRIOR READ and is not evidence yet

The $11.36/event/day pool and the zero-qualifying-makers observation come from
an earlier live read in this programme, not from a fetch made today, and the
"~$10 to qualify" is *derived* from the published minimum (10 contracts,
two-sided, at a ~$0.50 midpoint), not observed. The whole row is therefore
**UNVERIFIED** and is exactly what the read-only Gemini recovery exists to
settle. It is placed here for shape, not for decision.

If it survives verification it is a genuinely different proposition from
Polymarket's August: not "take a share of a large pool" but "be the only
qualifier in a small one". Comparable reward-per-capital, tiny absolute
dollars per event, and therefore scalable only by breadth — the same lesson
the August event study produced, arriving from the opposite direction.

## What this means

1. **The August environment cannot be re-entered on Polymarket International.**
   Any plan whose economics depend on that program is dead as written.
2. **The behavioural finding survives the program's expiry.** Breadth-over-size
   is a property of how minute-sampled, share-of-market reward scoring works,
   not of one August pool. It applies to any venue paying that shape — which
   is what makes the Gemini verification the live question.
3. **$480/day is not a bankroll problem, and now it is not an August problem
   either — it is a "find an ACTIVE reward environment" problem.** Nothing in
   today's PMI data supports approaching that target on that venue by trading
   alone.
4. **Do not re-tune the PMUS engine for minute-sampled resting rewards.** Per
   the owner: the 5-second rest/requote cycle answers execution fidelity, a
   different question. Reward behaviour belongs in a separate layer —
   `L5 trading behaviour + resting-liquidity reward policy + venue safety` —
   and there is currently no ACTIVE PMI program for that layer to target.

## Still unresolved, carried forward

- **MAKER_REBATE grew 6.65x against 3.09x volume** in the August event window.
  Unexplained. It may indicate a second rate change or an accounting error on
  our side, and it is the one number in the reconstruction that does not fit.
- **Residual drag grew 4.71x against 3.09x capital.** L5's program paid enough
  to absorb it. No transfer may assume ours will. The bar stays:
  `credited rewards + rebates + trading P&L > residual losses + fees`, with
  credited meaning venue-credited, not modelled.

## Method note

Gamma (`gamma-api.polymarket.com/markets?slug=...`) was used to resolve live
slugs to `conditionId`; the CLOB (`clob.polymarket.com/markets/<id>`) is the
authority for the reward block. Both are public and unauthenticated; no
account credentials were used and nothing was submitted. Slugs were
constructed from the current epoch aligned to each horizon, so the markets
checked are the ones trading at the moment of the fetch.
