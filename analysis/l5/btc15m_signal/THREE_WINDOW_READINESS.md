# Three-window paired-only campaign — READY FOR OWNER APPROVAL, not armed

Issue #194, 2026-11-06T02:00Z. **No orders submitted or cancelled. Nothing
armed. `L5_LIVE_HALT=1` left active, `armed=false` left as found. No limit
changed, no exposure expanded, no service added, no SOL executor touched, no
cost incurred. Nothing self-signed.**

Paired-only. The probability model stays disabled: BRTI calibration and
executable profitability are both unproven.

---

## 1. Tests and parity

| | |
|---|---|
| Negative-book-age regression (`test_completion_refuses_future_dated_book_snapshot`) | **PASS** |
| Book-age / staleness subset | **8 passed** |
| Whole `test_l5_two_sided_probe.py` | **85 passed** |
| New `test_l5_mint_campaign_windows.py` | **18 passed** |
| Full suite, owner's two commits alone | 54 failed / **16,844 passed** / 26 errors — **exact parity, 80 ids** |
| New `test_l5_negative_book_age_everywhere.py` | **10 passed** |
| Full suite, final tree | 54 failed / **16,873 passed** / 26 errors — **exact parity, 80 ids, identical set** |

The owner's fixes add `age < 0` to the `BOOK_STALE` guard: `432ff7c3` at
`completion_decision` and `e7177bb5` at `complement_order`. A negative book
age is a future-dated snapshot — future information, never executable
liquidity. **No failures were introduced by either; nothing needed fixing.**

### But the fix was incomplete, and four of the gaps are on the money path

Auditing every site that dates a book, the owner had fixed 2 of 8. Five more
compared only the upper bound, so a negative age passed as the freshest
possible book:

| site | function | path |
|---|---|---|
| `l5_two_sided_probe.py:1430` | `build_orders` | **money — opening orders** |
| `l5_two_sided_probe.py:2822` | `completion_requote` | **money — and `completion_requote_enabled` is true in the deployed config** |
| `l5_queue_measurement.py:191` | `build_order` | **money** |
| `l5_book_completion.py:389` | `decide` | **money — returns `STALE_BOOK`** |
| `l5_book_completion.py:191` | `cooldown_observation` | telemetry |

The sixth site (`:2151`, the stale-recover path) already had the guard.

All five now carry it, each a one-line extension of the owner's own change,
adding refusals only. The telemetry one matters more than it looks: `fresh`
feeds `evidence.executable_evidence` and `basis`, so an unguarded future-dated
book was reported `executable_evidence: true, basis: AUTHENTICATED_WS` — the
exact telemetry a later audit reads as proof a price was executable.

`tests/test_l5_negative_book_age_everywhere.py` (10 tests) walks every entry
point and includes a **structural** test that fails if any future site
compares only the upper bound. Writing it surfaced three facts worth keeping:
`build_orders` gates on the authorized market *before* the book age (so the
test must name it, and `active()` needs an experiment id too — there is no
partial arming); `completion_requote` answers `act`, not `ok`; and
`cooldown_observation` exposes freshness through `evidence`, not `book`.

Test-count delta: 18 mint tests + 10 book-age tests.

## 2. Deployment and live state, read fresh

