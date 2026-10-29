# COHORT-2 — 20 consecutive q=1 BTC15m windows

**Status: GRANTED AND ARMED, 2026-09-22.**
`OWNER-2026-09-22-GEMINI-BTC15M-Q1-CAP4-COHORT-2` — the owner approved this
envelope in full, including both stops. Both halves of the dual key are
installed: the code constants in `ws_continuous`/`arming`/`bundle`, and the
`GEMINI_CANARY_LIVE` + `GEMINI_CANARY_AUTH` pair on the cohort workflow step,
bounded by `GEMINI_CANARY_AUTH_EXPIRES: 2026-09-22T20:00:00Z`.

## What requires a new grant

`OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-COHORT-1` was a **10-window, $10.00**
grant and it is **SPENT**: run 35677907781 bound four contracts and stopped.
A spent authorization never arms again, and 20 windows is a larger envelope
than 10, so this needs a new grant. The arm also cannot be supplied from here:
`GEMINI_CANARY_AUTH` / `GEMINI_CANARY_AUTH_EXPIRES` are repository secrets the
owner sets, and they are never written into the tree.

## The two cohort-level stops — exact definitions

Both are **settlement-inclusive** and computed over **SETTLED windows only**.
Neither can be moved by turnover, and an unresolved market contributes nothing
to either: its risk stays in the per-market committed / residual / exposure
gates inside the driver, where it has always been.

For one settled contract:

```
net_pnl = settlement_payout + confirmed_rebates - fill_spend - confirmed_fees
economic_loss = -net_pnl                    (a PROFITABLE window is negative)
```

### 1. Cumulative economic loss — proposed cap **$8.00**

```
cumulative_economic_loss_usd = sum(economic_loss) over SETTLED windows
stop when  cumulative_economic_loss_usd >= 8.00
```

Checked **before binding the next contract**, so the cap cannot be crossed by
a window that was already running.

**Why $8.00.** A window cannot lose more than the per-market committed bound,
so $8.00 is **two maximum-loss windows** out of twenty. Against the only
evidence we have — the four recoverable COHORT-1 windows, whose worst single
window was **−$0.40** — a cohort losing at that rate every single window
reaches the cap at window 20, i.e. the cap does not truncate a merely
mediocre sample but does stop a genuinely bad one well before the end.
The previous grant was $1.00/window; this is $0.40/window, and tighter.

### 2. Peak-to-trough drawdown — proposed cap **$5.00**

```
equity_t   = running sum of settlement_implied_net over SETTLED windows
drawdown_t = max(equity_0..t) - equity_t
stop when  max_drawdown_usd >= 5.00
```

**Why a second bound at all.** Cumulative loss alone cannot see a cohort that
makes $4.00 and gives all of it back: it reads zero throughout, while the
strategy's recent behaviour is as bad as it has ever been. On the four
COHORT-1 windows this measure reads peak $1.77, max drawdown $0.63.

**Why $5.00.** Looser than the cumulative cap in absolute terms, because it
starts counting from a peak rather than from zero — so it binds on a cohort
that earned its way up and is now bleeding, and does not double up with the
cumulative cap on one that never rose.

Set `--max-drawdown-usd 0` to disable; that is how every cohort before this
one ran, so the bound is purely additive.

## Everything else that bounds this run — unchanged

| bound | value | enforced by |
|---|---|---|
| contract family | BTC15m | spec / envelope |
| action size q | **1** | envelope `action_size` |
| YES / NO / imbalance / same-side-run cap | **4** | CAP-4 envelope |
| committed per market | **$4.00** | envelope `max_committed_usd` |
| residual per market | **$4.00** | envelope `max_residual_usd` |
| working orders | **2** | envelope `max_working_orders` |
| desired rungs per outcome | **1** | frozen controller |
| concurrent markets | **1** | envelope |
| order type | maker / post-only | driver |
| retention floor | 30 s | `WITHDRAW_ALL_TTE_S` |
| quoting interval | TTE **[60, 880]** | `quote_band`, inside `spec_sha` |
| ordinary actions per market | **12** | one whole ledger per contract |
| cohort action budget | **240** = 20 × 12 | the 20-ledger chain |
| safety cleanup | separate budget, **cancel only** | `MAX_CLEANUP_MUTATIONS` |

No midpoint logic, no bid improvement, no extra rungs, no forced fills, no
taker orders, no q increase. The L5 controller is frozen.

## Hashes

| | value | moved? |
|---|---|---|
| `executable_sha` (frozen controller) | `f90b84c91d2e171e6805503e71dd523ecb7380b4d0d6067b0325a76fd73e0903` | **no** |
| `envelope_sha` (CAP-4) | `3aed2098fe0290b7ad8a16cdc053eae7cfcc07b02cbae6b8299eae28da3f6c6b` | **no** |
| `spec_sha` COHORT-1 | `223b81edd6932bce48afbc35b1be88d9898af2849ddd38cf309e2d25e07bd65f` | — |
| `spec_sha` COHORT-2 | `ba64b54c8711166b9515007e6835e8190dc8bfddfbae2a1ba72c4e7798cefcb4` | **yes** |
| `bundle_sha` | `9cf7132a474df098f3647a0d8d2eca3eb16a8429f3710ef2be3406de2a5c833b` | **yes** |

Two hashes moved and both are disclosed rather than explained away:

