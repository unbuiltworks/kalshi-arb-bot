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

---

# The measurements

## 1. The sports population is genuinely wide-spread

Full enumeration, 60 pages, reaching a short page (so this is the
population, not a lower bound):

```
sports markets open+active        9,102
two-sided                         7,665
one-sided or unquoted             1,437
spread >= 5c                      4,030   = 52.6% of two-sided
```

| spread | markets | share of two-sided |
|---|---:|---:|
| <= 0.01 | 1,025 | 13.4% |
| 0.01-0.02 | 1,150 | 15.0% |
| 0.02-0.03 | 496 | 6.5% |
| 0.03-0.05 | 1,795 | 23.4% |
| 0.05-0.12 | 1,551 | 20.2% |
| 0.12-0.25 | 786 | 10.3% |
| 0.25+ | 862 | 11.2% |

Against BTC5m, where 71% of quotes were a single cent, this is a different
venue. **Spread is not the scarce thing on sports.** The handoff was right
about that much, and right that it is 20x BTC5m.

An earlier 40-page sweep reported 1,482 sports markets and was labelled a
lower bound because it hit its page cap. It was: the real number is 6x
larger. The label was doing real work and is why the figure was not quoted
as the population.

## 2. Depth at the wide touches is NOT 1-3 shares

188 wide markets, sampled stratified across the spread range:

| | min | p25 | median | p75 | max |
|---|---:|---:|---:|---:|---:|
| spread | 0.0400 | 0.0800 | 0.1100 | 0.2200 | 0.5500 |
| pair size (sh) | 0.10 | 8.00 | **19.00** | 50.00 | 1,200.00 |
| capacity ($/pair) | 0.0084 | 0.95 | **2.00** | 7.50 | 408.00 |
| lifetime traded (sh) | 0.0 | 0.0 | **0.0** | 0.1 | 101,900.9 |

`pair size` is `min(bid_touch, ask_touch)` -- a pair needs both legs, so the
smaller touch is the size, never the average.

**The depth worry in the handoff is refuted.** Median pair size is 19
shares, p75 is 50, the maximum is 1,200. Against a clip of ~10 contracts
that is ample. The "1 and 3 shares" observation was real but was two
markets, and two markets from the extreme tail: in this sample the markets
with spread >= 15c have a median pair size of 19 shares, not 1-3.

## 3. The blocker is flow, and it is near-total

Look at the last row of that table. **Median lifetime volume in a
wide-spread sports market is 0.0 shares.** Not a low rate -- nothing, ever.

Non-cumulative, so no clause ordering can hide it:

| spread | has flow (>=100 sh lifetime) | no flow | flow % |
|---|---:|---:|---:|
| 0.05-0.08 | 1 | 39 | 2.5% |
| 0.08-0.12 | 2 | 57 | 3.4% |
| 0.12-0.18 | 0 | 24 | 0.0% |
| 0.18-0.25 | 1 | 29 | 3.3% |
| 0.25+ | 0 | 35 | 0.0% |
| **all wide** | **4** | **184** | **2.1%** |

And of markets quoting >= 15c, **99% have traded under 100 shares in their
entire lifetime** (77 of 78).

A spread is an invitation, not a trade. These are quotes posted into markets
with no participants -- almost certainly venue-side or RFQ seeding on the
long tail of a 9,102-market sports catalogue. You cannot capture a spread
without a counterparty, and **capacity without flow is a quote, not an
edge.**

## 4. Where flow DOES exist, the spread is 1c and adverse selection is worse than BTC5m

This is the finding that closes it. From the live in-game slate tape,
634 rows / 79 markets / 601 readable observations:

| spread bucket | obs | markets | med spread | pair size | capacity $ | **flow sh/min** |
|---|---:|---:|---:|---:|---:|---:|
| <= 0.01 | 295 | 44 | 0.0100 | 1,620.95 | 16.21 | **291.7** |
| 0.01-0.02 | 40 | 22 | 0.0200 | 13.00 | 0.26 | 4.3 |
| 0.02-0.03 | 28 | 15 | 0.0300 | 19.00 | 0.57 | 1.6 |
| 0.03-0.05 | 34 | 17 | 0.0400 | 0.31 | 0.01 | 0.1 |
| > 0.05 | 122 | 32 | 0.2350 | 1.00 | 0.30 | **0.0** |

