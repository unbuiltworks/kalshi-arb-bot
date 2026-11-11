# Non-latching rehearsal, and closing the emergency-cancellation gap

**Executor disarmed throughout. No latch asserted, no nonce consumed, no
valid marker published, no live orders, no new services, no credentials
created.**

---

# Part 1 — the non-latching rehearsal

## The configured endpoint does not exist yet

`l5-halt-watchdog`'s variables hold **no** `L5_EMERGENCY_HALT_URL`,
`L5_EMERGENCY_HALT_SECRET` or `L5_EMERGENCY_HALT_SERVICE`, and
`sealedVariableNames` is empty. So there is no configured marker endpoint to
test against, and `E.configured()` returns False — the running watchdog is
not polling any marker at all today.

I also checked whether I could measure the GitHub CDN hop without owner
setup. I cannot: the repo is private, so
`raw.githubusercontent.com/unbuiltworks/kalshi-arb-bot/...` returns **404**
unauthenticated. **That hop still needs your Gist**, which is why the
browser-only steps below are where I stop.

## What I did measure

Against the real `MarkerSource`, real `evaluate()`, real `check()` and a real
HTTP server — with a **deliberately invalid HMAC** and an ephemeral
per-run secret that was discarded:

```
P1 endpoint 404        acted=False reason=MARKER_UNREACHABLE
P2 published at        t=0.000s  (invalid hmac, command=HALT)
P2 observed            t=0.001s  reason=MARKER_SIGNATURE_INVALID acted=False
P2 verdict             {"act": false, "command": "HALT",
                        "nonce": null, "reason": "MARKER_SIGNATURE_INVALID"}
P3 latch kind          ABSENT
P3 halt value          None
P3 acted nonces        []
```

**`MARKER_SIGNATURE_INVALID` confirmed, and nothing latched.** Three things
worth naming:

- **`MARKER_UNREACHABLE` is now actually verified.** Last turn I reported
  only `MARKER_MALFORMED` from my stub and said so; phase 1 exercises the
  real unreachable path and returns the right reason.
- **`nonce: null` on a bad signature.** The nonce is not even extracted
  before the HMAC is checked, so a refused marker cannot burn a nonce. The
  `acted` list stayed empty, which is the property that matters: a rehearsal
  cannot consume the nonce a real marker would need.
- **`command` is reported as HALT and still refused.** Every field was
  correct except the signature, so this exercised the signature gate rather
  than an earlier reject.

**Fetch + evaluate: 0.001s on loopback.** Worst-case watchdog observation is
therefore `poll_s + fetch ≈ 20.0s`, plus internet RTT and **plus the
unmeasured GitHub publish-to-CDN delay**. I am not going to turn 1ms of
loopback into a claim about the real path.

## Owner setup — browser only, and this is where I stop

1. **gist.github.com** → new **secret** gist, filename `halt.json`, content
   `{}`, Create.
2. Click **Raw**, copy that URL.
3. Generate a secret — any password manager's 32-byte hex, or locally:
   ```
   openssl rand -hex 32
   ```
4. Railway dashboard → `l5-halt-watchdog` → **Variables**, add three:

   | name | value |
   |---|---|
   | `L5_EMERGENCY_HALT_SECRET` | the hex from step 3, **marked SEALED** |
   | `L5_EMERGENCY_HALT_URL` | the raw URL from step 2 |
   | `L5_EMERGENCY_HALT_SERVICE` | `l5-pmus-live` |

No CLI, no file signing, no secret in chat. Once those exist I can rerun
phase 2 against the real endpoint and give you the true end-to-end number —
still without latching anything.

---

# Part 2 — the emergency-cancellation gap

## The gap

The executor cancels only **while armed**. The watchdog cancelled only orders
older than `max_order_age_s`, **300s** in production. So a HALT asserted while
a fresh order rested left that order live for up to five minutes, free to
fill and open a residual with nothing managing it.

## The patch: a separate immediate path

**+99 lines in `src/l5_watchdog.py`, mostly comment.** Two parts:

**`Watchdog.emergency_state() -> (bool, reason)`** reads the same latch
record the executor's gate reads, so the two cannot disagree about whether
the system is stopped.

**A branch in `sweep()`**, placed right after the open-orders read and before
the age logic: when a halt is latched, every non-terminal known order becomes
a target regardless of age, each tagged `selected_by:
"EMERGENCY_HALT_LATCHED"` and still carrying its `age_s` — reported, but not
a condition.

**Why a separate path and not a smaller deadline:** lowering
`max_order_age_s` would cancel legitimately resting maker orders during
normal trading, which is the one thing it must not do. Requirement 7 holds —
**the age-based path is byte-for-byte unchanged.**

## Preserved, by construction rather than by assertion

I extracted the existing cancel loop into **`_cancel_targets(targets, out)`**
and both paths call it. A test walks the AST and asserts exactly two callers
and exactly one implementation, so exact-venue-ID cancellation, the
independent venue re-read, the ownership checks and UNCERTAIN handling
**cannot drift apart** between the two paths. The emergency path chooses
*which* orders; it has no say in whether any of them may be cancelled.

The emergency branch also **never re-asserts and never attests**:
re-asserting would overwrite the original reason and evidence — the record of
why the system stopped — and attesting would clear the latch.

