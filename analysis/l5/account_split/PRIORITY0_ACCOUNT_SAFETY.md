# Priority 0 — account safety: current state, diagnosis, rollout

Read-only investigation. **Nothing was deployed, no production variable
was changed, no halt was reset, no trading was authorized.** Every figure
names the live log line or source file it came from.

## 1. Current production state, 2026-11-18T14:18Z

| service | SHA | state |
|---|---|---|
| `pmus-spring-l5-probe` (sports) | `ac18a20bf` | **live**, deployment `2bdf0cc9` booted 14:18:37Z |
| `l5-pmus-live` (BTC15m) | `a8a5898e0` | shadow-locked, halted, `APPROVAL_NOT_VALID` |
| `l5-btc5m-live` | `558bdb0e0` | disarmed |
| `l5-halt-watchdog` | `cea7e608a` | live |

### Can new orders still be submitted? NO — but only by a manual lock.

`Engine.place` has exactly three gates, in this order:

1. `account_risk_blocks(px, qty)` — **bypassed**. `PMUS_TWO_SIDED_ACCOUNT_RISK`
   is `observe`, so `account_risk_blocks` returns `None` at its second line
   (`not ACCOUNT_RISK_ON`).
2. `submission_locked()` — **ASSERTING.** Observed live:
   `place_refused_submission_lock` at 14:18:15Z, slug
   `aec-setkamemd-ciumir-cojdor-2026-11-18`, NO, px 0.35, qty 5, stage
   `two_sided_open_rewarded_cheap_first`.
3. `shared_halt()` — **PERMITTED.** Observed live at 14:18:41Z:
   `shared_halt {state: "PERMITTED", why: null}`.

So the lane **is** trying to open (that refused order was an opening
stage), and the only thing stopping it is
`PMUS_TWO_SIDED_SUBMISSION_LOCK=1` — a manually set variable. No risk
control is holding it:

  - account-wide risk: observe, refuses nothing;
  - `PMUS_TWO_SIDED_OPEN_EV_GATE`: off;
  - shared halt: permitted;
  - `max_taker_qty: 0`, maker-only, `sports_cap 0.97`, per-market $1.50,
    total $60, lane daily stop $15.

**If that one variable is cleared, sports resumes opening immediately,
with the account-wide daily halt breached and negative measured EV in all
20 sports.** That is the whole of the Priority 0 exposure.

`submission_locked()` is also `== "1"` after strip, so `"ON"`, `"true"`,
`"yes"` would all read as *unlocked*.

### Venue orders and inventory

`INVENTORY_RECONCILE` 14:07:57Z: `ok: true`, `reconciled: true`,
`venue_positions_nonzero: 0`, `mismatch: 0`, `open_orders_by_owner: {}`,
`foreign_orders_in_sports_markets: []`,
`sports_open_unmatched_cost_usd: 0.0000`, 643 sports book markets
(12 match / 631 settled_flat / 0 pending).

**There is no open unmatched sports exposure and no unexplained venue
order.** The exposure is not in positions; it is in the permission to
create new ones.

### The two halts disagree

  - BTC15m heartbeat: `halt: true` with `halt_local_env: false` — the
    latch comes from the **remote halt document**, not a local variable.
  - Sports, reading `L5_HALT_URL`: `PERMITTED`.

Two services that should share one halt return opposite answers. This is
NOT diagnosed here and is flagged as an open item: either they read
different URLs, or they read the same document with different semantics.
Until it is resolved, the watchdog's latch cannot be relied on to stop
sports.

### Daily loss, and what the figure is made of

    utc_day            2026-11-18
    utc_day_realized  -$52.99      halt $50   would_halt TRUE   room -$2.99
    all_time_realized -$88.589     (the figure the cumulative gates read)

Origin split from the same heartbeat (`origin.realized.by_origin`):

    BTC15M    -$3.6173   193 markets  (171 authenticated, 22 unverified)
    BTC5M            $0     0 markets
    MANUAL           $0     0 markets
    UNKNOWN  -$163.6754   254 markets  (108 authenticated,
                                        146 UPPER_BOUND_1USD_PER_CONTRACT)
    sum of rows -$167.2927 over 447 rows, complete, none unreadable

