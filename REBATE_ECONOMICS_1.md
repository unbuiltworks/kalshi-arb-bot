# Maker rebates on this account — what the venue actually shows

**Located, not guessed.** A read-only survey of 17 candidate authenticated
paths under both verbs, 2026-09-19. Every prediction-market-shaped guess
404s: `/rewards`, `/rebates`, `/liquidity-rewards`, `/incentives`,
`/incentives/earnings`, `/prediction-markets/activities`, `/portfolio/
activities`, `/account/activities`, `/activities`. **No scraper was written
and nothing was read out of a screenshot.**

What exists:

| surface | carries |
|---|---|
| `POST /v1/transfers` | **the credits** — `{type, status, timestampms, eid, currency, amount, purpose}` |
| `POST /v1/tradevolume` | **the denominator** — per symbol, per day, `buy/sell_maker_notional`, `*_maker_count` |
| `POST /v1/notionalvolume` | fee-tier bps, 30d/1d notional |

## The confirmed credits

Seven, **$0.51 total**, every one `type: AdminCredit`, `purpose: "Predictions
maker rebate"`, `status: Complete`, each with its own venue `eid`.

| credited (UTC) | amount | eid |
|---|---|---|
| 2026-08-27 21:03:53 | $0.01 | 368822540373 |
| 2026-08-28 21:03:42 | $0.07 | 368823437387 |
| 2026-08-29 21:03:52 | **$0.05** | 368824547191 |
| 2026-08-30 21:03:41 | **$0.14** | 368825667773 |
| 2026-09-01 21:03:46 | **$0.17** | 368827790024 |
| 2026-09-08 22:27:16 | **$0.06** | 368835478761 |
| 2026-09-08 22:38:23 | **$0.01** | 368835488128 |

The five in bold are the ones in the Activity screenshots. The venue record
also shows **two the screenshots did not** — Aug 27 and Aug 28 — so the
authoritative total is **$0.51, not $0.43**.

## Maker volume → rebate → effective rate

**Two independent venue reads agree.**

```
venue  POST /v1/tradevolume, prediction rows   $52.2573
ours   POST /v1/mytrades, aggressor=false      $52.2604
difference                                     $0.0031
   = 2026-09-19, the day tradevolume has not yet posted
   = C8B's own maker fill, YES 0.01 @ 0.31
```

They reconcile exactly. That is a real cross-check, not a restatement.

```
confirmed credits  $0.51
maker notional     $52.2573        41 prediction rows, 2026-08-21 … 09-18
effective rate     0.009759  =  97.59 bps  =  0.98% of maker notional
```

**≈1% of maker notional**, at `WINDOW_LEVEL` attribution confidence.

## The per-day picture — observation, not attribution

| day | maker notional | maker fills | credit posted that day |
|---|---|---|---|
| 2026-08-27 | $5.64 | 12 | $0.01 |
| 2026-08-28 | $3.20 | 7 | $0.07 |
| 2026-08-29 | $5.25 | 2 | $0.05 |
| 2026-08-30 | $18.98 | 5 | $0.14 |
| 2026-08-31 | $0.25 | 1 | — |
| 2026-09-01 | $17.45 | 27 | $0.17 |
| 2026-09-02 | $1.47 | 3 | — |
| 2026-09-08 | — | — | $0.07 |
| 2026-09-18 | $0.0173 | 4 | — |

**No per-day rate is stated, and the table is not an attribution.** The
credits post at **21:03Z — 5:03pm ET, mid-day** — so a same-day mapping
cannot be right, and the data does not establish which activity window each
credit covers. Aug 31 and Sep 2 have volume with no adjacent credit; Sep 8
has two credits with no volume that day. The schedule is irregular and the
covered window is **not established**.

`/v1/transfers` carries **no order id, no symbol and no fill linkage**, so
**fill-level attribution does not exist** and none is invented. Every credit
records `attribution_confidence: UNATTRIBUTED` with that reason on the row.

## A mistake worth recording

The first run reported `rows_matched: 0` and asserted, in prose, that
`/v1/tradevolume` "does not cover prediction instruments, so it is the
spot/derivatives volume surface".

**That was false, and it was my filter.** The endpoint returns
`gemi-btc05m2608271730-up` in **lower case** while every other Gemini surface
— events, order history, mytrades, the order acks — returns
`GEMI-BTC05M…-UP`. A case-sensitive prefix matched zero of 62 real prediction
rows and turned that into a confident negative.

The coverage diagnostic added one commit earlier is what exposed it: it
printed the symbols the endpoint *does* carry, and they were plainly the
contracts the filter claimed were absent. That is the entire reason for
establishing a negative rather than asserting one. Both matches are now
case-insensitive and pinned by test.

## What this does and does not mean

* **It is real money, confirmed by the venue's own account record.** Seven
  credits, seven event ids, all `Complete`.
* **It does not make the L5 strategy profitable on Gemini.** The research
  question is unchanged: whether the L5 behaviour we reproduce *naturally*
  generates maker economics that materially improve executable expectancy.
  This is one input to model B.
* **C8B earned no assumed rebate.** Its fill was genuinely maker — `aggressor
  false`, `fee 0`, and its $0.0031 appears in the maker-volume denominator —
  but **no credit has been attributed to it**, and the confirmed line stays
  at what the account record shows. Maker evidence is not a credit.
* **≈1% is an aggregate over a period whose trading was not ours.** The
  Aug 27 – Sep 2 volume came from activity outside this programme. Whether
  the L5 policy's own maker surface earns at the same rate is exactly what
  the continuous evidence dataset is being built to answer, and it is not
  answered here.

**Nothing about size, risk, aggressiveness or capital changes because these
credits exist.**
