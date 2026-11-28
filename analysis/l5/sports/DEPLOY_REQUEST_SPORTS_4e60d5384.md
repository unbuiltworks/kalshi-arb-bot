# Deploy request — sports bot `4e60d5384` (audit protection), locked

23 Nov 2026, 20:20 UTC. **Not approved, not deployed.** A locked,
disarmed deployment only. It places no orders. It does not arm, reset a
halt, raise a limit or change any risk setting other than the two new
protections below.

## What is deployed

| | |
|---|---|
| Service | `pmus-spring-l5-probe` |
| Branch | `release/pmus-sports-rn1-4e60d5384` |
| Commit | `4e60d5384a5eeab383f1bcee2f25473b92cf81a3` |
| Code | `0ebb4a14a` (two commits on top of the running `c337c3aef`) |
| Lock | stays `1` (plain lock) before, during and after |

Changes since the running build:
- The account audit, the position reconciliation and the reward read take
  a reserved class in the request budget. While one is waiting, order
  placement stands aside. Cancels and order-status reads are untouched.
- An audit or reward read that did not complete is retried after 60 s.
- New, set in this deployment: `PMUS_TWO_SIDED_AUDIT_MAX_AGE_S=4500`. No
  completed audit in 75 minutes (none since boot counts) blocks new
  exposure. Completions and cancels are not blocked.
- New, set in this deployment: `PMUS_TWO_SIDED_SETTLE_CHECK_S=120`. The
  loop records the venue's public final settlement price for closed
  markets (a keyless read, at most two per pass). Only a final price
  releases a market from the $16 spend ceiling; a balanced position does
  not.
- Heartbeat reports four capital states separately (resting orders,
  unpaired inventory, paired awaiting settlement, settled) and the age of
  the last completed audit.

Unchanged: every limit ($16 / $6 / $8 / $3, $4 loss stop, $5 daily halt,
two contracts, maker-only, 0.97 pair cap, 10-contract depth, international
price guard), the watchdog, the ledger, the shared halt.

## Tests

- New file `tests/test_pmus_sports_audit_starvation.py`: 25 tests.
  - the failure as it happened (empty bucket refuses an ordinary read,
    serves a safety read);
  - continuous order traffic on three threads does not starve the audit;
  - cancels never wait behind a safety read;
  - the order pass that runs between audit requests keeps its own class;
  - retry within 60 s, never postponing a sooner re-check;
  - the 23 Nov timeline simulated second by second: audit at boot, the
    next one refused, retries every minute, block at exactly 75 minutes,
    lifted by one clean audit;
  - restart: a new process is blocked until its own first audit
    (about two minutes after boot);
  - a stale audit pulls openings and keeps the maker completion;
  - settlement: balanced is not released, a final price is, an open
    market is never looked up.
- Each safeguard was switched off in turn; tests failed each time.
- Full suite on `0ebb4a14a`: 5,330 passed, 24 failed. The 24 are the same
  tests that fail on the running build.
- Container file set: imports the same as the running build.

## What the 75-minute limit means in practice

- Audits are attempted two minutes after boot and then hourly, so a healthy
  bot is never more than about 62 minutes from a completed audit.
- One missed audit is retried every minute for about 13 minutes before the
  limit bites.
- Every restart (including every arming, which restarts the service) starts
  blocked for about two to four minutes until the first audit completes.
  A window should open with that in mind.
- The existing 2 h 10 min loss-meter limit stays as a second, slower stop.

## Verification after deploying (read-only, bot locked)

1. Deployment `SUCCESS` on the commit above; boot line shows locked for new
   exposure and completions.
2. `ACCOUNT_AUDIT ok=true` within about five minutes of boot, and
   `INVENTORY_RECONCILE` present.
3. Heartbeat `audit.last_complete_age_s` is a number and
   `stale_blocks_new` is false after that audit; `capital` block present.
4. `SETTLEMENT_RECORDED` lines for the 23 Nov tennis markets once the
   venue shows them final.
5. No order, no error, no safety latch; watchdog sweeps unchanged.

What this cannot verify while locked: the reserved class under real quote
traffic. That is first seen in the next authorised window, and the first
hourly audit of that window is the check.

## Rollback

Reconnect the service to `release/pmus-sports-rn1-62dca41cd`, commit
`62dca41cd7167fab117753a2f010fddffd95d55c` (the build that ran on 23 Nov).
The two new variables are ignored by that build. Lock stays `1` throughout.
Each step is a redeploy and took 6 to 15 minutes on 23 Nov.

## Approval needed

Deploy `4e60d5384` locked, with `PMUS_TWO_SIDED_AUDIT_MAX_AGE_S=4500` and
`PMUS_TWO_SIDED_SETTLE_CHECK_S=120`. Nothing else.

## Deployed and verified — 23 Nov, 20:45 UTC (owner: "Deploy locked")

| Check | Result |
|---|---|
| Running commit | `4e60d5384a5eeab383f1bcee2f25473b92cf81a3`, deployment `d7dcd4bf`, container started 20:37:25 (source switched about 20:28; the platform created the deployment at 20:36:31) |
| Lock | new exposure locked, completions locked, reason "locked" |
| Orders | none since 18:56:56; watchdog open orders 0 on every sweep since 18:59:36; ledger reconcile clean, 0 open orders at the venue |
| Before the first audit | heartbeat showed no completed audit and new exposure blocked by the stale-audit rule, as designed |
| First audit | completed 20:41:06, 3 min 41 s after start: reward read ok, position reconciliation clean (no mismatch, walk complete), account audit ok |
| Reserved request class | 87 safety requests sent, 0 deferred |
| Heartbeat | audit age reported, limit 4500 s, not blocking; four capital states present (all campaign capital shown as settled, $6.67) |
| Errors | none; no safety latch |

Campaign of 23 Nov, from this audit (venue rows): 9 fills, 14 contracts,
7 pairs, realized net +$0.33, no fees, no rebates, nothing unconfirmed,
cash change fully explained (unexplained $0.0000). Ledger has booked
+$0.29 (Strombachs–Perez +0.10, Debru–Kopp +0.19); Karpol–Benwei (+0.04)
is not yet booked there.

Rewards: no row dated 23 Nov yet. All-time on the account: paid $22.79,
pending $1.34, skipped $3.96.

Open observations, none a risk while locked:
- The bot tracks five markets (soccer, NHL, ATP) carried over from the
  campaign although the allowed list is back to NFL and NBA. Every one is
  blocked by the lock. Whether the allowed list alone would stop a bid on
  them has to be confirmed before any arming.
- No `SETTLEMENT_RECORDED` line: the three markets' settlement prices were
  already recorded by the previous build's audits. The loop-side check is
  therefore not yet seen working in production.
- The reserved class has not yet been seen under quote traffic.
