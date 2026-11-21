# FINAL — READY_FOR_A+B_Q1_APPROVAL, shortened to ~2 hours

**NOTHING ARMED.** `AUTHORIZED_BUNDLE is None`;
`arming.EXPECTED_AUTH_ID == "NO-AUTHORIZATION-IN-FORCE"`.

## Two corrections to the previous manifest — both about safety behaviour

I described two defects as "carried into this probe, unfixed". **Both are
already fixed in the tree you are being asked to authorize.** I was describing
EP016's post-mortem, not the current code. Corrected:

1. **The aggregate stop is no longer a turnover measure.** It now reads
   `cumulative_economic_loss_usd` and its own message says:
   *"cumulative ECONOMIC loss … Settlement-inclusive over SETTLED windows only
   (spend + confirmed fees − payout − credited rebates); gross turnover is …
   and is NOT what this measures."* The behaviour that halted COHORT-1 at 10.63
   while it was up +1.14 does not exist here.
   There is also a **second, tighter bound** available — a peak-to-trough
   `max_drawdown_usd`, checked **before** the loss cap — currently **disabled
   at 0**, which is how every cohort so far has run. I have **not** enabled it;
   say the word and it becomes the tighter of the two stops.
2. **`ledger_for` no longer hands a market a partially spent ledger.** It now
   requires `consumed == 0` — a whole unused ledger per market — and raises
   `IndexError` when none remains. The EP016 behaviour where two of six windows
   got 4 and 5 slots instead of 12 is gone.

The second correction is what makes the shortening below structural rather than
advisory.

## The change: window counts only

**Window count is granted budget, not a behavioural parameter.** Each market is
given one whole ledger, and when the chain has no unused ledger left the cohort
stops. So the window cap is set by **how many ledgers are granted** — no code
change, no operator flag, no behavioural parameter touched.

| | Probe A (BTC15m) | Probe B (BTC5m) |
|---|---|---|
| windows | **4** (was 10) | **12** (was 28) |
| ledger chain to grant | **4** ledgers × 12 slots = 48 | **12** ledgers × 12 slots = 144 |
| approximate duration | ~1h00m | ~1h00m |
| **combined** | | **~2h00m, sequential** |

**Every hash is unchanged, verified after the change:**

| | value | moved? |
|---|---|---|
| A `spec_sha` | `ba64b54c8711166b9515007e6835e8190dc8bfddfbae2a1ba72c4e7798cefcb4` | **no** |
| B `spec_sha` | `bfe7ba23199c544d30f25cd1c730d92e62d0bea534274b1c00feff961242a650` | **no** |
| **BUNDLE_SHA** | **`47ff16764d817a0d50cad75ba92fd8d589b782651a7ea45cee237a1817fb8706`** | **no** |

Placement, entry bands, CAPs, controller, pricing, rung count, rest floor,
settlement guards, q: **all untouched.**

## Recalculated maximum exposure — and it is larger than I said before

My previous "$20 maximum" was wrong: it assumed the $10 stop caps total spend.
It does not, for two reasons — the stop sums **SETTLED windows only**, so it
lags by roughly a window; and it is checked **between** windows, so a window
already under way can still spend its full per-market allowance.

The number that cannot be exceeded under any circumstances is the structural
one: windows × per-market committed.

| | per market | windows | **hard ceiling** |
|---|---|---|---|
| Probe A | $4.00 | 4 | **$16.00** |
| Probe B | $2.50 | 12 | **$30.00** |
| **combined, sequential** | | 16 | **$46.00** |

* **Maximum gross cash out: $46.00.**
* **Maximum loss: $46.00**, reached only if every contract resolves against us
  and nothing pays out — every matched pair pays $1.00, so a realistic worst
  case is far smaller.
* **Expected halt** is much earlier: the $10.00 economic stop fires per probe
  once settled windows show a $10 economic loss, so each probe realistically
  halts around **$10–16 (A)** and **$10–15 (B)**.

