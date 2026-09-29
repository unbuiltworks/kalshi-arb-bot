"""Does PMUS expose PRIVATE order/fill lifecycle over WebSocket? Prove it.

STRUCTURALLY READ-ONLY. The only frames this process ever sends are
`subscribe` and `unsubscribe`. There is no order path, no HTTP mutation and
no POST-capable object constructed anywhere in it. It writes one JSON report
and nothing else.

WHY THIS EXISTS. `pmus_ws_evidence_recorder` uses `subscription_type` 1
(market data) and 3 (trades). Those two integers are the ONLY values anyone
here has ever sent. 2, and everything above 3, have never been probed, and no
document in this repository enumerates the type space. Before designing a
WS-driven lifecycle we have to know whether the venue will stream OUR order
acknowledgements, partial fills, fills, cancellations and rejections at all.

If it will not, that limitation is the finding, and it must be established
from the venue's own answers rather than assumed from the international
Polymarket CLOB schema, which is a DIFFERENT product with a different API.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_ws_evidence_recorder as R   # noqa: E402

#: every frame key this process may emit. Enforced at send time.
ALLOWED_KEYS = {"subscribe", "unsubscribe"}

#: the market-data host and path we already know works
KNOWN_URL = R.WS_URL
KNOWN_PATH = R.WS_PATH

#: candidate private stream paths on the same authenticated host. Each is
#: tried with the documented Ed25519 header scheme signed over ITS OWN path.
CANDIDATE_PATHS = [
    "/v1/ws/markets",          # control: known to work
    "/v1/ws/orders",
    "/v1/ws/user",
    "/v1/ws/users",
    "/v1/ws/private",
    "/v1/ws/fills",
    "/v1/ws/trades",
    "/v1/ws/account",
    "/v1/ws/portfolio",
    "/v1/ws",
]

#: subscription_type integers to probe on the known-good path. 1 and 3 are the
#: two we already use and serve as positive controls.
CANDIDATE_TYPES = list(range(0, 13))

PROBE_SECONDS = float(os.getenv("PMUS_WS_PROBE_SECONDS", "8"))
REPORT = os.getenv("PMUS_WS_PROBE_REPORT", "/state/pmus-ws/ws_private_probe.json")


def guard(frame: Dict[str, Any]) -> Dict[str, Any]:
    bad = set(frame) - ALLOWED_KEYS
    if bad:
        raise RuntimeError("blocked non-read-only frame keys: %s" % (bad,))
    return frame


async def probe_path(path: str, slug: str) -> Dict[str, Any]:
    """Can we even open an authenticated socket at this path?"""
    import websockets
    url = "wss://api.polymarket.us" + path
    out: Dict[str, Any] = {"path": path, "url": url}
    try:
        headers = R.auth_headers(os.environ[R.KEY_ENV],
                                 os.environ[R.SECRET_ENV], path=path)
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = "auth_headers: %s: %s" % (type(exc).__name__, exc)
        return out
    try:
        async with websockets.connect(url, additional_headers=headers,
                                      max_size=4 * 1024 * 1024,
                                      open_timeout=10) as ws:
            out["connected"] = True
            # a bare subscribe with no type, to see how the venue complains
            await ws.send(json.dumps(guard({"subscribe": {
                "request_id": "p-" + uuid.uuid4().hex[:8],
                "market_slugs": [slug]}})))
            msgs: List[Any] = []
            t0 = time.monotonic()
            while time.monotonic() - t0 < 4.0 and len(msgs) < 6:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    break
                except Exception as exc:                       # noqa: BLE001
                    msgs.append({"recv_error": str(exc)[:200]})
                    break
                try:
                    msgs.append(json.loads(raw))
                except Exception:                              # noqa: BLE001
                    msgs.append({"raw": str(raw)[:400]})
            out["messages"] = msgs
    except Exception as exc:                                   # noqa: BLE001
        out["connected"] = False
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
    return out


async def probe_types(slug: str) -> Dict[str, Any]:
    """Which subscription_type integers does the known-good socket accept?

    One socket, one type per request_id, so each answer is attributable.
    """
    import websockets
    headers = R.auth_headers(os.environ[R.KEY_ENV], os.environ[R.SECRET_ENV],
                             path=KNOWN_PATH)
    per: Dict[str, Any] = {}
    by_req: Dict[str, int] = {}
    async with websockets.connect(KNOWN_URL, additional_headers=headers,
                                  max_size=8 * 1024 * 1024,
                                  open_timeout=15) as ws:
        for st in CANDIDATE_TYPES:
            rid = "t%d-%s" % (st, uuid.uuid4().hex[:8])
            by_req[rid] = st
            per[str(st)] = {"request_id": rid, "responses": [],
                            "market_messages": 0}
            await ws.send(json.dumps(guard({"subscribe": {
                "request_id": rid, "subscription_type": st,
                "market_slugs": [slug]}})))
            await asyncio.sleep(0.2)
        t0 = time.monotonic()
        other: List[Any] = []
        while time.monotonic() - t0 < PROBE_SECONDS:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:                           # noqa: BLE001
                other.append({"recv_error": str(exc)[:200]})
                break
            try:
                m = json.loads(raw)
            except Exception:                                  # noqa: BLE001
                other.append({"raw": str(raw)[:300]})
                continue
            rid = None
            if isinstance(m, dict):
                for k in ("request_id", "requestId"):
                    if k in m:
                        rid = m[k]
                        break
                if rid is None:
                    for v in m.values():
                        if isinstance(v, dict) and v.get("request_id"):
                            rid = v["request_id"]
                            break
            if rid in by_req:
                bucket = per[str(by_req[rid])]
                if len(bucket["responses"]) < 4:
                    bucket["responses"].append(m)
            else:
                # an unattributed data frame: record its SHAPE, not its volume
                keys = sorted(m)[:8] if isinstance(m, dict) else ["<non-dict>"]
                other.append({"keys": keys})
        per["_unattributed_shapes"] = other[:40]
    return per


async def probe_private_schema(slug: str) -> Dict[str, Any]:
    """/v1/ws/private ACCEPTS an authenticated socket. What does it want?

    The type probe proved there is no private lifecycle subscription_type in
    0-12 on /v1/ws/markets, and that every other candidate path 404s. But
    /v1/ws/private connects and answers `invalid_message` to a market-slug
    subscribe, which means the endpoint exists and expects a DIFFERENT frame
    shape. This enumerates plausible shapes and records the venue's own words.
    """
    import websockets
    path = "/v1/ws/private"
    url = "wss://api.polymarket.us" + path
    headers = R.auth_headers(os.environ[R.KEY_ENV], os.environ[R.SECRET_ENV],
                             path=path)
    shapes: List[Dict[str, Any]] = []
    # type-only, no market scope: a private stream is account-scoped
    for st in range(0, 13):
        shapes.append({"label": "type_only_%d" % st,
                       "frame": {"subscribe": {"subscription_type": st}}})
    # documented-style variants
    shapes += [
        {"label": "no_type_no_scope", "frame": {"subscribe": {}}},
        {"label": "event_slugs",
         "frame": {"subscribe": {"subscription_type": 4,
                                 "event_slugs": [slug.replace("cpc-", "")]}}},
        {"label": "orders_string",
         "frame": {"subscribe": {"subscription_type": "SUBSCRIPTION_TYPE_ORDER"}}},
        {"label": "fills_string",
         "frame": {"subscribe": {"subscription_type": "SUBSCRIPTION_TYPE_FILL"}}},
        {"label": "channel_orders",
         "frame": {"subscribe": {"channel": "orders"}}},
        {"label": "channels_list",
         "frame": {"subscribe": {"channels": ["orders", "fills"]}}},
        {"label": "market_scoped_type1",
         "frame": {"subscribe": {"subscription_type": 1,
                                 "market_slugs": [slug]}}},
    ]
    out: List[Dict[str, Any]] = []
    try:
        async with websockets.connect(url, additional_headers=headers,
                                      max_size=4 * 1024 * 1024,
                                      open_timeout=15) as ws:
            for sh in shapes:
                rid = "s-" + uuid.uuid4().hex[:8]
                frame = dict(sh["frame"])
                if "subscribe" in frame and isinstance(frame["subscribe"], dict):
                    frame["subscribe"] = dict(frame["subscribe"],
                                              request_id=rid)
                await ws.send(json.dumps(guard(frame)))
                got = []
                t0 = time.monotonic()
                while time.monotonic() - t0 < 1.2 and len(got) < 2:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        break
                    except Exception as exc:                   # noqa: BLE001
                        got.append({"recv_error": str(exc)[:200]})
                        break
                    try:
                        got.append(json.loads(raw))
                    except Exception:                          # noqa: BLE001
                        got.append({"raw": str(raw)[:300]})
                out.append({"label": sh["label"], "sent": frame,
                            "responses": got})
    except Exception as exc:                                   # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
                "tried": out}
    return {"shapes": out}


#: Candidate enum NAMES for the private socket. v2 proved the string form is
#: honoured there: "SUBSCRIPTION_TYPE_ORDER" came back "slugs already
#: subscribed" (recognised) while "SUBSCRIPTION_TYPE_FILL" came back
#: "invalid_message" (not a name). Each name gets its OWN connection so a
#: previous subscribe cannot mask the answer.
PRIVATE_NAMES = [
    "SUBSCRIPTION_TYPE_ORDER", "SUBSCRIPTION_TYPE_ORDER_SNAPSHOT",
    "SUBSCRIPTION_TYPE_ORDER_UPDATE", "SUBSCRIPTION_TYPE_ORDER_EVENT",
    "SUBSCRIPTION_TYPE_EXECUTION", "SUBSCRIPTION_TYPE_TRADE",
    "SUBSCRIPTION_TYPE_POSITION", "SUBSCRIPTION_TYPE_POSITION_SNAPSHOT",
    "SUBSCRIPTION_TYPE_BALANCE", "SUBSCRIPTION_TYPE_ACCOUNT",
    "SUBSCRIPTION_TYPE_PORTFOLIO", "SUBSCRIPTION_TYPE_RFQ",
    "SUBSCRIPTION_TYPE_SETTLEMENT", "SUBSCRIPTION_TYPE_USER_TRADE",
]


async def probe_private_names() -> Dict[str, Any]:
    """Map the private enum by NAME, one fresh socket per candidate."""
    import websockets
    path = "/v1/ws/private"
    url = "wss://api.polymarket.us" + path
    out: List[Dict[str, Any]] = []
    for name in PRIVATE_NAMES:
        rec: Dict[str, Any] = {"name": name}
        try:
            headers = R.auth_headers(os.environ[R.KEY_ENV],
                                     os.environ[R.SECRET_ENV], path=path)
            async with websockets.connect(url, additional_headers=headers,
                                          max_size=2 * 1024 * 1024,
                                          open_timeout=10) as ws:
                rid = "n-" + uuid.uuid4().hex[:8]
                await ws.send(json.dumps(guard({"subscribe": {
                    "request_id": rid, "subscription_type": name}})))
                got = []
                t0 = time.monotonic()
                while time.monotonic() - t0 < 2.5 and len(got) < 3:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        break
                    except Exception as exc:                   # noqa: BLE001
                        got.append({"recv_error": str(exc)[:160]})
                        break
                    try:
                        got.append(json.loads(raw))
                    except Exception:                          # noqa: BLE001
                        got.append({"raw": str(raw)[:300]})
                rec["responses"] = got
                rec["recognised"] = not (
                    len(got) == 1 and isinstance(got[0], dict)
                    and got[0].get("error") == "invalid_message")
        except Exception as exc:                               # noqa: BLE001
            rec["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        out.append(rec)
    return {"names": out}


async def probe_depth_end_to_end(slug: str, seconds: float = 12.0) -> Dict[str, Any]:
    """P0 PROOF: live venue depth, all the way into the FROZEN walker.

    Subscribes type 1, feeds each raw frame through the REAL
    `AuthenticatedBookFeed.ingest`, reads the REAL `feed.ladder`, and hands
    it to the REAL `pmus_completion_shadow.buy_levels` /
    `marginal_complete` and `pmus_l5_native_engine.worst_admitted_price`.
    No re-implementation: if the wiring is broken anywhere, this shows zero
    levels rather than a number someone typed.
    """
    import websockets
    from src import l5_live_book_feed as LF
    from src import pmus_completion_shadow as S
    from src import pmus_l5_native_engine as E
    from decimal import Decimal as D

    feed = LF.AuthenticatedBookFeed(slug)
    headers = R.auth_headers(os.environ[R.KEY_ENV], os.environ[R.SECRET_ENV],
                             path=R.WS_PATH)
    counts: List[Dict[str, Any]] = []
    async with websockets.connect(R.WS_URL, additional_headers=headers,
                                  max_size=8 * 1024 * 1024,
                                  open_timeout=15) as ws:
        await ws.send(json.dumps(guard({"subscribe": {
            "request_id": "d-" + uuid.uuid4().hex[:8],
            "subscription_type": R.SUB_MARKET_DATA,
            "market_slugs": [slug]}})))
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except Exception:                                  # noqa: BLE001
                break
            try:
                feed.ingest(json.loads(raw))
            except Exception:                                  # noqa: BLE001
                continue
            lad = feed.ladder
            if not lad:
                continue
            counts.append({"bids": len(lad.get("bids") or []),
                           "offers": len(lad.get("offers") or [])})
    lad = feed.ladder
    if not lad:
        return {"proof": "NO_DEPTH_RECEIVED", "frames_with_depth": 0}
    # END TO END through the frozen walk, with a basis cheap enough that the
    # criterion admits, so the number of levels consumed is visible.
    up = S.buy_levels(lad, "up")
    dn = S.buy_levels(lad, "down")
    basis, theta = D("0.20"), D("0.96")
    want = D(5000)
    n_up, c_up, f_up, _ = S.marginal_complete(up, want, basis, theta)
    n_dn, c_dn, f_dn, _ = S.marginal_complete(dn, want, basis, theta)
    return {
        "proof": "VENUE_DEPTH_REACHED_THE_FROZEN_WALK",
        "frames_with_depth": len(counts),
        "levels_last_frame": {"bids": len(lad["bids"]) if lad.get("bids") else 0,
                              "offers": len(lad["offers"]) if lad.get("offers") else 0},
        "levels_max_seen": {
            "bids": max((c["bids"] for c in counts), default=0),
            "offers": max((c["offers"] for c in counts), default=0)},
        "ladder_health": feed.ladder_health(),
        "walker_input_levels": {"up": len(up), "down": len(dn)},
        "top_three_up": up[:3], "top_three_down": dn[:3],
        "frozen_walk_at_basis_0.20": {
            "theta": str(theta),
            "up_admitted_qty": str(n_up), "up_cost": str(c_up),
            "up_fee": str(f_up),
            "up_worst_admitted_px": str(
                E.worst_admitted_price(up, want, basis, theta)),
            "down_admitted_qty": str(n_dn), "down_cost": str(c_dn),
            "down_fee": str(f_dn),
            "down_worst_admitted_px": str(
                E.worst_admitted_price(dn, want, basis, theta))},
        "labels": ("every level is the venue's own px/qty; the walker calls "
                   "are the production functions, not a copy"),
    }


async def main() -> int:
    slug = (os.getenv("PMUS_WS_PROBE_SLUG", "").strip()
            or "cpc-btc-updown-15m-2026-09-29-1115z")
    rep: Dict[str, Any] = {
        "LABEL": "PMUS_WS_PRIVATE_PROBE",
        "read_only": True,
        "frames_this_process_may_send": sorted(ALLOWED_KEYS),
        "slug": slug,
        "known_market_data_types": {"market_data": R.SUB_MARKET_DATA,
                                    "trade": R.SUB_TRADE},
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not R.has_credentials():
        rep["error"] = "NO_CREDENTIALS"
        print("PMUS_WS_PROBE " + json.dumps(rep), flush=True)
        return 2
    try:
        rep["subscription_types"] = await probe_types(slug)
    except Exception as exc:                                   # noqa: BLE001
        rep["subscription_types_error"] = "%s: %s" % (type(exc).__name__,
                                                      str(exc)[:300])
    paths = []
    for p in CANDIDATE_PATHS:
        try:
            paths.append(await probe_path(p, slug))
        except Exception as exc:                               # noqa: BLE001
            paths.append({"path": p, "error": "%s: %s"
                          % (type(exc).__name__, str(exc)[:200])})
    rep["paths"] = paths
    try:
        rep["private_schema"] = await probe_private_schema(slug)
        rep["private_names"] = await probe_private_names()
    except Exception as exc:                                   # noqa: BLE001
        rep["private_schema_error"] = "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:300])
    if os.getenv("PMUS_WS_PROBE_DEPTH", "").strip() not in ("", "0"):
        try:
            rep["depth_end_to_end"] = await probe_depth_end_to_end(slug)
        except Exception as exc:                               # noqa: BLE001
            rep["depth_error"] = "%s: %s" % (type(exc).__name__,
                                             str(exc)[:300])
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as fh:
            json.dump(rep, fh, indent=1)
        rep["report_written"] = REPORT
    except Exception as exc:                                   # noqa: BLE001
        rep["report_error"] = str(exc)[:200]
    print("PMUS_WS_PROBE " + json.dumps(rep, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
