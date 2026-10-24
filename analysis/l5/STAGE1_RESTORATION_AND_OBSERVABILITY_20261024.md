# Stage 1: $5 restoration verified, 1500z adverse selection, recorder 401 diagnosed

**2026-10-24.** Deployment `6a03034e` (commit `c2b6f447`, variable-only change),
service `l5-pmus-live`.

Labels throughout: **settlement-only realized P&L**. The 1500z residual below is
an open position, not a realized loss, until the venue settles it.

---

## 1. The five restoration checks, from the running process

`L5_MAX_CASH_DEPLOYED_USD` was restored from `3.70` to `5.00` at 14:54:05Z;
the redeploy reached SUCCESS at 14:55:01Z on the same commit, so no code moved.

| # | Check | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `MANDATE_SCOPE_CHANGED` cleared | **PASS** | `L5_LIVE_ARMED` 15:01:25.43Z armed 1500z. The refusal is gone, and the arm carries `scope_digest c94e52d3e2e2f2b72e6ac5395bc851c85cfe25f4543eb2f9ea36e838009dea2a` with `max_cash_deployed_usd: "5"` in scope — the digest the mandate recorded. |
| 2 | Mandate still valid | **PASS** | `mandate_session_id camp.543686b8c6a9df9989b2813f`, `not_after 2026-10-24T17:24:27+00:00` unchanged, `max_windows "8"`. `L5_AUTHORIZATION_PREFLIGHT` 14:55:02Z `ok: true, fail: [], unknown: []`, `campaign_approval state: approved`, `expires_in_s 8964.2`. |
| 3 | All risk checks pass | **PASS** | Scope as armed: `max_usd_per_market 20`, `max_total_exposure_usd 5`, `daily_realized_loss_halt_usd 5`, `max_live_orders 1`, `execution_mode MAKER_ONLY`, `close_margin_s 60`. `L5_LIVE_PREFLIGHT PASSED` 14:56:38Z, 15/15 blocking checks. |
| 4 | Exposure and unresolved orders zero at restoration | **PASS** | `no_unexpected_open_orders: 0`, `executor_starts_disarmed: true` at 14:56:38Z, before any arm. |
| 5 | Opening quantity cannot exceed 3 contracts | **PASS, and now proven live** | Preflight `residual_ceiling`: `absolute_cap_contracts "3"`, `effective_cap_contracts "3"`, `which_binds "ABSOLUTE"`. Then the venue record: order `D9X2DAXG11N6`, coid `l5f-71bebc5d5814ecaa9b74a791`, `ORDER_STATE_FILLED`, `cum_qty "3"` exactly, `avg_px "0.4400"`, `commission "0.0000"`, 15:01:31.437Z. |

Item 5 is the one that matters: the **same code path produced a 4.09-contract
fill at 13:31:45Z** before the cap. At the restored $5 envelope it produced
exactly 3.00. The cap, not the cash figure, is what bounds the contract count —
as the commit argued and the venue has now confirmed.

**A number that needed resolving rather than reporting.** The arm record reads
`windows_spent: 1` while 1331z had already spent one, which could have meant the
ledger reset across the restart. It did not: the live heartbeat at 15:04:34Z
reads `windows_spent = 2`. The arm record reports the **pre-claim** count. Two of
eight windows are spent and the ledger survived the redeploy.

---

## 2. 1500z: the complement was unreachable by construction

Venue-confirmed sequence, one market, one order at a time, maker only:

| Time (UTC) | Book (wire UP) | Event |
|---|---|---|
| 15:01:25.43 | 0.46 / 0.47 | armed |
| **15:01:31.44** | 0.44 / 0.45 | **first leg FILLED: 3.00 UP @ own 0.4400, commission 0.0000** |
| 15:02:03.90 | 0.31 / 0.32 | book gone |
| 15:04:37.64 | 0.12 / 0.13 | complement refused `COMPLEMENT_PAIR_ABOVE_CAP`, pair 1.2900 |
| 15:11:34.70 | **0.01 / 0.02** | `PAST_TERMINAL`; completion window closed |

At 15:04:37 the engine priced the DOWN complement at **0.8500** against a first
leg of 0.4400 — a pair of **1.2900** against the 0.96 ceiling. The frozen
complement plan price was 0.5100 (`frozen_price_superseded`), which would have
made a 0.95 pair; the book left it within 30 seconds.