- **`spec_sha`.** `spec_id` is deliberately inside `invocation_fingerprint`,
  so two *declared* specs never share an identity even when they would behave
  identically. Every other hashed field — family, symbol_family, envelope,
  envelope_id, entry_tte_band, quote_band, action_size, ledger, cohort and the
  whole bounds dict — is byte-identical to COHORT-1's, asserted as a dict
  comparison in `test_cohort2_is_cohort1_with_a_different_ledger_chain`.
  The claim is not "the hash did not move"; it is "nothing but the id moved".
- **`bundle_sha`.** Two members moved: `run_continuous.py`, from
  `a5c07ea824fa…` to `5e7bde43294a…` for the observability sink, and
  `live_probe_spec.py` for the COHORT-2 spec declaration itself. An earlier
  draft of this document quoted `75463e32…`, which was the value BEFORE the
  spec was declared; the correct armed value is `9cf7132a…`. The frozen
  controller `ws_continuous.py` is unchanged at `ecd95b1d16ac…`, which is why
  `executable_sha` did not move. Full diff: an optional `sink` on
  `DecisionObserver`, an `_emit` that runs strictly *after* a row is appended,
  and a `status_sink` parameter threaded to it. No decision, no bound, no
  venue read.

## Observability, and its inertness

`analysis/gemini/cohort_live_status.json` is rewritten atomically after every
decision and every mutation, with an append-only history in
`cohort_live_status.json.jsonl`. Per cycle: window # / symbol / TTE / cycle /
YES bid-ask / NO bid-ask / desired / resting orders / fills / YES-NO
inventory / matched qty + effective pair cost / residual / actions used of 12;
per window: stop reason and settlement-implied net once resolved; per cohort:
cumulative economic loss, drawdown, turnover, payout, fees, rebates.

It cannot change the run: it is fed rows the `DecisionObserver` has already
completed, it makes **no venue read** (asserted from the module's own import
and name set), a sink that raises is swallowed, and
`test_the_window_report_is_identical_with_and_without_a_sink` compares every
decision-relevant field of a window run with and without it.

`realized_net_usd` stays **null** everywhere. Nothing is REALIZED without an
attributable cash movement read back from the account.

## Proposed grant

```
OWNER-2026-09-22-GEMINI-BTC15M-Q1-CAP4-COHORT-2
  spec              btc15m_q1_cap4_w60_880_c2   ba64b54c…
  envelope          GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_CAP4_V1   3aed2098…
  windows           20 consecutive BTC15m contracts, one at a time
  actions           12 per contract, 240 cohort maximum, no inheritance
  cumulative stop   economic loss >= $8.00      settlement-inclusive
  drawdown stop     peak-to-trough >= $5.00     settlement-inclusive
  per market        q=1, CAP-4, $4.00 committed, $4.00 residual, 2 working
  runs unattended   no authorization between windows; no tuning during
  stops at once     mechanical/safety failure, unreadable ledger, refused
                    rollover, position disagreement, terminal venue error
```

Zero-fill windows and ordinary losing windows do **not** stop the cohort on
their own; only the authorized limits above do.

## Readiness as it stands

Static readiness, uncredentialed, on this tree:

```
spec               PASS
family_agrees      PASS
cohort_chain       PASS   20 ledgers, 12 each, 240 total, 0 consumed, all intact
cohort             PASS
authorization      FAIL   REQUIRED_AUTH_ID / AUTHORIZED_SHA / AUTHORIZED_ENVELOPE are None
bundle             FAIL   AUTHORIZED_BUNDLE is None
terms              FAIL   needs the credentials
positions_readable FAIL   needs the credentials
tte_independent    True   mutations_sent 0
```

The four FAILs are exactly the two halves of the arm the owner supplies and
the two reads that need credentials. They clear in the credentialed step once
the grant exists.


## Installed authorization — the exact armed values

```
REQUIRED_AUTH_ID     OWNER-2026-09-22-GEMINI-BTC15M-Q1-CAP4-COHORT-2   ws_continuous.py
EXPECTED_AUTH_ID     OWNER-2026-09-22-GEMINI-BTC15M-Q1-CAP4-COHORT-2   arming.py
GEMINI_CANARY_AUTH   OWNER-2026-09-22-GEMINI-BTC15M-Q1-CAP4-COHORT-2   the cohort step
GEMINI_CANARY_LIVE   YES                                               the cohort step
EXPIRES              2026-09-22T20:00:00Z
AUTHORIZED_SHA       f90b84c91d2e171e6805503e71dd523ecb7380b4d0d6067b0325a76fd73e0903
AUTHORIZED_ENVELOPE  be8b544b35929149c9ecea41e6a2461824426a59debb3a6e5d4e275f9250445d
AUTHORIZED_BUNDLE    9cf7132a474df098f3647a0d8d2eca3eb16a8429f3710ef2be3406de2a5c833b
```

**Installing the grant did not move `executable_sha`.** It is `f90b84c9…`
before and after, because the bundle's detached signature normalizes the
authorization *assignment lines* away before hashing. That only holds for a
single-line assignment: a first attempt wrapped the values across lines with
explanatory comments and moved the hash to `f1829c18…`, which would have
invalidated the authorization being installed. The values are therefore
installed one per line with no added commentary, and the constants' existing
comments are untouched.

The single-window launcher step stays at `NO-AUTHORIZATION-IN-FORCE`. Exactly
one step is armed, asserted by
`test_the_tree_is_armed_for_exactly_one_named_authorization`.

Full suite at the armed SHA: 7839 passed, 27 failed — byte-identical to the
clean-tree baseline.
