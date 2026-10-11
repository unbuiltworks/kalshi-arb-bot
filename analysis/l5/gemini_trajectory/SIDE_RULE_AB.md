# Did choosing the wrong initial side cause the Gemini capture failure?

**Exploratory. n = 2 windows.** Both are NEAR_COMPLETE, not FULL — the ≥290 s
criterion was NOT lowered. No Gemini order; the A/B is a replay parameter in
an analysis module and `CANONICAL` remains the default everywhere.

## Window census this session (BTC/ETH 5m)

| class | criterion | count |
|---|---|---|
| **FULL** | span ≥ 290 s | **0** |
| **NEAR_COMPLETE** | span ≥ 280 s | **2** |
| **PARTIAL** | span < 280 s | 17 |

NEAR_COMPLETE: `GEMI-BTC05M…1615-UP` (288.85 s, 96.03% two-sided, 14 prints)
and `GEMI-BTC05M…1455-UP` (283.40 s, 83.45%, 9 prints). The bar is being
missed in the **final seconds**, where the book goes one-sided, not on
coverage — 16:15 spans tte 292.91 → 4.06.

## The A/B, identical observed states, unchanged canonical policy

| window | rule | STRICT | SUPPORTED | OPTIMISTIC | matched | intentions | requotes |
|---|---|---|---|---|---|---|---|
| BTC 16:15 | CANONICAL (cheaper) | 0 | 0 | 0 | 0 | 4 | 1 |
| BTC 16:15 | **DEARER** | **1** | **1** | **6** | **0** | 1–6 | 69–75 |
| BTC 14:55 | CANONICAL (cheaper) | 0 | 0 | 0 | 0 | 3 | 54 |
| BTC 14:55 | DEARER | 0 | 0 | 0 | 0 | 3 | 68 |

The single STRICT fill under DEARER is `open UP @ 0.47, tte 29.44,
print_through_our_price` — **the first STRICT-evidenced fill the Gemini shadow
has ever produced.** Under OPTIMISTIC the same rule takes 6 fills at 0.63/0.67
against the taker sells that dominate this tape (12 of 14 prints were taker
SELLS, which can only reach a resting UP bid).

## Verdict

**Partially supported, and explicitly not proven.**

* On 1 of 2 windows the side rule is the difference between zero evidence and
  a STRICT fill. That is consistent with the canonical-history finding and
  with the flow direction already measured.
* On the other window **neither rule fills**, so side selection is not a
  complete explanation.
* **Neither rule produces a matched campaign.** DEARER yields RESIDUAL_HOLD,
  not MATCHED — it buys inventory it never completes. Fixing the side would
  not by itself have produced pair economics.

Two windows cannot settle this. The honest reading is that the wrong initial
side is *a* contributor that is now measurable, not the established cause.
More NEAR_COMPLETE and FULL windows are required before any rule change is
even proposed.
