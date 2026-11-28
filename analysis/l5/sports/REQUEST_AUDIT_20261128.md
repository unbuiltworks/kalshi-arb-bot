# PMUS sports bot: venue request audit (28 Nov 2026)

Branch `dev/l5-request-dedup`, based on `97a22688d`. Development only: not
deployed, not a release candidate. Every reduction below is OFF by default.

## Why

Live campaign 28 Nov, release `9e6c2039f`, 16:33 to 18:04Z. The signed
budget is one request a second shared by everything this bot signs
(`src/pmus_request_budget.py`). Observed: 41+ HTTP 429, 16
`account_read_error`, 14 `fill_list_error`, 13 `ORDER_NOT_SENT
deferred_by_request_budget`, `gateway_fallbacks` about 11,000 an hour, 31
`reward_discovery`, a combo RFQ poll refused by the budget every 10 s.

## What this audit could and could not measure

- The request map below comes from reading the code. Rates are ESTIMATES
  from code cadence, for 4 active markets with about 8 resting orders,
  `LOOP_S=5`, `FILL_LIST_POLL=1`, WS on, combo maker in shadow at
  `PMUS_COMBO_POLL_S=10`. They are not measurements.
- Measured inputs: the counters quoted above, and a 1,002-row sample of the
  campaign's events (16:35 to 18:04Z). The sample has no heartbeat rows, so
  `fill_poll.list_reads` / `exact_reads` and `venue_requests.sent` were not
  available to this audit.
- Until this branch the bot did not count requests per route, and did not
  count public gateway requests at all. That is why several rows below are
  estimates. This branch adds those counters (reporting only), so the next
  campaign's heartbeat answers these questions directly.

## Signed requests (on the 1 request/s budget)

| # | Route | Called from | Class | Cadence in code | Cache / TTL | Est. per hour |
|---|---|---|---|---|---|---|
| S1 | `GET /v1/orders/open` | `Engine._fill_list` via `Venue.open_orders` | critical (raised by caller) | at most once per `FILL_LIST_REFRESH_S` = 4 s, only when a step has an open order to prove | 4 s | up to 900 |
| S2 | `GET /v1/order/:id` | `step_market` exact read; `cancel_fast` final state (up to 12 polls 0.1 s apart); `orphan_sweep` confirm; `refresh_fills` | critical | each open order at least every `FILL_EXACT_EVERY_S` = 60 s; every step while the order is newer than the list by less than `FILL_LIST_SETTLE_S`; 1+ per cancel | list proof (S1) | 480 floor for 8 orders; 600 to 900 with placements and cancels |
| S3 | `GET /v1/portfolio/positions` | `Venue.account` from `read_account` | normal | main loop every 20 s (10 s after a failure); also `service_pass`; stale-account path at most once per 10 s | `_acct_cache`, 20 s cadence | 180 |
| S4 | `GET /v1/orders/open` | `Venue.account` (second half of the same read) | normal | same as S3 | same | 180 |
| S5 | `POST /v1/order/preview` | `micro.place_limit_buy` | normal (safety for completions) | one per placement | none (deliberate evidence) | 1 x placements |
| S6 | `POST /v1/orders` | `micro.place_limit_buy` | normal (safety for completions) | one per placement | none | 1 x placements |
| S7 | `POST /v1/order/:id/cancel` | `cancel_fast` (RN1 path, parallel legs); `cancel_exact` (legacy path, shutdown) | critical | one per cancel | none | 1 x cancels |
| S8 | `GET /v1/rfqs?status=OPEN` | combo maker thread `_open_rfqs` | research | every `PMUS_COMBO_POLL_S` (code default 3 s; 10 s in the campaign) | none | 360 attempted at 10 s; sent only from spare budget |
| S9 | `GET /v1/rfqs/quotes?...ACCEPTED` | combo maker `_last_look` | research | each pass, only while a live quote is active | none | 0 in shadow |
| S10 | `GET /v1/orders/open` | `orphan_sweep` own read | normal | every 30 s, ONLY when the account cache is older than 30 s | reuses S4 when fresh | about 0; 120 worst case |
| S11 | `GET /v1/portfolio/positions` (all pages) + `GET /v1/orders/open` | `positions_snapshot`: hourly `inventory_reconcile`; `safety_check` while a position mismatch is unconfirmed | safety (audit) / normal | hourly; per account read during a mismatch | none | 3 to 6 |
| S12 | `GET /v1/account/balances` | `pilot_cash` in the audit | normal / safety | hourly | none | 1 to 2 |
| S13 | `GET /v1/portfolio/activities`, `GET /v1/incentives/earnings` | `account_audit`, `reward_check` | safety | hourly (`REWARD_CHECK_S` = 3600) | none | 2 to 10 |

