# Diagnostic: is Gemini's display surface lagging the executable book?

**Answered entirely from the existing recorder tape. Nothing was built, no
strategy or collector was changed, and no new endpoint was added.** Source:
`analysis/gemini/btc5m_snap/btc5m_rec_*.jsonl.gz`, 4,981s span, 31,570 rows.

## Update frequency of each source

| source | rows | rate | per-market cadence |
|---|---|---|---|
| WS order book (`p_snapshot` touch) | 13,196 | 159/min | **median 1.000s**, p90 1.003s |
| WS book diffs (`p_depth`) | 3,316 | 39.9/min | event-driven |
| published LRRR (`index_price`) | 4,983 | 60.0/min | **exactly 1 Hz** |
| contract `prices` block -- the DISPLAY surface (`window_poll`) | 9,200 | 111/min | **median 10.12s**, p90 10.37s |
| trades (`p_trade`) | 127 | 1.5/min | sparse |

## The display surface lags the book by ~10s, and sometimes far more

678 display readings matched against the WS touch series, each against the
**nearest prior** WS state (no hindsight alignment):

| | |
|---|---|
| display equals the **current** WS state | 246 / 678 (**36.3%**) |
| display equals an **earlier** WS state (<= 60s back) | 420 |
| display matches **no** WS state within 60s | 12 (1.8%) |
| **implied display lag** | **median 9.33s, p90 42.57s, max 58.92s** |

The median lag (9.33s) tracks the display's own refresh interval (10.12s),
the expected signature of a periodically-refreshed snapshot of the same
book. The p90 of 42.6s is the part that matters: a display reading can be
four refresh periods stale.

Independently, comparing the executable ladder to the display at 312 paired
checkpoint instants: exact agreement on **both** touches in only **19.9%** of
cases, median delta 0.0000 on each side but means +0.026 (bid) and +0.040
(ask), maximum absolute divergence **0.82**. The display is not
systematically wide or narrow -- it agrees at the median and diverges in a
fat tail, exactly as a stale snapshot would. Consistent with the earlier
BTC15m measurement in `ci15m_probe`: the contract price agreed with the
executable best ask 23.1% of the time across 372 instants.

### Scope, stated plainly

This measures the **API's contract-`prices` surface**, the only display-like
value any public endpoint supplies. **No public endpoint exposes the mobile
UI**, so the app is not measured and I am not claiming it is. Established:
the API's display surface refreshes every ~10s and is stale by a median 9.3s
/ p90 42.6s against the 1 Hz executable book. If the mobile UI is fed by that
surface it inherits the lag; if it has its own cadence, that is unmeasured.

## THE WIDE BOOKS ARE GENUINE, NOT A DISPLAY ARTIFACT

The question that mattered, and the two independent surfaces agree:

| | executable ladder | display surface |
|---|---|---|
| median spread | **0.290** | **0.280** |
| mean spread | 0.363 | 0.350 |
| min / max | 0.05 / 0.97 | 0.01 / 0.96 |

The `/v1/book` ladder (WS and REST) and the contract object's own
`bestBid`/`bestAsk` -- two separate endpoints, independently parsed -- report
**the same wide spread distribution**. A display artifact would be wide on
the display surface and tight on the book. It is not.

Recorder-measured book staleness at checkpoints is small: median **0.287s**,
p90 0.916s, max 1.000s, bounded by the 1 Hz snapshot cadence.

So the wide UP/DOWN prices seen through the API are **real executable
books**. That corroborates the live evidence rather than contradicting it:
the underdog cohort's five live fills all executed at the captured ladder
price with combined q1 cost 1.16-1.31, and cohort 1's fills landed at exactly
the displayed ask. A market whose two sides genuinely cost ~1.25 together is
what every available surface shows.

## What this does and does not license

It does **not** license a displayed-percentage comparison against any other
venue. It establishes only that Gemini's executable book is what it appears
to be, and that its display surface is a ~10s-stale view of it.
