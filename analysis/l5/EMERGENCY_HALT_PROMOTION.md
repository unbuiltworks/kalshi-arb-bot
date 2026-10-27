# Promoting the owner emergency-HALT into the deployed watchdog

Branch `promote-emh`, cut from `origin/claude/gemini-btc-economic-viability-h3u7zl`
at `e778e866` -- the exact commit the deployed watchdog follows.

## 1. The deployed target, verified from Railway

    service      l5-halt-watchdog  (fb038b8c-c7c1-4599-b06a-54f72bad544f)
    project      arb-ev-wallet-shadow / production
    repo         unbuiltworks/kalshi-arb-bot
    branch       claude/gemini-btc-economic-viability-h3u7zl   (NO commitSha pin)
    dockerfile   deploy/l5_halt_watchdog/Dockerfile
    watchPatterns deploy/l5_halt_watchdog/DEPLOY_TRIGGER
    startCommand python3 -m scripts.l5_halt_watchdog
    volume       /data          state_dir /data/live
    poll_s       20.0           mandate_doc_max_age_s 900.0
    staged       null

The watchdog follows the branch head but builds only when DEPLOY_TRIGGER
changes. Both facts matter to the plan in section 6.

`e778e866` contained none of the three emergency-halt files.

## 2. The trust model, resolved

HMAC-SHA256, which is SYMMETRIC. The watchdog cannot verify without holding
`L5_EMERGENCY_HALT_SECRET`. There is no version of this design where the
secret stays off the deployed service, and the earlier claim that it could
was wrong.

What the symmetry does and does not cost:

* The verifier module exports `sign()`, so the watchdog could in principle
  mint its own marker. This grants it nothing: it already holds
  `HaltAsserter` and can latch directly. No escalation.
* A forger who obtains the secret can only ever assert HALT. `COMMAND` is
  `HALT` and nothing else parses, and `HaltAsserter` has no clear by
  construction. The worst outcome of a compromised secret is that we stop
  trading, which is the safe direction.
* The signer `scripts/l5_emergency_halt_sign.py` is in NO image. Every
  Dockerfile in the repo uses explicit per-file COPY -- there is no
  `COPY scripts/` wildcard anywhere -- so this is enforced repo-wide, not
  just for the watchdog.
* The executor image carries neither the signer nor the verifier. It learns
  of a halt only through the attestation it already reads.

`service` is one of the five SIGNED_FIELDS, so a marker is bound to one
deployment and cannot halt an unrelated service reading the same URL.

## 3. What each state does

    MARKER_UNREACHABLE          changes nothing. Asserting on a failed read
                                would latch on every transient network error.
                                The executor is failed closed by a DIFFERENT
                                mechanism when the watchdog goes quiet.
    MARKER_SIGNATURE_INVALID    refused
    MARKER_WRONG_SERVICE        refused
    MARKER_COMMAND_NOT_HALT     refused -- there is no resume path
    MARKER_STALE  (> 86400s)    refused BY NAME and surfaced
    MARKER_FUTURE_DATED         refused beyond 120s skew
    MARKER_ALREADY_ACTED        nonce replay record on the watchdog volume
    MARKER_LATCH_FAILED         surfaced, never counted as success

Detection latency: one poll, so <= 20s, worst case ~23s with the 3s fetch
timeout. That is far inside the 900s mandate-document staleness bound, so
the added fetch cannot starve the sweep into staleness.

## 4. Credential hygiene

No secret, and no signature, ever reaches the emitted record. Fetch errors
are logged as `type(exc).__name__` only, never `str(exc)` -- so a marker URL
carrying a token cannot leak through an exception message.

## 5. A finding in the promoted module: one comment and one test are wrong

`_acted()` returns the sentinel `["__unreadable__"]` when the nonce record
cannot be read, and its comment says:

    # UNREADABLE IS TREATED AS "EVERYTHING ACTED": the only consequence
    # is that we do not latch again ...

