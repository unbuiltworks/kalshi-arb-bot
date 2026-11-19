# BTC5m one-window live-test envelope — ready to approve

**Status:** not approved, not armed, not deployed. Nothing here is set.
Running SHA is already `38b243c247d8480892bae025f7fe86cb2656e2ae`
(deployment `6689130f`, SUCCESS, disarmed).

---

## 0. THE BLOCKER HAS CLEARED, AND THE RELEASE IS DEPLOYED

Both conditions this envelope was gated on are now satisfied by evidence,
not by assumption.

**Deployed:** `3f50258699b3b4ea5c32d546fcf6ff55c4e2f9bf`, deployment
`ccc80612-89f6-48df-bd09-137cea517239`, SUCCESS at 2026-11-19T03:09:10Z,
from `release/l5-btc5m-foreign-qty-20261119`. Disarmed:
`L5_BTC5M_ARM {armed: false, reason: "ENVELOPE:ENVELOPE_EXPIRED"}`,
`can_submit: false`, `serving_registry: true`.

**Exposure, at 2026-11-19T03:14:28Z:**

```
account_exposure {ok: true, account_total_worst_case_usd: "0",
                  capacity_usd: "3.00", markets_counted: []}
foreign_orders   {}                      <- DT0VCZR013ZK is gone
preflight        PASSED                  <- was FAILED
```

The unreadable-quantity blocker cleared on its own: `DT0VCZR013ZK`
resolved and its market settled for -$0.60. **So the deployed
foreign-quantity recovery is not what cleared it** -- it is insurance
against the venue omitting a quantity again mid-window, which on the
previous SHA would have refused every submission and wasted the window.

`DT0VA9S013YY` had FILLED earlier (5 contracts UP) and has since settled;
it is gone from `positions`. The two remaining legs in PMUS's book are
phantoms -- its own `drift` reports `venue: 0` for both -- and BTC5m's
reader consults the VENUE, so its foreign exposure is 0.

**WHAT IS STILL NOT DIRECTLY VERIFIED.** BTC5m's OWN
`account_exposure()` has never been executed: it runs only inside
`_submit`, and the lane is disarmed. The judgement above is inferred from
PMUS's reads of the SAME venue endpoints plus BTC5m's code. Its first
real execution is at arming, where it fails closed.

**ONE PRE-ARM CHECK I CANNOT MAKE.** `budget_reach` preflights the OPEN
class only (`open_budget: 90`, `orders_per_window: 2`,
`windows_payable: 45`, `ok: true`). BTC5m's `L5_LIVE_AUTH_MAX_COMPLETE`
and `_MAX_REPLACE` are not preflighted and their values are redacted to
me. This is the 19:30z failure mode exactly -- COMPLETE was 2 while the
controller needed more, so the risk-REDUCING leg starved. **Confirm
COMPLETE has at least ~8 tickets of headroom before arming.**

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

Two approvals, in order. They are separate on purpose.

**(a) The safety release, disarmed:**

> Deploy `3f50258699b3b4ea5c32d546fcf6ff55c4e2f9bf` to `l5-btc5m-live`,
> strictly disarmed, changing no variable, no limit, no capital and no
> trading authorization.

**(b) The window, only after (a) shows `account_exposure.ok: true`:**

> Set `L5_BTC5M_MARKET` (per §1), `L5_BTC5M_CLIP_USD=0.08`,
> `L5_BTC5M_COMPLETION_PAIR_COST=0.96`,
> `L5_BTC5M_RANKER_VACUOUS_HOLD=FALLBACK` on `l5-btc5m-live` at that SHA,
> and arm for that single window and no other, with every limit in §3
> unchanged and taker execution disabled.

**Automatic disarming at window end** is already the deployed behaviour,
not something this envelope adds: the campaign envelope expires, the
persistent state latches to MONITOR, `can_submit` returns false, and the
lane stays resident serving its registry. A refreshed envelope alone
cannot re-arm it — re-arming requires the startup ceremony.

Neither approval covers: a second window, a capital increase, taker
execution, relaxing any gate, resetting a halt, or re-arming after the
window ends.

**(b) cannot take effect until §0 clears — which the release is intended
to achieve but has not yet demonstrated in production.**
