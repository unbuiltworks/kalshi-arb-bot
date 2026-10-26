# Deployment-timing safeguard for bounded live campaigns

**Status: documentation and an advisory script only. Nothing here is wired
into `l5-pmus-live`, imported by the runner, or copied into the image.** The
Dockerfile's COPY set is `src/` (53), `config/` (4) and one `analysis/` JSON,
so `analysis/*.md` and `scripts/` never enter the deployed image.

## The failure mode

A campaign window claim is **irreversible**. `WindowLedger.reserve` is not
refundable, `retire_settled_market` is the only thing that frees a campaign,
and `l5_live_authorization` records that "one authorization trades once. A
new probe needs a new session id." A claim is also never cleared on
shutdown, by design: clearing it would permit replay.

Therefore a configuration rollout that swaps the container **while a process
holds a claim that could still have traded** destroys that window's
authorization for nothing. The campaign loses a window and gains no data.

This is not hypothetical. On 2026-10-26, in one 8-window campaign:

| window | what happened | cost |
|--------|---------------|------|
| 1945z | process armed 19:50:27; a redeploy to apply a no-op variable killed it mid-window; the replacement landed at T+348 and refused `INSUFFICIENT_ENTRY_RUNWAY:192.0<300.0` | 1 window |
| 2030z | claimed 20:30:01 by a process still carrying a misconfigured loss ceiling, which refused every pair `13.64050000>=10`; the corrected build landed at T+243.3 and refused `INSUFFICIENT_ENTRY_RUNWAY:297.0<300.0`, missing the deadline by 3.3s | 1 window |
| 2100z | variable applied 20:59:16, 44s before the boundary; old container removed 21:00:50, 50s *after* it. The replacement won the claim and traded the window. **Survived on timing luck, not by design.** | 0 windows |

Three rollouts, two windows destroyed, and the third saved by a margin of
seconds. 25% of an 8-window authorization was consumed by rollout timing
rather than by market conditions.

## Why existing protections do not cover it

`window_eligible` already refuses to *claim* a window that cannot be used
(`MIN_ENTRY_RUNWAY_S = 300.0`, measured against the venue's own `t_end`
rather than `start + WINDOW_S`). That is the right guard and it worked
correctly in both failures above — it is what emitted the two
`INSUFFICIENT_ENTRY_RUNWAY` refusals.

But it protects the *new* process from wasting a claim it cannot use. Nothing
protects an *already-held* claim from the rollout itself. The guard fires
after the damage, because by then the claim is already spent.

## The rule

A rollout's container swap must satisfy **both** conditions:

1. **Land after the current window's entry cutoff.** Before it, the running
   process could still open a position, so killing it forfeits a tradeable
   window. The entry cutoff is composite and venue-dependent: `entry_cutoff_s`
   is 600 but `may_enter` stopped at ~T+540 on 2030z and ~T+565 on an earlier
   0000z, because `t_end` comes from the venue and can precede
   `start + 900`. Treat **T+540** as the conservative cutoff.
2. **Complete before the next window's claim moment, with margin.** Observed
   healthy rollout latency on this service is 90-140s (`4753571f` 90s,
   `ed919f9c` 140s); a failed snapshot fetch ran 312s. Require margin
   >= 300s.

For 900s windows that yields a safe start band of **T+540 to T+600**, i.e.
**9:00-10:00 past each quarter hour**, which leaves >= 300s before the next
boundary. Never roll out in T+0 to T+540.

My 20:59:16 rollout was at T+796 of 2045z: it satisfied condition 1 and
violated condition 2, landing 46s past the next boundary. That is precisely
the case the second condition exists to forbid.

## Advisory check

`scripts/l5_rollout_window_check.py` is pure arithmetic over the wall clock
and the window cadence. It imports nothing from the money path, touches no
ledger, and returns GO/NO-GO plus the next safe band. Run it before any
configuration change during a live campaign.

    python3 scripts/l5_rollout_window_check.py
    python3 scripts/l5_rollout_window_check.py --at 2026-10-26T20:59:16Z

## The durable fix, not proposed for now

The rule above is operational discipline; it cannot prevent an operator
mistake, only make it visible. The durable fix is **claim adoption**: let a
new process inherit an existing claim when the mandate session matches, the
prior holder is demonstrably gone (`holder.boot_id` / `replica_id` differ and
the deployment is REMOVED), and **no submission was ever charged under that
claim**. That last condition is what keeps it from becoming replay: a claim
that has already put an order on the wire must stay spent.

This touches `l5_live_authorization.authorize_arming`, which is money-path
authorization on a service that can move real money. It should not be written
against a live campaign with 2 windows remaining, and it needs its own
review, adversarial tests, and an owner decision. Recorded here so the option
is not lost.
