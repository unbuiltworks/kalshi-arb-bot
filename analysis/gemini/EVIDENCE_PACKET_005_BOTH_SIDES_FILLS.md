# Evidence packet 005 — opposite-outcome fills on one Gemini contract

**Status of the two questions this packet answers:**

| claim | status |
|---|---|
| Gemini's prediction API permits opposite-outcome BUY orders to **both fill** on the same contract while the first exposure remains open | **SUPPORTED** |
| Which position representation results — independent YES+NO inventory (A) vs a single signed/net position (B) | **UNRESOLVED** |

Raw provenance: `analysis/gemini/fill_forensics_20260921T105113Z.json` — 231
verbatim rows from the venue's order-history surface, window 2026-08-26 →
2026-09-03, `filled` 58 + `cancelled` 173, both HTTP 200, neither page full.
Captured read-only by `scripts/gemini_fill_forensics.py` on 2026-09-21.

## SUPPORTED — both outcomes traded, first exposure still open

**Ten contracts** carry `filledQuantity > 0` on **both** outcomes with **no
intervening sell**. Across all 231 rows there are 230 buys and exactly one
sell (a `fill-or-kill` YES @0.10 on `GEMI-BTC05M2608302335-UP`, an unrelated
contract).

### The flagship: `GEMI-ETH05M2609010215-UP`

Six `maker-or-cancel` limit BUY orders, laddered within 500 ms at 02:08:16Z:

| outcome | price | qty | filledQuantity | orderId |
|---|---|---|---|---|
| yes | 0.48 | 1 | 1 | 145828835757642324 |
| yes | 0.47 | 1 | 1 | 145828835757642379 |
| yes | 0.46 | 1 | 1 | 145828835757642398 |
| no | 0.50 | 1 | 1 | 145828835757642426 |
| no | 0.49 | 1 | 1 | 145828835757642454 |
| no | 0.48 | 1 | 1 | 145828835757642474 |

* **YES q=3 filled at `2026-09-01T02:10:03.805Z`**
* **NO q=3 filled at `2026-09-01T02:10:58.081Z`** — 54.3 s later
* every row `side: buy`, `timeInForce: maker-or-cancel`; **no sell**
* total cost **$2.88**, which matches `/v1/tradevolume`'s independent
  `maker_notional_by_symbol` figure for this symbol exactly

### The second ETH case: `GEMI-ETH05M2609011145-UP`

NO q=1 @0.21 filled `11:41:16.642Z`; YES q=1 @0.76 filled `11:41:42.354Z` —
25.7 s later, both BUY, no sell. Pair cost $0.97.

### All ten

`GEMI-BTC05M2608272040-UP`, `…2608272235-UP`, `…2608272250-UP`,
`…2608272350-UP`, `…2608280005-UP`, `…2608300230-UP`, `…2609010305-UP`,
`…2609021210-UP`, `GEMI-ETH05M2609010215-UP`, `GEMI-ETH05M2609011145-UP`.

## UNRESOLVED — A vs B

No authoritative position snapshot was captured at the time, and the position
surface today returns `[]`, which says nothing about a moment three weeks
past. **The order history does not establish the position representation:** a
netting venue could record exactly these rows. Independent YES+NO inventory
must not be claimed from this evidence.

What the record *does* narrow: **model C (close/reopen, "cash out first") is
strongly disfavoured.** Opposite-outcome buys filled 25–54 s apart with no
sell between them, 230 buys against one sell overall. A venue requiring an
exit before an opposite entry could not produce that.

## A detector error, recorded rather than quietly fixed

The first pass reported **12** both-sides contracts. It counted
`status == "closed"` as a fill. On this venue `closed` means **closed at
expiry**, and such a row routinely carries `filledQuantity: 0` — an order that
never traded. Two false positives resulted, one of them ETH:

```
GEMI-ETH05M2608290140-UP   yes  0.45  filledQuantity 1  status filled
                           no   0.43  filledQuantity 0  status closed  <-- miscounted
GEMI-BTC05M2608301245-UP   (same shape)
```

The true count is **10**. `_is_filled` now tests `filledQuantity > 0` alone and
fails closed on an unparseable value; `tests/test_gemini_fill_forensics.py`
pins it on the exact rows that caused the error. The artifact's own `verdict`
block still reads 12 and is **left unedited** — raw provenance is not rewritten
after the fact; the corrected count is recomputed from the raw rows.

## Execution geometry: historical both-filled vs LIVE-2

| | historical both-filled (n=10) | LIVE-2 (2026-09-21) |
|---|---|---|
| quantity | **1** (9 of 10), 10 (1 of 10) | **0.01** |
| rungs submitted | 2 (×7), 3 (×2), 6 (×1) | 7 |
| submission spread | **< 500 ms in 8 of 10** | **117,602 ms** |
| initial YES / NO | ~0.30–0.51 / ~0.37–0.62 | 0.14 / 0.58 |
| initial pair cost | **$0.86 – $0.97** | $0.72 |
| TTE at first submission | 251–404 s (mostly ~290) | 252 s |
| first → opposite fill | 3.6 – 175.3 s (median ≈ 29) | **never** |
| maker / taker | maker-or-cancel, fee 0 | maker, `m:true`, fee 0 |
| cancellations | **0 in 8 of 10** | **5** |
| re-rung behaviour | place and leave resting | NO chased 0.58→0.68→0.77→0.87→0.95 |

LIVE-2's NO rungs rested **9 s, 19 s, 11 s** before being cancelled and
replaced. Seven of the ten historical contracts took **longer than 19 s** to
get their opposite fill.

## The question asked: ordinary market difference, or our executor?

**Both, and they are confounded — but one is material and testable.**

**Market difference, real:** the historical both-filled contracts were quoted
into a *balanced* book — YES and NO each roughly 0.30–0.60, pair $0.86–$0.97.
LIVE-2 met a *skewed* book: YES at 0.14 means the market priced NO near 0.86,
so the opening NO @0.58 was far from fillable. Nobody sells NO at 0.58 when it
is worth 0.86. Chasing to 0.95 then pushes the pair to $1.09 — above the $1
that makes the pair worth holding at all. This alone can explain a
non-completion without any executor defect.

**Executor difference, also real:** the historical both-filled cases placed
both legs within half a second and **left them resting** — zero cancellations
in eight of ten. LIVE-2 cancelled and re-rung its opposite leg every ~10–20 s.
Each cancellation surrenders price-time priority. Against a median historical
time-to-opposite-fill of ~29 s, LIVE-2's first three NO rungs were withdrawn
**before the window in which most historical opposite fills arrived**.

**Not established, and not to be assumed:** that leaving the NO resting would
have filled it. The price was also moving against us, and ten contracts is far
too few to derive a rule from. Size (0.01 vs 1) is **not** implicated — both
are maker orders whose fill depends on price-time priority, not size, and a
smaller order is if anything easier to fill completely.

**The honest summary:** LIVE-2's failure to complete is *consistent with*
ordinary market conditions, and *additionally* our executor withdrew the
opposite leg faster than the historical fills typically arrived. The two
cannot be separated from one run. What would separate them is holding an
opposite-leg rung for the historical duration and observing whether it fills —
an observation, not a rule change.

**No rule is derived here.** No strategy, envelope, bound or authorization is
modified by this packet.