**146 of the 254 UNKNOWN rows were booked at $1 a contract because this
ledger never saw their price.** The publisher's own label says it: "their
figure is a bound and not a result... the account-wide loss limits read
the account-wide totals, which include every row here whatever its
label."

So the breach is real **as the gate measures it**, and the measurement is
an upper bound on loss for a majority of the UNKNOWN rows. The true loss
is smaller than -$52.99. That is the conservative direction and refusing
on it is safe — but it means the breach should not be reported as a
measured -$52.99 of realized loss.

## 2. Diagnosis: why the account-wide daily halt did not stop sports

It was never consulted for a decision. `src/pmus_sports_two_sided.py`:

    _ACCOUNT_RISK_MODE = os.getenv("PMUS_TWO_SIDED_ACCOUNT_RISK", "0").strip()
    ACCOUNT_RISK_ON      = _ACCOUNT_RISK_MODE == "1"
    ACCOUNT_RISK_OBSERVE = _ACCOUNT_RISK_MODE.lower() == "observe"

and in `account_risk_blocks`:

    if v.get("off") or not ACCOUNT_RISK_ON:   # observe: log only
        return None

The live value is `observe`. The record is fetched, evaluated with the
full fail-closed semantics, and logged as `ACCOUNT_RISK` — the verdict
reached is correct — and then discarded. **Nothing failed. Enforcement was
never switched on.** `observe` is the deliberate read-only first step
`ac18a20bf` shipped, and it did its job: it proves the record is
reachable and its figures parse.

## 3. Configuration changes required (for owner approval; NOT applied)

Both switches already exist, are implemented, and are tested. No code
change is needed to arm them.

| variable | now | proposed | effect |
|---|---|---|---|
| `PMUS_TWO_SIDED_ACCOUNT_RISK` | `observe` | `1` | openings/extensions refused on any unverifiable, stale or breached account-wide state; completions/exits never refused |
| `PMUS_TWO_SIDED_OPEN_EV_GATE` | `0` | `1` | openings refused where per-sport settled EV <= `OPEN_EV_MIN` |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `1` | **keep `1`** | the current stop; do not clear it until the two above are armed |

`L5_ACCOUNT_RISK_URL`, `L5_ACCOUNT_MAX_EXPOSURE_USD` and
`L5_ACCOUNT_LANES` must be set for arming: with the URL unset the verdict
is `ACCOUNT_RISK_URL_NOT_SET` and with the cap unset
`ACCOUNT_CAP_UNSET_OR_UNREADABLE` — both REFUSE, so a half-armed
configuration fails closed rather than opening. The boot line reports
`url_set: true` already.

Not proposed, per instruction: no change to BTC5m `FOREIGN_EXPOSURE`
(stays `CHARGED`), and no managed-lane exemption anywhere.

## 4. Verification and tests

`ac18a20bf` already covers the four cases asked for, and they pass here
(193 tests, `tests/test_l5_account_risk.py` +
`tests/test_pmus_sports_two_sided.py`):

  - breached halt → `ACCOUNT_DAILY_LOSS_HALT`, openings refused;
  - stale / future-dated / says-unreadable / wrong-day / unparseable
    figures / unreachable record / unset URL → all REFUSE;
  - concurrent lanes → two lanes cannot spend the same room; the lane
    share is enforced per order;
  - risk-reducing operations → a breach never blocks a completion;
  - `observe` evaluates and logs but never blocks.

