# BTC5m one-window live-test envelope — ready to approve

**Status:** not approved, not armed, not deployed. Nothing here is set.
Running SHA is already `38b243c247d8480892bae025f7fe86cb2656e2ae`
(deployment `6689130f`, SUCCESS, disarmed).

---

## 0. A HARD BLOCKER COMES FIRST

**The probe cannot submit anything today, whatever is approved.**

BTC5m's `account_exposure()` returns
`unknown("OPEN_ORDER_QUANTITY_UNREADABLE")` the moment any foreign open
order has an unreadable remaining quantity, and `_submit` then refuses
`ACCOUNT_STATE_UNKNOWN`. The owner's two hand-placed sports orders are in
that state right now — `l5-pmus-live` reports it directly:

```
account_exposure: {ok: false, reason: "FOREIGN_ORDER_UNREADABLE:DT0VA9S013YY"}
foreign_orders:   DT0VA9S013YY, DT0VCZR013ZK   (aec-nba-mil-mia-2026-11-18,
                                                remaining: "")
```

Both lanes read the same venue open-orders endpoint, so BTC5m hits the
same row. This is fail-closed and correct — an unknown account refuses
every submission — but it is a precondition, not a detail.

**It clears when those two orders stop being unreadable**: the owner
cancels them, they fill, they expire, or the venue starts reporting their
`remaining`. Nothing in this envelope touches them, and I have not.

The same cause blocks sports expansion, which the owner already gated on
"shared account and RFQ liabilities correctly bounded". **One fact blocks
both.** Clearing it is the single highest-value action available.

---

## 1. The window

| | |
|---|---|
| Market | `cpc-btc-updown-5m-2026-11-19-0130z` |
| Window | 2026-11-19 **01:30:00Z → 01:35:00Z** |
| Count | ONE window. No automatic repeat, no roll to the next. |

The slug must name a window that has **not yet opened** when the
variables are set. If 0130z has passed by then, re-name it explicitly to
the next `…-19-HHMMz` — do not let the lane roll. The lane currently
names a stale `2026-11-17-2225z`, which must be overwritten.

## 2. Configuration to set (none of it is set now)

```
L5_BTC5M_MARKET                 cpc-btc-updown-5m-2026-11-19-0130z
L5_BTC5M_CLIP_USD               0.08
L5_BTC5M_COMPLETION_PAIR_COST   0.96
L5_BTC5M_RANKER_VACUOUS_HOLD    FALLBACK
```

`0.08` is measured, not guessed: a first leg from flat is entirely
unmatched, so its prospective unmatched cost IS its notional, and the
mode opens only when clip ≤ `straddle_max_unmatched_usd`. Verified on the
release tree — 0.08 yields a 1.00-contract clip at $0.0800 against the
$0.10 ceiling, clearing the venue minimum (0.01 contracts) by 100×, while
0.11 refuses the first leg with `UNMATCHED_CEILING`. `0.10` also passes
but sits exactly on the ceiling, so 0.08 is preferred for the cent of
headroom.

`COMPLETION_PAIR_COST` must be > 0 or the entire completion branch — the
inventory ranker included — is skipped.

**Arming itself is separate and is NOT part of this document.**

## 3. Limits — all existing, none raised

| Bound | Value | Source |
|---|---|---|
| Per-side committed | **$0.30** | `straddle_max_side_usd` |
| Unmatched / worst-case lone leg | **$0.10** | `straddle_max_unmatched_usd` |
| Max aggregate exposure, this lane | **$5.00** | `max_exposure_usd` |
| Account-wide cap | **$10.00** | `L5_ACCOUNT_MAX_EXPOSURE_USD` |
| Aggregate notional, both sides | **$0.60** | 2 × side cap |
| Concurrent live orders | **2** | `live_order_bound` (1 rung × 2 sides) |
| Daily realized loss halt | **$50** account-wide | `daily_realized_loss_halt_usd` |
| Probe-specific realized stop | **$0.50** | this envelope |
| Entry cutoff | **t ≥ 270s** | `no_new_entry_after_s` |
| Taker execution | **disabled** | `taker_completion: false` — unchanged |

Nothing above is raised by this envelope. The owner asked for the $20
account cap earlier; production reports **$10.00**, which is tighter, and
I have not changed it.

## 4. Maker-only

Maker-only, enforced by configuration already in place: `taker_completion:
false`, and the lane logs `SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER`. This
envelope does not enable taker execution at any price, including for
completion. A completion that cannot be made as a maker is not made.

## 5. Economic gates — all retained

Every one of these still refuses, verified with the fallback ON:

- `ENTRY_EV_P_PAIR_NATURAL_NOT_SET` / `_P_COMPLETE_NOT_SET` /
  `_P_LONE_WIN_NOT_SET` — an unpriceable rung is refused, never assumed;