Estimated total for the modelled case: about 2,300 to 2,900 signed requests
an hour, 0.65 to 0.8 of the budget, of which S1 + S2 + S7 (never delayed,
may run the bucket into deficit) are well over half. That matches the 0.8
requests a second measured on 25 Nov before `FILL_LIST_POLL`.

Placements: one order costs two normal requests (S5 + S6) and then one or
more exact reads (S2) until a list read at least 2 s younger than the order
exists. A cancel in the RN1 path costs one POST and at least one exact read.

## Public gateway requests (keyless, NOT on the signed budget)

| # | Route | Called from | Cadence in code | Cache / TTL | Est. per hour |
|---|---|---|---|---|---|
| G1 | `GET /v1/markets/:slug/bbo` | `Venue.bbo` whenever the WS has no current top for that slug: combo maker `books()` (every leg of every unseen RFQ), `_opportunity` (3 new per 20 s), `_try_select`, `_lf_could_select`, `reward_first_rank`, `sharp_shadow_tick`, and active markets only while the socket is quiet | per caller | WS top (3 s); none on the gateway side | about 11,000 OBSERVED (`ws.gateway_fallbacks`) |
| G2 | `GET /v1/markets/:slug/book` | `_rn1_reward_estimate` (30 s or on a leg change), `rn1_marginal_ev` / `_rn1_book`, `_opportunity`, `_reward_share_score`, `top_depth` | per caller | four separate caches: 30 s, 60 s, rank TTL 300 s, 10 s | 150 to 300 per rewarded active market |
| G3 | `GET /v1/market/slug/:slug` | `Venue.spec` | every step and every candidate priced | 60 s per slug (a closed market must be noticed) | 60 per active market plus candidates |
| G4 | `GET /v1/events?live...` (500 rows) | `Venue.live_sports`: `select` every 20 s; a second read in the same pass when the slots are full; `_kickoff_hold` every 30 s | 20 s | none | 180, up to 360 when full |
| G5 | `GET /v1/events?...startDateMin` | `Venue.pregame_sports` | per select | `PREGAME_CACHE_S` = 120 s | 30 |
| G6 | `GET /v1/incentives?symbols=` | `reward_programs` via `SpacedGet` | per select, stale slugs only | 300 s per slug, 60 slugs per request | about 21 OBSERVED (31 requests, 244 slug lookups in 90 min) |
| G7 | `GET /v1/events?slug=`, sharp candidates page | line refresh thread, `sharp_candidates` | 600 s | yes | under 20 |
| G8 | `GET /v1/markets/:slug/bbo` (settlement) | `settle_lookups` | `SETTLE_CHECK_S` (default off) | once per closed market | under 10 |

Other outbound traffic is not to the venue: the account-risk ledger
(`RiskReader`, 5 s, own thread), the campaign authorization file (20 s, own
thread), the international price guard, the sportsbook odds API.
`registry_read` (41 in the sample) is INBOUND: another service reading this
bot's order registry. It costs the venue nothing.

## Which suspected redundancies are real