Per-market limits and the conservative stop behaviour are unchanged, as
instructed. If $46.00 is more than you want authorized, the levers that do not
touch behaviour are: fewer windows, or enabling the drawdown bound above.

## Frozen configuration (unchanged from the approved manifest)

| | **A — BTC15m** | **B — BTC5m** |
|---|---|---|
| spec | `btc15m_q1_cap4_w60_880_c2` | `btc5m_q1_improve` |
| envelope | CAP-4 `…Q1_BTC15M_CAP4_V1` | CAP-2 `…Q1_V1` |
| interval / entry | TTE [60,880] / 840:880 | lane default / 240:280 |
| q / placement | 1 / **None** | 1 / **`maker_improve`** |
| committed · residual | $4.00 · $4.00 | $2.50 · $2.00 |
| inventory cap | 4 units | 2 units |
| working orders · markets | 2 · **1** | 2 · **1** |
| actions per window | 12 | 12 |

Sequential by construction: `max_concurrent_markets = 1` in both envelopes.
Separate ledgers, separate stops, separate results.

## Stop conditions (per probe, on its own counters)

1. **`STOPPED_WINDOWS_EXHAUSTED`** — the granted chain has no unused ledger
2. `STOPPED_AGGREGATE_LOSS` — settlement-inclusive economic loss ≥ $10.00
3. `DRAWDOWN` — available, currently disabled at 0
4. envelope refusal — imbalance, per-leg, committed or residual past bound
5. mutation budget — 12 attempts per window, one whole ledger per market
6. end of window — no OPEN/EXTEND below TTE 60s; withdraw all below 30s
7. deadman — market state older than 20s
8. bounded UNREADABLE hold — 5 consecutive unreadable/crossed cycles
9. fail-closed rollover — order still listed, or unreadable orders/position
10. **Probe B only** — a price at or through the ask, or an unreadable ask,
    stops the window before anything is sent
11. authorization expiry — `GEMINI_CANARY_AUTH_EXPIRES` mandatory
12. no automatic relaunch after a completed or crashed probe

## The authorization identifier required to arm

    OWNER-2026-09-23-GEMINI-Q1-AB-1

**Verified: installing it does NOT move `BUNDLE_SHA`.** I set it, recomputed,
and got `47ff1676…` unchanged — the authorization line is normalized out before
hashing, which is exactly why `EXPECTED_AUTH_LINES["arming.py"] = 1` exists.
With the identifier installed and no environment flags, `arming.armed()` is
still `False`. The tree has been restored to `NO-AUTHORIZATION-IN-FORCE`.

To arm, after your explicit approval of this manifest, three independent keys:

```
1. src/gemini_execution/arming.py : EXPECTED_AUTH_ID = "OWNER-2026-09-23-GEMINI-Q1-AB-1"
2. src/gemini_execution/bundle.py : AUTHORIZED_BUNDLE = "47ff16764d817a0d50cad75ba92fd8d589b782651a7ea45cee237a1817fb8706"
3. at run time: GEMINI_CANARY_LIVE=YES
                GEMINI_CANARY_AUTH=OWNER-2026-09-23-GEMINI-Q1-AB-1
                GEMINI_CANARY_AUTH_EXPIRES=<ISO instant; absent is refused>
```

Plus granting the two ledger chains (4 and 12). **None of this is done.**
An authorization is spent on use and never arms again.

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

## What this experiment is for

Not fills. Whether **either frozen configuration produces better pre-rebate
economics while retaining L5-like inventory behaviour** — judged on
`net_pre_rebate_usd` (payout + sells − spend − actual fees), with rebates
reported separately and excluded from selection, and on the trajectory
statistics: same-side run length, EXTEND→COMPLETE transitions, imbalance
crossings, peak depth and terminal residual, scored against L5's own
**duration-matched** behaviour. A configuration that earns more while behaving
nothing like L5 is a negative replication result.

Sixteen windows is a small sample and will not settle expectancy. It is sized
to produce trustworthy per-fill economics, not a verdict.