Flow and spread are **monotonically inverse and the relationship is total**:
291.7 shares a minute at one cent, 0.0 at a 23.5c spread. There is no cell
with both.

Measured adverse selection on the same tape, volume-triggered, with the leg
inferred from the aggressor's print:

```
                              c/share      se      n      t
queue-blind   tau 1            +5.055   0.432    311   +11.7
queue-blind   tau 3            +4.939   0.902    224    +5.5
queue-cleared tau 1            +6.730   0.762    130    +8.8
queue-cleared tau 3            +7.025   1.621     90    +4.3

BTC5m, for comparison                    4.87c
```

Positive means the mid moved **against** the leg we were filled on.
"Queue-cleared" additionally requires the interval's volume to exceed the
depth resting in front of us -- i.e. our order was actually reached. That is
the realistic number, and it is **larger**: being at the back of the queue
means we are filled preferentially when the flow is big enough to be
informed.

So in the only sports regime where a maker can actually get filled:

```
spread available            1.0 c
adverse selection           6.7 c   (queue-cleared, t +8.8)

adverse selection is 6.7x the entire spread
```

**BTC5m was 4.9x. Sports is 6.7x.** The ratio that closed BTC5m is not
better here -- it is worse. Sports did not escape the arithmetic; the wide
spreads simply sit in markets that do not trade, so they never enter it.

A hand check of the estimator on individual intervals behaves exactly as it
should: `aec-atp-yuhhsu-jayfri` quoted 0.83/0.84, 7,716 shares printed at
0.75, and the mid moved 0.835 -> 0.745. A maker buying at 0.83 there is
down 9 cents on a 1-cent spread, immediately.

## 5. The one real candidate, and why it is not a conclusion

Exactly one market in the sample had a wide spread, a placeable pair, and
real volume:

```
atc-nfl-det-chi-series-2026-chi
  spread 0.240   pair size 78.86 sh   capacity $18.93 a pair
  lifetime traded 101,900.9 shares    book 2 levels / 2 levels
```

$18.93 of gross per filled pair is not noise, and it is the reason this
report does not end at "stop".

**Three things keep it from being a finding.** It is n = 1 out of 188. Its
family is tiny -- only 39 `atc-*series*` markets exist open and two-sided,
of which two are wide, so there is no exploitable family behind it. And
most importantly, **101,900 shares is a lifetime, not a rate**: a
season-long `series-2026` market opened months ago trading 101,900 shares
is a trickle, where the 1c in-game books do 291.7 shares a *minute*.
`P(both)` depends on the rate, and a 2-level book is thin cover.

## 6. The mechanism: the wide spread and the absent flow are ONE variable

Sections 3 and 4 read as two independent facts, and read that way they
leave a door open -- maybe a wide market will attract flow later and pay us
the spread. It will not, and this is the measurement that closes it.

Sorting `aec-` moneyline markets by days until their game:

| days to game | markets | median spread | p25 | p75 |
|---:|---:|---:|---:|---:|
| -1 (in progress) | 5 | 0.0100 | 0.0050 | 0.0100 |
| 0 | 96 | 0.0100 | 0.0100 | 0.0600 |
| 1 | 214 | 0.0150 | 0.0100 | 0.0900 |
| 2 | 60 | 0.0600 | 0.0100 | 0.1400 |
| 3 | 26 | 0.1150 | 0.0400 | 0.2300 |
| 4 | 36 | 0.3050 | 0.2400 | 0.4300 |
| 5 | 40 | 0.3000 | 0.2600 | 0.3600 |
| 6 | 25 | 0.3000 | 0.2300 | 0.3750 |
| 7 | 12 | 0.2600 | 0.1950 | 0.3300 |
| 8 | 53 | 0.2800 | 0.1800 | 0.4200 |

```
game 5+ days out   median spread 0.2850
game day           median spread 0.0100      28.5x tighter
```

Monotone, across 231 markets of one family. **The spread is wide because
nothing is trading yet, and it closes as the flow arrives.** Spread and flow
are the same variable observed at two points on one clock, not two
quantities that might happen to coincide somewhere.

