# Recorder trade export — deployment diff and safety proof

**Status: PREPARED, NOT DEPLOYED. The credentialed recorder has not been
redeployed and must not be without explicit owner approval.**

Patch: `deploy/pmus_ws_recorder/TRADE_EXPORT.patch`
Target: `origin/feature/l5-transfer-backtest` (the branch the service serves)
Service: `pmus-ws-evidence-recorder` `71b6ea99-ea6a-4b20-9602-fa873721ed35`

---

## 1. The diff

**107 insertions, 0 deletions, one file.** Purely additive: no existing line is
changed or removed.

Verified against the deployed branch, not merely against mine:

- applies cleanly to `origin/feature/l5-transfer-backtest`
- the patched tree parses
- that branch's own `tests/test_pmus_ws_evidence_recorder.py` — **20 passed**

*Note on isolation.* Diffing my branch against the deployed branch shows 180/6,
because my branch also carries the earlier `PMUS_WS_STATUS_LIVE` in-memory
status handoff, which is **not** part of this change. The patch above is the
trade export **alone**. It has no code dependency on that work — it defines its
own `live_trades_bytes` and calls nothing from it — and the 6 deletions belong
entirely to that other change. A credentialed redeploy must not bundle two
things, so this is isolated deliberately.

---

## 2. Every constraint, and where it is enforced

| constraint | enforcement | test |
|---|---|---|
| read-only **GET** | the handler defines `do_GET` only; no `do_POST`/`do_PUT`/`do_DELETE`/`do_PATCH` exists | `test_it_is_a_GET_only_handler` |
| **off by default** | `trades_http_enabled()` returns False unless `PMUS_WS_TRADES_HTTP` ∈ {1,true,yes,on} | `test_the_endpoint_is_off_by_default` |
| **same recorder process** | no new thread, server or port; one `HTTPServer(` in the module, one `serve_status_doc` | `test_it_is_served_by_the_same_process_and_port_as_status` |
| **same snapshot as book/status** | `publish_live_trades(snap, …)` is called immediately after `write_status_doc(root, st)`, from the same `snap` | `test_the_trade_tail_comes_from_the_same_snapshot_as_the_status` |
| **bounded payload** | `TRADES_PER_MARKET = 200`, newest-last slice of a ring that holds 500 | `test_the_payload_is_bounded` |
| **includes `ring_full`** | travels per market with the data | `test_the_export_declares_a_wrapped_ring_with_the_data` |
| **no credential material** | payload is `{t, px, qty, dir}` only; scanned for key/secret/signature/token/authorization | `test_the_payload_carries_no_credential_material` |
| **no order endpoints/imports** | AST scan over the four new functions for `place_limit_buy`, `new_order`, `cancel_order`, `submit`, `OrderArgs`, `post` | `test_the_export_adds_no_order_endpoint_or_import` |
| **private-network only** | no domain is generated; the service has `serviceDomains: []` and is reached at `pmus-ws-evidence-recorder.railway.internal` | unchanged by this patch |

**A disabled endpoint returns 404, never an empty tape.** An empty tape would
read as "nobody traded"; a 404 cannot be mistaken for evidence.

---

## 3. The adversarial suite the owner named

`tests/test_pmus_trade_export_adversarial.py` — **24 tests**, grouped by the
lie each one prevents:

1. **unreadable ≠ empty.** Held at every layer: `to_prints(None) is None` vs
   `to_prints([]) == []`; `trade_flow_for` readable flag; `fetch_status`
   returning None; and the surface verdict `TRADE_FLOW_NOT_OBSERVABLE` vs
   `NO_FILLS_NO_FLOW`.
2. **wrapped ring ≠ complete tape.** A full ring forfeits coverage; one short
   of full still vouches; and a wrapped ring can never produce an evidenced
   no-fill — the dangerous outcome is a busy market reporting "nobody traded".
3. **late subscription ≠ full coverage.** Subscribing after the quote forfeits;
   an unknown start is not assumed early; and **every** refusal returns `None`,
   never `False`, so "we did not watch" can never render as "we watched and
   saw nothing".
4. **fill implies trade_flow.** Monotone on both sides, and an *unknown* queue
   can never manufacture a fill (99,999 contracts through an unknown queue
   still yields zero fills).
5. **monotone quote-level funnel.** Held across a 300-step seeded random walk
   of moving books and random trades:
   `PLACED ≥ RESTABLE ≥ BOOK_REACHED ≥ TRADE_FLOW ≥ EVIDENCED_FILL`, with each
   stage counted once per quote.

---

## 4. What this does NOT do

- **Does not weaken `l5-exec-shadow` isolation.** I attempted to run the PMUS
  surface there as a stream and **reverted it**: that service's guarantee is
  zero order capability, and its guard bans any `src/pmus_*` from the image
  because those lazily reach `pmus_q1_executor`. Narrowing a safety guard to
  fit my module would weaken the one service that promises it. The invariant
  stands untouched.
- **Does not add order capability to the recorder.** It adds a read.
- **Does not change** discovery, subscription, gating, admission, the L5 model,
  the status document, the shadow report, or any threshold.
- **Does not enable itself.** Deploying this patch with the flag unset changes
  the recorder's observable behaviour in exactly one way: `GET /trades`
  returns 404 instead of 404. (The path did not exist before; it 404'd as an
  unknown path. It now 404's as a disabled one.)

---

## 5. To deploy — owner decision, two steps

1. Apply `TRADE_EXPORT.patch` to `feature/l5-transfer-backtest` and push.
   The service's watchPattern includes `src/pmus_ws_evidence_recorder.py`, so
   this redeploys the credentialed recorder. **Expect a brief gap in the WS
   tape across the restart** — the volume is per-service and Railway takes the
   old deployment down before the new one mounts.
2. Only then set `PMUS_WS_TRADES_HTTP=1`.

Leaving step 2 undone is a safe, complete state: the code ships inert.

The consumer is already built and tested (`src/pmus_l5_surface_runner.py`,
source `STATUS_HTTP`). It still needs a host that may carry `src/pmus_*`
modules, which `l5-exec-shadow` deliberately may not.
