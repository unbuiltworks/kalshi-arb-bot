# OBSERVE-ONLY verification — 2026-09-24 21:36Z — **FAILED**

Service `l5-live-probe-oneshot` (`84aa9999-6d7a-4980-9a87-96694d8178dd`),
deployment `eee37fbb`, commit `784faf1`, `L5_OBSERVE_ONLY=YES`, no
`PMUS_Q1_*` variables set anywhere. Target window
`cpc-btc-updown-15m-2026-09-24-2130z`.

## Verdict

**FAILED.** The observe-only run terminated ~1 second after container start
with an `ImportError`. It never polled the L5 activity feed, so it produced
zero L5 actions, zero L5_NATIVE decisions and zero would-submits, while the
continuous recorder recorded **163 L5 actions and 89 would-orders in the
same 2130z window**. Zero observe-only actions against non-zero recorder
actions in the same interval is a failure, not a quiet market.

The safety properties all held: no authorization was claimed, no POST-capable
transport was constructed, `armed` stayed `false`, `submitted_live` stayed `0`.

## Exact stdout — verbatim

```
[2026-09-24T21:36:21.208339452Z] info: L5_PROBE_ERROR {
  "LABEL": "LIVE_PROBE_ERROR",
  "mode": "OBSERVE_ONLY",
  "market": "cpc-btc-updown-15m-2026-09-24-2130z",
  "stage": "observing",
  "error": "ImportError",
  "detail": "cannot import name 'pmus_l5_persistence_v2' from 'src' (/app/src/__init__.py)",
  "l5_feed": {"polls": 0, "polls_ok": 0, "polls_failed": 0, "raw_rows": 0,
              "rows_in_window": 0, "new_actions": 0, "last_ok_utc": null,
              "last_error": null, "healthy": false},
  "book_feed": {"market": "cpc-btc-updown-15m-2026-09-24-2130z",
                "connected": true, "frames": 3, "book_states": 2,
                "last_book_age_s": 0.162, "fresh": true, "errors": [],
                "stop_reason": "running", "source": "AUTHENTICATED_WS"}
}

[2026-09-24T21:36:21.208344409Z] info: L5_OBSERVE_ONLY {
  "LABEL": "LIVE_OBSERVE_ONLY",
  "market": "cpc-btc-updown-15m-2026-09-24-2130z",
  "stage_reached": "error",
  "authorization_claimed": false,
  "post_capable": false,
  "transport": "NULL",
  "l5_feed": {"polls": 0, "polls_ok": 0, "polls_failed": 0, "raw_rows": 0,
              "rows_in_window": 0, "new_actions": 0, "last_ok_utc": null,
              "last_error": null, "healthy": false},
  "book_feed": {"market": "cpc-btc-updown-15m-2026-09-24-2130z",
                "connected": false, "frames": 4, "book_states": 2,
                "last_book_age_s": 0.269, "fresh": true, "errors": [],
                "stop_reason": "local_cancellation", "source": "AUTHENTICATED_WS"},
  "actions_handled": 0,
  "l5_native_decisions": 0,
  "pmus_translations_ok": 0,
  "overlay_refusals": 0,
  "would_submit": 0,
  "refused_by_layer": {},
  "refused_reason": {},
  "armed": false,
  "submitted_live": 0,
  "live_limits": {"max_usd_per_market": "5", "max_abs_residual": "10",
                  "max_total_exposure_usd": "5", "max_concurrent_markets": 1,
                  "max_live_orders": 1, "daily_realized_loss_halt_usd": "5",
                  "band": null},
  "labels": "OBSERVE ONLY. No authorization was claimed, no PmusTransport was constructed, and the executor held NullTransport, which has no HTTP client. A would-submit is an intent the transport refused, never a fill."
}
```

## Root cause — the probe image is missing four modules the L5 feed needs

`src/l5_window_actions.new_l5_actions` defers `from src import
l5_pmus_realtime_observer as O` to call time (`src/l5_window_actions.py:117`),
because it needs `O.action_key` and `O.l5_to_pmus_action`. The observer
imports, at module scope (`src/l5_pmus_realtime_observer.py:58-61`):

    from src import pmus_fees as fees                  # in the image
    from src import pmus_interaction_classifier as clf # in the image
    from src import pmus_l5_persistence_v2 as pv2      # NOT in the image
    from src.polymarket_l5.action_model import state_key  # NOT in the image

`deploy/l5_live_probe/Dockerfile` copies the observer but not its transitive
dependencies. The recorder's Dockerfile copies all of them, which is why the
recorder is unaffected.

Reproduced locally, byte-for-byte, by materialising exactly the Dockerfile's
23 `COPY` lines into an empty tree and calling the same function:

    ImportError: cannot import name 'pmus_l5_persistence_v2' from 'src'

Adding the missing files one at a time shows the full chain — fixing only the
module the error names surfaces two more:

| added | next result |
|---|---|
| `src/pmus_l5_persistence_v2.py` | `ModuleNotFoundError: No module named 'src.polymarket_l5'` |
| `src/polymarket_l5/__init__.py` | `ModuleNotFoundError: No module named 'src.polymarket_l5.action_model'` |
| `src/polymarket_l5/config.py` | `ModuleNotFoundError: No module named 'src.polymarket_l5.action_model'` |
| `src/polymarket_l5/action_model.py` | **observer imports clean** |

`analysis/l5/l5_action_model.json` and `l5_action_model_v2.json` are loaded
lazily and are not needed on this path; `action_key` and `l5_to_pmus_action`
reference neither `pv2` nor `state_key` (checked by AST). They are
import-time-only dependencies of a module the probe uses two functions from.

## This is also the true root cause of Probe 02

Probe 02 ran under `4c1f328`. That image copied
`src/l5_pmus_realtime_observer.py` but not `pmus_l5_persistence_v2` or
`polymarket_l5` either, and the pre-consolidation one-shot made the same
lazy import at `scripts/l5_pmus_live_oneshot.py:98`. Reconstructing the
`4c1f328` image from git and performing that import reproduces the identical
error.

So Probe 02's `l5_actions 0` and its early exit were **this ImportError**,
not the row-shape defect. The row-shape defect was real and is genuinely
fixed (`368b298`, six regression tests fail when it is reinstated), but it
was latent — the import failed before the frozen rule was ever reached.
The earlier Probe 02 diagnosis was therefore incomplete, and is corrected here.

Probe 02 was **armed with real money** when this happened. It spent its
one-shot authorization and did nothing. No order was sent and no money moved.

## Checklist

| gate | required | observed | |
|---|---|---|---|
| OBSERVE-ONLY HAS POST CAPABILITY | NO | `post_capable: false`, `transport: "NULL"` | PASS |
| AUTHORIZATION CLAIMED | NO | `authorization_claimed: false` | PASS |
| ARMED | false | `false` | PASS |
| SUBMITTED_LIVE | 0 | `0` | PASS |
| AUTH WS SUSTAINED | yes | connected, `AUTHENTICATED_WS`, but only ~1s before the error | **FAIL** |
| BOOK FRAMES / STATES | sustained | 4 frames / 2 states, `last_book_age_s` 0.269, `fresh` | **FAIL** |
| L5 POLLS OK / FAILED | sustained OK | 0 / 0 — never polled | **FAIL** |
| RAW / TARGET / NEW / READABLE | > 0 | 0 / 0 / 0 / 0 | **FAIL** |
| L5_NATIVE DECISIONS | > 0 | 0 | **FAIL** |
| PMUS TRANSLATIONS | > 0 | 0 | **FAIL** |
| OVERLAY WOULD-SUBMITS / REFUSALS | > 0 | 0 / 0 | **FAIL** |
| EARLY EXIT / ERROR | none | exited at ~1s, `stage_reached: "error"` | **FAIL** |
| RECORDER ACTIONS SAME INTERVAL | — | 2130z: 163 actions, 176 polls, 0 failures, 89 would-orders, preflight PASSED | recorder healthy |
| READY FOR PROBE 03 | — | **NO** | **BLOCKED** |

## Recorder, same and following windows (deployment `c34af3d9`, commit `45b8749`)

| window | coverage % | usable states | l5_actions | polls | fails | would_orders | submitted_live | preflight | armed |
|---|---|---|---|---|---|---|---|---|---|
| 2130z | 99.9 | 7971 | 163 | 176 | 0 | 89 | 0 | PASSED | false |
| 2145z | 99.9 | 7364 | 71 | 177 | 0 | 23 | 0 | PASSED | false |
| 2200z | 99.9 | 8630 | 125 | 176 | 0 | 76 | 0 | PASSED | false |
| 2215z | 99.9 | 7292 | 81 | 177 | 0 | 35 | 0 | PASSED | false |
| 2230z | 100.0 | 8522 | 133 | 176 | 0 | 72 | 0 | PASSED | false |
| 2245z | 99.9 | 7114 | 231 | 177 | 0 | 91 | 0 | PASSED | false |
| 2300z | 97.2 | 7613 | 150 | 176 | 0 | 74 | 0 | PASSED | false |
| 2315z | 99.9 | 7286 | 167 | 177 | 0 | 71 | 0 | PASSED | false |

No `L5_SESSION_HALT` lines. The recorder is unaffected by this defect and
continues to run read-only, 24/7.

## State left behind

- No fix deployed. Per standing instruction, the run failed and work stopped
  at the report.
- `L5_OBSERVE_ONLY=YES` **deliberately left set** on
  `84aa9999-6d7a-4980-9a87-96694d8178dd`. Removing it is the one change that
  moves the service back toward being able to construct a POST-capable
  transport, and the integration path it gates is currently broken.
- No `PMUS_Q1_*` variables set. Nothing armed. No orders. No money moved.

## What the observe-only gate bought

It caught, for free, the exact defect that silently consumed a real-money
authorization in Probe 02 — and it named it in stdout instead of losing it to
an unreadable volume. It should stay a permanent pre-live gate.
