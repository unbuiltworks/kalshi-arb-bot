# READY_FOR_A+B_Q1_APPROVAL — two sequential bounded q=1 probes

**NOTHING ARMED. NOTHING SUBMITTED.** `AUTHORIZED_BUNDLE is None`;
`arming.EXPECTED_AUTH_ID == "NO-AUTHORIZATION-IN-FORCE"`, an identifier no
environment value matches, so the transport cannot arm even if every flag were
set by accident.

**Both configurations are frozen NOW, before any new live order.** Probe B's
identity was fixed and hashed before Probe A runs, so B cannot be tuned after
seeing A. That is the point of declaring them together.

## Identity

| | value |
|---|---|
| branch | `claude/gemini-btc-economic-viability-h3u7zl` |
| commit at manifest | `364c79de2c76` |
| currently deployed (read-only shadow + P10) | `e6b7ef97`, deployment `d3b2cf90`, SUCCESS |
| **BUNDLE_SHA to authorize** | **`47ff16764d817a0d50cad75ba92fd8d589b782651a7ea45cee237a1817fb8706`** |
| AUTHORIZED_BUNDLE | `None` — disarmed |

Bundle membership grew by two, both because they now run on the live path:
`fill_quality.py` (the economics a probe is judged on) and `ab_arms.py` (the
rule that decides whether an order is a maker order at all).

## Probe A — BTC15m, current placement

| field | value |
|---|---|
| spec | `btc15m_q1_cap4_w60_880_c2` |
| **spec_sha** | **`ba64b54c8711166b9515007e6835e8190dc8bfddfbae2a1ba72c4e7798cefcb4`** |
| envelope | `GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_CAP4_V1` (`3aed2098…`) |
| quote interval | TTE **[60, 880]** |
| entry band | 840:880 · q = **1** · placement **None (frozen)** |

**Unchanged from the configuration that last traded (EP016 / COHORT-1).** Its
reviewed hashes are asserted by test to be exactly where they were:
`223b81ed…`, `ba64b54c…`, `7fda21b1…`.

## Probe B — BTC5m, maker-improve placement

| field | value |
|---|---|
| spec | `btc5m_q1_improve` |
| **spec_sha** | **`bfe7ba23199c544d30f25cd1c730d92e62d0bea534274b1c00feff961242a650`** |
| envelope | `GEMINI_L5_LIVE_ENVELOPE_Q1_V1` (CAP-**2**) |
| quote interval | lane default · entry band 240:280 · q = **1** |
| placement | **`maker_improve`** |

A test asserts B is A-for-BTC5m **plus exactly one field**: the two invocation
fingerprints are equal after removing `spec_id` and `placement`.

## Exact live-path code difference for Probe B

`ws_continuous.Driver._step`, between the q check and `reconcile_surface`:

```python
acts = turn.get("actions") or []
if self._placement == "maker_improve" and acts:
    from src.gemini_execution import ab_arms as AB
    book = _improve_book(st)          # Up->yes, Down->no, bid/ask
    acts = AB.apply_A(acts, book)     # one tick better where that stays maker
    bad = _crossing(acts, book)       # INDEPENDENT re-check
    if bad is not None:
        self.stop(DO_NOT_TRADE, "...refused rather than sent")
        return {...}                  # window stops; nothing is sent
    turn = {**turn, "actions": acts}
```

`placement` is a declared, **hashed** field on `LiveProbeSpec`. There is no
module default and no environment variable; the `Driver` refuses any value
outside `(None, "maker_improve")` at construction. Side, size, action and
classification are untouched — only the price the order carries.

## Proof it cannot cross or take

Three independent layers, all tested (18 tests in `test_probe_b_placement.py`):

1. **The rule.** `maker_only_improve` computes `p + 0.01` and returns **`None`
   whenever `imp >= ask`** — so the price is left *unchanged*, never raised to
   or through the ask. Tested exhaustively across the whole 0.01…0.99 tick grid
   on both legs: every returned improvement is strictly below that leg's ask.
2. **An unreadable ask yields no improvement.** `maker_only_improve` returns
   `None` when the ask is missing.
3. **An independent live re-check.** `_crossing()` re-reads the book and
   compares each outgoing price to its own ask; a price at or through the ask
   **stops the window instead of sending**. An unreadable ask is reported as a
   violation, not waved through — "we could not tell" and "it is safe" are
   different answers. A test asserts `_crossing` never calls the rule it
   checks, so a defect in the improvement cannot also disable its guard.

## Exposure, windows and duration

