# FINAL — A+B q=1 probes, $18.00 deterministic gross-cash-out envelope

**NOT ARMED. NOTHING SUBMITTED.** Awaiting explicit GO.

## The only change: granted ledger budgets

| | Probe A (BTC15m) | Probe B (BTC5m) |
|---|---|---|
| windows | **2** | **4** |
| ledgers to grant | **2** (24 slots) | **4** (48 slots) |
| per-market committed cap | $4.00 | $2.50 |
| **hard gross-cash-out ceiling** | **$8.00** | **$10.00** |
| window length | 15 min | 5 min |
| bound trading time | ~30 min | ~20 min |

### Combined deterministic gross-cash-out ceiling: **$18.00**

This is **structural, not advisory**. Each market is granted one whole unused
ledger; when the chain has no unused ledger left the cohort stops
(`STOPPED_WINDOWS_EXHAUSTED`). Two ledgers cannot fund three markets, and a
market cannot spend past its envelope's committed cap. `2 × $4.00 + 4 × $2.50 =
$18.00` is therefore the most that can leave the account, reached only if every
window spends its full allowance and the settlement-aware loss stop never fires
first.

**Maximum loss is $18.00**, and only in the case where every contract resolves
against us and nothing pays out. Every matched pair returns $1.00 at
settlement, so a realistic worst case is materially smaller.

## Hashes re-verified AFTER the change — nothing moved

A window budget is a **grant**, not a parameter: it lives in the granted ledger
chain, deliberately outside `spec_sha` (granting a budget is cohort metadata,
not a change of experiment) and outside the bundle (no source file changes).
Recomputed on a clean working tree:

| | value | moved? |
|---|---|---|
| A `spec_sha` | `ba64b54c8711166b9515007e6835e8190dc8bfddfbae2a1ba72c4e7798cefcb4` | **no** |
| B `spec_sha` | `bfe7ba23199c544d30f25cd1c730d92e62d0bea534274b1c00feff961242a650` | **no** |
| **BUNDLE_SHA** | **`47ff16764d817a0d50cad75ba92fd8d589b782651a7ea45cee237a1817fb8706`** | **no** |

`git status` is clean; no file changed to achieve the smaller envelope.

## Frozen behaviour — untouched, as instructed

| | **A — BTC15m** | **B — BTC5m** |
|---|---|---|
| spec | `btc15m_q1_cap4_w60_880_c2` | `btc5m_q1_improve` |
| envelope | CAP-4 `…Q1_BTC15M_CAP4_V1` | CAP-2 `…Q1_V1` |
| quote interval | TTE **[60, 880]** | lane default |
| entry band | 840:880 | 240:280 |
| q / placement | 1 / **None** | 1 / **`maker_improve`** |
| committed · residual | $4.00 · $4.00 | $2.50 · $2.00 |
| inventory cap | 4 units | 2 units |
| working orders · markets | 2 · **1** | 2 · **1** |
| **action budget** | **12 per window** | **12 per window** |
| controller · pricing · rung count · rest floor · settlement guards | unchanged | unchanged |

**Nothing was altered to reduce exposure.** CAPs, entry bands, placement,
controller, pricing and the 12-action budget are exactly as approved; the
exposure fell only because fewer markets are funded.

**The drawdown bound remains DISABLED at 0**, as instructed.
**The settlement-aware economic-loss stop is unchanged** — it reads
`cumulative_economic_loss_usd`, settlement-inclusive over settled windows
(spend + confirmed fees − payout − credited rebates), cap $10.00 per probe.

Sequential by construction: `max_concurrent_markets = 1` in both envelopes.
Separate ledgers, separate stops, separate results.

## Arming prerequisites — all satisfied, none executed

| # | prerequisite | state |
|---|---|---|
| 1 | all 15 bundle members normalize and hash | **OK** |
| 2 | `BUNDLE_SHA` computed | `47ff1676…` |
| 3 | `AUTHORIZED_BUNDLE is None` | **disarmed** |
| 4 | `EXPECTED_AUTH_ID` | `NO-AUTHORIZATION-IN-FORCE` — matches no env value |
| 5 | `arming.armed()` | **False** (`GEMINI_CANARY_LIVE is not YES`) |
| 6 | both specs resolve, placements as declared | `None` / `maker_improve` |
| 7 | `max_concurrent_markets` both | **1** |
| 8 | probe/quality/recorder/audit tests | **68 passed** |
| 9 | working tree | clean |
| 10 | identifier install does not move `BUNDLE_SHA` | **verified**, then reverted |

### To arm, on your GO — three independent keys plus two grants

```
1. src/gemini_execution/arming.py
     EXPECTED_AUTH_ID = "OWNER-2026-09-23-GEMINI-Q1-AB-1"
2. src/gemini_execution/bundle.py
     AUTHORIZED_BUNDLE = "47ff16764d817a0d50cad75ba92fd8d589b782651a7ea45cee237a1817fb8706"
3. run time:
     GEMINI_CANARY_LIVE=YES
     GEMINI_CANARY_AUTH=OWNER-2026-09-23-GEMINI-Q1-AB-1
     GEMINI_CANARY_AUTH_EXPIRES=<ISO instant; absent is REFUSED>
4. grant ledger chain A: 2 ledgers × 12 slots
5. grant ledger chain B: 4 ledgers × 12 slots
```

**None of steps 1–5 has been done.** An authorization is spent on use and never
arms again.

## Rollback

```bash
unset GEMINI_CANARY_LIVE                      # immediate, no deploy
sed -i 's/^EXPECTED_AUTH_ID = .*/EXPECTED_AUTH_ID = "NO-AUTHORIZATION-IN-FORCE"/' \
    src/gemini_execution/arming.py
sed -i 's/^AUTHORIZED_BUNDLE = .*/AUTHORIZED_BUNDLE = None/' \
    src/gemini_execution/bundle.py
python3 -m src.gemini_execution.run_continuous --cleanup-only
python3 -c "import sys;sys.path.insert(0,'.');\
from src.gemini_execution import arming,bundle;\
print(arming.state()); print(bundle.state()['authorized'])"
```

## What six windows can and cannot tell us

Stated plainly so the result is not over-read later.

**It can:** produce trustworthy per-fill economics under the accounting you
specified — executable complement ask, projected pair cost, matched/residual
inventory, residual basis at risk, next controller action — with fees taken
from the venue's own `fee_amount` and rebates excluded from selection. It can
also settle whether `maker_improve` holds its non-crossing guarantee against a
live book, and whether CAP4 alters a desired action at real inventory depth.

**It cannot:** compare A against B. Two windows against four, on different
instruments with different envelopes, is not a comparison — it is two small
observations. Expectancy needs far more than six windows, and any difference
seen here will be within noise.

The objective remains: whether either frozen configuration produces better
**pre-rebate** economics while retaining L5-like inventory behaviour. Trajectory
statistics are scored against L5's own **duration-matched** behaviour, and a
configuration that earns while behaving nothing like L5 is a negative
replication result.
