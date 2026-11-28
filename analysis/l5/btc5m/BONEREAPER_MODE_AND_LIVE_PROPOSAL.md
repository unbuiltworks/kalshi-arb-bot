# Bonereaper-style BTC5m mode: is the one-window probe safely executable? (issue #247)

**Verdict 2026-11-19: SAFE TO BOUND, NOT AUTHORIZABLE.** The loss and
exposure of one window are now provably capped. The probe still cannot be
authorized, because the normal entry-EV gate refuses every quote and nothing
measured would honestly let it through. Nothing is deployed or armed. Zero
venue-confirmed BTC5m fills. This document authorizes nothing.

Code under review: **`cab8e12894b5c0e8f43d9ce5ce412b23fc615085`** on
`claude/runner-executor-verification-utzgrh`.

## 1. Correction to the previous version of this document

The version at `f1d7ada14` put the worst case for one window at **$0.55**.
That was wrong. It assumed a matched pair costs at most the 0.99 pair
ceiling. The ceiling bounds the two prices *quoted together in one step*. An
UP leg filled at 0.96 before a reversal and a DOWN leg filled at 0.96 after
it are a matched pair costing 1.92, with nothing unmatched and every quote
under the ceiling when it was made.

Measured through the real executor against an adversary that fills the dear
side and then reverses the market, at the profile that version proposed:
**$1.108 lost in one window.**

## 2. Maximum loss and exposure, proven

A window settles UP or DOWN, so what it can lose is

    total cost − min(UP shares, DOWN shares)

**New gate:** `L5_BTC5M_STRADDLE_MAX_WINDOW_LOSS_USD` (default off). Before
any leg is placed, that figure is computed over every subset of what could
still fill — resting orders including any with a cancel in flight, orders
queued in the same step, and the leg itself — and the leg is refused
`WINDOW_LOSS_CEILING` if any subset exceeds the cap. The check is exhaustive
because a light-side fill can lower the loss; the figure is convex in fill
fractions, so partial fills cannot exceed full ones. It can only add a
refusal.

| | without the gate | with the gate at $0.45 |
|---|---|---|
| provable bound on window loss | the per-side cap, $1.20 | $0.45 |
| dear-side-then-reverse adversary | $1.108 | $0.372 |
| 40 random adversaries, every step | — | never above $0.45 |
| gross (filled cost + resting) | ≤ $2.40 | ≤ $2.40, observed $0.77 |

The random adversaries walk the book across the whole price range, fill each
resting order in full, in part or not at all, and arm fills to beat cancels.
The bound is checked at each of 130 steps on realized inventory and on
everything still working, and equals the executor's own settlement
arithmetic.

**Each hazard named in the request:**

* **Adverse fill ordering** — the subset maximisation is over fills in any
  order; covered by both adversaries.
* **Cancel/fill races** — an order with a cancel in flight still counts as
  fillable. Separately, the executor refuses every placement in a step where
  a cancel was found filled (`DECISION_STALE_FILL_DURING_CANCEL`).
* **Fees** — every order is post-only; maker fee and maker rebate are both
  zero at Tier 1 (`src/pmus_fees.py`, pinned by a test that fails if that
  changes). No taker order exists in this mode.
* **Settlement** — $1.00 per winning share, $0 per losing share, no
  settlement fee. Not covered: a market the venue voids or resolves
  irregularly. The bound then is total cost, ≤ $2.40.

**Not proven here:** anything about the real venue. All of the above is
against a venue double. The bound assumes the ledger's fills are the venue's
fills, which the existing reconciliation is there to enforce.

Tests: `tests/test_l5_btc5m_bonereaper_mode.py`, 82 tests. Mutations: gate
off → 13 fail; greedy instead of exhaustive → 9; ignore resting orders → 1;
ignore same-step orders → 1; six mutations of the requote logic → ≥1 each.

## 3. Configuration propagation

| hop | verified by |
|---|---|
| env var → clamping reader | unit tests incl. over-ask, negative, unreadable, NaN |
| reader → `effective_params` (what `step` hands `decide`) | test; also that both are ignored outside STRADDLE mode |
| runner uses the same `Btc5mExecutor.step` | test |
| boot log | `L5_BTC5M_EFFECTIVE_CONFIG`, emitted before arming, now reports requote ticks, loss ceiling, book-relative, pair ceiling, join ticks and clip from the same effective params |
| live image | `deploy/l5_btc5m_live/Dockerfile` copies every module involved; test fails if the controller imports one it lacks |
| restart | leg-vs-completion and inventory are read from the ledger; a rebuilt executor requotes correctly and stays inside the loss ceiling |

Not verified: the running service. It is on `3f50258`, which has neither
setting. Verification on production means reading `L5_BTC5M_EFFECTIVE_CONFIG`
from the boot log after a disarmed deploy.

## 4. Can a behaviour-only probe be authorized? No. Blocker:

**The entry-EV gate.** Every quote must be admitted by it. For a quote at the
book (0.50 / 0.48) with the fixed 6¢ adverse-selection charge it admits only
when `L5_BTC5M_STRADDLE_P_PAIR_NATURAL` is **≥ 0.751** (≥ 0.763 with the
positive minimum EV the campaign preflight requires), and the owner must also
attest `L5_BTC5M_STRADDLE_P_BASIS=CONDITIONAL_ON_FILL`.

What exists:

| source | pair rate |
|---|---|
| the target's own matched share | 69.7% (and it loses money) |
| our settled BTC15m record | 66.9% |
| measured `p_pair_natural`, 503 shadow windows at 0.08 rungs | 7.0% |
| measured at book-relative prices on PMUS | none |

