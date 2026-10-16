# L5 loss-gate rescoping — PROPOSED, NOT APPLIED

**Status: BLOCKED pending a human with write access. No code change has been
made. The deployed executor enforces every existing loss gate unchanged, and
a test (`test_gate_input_changes_no_policy_by_itself`) asserts the three
protected call sites still read `realized_today()` by counting them in the
source.**

**Updated 2026-10-16 at `e42c3ef1`.** Two things changed since the first
draft and both matter to the decision:

1. **The account-wide figure is now `-218.99`, not `-79.01`.** Four post-T
   settlements arrived priced at the `$1`/share upper bound with
   `UNKNOWN_COST_UPPER_BOUND` provenance: BTC 0330z `-2.00`, ETH 0330z
   `-50.00`, SOL 0330z `-50.00`, XRP 0330z `-37.98`. **These are estimates,
   not verified realized losses**, and none is attributable to L5 or to
   manual trading.
2. **The mechanism the change needs now exists and is landed separately from
   the change.** `InventoryStore.four_bucket_accounting()` and
   `gate_input(bucket)` split realized P&L into `account_wide`,
   `verified_l5`, `manual` and `unknown_unattributed`, maintain the identity
   `account_wide = verified_l5 + manual + unknown_unattributed`, and return
   `None` -- never zero -- for an unknown. So the diff below is now a
   *substitution of a named bucket*, not the introduction of a new concept.

### What the four buckets say today

| Bucket | Value | Note |
|---|---|---|
| `account_wide_realized` | `-218.99` | authoritative total; what the gates read now |
| `verified_l5_realized` | `0` | zero **attested** L5 settlements since T |
| `manual_realized` | **`None`** | a residual, and unavailable while anything is unattributable |
| `unknown_unattributed_realized` | `-218.99` | every unattested post-T settlement plus the whole pre-T total |

**Essentially the entire `-218.99` is UNKNOWN.** It is neither verified L5
nor verified manual. The gates are therefore refusing on a figure that is
almost wholly an upper-bound estimate of unattributable activity -- which is
the conservative behaviour, and is also why it can never clear on its own.

### Why the cost-basis audit cannot resolve it

Run in production, the audit reported `our_client_order_ids: 0`: our durable
fill-provenance registry is empty, so **no historical fill can be attributed
to either party**. That is unrecoverable -- the venue does not say who placed
a historical fill on a shared account. A separate defect (the walk bounded at
40 pages) is fixed at `e42c3ef1`, which may let the audit establish the
*cost* of those legs (an account fact) and so measure the overstatement; it
will never establish their *ownership*.

Owner approved this change in principle on 2026-10-16, conditional on
production verification of account-wide exposure aggregation. That condition
is now **met** (evidence below). The edit itself was refused by the harness
safety classifier as `[Security Weaken]`, and the owner has directed that the
denial not be bypassed and that tool permissions not be changed. So the
change is documented here instead.

---

## 1. What the change does

Three gate call sites are fed the **account-wide** realized total
(`store.realized_today()`, currently `-79.01` and rising with the owner's
manual trading). The proposal feeds them the **L5-scoped** figure measured
from the verified baseline epoch instead.

**No limit value changes.** `$5` stays `$5`; `$20` stays `$20`. What changes
is the population measured.

### The three affected call sites

All in `src/l5_live_execution.py` at `2e8b4772`:

| Line | Gate | Limit | Current input | Proposed input |
|------|------|-------|---------------|----------------|
| 3372 | `ValidationGates.refuse(cumulative_realized=…)` | `max_cumulative_loss_usd` = **20** | `self.store.realized_today()` | `l5_scoped` |
| 3818 | `ValidationGates.refuse(cumulative_realized=…)` | `max_cumulative_loss_usd` = **20** | `self.store.realized_today()` | `l5_scoped` |
| 3850 | `SafetyLimits.refuse(realized_today=…)` | `daily_realized_loss_halt_usd` = **5** | `self.store.realized_today()` | `l5_scoped` |

**Site 3850 was missed in my earlier reports to the owner.** I described this
as "one line at 2989". That was wrong in both the line number and the count.
Rescoping only the two `cumulative_realized` sites would have left the `$5`
daily halt still reading `-79.01` and still refusing every order — the change
would have looked complete and achieved nothing. Recorded here because an
incomplete rescoping is worse than none: it would present as done.

### The fail-closed guard

Inserted before the gate calls, beside the existing account-wide exposure
guard so both fail closed in one place:

```python
# L5-SCOPED REALIZED, fail-closed. None means no verified baseline or an
# unreadable settlement row, and a loss ceiling fed an unknown figure is not
# a ceiling. It is NEVER substituted with zero.
l5_scoped = self.store.l5_realized_since_baseline()
if l5_scoped is None:
    prov.stage("SAFETY_OVERLAY", {"refused": "L5_SCOPED_REALIZED_UNAVAILABLE"})
    return prov.refuse("SAFETY_OVERLAY", "L5_SCOPED_REALIZED_UNAVAILABLE")
```

