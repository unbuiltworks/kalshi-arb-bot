# BTC5m maker/taker controller — what was finished, and the authorization request

Date 2026-11-17. Branch `claude/btc5m-favorite-accumulation-wzh2xo`.
**No deployment, arming, or live trading is authorized by this document,
and none was performed.**

## 1. The single highest-impact missing behaviour

I expected it to be the taker-completion path being unreachable. It was
something strictly upstream of that, and worse.

### 1a. The straddle's entry-EV gate had no reader, so the mode could not open a leg

`Params.straddle_p_pair_natural`, `straddle_p_complete` and
`straddle_p_lone_win` are all declared `None`, and the module documents
`None` as the value that **refuses** — deliberately, so a guessed rate
cannot manufacture the edge the gate tests for. **None of the three had
an env reader.** Nothing anywhere in `src/` or `deploy/` ever set them.

So in STRADDLE mode the gate returned `ENTRY_EV_P_PAIR_NATURAL_NOT_SET`
on every step of every window, and **the mode could not place a single
leg**. Everything downstream — the first fill, the residual, the
completion, the crossing — was unreachable, because there was never a
first leg.

This is the same defect, in the same module, that silently disabled the
late favourite, and the comment that records it there describes this case
exactly: *a param with no env reader sits at its dataclass default
forever, and "refused every time" reads identically to "no opportunity"
in a log.*

Found because the end-to-end test I wrote to prove repeated fills could
not get a single order placed against a scripted venue.

**Fix:** five env readers (`l5_btc5m_execution.straddle_probability`,
`straddle_fee_per_share`, `straddle_min_ev_per_share`), wired into the
STRADDLE params override. **The defaults still refuse.** A probability
outside (0, 1] refuses rather than being clamped, because a clamped rate
is a rate nobody measured. The owner sets them deliberately; the measured
values over 503 shadow windows are recorded in the code as reference and
explicitly not as defaults.

### 1b. The completion never priced against the book

With leg entry unblocked, the next gap: the patient completion rests at
`completion_pair_cost − avg_cost_of_held_leg` — a **budget ceiling**.
Nothing ever compared it to the offer it was trying to buy. Two
consequences:

1. An offer already cheaper than the ceiling was never lifted. The
   completion rested at the ceiling instead, forgoing the same side in
   the same size at a strictly lower all-in price.
2. **A resting BUY is filled by sellers coming down to it.** So the
   patient completion filled precisely when that side was being marked
   down — adversely selected by construction. Window 1925z is the
   measured instance: rested at 0.88, duly filled at 0.88 on DOWN's way
   to worthless.

**Fix:** `l5_btc5m_controller._completion_cross`. When the offer, plus the
taker fee charged at its worst point in the fill range, is **strictly
cheaper all-in** than the ceiling the completion would otherwise rest at,
and the resulting pair is still inside its budget, the completion lifts
the offer instead of resting at the ceiling.

This does not decide *whether* to complete — the pair-cost gate already
did. It decides only *how* the completion it already chose is executed.
That makes it a **dominance comparison** on one side in one size, fees
included, not a directional bet, so it consults no settlement
probability and invents none. Pinned by
`TestNoForecastIsConsulted`.

Crossing needs **both** `completion_cross_enabled` and the envelope's
`taker_allowed`, and the adapter's `completion_taker_fault` then
re-verifies against the live book with its own depth haircut, slippage
charge and notional cap. Either refusing keeps the completion resting,
which is today's behaviour. The restriction attestation is updated from
three gates to four.

### Two defects I introduced and caught

- The late-favourite stand-down matched the completion reason by string
  equality (`why_s == "STRADDLE_COMPLETING"`). A crossing completion
  reports `STRADDLE_COMPLETING_CROSS`, so the late favourite would have
  run beside it — the 1925z double-buy re-created by a string
  comparison. Now a prefix match, pinned by a test.
- The completion-rung predicate keyed off the ceiling alone. A crossing
  completion that fills only partly rests at the **offer**, which the
  ceiling test reads as a cheap rung — so we would have cancelled and
  re-placed our own working completion every step, losing its queue
  place and crossing again for the same shares. Both prices now count.

Also caught: reading `quote` inside the straddle branch, which is built
after that branch returns — the identical forward-reference trap the code
already documents for `priced()`. The offer is derived from `bid`/`ask`.

## 2. Defaults: nothing changes until the owner sets it

| Knob | Default | Effect of the default |
|---|---|---|
| `L5_BTC5M_STRADDLE_P_PAIR_NATURAL` / `_P_COMPLETE` / `_P_LONE_WIN` | unset | entry-EV gate refuses every rung |
| `L5_BTC5M_STRADDLE_FEE_PER_SHARE` / `_MIN_EV_PER_SHARE` | 0 | historical values |
| `completion_cross_enabled` | False | completion rests at the ceiling, as today |
| `completion_cross_slippage_ticks` | 0 | limit priced exactly at the offer |
| `completion_cross_min_margin` | 0, floored to q1's 1c by `taker_min_margin()` | crossing must save ≥1c/share all-in |

## 3. The four properties, proved deterministically

`tests/test_l5_btc5m_live_loop.py` — 16 tests, real controller, real
adapter, one scripted venue, one scripted clock.