Setting 0.76 would be inventing a number, and the basis attestation would be
false. The existing discovery token
(`L5_BTC5M_STRADDLE_FILL_MODEL=UNMEASURED_DISCOVERY_AUTHORIZED`) does not
help: it declares the *fill model* unmeasured, and the gate still needs the
rate. There is no campaign class today that admits a quote on a loss budget
instead of on EV.

So the existing bounded-campaign machinery cannot carry this probe without
either a fabricated rate or a weakened gate. **I have built neither.**

What would change the answer, each an owner decision:

1. **A measured rate.** A conditional-on-fill pair rate for book-relative
   quotes on PMUS at or above 0.763. The recorded-book replay already on file
   says this family is negative at every distance, so this is unlikely.
2. **A separate campaign class**, e.g. "behaviour probe, EV unproven", where
   the approval document itself replaces the EV admission with the hard loss
   ceiling of §2, for a named window only. That is a deliberate exception to
   the normal economic gate. It needs an explicit instruction to build it,
   and a second explicit approval to use it.

**Update, same day:** the owner chose option 2 for engineering only. The
campaign class is built at `7182a43` and described in
`analysis/l5/btc5m/BEHAVIOUR_PROBE.md`. It is not deployed, armed or used,
and the deployment SHA for any future probe is `7182a43`, not the `cab8e12`
named below.

## 5. Packet for owner review (conditional on §4 being resolved)

| item | value |
|---|---|
| venue / lane | PMUS, `l5-btc5m-live` only |
| window | **2026-11-20 15:00:00Z – 15:05:00Z**, `cpc-btc-updown-5m-2026-11-20-1500z`; `L5_BTC5M_NOT_AFTER=2026-11-20T15:05:00Z`. Placeholder: any later single window can be substituted |
| deployment SHA | `cab8e12894b5c0e8f43d9ce5ce412b23fc615085` |
| orders | post-only maker BUY; no taker, no completion, no repricing |
| clip | $0.10 a side |
| per-side cap | $1.20 |
| unmatched cap | $0.45 |
| **window loss ceiling** | **$0.45** (`L5_BTC5M_STRADDLE_MAX_WINDOW_LOSS_USD`) |
| pair ceiling / requote | 0.99 / 2 ticks |
| cash ceiling | $2.40 gross, inside the $2.70 in force |
| exposure ceiling | `L5_ACCOUNT_MAX_EXPOSURE_USD=3.00`, unchanged |
| daily-loss ceiling for the probe | approval document `max_daily_loss_usd: 0.50`; `max_windows: 1`; `max_total_exposure_usd: 2.40` |
| account-wide backstop | $50 daily halt, owner-approved, unchanged; not this probe's budget |
| live orders | 2 working, limit 6 |

**Watchdog, read 2026-11-19 18:48Z** (`l5-halt-watchdog`, commit `faebeca`):
sweeping every 20s, not halted, cancel enabled, 0 open orders, registry
`l5-btc5m-live:8090` OK. Two things to know:

* It latches the account-wide halt on any order older than **300s**. A quote
  kept unmoved from t=2s is ~298s old at the window end. The executor cancels
  at 300s, so this is inside the limit by about two seconds. If that margin
  is too thin, stop new entries earlier; that is a setting, not built here.
* Its managed-lane read of `l5-pmus-live:8090` is `UNREACHABLE` (the known
  wrong-port entry). It does not involve this lane, but the campaign
  preflight should be run and must read READY before arming.

**Current production:** `l5-btc5m-live` on `3f50258`, `can_submit: false`
(`ENVELOPE_EXPIRED`); `l5-pmus-live` on `c9f9f87`, not armed, halted, 0 live
orders.

**Sequence, each step needing its own approval:**

1. Deploy `cab8e12` to `l5-btc5m-live`, disarmed. Read
   `L5_BTC5M_EFFECTIVE_CONFIG` and confirm every row of the table above.
2. Run `l5_btc5m_campaign_preflight`; require READY.
3. Owner places the approval document and arm token for the one window.
4. Window runs. `restartPolicyType: NEVER`; the lane does not relaunch.
5. Settlement audit from venue cashflows. P&L reported as settled only.

**Stop conditions:** any reconciliation mismatch, any order not post-only,
more than one working leg a side, any halt, `WINDOW_LOSS_CEILING` reached.

**Rollback:** Railway rollback of `l5-btc5m-live` to deployment
`ccc80612-89f6-48df-bd09-137cea517239` (`3f50258`, `canRollback: true`).
Disarm at any moment by removing `L5_BTC5M_LIVE` or setting `L5_LIVE_HALT`.
Both new settings default off, so unsetting them restores prior behaviour on
the new SHA without a redeploy. A rollback does not flatten filled
inventory; that settles with the window.

**What one window can and cannot show.** It can show both sides quoted
within seconds, requotes by exact id, fills booked, and the ledger
reconciling to the venue. It yields a handful of fills and cannot measure the
0.763 rate, which needs hundreds of legs. Expect a small loss, at most $0.45.

## 6. What the target does, for reference

Measured from 18,715 fills in 304 windows
(`python3 analysis/traders/bonereaper/behaviour_btc5m.py`): 50.5 fills a
window; 97.4% of windows filled on both sides; buys across the whole price
range (median 0.46–0.47, 34.9% of dollars at 0.90+); clip size flat at every
level of imbalance; 57.1% of fills add to the already-heavy side; 27.8% of
final holdings unmatched; 69.7% of shares end matched.

Not copied, by the controls #247 sets: taker orders (about half his dollars),
pairs at or above 1.00 (his volume-weighted pair cost is 1.0389), 0.90+ legs
without the EV gate, uncapped unmatched inventory. Those are where his
−$9,727 on these windows comes from.