**This is adverse selection, stated plainly.** Our maker bid at 0.44 filled
*because* the market was moving through it. Two and a half minutes later UP was
worth 0.125. The complement was then unreachable not by bad luck or bad timing
but by arithmetic: once the first leg is 0.44 and UP trades at 0.12, no DOWN
price exists that both completes the pair under 0.96 and is available to buy.
### Replayed through the new module, against the engine's own quoting rule

The deployed probe rests **2 ticks behind the touch**, so the complement's own
price is `1 - (ask + 2 ticks)`. The module reproduces the engine's own logged
figures exactly at two independent points — `0.5100` (its
`frozen_price_superseded`) and `0.8500` for a pair of `1.2900` (its refusal) —
which is what makes the rest of this table trustworthy:

| Frame (UTC) | Book | Engine's DOWN quote | Pair | Fee-safe? |
|---|---|---|---|---|
| **15:01:26.70** (in effect at the fill) | 0.46 / 0.47 | **0.5100** | **0.9500** | **YES** |
| 15:01:35.17 | 0.44 / 0.45 | 0.5300 | 0.9700 | no |
| 15:02:03.90 | 0.31 / 0.32 | 0.6600 | 1.1000 | no |
| 15:04:37.64 | 0.12 / 0.13 | 0.8500 | 1.2900 | no |
| 15:11:34.70 | 0.01 / 0.02 | 0.9600 | 1.4000 | no |

**The fee-safe window was the frame the fill happened in, and no later one.**
That frame was in effect at the venue fill timestamp, was 3.007s old when the
leg printed and stood for 8.473s in total. By the next observed frame — 5.5s
after the fill — the pair was 0.9700, one cent over the ceiling, and it only
widened from there.

**Stated as the limitation it is:** those five frames are a sparse
reconstruction from the few book blocks that happen to appear in logs. The real
stream carried thousands of updates per window (1445z alone logged 2,735), so
"no later frame was fee-safe" is properly "none of the four observed frames
after the fill was fee-safe". Closing that gap from assertion to measurement is
exactly what the ring is for.

A correction worth recording, because I had it backwards first and a test caught
it: resting behind the touch makes the own price **cheaper**, not dearer — it is
a less aggressive bid, so it costs less *if it fills* and is *less likely* to
fill. Price and fill probability move in opposite directions, so neither figure
alone answers "was it reachable", and the module now reports both with that
stated.

`frozen_price_superseded` is the mechanism — the plan prices both legs at the
same instant, and the fill selects for the instants when that instant is stale.

Outcome: **3.00 UP stranded unmatched at a cost of $1.3200, commission
$0.0000, and the venue has now settled it worthless** — `settlement_pnl
"-1.3200"` at 15:16:18.885Z, `yes_value "0.0000"`. This is a **settled, realized
loss**, the only kind this project labels realized. Details and the resulting
ledger positions are in §2b.

---

## 2b. 1500z settled in 78 seconds, and 1515z was therefore clean

I first read the 1515z fill as a possible breach of *"no new first leg while
unrelated residual exposure exists"*, and checked before reporting it as one. It
was not a breach, and the reason is worth more than the non-finding.

**`L5_LIVE_SETTLED` for 1500z, 15:16:18.885Z** — 78 seconds after the window
closed at 15:15:00, not the ~16 minutes recorded historically:

- `yes_value "0.0000"`, `venue_state "MARKET_STATE_EXPIRED"`,
  `resolution_source "VENUE_SETTLEMENT_PX"` — UP settled worthless
- `residual_qty "3"`, `residual_side "UP"`, `residual_cost "1.3200"`,
  `residual_redeem "0.0000"`, **`settlement_pnl "-1.3200"`**
- `matched_qty "0"`, `matched_pnl "0.0000"` — no pair, as expected
- `committed_released "1.3200"`

The 1515z first leg was submitted at **15:17:00** (`ORDER_STATE_NEW`,
`D9X9F28G11N6`) and filled at 15:17:13.987Z — **42 seconds after 1500z settled
and released its committed cash.** `MAX_CONCURRENT_MARKETS` is checked at
submission, `max_concurrent_markets` is `1` in production (confirmed from the
emitted limits, no env override), and `open_markets()` counts markets with
`committed > 0`. With 1500z released, the count was zero. The gate was satisfied
on its own terms, and no residual was carried across windows.