| | Probe A (BTC15m) | Probe B (BTC5m) |
|---|---|---|
| max committed per market | **$4.00** | **$2.50** |
| max residual per market | $4.00 | $2.00 |
| inventory cap | 4 units (CAP-4) | 2 units (CAP-2) |
| working orders / concurrent markets | 2 / **1** | 2 / **1** |
| actions per window | 12 | 12 |
| window length · one market per window | 15 min | 5 min |
| **windows for ≥2 hours** | **10** (2h30m) | **28** (2h20m) |
| ledger chain required | 10 × 12 = 120 slots | 28 × 12 = 336 slots |
| cumulative cash-out stop | **$10.00** | **$10.00** |
| **maximum loss** | **$10.00**, ≤$4.00 in any one market | **$10.00**, ≤$2.50 in any one market |

**Maximum loss across both probes run sequentially: $20.00 of cash out**, and
each probe stops independently — they do not share a ledger, a stop or a
result. `max_concurrent_markets = 1` in both envelopes makes simultaneous
operation structurally impossible, not merely discouraged.

## Stop conditions (identical for both, on their own counters)

1. `STOPPED_AGGREGATE_LOSS` — cumulative `window_loss_usd` ≥ $10.00
2. envelope refusal — imbalance, per-leg, committed or residual past the bound
3. mutation budget — 12 attempts per window, one ledger per window
4. end of window — no OPEN/EXTEND below TTE 60s; withdraw all below TTE 30s
5. deadman — market state older than 20s withdraws and stops
6. bounded UNREADABLE hold — 5 consecutive unreadable/crossed cycles
7. fail-closed rollover — an order still listed, or an unreadable order list or
   position, STOPS the cohort rather than skipping a contract
8. **Probe B only** — a placement result at or through the ask, or an
   unreadable ask, stops the window before anything is sent
9. authorization expiry — `GEMINI_CANARY_AUTH_EXPIRES` mandatory
10. a completed or crashed probe **does not relaunch itself**

## Accounting — identical in both probes, apples to apples

After **every** fill, `fill_quality.quality()` records: matched inventory ·
residual inventory (signed) · average cost each side · effective matched pair
cost · **executable complement ask** (the ask we would have to pay, never the
mid) · **projected pair cost if completed now** · `pair_locked_below_1` ·
**residual basis at risk** (basis, not $1 face) · **next controller action** ·
fee, or `NOT YET RECONCILED`.

`cap_counterfactual()` records every offered action whether or not the envelope
binds — so the denominator is visible. It relaxes nothing.

Lifecycle `SUBMITTED → FILLED → SETTLED → CASH_RECONCILED`; **REALIZED is
refused as a stage.**

`settlement_economics()` computes, **separately from and never feeding the
stop**:

```
net_pre_rebate = settlement_payout + sell_proceeds − buy_spend − fees_actual
net_with_rebate = net_pre_rebate + rebates_paid          (reported, not selecting)
```

`selection_basis` is hard-coded to `net_pre_rebate_usd`. A test proves a rebate
cannot rescue a losing configuration: spend 1.00, payout 0.90, rebate 0.50 →
pre-rebate **−0.10** (loses) while with-rebate would read +0.40. Only rebates
the venue marks **PAID** are counted; fees come from the venue's own
`fee_amount`, never the published formula.

Checked against EP016: spend 6.86, payout 8.00 → **net_pre_rebate +1.14**,
beside its `cash_out_stop_measure 10.63`. Both numbers are reported; neither
replaces the other.

## Rollback

```bash
unset GEMINI_CANARY_LIVE                      # 1. immediate, no deploy
sed -i 's/^EXPECTED_AUTH_ID = .*/EXPECTED_AUTH_ID = "NO-AUTHORIZATION-IN-FORCE"/' \
    src/gemini_execution/arming.py            # 2. burn the identifier
sed -i 's/^AUTHORIZED_BUNDLE = .*/AUTHORIZED_BUNDLE = None/' \
    src/gemini_execution/bundle.py            # 3. break the bundle
python3 -m src.gemini_execution.run_continuous --cleanup-only   # 4. cancel resting
python3 -c "import sys;sys.path.insert(0,'.');\
from src.gemini_execution import arming,bundle;\
print(arming.state()); print(bundle.state()['authorized'])"      # 5. verify
```

## Carried unfixed, and declared

1. **`window_loss_usd` is a turnover measure, not a loss measure.** Kept as-is:
   it stops sooner than the economics would. Expect early stops in both probes.
2. **`ledger_for` returns the first UNSPENT ledger**, so a window can inherit a
   partly-spent one — in EP016 two of six windows got 4 and 5 slots instead of
   12. Not fixed here because fixing it *increases* activity per window.

## What approval authorizes

Probe A, then Probe B, **sequentially**, under the bounds above, with separate
ledgers, separate stops and separate results. On approval the remaining steps
are: issue an `EXPECTED_AUTH_ID`, set `AUTHORIZED_BUNDLE` to `47ff1676…`, grant
the two ledger chains, and dispatch A. **None of that is done.**

P10 collection continues independently and gates nothing here: it governs
historical conclusions only, and these probes produce their own forward,
venue-confirmed evidence.