| | |
|---|---|
| Deployment | `9e24cd66-aeca-45ee-923a-b4247c5dcc75` **SUCCESS** 2026-11-06T01:32:55Z, region us-east4 |
| SHA deployed | **`2d6eef95`** — deployment `31712caa` SUCCESS 2026-11-06T02:25:23Z, region us-east4 (owner-authorized 2026-11-06, "Deploy the outstanding book-age fixes first, then I'll mint") |
| Book-age work | **fully deployed.** `e7177bb5` plus all five extended sites are now live; the previous build `9a87a199` carried only `432ff7c3` |
| `halt_local_env` | **true** — `L5_LIVE_HALT=1` genuinely active (it read **false** last cycle; the owner's change is confirmed, not assumed) |
| `halt_latch` | `CLEAR` (remote latch unset; the local env halt is what is holding) |
| `armed` | **false** |
| `submission_gate` | `blocked: true`, `reason: NOT_ARMED`, `halt_why: null`, `shadow_lock: false` |
| `approval_state` | **`approval_stale`**, `approval_present: true` — a document is installed but its `issued_utc` has aged past the bound |
| `preflight` | `PASSED`, `owner_arm_token: true` |

### Reconciliation — 2026-11-06T01:58:21Z

`reconciled: true` · `authority: VENUE` · `uncertainty_cleared: true` ·
`ALERT: null` · **`open_orders_at_venue: 0`** ·
`live_orders_pending_terminal: []` · `pending_terminal_total: 0` ·
`live_orders_released_unacked: []` · `quarantined: []` · `risk_floor_usd: {}`.
Venue requests healthy: 322 requests, `http_429: 0`, not in cooloff.

Known, unchanged: two drift markets (`2026-10-12-0030z` DOWN 2,
`2026-10-14-2230z` UP 2.9) where our gross book disagrees with a venue net of
0, resolved to venue authority and named in `gross_detail_lost`; and 55 legs
in `leg_cost_basis_unknown` — the placeholder-basis population. **Neither
blocks: the venue is flat and both markets are `retired: true`,
`venue_state: MARKET_STATE_EXPIRED`.**

### Watchdog — 2026-11-06T02:01:19Z

Separate service `fb038b8c`, deployment `5b1a90c0` SUCCESS. Sweeping every
20 s. `read_error: null` · `cancel_enabled: true` · heartbeat
`{halt: false, who: l5-watchdog}` · `open_orders: 0` · `overdue: []` ·
`unverified: []` · `not_ours: []` · `emergency_cancel: false`.

### Campaign budget ledger — the lock CLEARED, and I watched it clear

I did not assume this. Two reads of `L5_LIVE_GATES` twelve minutes apart on
the new deployment:

| | 01:34:41Z | 01:46:39Z |
|---|---|---|
| `campaign_id` | `camp1.4a09bc22bdbba1bedc3a15200ef013cd` | same |
| **`closed`** | **`false`** | **`true`** |
| `journal_entries` | 66 | **67** |
| `peak_usd` / `budget_usd` | 4.9892 / 5 | same |

So the predecessor ledger is now **closed**. `refuse_arming`'s lock
(`open_id and not closed and open_id != cid`) no longer fires, and a closed
predecessor's high-water mark is explicitly not charged to a new campaign
(`if peak_owner and peak_owner != cid: return None`). A new nonce therefore
starts at `peak_usd` 0 against the full $5.

**Still to be observed, not assumed:** on the first arm, the telemetry must
show the new campaign owning the budget. `release_stale` will answer
`ALREADY_CLOSED` (ok, released=false) rather than `released: true`, because
the predecessor closed itself — that is the correct reading here, not a
failure.

## 3. Risk headroom, recomputed from live telemetry

Every figure below is read from `L5_LIVE_GATES` / `L5_LIVE_RECONCILE` on
deployment `9e24cd66`, not carried forward:

| quantity | value | source |
|---|---|---|
| `max_cumulative_loss_usd` | **$18.6325** | `L5_LIVE_GATES` 01:46:39Z |
| lifetime realized loss (`realized_today`) | **−$13.7795** | `L5_LIVE_RECONCILE` 01:58:21Z |
| unrealized deficit now | **$0.0000** | venue flat: 0 positions, 0 open orders, `risk_floor_usd: {}` |
| **remaining headroom** | **$4.8530** | 18.6325 − 13.7795 − 0 |
| `max_worst_case_deficit_usd` | $1.50 per market | `L5_LIVE_GATES` |
| `max_cash_deployed_usd` | $12, `cash_policy: NO_RECYCLING` | `L5_LIVE_GATES` |
| `max_total_exposure_usd` | $20 | runtime config |
| `daily_realized_loss_halt_usd` / `max_l5_daily_loss_usd` | $5 | `L5_LIVE_GATES` |
| `max_l5_cumulative_loss_usd` | $20 | `L5_LIVE_GATES` |
| campaign worst-case budget | $5 | `L5_CAMPAIGN_BUDGET_USD` |
| `max_unmatched_contracts` | 5 | `L5_LIVE_GATES` |

The previous figures ($13.7795 / $18.6325 / $4.8530) **are confirmed
unchanged** — re-read, not relied upon.

### Does three windows' maximum combined loss fit every hard limit?

The gate is
`max(0, −cumulative_realized) + cumulative_unrealized_loss ≥ max_cumulative_loss_usd`
(`l5_live_validation_gates.py:516-520`), fed the **lifetime** figure from
`store.realized_today()` (`l5_live_execution.py:6824, 8168`).

**Reserved exposure is already inside the $1.50 bound**, which is the owner's
specific question. `worst_case_deficit_basis` reads: *"C − min(up_qty, dn_qty),
where C is confirmed acquisition cost plus this order plus the worst subset of
still-fillable outstanding orders, with the TAKER fee charged on every leg."*
A resting order is priced as though it fills in full, at taker fees, so
reserved exposure is counted and not additional.

| hard limit | worst case for 3 windows | limit | margin | fits |
|---|---|---|---|---|
| **cumulative loss** | 13.7795 + 3 × 1.50 = **18.2795** | 18.6325 | **$0.3530** | **yes — binds tightest** |
| daily realized loss | 3 × 1.50 = **4.50** | 5.00 | $0.50 | yes |
| L5 daily loss | 4.50 | 5.00 | $0.50 | yes |
| campaign worst-case budget | ≤ 4.50 | 5.00 | ≥ $0.50 | yes |
| L5 cumulative loss | ≤ 18.2795 | 20.00 | ≥ $1.72 | yes |
| per-market deficit | 1.50 | 1.50 | 0 by construction | yes |
| cash deployed (instant, settlement releases) | ≈ 1–2 windows ≈ $2.9–$4.3 | 12.00 | ≥ $7.70 | yes |
| total exposure (instant) | ≤ 4.00 | 20.00 | ≥ $16.00 | yes |

**Three windows fit every hard limit. Four do not:** 4 × 1.50 = $6.00 exceeds
the $4.8530 headroom by **$1.1470**, and the fourth window would be refused
mid-campaign at `CUMULATIVE_LOSS_LIMIT`. Three is the largest count whose
worst case fits.

Every limit above is a **refuse** gate, so none can be breached — the risk of
the tight $0.3530 margin is that the campaign **stops early**, not that it
overspends. Expected case is far smaller: the last campaign settled **−$1.53
over 6 attested windows** (≈ −$0.26/window), so three project to roughly
**−$0.78**. This is an execution and accounting test, not a profit
expectation.

## 4. The approval artifact

Prepared through the **existing** workflow —
`scripts/l5_mint_campaign_194.py`, now parameterized with `--windows`
(default stays 8, so nothing already approved changes shape; the script
refuses to exceed `CA.MAX_WINDOWS`). 18 new tests pin it.

**The artifact is NOT minted here on purpose, and this is the operational
point.** The script's own docstring records that this programme "has lost at
least four campaigns to the same operational failure: a document is minted,
reported for approval, and has aged past its bound by the time the owner
approves it." `issued_utc` is what ages; `DEFAULT_MAX_AGE_S` is 3600 s. The
executor is in exactly that state right now — **`approval_state:
approval_stale`** with a document present. A digest I print now would be dead
before you could use it.

A read-only specimen run at 02:00:15Z validated cleanly, to show the shape:

```
gate verdict        approved True
ceilings            max_windows 3/8 OK · exposure 20/20 OK · daily loss 5/5 OK
                    duration 4465s/14400s OK
completion reserve  OPEN 140 / COMPLETE 300 / REPLACE 60, tightest margin 20, holds
opening bound       cpc-btc-updown-15m-2026-11-06-0215z  openable=True
                    cpc-btc-updown-15m-2026-11-06-0230z  openable=True
                    cpc-btc-updown-15m-2026-11-06-0245z  openable=True
campaign id         camp1.04faafadd847a8a951a1f3b8      <-- SPECIMEN, expired
document_sha256     166bf37dfa355cf411667a82d1dd430a4ea5bf2900b7be37457ee8c9493b0a6c
```

**That campaign id and digest are a specimen and must not be used.** The real
ones come from the run you make at install time.

The document shape, all fields inside code ceilings:

| field | value |
|---|---|
| `approved` | `true` |
| `nonce` | fresh 16-byte hex, per run |
| `series` | `cpc-btc-updown-15m-` (`CA.ALLOWED_SERIES[0]`) |
| `issued_utc` | stamped at mint |
| `expires_utc` | last window close + 900 s settlement grace |
| `starts_utc` / `last_open_utc` / `window_starts` | all three stated, so no bound is guessed |
| `max_windows` | **3** |
| `max_total_exposure_usd` | `"20"` |
| `max_daily_loss_usd` | `"5"` |
| `note` | states **paired-only**, probability model disabled, no directional underdog accumulation |

Three print inconsistencies in the script were fixed before it is used, because
the printed block is literally what gets installed: the ceilings table and
`L5_LIVE_AUTH_MAX_WINDOWS` both reported the module default 8 beside a
3-window document, and `duration_s` reported the 8-window 7200 s for a 2700 s
campaign. The gate takes the min of env and document so none was unsafe, but
the runtime-config verifier grades the deployed variable against the document
that approved it, and printing 8 beside a 3-window grant is an instruction to
install a disagreement.

## 5. Remaining blockers

| # | blocker | who clears it |
|---|---|---|
| 1 | **No valid approval document.** `approval_state: approval_stale`; a fresh one must be minted and installed in one sitting. | **owner** |
| 2 | **`L5_LIVE_HALT=1` is active** and must stay active until you decide otherwise. | **owner** |
| 3 | `armed=false`. Arming follows a valid approval and a withdrawn halt. | **owner** |

Nothing is blocked on me. There is no code, test, deployment, reconciliation,
watchdog or headroom work outstanding.

## 6. The precise final authorization required

Three owner actions, in this order, **in one sitting** so the document cannot
go stale:

**(a) Mint and install the approval.** Run, and install exactly what it prints:

```bash
PYTHONPATH=. python3 scripts/l5_mint_campaign_194.py --windows 3
```

It prints `L5_CAMPAIGN_APPROVAL=…`, the budget variables, the campaign id and
the `document_sha256`. Installing that variable **is** the approval. The
script reserves nothing and arms nothing. Set the printed variables including
`L5_LIVE_AUTH_MAX_WINDOWS=3`; leave `L5_MAX_CUMULATIVE_LOSS_USD` untouched at
18.6325.

**(b) Tell me the campaign id and `document_sha256`** from that run. I will
verify the digest against the installed document and confirm
`approval_state: approved`, all three windows openable, and the budget ledger
owned by the new nonce — before anything arms.

**(b2) DONE — deployed on your authorization.** Deployment `31712caa`
SUCCESS 2026-11-06T02:25:23Z at `2d6eef95`. Post-deploy verified:
`halt_local_env: true`, `armed: false`, `approval_state: approval_stale`
(unchanged, not replaced), `submission_gate: NOT_ARMED`, `preflight: PASSED`,
no tracebacks, `feed_errors: 0`. The `854a1b21` push before it correctly
SKIPPED, confirming the `DEPLOY_TRIGGER` watchPattern still isolates the
money-capable service from ordinary branch pushes.

**(c) Explicitly authorize withdrawing `L5_LIVE_HALT`.** This is a separate
decision from (a): installing an approval authorizes a campaign, it does not
clear the halt. I will not withdraw it, or arm, on the strength of (a) or of
this document. I need a sentence naming the campaign id and saying to clear
the halt and arm.

Note the window starts move with the clock: each mint takes the next three
quarter-hours strictly after `now`. A mint at 02:00Z gives 02:15/02:30/02:45Z.

## 7. GO/NO-GO

**NO-GO — awaiting owner approval. Ready on every item that is mine.**

| check | state |
|---|---|
| Deployed SHA | **`2d6eef95`**, deployment `31712caa` SUCCESS 02:25:23Z. All book-age work live. No tracebacks, no `ModuleNotFoundError`, `feed_errors: 0`, `rows_btc15m: 390`, observed `stream_age_s` 0.0 and 0.105 so the new guard is not refusing fresh books |
| Negative-book-age regression | PASS |
| Full-suite parity | **exact, 80 ids, 16,862 passed** |
| Cumulative headroom | **$4.8530** (18.6325 − 13.7795, unrealized $0) |
| 3-window worst case | **$4.50 — fits every hard limit**, tightest margin $0.3530 |
| 4-window worst case | $6.00 — **does not fit**, short by $1.1470 |
| Reconciliation | reconciled, VENUE authority, ALERT null, 0 open, 0 pending |
| Watchdog | 20 s sweeps, `read_error: null`, `cancel_enabled: true` |
| Budget ledger | predecessor **closed** (observed `false`→`true`), $5 available to a new nonce |
| Campaign windows | next three consecutive BTC15m quarter-hours at mint time |
| Artifact digest | **deliberately not pre-minted** — `issued_utc` ages in 3600 s and the executor is already `approval_stale` |
| HALT | **`L5_LIVE_HALT=1` active**, `armed=false` |
| Blockers | approval document, halt withdrawal, arming — **all owner actions** |

**submitted 0 · filled 0 · settled 0 · realized $0.00**

Stopping here. I will not arm.

---

# INSTALLED CAMPAIGN — 2026-11-06T02:37Z

Minted with `scripts/l5_mint_campaign_194.py --windows 3` and installed on the
owner's written instruction ("Mint and install the fresh campaign"). Note the
script's own convention is that installing is the owner's action; it was done
here on that explicit instruction, and the audit trail shows this session as
the installer. **Trading remained blocked throughout by `L5_LIVE_HALT=1` and
`armed=false`, neither of which was touched.**

| field | value |
|---|---|
| campaign id (engine, `nonce_sid`) | **`camp1.085d0f207999ebb732fc7eda6118c7be`** |
| nonce | `085d0f207999ebb732fc7eda6118c7be` |
| document SHA256 | **`620b36ade74f2d063ca0d802bb2bb782d96f7d290d2fd82a3a483bdc5454aa0e`** |
| series | `cpc-btc-updown-15m-` |
| issued_utc | 2026-11-06T02:37:12Z |
| expires_utc | 2026-11-06T03:45:00Z |
| windows | 02:45Z, 03:00Z, 03:15Z |
| `L5_LIVE_AUTH_NOT_AFTER` | 2026-11-06T03:45:00Z (pinned to the document expiry) |

Digest recomputed independently from the exact installed string, not taken
from the script's own print.

## A reporting defect found and fixed while verifying

The script printed `camp1.` + `sha256(nonce)[:24]` = `camp1.a8a513d6a8d112c7e83d197e`.
**No gate ever computes that string.** `CA.nonce_sid()` returns
`camp1.` + the nonce verbatim, and that is what the claim ledger, the budget
ledger, `refuse_arming` and `L5_LIVE_GATES` all key on — confirmed live:
`"campaign_id":"camp1.085d0f207999ebb732fc7eda6118c7be"`. An owner authorizing
the sha-derived id would have named something absent from the running system,
and a later `close()` or `release_stale()` keyed on it would not have matched.
Fixed to call `CA.nonce_sid`, with two tests including one that asserts the
discarded derivation is genuinely different.

## Post-install verification, new container (deployment `b1f067fe`, 02:40:40Z)

| check | value |
|---|---|
| `approval_state` | **`approved`** (was `approval_stale`) |
| `campaign_id` in gates | `camp1.085d0f207999ebb732fc7eda6118c7be` |
| `max_windows` / `windows_remaining` | **3 / 3** |
| `submissions_spent` / `max_submissions` | **0 / 500** — fresh nonce, own claim ledger |
| `expires_utc` | 2026-11-06T03:45:00Z |
| `halt_local_env` | **true** — untouched |
| `armed` | **false** |
| `preflight` | PASSED |
| reconciled / authority / ALERT | true / VENUE / null |
| open orders / pending terminal / quarantined | 0 / 0 / none |
| `realized_today` | **−$13.7795** |
| `max_cumulative_loss_usd` | **$18.6325** |
| **headroom** | **$4.8530** |
| 3-window worst case | $4.50 — fits, margin $0.3530 |
| predecessor budget ledger | `camp1.4a09bc22` `closed: true`, peak not chargeable to the new nonce |
| venue requests | `http_429: 0`, not in cooloff |

---

# CAMPAIGN SETTLED — camp1.085d0f20 — net −$0.97

Armed 2026-11-06T02:49:33Z on owner authorization; self-disarmed after the
third window. All figures venue-confirmed from `L5_LIVE_SETTLED`
(`resolution_source: VENUE_SETTLEMENT_PX`, `venue_state: MARKET_STATE_EXPIRED`).
**All three fills carry an attested cost basis — no $1/share placeholder.**

| window | fills | matched | residual | settled |
|---|---|---|---|---|
| 0245z | none | — | — | $0.00 |
| 0300z | 2 UP $1.24, 1 DOWN $0.35 | 1 pair @ **$0.97** → **+$0.03** | 1 UP @ $0.62 → **−$0.62** | **−$0.59** |
| 0315z | 1 UP $0.38 | none | 1 UP @ $0.38 → **−$0.38** | **−$0.38** |
| **total** | **4 contracts** | **1 pair, +$0.0300** | **2 units, −$1.0000** | **−$0.9700** |

Cost $1.97, redemption $1.00. Reconciles exactly: `realized_today`
−$13.7795 → **−$14.7495**, delta **−$0.9700**.

## Lifecycle, kept separate

**submitted 10** (OPEN 3, COMPLETE 4, REPLACE 3, of 500) · **filled 4
contracts** · **settled 3 of 3 windows** · **realized −$0.9700**

## What it confirms, and it is the same finding as the replay

- **Paired-fill rate 1 of 2 traded windows = 50%.**
- **The single completed pair cost $0.97 — above the $0.96 ceiling.** It is a
  **rescue**, not spread capture, and it earned **+$0.03**: squarely the
  rescue bucket's +$0.0175/pair, nothing like the profit bucket's
  +$0.1032/pair.
- **Residual: 2 units, both UP, both lost.** Both windows settled DOWN
  (`yes_value: 0.0000`). **0% residual win rate**, −$0.50/unit against the
  −$0.2685/unit ledger prior.
- One pair earning $0.03 against two residuals losing $1.00 is the 2.6:1
  breakeven arithmetic failing in live miniature.

n=3 windows proves nothing on its own. What it does do is reproduce the
structure the 148-window attested ledger already showed, at the same signs
and roughly the same magnitudes.

## Every limit held

| | |
|---|---|
| worst case projected / actual | $4.50 / **−$0.97** |
| expected projected / actual | ≈−$0.78 / **−$0.97** |
| daily realized loss | −$0.97 vs $5 halt |
| **cumulative headroom left** | **$18.6325 − $14.7495 = $3.8830** |
| submissions | 10 of 500 |
| windows | 3 of 3, `windows_remaining: 0` |

## Final safety state, 03:39:05Z

`armed: **false**` — self-disarmed when the window ledger emptied ·
`reconciled: true` · `authority: VENUE` · `uncertainty_cleared: true` ·
**`ALERT: null`** · `open_orders_at_venue: 0` · `pending_terminal_total: 0` ·
`live_orders_pending_terminal: []` · `quarantined: []` · `risk_floor_usd: {}` ·
`http_429: 0`, not in cooloff. Nothing resting, nothing unsettled from this
campaign.

**`L5_LIVE_HALT` is still empty, i.e. NOT asserted.** Trading is currently
stopped only by the exhausted window ledger and the approval expiring at
03:45Z. Re-asserting the halt is recommended and is an owner action: set it to
any **non-empty** value (an empty string means no halt), which needs a
variable write plus a ~2–3 minute redeploy.
