# Promotion triggers — deployment is a deliberate act, not a side effect

Every file in this directory is a **deployment trigger sentinel**. A service
whose Railway `watchPatterns` point here rebuilds **only** when someone edits
its sentinel — never because source, research, docs, tests or runtime data
changed.

## Why

On 2026-09-15 a single push intended for one service produced **four**
successful builds: `pmus-spring-l5-probe` (intended), plus `kalshi-arb-bot`,
`kalshi-l5-shadow` and `r5ft-recon-7b75689c-buildcheck` (all unintended). Three
of four builds were waste, and that was not unusual — a push on 2026-09-06
fanned out to **ten** builds in the same minute.

The cause is uniform: services on the production branch with **no**
`watchPatterns` rebuild on *every* push, and diagnostic services watching
`src/**` rebuild the entire executor image to print one line and exit.

## How to promote

```
echo "promote <sha>: <why>" >> deploy/_promote/<service>
git commit && git push
```

That is the whole mechanism. One file, one service, one deliberate build.

## The executor is different and stricter

`pmus-spring-l5-probe` does **not** use this directory. It has its own
long-standing sentinel, `deploy/pmus_spring_l5/DEPLOY_TRIGGER`, which is its
**only** watch pattern and already the mechanism this section describes. It was
correct before this work and is unchanged by it — verified, not modified.

**Note the trap it creates:** because `DEPLOY_TRIGGER` is the only pattern,
pushing a source fix to the executor deploys *nothing*. A fix can sit inert in
the branch while production keeps running the old artifact. That happened
during the accounting cutover. The fix is not to widen the pattern — it is to
remember that promotion is an explicit, separate act.

## What must never be traded away for build savings

Narrowing `watchPatterns` changes **when a new image is built**. It does not
touch, and must never be used to touch:

- `restartPolicyType: ALWAYS` — crash recovery
- deadman / shutdown-latch behaviour
- the −$30.00 daily stop
- settlement polling
- risk-reduction lanes

Restarting an existing container is not the same event as building a new one.
Only the second is being made rare.
