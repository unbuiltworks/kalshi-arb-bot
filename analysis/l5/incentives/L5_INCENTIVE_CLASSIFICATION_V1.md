# L5_INCENTIVE_CLASSIFICATION_V1 — what L5 is paid, and by whom

P1. Every stream kept separate. Nothing allocated to a fill without evidence.
Nothing used to rescue trading economics.

## The account-level record, from the venue's own activity feed

Reconstructed by `analysis/l5/exploratory_v2/run_vol_pair.py::incentive_context`
over L5's public activity (`INCOME_TYPES` = `MAKER_REBATE`, `TAKER_REBATE`,
`REWARD`). **203 rows, $71,551.74 total.**

| stream | rows | USD |
|---|---|---|
| MAKER_REBATE | 110 | **$35,228.32** |
| TAKER_REBATE | 73 | $7,525.73 |
| REWARD (liquidity) | 20 | **$28,797.69** |

By month: 2026-04 $918 · 05 $2,004 · 06 $1,834 · 07 $5,773 · **08 $61,022**.
August is **85%** of everything and is the first month with any `REWARD` row.

**These are ACCOUNT-LEVEL and stay `UNATTRIBUTABLE` to a market or a fill.**
The venue publishes no per-market reward row for another trader's account, so
any split across markets would be invented. Alongside them, BTC15m trading
economics were **+$18,067** across the whole sample — incentives are **4.0×**
that, and in August alone **44×** the month's BTC15m trading P&L.

## Grok's two proposed addresses — verified independently on chain

| claim | address | verdict |
|---|---|---|
| "possible pUSD" | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | **CONFIRMED** |
| "possible distributor" | `0x3a9418b2651c8164db5ebc56f12008137865e0f7` | **UNVERIFIED** |

**pUSD is confirmed, and it is not an incentive channel.** Direct `eth_call`:
`name()` → **"Polymarket USD"**, `symbol()` → **"pUSD"**. It also appears in
L5's own transaction receipts, which is independent of the lead. A bounded
`eth_getLogs` over the most recent ~2,000 blocks (~70 minutes) found **14
Transfer events to L5 totalling 6,403.35 pUSD**.

**Those transfers are cashflow of unknown composition, not incentives**, and
are recorded that way: pUSD is the settlement currency, so sale proceeds,
redemptions and any credits all move through it. Nothing here separates them,
and no part of that 6,403.35 is called a rebate or a reward.

**The proposed distributor is a deployed contract and nothing more is
established.** It has code, but returns no ERC-20 `name()` or `symbol()`, and
sent **zero** transfers to L5 in the same sampled window. It does not enter
canonical evidence.

## A lead the pUSD result opens, stated as a lead

L5 settles in **pUSD**, the Polymarket US dollar. The public OrderFilled
archive that does **not** contain him
(`analysis/l5/causal/EXTERNAL_LEADS_VERIFICATION.md`) covers *"CLOB V2
Exchange"* contracts. **If the pUSD-settled deployment uses a different
exchange contract set, that would explain the absence.** It is a candidate
explanation and **untested** — our own hand-decode of an L5 transaction shows
the same exchange address that appears in the archive's sample row, so it is
not a clean story yet.

## What remains UNATTRIBUTABLE, and stays that way

* which market, fill or parent order earned any rebate or reward;
* which incentive **program** paid it (no program identifier is published on
  the activity row);
* whether any of it would be available to our account, which receives none of
  it today.

**No incentive is netted into trading P&L anywhere in this repository**, and
the August result — 59% of all BTC15m spend returning **3.7 bp** while
account-level incentives ran $61,022 — is reported as the warning it is, not
rescued.

## Verification method, for reuse

Everything above came from: the venue's own activity feed for the account-level
totals; `eth_call` of `name()`/`symbol()` for contract identity; and a bounded
`eth_getLogs` for actual transfers. Public Polygon RPCs refuse a 40,000-block
`eth_getLogs` and accept 2,000, so wider history needs a different provider —
a retrieval limit, measured, not a conclusion about the data.