| Suspected | Verdict | Evidence |
|---|---|---|
| List read followed by exact reads of the same orders in the same pass | NOT redundant by design, with one real duplicate beside it | An exact read is made only when the list cannot prove the order unchanged (too new, missing, state or quantity differs, 60 s since the last exact read, list unreadable). That is the reconciliation guarantee and is kept. The real duplicate is that `GET /v1/orders/open` is fetched by two independent readers: the fill poll (S1, every 4 s) and the account read (S4, every 20 s). |
| BBO gateway fallbacks while the WS has a fresh book | NOT real | `TwoSidedWs.fresh_bbo` returns the WS top whenever it is current; a fallback is counted only when it is not. Only ACTIVE markets are subscribed (`ws.set_slugs(self.active())`). In the sample all 82 `ws_stale` rows concern 7 slugs with a median top age of 1,097 s (max 2,004 s) and `connected=true` in 77: tops left over from an earlier connection for markets no longer subscribed, i.e. candidates being priced. The counter also includes every combo RFQ leg (never subscribed). |
| What the 11,000/h actually is | Mostly combo leg pricing, by inference | Engine-side callers are bounded by code to roughly 500 to 1,500 an hour (3 new rank prices per 20 s, a few `_try_select` per selection, 128 reselections). The combo maker prices every leg of every unseen eligible RFQ (up to 100 RFQs a read, 2 to 4 legs each) with no cache; a 21 Nov note in that file records about 19 requests a second from the same loop. This is an inference: the split was not measured. The new `gateway_fallbacks_by_cause` and `gateway_requests.by_route` counters, and the combo maker's `leg_book_reads`, measure it. |
| Per-pass account reads | NOT real | The account is read every 20 s, not every pass, and the orphan sweep already reuses it. The 16 `account_read_error` are venue 429s on that read (4 of 4 in the sample), i.e. a symptom of the burst of critical reads around it, not of reading too often. |
| Repeated reward discovery for unchanged candidates | Real but negligible | 31 log lines were 31 gateway requests in 90 minutes (one batched request each), because slugs go stale at different moments. Batching them on one 300 s boundary would save about 9 requests an hour. Not changed. |
| Combo RFQ poll while the maker is in shadow | Real | In shadow the maker can write nothing, yet polls at the live cadence: 360 signed attempts an hour at 10 s (1,200 at the code default of 3 s), each success followed by a gateway read per leg. |
| Spec / market lookups without caching | NOT real for spec; real for the book | `Venue.spec` is cached 60 s per slug, and that TTL is what notices a closed market. `/book` is read through four separate caches that do not share (G2). Not changed here: see below. |
| Live events page read twice in one pass | Real (found by this audit) | Main loop: `self.v.live_sports()` "to keep game clocks fresh" when the slots are full, then `select()` -> `slate_targets()` -> `live_sports()` again in the same pass when `RN1_LIVE_FIRST` lets selection continue; `_kickoff_hold` reads it a third time. |
| Legacy cancel path costs 4+ requests | Real, but not on the live path | `self.v.cancel(oid); self.v.status(oid)` (pre-read, POST, poll, read again) is used by the legacy two-sided step and by shutdown. With RN1 on, every routine cancel goes through `cancel_fast` (POST + final read). Not changed. |

## What changed on this branch

All four reductions are default OFF. With the flags unset the bot makes
exactly the requests it made at `97a22688d`.

| Flag | Default | Effect when on | Expected saving |
|---|---|---|---|
| `PMUS_TWO_SIDED_ACCT_SEEDS_FILL_LIST=1` | off | A SUCCESSFUL account read hands its open-order list to the fill poll, dated to just before that request. The fill poll's next own list read is then due one refresh later. | about 180 critical signed reads an hour (one S1 read in five), i.e. 5% of the whole budget and the class that causes the bursts |
| `PMUS_TWO_SIDED_LIVE_EVENTS_TTL_S=<s>` (suggest 5) | 0 | `Venue.live_sports()` reuses a read newer than this. A failed read is never cached. | up to 180 gateway reads an hour of a 500-row page when slots are full, plus up to 120 during a kick-off hold; one less blocking HTTP call in those passes |
| `PMUS_COMBO_SHADOW_POLL_S=<s>` (suggest 60) | 0 | A maker configured `shadow` makes one pass every this many seconds. `live` always keeps `PMUS_COMBO_POLL_S`. Never faster than `POLL_S`. | 300 signed research attempts an hour at 10 s -> 60 s (1,140 from the code default of 3 s), and the leg reads that follow each successful poll |
| `PMUS_COMBO_QUOTE_BOOK_TTL_S=<s>` (suggest 10) | 0 | In `shadow` only, a leg book read for pricing a new RFQ is reused for this long. `live` reads every leg for every quote as before; the last look never uses the cache in any mode. Unreadable legs are not cached. | the repeated share of G1. If combo legs are most of the 11,000/h and legs repeat across RFQs, several thousand gateway reads an hour; UNMEASURED until `leg_book_reads` is seen live |