**What was missing, and is added here (17 new tests):** nothing tested the
two switches TOGETHER, or that either leaves the risk-reducing paths
alone. Those are exactly the properties needed before arming both.

  - both armed → every new opening refused;
  - both armed → `two_sided_complete`, `two_sided_complete_taker` and
    `two_sided_exit` all still send, while `two_sided_open`,
    `two_sided_extend` and `two_sided_open_rewarded` do not;
  - both armed → cancels still succeed and `cancel_all_own` does not
    raise (cancelling is not submitting; the watchdog and abandon paths
    depend on it);
  - both armed → reconciliation stays readable and the figures stay
    visible, so a breach cannot hide the book;
  - only the exact string `"1"` enforces — parametrised over `"ON"`,
    `"on"`, `"true"`, `"yes"`, `"enabled"`, `"enforce"`, each of which
    reads as enforcement to a human and is OFF in code.

**Mutation-checked**, so these are load-bearing rather than decorative:
removing the account-risk call from `place()` fails the completion test
(openings leak through), and making `is_completion_stage` return `False`
also fails it (exits get blocked). Both restored.

## 5. Bounded rollout plan

Each step is reversible by resetting one variable, and no step clears the
submission lock until the controls above it are proven in production.

  **Step 0 (now, no approval needed).** Keep
  `PMUS_TWO_SIDED_SUBMISSION_LOCK=1`. Nothing can open. This is the
  status quo and it is safe.

  **Step 1 — resolve the halt disagreement.** Determine why BTC15m reads
  `halt: true` from the remote document while sports reads `PERMITTED`.
  Read-only. Until this is closed the watchdog is not a backstop for
  sports, and arming the rest rests on application-level gates alone.

  **Step 2 — arm the EV gate** (`PMUS_TWO_SIDED_OPEN_EV_GATE=1`), lock
  still on. Confirm `open_ev_reject` lines appear and
  `open_gate_snapshot.on` is true. No order can be sent either way, so
  this costs nothing and proves the gate evaluates in production.

  **Step 3 — arm account-wide enforcement**
  (`PMUS_TWO_SIDED_ACCOUNT_RISK=1`), lock still on. Confirm the
  `ACCOUNT_RISK` line reports `ok: false`,
  `reason: ACCOUNT_DAILY_LOSS_HALT`, and that
  `place_refused_account_risk` appears for opening stages. Still nothing
  can be sent.

  **Step 4 — verification gate, no clearing yet.** With both armed and
  the lock still on, confirm from logs that completion and exit stages
  are *not* refused by account risk, and that cancels still work. If any
  completion is refused, STOP: the exemption is not behaving and clearing
  the lock would strand inventory.

  **Step 5 — only on explicit owner approval.** Clear
  `PMUS_TWO_SIDED_SUBMISSION_LOCK`. Openings are then refused by the EV
  gate (negative in all 20 sports) and by the breached account halt,
  while completions, exits and cancels continue. Expected behaviour:
  **no new openings at all** until either the day rolls over or sport EV
  turns positive. If any opening is submitted, re-set the lock.

  **Step 6 — reconcile the loss figure.** The -$52.99 is dominated by
  146 upper-bound-booked rows. Attributing those at measured prices is
  what would let the daily halt reflect real losses rather than a bound.
  Read-only accounting work; no trading depends on it.

Nothing in steps 1-6 is executed here. Steps 2, 3 and 5 are production
variable changes and need explicit owner approval.

## 6. Item 5 — adverse entry selection, for after the safety path

The EV gate's own evidence (`src/pmus_sports_open_gate.py`, offline
ledger 2026-11-15, 682 venue-confirmed fills at the venue's final
settlement price, 77 settled markets):

    fully paired markets      +$6.03   over 40 markets
    markets left unmatched   -$25.80   over 37 markets
    of those 37 unmatched legs, 0 WON, against ~18 implied by their
    own prices

That is the adverse selection to attack, and it is structural rather than
bad luck: a leg the market moves FOR is cheap to complete and becomes a
pair; a leg the market moves AGAINST is too expensive to complete and is
held to zero. It is also the **opposite sign** to BTC5m, where strands are
favourably selected (69.6% win at a 0.5987 basis). So sports
adverse-selection assumptions must not be carried to BTC5m, and the
sports fix is at entry, not at completion.
