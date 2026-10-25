# Taker-at-entry: timestamp-causal replay on independent historical windows

**Result: taker-at-entry is refuted. Robustly, on every split and configuration.
No policy beats the incumbent out-of-sample. The evidence does not justify a
bounded live test — it justifies not building it.**

Nothing deployed. The wrong-side fix is preserved and undeployed. No taker path
exists in the engine. No capital or infrastructure changed.

---

## 0. Reconciliation: −$1.4584 against the −$1.3328 day ledger

**Yes — the previous campaign's +$0.1256 explains the difference exactly, and
every link verifies against the venue's own `realized_today_after` field.**

| | |
|---|---|
| start-of-day `all_time` | −11.62130 |
| campaign 1 (0030z–0145z, 6 windows) | **+0.1256** |
| campaign 2 (1245z–1400z, 6 traded) | **−1.4584** |
| sum | **−1.3328** |
| logged `utc_day_realized_usd` | **−1.332800** |
| end `all_time` | −12.95410 (logged −12.95410) |

All 11 consecutive links reproduce the logged value to the cent. There is no
unattributed amount and no gap.

---

## 1. Why the 453-window shadow dataset could not be used

**It is not reachable from this container.** The shadow controller's per-window
detail lives in `/data/l5-live-20261012z/shadow_controller_argmax.json` on the
Railway volume; its log emission is the COMPACT report, which states
`per_window_omitted: 384`. `L5_SHADOW_DECISION` rows carry bucketed state
(`p55-75c|tte>10m|flat`), inventory and tte — but **no book depth**, so a taker
entry cannot be priced from them. I did not substitute a proxy and call it the
453-window set.

To unblock it later: mirror that state file to object storage, or emit per-window
rows with the touch and its depth.

## 2. What I used instead

An **independent historical dataset** from the existing collectors:
`data/policy_d_port/` + `data/policy_d_port_ext/` — 63 tapes, 2026-08-30 →
2026-09-01, eight weeks before the live windows.

- **130 windows** with provable settlement: 48 KXBTC15M, 50 KXETH15M, 14 SOL,
  15 XRP, 3 DOGE. Settlement 68 NO / 62 YES.
- Settlement established two independent ways — a terminal print inside 2¢ of a
  boundary (53 windows), and strike versus the `ref_lag` reference
  `settlement_value` (77 windows). **On the 47 windows both methods resolve they
  agree 47/47.** 33 windows whose outcome could not be established were dropped
  rather than guessed.
- Median 46 book snapshots and 2,011 aggressor-tagged prints per window.

**Train / test split: tuned on ETH/SOL/XRP/DOGE (82 windows), evaluated on
KXBTC15M (48 windows) — the series we actually trade.** Parameters were selected
on train only; every number below is held-out BTC15m.

## 3. The 2×2 — separating execution type from entry selection

A 2×2 is needed to separate the two effects, so I added a fourth arm:

| | cheapest-first selection (our engine) | L5-band selection |
|---|---|---|
| **maker-open** | **A** baseline | **D** |
| **taker-open** | **B** | **C** |

`A→B` and `D→C` isolate **execution type**. `A→D` and `B→C` isolate **entry
selection**. Policy C/D's selection is derived from **L5's own corpus, external
to these windows**: his 321 OPEN-from-flat actions have median own price **0.59**
and central-80% band **0.20–0.83**, modal 0.5–0.8 — **he opens the favourite,
while our engine opens cheapest-first.**

### Held-out BTC15m, shared configuration (no policy gets a tuning advantage)

| policy | entered | clean | strand | pair P&L | residual P&L | **net** | fees | adverse |
|---|---|---|---|---|---|---|---|---|
| **A** maker/cheapest | 25/48 | 19 | 6 | +0.5237 | −1.6686 | **−1.5849** | 0.44 | 0.477 |
| **B** TAKER/cheapest | 47/48 | 41 | 6 | +1.1389 | −3.3968 | **−4.3179** | 2.06 | 0.511 |
| **D** maker/L5 band | 31/48 | 28 | 3 | +1.0132 | −2.3667 | **−1.9435** | 0.59 | 0.458 |
| **C** TAKER/L5 band | 43/48 | 41 | 2 | +1.0637 | −1.5557 | **−2.4520** | 1.96 | 0.452 |

```
                    cheapest-first     L5 band      SELECTION effect
  maker-open          -1.5849         -1.9435         -0.3586
  TAKER-open          -4.3179         -2.4520         +1.8660
  EXECUTION effect    -2.7331         -0.5085
```

### Paired per-window tests (the right test at this sample size)

**Execution effect — maker-open → taker-open:**

| at selection | n | mean | SE | t | worse/better | sign p |
|---|---|---|---|---|---|---|
| cheapest-first | 47 | **−0.0582** | 0.0276 | −2.11 | **44 / 3** | **<0.0001** |
| L5 band | 43 | −0.0118 | 0.0333 | −0.36 | **41 / 2** | **<0.0001** |

