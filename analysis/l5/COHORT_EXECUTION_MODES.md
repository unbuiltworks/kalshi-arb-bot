# BTC15m is L5's only maker cohort — and the only one running the mechanism

Chain-attributed roles, cross-checked against the venue's own flag at
**1,270 / 1,270 (100 %)** across BTC15m and BTC5m.

| cohort | windows | fills | maker | taker | **maker %** | fills/window |
|---|---|---|---|---|---|---|
| **BTC15m** | 527 | 63,569 | 55,639 | 2,665 | **95.5 %** | **121** |
| BTC5m | 957 | 6,227 | 3 | 6,222 | 0.05 % | 6.5 |
| ETH15m | 29 | 117 | 0 | 117 | 0 % | 4.0 |
| ETH5m | 703 | 2,069 | 5 | 2,064 | 0.24 % | 2.9 |

## Sequence tables

**BTC15m** — 313 attributed first/complement sequences:

| sequence | n | share |
|---|---|---|
| **maker → maker** | **259** | **82.7 %** |
| taker → maker | 40 | 12.8 % |
| maker → taker | 10 | 3.2 % |
| taker → taker | 4 | 1.3 % |

**BTC5m** — 322 attributed sequences: **taker → taker, 322 / 322 (100 %)**.
Chain and feed agree 644 / 644.

**ETH15m** — 13 attributed sequences: **taker → taker, 13 / 13**. Chain and feed
agree 26 / 26. The module declines to call 13 sequences a reading, and so do I;
it is consistent with the feed's own 0 / 117 maker fills for this cohort, which
is the stronger evidence. (ETH5m attribution still running.)

So **every cohort so far outside BTC15m is taker on both legs**, on both the
chain and the venue's own flag, with no exceptions in 335 attributed
sequences.

---

## What this changes

### 1. There are two different L5s, and we have been averaging them

BTC15m runs **121 fills per window at 95.5 % maker**. That is a continuous
two-sided market-making operation. BTC5m, ETH15m and ETH5m run **3–7 fills per
window at ~0 % maker** — a low-frequency taker activity that is not the same
strategy and was never going to be reproduced by the same engine.

**Every cross-cohort statistic in this programme has been mixing them.** The
"L5 median pair cost 0.9646" figure spans both; so did "72.7 % two-sided".

### 2. It explains the ETH pair costs that looked irrational

ETH's median pair cost above **1.00** — a guaranteed loss on the matched leg —
made no sense for a liquidity provider. It makes complete sense for a **taker**:
crossing the spread twice costs about the spread twice. Those cohorts are not
paying for a pair, they are paying for immediacy, and whatever L5 gets out of
them is not pair economics.

### 3. It explains BTC15m's *good* pair cost

0.9624 is better than the touch sum we measure live (0.9930) because **he is
earning the spread rather than paying it, on both legs.** A maker-maker pair
collects roughly the spread twice; a taker-taker pair pays it twice. That is
most of the ~3.3 ¢ gap, and it is not available to a clone that joins the touch
and waits to be hit only sometimes.

### 4. The target is narrower and clearer than before

> **Reproduce BTC15m only. It is the one cohort where L5 is a maker, the one
> where the pair economics work, and the one where the two-sided mechanism
> exists at all.**

BTC5m should be dropped from the replication target — not because it is
unprofitable for him, but because **it is a different strategy**, and running
it through an engine built to imitate his market-making would imitate nothing.

### 5. And it sharpens the Gemini question

The open Gemini question was whether high placement reachability (85.5 %)
converts into fills. This says what has to convert: **resting maker fills on
both sides, repeatedly, within a 15-minute window.** Testing Gemini with short
horizons was testing the wrong thing.

## Provenance

- `analysis/l5/pair_formation/roles.py`, cohort reports `roles_btc15m.json`,
  `roles_btc5m.json`, `roles_eth15m.json` (ETH5m in progress)
- roles from the Polygon CTF Exchange `OrderFilled` counterparty slot, validated
  against the venue's published `maker_taker` at 1,270/1,270
- every row keeps tx, block, logIndex, contract and the node that served it
