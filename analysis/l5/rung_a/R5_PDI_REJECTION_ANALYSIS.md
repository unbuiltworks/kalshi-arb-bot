# R5 1115z — post-only rejection analysis (13 orders)

Evidence: authoritative `GET /v1/order/{id}` for all 13 orders, plus 54
`native_tick` rows carrying the contemporaneous authenticated YES book,
recovered read-only from `nativeq10_r5_journal.jsonl`.

## Representation (unchanged, and confirmed correct)

* YES (UP) BUY at own price X is wire BUY at X. Marketable iff `X >= YES ask`.
* NO (DOWN) BUY at own price X is wire `ORDER_SIDE_SELL` at `1-X` with
  `ORDER_INTENT_BUY_SHORT`. Marketable iff `1-X <= YES bid`.

Each DOWN order's own price was verified independently against its mandate
`cash_at_risk` (wire 0.51 -> own 0.49 -> $4.90 for q10, and so on). The
inversion is applied once and correctly in all 13 cases.

## The 13 orders

| # | order | out | own | wire | submitted | decision book | arrival book | mkt@decision | mkt@arrival | predicted | actual |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | CSPEJNMW2YC6 | UP | 0.50 | 0.50 | 11:15:49.153 | 0.50/0.51 | 0.50/0.51 | no | no | ACCEPT | FILLED |
| 2 | CSPEJ9AG8YAQ | DOWN | 0.49 | 0.51 | 11:15:49.321 | 0.50/0.51 | 0.50/0.51 | no | no | ACCEPT | FILLED |
| 3 | CSPE7YNY6YB5 | UP | 0.45 | 0.45 | 11:15:58.481 | 0.45/0.46 | 0.45/0.46 | no | no | ACCEPT | FILLED |
| 4 | CSPEJNQ68YC6 | DOWN | 0.54 | 0.46 | 11:15:58.623 | 0.45/0.46 | 0.45/0.46 | no | no | ACCEPT | FILLED |
| 5 | CSPENT8GGYCD | UP | 0.43 | 0.43 | 11:16:02.055 | 0.43/0.44 | 0.39/0.40 | no | **yes** | REJECT | **REJECTED** |
| 6 | CSPENGF2MYC3 | UP | 0.39 | 0.39 | 11:16:06.235 | 0.39/0.40 | 0.39/0.40 | no | no | ACCEPT | FILLED |
| 7 | CSPF4389YYAQ | DOWN | 0.54 | 0.46 | 11:16:47.643 | 0.45/0.46 | 0.46/0.47 | no | **yes** | REJECT | **REJECTED** |
| 8 | CSPERKXEYYB5 | UP | 0.46 | 0.46 | 11:16:49.730 | 0.46/0.47 | 0.46/0.47 | no | no | ACCEPT | EXPIRED |
| 9 | CSPERKXFTYB5 | DOWN | 0.53 | 0.47 | 11:16:49.824 | 0.46/0.47 | 0.48/0.49 | no | **yes** | REJECT | **REJECTED** |
| 10 | CSPF4H8JRYBY | DOWN | 0.50 | 0.50 | 11:16:54.659 | 0.49/0.50 | 0.49/0.50 | no | no | ACCEPT | FILLED |
| 11 | CSPF4Q6E6YBQ | DOWN | 0.49 | 0.51 | 11:17:00.155 | 0.49/0.50 | 0.50/0.51 | no | no | ACCEPT | FILLED |
| 12 | CSPF4Q834YBQ | DOWN | 0.47 | 0.53 | 11:17:05.711 | 0.50/0.51 | 0.51/0.52 | no | no | ACCEPT | FILLED |
| 13 | CSPF4HD6TYBY | DOWN | 0.48 | 0.52 | 11:17:12.757 | 0.51/0.52 | 0.54/0.55 | no | **yes** | REJECT | **REJECTED** |

## Contingency

Predicted from the **arrival** book (nearest observed book at/after submission):

