# R4 exact chronology — and a correction

Authoritative sources: `mandate_commit` rows in `/state/pmus-ws/ledger.jsonl`
(`committed_utc`), the journal's `mandate_admission` rows, and
`GET /v1/order/{id}`'s `lastTransactTime`. Recovered read-only, deployment
`2d8a29dd`.

| t (UTC) | event | source |
|---|---|---|
| 00:46:35.034013 | metadata pinned, `MARKET_STATUS_OPEN` | journal `market_metadata_pin` |
| **00:46:35.675345** | **UP `CSDKAWYZCYBQ` admitted + submitted** — BUY 10 UP @ 0.26, cash_at_risk $2.60 | `mandate_commit` / `mandate_admission` |
| **00:46:35.909277** | **DOWN `CSDKDZZVRYAQ` admitted + submitted** — BUY 10 DOWN @ 0.73 own / 0.27 wire, cash_at_risk $7.30. **+0.234 s after UP** | `mandate_commit` / `mandate_admission` |
| **00:46:36.003236** | **DOWN terminal `ORDER_STATE_REJECTED`**, cum 0. **+0.094 s after its own submit** | `GET /v1/order/CSDKDZZVRYAQ` `lastTransactTime` |
| **00:46:46.376683** | **UP `ORDER_STATE_FILLED`**, cum 10, avgPx 0.2600, commission −0.0200. **+10.701 s after its submit** | `GET /v1/order/CSDKAWYZCYBQ` `lastTransactTime` |

**The rejection precedes the UP fill by 10.373 s.**

## The correction

The adversarial review is right and my earlier framing was wrong in a way
that matters.

I described R4 as "first leg fills, then the complement is rejected". It was
not. Both legs were submitted **234 ms apart**, which is the frozen policy's
rule 4 — *two-sided passive acquisition at our touch, one clip per side* —
not an OPEN followed by a COMPLETE. The DOWN order was rejected while we held
**zero inventory**. The UP order then rested for 10.7 s as a maker quote and
filled.

Two consequences:

1. **The rejection cannot have been caused by holding UP inventory**, by a
   residual cap, or by anything downstream of a fill. At 00:46:35.909 the book
   was flat.
2. The intended 0.99 pair was a *simultaneous two-sided quote*, so its failure
   is a failure to get one side accepted, not a failure to complete a held
   residual.

`analysis/l5/rung_a/R4_TRADE_LEDGER_AND_SETTLEMENT.md` describes the DOWN leg
as "the complement leg of a two-sided acquisition", which is accurate; the
narrative ordering in my chat summary was not, and this file is the record.

## What the existing regression does and does not claim

`tests/test_r4_lifecycle_consolidation.py::test_r4_up_fills_then_complement_is_rejected`
exercises the lifecycle correctly and stays valuable, but its ordering is
**FILL then REJECT**, which is not the R4 event sequence. It is retained as a
lifecycle-correctness fixture and is being retitled accordingly; the
exact-chronology fixture is
`test_r4_exact_chronology_two_sided_quote_then_late_fill`.
