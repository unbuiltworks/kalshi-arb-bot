"""One-shot bounded preview session. Previews orders; cannot send one.

Runs in its OWN ephemeral service so the 24/7 L5 observer keeps its
structural guarantee of containing no path that can POST. The image carries
`l5_preview_session`, the shared order serializer and the signing module --
and no submit, cancel, modify or close-position path at all.

What one run establishes, from the venue's own answers:

  1. the executable market, echoed back by the authenticated feed rather
     than assumed from a slug we constructed;
  2. whether the representation `PmusTransport.submit()` sends today is
     accepted or rejected;
  3. whether the representation the published schema documents is accepted;
  4. whether an off-tick price is refused;
  5. whether a sub-minimum quantity is refused.

It submits nothing. The only POST path the transport will carry is
/v1/order/preview.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from decimal import Decimal as D

from src import l5_order_body as OB
from src import l5_preview_session as P
from src import pmus_auth as Q1

WINDOW_S = 900


def window_slug(now: dt.datetime) -> str:
    """The candidate slug for the window that is open right now.

    A candidate only: nothing is believed about it until the authenticated
    feed echoes the same marketSlug back with a tradable state.
    """
    m = (now.minute // 15) * 15
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (now.strftime("%Y-%m-%d"),
                                                now.hour, m)


def read_book(transport, slug: str) -> dict:
    """The authenticated book for one slug, or an explicit miss.

    A 404 here is ordinary, not exceptional: this venue publishes a BTC15m
    window's REST resources some minutes after the window opens, and an
    unhandled HTTPError would end the session on a market that is simply
    not listed yet.
    """
    try:
        r = transport.request(
            "GET", "/v1/markets/%s/book" % slug, None, Q1.GATEWAY) or {}
        return {"ok": True, "marketData": r.get("marketData") or {}}
    except BaseException as exc:                               # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def live_market(transport, now: dt.datetime, *, wait_s: float = 240.0,
                sleep=time.sleep, clock=None) -> dict:
    """Confirm the executable market from the AUTHENTICATED book.

    The venue echoes `marketSlug` and reports `state` plus both sides of the
    book. Identity and tradability come from that answer, never from the
    text of the slug we happened to construct -- that slug is only ever a
    candidate. Polls for a bounded period because the window may not be
    published the instant it opens, then gives up cleanly.
    """
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    deadline = now.timestamp() + wait_s
    attempts = []
    while True:
        t_now = clock()
        cand = window_slug(t_now)
        book = read_book(transport, cand)
        md = book.get("marketData") or {}
        bids, offers = md.get("bids") or [], md.get("offers") or []

        def top(rows):
            if not rows:
                return None
            px = (rows[0].get("px") or {}).get("value")
            return D(str(px)) if px else None

        out = {
            "candidate": cand,
            "venue_market_slug": md.get("marketSlug"),
            "identity_confirmed": md.get("marketSlug") == cand,
            "state": md.get("state"),
            "open_tradable": str(md.get("state") or "").upper().endswith("OPEN"),
            "best_bid": str(top(bids)) if top(bids) is not None else None,
            "best_ask": str(top(offers)) if top(offers) is not None else None,
            "two_sided": bool(bids and offers),
            "source": "authenticated GET /v1/markets/{slug}/book",
            "attempts": attempts,
        }
        attempts.append({"utc": t_now.isoformat(), "slug": cand,
                         "served": book.get("ok"),
                         "error": book.get("error"),
                         "state": md.get("state")})
        if out["identity_confirmed"] and out["open_tradable"] \
                and out["best_bid"]:
            return out
        if t_now.timestamp() >= deadline:
            out["gave_up_after_s"] = round(wait_s, 1)
            return out
        sleep(10.0)


def proposed_intent(mkt: dict) -> dict:
    """The order shape the bounded probe's first leg actually proposes.

    A maker OPEN joining the current best bid, at the envelope's bounded
    size. Price comes from the live authenticated book; the cap comes from
    the frozen probe manifest. Nothing here is invented for the preview --
    this is the shape that would be sent.
    """
    from src import l5_probe_manifest as PM
    bid = D(mkt["best_bid"])
    lam = PM.LAMBDA
    qty = (D(1) * lam).quantize(D("0.01"))          # one median L5 action
    cap = PM.probe_limits().max_usd_per_market
    if bid * qty > cap:
        qty = (cap / bid).quantize(D("0.01"))
    return {"client_order_id": "preview-no-submit",
            "market": mkt["venue_market_slug"],
            "side": "UP", "price": str(bid), "qty": str(qty),
            "mode": "MAKER"}


def run(*, wait_s: float = 240.0, sleep=time.sleep, clock=None) -> dict:
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    now = clock()
    t = P.PreviewOnlyTransport()
    out = {"utc": now.isoformat(),
           "api_key_fingerprint": P.key_fingerprint(),
           "credentials_present": Q1.has_credentials(),
           "LABEL": "PREVIEW ONLY -- no order was submitted",
           "armed": Q1.armed()}
    if out["armed"]:
        out["ABORT"] = "session reports ARMED; refusing to run"
        return out

    mkt = live_market(t, now, wait_s=wait_s, sleep=sleep, clock=clock)
    out["market"] = mkt
    if not (mkt["identity_confirmed"] and mkt["open_tradable"]
            and mkt["best_bid"]):
        out["ABORT"] = ("no confirmed OPEN market with a bid within the wait "
                        "window; nothing previewed")
        return out

    intent = proposed_intent(mkt)
    out["proposed_intent"] = intent
    slug = mkt["venue_market_slug"]

    def shot(label, body):
        rec = {"label": label, "body": P.redact(body)}
        try:
            resp = t.request("POST", P.PREVIEW_PATH,
                             OB.preview_envelope(body), Q1.API)
            rec.update(accepted=True, response=P.redact(resp))
        except P.MutationRefused:
            raise
        except BaseException as exc:                           # noqa: BLE001
            rec.update(accepted=False,
                       error=P.redact("%s: %s" % (type(exc).__name__, exc)))
        return rec

    body = OB.from_intent(intent, slug)
    out["submit_body"] = P.redact(body)
    out["preview_equals_submit_body"] = OB.differences(
        OB.preview_envelope(body)["request"], body
    )["identical_but_for_preview_fields"]

    out["proven_body"] = shot("PROVEN", body)

    if out["proven_body"]["accepted"]:
        off = dict(intent, price=str(D(intent["price"]) + D("0.005")))
        small = dict(intent, qty="0.005")
        c = {"off_tick": shot("OFF_TICK", OB.from_intent(off, slug)),
             "sub_minimum_qty": shot("SUB_MIN_QTY", OB.from_intent(small, slug))}
        c["tick_enforced"] = c["off_tick"]["accepted"] is False
        c["min_qty_enforced"] = c["sub_minimum_qty"]["accepted"] is False
        c["proven"] = bool(c["tick_enforced"] and c["min_qty_enforced"])
        out["constraints"] = c
    else:
        out["constraints"] = {
            "skipped": "the proposed order itself was refused; a refusal of "
                       "a bad shape would prove nothing",
            "proven": False}

    out["paths_touched"] = sorted({p for _, p in t.calls})
    out["mutations_sent"] = 0
    return out


def main(argv=None) -> int:
    rep = run()
    print("PMUS_PREVIEW " + json.dumps(rep, default=str, separators=(",", ":")),
          flush=True)
    return 0 if (rep.get("proven_body") or {}).get("accepted") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
