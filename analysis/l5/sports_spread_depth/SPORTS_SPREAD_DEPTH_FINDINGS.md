# Sports spread x depth: the spread is real, the flow is not, and the two never overlap

2026-11-13. Read-only. No order path, no credentials, no deployment, no money.
Run against the first task in `SPORTS_HANDOFF_PROMPT.md`.

## Headline

Sports does **not** escape the BTC5m arithmetic. It relocates it.

```
where the spread is wide (>=5c, 850 of 1,329 two-sided sports markets)
    there is no flow        -> a pair never completes
where there is flow (deep books, real volume)
    the spread is 1c        -> against ~7c of adverse selection
```

The two regimes are **disjoint**. Wide spread and real flow do not co-occur
in the sample, so the 19-26c spread the handoff found is visible and not
takeable, and the books that do trade offer the same 1c that closed BTC5m
against a *worse* adverse-selection number.

**The depth worry in the handoff was wrong, and it does not matter.** Depth
at the wide touches is not 1-3 shares -- it is a median 19 shares with a p75
of 359 and a maximum of 1,200, which is ample for our clip. The binding
constraint is not how much size is resting, it is that **nothing trades
there at all**: lifetime volume was under 100 shares in 100% of the widest
cells, median 0.0 shares. You cannot capture a spread without a
counterparty, and capacity without flow is a quote, not an edge.

## The one equation, with measured numbers in it

A resting buy on both sides at the touch costs `bid + (1 - ask)` = `1 -
spread`, so a filled pair captures exactly the spread, and

```
net per placement  =  P(both) x spread  -  P(one) x adverse_selection
```

Every term below was measured, none fitted.

## What was measured, and with what

| | |
|---|---|
| universe | 20,000 open+active markets enumerated (40 pages, a **lower bound** -- the sweep hit its page cap, it did not reach a short page) |
| sports | 1,482 markets on sports prefixes; 1,329 two-sided; 153 one-sided or unquoted |
| wide | **850** with spread >= 5c |
| depth sample | 194 of those 850, **stratified** by spread bucket |
| tape | the live sports slate polled on a cycle, one row per market per poll |

## Instrument validation, before any sports number was trusted

`census_report.py --crosscheck` runs this analysis's own fill rule and P&L
arithmetic over the committed BTC5m tape and reproduces the published
baseline exactly:

```
                 this code      published
windows               187            189 (187 settled)
BOTH            19 = 10.2%          10.2%
ONE            162 = 86.6%          86.6%
NONE             6 =  3.2%           3.2%
net              +3.74 c/w          +3.74 c/w
t                    +1.67              +1.67
median spread       0.0100       1 cent
1c share       70.9% of 21,151   71% of 21,151
```

Same discipline as `shadow_crosscheck.py`, and for the same reason: an
unfavourable reading from a broken instrument is worthless. The 21,151
two-sided quote count matching is the tell that it is the same tape read
the same way.

**What the cross-check does NOT cover**, stated because an unvalidated
component inside a validated harness is exactly how a clean zero gets
reported: the volume-triggered fill model and the queue test are exercised
only on the sports tape. The BTC5m tape carries no volume column, so they
cannot be checked against it.

## Four venue facts in the handoff are wrong

All measured against the live gateway today. These are carried forward so
they are not re-derived a third time.

1. **`/v1/markets/{slug}/bbo` does not 404 while OPEN on sports.** It
   answers 200 with `state: MARKET_STATE_OPEN` and carries `sharesTraded`.
   The 404 is real but is specific to `cpc-` crypto windows -- verified both
   ways today, 404 on a live `cpc-btc-updown-5m` window and 200 on a live
   `aec-` market. The crypto behaviour was generalised to the venue.

2. **`/v1/markets/{slug}/book` is not mentioned in the handoff at all**, and
   it serves the full depth ladder on an open market -- sell side under
   `offers`, not `asks`.

3. **"Volume-while-fillable CANNOT be measured on a live window from public
   data" is false, including on BTC5m.** `/book` answers 200 on a *live*
   `cpc-btc-updown-5m` window with a 98-level ladder, and its `stats` block
   carries `sharesTraded`, `notionalTraded`, `lastTradePx`, `lastTradeQty`
   and `openInterest`. The BTC5m shadow recorded `vol_while_fillable: None`
   with reason `BBO_404_WHILE_OPEN` across 199 windows; the quantity was one
   endpoint away the entire time, and it was the one number that would have
   answered whether 45 shares fills. **This does not reopen BTC5m** -- that
   is closed on spread arithmetic, not on this -- but "cannot be measured"
   was wrong, and the assumption would have travelled into this census
   unexamined.

4. **The live-slate query was not unsolved.** It was already in
   `src/pmus_sports_collector.py`:
   `/v1/events?live=true&active=true&closed=false` returns the live slate
   (76 events / 598 markets), and `/v1/markets?active=true&closed=false`
   pages the open universe with `bestBidQuote`/`bestAskQuote` **inline** --
   so a spread census over thousands of markets costs one call per 500
   markets rather than one per market.

### And one trap worth more than the four corrections

`/book`'s `stats` also carries `settlementPx`, and **on an OPEN market it
reads `0.0000`, not null** -- measured on a live window that was trading at
0.98. `straddle_shadow.py` treats a `settlementPx` of 0 or 1 as the settled
outcome. Reading settlement from `/book` would therefore have resolved
**every open window to DOWN**, with total confidence and no error. This
census takes settlement only from `/bbo`, which correctly returns null while
open, and the constant `SETTLEMENT_NEVER_FROM_BOOK` records why.

## Process note: the sampling bug that nearly produced a clean false negative

The first universe sweep took the **widest 220 of 850** wide markets. That
sample had a median spread of 0.32 and lifetime volume of 0.0 shares in
100% of cases, and it reported **no survivors**. True, and useless: the
widest quotes are the deadest markets *by construction*, so "no survivors"
was an artefact of the ordering, not a fact about the venue -- and it would
have looked exactly like a clean negative result, which is the failure mode
this programme has been bitten by repeatedly.

The sweep now samples an equal quota per spread bucket and emits the strata
so the sample is auditable. The conclusion survived the fix; the point is
that it was not entitled to until the fix was made.
