# L5 BTC15m maker/taker sequence table — from chain evidence

**Established from the Polygon CTF Exchange `OrderFilled` log. Never inferred
from fees. Validated against the venue's own published flag: 626/626 agreement
(100 %).** Every row keeps its transaction hash, block and log index.

---

## The table

**313 fully attributed BTC15m first/complement sequences:**

| sequence | n | share |
|---|---|---|
| **maker → maker** | **259** | **82.7 %** |
| taker → maker | 40 | 12.8 % |
| maker → taker | 10 | 3.2 % |
| taker → taker | 4 | 1.3 % |

| leg | maker | taker |
|---|---|---|
| first fill | 269 (86.0 %) | 44 |
| **first opposite fill** | **299 (95.5 %)** | **14** |

**L5 almost never reaches out for the complement.** In 95.5 % of sequences the
complement was a maker fill — his order was resting and someone hit it. He is
not going and getting the other side; the other side comes to him.

**This substantially elevates the resting-order hypothesis**, as predicted. It
does not by itself show *when* those orders were placed — that is the bound
below.

## Coverage, and a regime boundary

| month | attributed |
|---|---|
| 2026-03 | 0 of 73 |
| 2026-04 | 0 of 120 |
| **2026-08** | **313 of 313** |

March and April are **not a decode failure**. Those fills route through a
different contract (`to` = `0xe3f18acc…`, not the CTF Exchange) and emit no
`OrderFilled` event at all — only USDC transfers, ERC1155 `TransferSingle` and
that contract's own events. **Two different venue mechanisms, and only the
later one exposes the role.** They are reported as unknown rather than guessed.

## Pre-resting bounds, recomputed on chain roles

| assumed reactive latency | pre-resting (reaction ruled out) | requires reaction | unresolved |
|---|---|---|---|
| **2.1 s** (structural floor) | **37 (11.8 %)** | 14 | 262 |
| 5.0 s | 74 (23.6 %) | 14 | 225 |
| 10.0 s | 123 (39.3 %) | 14 | 176 |

The 2.1 s row is a hard floor, not a judgement: a fill's timestamp is its
block's, and a reactive complement needs a later block than the first leg.

**Proven-non-reactive outnumbers proven-reactive 2.6 : 1 at the floor and
8.8 : 1 at 10 s.** Still a lean, not proof — 84 % is unresolved at the floor.

### And the number that binds us regardless

> **54.3 % of L5's complements fill within our 15.16 s observation lag.**

More than half of them are already done before the venue publishes his *first*
leg. That is independent of whether he pre-rests: **no reactive policy of ours
reaches them, however he placed his.**

## Three wrong readings before this one

The role is not in the topic position, and it took three attempts to see that.

| version | rule | agreement with the venue's flag |
|---|---|---|
| 1 | topic 2 = taker, 3 = maker | **1 / 60** |
| 2 | topic 2 = maker, 3 = taker | 568 / 626 |
| **3** | **counterparty slot decides** | **626 / 626** |

One `matchOrders` emits **two** `OrderFilled` logs — one per maker order, plus
one for the taker's own order — and in the taker's own log the counterparty
slot holds **the exchange contract** rather than a trader:

```
feed says L5 TAKER:  topic2=0x8ec4…  topic3=L5          (the maker's log)
                     topic2=L5       topic3=0xe1111800  (L5's own → TAKER)
feed says L5 MAKER:  topic2=L5       topic3=0xf0dd…     (L5's own → MAKER)
                     topic2=0xf0dd…  topic3=0xe1111800  (theirs   → taker)
```

So `topic2` means *whose order it was*, never *which side provided liquidity*.

Version 2 was validated on 70 fills and passed 69 — which is exactly how a
plausible wrong rule survives a small sample. It took the full 626 and a
single-leg, exact-match disagreement to expose it. **None of this affected
price or size attribution, so the promotion gate's G-C4 figures are unchanged.**

## Provenance

- `analysis/l5/pair_formation/roles.py` · `tests/test_l5_chain_decode.py`
- `analysis/l5/pair_formation/roles_btc15m.json` — every row with tx/block/logIndex
- receipts cached at `analysis/l5/pair_formation/receipts.jsonl` (1,012)
- archive RPC `polygon.drpc.org`; `publicnode` prunes before 2026-08-28 and
  returns NULL, which would have silently capped this at 59 % of windows
