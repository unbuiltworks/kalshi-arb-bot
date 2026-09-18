# The prior Gemini order path, recovered

Recovered 2026-09-18 before authorizing GEMINI_CANARY_V2, to answer one
question: does a previously successful Gemini live order exist, and is
CANARY_V2 built like it?

**It exists, it was Prediction Markets, and it went over a different
transport from the one CANARY_V1 used.**

## 1. The known-good implementation

The live path was the **authenticated WebSocket**, not REST.

`GeminiMakerSession` (`src/gemini_kalshi_xrp_maker_live_canary.py`) opens
`wss://ws.gemini.com` through `authenticated_ws_connect`, subscribes
`orders@account`, and then sends order requests as socket messages:

```python
await self.ws.send(json.dumps(
    {"id": rid, "method": method, "params": params}, separators=(",", ":")))
```

Every live module places through it — `gemini_l5_pair_q1`,
`gemini_l5_ladder_q1`, `gemini_l5_incremental_live_q1`,
`gemini_q10_production_executor_v5_2`, `gemini_short_crypto_btc5m_maker_canary_q1`
— all calling `maker.request("order.place", params)` and treating
`int(ack["status"]) == 200` as acceptance.

The canonical first-leg params (`gemini_short_crypto_btc5m_maker_canary_q1`):

```python
{"symbol": event.symbol, "side": "BUY", "type": "LIMIT",
 "timeInForce": "MOC", "price": str(quote), "quantity": "1",
 "eventOutcome": "YES", "clientOrderId": trade_id[:36]}
```

Those live runs executed on an **AWS EC2 host** (`i-0b90f0a3…`) over SSM,
from branches such as `feature/gemini-l5-ladder-cancel-same-side-q1`, writing
state to `/state/gemini-l5-*.json` on that host. None of that state was ever
committed here, which is why no request/response artifact exists in the tree.

**This corrects commit 6ea0b041**, which said the old aws-gemini workflows
"ran code that was never in this repo". The code IS in this repository's
history, on other branches. What is absent is its evidence, not its source.

`src/gemini_l5_canary.py` (commit 0354bcf5) is a REST order module with the
correct field names, but it is explicitly "PREPARED but intentionally
DISARMED", was referenced only by a test workflow and a lane-state note, and
was never wired to a live run. It is not a known-good path.

## 2. The successful artifact

From the venue's own `POST /v1/prediction-markets/orders/history`, read-only,
run 35296466740, these credentials:

```json
{"orderId": 145828835409069189,
 "globalOrderId": "145828835409069189",
 "clientOrderId": "mlbgkm-sup-0819-233943-15-g",
 "symbol": "GEMI-MLB-2608192240-STL-CIN-PPHITS-NCHURCHO1",
 "side": "buy", "outcome": "no", "orderType": "limit",
 "timeInForce": "fill-or-kill",
 "price": "0.64", "quantity": "1",
 "filledQuantity": "1", "remainingQuantity": "0",
 "status": "filled",
 "createdAt": "2026-08-19T23:42:58.121Z"}
```

Plus at least 100 `cancelled` orders (a full page, not a total), e.g.
`145828835580744767`, `GEMI-MLB-2608242240-BOS-MIA-PPHITS-CDURBINO0`,
`sell`/`no`, `0.42`, FOK, `filledQuantity 0`.

**This is Gemini PREDICTION MARKETS, not spot.** There is no Gemini spot
order code anywhere in this repository: `/v1/order/new` appears only inside
two test BAN-lists whose purpose is to keep it out.

Note what the sample is NOT: both recovered rows are **fill-or-kill**, i.e.
taker orders. A venue-accepted **MOC maker** order is not among them, so
maker acceptance remains unproven from this evidence even though the maker
code path is the one that was written.

## 3. Known-good versus GEMINI_CANARY_V2

| | known-good (WS, filled) | GEMINI_CANARY_V2 (REST) |
|---|---|---|
| endpoint | `wss://ws.gemini.com`, `method: "order.place"` | `POST /v1/prediction-markets/order` |
| auth | once, at the WS handshake | per request |
| signing | `X-GEMINI-PAYLOAD` = base64 of the nonce STRING; HMAC-SHA384 | payload = base64 JSON `{request, nonce, …fields}`; HMAC-SHA384 |
| encoded payload | none per message — plain JSON frame on an authenticated socket | base64 `X-GEMINI-PAYLOAD` **and** a JSON body |
| headers | handshake only | every request |
| symbol | `GEMI-MLB-…` / `GEMI-BTC05M…` | `GEMI-BTC05M…-UP` — same family |
| side | `"BUY"` (upper) | `"buy"` (lower) |
| outcome | `eventOutcome: "YES"` | `outcome: "yes"` |
| quantity | `"1"` | `"0.01"`, from `quantityMinimum` |
| price | decimal string | decimal string |
| order type | `type: "LIMIT"` | `orderType: "limit"` |
| maker/post-only | `timeInForce: "MOC"` | `makerOrCancel: true` + `timeInForce: "good-til-cancel"` |
| nonce | one, at connect; epoch SECONDS | per request; epoch SECONDS |
| undocumented fields | `clientOrderId`, accepted and recorded | none — V2 refuses one |

The venue's own record spells `side`, `outcome`, `orderType` and
`timeInForce` in lowercase and calls the field `outcome`, not
`eventOutcome`, whichever transport created the order. So the two spellings
are transport dialects over one order model, and V2's REST spelling matches
the venue's canonical record.

## 4. What this does to the open hypothesis for canary #1's 400

It weakens it. `clientOrderId` is not merely tolerated by this venue — it is
stored and returned as a first-class field on orders this account placed. The
REST `OrderRequest` schema still does not list it, and the orders that carry
it were placed over the WebSocket, so this is not a direct refutation. But
"the REST validator rejects clientOrderId" is now the weaker reading it was
before.

Two differences the recovered order shows that V1 and V2 both diverge from,
and that are worth weighing before the next submission:

- **quantity.** Every historical order on this account is `"1"`. V2 sends
  `"0.01"` on the strength of BTC5m `quantityMinimum`. The metadata is
  per-contract and the recovered rows are MLB, so this is not a
  contradiction — but no order of size 0.01 has ever been accepted here.
- **transport.** Every accepted order on this account went over the
  WebSocket. No REST order has ever succeeded on these credentials. The one
  REST attempt is canary #1, and it was rejected.

Neither is a reason to change V2 by itself. Both are reasons not to describe
the REST path as proven.
