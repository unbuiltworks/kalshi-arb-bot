# Rung-A — ABORT 002. The pinned artifact `e48960f7` CANNOT RUN.

Found during the known-disarmed verification run, before any arming. **No order,
no arm triple, no mandate, no money at risk at any point.** Reported rather than
improvised, per the abort rule.

## What happened

Deployment `a64ecf5a`, built from the pinned commit, DISARMED, `OBSERVE_ONLY`:

```
2026-09-28T04:15:07.748Z
L5_PROBE_ERROR {"mode":"OBSERVE_ONLY","stage":"observing","error":"ImportError",
  "detail":"cannot import name 'config' from 'src.polymarket_l5'
            (/app/src/polymarket_l5/__init__.py)"}
L5_OBSERVE_ONLY {"stage_reached":"error","authorization_claimed":false,
  "post_capable":false,"transport":"NULL","armed":false,"submitted_live":0}
```

`src/polymarket_l5/config.py` is **not in the image**. Ten-plus `polymarket_l5`
submodules import it as `from . import config`.

The safety envelope held exactly as designed while this happened: no
authorization claimed, `NullTransport`, `post_capable false`, `armed false`,
`submitted_live 0`. The artifact failed closed. But it **cannot execute Rung A**,
so arming it was never possible.

## Root cause — in my verification tool, not only in the Dockerfile

The script that computed the "44-file closure" filtered on `n.level == 0`, which
**silently skipped every relative import in the repository**. `from . import
config` was therefore invisible to it.

The COPY list was then generated *from that closure* and checked *against that
same closure*. Both sides of "byte-identical" came from one tool with one blind
spot, so they agreed with each other and disagreed with Python. The check was
vacuous in the specific way the project has been bitten by before: a guard that
cannot see what it is guarding reports clean.

Worse, `_closure()` inside `tests/test_lock5_entrypoint_composition.py` — the
guard that exists to catch exactly this — had the **same** blind spot, looking
only at `n.module` and never at `n.level`. And nothing in the suite compared the
Dockerfile to the closure at all; that comparison had been done by hand, in a
shell, once.

## Corrected closure: 45 files

The corrected computation resolves relative imports. It yields **45** files:
the 44 already copied, plus `src/polymarket_l5/config.py`. Exactly one file
short, and it was the one that crashed.

**The two absence proofs were re-run against the corrected closure and HOLD:**

* `pmus_l5_ramp_ladder` and `pmus_micro_executor`: **0 occurrences**. RAMP_LADDER_001
  stays out of the capability graph; the mandate/GuardedTransport boundary remains
  the only risk/sizing authority. The under-approximation did not conceal it.
* `L5_LIVE_HALT`: **0 readers** among all 45 files, independently confirming the
  HALT clarification was factually correct.

## A second finding, separate and unresolved

The same report printed the artifact's operative `live_limits`:

```
max_usd_per_market 5 · max_total_exposure_usd 5 · max_abs_residual 10
max_concurrent_markets 1 · max_live_orders 8 · daily_realized_loss_halt_usd 5
```

These come from `l5_probe_manifest.probe_limits()`, **hardcoded at $5**, five
times the approved $1.00 envelope, and they are in the capability graph.

This is not the RAMP_LADDER_001 failure — it is static, not a dynamic rung table
that reloads stale owner state — which is exactly why the structural test aimed at
dynamic rung-based caps did not flag it. It is still a **second risk authority in
an artifact that is supposed to have one**.

It is not a live $5 exposure today: the mandate is a hard gate in front of the
transport (`admit()` before every body, `GuardedTransport` at `POST /v1/orders`),
and with `PMUS_LIVE_MANDATE` empty nothing can submit at all. With the $1 mandate
installed, $1 binds because it is tighter. The objection is structural: if the
mandate were ever absent, misparsed, or bypassed on some path, $5 would be the
operative bound, and the owner's stated requirement is one authority, not a
tighter one in front of a looser one.

**Not fixed here.** Reconciling it changes execution-class behaviour and needs its
own decision: either `probe_limits()` is derived from the mandate, or it is
removed from this artifact's graph, or the owner accepts it as a documented
belt-and-braces ceiling. I am not choosing for you.

## Fixes made — a SUCCESSOR artifact, not a modification of `e48960f7`

`e48960f7` is untouched, as instructed. The following are a candidate successor:

1. `deploy/l5_pmus_lock5_probe/Dockerfile` — `COPY src/polymarket_l5/config.py`.
   The COPY list now equals the corrected 45-file closure, both directions.
2. `tests/…composition.py::_closure` — follows relative imports, resolving
   `n.level` against the containing package; also emits the bare package name so
   `src/__init__.py` is accounted for rather than looking "copied but unreachable".
3. **New: `test_the_dockerfile_copies_exactly_the_entrypoint_closure`** — set
   equality in both directions, naming offenders. `missing` catches an unrunnable
   image; `extra` catches a file copied but unreachable, which is how dead
   capability gets concealed. This is the check whose absence allowed the
   divergence, and it now lives in the suite instead of in a shell.
4. **New: `test_the_closure_follows_relative_imports`** — builds a throwaway
   package using `from . import sib` and fails if the sibling is not pulled in.
   This is the blind spot itself, pinned, so it cannot come back.
5. **New: `test_polymarket_l5_config_is_reachable_and_copied`** — the specific
   file whose absence crashed the probe at 04:15:07Z.

Test evidence: 30 pass in the composition suite; **294 pass** across the twelve
focused probe-path suites.

## What I have NOT done

The service is **still pinned to `e48960f7`** and still DISARMED. I have not
re-pinned it to the successor, because the approved `strategy_version` is
`e48960f7` and a new SHA is outside that approval. No mandate, no window binding,
no arm triple. `l5-pmus-live` is still un-quiesced (this MCP cannot scale).

## What I need from you

1. **Approve the successor artifact** (its SHA below once committed), which is
   `e48960f7` plus one COPY line and three test guards — no execution-class code
   change — and authorize re-pinning the service to it.
2. **Decide the `probe_limits()` $5 question** above.
3. The two items still outstanding from ABORT 001: quiesce `l5-pmus-live`, and
   confirm the DISARMED -> ARMED transition.
