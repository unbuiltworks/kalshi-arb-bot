# Sports bot release checklist (permanent)

Applies to every release of `pmus-spring-l5-probe`. Added 27 Nov 2026 after
`1c1d546b6` shipped an image without three lazily imported modules
(STANDING_AUTHORIZATION_20261122.md §75a). Repository tests passed; the
image did not.

## Before requesting authorization

1. Exact SHA and branch recorded. `src/` diff stated against the running
   release.
2. Full regression flags off, and the enabled-mode safety module under the
   activation config.
3. **Image contents check (repository):**
   `tests/test_pmus_spring_image_modules.py` passes. Every src module that a
   copied src file references, at module scope or lazily inside a function,
   is itself copied by `deploy/pmus_spring_l5/Dockerfile`.
4. **Image import check (built image):** build the Dockerfile and, inside
   the image, import every entry module with the activation flags on:
   `src.pmus_l5_continuous_session`, `src.pmus_sports_two_sided`,
   `src.pmus_rn1_replica` and each module a new flag imports. Where no
   container runtime is available to the session, say so in the packet. The
   built-image check then happens in production under step 6, with the lock
   held.

## Deploy (lock held at `1` throughout)

5. Set the activation variables with the lock at `1`, then pin the source to
   the exact SHA.
6. **Startup verification on the deployed image:**
   - `boot` logged with the expected config: every activated flag visible
     in boot, at the expected values;
   - every activated controller path has run at least once with no
     `loop_error`, `ImportError`, `Traceback` or `gate_error`;
   - **at least five minutes** of continuous healthy runtime;
   - no new `SAFETY_LATCH`.
7. Fresh `INVENTORY_RECONCILE`: ok, 0 open orders, 0 positions (or the
   positions expected), 0 mismatches.
8. `ACCOUNT_RISK` readable. Remaining lifetime allowance is at least the
   campaign loss stop (resting, in-flight and uncertain orders counted).
9. Independent watchdog sweep healthy: 0 open orders, all registries OK.
10. Only then: acknowledge any latch the owner has authorized, record exact
    UTC start, end and grace end, and set the window.

## Failure

Any failed step: the lock stays `1`, the source goes back to the last good
release, and the failure is recorded. No window is set.