That is not what the code does. The replay check is
`nonce in set(acted_nonces)`, and a genuine nonce is not the string
`__unreadable__`, so it does not match. Measured directly:

    unreadable store + FRESH real nonce -> acted = True
                                           reason = HALT_LATCHED_FROM_OWNER_MARKER

So an unreadable nonce record does NOT suppress a real owner halt. The
actual behaviour is the safer of the two, and safer than the comment claims:
a corrupt bookkeeping file cannot silence the owner's emergency stop. What
is lost is replay suppression, and a replayed marker can only re-latch HALT,
whose sole cost is overwriting the original halt's evidence.

`test_an_unreadable_nonce_record_refuses_rather_than_re_latching` passes
only because it signs a marker whose nonce is literally `"__unreadable__"`
-- it matches the sentinel against itself. It therefore asserts nothing
about the case its name describes, and is vacuous.

No safety hole, so this does not block promotion. But a safety module
should not ship a comment that states the opposite of its behaviour, nor a
test that appears to cover a case it does not. Proposed, pending approval
and not applied: correct the comment to describe the real behaviour, and
re-point the test at a genuine fresh nonce asserting `acted is True`.

## 6. Deployment plan

Nothing here arms anything. The promoted code is INERT until configured:
`from_env` returns None unless BOTH `L5_EMERGENCY_HALT_URL` and
`L5_EMERGENCY_HALT_SECRET` are set, and the watchdog service currently has
neither. A half-configured mechanism is off by design, because a URL with
no secret would accept an unsigned marker.

    step 1  merge promote-emh into claude/gemini-btc-economic-viability-h3u7zl
    step 2  touch deploy/l5_halt_watchdog/DEPLOY_TRIGGER in the same push --
            without it watchPatterns suppresses the build and the branch head
            moves with no deployment, which is exactly the 06:37->11:09
            failure mode from the campaign that did not activate
    step 3  wait for deployment SUCCESS, then confirm RUNTIME ADOPTION by
            reading L5_EMERGENCY_HALT_MARKER_CONFIG in the logs. Expect
            configured=false at this point -- that is the proof the code is
            live and correctly off
    step 4  owner sets L5_EMERGENCY_HALT_SECRET and L5_EMERGENCY_HALT_URL,
            publishes a signed marker, and we confirm the latch end to end
            while no orders are live
    step 5  only then resume the eight-window programme

Treat deployment creation, deployment SUCCESS and runtime adoption as the
three distinct states the activation procedure already requires. Step 3 is
the runtime-adoption check; an acknowledged variable write is not proof.

## 7. Invariant held throughout

Verified on the live executor during this work:

    campaign.approval_present  false
    armed                      false
    owner_arm_token            false
    live_orders                0
    halt_latch                 CLEAR
    realized_total             -13.63250000   (unchanged)

No deployment was made, no variable was written, no campaign was armed.

## 8. Which commit is the deployable artifact, and why not this branch

The deployable artifact is `53756aaf` on local branch `promote-emh`, cut
from `e778e866` -- the exact commit the watchdog service is running.

It is NOT this branch. `claude/l5-wallet-ingestion-fix-kqcqoa` already
carries the three emergency-halt files, the watchdog wiring and the
Dockerfile COPY, so on this branch the change looks already done. But its
`scripts/l5_halt_watchdog.py` is an older lineage:

    _first_publish occurrences
      e778e866  (what the watchdog is running)   4
      6e6036d1  (this branch)                    0

Promoting this branch's watchdog script would silently revert the first-
mandate-publish observability that `e778e866` added. That is why the patch
was rebuilt by hand onto the deployed commit instead of taking the dev
branch's file wholesale, and why the Dockerfile COPY -- already present
here -- was the only line the cherry-pick onto this branch produced.

Pushing `promote-emh` to the remote needs your go-ahead, since it is not
the branch assigned to this session.