| | FILLED | EXPIRED | REJECTED |
|---|---|---|---|
| predicted ACCEPT | 8 | 1 | 0 |
| predicted REJECT | 0 | 0 | 4 |

Predicted from the **decision** book (the book LOCK5 quoted against):

| | FILLED | EXPIRED | REJECTED |
|---|---|---|---|
| predicted ACCEPT | 8 | 1 | **4** |
| predicted REJECT | 0 | 0 | 0 |

**Arrival-book PDI-cross predicts all 13 outcomes exactly. Decision-book
PDI-cross predicts none of the four rejections.**

## Cause, per order

PDI-cross is CONFIRMED for all four rejections, and the mechanism is
decision-to-arrival staleness, not translation:

* **#5** UP BUY 0.43. Ask 0.44 at decision (11:15:59.98), 0.40 by
  11:16:04.9 — the market fell 4c in the 2.08 s before arrival, so a bid at
  0.43 was above the ask and crossed.
* **#7** DOWN own 0.54 = SELL 0.46. Bid 0.45 at decision (11:16:46.25),
  0.46 by 11:16:48.4 — 1.39 s, bid rose onto the quote.
* **#9** DOWN own 0.53 = SELL 0.47. Bid 0.46 at decision (11:16:48.39),
  0.48 by 11:16:49.88 — 1.43 s.
* **#13** DOWN own 0.48 = SELL 0.52. Bid 0.51 at decision (11:17:05.78),
  0.54 by 11:17:12.82 — **6.98 s**, the longest gap and the largest move.

Every accepted order had a decision-to-arrival gap during which the book did
NOT reach its price, including three DOWN orders with gaps of 3.5 s, 4.3 s
and 5.6 s. Latency alone is not the discriminator; latency **times** the
book's move is.

The window trended hard: YES went 0.50 -> 0.39 -> 0.55 in about 90 seconds.

## Not a translation problem

The hypothesis that the NO->YES-SELL mapping is systematically wrong is
ELIMINATED. Were it wrong, accepted and rejected DOWN orders would separate
by side or by price direction. They do not: five DOWN orders rested and
filled as maker under the same mapping that produced three rejections, and
the mapping reproduces each order's mandate cash_at_risk exactly.

## MAKER vs TAKER classification

These four intents were economically valid completions that LOCK5 issued as
passive maker quotes, and post-only then refused the only price at which
they were available:

* #7, #9, #13 are DOWN completions against held UP inventory. At own 0.54 /
  0.53 / 0.48 against a UP average of 0.4467, pair cost would have been
  0.9867 / 0.9767 / 0.9267. #13 at 0.9267 is comfortably inside the <= 0.96
  spring target; #7 and #9 are not.
* #5 is a UP first leg, not a completion, and a maker quote is the right
  shape for it. Its rejection cost nothing but the queue slot.

So the actionable finding is narrower than "enable taker": a completion that
is inside the pair-cost target and has become marketable at arrival is a
valid TAKER action, and the current path has no way to express it — it can
only re-quote passively and be refused again. **PDI is not disabled
globally, and nothing here changes LOCK5's parameters.** This records what a
future change would have to distinguish; one window of n=4 does not
authorise making it.

## Secondary finding: the UP side was locked out

`CSPERKXEYYB5` (#8) rested un-filled and its cancel returned `HTTPError`
on every attempt from 11:16:54 to the 11:30 close, leaving
`replacement_blocked {side: up, reason: CANCEL_FAILED}` for ~13 minutes.
The R4 fix resolves a cancel failure against an ALREADY_TERMINAL order;
this order was genuinely live, so the block was correct behaviour on an
unreadable cancel — but it is why the window took 3 UP fills against 5 DOWN.

**The 20 DOWN residual is an execution artifact, not a strategy choice.**

Three `ORDER_STATE_UNKNOWN` halts (404 on `GET /v1/order`) also stalled the
loop for several seconds each, contributing to the arrival gaps above.
