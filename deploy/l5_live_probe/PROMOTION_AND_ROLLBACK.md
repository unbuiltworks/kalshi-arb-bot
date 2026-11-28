# Promotion and rollback — live economic-measurement probe

**Nothing here is deployed.** This is the procedure written down in advance so
that promoting is a checklist rather than a judgement call at the moment of
highest pressure. The money-moving executor does not exist yet; when it does,
it promotes by this document or not at all.

## Preconditions (all, not most)

1. `src.l5_live_probe.preflight.verdict(...)` returns `CLEAR_TO_MEASURE`.
   Any `UNDETERMINED` is a blocker — "could not check" never reads as "fine".
2. The exposure envelope is **explicitly chosen** by the owner: all five of
   `L5PROBE_DAILY_LOSS_STOP`, `L5PROBE_MAX_INVENTORY_SHARES`,
   `L5PROBE_MAX_MARKET_SPEND`, `L5PROBE_MAX_CONCURRENT_MARKETS`,
   `L5PROBE_DEADMAN_S`. `envelope.shell()` raises until then, by design.
3. Legacy PMUS is still HALTED and its latch is untouched.
4. `PMUS_SEPARATION` passes: distinct state root, no PMUS import, no order verb.
5. Full suite green on the exact artifact being promoted.
6. The serving SHA to pin is recorded **before** promotion, not after.

## Promotion

1. Record pre-deploy state: serving deployment id, artifact SHA, open orders
   (expect zero), held inventory, latch state.
2. Push the artifact. The service deploys only on its promote marker — a commit
   that does not touch it lands as SKIPPED and changes nothing. This is what
   protects a running experiment while other commits land.
3. Confirm the new deployment reaches SUCCESS and the serving SHA matches the
   pinned SHA. A mismatch is `SHA_DRIFT` and is a STOP, not a warning.
4. Re-run the preflight **against the deployed artifact**. Passing locally is
   not passing in the container — the COPY allowlist has already caught one
   module that existed in the repo and not in the image.
5. Start in measurement-only posture and confirm one full accounting chain
   reaches `settlement` before any second market is considered.

## Rollback

**Trigger** — any of: preflight stops returning `CLEAR_TO_MEASURE`;
`reconcile.decide()` returns `STOP`; serving SHA drifts; inventory disagrees
with the venue; an orphan order appears; settlement stalls past its limit.

**Procedure**
1. **Cancel every resting order first.** A rollback that leaves orders resting
   is not a rollback — it is an unsupervised position.
2. Latch. `reconcile.decide()` sets `latched` and nothing in the codebase
   clears it; clearing is an owner action.
3. Redeploy the prior deployment id recorded in step 1 of promotion. Rolling
   back to "the previous commit" is not the same thing and can pick up an
   artifact nobody pinned.
4. **Hold inventory. Do not liquidate to flatten.** Residual is held to
   settlement; forcing an exit converts a measurable outcome into a taker fee
   and an unmeasurable one.
5. Reconcile against the venue and settle out. Realized net is reported only
   after venue-confirmed settlement.

## Standing rules that survive both paths

- Read-only collectors may self-recover and run 24/7.
- **A money-moving session fails closed and never relaunches itself** after a
  crash, a stop or a completed probe.
- No automatic repeat after a completed or crashed probe.
- Settlement-only realized labelling. Nothing before settlement is realized.
- Credentials are never printed, copied, rotated or created by any of this.
