# CP1 activation — HALT removed

**OWNER AUTHORIZATION 2026-09-16, "final CP1 activation".** The HALT latch is
removed and the bounded three-market continuous live probe is armed.

## The authorized envelope, verified at runtime before removal

Deployment `3dc110e7`, commit `78ac58e6b76d`, halted disclosure at 20:51:54Z:

| | authorized | runtime |
|---|---|---|
| per-market spend | $5.00 | `effective_per_market_cap_usd "5.00"` = min(rung $5.00, mandate $5.00) |
| total campaign spend | $15.00 | `max_total_spend "15.00"` |
| residual | $3.00 / 3 contracts | `effective_residual_cap_units 3` |
| concurrent markets | 1 | `max_markets 1`, rung `concurrency 1` |
| total markets | 3 | `max_total_markets 3`, `markets_per_campaign 3` |
| daily realized-loss stop | −$6.00 | `effective_daily_loss_stop_usd "-6.00"` |
| mandate expiry | 24 h | `2026-09-17T20:36:35Z` |
| k | 0.00619 unchanged | never touched |
| CP1 sizing | 0.10 / 0.125 | `target_notional_usd "0.10"`, `max_leg_notional_usd "0.125"` |

`after_restore` showed `pinned_rung CP1`, `stored_rung R3L`,
`effective_rung CP1`, `tightened_by_stored []`, **`no_dimension_loosened true`**
— B3 resolving the volume's stale R3L without loosening anything.

Account immediately before removal (20:56:58Z): `SHUTDOWN_VERIFIED`,
`sweep.seen 0`, `still_open_ours []`, `foreign_open []`, `held_preserved []`,
`refusals 0`, realized today −$0.0916 against a −$6.00 budget. **Flat, zero
open orders.**

## The security test was restated, not removed

`test_the_repo_is_currently_halted` asserted `HALT.exists()` unconditionally,
and it fired the moment HALT was removed — which is what it was for. The
removal stopped there until the owner authorized this specific change.

It is now `test_the_halt_and_the_authorization_are_consistent`, a consistency
invariant that is **stronger** than what it replaced:

- **HALT present → must be halted.** Always a safe state.
- **HALT absent → requires a committed, parseable, unexpired mandate naming
  exactly `l5-r1-probe`.**

The original only asserted a file exists; it would have passed again for
anyone who re-added HALT regardless of whether any authorization existed, and
said nothing about the mandate. This one ties the two together in the
dangerous direction.

**Absence of HALT is never authorization on its own** — stated as its own test.

The rule lives in `activation_authorized()` so the live tree and the
adversarial cases are checked by the same code. All seven cases the owner
enumerated pass:

| HALT | mandate | result |
|---|---|---|
| present | none | halted ✓ |
| present | valid | halted ✓ |
| absent | none | **fail** — `no_halt_and_no_mandate` |
| absent | malformed | **fail** — `mandate_unusable` |
| absent | expired | **fail** — `mandate_expired` |
| absent | wrong service | **fail** — `mandate_wrong_service` |
| absent | valid exact CP1 | permitted ✓ |

Nothing was deleted, skipped, xfailed, or disabled. Full regression suite:
**4959 passed, 27 skipped, 1 xfailed, 0 failed.**

## What still stops this probe

Removing HALT un-halts the process; it does not widen anything.

1. **The wire guard.** Every `POST /v1/orders` is admitted against the
   immutable mandate by economic effect, not by stage name.
2. **`min(rung, mandate)`** — a stale rung can never raise the envelope.
3. **B3** — stored state can only tighten the pin, never replace it.
4. **The mandate expires** in 24 h and is re-read every campaign pass, so an
   unattended process stops itself when its authorization runs out.
5. **The HALT latch can be re-committed at any time** and is read before
   `armed()` on the next boot.

## Accounting labels for this probe

**ARMED → SUBMITTED → FILLED → SETTLED → REALIZED**, reported separately. A
fill is not realized profit. A settlement without the venue's confirmed
cashflow stays `SETTLED_REALIZED_UNCONFIRMED` and out of every net.

---

# Activation result — ARMED, SUBMITTED, no fills, stopped. HALT re-committed.

**Deployment `51114cbc`, commit `7944bfa697e8`, SUCCESS 21:13:17Z.**

## Lifecycle, reported strictly

| stage | result |
|---|---|
| **ARMED** | 21:13:02Z, shell `CP1/cap5.00/residual3/conc1/maker-only/fee-safe0.98/rest30s/action0.10` |
| **SUBMITTED** | **6** maker orders on `aec-dota2-yes-tm-2026-09-16`, each **qty 0.22** |
| **FILLED** | **0** — all six `no_fill_cancelled` |
| **SETTLED** | none from this probe |
| **REALIZED** | **none. $0.00 moved.** Inventory zero, `spent "0"` |

The order size is exactly the authorized CP1 sizing: 0.22 contracts, which is
what `$0.10` target / `$0.125` per-leg yields at a ~$0.45 touch. The sizing fix
worked. `cap_check` on the live path recorded `proposed_notional $0.078000`
against `cap $5.00`, `remaining_after $4.922000`.

Every limit held. Nothing approached a cap: 6 of 6 allowed actions used, $0 of
$5.00 spent, 0 of 3 residual units, 1 of 1 concurrent market, 1 of 3 total
markets, realized $0.00 against a −$6.00 stop.

## The material discrepancy

Session 3 selected a second market and the **wire guard refused**:

```
mandate_admission  admits=false  reason=max_concurrent_markets_exhausted
                   markets_open=1  max_markets=1
session_end        status=HALT  reason=MandateRefusal:max_concurrent_markets_exhausted
campaign_end       status=CAMPAIGN_STOP:HALT  sessions=3  filled_markets=0
                   next="none: this status is an owner decision or a stop, and
                         is never auto-restarted"
```

**`consumption()` counts a market as OPEN from the moment an order is ADMITTED
— a commit row — not from a fill.** Six admitted-but-unfilled orders made
`markets_open = 1`; with `max_markets = 1` the next market is refused. An
unfilled market never settles, because there is nothing to settle, so it never
leaves the open set.

**The three-market probe cannot advance past the first market it touches.**
That is terminal, not transient. A second effect compounds it: the campaign
treats a `MandateRefusal` as a session HALT that ends the whole campaign,
rather than waiting for the open market to resolve.

This is **fail-closed** — it stopped rather than over-trading, no money moved,
the account is flat. But the authorized experiment cannot run as designed, and
every fix touches how the mandate counts open markets, which is a safety
mechanism. The owner's instruction for this case is explicit: *"HALT
immediately and report it rather than modifying another safety mechanism."*

**HALT re-committed 21:15Z. No further orders are possible.**

## What would make the probe runnable — for the owner to choose, none started

1. **Count a market as open only when it holds inventory or a live order**,
   rather than from first admission. Most faithful to "concurrent markets", but
   it changes `consumption()`, which is the wire guard.
2. **Release a market from the open set when it ends flat with zero fills.** A
   narrower version of (1), still a guard change.
3. **Raise `max_markets` to 3** so concurrency equals the total allowance. The
   smallest change and it touches no code — but it authorizes three genuinely
   concurrent markets, which is a real risk increase and not what was
   authorized.
4. **Accept one market per campaign** and re-arm per market. No code change;
   each activation is its own reviewed commit. Slowest, but strictly within the
   current envelope.

The no-fill outcome is itself information: six maker orders at the fee-safe
bound rested and were cancelled without a fill. That is consistent with the
shadow, where `residual_cap` forcings dominate and fills are theoretical, and
it is the first live evidence that the CP1 size posts and rests correctly.