| Property | Evidence |
|---|---|
| Repeated fills work | a filled side is re-quoted; three consecutive fills on one side each replenish; the side bound and the unmatched ceiling are never breached over eight steps |
| One-sided fills trigger safe residual management | residual named from venue-confirmed fills; the completion answering it never buys a side twice; no loss-making hedge is forced when the offer is outside budget; an unfilled completion leaves the residual exactly as it was |
| Uncertain order states fail closed | an unreadable fate latches `ORDER_UNCERTAIN` and stops submission; it stays stopped while reads keep failing; it lifts only on a clean read; a submit whose outcome is unknown is never retried blind |
| Expiry cancels and disarms | past the cancel lead nothing new is sent; working orders withdrawn by exact id and only ids this lane owns; arming refuses after the window; a cancel that loses to a fill is booked as FILLED, not assumed away |

`tests/test_l5_completion_crossing.py` — 25 tests on the crossing
arithmetic and its bounds.

## 4. Regression

Baseline before this work: **34 failed / 14 errors** (the figure I twice
misreported as 1; corrected in `b0275cfa5`).

**Full suite after this work: 34 failed, 14 errors, 22,358 passed, 28
skipped, 1 xfailed, in 940s.** The failure and error counts are the
baseline unchanged, and passed moved 22,317 to 22,358 -- exactly the 41
tests added here (16 + 25), which reconciles with nothing else having
changed state.

One precision, because I have twice overstated a baseline comparison: I
kept only the summary line of that run, so what is verified is that the
**counts** match, not that the failing **set** is identical. The
independent evidence that the touched area is clean is that every
targeted suite over it passes, and no failure or error in the run names a
btc5m, straddle, completion, crossing or restriction test. This is not a
clean suite and is not described as one: 34 failures and 14 errors remain,
all pre-existing, none in live execution, risk controls or watchdog
behaviour.

Targeted suites:
`test_l5_btc5m_straddle.py` 111 passed, `test_l5_btc5m_live_loop.py` 16,
`test_l5_completion_crossing.py` 25, `test_l5_straddle_repeat_entries.py`
+ `test_l5_taker_pair_completion.py` + `test_l5_residual_phase.py` 126,
`test_l5_restriction_audit.py` 4.

## 5. Preflight, from production

Read 2026-11-17T13:17–13:20Z.

- **Deployed SHA on `l5-btc5m-live`:**
  `241f7432d04e1e8c12f14b653e5ff1426ef275c3`, deployed 2026-11-16T22:38Z,
  status SUCCESS, branch `integration/l5-safe-release-candidate-20261109`.
  **This work is not in it.** The integration merge `ee547053f` deployed
  as SKIPPED by design (the service's watchPattern file is unchanged), so
  the taker gate, the repeated-entry bounds, the residual phase, the EV
  readers and the crossing path are all on the branch and none of them is
  running.
- **Watchdog `l5-halt-watchdog`:** healthy, sweeping every 20 s.
  `open_orders: 0`, `halted: null`, `read_error: null`, `unverified: []`,
  `not_ours: []`, `cancel_enabled: true`, `max_order_age_s: 300`.
- **Account-wide exposure:** zero open orders, from the watchdog's own
  sweep rather than from any lane's self-report.
- **Settled realized P&L, BTC5m: $0.00.** There has never been a
  venue-confirmed fill on this lane. Not a flat result — no result.
- **Ownership registry: still unresolved, and it is my error.** I
  overwrote `L5_ORDER_REGISTRY_URL` on the watchdog acting on a misread
  field, and variable values are redacted to this session, so I cannot
  read back what it was. The previous value is needed from the Railway
  dashboard. `managed_lanes` in the sweep above is the managed-lane list,
  **not** the registry, and says nothing about registry coverage.
  Practical risk is currently nil (zero open orders account-wide) but
  that is timing, not a sound state. **I would not arm a live window
  before this is resolved.**

## 6. Authorization request

Not yet ready to request a window, for three reasons that are mine to
fix or the owner's to waive:

1. The work is **not deployed**, and deploying it is money-capable by
   definition even though every new capability ships off.
2. The **ownership registry** is in a state I put it in and cannot read
   back.
3. The **entry-EV rates would have to be set** for the mode to place
   anything at all. That is a deliberate owner decision about three
   measured probabilities, not a default I should pick.

If the owner wants to proceed regardless, the shape of the smallest
venue-confirmed test is:

| Field | Value |
|---|---|
| Service | `l5-btc5m-live`, project `arb-ev-wallet-shadow` |
| SHA to deploy | the commit carrying this work (recorded at commit time) |
| Window | one BTC5m window, e.g. `2026-11-17T13:30:00Z`–`13:35:00Z`, slug `cpc-btc-updown-5m-2026-11-17-1330z`; windows start every 5 minutes, so the next available is the first boundary ≥ ~6 minutes after a deploy lands |
| Mode | STRADDLE, maker-first, one market, one outstanding order per side |
| Taker | completion only, behind all four gates; set `L5_BTC5M_TAKER_COMPLETION=YES` only if the owner wants the crossing path exercised in this window — the window is valid without it |
| Max additional loss | **$1.60**, inclusive of fees and of outstanding exposure. Nothing else is outstanding (zero open orders), so the whole $1.60 is available to this window — but it is a combined bound, not an allowance on top of exposure |
| Disarm | automatic at window end: `end_of_window` cancels every one of our orders by exact id, the campaign approval covers one window, restart policy NEVER, no automatic repeat after completion or crash |
| Rebates | none assumed. This account is maker-rebate Tier 1 — **no rebate** — under the 2026-11-14 re-tiering |

Three explicit non-assumptions carried into this work: no atomic
multi-leg FOK (see `PMUS_ATOMICITY_VERIFICATION.md`), no maker rebates,
no forced loss-making hedge.