**The operationally useful part: settlement at T+78s, not T+16min.** The earlier
figure made every-window participation look like it required carrying unsettled
inventory, which is what put *continuous participation* and *no new leg while
residual exists* in apparent conflict. At T+78s there is no conflict — a window
can settle fully inside the next window's entry period. If T+78s holds up across
more windows, both requirements are satisfiable at once and nothing needs
relaxing. I am recording it as **two observations (1500z, and whatever 1515z
does), not a rule.**

### Second opening leg capped at exactly 3 — and 1515z COMPLETED

`D9X9F28G11N6` / `l5f-223d04e4ab2680c5057ff63c`, `ORDER_STATE_FILLED`,
`cum_qty "3"`, `avg_px "0.3000"`, `commission "0.0000"`. The 3-contract ceiling
held a second time, at a different price — 0.30, where the old cash-only sizing
would have permitted `5 / 1.30 = 3.85`.

**And unlike 1500z, the complement came back.** `L5_WINDOW_ACCOUNTING` for
1515z, `phase "CONFIRMED"` at 15:31:25Z:

- `orders.submitted 2`, `filled_orders 2`, `partial_fills 0`, `cancelled 0`,
  `rejected 0`; `maker_orders 2`, `taker_orders 0`, `actual_fees_usd "0"`
- `contracts.filled_total "6"`, `first_leg_filled "3"`,
  **`complement_filled "3"`**
- `settled.matched_qty "3"`, `matched_pnl_usd "0.2400"`,
  `residual_qty "0"`, `residual_pnl_usd "0.0000"`,
  `venue_state "MARKET_STATE_EXPIRED"`
- **`realized_net_usd "0.2400"`**, `realized_basis` venue-confirmed settlement
  only, excluding the `0.0143` estimated rebate entirely

The pair cost 0.92 — first leg 0.30 plus a 0.62 complement — comfortably inside
the 0.96 ceiling, returning **+$0.0800 per contract**, which is almost exactly
the +0.0824/contract the previous nine windows produced on pairs. **A correction
to my own earlier reading:** I checked for complement fills through 15:25, found
none, and wrote that the leg was unmatched. It filled after that, and the
conclusion was wrong. The lesson is the one already on the record — an order is
declared absent only when the lifecycle stream and the settlement lines agree,
and a window is not over until it settles.

### Where the limits now stand

Two ledgers, as designed, and they disagree because they measure different
things:

- **$5 daily halt** (`realized_current_utc_day`): −$0.8998 − $1.3200 + $0.2400
  = **−$1.9798**, leaving **$3.0202**.
- **$20 cumulative halt** (`realized_today`): `-10.1217` after 1500z, improved
  by the 1515z pair, leaving roughly **$10.12**.

**The $5 daily halt remains the binding constraint**, with room for about two
more windows like 1500z. If it fires, that is the gate doing its job and not a
failure; no intervention is needed or recommended.

**Three windows, and the pattern is the one the frozen corpus predicts**: pairs
earn (+$0.2400 on 3 contracts), stranded first legs pay for them (−$1.3200 on 3
contracts), and the residual is roughly five times the pair in magnitude. The
question is not whether pairs are profitable — 5 of 5 now are — but how often a
first leg strands, and *that* is what the book evidence in §3 measures.

---

## 3. The decisive data gap, and the smallest thing that closes it

The owner's question — was a fee-safe complement *ever* displayed while we held
unmatched inventory, and with how much depth — could not be answered from the
deployed stack, for three separate reasons:

1. `FillCalibration` captures a book block at **pre-submit only**. Nothing is
   recorded at a fill.
2. `AuthBook.snapshot` keeps the **current** top of book and no history. It
   reads a `bbo_history` key that **only the separate evidence recorder ever
   writes**, so in the live process `last_update_t` is permanently `None` and a
   book synchronized to a venue fill timestamp is not reconstructable at all.
3. The ladder keeps full rungs for **exactly one frame**.

Between them there is no record of the opposite side at the moment a leg filled,
and none whatsoever for the intervals when nothing of ours was resting — which
is precisely when the position sits unmatched.

### `src/l5_book_evidence.py` — reporting only, 642 lines, 39 tests

