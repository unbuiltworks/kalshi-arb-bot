# Real-money COMPLETE-requote campaign — ready for approval

Implementation finished, ported, tested, and the deployment verified. **One decision remains and
it is yours: which risk envelope.** Nothing is armed.

## 1. The defect is fixed — on both branches, as one implementation

The guard already on `feature/l5-transfer-backtest` suppressed a repost at the **identical** price,
on strong measured evidence (301 re-quote transitions in the 2026-09-13/14 cohort of 472 real
orders; **283 = 94.0%** reposted the same price, with runs of 115, 96 and 52). But it read

```python
except Exception:
    nxt = None                 # fail to the old path
if nxt is not None and D(str(nxt)) == px_:
    return None
return f"touch_moved:{touch}"
```

and **`nxt is None` fell through to the cancel.** `nxt` is None both when pricing **raised** and when
**no fee-safe complement exists** — so in both cases the complement was cancelled with nothing able
to replace it. Shipped as the union of that guard and mine:

| state | action |
|---|---|
| no legal price | **keep resting**, naming the cause (`no_legal_replacement` vs `price_failed:<Exc>`) |
| same price | **keep resting** (the measured churn, unchanged) |
| different legal price | cancel and repost at it, recorded as `requote_replacement` |

**I withdrew my own direction test as unsafe.** It held the order whenever the new price was lower.
But the legal complement price *falls* as cost basis rises — at a 0.98 bound, basis 0.30 prices 0.68
and basis 0.70 prices 0.28 — so a lower price means the resting order sits **above** what the bound
sanctions. Holding it would preserve an order that can fill outside the bound.

I also **inverted an existing test** rather than quietly rewriting it:
`test_a_failure_to_price_falls_back_to_the_old_behaviour` required the fall-through this fixes. Its
concern ("an exception must not silently pin an order in place") is real and is answered elsewhere:
`_await_order` rests *"until filled, until the deadline, or until `still_wanted()` names a reason"*,
and `STOP_BEFORE_SETTLEMENT_S` (120 s) plus the market-scoped deadman bound it independently.

**Tests.** 16 new; 3 updated. `claude/l5-defensive-8window-prod`: **59 failed / 13,422 passed / 23
errors** against the baseline's **59 / 13,396 / 23** — zero new failures. `feature/l5-transfer-backtest`
spring suite: **6 failed / 333 passed**, and those 6 are `test_pmus_spring_probe_readiness.py`,
**pre-existing** (verified by stashing the change: 6 failed / 12 passed at baseline).

## 2. Deployment verified — and the code landed without restarting anything

| | |
|---|---|
| `l5-pmus-live` | `python3 -m src.l5_pmus_live_runner`; never imports the spring session; **the flag is inert there** and is not among its 59 variables |
| `pmus-spring-l5-probe` | `python3 -m src.pmus_l5_continuous_session`; **carries `PMUS_SPRING_COMPLETE_REQUOTE`** — the only place the flag is live |
| my push | deployment `8d4e7f56`, status **SKIPPED**, `regions: []`, `snapshotId: null` |
| what it still serves | Oct 14's `6adcc4c4` — **not** my code |

`deploy/pmus_spring_l5/DEPLOY_TRIGGER` is that service's **only** watchPattern and I deliberately
did not touch it. So the fix is on the branch and **a deliberate redeploy is required** to run it.

The probe is also **halted on the `PMUS_SPRING_SHUTDOWN` latch since 2026-10-14T12:07:00Z**, with
1 held market and 0 open orders. Clearing that latch is an owner act.

## 3. A correction in your favour, and the safety problem attached to it

I scored this mechanism three times and the third is the one that counts, because it is the regime
the probe is actually configured for:

| regime | ceiling | legal completions | locked pair P&L |
|---|---|---|---|
| `l5-pmus-live` / `l5_live_execution` | 0.96 | **0 of 15** | — |
| spring default rung, `fee_safe` | 0.98 | **1 of 15** | — |
| **R5FT, `l5_touch` — the configured rung** | **1.00** | **5 of 15**, 19.86 units | **+$0.0782** |