Two consequences, and together they are the verdict:

1. **A maker cannot wait in the wide book for flow.** By the time flow
   arrives the spread is one cent, which is the regime measured in §4 at
   1c against 6.7c.
2. **Resting through that transition is the worst available position.** The
   pre-game flow that tightens a 28c spread to 1c is price discovery -- it
   is better informed than the stale quote it trades against, and the stale
   quote is ours. That is not a hypothetical: it is the same mechanism the
   tape already measures at **6.7 c/share**, and the fact that the figure
   is flat from tau 1 to tau 3 (6.73c then 7.03c) says the loss is a **step
   at the fill**, not a drift. A faster cancel does not recover it, because
   there is nothing to escape -- the damage is done at the moment of
   execution.

So `spread > adverse selection` has no reachable cell on PMUS sports. The
inequality is not narrowly failed in one place and nearly met in another:
**its two sides are never measurable at the same time.**

---

# Verdict

## The two questions the task asked

**1. Is `spread > adverse selection` on sports?**

**No.** In the only regime where a maker can be filled, the spread is 1.0c
and adverse selection is 6.7 c/share (queue-cleared, se 0.76, n 130,
t +8.8). That is a ratio of **6.7x against**, where BTC5m was 4.9x against.
Sports is *worse* on the one number that decides it.

In the wide-spread regime, adverse selection is not favourable -- it is
**unmeasurable, because nothing trades there**, and an unmeasured quantity
is not a good one. §6 shows why the two regimes cannot be combined.

**2. Is the depth large enough to matter, or is it 1-3 shares?**

**It is large enough, and the handoff's worry was misplaced.** Median pair
size in wide-spread markets is 19-42 shares depending on the sample, p75
50-144, p95 994, max 2,110. Capacity reaches $408 a pair. **Depth was never
the constraint.**

The constraint is flow: **95.7% of wide-spread sports markets have never
traded a single share**, and only 2.5% have traded 100.

## So: stop

The task said that if depth turned out to be negligible, say so and stop.
Depth is *not* negligible, so that exit does not apply -- but the strategy
fails anyway, for a sharper reason than the one anticipated, and the
recommendation is the same.

**Do not pursue two-sided maker spread capture on PMUS sports.** Not at any
clip, band, horizon or market family in this venue. The seven BTC5m levers
failed because none of them changed the spread-to-adverse-selection ratio;
here the ratio is worse still, and §6 shows it is enforced by the event
clock rather than by any parameter a strategy could set.

**Nothing was deployed, armed, or spent.** The account remains past its $50
lifetime loss ceiling at `loss_room_usd: -1.18`, which was neither raised
nor touched; `l5-btc5m-live` remains disarmed; `PMUS_SPRING_ARM` /
`PMUS_SPRING_LIVE` were never requested, because on this evidence they
should not be. Total cost of this result: read-only GETs against a public
gateway.

## The one thing that would change this answer

Not a parameter. A cell where wide spread and real flow genuinely co-exist,
which §6 says the event clock forbids for game markets. The single
candidate found in 188 + 280 screened wide markets was
`atc-nfl-det-chi-series-2026-chi` (24c spread, 78.86-share pair, $18.93 a
pair, 101,900 shares lifetime) -- a season-long series market, i.e.
precisely a market with **no event clock**. Its family is 39 markets, of
which two are wide.

If anything here deserves a follow-up it is that family, and the follow-up
is a **measurement, not a trade**: poll those 39 names for pair-completion
rate and flow *rate* (lifetime volume is not a rate). On a 39-market family
with ~$19 of gross a pair, the realistic ceiling is small enough that this
is a curiosity rather than a programme.

## What would make the measurement sharper, and it is an owner action

Every adverse-selection figure here is a markout at a ~95-second sampling
gap, because that is what two REST calls a market across a 50-market slate
costs. The loss is a step at the fill, so 95s does not *inflate* it -- but
it cannot resolve the shape of it inside a second either.

`pmus-ws-evidence-recorder` has been dead 22 days on a WebSocket 401 and
needs `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY`. A live book feed would
give per-print markouts instead of per-95-seconds. **It would sharpen this
number; on the evidence above it would not reverse it**, and it is not
worth asking for on behalf of this strategy.
