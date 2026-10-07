"""LIVE readiness proof: the PMUS decision feed against the REAL venue feed.

WHY THIS EXISTS. The suite can prove the readiness LOGIC with an injected
book. It cannot prove that an actual PMUS BTC15m window reaches a usable
two-sided authenticated book at all. This runs the production readiness
path -- the same `PmusWindowProvider` bound, the same `PmusDecisionFeed`
health predicate -- against the real `api.polymarket.us` feed and reports
whether the pinned slug's book becomes fresh, two-sided and
authenticated-sourced.

WHAT CHANGED FROM THE POLYMARKET.COM VERSION. The old check proved
BOTH_TOKENS_STREAMING on two CTF token ids over the public
`ws-subscriptions-clob.polymarket.com` feed. PMUS has no CTF token ids and
no public market websocket: identity is `marketSlug` + `outcomeSide`, and
the book arrives over an AUTHENTICATED socket. So this check:

  * names a PMUS slug (`cpc-btc-updown-15m-YYYY-MM-DD-HHMMz`), never a
    token id;
  * necessarily USES CREDENTIALS, because the only PMUS decision feed is
    authenticated. `credentials_used` is therefore true and says so. That
    is a signing capability, not an ordering capability.

ORDERING IS STILL DISABLED BY CONSTRUCTION, not by a flag:

  * it imports NO latch, NO order body, NO preview gate and NO order
    submission module -- `assert_no_order_path_imported()` checks
    `sys.modules` AFTER the work is done and fails if any appeared;
  * it reads `PMUS_E1_*` for nothing. It does not arm, and arming it
    would change nothing here;
  * the only network it performs is PMUS discovery/metadata reads and the
    authenticated book socket. It POSTs nothing.

It exits 0 on a usable book, 2 on a bounded readiness timeout (reporting
the health reason), 3 if no window could be identified.

Run:
    python3 -m scripts.l5_e1_ws_readiness_check
"""
from __future__ import annotations

import json
import sys
import time

from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import pmus_provider as PV
from analysis.l5.e1_cohort import pmus_selector as SEL

#: anything here means this check reached a money-moving module. It must
#: not, and the check proves it rather than asserting it in prose.
#:
#: `src.pmus_auth` is deliberately ABSENT from this list and present in
#: the process: AuthBook signs its own subscription. Signing is not
#: ordering, and the modules that can actually place or preview an order
#: are the ones listed.
FORBIDDEN_MODULES = (
    "analysis.l5.live_exec2.orders",
    "analysis.l5.e1_cohort.latch",
    "src.l5_order_body",
    "src.l5_preview_gate",
)

#: Polymarket.com identity must not appear anywhere in a PMUS proof.
FORBIDDEN_IDENTITY_MODULES = (
    "src.l5_clob_ws",
    "src.l5_market_binding",
    "src.l5_book_shadow",
)


def assert_no_order_path_imported() -> dict:
    present = sorted(m for m in FORBIDDEN_MODULES if m in sys.modules)
    if present:
        raise SystemExit(
            "REFUSED: an order-capable module is loaded in a read-only "
            "readiness check: %r" % (present,))
    foreign = sorted(m for m in FORBIDDEN_IDENTITY_MODULES
                     if m in sys.modules)
    if foreign:
        raise SystemExit(
            "REFUSED: a Polymarket.com identity module is loaded in a "
            "PMUS-native readiness check: %r" % (foreign,))
    return {"forbidden_modules_loaded": [],
            "polymarket_com_identity_modules_loaded": [],
            "ordering_disabled": True,
            "venue_post": "none"}


def candidate_starts(now: float, n: int = 4) -> list:
    out = [SEL.window_start_for(now)]
    out += [s for s in SEL.next_window_starts(now, n) if s not in out]
    return out


def main() -> int:
    out: dict = {"check": "E1_PMUS_BOOK_READINESS_LIVE",
                 "venue": "polymarket_us",
                 "identity": "slug_plus_outcome_side",
                 "ctf_token_ids_used": False,
                 "timing_authority": PBK.TIMING_PMUS_AUTHENTICATED_WS,
                 "rest_is_sufficient_timing_authority": False,
                 "timeout_s": PV.WS_READY_TIMEOUT_S,
                 "poll_s": PV.WS_READY_POLL_S,
                 "max_book_age_s": PBK.MAX_BOOK_AGE_S,
                 "ordering_disabled": True,
                 "venue_post": "none",
                 # the ONLY PMUS decision feed is authenticated. This is a
                 # signing capability, not an ordering capability.
                 "credentials_used": True}

    now = time.time()
    pin = None
    tried = []
    for start in candidate_starts(now):
        try:
            pin = SEL.select_window(start)
            break
        except Exception as exc:                            # noqa: BLE001
            tried.append({"window_start": start,
                          "refused": "%s: %s" % (type(exc).__name__, exc)})
    out["windows_tried"] = tried
    if pin is None:
        out["result"] = "NO_IDENTIFIABLE_WINDOW"
        out.update(assert_no_order_path_imported())
        print(json.dumps(out, indent=1, sort_keys=True, default=str))
        return 3

    out["slug"] = pin["slug"]
    out["market_id"] = str(pin.get("market_id") or "")
    out["outcome_up"] = str(pin["outcome_up"])
    out["outcome_down"] = str(pin["outcome_down"])

    feed = PBK.PmusDecisionFeed(clock=time.time)
    feed.start()
    t0 = time.time()
    try:
        try:
            health = PV.await_pmus_book_ready(
                feed, pin, now_fn=time.time, sleep_fn=time.sleep,
                timeout_s=PV.WS_READY_TIMEOUT_S,
                poll_s=PV.WS_READY_POLL_S)
        except PV.FeedNotReady as exc:
            h = feed.health(pin)
            out["result"] = "FEED_NOT_READY"
            out["reason"] = str(exc)
            out["elapsed_s"] = round(time.time() - t0, 3)
            out["health_why"] = h.get("why")
            out["health_market"] = h.get("market")
            out["health_source"] = h.get("source")
            out["health_stream_age_s"] = h.get("stream_age_s")
            out["health_book_updates"] = h.get("book_updates")
            out.update(assert_no_order_path_imported())
            print(json.dumps(out, indent=1, sort_keys=True, default=str))
            return 2

        out["result"] = "PINNED_MARKET_STREAMING"
        out["elapsed_s"] = round(time.time() - t0, 3)
        out["readiness"] = health.get("readiness")
        out["proven_for_the_selected_market"] = health.get(
            "proven_for_the_selected_market")
        out["health_source"] = health.get("source")
        out["health_stream_age_s"] = health.get("stream_age_s")
        out["health_book_updates"] = health.get("book_updates")
        out["health_market_state"] = health.get("market_state")

        book = feed.book(pin)
        # THE INVARIANT THIS CHECK EXISTS FOR: the book that would cause a
        # decision names the exact slug that would be serialized.
        out["book_market"] = book.get("market")
        out["book_slug_equals_pinned_slug"] = (
            str(book.get("market")) == str(pin["slug"]))
        out["first_leg"] = {k: book.get("first_leg", {}).get(k)
                            for k in ("side", "outcome_side", "slug",
                                      "best_bid", "best_ask",
                                      "size_at_our_price")}
        out["complement"] = {k: book.get("complement", {}).get(k)
                             for k in ("side", "outcome_side", "slug",
                                       "best_bid", "best_ask")}
    finally:
        feed.stop()

    out.update(assert_no_order_path_imported())
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
