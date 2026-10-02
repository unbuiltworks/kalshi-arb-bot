# P0: PMUS_BOOK_ROWS completeness

`0d560681` is frozen; the 1300z result stands as instrumentation/discovery evidence
and is **not** rescored. Nothing was altered in trade collection, strategy logic,
fill assumptions, `82714061`, or any historical file.

## The mechanism, located in code

`src/pmus_ws_evidence_recorder.py`, in `emit_new_books`:

```python
out[slug] = fresh[-cap:]
```

`cap` is `PMUS_WS_BOOK_LOG`, applied **per market per emission**, and the slice
keeps only the **newest** `cap` rows. Everything older in that emission is
discarded with no counter. It is not a rate limit; it is a truncation of the
variable the study is about.

## Quantified from production BEFORE changing anything

Measured over the harvested 1245z–1330z logs at the then-current cap of 50
(`scripts/pmus_book_completeness_audit.py`):

| quantity | value |
|---|---|
| states generated (Δ `identity.book_updates`) | **17,072** |
| states emitted | 9,422 |
| **states dropped by the cap** | **7,825 — 45.8%** |
| market-emissions at the cap | **180 of 198** |
| generated per market per emission | p50 98, p90 100, p99 101, max 102 |
| emission cadence | p50 10.041 s, p90 20.023 s, max 20.143 s |
| log line bytes | p50 7,629, max 8,375 — **10.2%** of the 81,920 limit |

So nearly half of all book states were being censored, on 91% of emissions. The
87 gaps in 1300z were this, not a quiet market.

## Required capacity, and why 200

Generation runs at ~10 Hz. The binding case is the **p90 emission cadence of
20.0 s**, which needs roughly 200 states per market per emission — about twice
the observed p99 of 101 at the 10 s cadence. `PMUS_WS_BOOK_LOG` was therefore set
to **200**, which is 2× the measured p99 and covers a full 20 s emission at the
observed rate.

The ceiling is not arbitrary. An over-long log line is truncated by the platform
at 81,920 bytes and every fragment becomes unparseable, losing the whole emission
— this already happened once, and is why `BOOK_LOG_MARKETS_PER_DOC` exists. At
152.6 bytes per row (p50) and the 1–2 markets per document BTC15m actually
produces, cap 200 lands near 41% of the limit. The code also hard-clamps the cap
at 400, so 200 leaves room to raise it again without a code change if the cadence
slips further.

## Minimum safe change — what was and was not touched

**Changed:** one service variable, `PMUS_WS_BOOK_LOG` 50 → 200 on
`pmus-ws-evidence-recorder`. That is the whole change.

**Not changed:** no recorder code, so the image holding the PMUS credentials is
not rebuilt; no other variable; no trade-collection setting; no strategy, fill
rule, pre-registration or historical file; `btc-ref-collector` untouched.

Setting a variable does restart the service, which is unavoidable for any change
and costs one window boundary.

**Auditability needed no code change.** Each document already carries
`identity.<slug>.book_updates`, the recorder's own cumulative counter, so
generated-minus-emitted is computable from the evidence itself — which is exactly
how the 45.8% above was measured. `scripts/pmus_book_completeness_audit.py` reports
it, plus cap hits, cadence, document size against the limit, and the resulting
book-gap distribution per market.

## Verification required on a FRESH window before the study resumes

1. zero cap-induced book loss (`rows_lost_to_cap == 0`, no emission at cap);
2. sequence continuity, on both the PMUS and BTC reference streams;
3. full ≥600 s coverage of the window;
4. book-gap distribution — gaps above 2 s should collapse toward zero;
5. trade stream unchanged (document and row counts in line with before);
6. BTC-reference collection unchanged.

Until all six pass, no synchronized window is scored. 1300z is **not** rescored
with invented or missing book states.