`l5_realized_since_baseline()` returns `None` when there is no baseline, and
also when any post-T settlement row cannot be read. Both refuse. Missing is
never zero.

---

## 2. What is preserved

- **Historical accounting.** `realized_attributed` (`-79.01`),
  `realized_unattributed`, the 42 unknown-basis keys and
  `verified_l5_status: UNPROVEN_LEGACY_ATTRIBUTION` remain in
  `realized_lines()` and the heartbeat. Nothing is reset, netted or erased.
  The pre-T snapshot is held inside the baseline record.
- **L5-specific ceilings**, already live and unchanged:
  `L5_MAX_L5_DAILY_LOSS_USD=5`, `L5_MAX_L5_CUMULATIVE_LOSS_USD=20`, measured
  from T.
- **Account-wide capital and exposure ceilings**, unchanged in value:
  `max_total_exposure_usd` **5**, `max_usd_per_market` **5**,
  `max_cash_deployed_usd` **7**, `max_worst_case_deficit_usd` **3.60**,
  `max_abs_residual` **10**, `max_unmatched_contracts` **5**.
- **Attribution discipline.** Only markets retired after T whose ownership
  was attested at settlement count toward the L5 figure. An unattested post-T
  settlement is excluded *and* listed under
  `post_baseline_settlements_NOT_attested`, so it can never read as an L5
  zero.

## 3. What is given up — the reason this needs review

**After this change, manual trading has no realized-loss ceiling.** Today the
`$20` cumulative and `$5` daily gates do restrain the account as a whole,
even though they restrain it by counting the owner's own trades as the
strategy's. Afterwards they restrain L5 only.

What stands behind manual activity instead is capital and exposure: the `$5`
account-wide worst-case exposure ceiling, which **as of `f69427f0` genuinely
aggregates every market including manual inventory**. Before that commit it
did not — see §4.

Second-order consequence: if any part of the `-79.01` genuinely was L5's,
that loss stops restraining new L5 trading. Our records cannot settle the
question — `fill_provenance` is empty and `our_client_order_ids` is `0`, so
no historical fill can be attributed either way. This is stated rather than
resolved.

---

## 4. Verification evidence (the owner's condition)

### Account-wide aggregation, observed in production 2026-10-16T03:10:45Z

```json
"account_exposure": {
  "ok": true,
  "markets_counted": ["cpc-btc-updown-1d-2026-10-15-2100z"],
  "per_market_worst_case_usd": {"cpc-btc-updown-1d-2026-10-15-2100z": "2.0400"},
  "account_total_worst_case_usd": "2.0400",
  "max_total_exposure_usd": "5",
  "capacity_usd": "2.9600"
}
```

The owner's manual 1-day DOWN leg contributes **$2.0400**. Under the previous
one-entry `risk_by_market={market: …}` it contributed **zero**, which made
`max_total_exposure_usd` arithmetically identical to `max_usd_per_market` —
there was no account-wide ceiling at all.

### Verified baseline

```
baseline_t                        2026-10-16T02:45:12.565947+00:00
baseline_cash_usd                 350.05255
l5_realized_since_baseline        0          (counted, zero attested L5 settlements)
pre_baseline_realized_total       -79.01000000   (preserved)
pre_baseline_unknown_basis_legs   42             (preserved)
```

### Worst case under executor failure

The credential-free watchdog is accepted as an independent halt **detector**
that cannot cancel a resting order. The unmitigated failure is therefore: the
executor wedges with both legs resting, nothing withdraws them, both settle.
`tests/test_l5_wedge_worst_case.py` asserts that at every price tried,
whatever size the gate permits leaves that wedge inside `$3.60`, including
every partial fill and the one-sided fill (the binding shape).

**Correction recorded there:** the envelope was approved as "4.86 a side AND
$3.60", and those are not simultaneously satisfiable. A DOWN-only fill of
4.86 at `0.92` loses `4.4712`; at `0.87`, `4.2282`. The count was only safe
for the moderate book it was measured on (`4.86 × 0.55 = 2.673`). Safety
comes from the deficit bound binding and the size shrinking.

---

## 5. Capacity is observed, not allowed

`capacity_usd = 2.9600` is a **reading at 03:10:45Z, not an allowance.** It is
`$5` minus the owner's manual exposure at that instant, and the owner trades
continuously. It can shrink — to zero, which would block any test outright.

**Any eventual live test must recompute capacity immediately before
submission and size down or refuse.** It must not carry `2.96`, and it must
not carry the approved `3.60`, as a standing figure. The binding number is
whichever of `max_worst_case_deficit_usd` and remaining account-wide capacity
is smaller **at submission time**.

---

## 6. Approval mechanism

This repository defines **no** security-review mechanism: there is no
`CODEOWNERS`, no `SECURITY.md`, no `CONTRIBUTING`, and `.github/` contains
only `workflows/`. So no in-repo process can authorize this, and none is
being claimed.

What the change actually requires is a **human with write access applying it
directly** — in their own checkout, or through GitHub's web editor — because:

- the harness classifier refuses the edit when Claude makes it, on the
  content of the change rather than the tool used, so switching tools would
  defeat the denial's intent; and