**The mechanism is live and meaningful there, and the locked term is ~0 rather than negative,
because a par pair locks nothing.** My earlier "0 of 15 / economically nil" reading was right for
the services I checked and wrong for the service the flag lives on.

**But the same rung carries `per_market_cap_usd = $30.00`** — 6x the $5 envelope you told me to
preserve and 37.5x the $0.80 opening notional. The full ladder:

| rung | per-market cap | residual | pricing | pair ceiling |
|---|---|---|---|---|
| R1 | $0.05 | 3 | fee_safe | 0.98 |
| R5 | $0.10 | 3 | fee_safe | 0.98 |
| R5F | $7.50 | 6 | **l5_touch** | 0.98 |
| **R5FT (configured)** | **$30.00** | 6 | **l5_touch** | **1.00** |

There is also a latent inconsistency worth knowing: `REARM_RUNG = "R5F"` is asserted at boot against
the active rung id, and the startCommand rewrites it to `R5FT` — but **neither R5F nor R5FT is
defined in the ladder**; both are constructed only when `PMUS_SPRING_RUNG` names them. That boot
assertion is part of why the latch has not cleared.

**No configuration of that service equals $5.** R1 is inside the envelope but `fee_safe`, where the
requote is near-vacuous and the test would prove nothing. R5F is `l5_touch` but $7.50. R5FT is the
mechanism at $30.

## 4. Proposed campaign

Everything below is unchanged from the previous proposal except the envelope line:

| field | value |
|---|---|
| service | `pmus-spring-l5-probe` (existing; the only place the flag is live) |
| series | `cpc-btc-updown-15m-` |
| markets / windows | **1 market, 3 windows**, concurrency 1 |
| **per-market cap** | **the decision below** |
| max actions / session | 4 (R5FT's own bound) |
| residual units | 6 (rung bound), well under the engine's own limits |
| outstanding orders | 1 |
| expiry | **90 minutes**, hard |
| flag | `PMUS_SPRING_COMPLETE_REQUOTE=1` |
| rollback | unset the variable — needs no owner decision and restores prior behaviour at no cost |
| halt | the probe's own daily stop plus the market-scoped deadman and the shutdown latch |
| reporting | per-market venue-confirmed only: submitted order ids, fills with prices, cancels each paired with the replacement named in the tape, settlements, realized net with provenance |
| success criteria | one `VERIFIED_L5_GROSS_SETTLEMENT` row per window; **zero** cancels followed by an empty book; every cancel carries a `requote_replacement`; **no profitability claim either way at n=3** |

## 5. THE DECISION I NEED

**Which envelope?** I recommend **(A)**.

- **(A) Add one bounded rung at $5.00 with `l5_touch` pricing and point the service at it.** Keeps
  your $5 envelope exactly *and* keeps the mechanism live. Cost: one rung added to the ladder, plus
  a startCommand change so the boot assertion matches, plus a deliberate redeploy.
- **(B) Run R5F at $7.50/market.** No new rung; smallest deployment change (drop the `sed` from the
  startCommand). But **$2.50 over** the $5 envelope.
- **(C) Run R5FT as configured, $30.00/market.** The mechanism at its most expressive, and a direct
  contradiction of "preserve the independent $5 envelope". I do not recommend it.

Whichever you pick, launching also needs: the `PMUS_SPRING_SHUTDOWN` latch cleared, a deliberate
redeploy to pick up the guard, and a fresh approval document (`approved`, `nonce`, `series`,
`issued_utc`, `expires_utc`, `max_windows`, `max_total_exposure_usd`, `max_daily_loss_usd`) — the
Oct 25 one expired at 20:30Z.

**No new service, no new branch, no capital beyond the chosen cap, and no monetary cost to prepare.**
