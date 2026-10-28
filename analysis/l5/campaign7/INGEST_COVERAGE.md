# L5 ingest coverage, latency, and where the orders are actually lost

Independent route to Polymarket is unavailable from the analysis container
(`CONNECT tunnel failed, 403`), so the ground truth used here is the
executor's own **pre-filter** evidence: `ingest_slugs` counts every row of
L5's feed before any filter, which is exactly what that instrumentation was
built for.

## The feed is healthy

Final counters, since boot at 19:57, covering the whole campaign:

    polls_ok 1838      rows 367600      dup 367204
    new 396            admitted 366     refused:NOT_BTC15M_SLUG 30
    rows_btc15m 337306 distinct_slugs_seen 46   FEED_FAILED 2

- 2 feed errors in 1,838 polls (0.1%).
- **Zero `STALE_ACTION` refusals** -- every new row was inside the 900 s bound.
- 396 new, 366 admitted, the 30-row difference entirely non-BTC15m markets.
- The page carries ~183 BTC15m rows per poll and polls run every ~3.5 s, so a
  new L5 action appears many polls before it could age off the page.

**Ingest coverage of what the API serves is effectively complete. The
pipeline is not defective.**

## Corrections to the previous report

| claim | corrected |
|---|---|
| 122 admitted actions | **366** -- 122 was a mid-campaign snapshot at 20:33 |
| ~15 L5 actions/window | **52/window** (366 over ~7 windows) |
| "we observe ~9% of L5's actions" | withdrawn; it rested on the 122 figure |
| "1945z: no L5 action" | **wrong -- L5 DID trade 1945z** (42,601 feed rows) |

1945z is explained by timing, not absence: the process booted at ~19:57,
inside that window, and those rows were ingested after it had closed.

## Did L5 trade 1945z and 2045z?

Decoding the slug epochs:

| window | epoch | in top-6 slugs |
|---|---|---|
| 1945z | 1793216700 | **PRESENT, 42,601 rows** |
| 2000z | 1793217600 | PRESENT, 83,566 |
| 2015z | 1793218500 | PRESENT, 53,357 |
| 2030z | 1793219400 | PRESENT, 69,428 |
| **2045z** | 1793220300 | **absent** |
| 2100z | 1793221200 | PRESENT, 37,951 |
| 2115z | 1793222100 | PRESENT, 19,594 |

Row counts are cumulative across polls, so an older window has had longer to
accumulate. 2045z is older than both 2100z and 2115z, which do appear, so had
L5 worked it the count would exceed 2115z's 19,594. **2045z is genuine L5
inactivity**; our silence there was correct.

## Where the orders are actually lost

    366 observed  ->  190 intent-bearing decisions  ->  36 submitted
                  ->  18 filled  ->  7 pairs  ->  14.03 matched contracts

**9.8% of observed L5 actions became an order.** The loss is downstream of
ingest, not in it. By distinct episode the only large class that was neither
loss-making nor time-expired is `MAX_LIVE_ORDERS`, at 24 episodes.

## Two constraints that will bind next time

1. **The submission backstop nearly bound.** `submissions_spent 36` of
   `max_submissions 40` -- four left -- while the class budgets were barely
   touched (OPEN 15/40, COMPLETE 14/40, REPLACE 7/64). The global backstop,
   not the strategy, would have stopped the next window. `ASK_TOTAL` must be
   raised for any longer campaign.
2. **Latency against L5's cadence.** We act on an action a median **19.6 s**
   old while L5's median inter-fill spacing is **2.0 s**. We are structurally
   ~10 of his actions behind, so copying his fills cannot reproduce his
   inventory path however complete the feed is.