- `UNMATCHED_CEILING`, `SIDE_CAP_REACHED`, `NO_EXPOSURE_ROOM`;
- `ENTRY_WINDOW_CLOSED`;
- `ACCOUNT_STATE_UNKNOWN` (§0), `ACCOUNT_EXPOSURE_CAP`,
  `ACCOUNT_CASH_SHORT`;
- the authorization class budgets, including the completion reserve that
  refuses to open exposure the COMPLETE budget could no longer close.

The ranker fallback invents no fill rate, raises no cap and authorizes no
taker; three tests assert exactly that, and it only stops a comparison
that compared nothing from vetoing a risk-reducing completion.

## 6. Watchdog

- Independent `l5-halt-watchdog` (`faebeca6`) must report
  `l5-btc5m-live…:8090:OK` in `registries` before arming, and it does
  now.
- `cancel_enabled: true`, `max_order_age_s: 300`.
- The lane serves its registry while disarmed (`serving_registry: true`),
  so an expired envelope no longer blinds the watchdog.
- **The registry claims only our own orders** — adopted rows are reported
  under `adopted_not_ours` and never offered for cancellation.
- Abort if the watchdog's `read_error` is non-null or its heartbeat
  stales beyond its own tolerance.

## 7. Cancellation and reconciliation

- Cancel by **exact venue order id** only, through `deadman`, which
  re-reads each id and classifies by the venue's own state. `cancelled`
  means terminal on re-read; `still_open` and `unverified` mark the book
  UNCERTAIN and are retried, never reported as success.
- **No order is cancelled without verified ownership.** An id with no
  submit record of ours is refused and listed in `refused_not_ours`.
- Cancellation stays available while submission is blocked: it only ever
  reduces our own exposure.
- At window end: `settle()` records the venue's own settlement; a market
  the venue has not finally settled keeps counting as exposure.
- A cancelled order does **not** flatten filled residual inventory —
  residual is closed by a maker completion or carried to settlement.
- Any ambiguous POST halts re-entry pending authoritative reconciliation.

## 8. Stop conditions — any one aborts, no repeat

1. probe realized loss reaches **$0.50**;
2. unmatched exposure exceeds **$0.10**;
3. an order is submitted that the registry cannot name (no
   `venue_order_id`) — the known, unfixed gap;
4. the watchdog becomes unreadable or stales;
5. the account-wide daily loss halt asserts, or any halt latches;
6. `account_exposure` returns UNKNOWN mid-window;
7. a fill lands on any market other than the single named window;
8. an unrecognised refusal string appears;
9. the window ends. **No automatic relaunch** after completion or crash.

## 9. Expected observables

1. `STRADDLE_LEG` on UP and DOWN in the same step (two-sided quoting);
2. a filled side re-quoted while inside both bounds — up to ~3 fills a
   side before the $0.30 side cap binds;
3. a terminal refusal naming a **dollar** bound, `SIDE_CAP_REACHED`, not
   a fill count;
4. on a lopsided fill, `STRADDLE_COMPLETION` on the LIGHT side with
   `evaluation.completion.ranking_vacuous: true` and
   `ranker_note: RANKER_NO_PRICEABLE_ALTERNATIVE` — **this line is the
   proof of the fix**; before it, the ranker vetoed that order and the
   leg stranded;
5. the registry readable throughout, `adopted_not_ours` empty.

## 10. Expected economics — negative, and that is the point

Measured BTC15m proxy (n=148, authenticated, 20 placeholders excluded):
mean realized **−$0.0215/market**, shrunk-mixture EV **−$0.0190**,
`ev_lower = ev − 1·se` **−$0.0750**. BTC5m has **no** authenticated
sample of its own.

**Expected settled realized net P&L for this window: negative** — about
−$0.02 expected, −$0.08 at one standard error, bounded at −$0.10 by the
unmatched ceiling plus fees. The probe's purpose is to obtain the first
authenticated BTC5m datum for `p_pair_natural` and to observe the
repeated-fill and completion mechanism. A positive single-window outcome
would be noise, not edge.

Report afterwards, settled-only: venue-confirmed fills (side, price, qty,
fee, timestamp); matched pairs and realized pair cost against $0.96;
residual unmatched qty at settlement and its realized P&L; **settled
realized net P&L**; realized natural-pair rate; and every refusal reason
with counts. No mark-to-market, no estimated rebates, no modelled pair
profit.

## 11. The approval required, precisely

One approval covering exactly:

> Set `L5_BTC5M_MARKET`, `L5_BTC5M_CLIP_USD=0.08`,
> `L5_BTC5M_COMPLETION_PAIR_COST=0.96`,
> `L5_BTC5M_RANKER_VACUOUS_HOLD=FALLBACK` on `l5-btc5m-live` at SHA
> `38b243c247d8480892bae025f7fe86cb2656e2ae`, then arm for the single
> window named in §1 and no other, with every limit in §3 unchanged and
> taker execution disabled.

It does **not** cover: a second window, a capital increase, taker
execution, relaxing any gate, resetting a halt, or re-arming after the
window ends.

**And it cannot take effect until §0 clears.**