Reporting only, always on (no request is added, removed or re-ordered):

- heartbeat `venue_requests.by_route`, `.deferred_by_route`,
  `.http_429_by_route`: signed requests per route family (ids, slugs and
  queries removed; bounded at 64 families);
- heartbeat `gateway_requests`: public gateway reads per route family, with
  errors and 429s (the gateway was not counted anywhere before);
- heartbeat `ws.gateway_fallbacks_by_cause`: `no_book`, `other_connection`,
  `not_two_sided`, `stale`;
- `COMBO_MAKER_STATUS.leg_book_reads`: `read` / `reused`;
- heartbeat `fill_poll.list_seeded`.

### Why the seeded list is safe

- Same venue route and the same rows the fill poll reads itself; the
  account read keeps its own class and its own two requests.
- The list is dated BEFORE its request was sent, exactly like the fill
  poll's own read, so `FILL_LIST_MAX_AGE_S`, `FILL_LIST_SETTLE_S`, the
  resting-state check, the filled-quantity check and the 60 s exact read
  all apply unchanged. A fill is still recorded only from the exact read.
- A list that is not a readable list is not stamped and seeds nothing. A
  failed account read seeds nothing: the fill poll reads its own list, and
  if that fails every open order is read exactly, as today.
- Only a list newer than the one held replaces it.
- `read_account` still returns None on failure; the freshness gate, the
  safety check and selection are untouched.

## Deliberately not changed

| Not changed | Why |
|---|---|
| Exact reads (S2): the 60 s refresh, the read of an order too new for the list, the read on any disagreement | This is the reconciliation guarantee. Rate-limiting exact reads per order would act on fill state up to that limit old in event-driven steps: a delay beyond today's bound. |
| `FILL_LIST_REFRESH_S` / `FILL_LIST_MAX_AGE_S` | Lengthening them delays fill detection. |
| `cancel_fast` poll (up to 12 exact reads 0.1 s apart) and parallel leg cancels | Probably the source of the bursts that draw venue 429s, but the timing of the final-state read is part of cancellation. Needs `by_route` data first; then a backoff after the second poll could be proposed with evidence. |
| Budget classes and waits | Cancels, order status and the fill list stay critical and always sent. |
| Account read cadence, `ACCOUNT_FRESH_S`, fail-closed on an unreadable account or positions | Hard constraint. Using the fill poll's list INSIDE the account read (the reverse direction) was rejected: the reservation release compares orders with the moment of the account read, and an older list could release a reservation for an order placed in between. |
| `POST /v1/order/preview` before every order | The only first-hand evidence of the time-in-force the venue applies; documented as deliberate. |
| `Venue.spec` 60 s TTL | It is how a closed market is noticed and its orders cancelled. |
| The four `/book` caches (G2) | Merging them changes which book age each reward estimate sees. Gateway only, modest volume. |
| Reward discovery batching (G6) | About 9 requests an hour. |
| Legacy `cancel(); status()` path | Not on the RN1 live path; shutdown code is not the place to save requests. |
| BBO for candidates (G1, engine side) | Not redundant: those markets are not subscribed. Subscribing the rank-watch list to the WS would remove them, but adds seats and changes what wakes the loop. |
| Loss stops, caps, watchdog, registry | Untouched. |

## Suggested order of evidence before any of this is enabled

1. Run one campaign with all flags off and read `venue_requests.by_route`,
   `http_429_by_route`, `gateway_requests.by_route`,
   `ws.gateway_fallbacks_by_cause`, `leg_book_reads`. That replaces every
   estimate in this file with a count, and says which route draws the 429s.
2. Combo flags first (shadow only, no order path).
3. `LIVE_EVENTS_TTL_S`.
4. `ACCT_SEEDS_FILL_LIST` last: compare `fill_poll.list_reads` + `list_seeded`
   and `seen_after_s` in `FILL_MARKOUT` with the run before.

Tests: `tests/test_pmus_request_dedup.py`.