- the owner has ruled out changing tool permissions, which is the only route
  the denial itself suggests.

The diff in §1 is complete and self-contained: three input substitutions plus
the guard. It touches no limit value.

If the change is applied, the following should gate its promotion:

1. the money-path gate (`tests/MONEY_PATH_GATE.txt`), green at **1,236** on
   `2e8b4772`;
2. `tests/test_l5_baseline_epoch.py`, `tests/test_l5_account_wide_exposure.py`
   and `tests/test_l5_wedge_worst_case.py`, which cover the baseline,
   aggregation and wedge bound;
3. a regression asserting that `l5_scoped is None` refuses at **all three**
   sites — the property whose absence would make a partial rescoping look
   complete;
4. deployment with `L5_SHADOW_ONLY` still enabled, and verification of the
   gate readings before any separate arming decision.

## 7. Unresolved, and not addressed by this change

**Independent cancellation.** PMUS issues one account-wide API key pair, so
permission-level scoping does not exist. The only available isolation is the
code-level route restriction in `CancelOnlyTransport`, which the owner
rejected on 2026-10-14 as not satisfying independent permission-level
isolation. The watchdog therefore stays credential-free, detects and latches
a durable HALT the executor cannot clear, and **cannot withdraw a resting
order.** Accepted by the owner for this bounded validation, with the residual
bounded at `$3.60` by §4.

A true independent canceller needs a second, cancel-scoped credential from
the venue. That is a request to PMUS, not an engineering task in this
repository.

---

## 8. The exact diff, and the exact approval action

Requested by the owner 2026-10-16. This is the complete change. It touches no
limit value and introduces no new concept -- `gate_input` and the four buckets
are already landed and tested at `e42c3ef1`.

### The diff

In `src/l5_live_execution.py`, insert the fail-closed guard before the gate
calls (beside the existing account-wide exposure guard):

```python
# L5-SCOPED REALIZED, fail-closed. None means no verified baseline or an
# unreadable settlement row, and a loss ceiling fed an unknown figure is
# not a ceiling. It is NEVER substituted with zero.
l5_scoped = self.store.gate_input("verified_l5")
if l5_scoped is None:
    prov.stage("SAFETY_OVERLAY", {"refused": "L5_SCOPED_REALIZED_UNAVAILABLE"})
    return prov.refuse("SAFETY_OVERLAY", "L5_SCOPED_REALIZED_UNAVAILABLE")
```

Then three input substitutions:

| Line | Gate | Limit | From | To |
|------|------|-------|------|----|
| 3372 | `ValidationGates.refuse(cumulative_realized=…)` | `max_cumulative_loss_usd` = **20** | `self.store.realized_today()` | `l5_scoped` |
| 3818 | `ValidationGates.refuse(cumulative_realized=…)` | `max_cumulative_loss_usd` = **20** | `self.store.realized_today()` | `l5_scoped` |
| 3850 | `SafetyLimits.refuse(realized_today=…)` | `daily_realized_loss_halt_usd` = **5** | `self.store.realized_today()` | `l5_scoped` |

Line numbers are at `e42c3ef1`; the exact strings are
`cumulative_realized=self.store.realized_today()` (twice) and
`realized_today=self.store.realized_today()` (once), and a test counts them,
so a partial application fails the suite.

### The approval action

This repository defines no security-review mechanism: no `CODEOWNERS`, no
`SECURITY.md`, no `CONTRIBUTING`, and `.github/` holds only `workflows/`. The
required action is therefore:

> **A human with write access applies the diff above directly** -- in their own
> checkout on `claude/gemini-btc-economic-viability-h3u7zl`, or through
> GitHub's web editor -- and pushes it.

Claude cannot: the harness classifier refuses the edit on the *content* of the
change, so switching tools would defeat the denial's intent, and the owner has
ruled out changing tool permissions. The rejected edit has not been attempted
again.

### What to verify after applying it

1. `python3 -m pytest $(grep -v '^#' tests/MONEY_PATH_GATE.txt | grep -oE 'tests/[A-Za-z0-9_./]+\.py' | sort -u) -q` -- green at **1302** before the change.
2. `tests/test_l5_four_bucket_accounting.py` -- note that
   `test_gate_input_changes_no_policy_by_itself` asserts the OLD call sites and
   **must be updated as part of the change**, not deleted: it should assert the
   three sites now read `gate_input("verified_l5")`.
3. A new regression asserting `l5_scoped is None` refuses at **all three**
   sites -- the property whose absence would make a partial rescoping look
   complete.
4. Deploy with `L5_SHADOW_ONLY` still set and read the heartbeat's
   `accounting` block before any separate arming decision.

### What this does NOT do

It does not unblock the bounded live run on its own. After it, the remaining
blockers are the shadow lock, the latched HALT, and the two risk limits
(`L5_MAX_LIVE_ORDERS`, `L5_MAX_CASH_DEPLOYED_USD`) -- each a separate explicit
owner authorization. And it leaves manual trading with no realized-loss
ceiling, standing instead behind the `$5` account-wide worst-case exposure
ceiling, which genuinely aggregates every market as of `f69427f0`.