## One judgement call, and I got it wrong first

My first version treated an **unreadable** latch as an emergency, failing
closed. Two tests with stub asserters caught it, and they were right to:
`.store` was missing, the read raised, every sweep became an emergency, and
because the emergency branch does not re-assert, it would have **cancelled
the book without ever latching a halt** — losing the record of why.

So the trigger is now a **confirmed** latch only. The deeper reason is that
cancelling is not purely risk-reducing here: **pulling a resting COMPLETE leg
strands the residual it was about to hedge**, which is exactly the 1415z harm
shape. Firing that automatically on every transient GET failure is a worse
failure mode than the gap it closes.

Nothing is lost by waiting for a readable answer: the executor's gate already
refuses to trade on an unestablished latch, the watchdog re-reads every
sweep, and the age-based path still catches the order on its own deadline.
The cost is bounded delay, reported as
`HALT_LATCH_UNREADABLE_NOT_EMERGENCY` rather than silent.

**One honest limitation.** The `EXECUTOR_REPORT_STALE` and
`UNMANAGED_RESTING_ORDER` branches assert their halt and return without
emergency-cancelling in that *same* sweep. The next sweep sees the latch and
cancels the whole book, so those two close **one sweep later (≤20s)** rather
than instantly. I left them alone deliberately: widening them would have
meant touching the age-based path.

## Tests — 20 new, all five required cases

`tests/test_l5_emergency_cancel_all.py`:

| case | test |
|---|---|
| **fresh orders** | a 5-second-old order under a 300s deadline is cancelled; **and** is left alone with no latch (parity); all three resting orders targeted, not just the old one; terminal rows never targeted |
| **partially filled** | cancelled with `cum_qty` surfaced, because the filled part is now someone's to reconcile; an order that fills completely mid-cancel is terminal, not a failure |
| **failed cancellations** | a refused cancel that leaves the order live reports `still_open`, never `cancelled` — the ack decides nothing, the re-read does; an unreadable outcome is `unverified`; one failure does not abandon the rest of the book |
| **executor unavailable** | a stopped executor's orders are cancelled without waiting 300s; an unreadable venue cancels nothing and claims no safety; credential-free mode says **STILL RESTING** rather than reading as success; an empty book is reported, not silent |
| **unknown ownership** | not in the registry → never cancelled (on a shared account that is your manual order); `VENUE_SAYS_MANUAL` → never; `MARKET_MISMATCH` → never; an empty registry distinguishes "we own nothing" from "we could not find out" |

Plus: the latch's original `reason` and `who` survive a sweep; an unreadable
latch is not an emergency; and the AST test above.

## Baseline parity

**1051 passed** across the watchdog, halt, reset, marker and cancel suites
(`-k "watchdog or halt or cancel or marker"`), the 20 new tests among them.
The narrower `-k "watchdog or halt"` selector is **542 passed**. A full-suite
run over all ~14,400 tests was still in flight when this was written; the
number it returns is reported separately rather than assumed here.

Two existing tests changed, both on a confirmed latch, and the change is a
strengthening: they asserted that `heartbeat_no_halt` *refuses* over a latch;
the sweep now never reaches the heartbeat at all. The direct guarantee is
independently covered without a watchdog at `tests/test_l5_halt_gate.py:133`
and in `test_l5_ported_safety_fixes.py`, so no coverage was traded away. Both
tests keep their real assertion — `submissions_permitted() is False`.

### Full-suite parity — proven by differencing, not asserted

I ran the whole suite twice: once on this tree, once on a clean worktree at
the pre-change commit `27d75333`.

| | pre-change `27d75333` | this tree |
|---|---|---|
| passed | 14,814 | **14,834** (+20, exactly the new tests) |
| failed | 54 | **54** |
| errors | 23 | **23** |
| sha of the sorted failing-test id list | `b70451e1…` | **`b70451e1…`** |

**The failing-test id sets are byte-identical.** `comm` finds nothing in
either direction: no test fails now that passed before, and none passes now
that failed before. **Zero regressions.**

Those 54 failures and 23 errors are pre-existing and unrelated to L5 live
execution — concentrated in `test_gemini_terms_gate.py` (17),
`test_pmus_status_live_handoff.py` (14), `test_spread_scanner.py` (9),
`test_gemini_request_transport.py` (9), mostly missing optional dependencies
such as `httpx`. They are a repo-health item, not this patch's, and I am
naming them rather than leaving them behind a green subset. Three further
files were excluded at collection for the same `httpx` import error.

**Correction to my own earlier reporting:** in prior turns I described
"baseline parity" on the strength of subset runs. A subset cannot establish
parity. This differenced full-suite run is the first thing in this workstream
that actually does.

## Deployment authorization IS required

`l5-halt-watchdog` deploys from branch
**`claude/gemini-btc-economic-viability-h3u7zl`** with watchPatterns
`deploy/l5_halt_watchdog/DEPLOY_TRIGGER`. This work is on
`claude/compassionate-allen-wic64u`. Promotion therefore needs a **cherry-pick
onto the watchdog branch**, and your previous grant was scoped to one
specific commit. **I have not deployed and have not touched either
deployment.** Say the word and I will cherry-pick this commit only, run the
watchdog suites on that branch, and promote via DEPLOY_TRIGGER.
