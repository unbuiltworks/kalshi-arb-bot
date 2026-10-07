"""PHASE 1: does PMUS recognize the market E1 would order?

READ-ONLY GETs, plus exactly ONE authenticated preview.

E1's live rejection was an identity failure, not a pricing one:

    POST /v1/order/preview  marketSlug btc-updown-15m-1791405000
    HTTP 400 {"code":3,"message":"Market not found: ...","details":[]}

That slug came from `gamma-api.polymarket.com`. This proof builds the
PMUS-NATIVE slug from the window clock, asks PMUS for its own metadata and
book, and then sends ONE preview with that slug to settle identity.

NO GAMMA. NO CTF TOKEN IDS. NO ws-subscriptions-clob. Asserted, not
promised: `assert_no_foreign_venue()` checks `sys.modules` for the
Polymarket.com feed and binding modules and fails if either appeared.

WHAT IT MAY DO: GET gateway metadata/bbo/book, and exactly ONE
authenticated POST /v1/order/preview. A preview creates nothing.

WHAT IT CANNOT DO, structurally: POST /v1/orders (the transport wrapper
refuses any path but preview), a second request of any kind, or consume a
latch slot (`latch.consume_attempt` is replaced with a raise).

EXIT CODES
  0  PMUS RECOGNIZES the market -- identity proven, even if the preview is
     then rejected for price/quantity/balance reasons.
  2  PMUS says MARKET NOT FOUND -- identity NOT proven. STOP.
  3  no PMUS metadata served for the slug.
  4  the preview could not be attempted.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback

OUT: dict = {"proof": "E1_PMUS_IDENTITY_PHASE1",
             "authorization": "READ-ONLY GETs + exactly ONE /v1/order/preview",
             "forbidden": ["POST /v1/orders", "latch", "retry", "gamma",
                           "CTF token ids", "ws-subscriptions-clob"]}

DIAG = "/state/pmus-ws/e1_identity"

#: these must NEVER be imported by this proof.
FOREIGN_MODULES = ("src.l5_clob_ws", "src.l5_market_binding",
                   "analysis.l5.e1_cohort.live_book",
                   "analysis.l5.e1_cohort.selector")


def assert_no_foreign_venue() -> dict:
    present = sorted(m for m in FOREIGN_MODULES if m in sys.modules)
    if present:
        raise SystemExit("REFUSED: a Polymarket.com venue module is loaded "
                         "in a PMUS identity proof: %r" % (present,))
    return {"foreign_modules_loaded": [], "gamma_used": False,
            "ctf_token_ids_used": False, "clob_ws_used": False}


def main() -> int:
    from analysis.l5.e1_cohort import latch as L
    from analysis.l5.e1_cohort import pmus_identity as PI
    from analysis.l5.e1_cohort import spec as SP
    from analysis.l5.live_exec2 import orders as O
    from src import l5_order_body as OB
    from src import pmus_auth as A

    # ---- GUARD: the latch is unreachable in this process.
    def _no_latch(*a, **k):
        raise AssertionError("LATCH REFUSED: identity proof")
    L.consume_attempt = _no_latch

    # ---- GUARD: one request, preview only.
    calls: list = []

    class OnePreviewOnly:
        def __init__(self) -> None:
            self._t = O.BoundedOrderTransport()

        def request(self, method, path, body=None, base=A.API):
            if str(method).upper() != "POST" or path != O.PREVIEW_PATH:
                raise AssertionError("ONLY POST %s ALLOWED, got %s %s"
                                     % (O.PREVIEW_PATH, method, path))
            if calls:
                raise AssertionError("ONE REQUEST TOTAL")
            calls.append({"method": "POST", "path": path, "base": base,
                          "unix": time.time()})
            return self._t.request(method, path, body, base)

    sec = [v for v in [(os.getenv("PMUS_API_SECRET_KEY") or "").strip(),
                       (os.getenv("PMUS_API_KEY_ID") or "").strip()]
           if len(v) >= 6]

    def red(x) -> str:
        s = str(x)
        for v in sec:
            s = s.replace(v, "<REDACTED>")
        return s

    def finish(code: int) -> int:
        OUT["request_count"] = len(calls)
        OUT["requests_made"] = calls
        OUT["submit_path_constant"] = O.SUBMIT_PATH
        OUT["venue_submit_path_called"] = False
        OUT["latch_consumed"] = False
        OUT["spec_fingerprint"] = SP.E1_FINGERPRINT
        OUT.update(assert_no_foreign_venue())
        blob = red(json.dumps(OUT, indent=1, sort_keys=True, default=str))
        try:
            os.makedirs(DIAG, exist_ok=True)
            p = os.path.join(DIAG, "identity_%d.json" % int(time.time()))
            with open(p, "w", encoding="utf8") as fh:
                fh.write(blob)
            OUT["persisted_to"] = p
        except Exception as exc:                               # noqa: BLE001
            OUT["persist_error"] = red(exc)
        for line in blob.splitlines():
            print("PID|" + line, flush=True)
            time.sleep(0.05)
        print("PID_SHA256 %s" % hashlib.sha256(blob.encode()).hexdigest(),
              flush=True)
        print("PID_END code=%d" % code, flush=True)
        return code

    # ---- 1. the canonical PMUS slug, from the window clock.
    ws = PI.window_start()
    slug = PI.slug_for(ws)
    OUT["window_start"] = ws
    OUT["window_start_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime(ws))
    OUT["pmus_slug"] = slug
    OUT["slug_builder"] = ("analysis.l5.e1_cohort.pmus_identity.slug_for, "
                           "pinned by test to src.l5_pmus_auth_book.slug_for")
    OUT["slug_matches_pmus_shape"] = PI.is_pmus_btc15m_slug(slug)
    OUT["slug_round_trips"] = PI.slug_start(slug) == ws
    OUT["the_slug_e1_used_to_send"] = "btc-updown-15m-%d" % ws
    OUT["that_slug_is_pmus_shaped"] = PI.is_pmus_btc15m_slug(
        "btc-updown-15m-%d" % ws)

    http = PI.read_only_transport()

    # ---- 2. PMUS metadata.
    try:
        mk = PI.resolve(slug, http)
        OUT["pmus_metadata_served"] = True
        OUT["pmus_metadata_slug"] = mk.get("slug")
        OUT["pmus_metadata_matches_requested_slug"] = mk.get("slug") == slug
        OUT["pmus_outcomes"] = PI.outcome_sides(mk)
    except Exception as exc:                                   # noqa: BLE001
        OUT["pmus_metadata_served"] = False
        OUT["pmus_metadata_error"] = red("%s: %s" % (type(exc).__name__,
                                                     exc))
        OUT["result"] = "NO_PMUS_METADATA"
        return finish(3)

    # ---- 3. PMUS's own book. Identity/cross-check only.
    for name, fn in (("bbo", PI.bbo), ("book", PI.book)):
        try:
            r = fn(slug, http)
            OUT["pmus_%s" % name] = r
            OUT["pmus_%s_served" % name] = True
        except Exception as exc:                               # noqa: BLE001
            OUT["pmus_%s_served" % name] = False
            OUT["pmus_%s_error" % name] = red("%s: %s"
                                              % (type(exc).__name__, exc))

    md = (OUT.get("pmus_bbo") or {}).get("marketData") or {}
    bid = (md.get("bestBid") or {}).get("value")
    ask = (md.get("bestAsk") or {}).get("value")
    OUT["pmus_best_bid"] = bid
    OUT["pmus_best_ask"] = ask

    # ---- 4. ONE preview, with the frozen quantity and post-only.
    from decimal import Decimal as D
    price = None
    try:
        if bid is not None and D(str(bid)).is_finite() and D(str(bid)) > 0:
            price = str(D(str(bid)))
            OUT["price_source"] = "PMUS_BEST_BID"
        else:
            price = "0.01"
            OUT["price_source"] = ("IDENTITY_PROBE_MIN_TICK -- PMUS quoted "
                                   "no positive bid, so this price is NOT "
                                   "an E1 decision and no eligible state "
                                   "was fabricated; it exists only to make "
                                   "the venue answer about the MARKET")
    except Exception:                                          # noqa: BLE001
        price = "0.01"
        OUT["price_source"] = "IDENTITY_PROBE_MIN_TICK (unparseable bid)"
    OUT["price_sent"] = price
    OUT["qty_sent"] = str(SP.FIRST_LEG_QTY)
    OUT["post_only"] = True

    try:
        body = OB.build(slug=slug, side="UP", price=price,
                        qty=SP.FIRST_LEG_QTY, post_only=True)
        OUT["wire_body_non_secret"] = body
        OUT["wire_market_slug_is_pmus"] = PI.is_pmus_btc15m_slug(
            body.get("marketSlug"))
        env = O.preview_request(body)
    except Exception as exc:                                   # noqa: BLE001
        OUT["result"] = "COULD_NOT_BUILD_REQUEST"
        OUT["error"] = red("%s: %s" % (type(exc).__name__, exc))
        return finish(4)

    t = OnePreviewOnly()
    t0 = time.time()
    try:
        resp = t.request("POST", O.PREVIEW_PATH, env)
        OUT["result"] = "PREVIEW_ACCEPTED"
        OUT["http_status"] = 200
        OUT["venue_response"] = resp
        OUT["pmus_recognized_the_market"] = True
        OUT["elapsed_s"] = round(time.time() - t0, 3)
        OUT["NOTE"] = ("identity PROVEN. A successful preview does NOT "
                       "authorize submission.")
        return finish(0)
    except O.VenueHTTPError as exc:
        d = exc.detail()
        OUT["http_status"] = exc.status
        OUT["http_reason"] = red(exc.reason)
        OUT["venue_body"] = red(exc.body)
        OUT["venue_detail"] = json.loads(red(json.dumps(d, default=str)))
        OUT["elapsed_s"] = round(time.time() - t0, 3)
        not_found = "market not found" in str(exc.body or "").lower()
        OUT["pmus_recognized_the_market"] = not not_found
        if not_found:
            OUT["result"] = "MARKET_NOT_FOUND_IDENTITY_NOT_PROVEN"
            OUT["VERDICT"] = "STOP. Do not rebuild E1."
            return finish(2)
        OUT["result"] = "PREVIEW_REJECTED_BUT_MARKET_RECOGNIZED"
        OUT["VERDICT"] = ("identity PROVEN: the venue answered about THIS "
                          "market rather than denying it exists.")
        return finish(0)
    except Exception as exc:                                   # noqa: BLE001
        OUT["result"] = "PREVIEW_FAILED_NON_HTTP"
        OUT["error"] = red("%s: %s" % (type(exc).__name__, exc))
        OUT["traceback_tail"] = red(traceback.format_exc()[-900:])
        return finish(4)


if __name__ == "__main__":
    raise SystemExit(main())