**85 of 90 paired windows are worse under taker-open.** The direction is near
universal even where the mean is small.

**Selection effect — cheapest-first → L5 band:**

| under execution | n | mean | SE | t | worse/better | sign p |
|---|---|---|---|---|---|---|
| maker-open | 44 | −0.0081 | 0.0369 | −0.22 | 9 / **35** | 0.0001 |
| taker-open | 47 | +0.0397 | 0.0409 | +0.97 | 13 / **34** | 0.0031 |

**Most windows improve, but the mean does not.** L5-band selection raises the
median window and increases tail risk. It is a candidate, not a conclusion.

## 4. Why taker-open loses even though it works as designed

It does exactly what was hypothesised — **and still loses.**

| | A maker | B taker |
|---|---|---|
| entry rate | 52% | **98%** |
| strand rate | 24% | **13%** |
| clean rate | 76% | **87%** |
| fees | $0.44 | **$2.06** |
| **net** | **−1.5849** | **−4.3179** |

Three mechanisms, all adverse:

1. **Fees: 4.7×.** $2.06 against $0.44 on a one-tick book.
2. **The spread, surrendered on every entry** — ~1¢/unit over ~57 matched units.
3. **It destroys the free option of not filling.** A participates in 52% of
   windows; B in 98%. A window the maker never enters costs nothing. **Our low
   fill rate is partly protective, not purely a defect** — which inverts how I
   characterised it in my last two messages.

Fees plus spread (~$2.19) account for most of the $2.73 gap.

**And L5-band selection cuts stranding further and more cheaply than crossing
does: 10% strand at $0.59 of fees (D) versus 13% at $2.06 (B).** If the goal is
fewer strands, leg choice is the cheaper instrument.

## 5. Confirmed out-of-sample: pairs earn, residuals destroy

**Pair P&L is positive for all four policies (+0.52 to +1.14); residual P&L is
negative for all four (−1.56 to −3.40).** Identical shape to the live campaign
(+$2.15 pair / −$3.48 residual) on 48 independent BTC15m windows from a
different month. That finding replicates.

**But so does the loss.** The baseline is **−$0.0330 per eligible window** here
against **−$0.111** live. Same sign. **Our live result was not bad luck.** The
positive pair economics genuinely fail to cover residual losses on this market.

## 6. Limitations — two of them matter

- **Latency is under-resolved, and this is the real weakness.** Net P&L is
  identical at λ = 0.5s, 1.0s and 2.0s, because the tape holds ~1 snapshot per
  20s, so λ in that range almost never crosses a frame boundary. **Taker
  slippage is therefore understated** (measured mean +1.1¢, max +18¢). That bias
  flatters the taker arm, so the conclusion that taker-open is worse is
  *conservative* — but a λ-resolved tape is required before any taker claim in
  the other direction.
- **Top-of-book only.** Level-2+ depth is absent; taker size is clamped to
  level-1 depth (~3,600 contracts against our ~1.3, so it rarely binds).
- Different assets and month; 48 BTC15m windows, 25–47 traded per policy.
- `BASE` and `CONSERVATIVE` queue scenarios differ by under $0.12 — the queue
  assumption is not driving any of this.
- The maker rebate (θ = −0.0125) is reported separately and **never netted into
  P&L**, per the operating model.

## 7. The strongest executable policy, and the live-test question

**Strongest: A — the existing maker-open / maker-complete engine, unchanged.**
Nothing beat it robustly. D wins at its own train-selected configuration
(−0.2020) but loses at the shared one (−1.9435); that config-sensitivity is
disqualifying on 48 windows.

**Does the evidence justify another bounded live test? No — not of taker entry,
and not yet of L5-band selection.**

- **Taker-at-entry: do not build it.** 85 of 90 paired windows worse. This is
  the clearest negative result in this program, and it retires the
  recommendation I led with two messages ago.
- **L5-band selection: promising, not ready.** 35 of 44 maker windows improve
  (p=0.0001) and the strand rate falls 24% → 10%, but the mean is negative and
  the result moves with configuration. It needs the λ-resolved replay and more
  windows before it is worth capital.
- **Spending live capital now would test a policy that is negative out-of-sample
  on the market we trade.**

## 8. What is actually worth doing next, at zero capital

1. **A λ-resolved tape.** Every conclusion about crossing is limited by 20s book
   sampling. The collector already exists; it needs per-event book capture on
   BTC15m. This is the single highest-value piece of missing infrastructure
   — and it is a collector change, not new infrastructure.
2. **Mirror the shadow state file** so the 453-window set becomes usable.
3. **Re-test the completion-cap change** (0.96 → 0.98 fee-inclusive, from the
   previous memo) on these 48 held-out BTC windows. It is the only candidate
   that has not yet been falsified, and it needs no taker entry — only taker
   completion of inventory already held.