- **`BookRing`** — a bounded per-slug history of full-depth frames (default 2048
  rows × 6 slugs × 8 rungs, deduped on unchanged ladders). It attaches through
  `FrameFanout.add`, which calls the money path's observer **first** and wraps
  extras individually, and it reads the ladder the executor itself models rather
  than parsing a raw frame — so the evidence and the money path cannot disagree
  about what the book was.
- **`as_of(slug, event_t)`** — the frame **in effect** at a venue timestamp: a
  row qualifies only when the next row starts after the event. It reports
  `frame_age_at_event_s`, `superseded_at_t` and `frame_held_for_s`, and refuses
  with `NO_FRAME_AT_OR_BEFORE_EVENT` rather than guessing.
- **`complement_reachability`** — the opposite side's top of book and depth, with
  the maker-join and taker-cross own-prices (`1 - wire` for DOWN), the engine's
  actual `depth_ticks`-behind quote, the
  queue-ahead **bound**, a depth walk giving the executable VWAP and an
  `insufficient_displayed_depth` flag, and the pair cost each implies against the
  first leg with `clears_pair_ceiling`.
- **`fill_evidence`** — carries **both time bases as separate keys**:
  `book_at_event` (the ring frame at the venue's own event timestamp) and
  `book_at_processing` (what we could see when the fill was handled), with
  `processing_lag_s` between them. A test pins a case where the two bases
  **disagree about whether the ceiling cleared**, which is the entire reason to
  carry both.
- **`ResidualSampler`** — driven by **inventory being non-zero, not by an order
  existing**, so it fires in exactly the intervals an order-hung sampler is blind
  to. `order_resting` is recorded as an observation, never as a condition.

**A touch is never a fill.** Maker figures are labelled a displayed quote whose
fill the venue's absent per-order queue cannot promise; only the taker walk is
called executable, and only against displayed depth, excluding fees.

**The boundary is structural, not promised.** The module imports `__future__`,
`decimal` and `typing` and nothing else — asserted by a test that walks its own
AST. It is handed books as data and returns dicts; it has no object through which
it could size, price, submit, cancel or refuse anything. Entry, sizing,
repricing and cancellation policy are untouched, and no new service is involved.

### Regression, measured by stashing rather than asserted

| Run | Failed | Passed | Errors |
|---|---|---|---|
| Baseline (these files moved aside) | 58 | 13023 | 23 |
| Module only | 58 | 13062 | 23 |
| **Module + wiring (committed)** | **58** | **13074** | **23** |

The failure and error ID sets are **byte-identical** across all three — 81 ids,
empty `diff` — and passes rise by exactly the test count each time (+39, then
+51 with the venue-timestamp tests). The 58 failures and 23 errors are all
pre-existing: `test_gemini_q1_runner_wiring`, byte-identity pins on files this
work does not touch, `test_pmus_auth` signature pins, and the three `httpx`
collection errors.

The one new failure this work did cause was
`test_l5_live_runner_graft.py::test_the_image_copies_every_module_the_entrypoint_can_reach`
— absent from the baseline, so unambiguously mine. The new module is reachable
from the entrypoint and was missing from the Dockerfile `COPY` list, exactly as
`l5_window_accounting` was last time. Added and re-run green. Without it the
module would have been absent from the image and the wiring would have failed
closed into `evidence_attach_error:ModuleNotFoundError` — recording nothing,
and breaking nothing.

**Deployment is deliberately held.** Wiring it in requires a restart, and the
restart at 14:55 already cost the 1445z window (`PAST_ENTRY_WITH_NO_INVENTORY`,
603s in, `claim_taken: false`). With a live 3-contract residual carried to
settlement under the existing limits, restarting now would abandon it mid-flight.
Recommended: deploy after the campaign closes at 17:24:27Z.

---

## 4. Recorder HTTP 401: diagnosed, needs one owner action

`pmus-ws-evidence-recorder` (`71b6ea99`) has recorded **nothing for three days**:
`conn: reconnecting`, `auth: unknown`, `last_error: "server rejected WebSocket
connection: HTTP 401"`, and `messages_received` frozen at the **identical**
4,538,375 at 2026-10-21T23:58, 2026-10-23T12:59 and 2026-10-24T14:40.
`top_markets: []`, `fresh_markets: 0`. **It captured nothing during either
campaign.**

What the evidence rules out:

- **Not a missing variable.** Both services define exactly `PMUS_API_KEY_ID` and
  `PMUS_API_SECRET_KEY`. Same two names, no extras.
- **Not a different endpoint, scheme or code path.** `l5_pmus_auth_book.py:488`
  calls `R.auth_headers(os.environ[R.KEY_ENV], os.environ[R.SECRET_ENV])` and
  connects to `R.WS_URL` — it imports the **recorder's own module**. Identical
  `wss://api.polymarket.us/v1/ws/markets`, identical Ed25519 signature over
  `{ts}GET{path}`, identical header names, identical env var names.
- **Not a one-session-per-key limit.** On 2026-10-19T23:58:42 the live service
  heartbeat shows `source: "authenticated_ws"`, `stream_age_s: 1.83`,
  `book_updates: 6539`, while 38 seconds later the recorder was still writing
  `authenticated_book_rows` with `auth: ok`. Two authenticated sessions on the
  same key coexisted.
- **Not a deployment.** The recorder's last deploy activity is 2026-10-21T00:43Z
  and every recent deploy is `SKIPPED` (build `watchPatterns`), so no new code
  reached it. The break began **after Oct 21 08:00** and was established by
  23:58; the recorder was healthy with `auth: ok` on Oct 17 and emitting
  `PMUS_LADDER_ROWS` / `PMUS_BOOK_ROWS` / `PMUS_TRADE_ROWS` on Oct 19 23:59.
- **Large clock skew is not indicated.** Internal-vs-ingestion gaps at the 401
  (6.29s, 9.58s, 4.71s) sit inside the measured bimodal Railway log lag
  (0.03–9.64s); timestamp rejection needs far more.

**What remains is the only input that differs and the only one I cannot read:
the credential *values* held by the recorder service.** Railway redacts variable
values to this role, so I can compare names but not contents. The live service
proves its own values authenticate right now (`keyfp:dd262ed4fb89`,
`funded:344.00565`).

### Owner action required

Set the recorder's two variables to the same values the working `l5-pmus-live`
service holds:

- service `pmus-ws-evidence-recorder` → Variables → `PMUS_API_KEY_ID`,
  `PMUS_API_SECRET_KEY`
- copy from service `l5-pmus-live`, same project
  (`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`), environment `production`

Then the recorder redeploys and `PMUS_WS_STATUS` should show `auth: ok` with
`messages_received` climbing off 4,538,375. If it still 401s with values proven
identical to the live service's, the next step is the venue — the key would be
scoped or rate-limited per client rather than stale.

**No credential value appears in this document, in any log quoted here, or in
anything I ran.** I have not rotated, regenerated or attempted to read any key.

### Worth knowing for §3

The recorder's `PMUS_LADDER_ROWS` on Oct 19 carried the full rungs
(`rungs_cap: 16`) and `PMUS_BOOK_ROWS` carried bid/ask/qty with staleness — it
is the one component that already retained close to the right record shape. But
it writes `bbo_history` into **its own** process state, not the live executor's,
so repairing it would give a second observer of the book and still not answer
what our own fills saw. The two are complementary, and §3 is the one that
answers the owner's question.

---

## 5. Next highest-impact profitability action

Not more windows, and not more capital. **Entry-time adverse selection is now
the measured bottleneck**, and 1500z is the cleanest instance yet: a 3.00-contract
first leg filled at 0.44 on a book that was 0.12 within three minutes, with the
complement mathematically out of reach from the moment the first leg printed.

Across all windows to date the split is unchanged in character: **every pair has
been profitable — now 5 of 5** (+$0.3865 over 4.69 contracts at +0.0824/contract
in the first nine windows, plus +$0.2400 over 3 contracts at +0.0800 in 1515z),
while stranded first legs pay for them (−$0.6282 over 7.33 contracts, plus
−$1.3200 in 1500z). The pairs work and are not the problem. **The problem is the
rate at which a first leg strands, and how adversely the book has moved by the
time it does** — in 1500z the fee-safe window was the single frame the fill
landed in.

The proposal is to measure before changing anything: deploy §3 after 17:24:27Z,
gather fills and unmatched intervals with the opposite side recorded on both time
bases, and answer whether a fee-safe complement was *ever* displayed during those
intervals. That single distribution decides between three very different
remedies — quote further behind the touch, require a stability condition before
the first leg, or accept the residual as a cost of the pair — and I cannot
honestly rank them without it. **No policy change is recommended on this
evidence, and none is included in the code.**
