# Probe 03 `SUBMIT_OUTCOME_UNKNOWN` — recovered cause

**Classification: B — PREVIEW ECHO REFUSAL.** `/v1/orders` was never called.
The echo gate refused the order because the venue would have created it at a
different quantity.

## Recovery method

Railway cannot read a volume with no running container (`readContainerFileTool`
and friends all require one), and `pmus-ws-evidence-recorder` mounts a
*different* volume (`18bd5702-…`), so it could not reach the probe's
`156032e0-…`. The least-invasive route was to start the one-shot's own
container in observe-only mode — `L5_OBSERVE_ONLY=YES`, no `PMUS_Q1_*`, no
`auth.claim()`, no `PmusTransport`, `NullTransport` in the executor — and read
the file while it lived. No authorization was created and no order was
possible.

The journal is at `/state/pmus-ws/l5_live_probe_journal.jsonl` (not
`l5_session_journal.jsonl`; `L5_SESSION_JOURNAL` points elsewhere). `Journal`
opens it append-only and never truncates, so Probe 03's records survived.

## The record, verbatim

`seq 304`, `2026-09-25T02:15:50.622635+00:00`, session `c97634157b0343f3`:

```json
{"kind":"action","market":"cpc-btc-updown-15m-2026-09-25-0215z","window":"0215z",
 "action_key":"0xd3c23f6f…|91137531137703372140102051961729605704496238255293805960144070388109225921521|BUY|0.62|9|1790302524",
 "L5_NATIVE":{"side":"UP","price":"0.62","l5_size":"9","relation":null},
 "stage_reached":"ORDER",
 "PMUS_TRANSLATION":{"ok":true,"mode":"TAKER","price":"0.4200",
                     "qty":"0.40668775417984630725","slippage_vs_l5":"-0.2000",
                     "depth":"1712.0300","reason":null},
 "SAFETY_OVERLAY":{"permitted":true,"reason":null,"live_orders":0,
                   "orders_uncertain":null},
 "ORDER":{"client_order_id":"l5f-c5f420ceb3262337ae5beaa1","submitted":null,
          "reason":"SUBMIT_OUTCOME_UNKNOWN",
          "detail":"venue would not create the order as proposed: [{'field': 'quantity', 'sent': '0.4066877541798463', 'echoed': '0.4', 'why': 'venue normalized or omitted the value'}, {'field': 'quantity', 'sent': '0.40"},
 "FILL":null,"refused_by_layer":"SAFETY_OVERLAY",
 "refused_reason":"SUBMIT_OUTCOME_UNKNOWN",
 "book_source":"AUTHENTICATED","pmus_snapshot_age_s":0.202}
```

Armed at `seq 303`, `02:15:50.467223Z`, `preflight: "PASSED"`.

## Answers

| asked | answer |
|---|---|
| exception class | `src.l5_preview_gate.PreviewRefused` — the message is uniquely `require_echo`'s second raise. The old build did not record the class; it is identified from the message text. |
| redacted message | `venue would not create the order as proposed: [{'field': 'quantity', 'sent': '0.4066877541798463', 'echoed': '0.4', 'why': 'venue normalized or omitted the value'}, {'field': 'quantity', 'sent': '0.40` — **truncated at 200 chars by `str(exc)[:200]`.** Nothing secret in it. |
| lifecycle stage | **PREVIEW_ECHO_VALIDATION** |
| client order id | `l5f-c5f420ceb3262337ae5beaa1` |
| intended side / outcome / price / qty | L5_NATIVE `UP` at `0.62` for `9`; translated TAKER, price `0.4200`, qty `0.40668775417984630725`, slippage `-0.2000` vs L5, depth `1712.0300`, book AUTHENTICATED at age `0.202s` |
| serialized wire body | reproduced locally from the same intent: `{"marketSlug":"cpc-btc-updown-15m-2026-09-25-0215z","type":"ORDER_TYPE_LIMIT","price":{"value":"0.4200","currency":"USD"},"quantity":0.4066877541798463,"tif":"TIME_IN_FORCE_GOOD_TILL_CANCEL","participateDontInitiate":false,"outcomeSide":"OUTCOME_SIDE_YES","action":"ORDER_ACTION_BUY","manualOrderIndicator":"MANUAL_ORDER_INDICATOR_AUTOMATIC"}` — note `quantity` goes on the wire as a **float**, `price.value` as a string |
| preview HTTP status | **200.** `urlopen` raises on any non-2xx, and execution reached `require_echo`, so the preview succeeded and returned an order. The response body itself was not journaled. |
| exact echo mismatch | `quantity`: sent `0.4066877541798463`, venue echoed `0.4`. Two entries because two detectors fire on the same difference — the `ECHOED_NUMERIC` loop and the leaves-quantity check at `l5_preview_gate.py:99-102`. The second is cut off by the 200-char truncation. |
| was `/v1/orders` called? | **No.** `require_echo` raises inside the `self.last_preview = {...}` assignment, which is the statement *before* `self.http.request("POST", "/v1/orders", …)`. Proven by code order and asserted by a new regression test. |
| POST status | N/A — no POST. |
| venue position / open orders afterwards | `reconcile_from_venue(positions(), open_orders())` returned `reconciled: true`, `drift: {}`, `open_orders_at_venue: 0`, `uncertainty_cleared: true`, `authority: VENUE`. |

## Root cause

From the same run's preflight record:

    tick_constraint_known       passed  detail "0.01"
    quantity_constraint_known   passed  detail "0.01"
    account_matches_expected    passed  detail "expected fingerprint d51b44a3fa83, got d51b44a3fa83"
    market_open_tradable        passed  detail state MARKET_STATUS_OPEN
    buying_power_usd            "63.04895"

**The venue's quantity increment is 0.01. We sent `0.4066877541798463`.** The
venue normalised it to `0.40` and echoed `0.4`; the gate refused rather than
let an order rest at a size the engine did not choose — the same discipline
that was written after the venue silently normalised an off-tick *price* on
2026-09-24T14:40Z.

The increment is **read** in preflight and never **applied**. λ =
`0.04518752824220514525` times an L5 size of 9 gives
`0.40668775417984630725`, which the venue cannot represent. The price was
fine: `0.4200` is a clean multiple of the `0.01` tick.

This is not a venue fault, a network fault, or a credential fault. The gate
worked. The order was never sent. No money was at risk at any point.

## Minimal corrective action — NOT DEPLOYED

Quantize the order quantity to the venue's own quantity increment, read from
the market metadata the preflight already fetches, immediately before the
body is serialised — so what we send is something the venue can create.

Three properties it must have:

1. **Floor, not round.** Rounding up can exceed the envelope; flooring cannot.
2. **Refuse below one increment.** A quantity that floors to `0` must be
   refused as unexecutable, never sent as a zero order.
3. **Read the increment, never assume it.** `0.01` is what this market
   returned today; it belongs in the body from `quantity_constraint_known`,
   not in a constant.

This changes what reaches the wire, not the sizing policy: the translation
still computes `0.40668775417984630725`, and the wire carries the largest
representable quantity that does not exceed it — here `0.40`, a loss of
`0.0067` shares, about 1.6%. That is a real behavioural change to execution
and is therefore for the owner to approve, not for me to slip in.

Left undecided deliberately: whether the same quantisation belongs on price
(already on-tick today, so nothing is broken) and whether flooring should be
reported as a distinct refusal reason when it bites.
