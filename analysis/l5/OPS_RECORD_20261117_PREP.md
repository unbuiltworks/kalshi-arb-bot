# Production changes made 2026-11-17 — prep only, no deploy, nothing armed

Owner authorised "prep + merge, no deploy" and "pause the spring probe
for the window". This records exactly what was changed, what it achieved,
and one mistake.

## What was changed

| target | change | effect, verified |
|---|---|---|
| `pmus-spring-l5-probe` | `PMUS_TWO_SIDED_ENABLED=NO` | **PAUSED, confirmed from its own log:** `{"kind":"cancelled_all_own","why":"disabled"}` at 12:31:43Z and 12:32:13Z. Its documented kill switch: *"PMUS_TWO_SIDED_ENABLED != YES -> cancel own orders, idle."* Nothing was at risk when applied — `unmatched_cost_total 0`, `open.notional_usd 0`. |
| `pmus-spring-l5-probe` | `L5_SUBMIT_PAUSE=0` | neutralises the inert variable below |
| `l5-pmus-live` | `L5_SUBMIT_PAUSE=1` | belt-and-braces. **It was already `armed: false` before this**, so the pause is NOT what is stopping it; its decisions log `armed: false`, `order: null`, `intent: null`, on BTC15m. |
| `integration/l5-safe-release-candidate-20261109` | merged `622836612` → **`ee547053f`** | **Deployed nothing.** Railway recorded the push as **`SKIPPED`**: the service's watchPattern is `deploy/l5_btc5m_live/DEPLOY_TRIGGER` and the merge left that file byte-identical to what is deployed (`2026-11-16T22:05:00Z`), because the merged side never touched it. Verified on the merge result before pushing, not inferred from the diff. |

**Deployed SHA is unchanged: `241f7432d04e`.** Nothing armed. No campaign
approval set. Every new capability still ships OFF.

## The mistake

I first set `L5_SUBMIT_PAUSE=1` on `pmus-spring-l5-probe`. **That service
does not read it.** `L5_SUBMIT_PAUSE` belongs to
`l5_live_authorization`, used by `l5-pmus-live`; the spring probe runs
`src.pmus_l5_continuous_session` from branch
`feat/pmus-two-sided-fast-cancel-20261115`, whose switch is
`PMUS_TWO_SIDED_ENABLED`.

Consequence: a **live trading service was restarted for no benefit**, and
it came back and took two active markets (`aec-cs2-fnc-100t`,
`aec-cs2-mgc-hero`) before the correct switch was applied. No notional
was committed at any point (`open.notional_usd 0` throughout), so the
cost was a pointless restart rather than money.

What I should have done: read the service's own `describe-service` config
first to see which branch and entrypoint it runs, instead of assuming a
pause mechanism found in one module applied to a differently-sourced
service. Applying a switch by name without checking the code that reads
it is the same class of error as trusting a variable's presence for its
value.

## Still blocked

**The BTC5m lane is still absent from the watchdog's ownership
registry** — `managed_lanes` lists only
`pmus-spring-l5-probe.railway.internal:8090`. Closing it needs the lane's
registry URL appended to the watchdog's `L5_ORDER_REGISTRY_URL`, and that
needs the value of **`L5_BTC5M_FILLS_PORT`**, which:

- has **no default** in code (`port = getenv(...); if port: serve_fills(...)`),
  so an unset value means the registry server does not run at all;
- **is set** on `l5-btc5m-live`, but Railway returns variable *names only*
  to this session, so the value cannot be read here;
- does not appear in the lane's logs (it exits at boot on
  `ENVELOPE_EXPIRED` before serving) or in its networking config (no
  domains, no TCP proxies).

I deliberately did **not** guess it. A wrong port degrades safely — the
watchdog's oracle builder treats an unusable URL as contributing nothing
and does not let it take the others down — but it would leave the blocker
*reported closed while still open*, which is worse than leaving it open.

**What is needed:** the value of `L5_BTC5M_FILLS_PORT`, or permission to
set it to a chosen port and append the matching URL to the watchdog.
