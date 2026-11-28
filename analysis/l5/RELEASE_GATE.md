# Release gate for the L5 BTC15m money path

Adopted 2026-10-20 at the owner's instruction, after a run of
one-fix-one-deployment cycles in which each deployment revealed the next
defect. The shadow service keeps running throughout; investigation is local.

## Deploy only when a complete, related batch passes ALL of these

1. **Targeted tests pass** -- the suites covering every file the batch touches.
2. **Full regression: zero NEW failures** against the recorded baseline of
   40 known-failing IDs (`26 failed / 14 errors`). Zero NEW and zero GONE,
   and the run must COMPLETE -- a crashed run's comparison is vacuous, not
   clean.
3. **`tests/test_l5_e2e_window.py` passes** -- the scripted runner-to-executor
   window, under the production configuration.
4. **Production configuration validated** -- every value the batch depends on
   established from a production record or from observed behaviour, never
   assumed, and never by reading the service's variables (the owner's arm
   token lives there).
5. **Shadow lock and safety controls verified** on the running process after
   the deploy: `L5_LIVE_SHADOW_LOCK {"locked": true}`, authorization state,
   and the limits echoed in `L5_LIVE_GATES`.
6. **One documented expected improvement** -- economic or operational, written
   down before the deploy, so the next window can confirm or refute it.

Then: ONE deployment for the batch, and confirm the RUNNING commit from a
boot line emitted by that deployment id -- not the GitHub commit, which the
watch pattern may SKIP by design.

## Standing exceptions

A genuine production blocker that cannot be investigated locally may be
deployed alone. "I would like another window of data" is not one: the shadow
service already evaluates consecutive windows and preserves its records.

## What does NOT justify a deployment

* Collecting another window -- shadow is continuous.
* A window with no eligible trades. Report the economic or technical reason
  from the records and change nothing.
* An observability gap that the durable tape already covers.
