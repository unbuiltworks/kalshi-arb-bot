"""One fresh BTC15m window, end to end, on the box that holds the credentials.

    subscription at open -> authenticated two-sided book -> authenticated
    trades -> real preflight -> L5 actions -> frozen L5_NATIVE ->
    PMUS_TRANSLATION -> SAFETY_OVERLAY -> would-submit order, DISARMED

Prints ONE `L5_PROBE_DRYRUN` line carrying every verdict the authorization
gate needs. It places no order: the transport is disarmed, and while disarmed
it holds `ReadOnlyTransport`, which raises on any method but GET -- so this
process does not merely decline to send an order, it has no object able to
send one.

The book comes from the RAW tape replayed through the recorder's own
apply_events, so every state carries its true receipt time. The limits come
from `l5_probe_manifest`, so what this process reports is what it loaded, not
what a document says it should have loaded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import l5_authenticated_observation as OBS
from src import l5_live_execution as EX
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM

WINDOW_S = 900.0
SLUG_PREFIX = "cpc-btc-updown-15m-"


def current_slug(now: Optional[float] = None) -> str:
    import time
    now = time.time() if now is None else now
    t = dt.datetime.fromtimestamp(int(now // WINDOW_S) * WINDOW_S,
                                  dt.timezone.utc)
    return "%s%s-%02d%02dz" % (SLUG_PREFIX, t.strftime("%Y-%m-%d"),
                               t.hour, t.minute)


def observation(root: str, slug: str) -> Dict[str, Any]:
    """Authenticated book + trades for this market, from the raw tape."""
    from scripts import l5_raw_tape_observation as RAW
    rep = RAW.replay(RAW.tape_rows(Path(root), max_age_s=7200.0))
    st = rep["state"].get(slug) or {}
    books = RAW.book_states(st)
    trades = []
    for tr in (st.get("trades") or ()):
        t = OBS._epoch(tr.get("trade_time")) or OBS._epoch(tr.get("t"))
        if t is not None:
            trades.append(t)
    trades.sort()
    ev = OBS.evaluate(slug, {slug: books}, {slug: trades}, [])
    ev["frames_applied"] = rep["frames_applied"]
    ev["subscribed_at_open"] = bool(
        books and OBS._epoch(books[0]["t"]) is not None
        and (OBS._epoch(books[0]["t"]) - (OBS.window_start_epoch(slug) or 0))
        <= 60.0)
    return {"eval": ev, "books": books, "trades": trades}


def l5_actions_for(slug: str) -> List[dict]:
    """L5's own trades in this window, from the PUBLIC activity feed."""
    from src import l5_pmus_realtime_observer as O
    ws = OBS.window_start_epoch(slug)
    rows = O.l5_activity(limit=200) or []
    out = []
    for r in rows:
        t = r.get("timestamp")
        try:
            t = float(t)
        except (TypeError, ValueError):
            continue
        if ws is None or not (ws <= t <= ws + WINDOW_S):
            continue
        act = O.l5_to_pmus_action(r)
        if not act.get("pmus_side"):
            continue
        out.append({
            "action_key": O.action_key(r),
            "receipt_utc": dt.datetime.fromtimestamp(
                t, dt.timezone.utc).isoformat(),
            "l5": act,
            "pmus": {"slug": slug},
        })
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getenv("PMUS_WS_ROOT",
                                                "/state/pmus-ws"))
    ap.add_argument("--slug", default=None)
    ap.add_argument("--state", default=os.getenv(
        "L5_PROBE_STATE", "/state/pmus-ws/l5_probe_inventory.json"))
    a = ap.parse_args(argv)
    slug = a.slug or current_slug()
    out: Dict[str, Any] = {"market": slug,
                           "utc": dt.datetime.now(dt.timezone.utc).isoformat()}

    # 1-3. authenticated observation
    try:
        obs = observation(a.root, slug)
        out["observation"] = obs["eval"]
    except BaseException as exc:                               # noqa: BLE001
        out["observation"] = {"error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200])}
        obs = {"books": [], "trades": []}

    # 4. the REAL preflight, read-only
    try:
        from src import l5_pmus_transport as TP
        tp = TP.PmusTransport(market_slug=slug)
        try:
            out["preflight"] = tp.preflight()
        except TP.PreflightFailed as exc:
            out["preflight"] = json.loads(str(exc))
    except BaseException as exc:                               # noqa: BLE001
        out["preflight"] = {"preflight": "FAILED",
                            "fatal": "%s: %s" % (type(exc).__name__,
                                                 str(exc)[:200])}
        tp = None

    # 5. L5 actions through the frozen engine, DISARMED
    try:
        actions = l5_actions_for(slug)
        limits = PM.probe_limits()
        store = EX.InventoryStore(a.state)
        run = W.run_window(actions, obs["books"], market=slug,
                           window=slug[-5:], store=store, limits=limits,
                           transport=(tp or EX.NullTransport()),
                           lam=PM.LAMBDA)
        out["engine"] = {"l5_actions": len(actions),
                         "summary": run["summary"],
                         "refusals": [
                             {"action_key": r.get("action_key"),
                              "layer": r.get("refused_by_layer"),
                              "reason": r.get("refused_reason")}
                             for r in run["records"]
                             if r.get("refused_by_layer")][:10]}
    except BaseException as exc:                               # noqa: BLE001
        out["engine"] = {"error": "%s: %s" % (type(exc).__name__,
                                              str(exc)[:200])}

    # 6. the limits THIS PROCESS loaded, not the ones a document claims
    out["live_limits"] = PM.probe_limits().as_dict()
    out["worst_case"] = PM.worst_case()
    out["armed"] = bool(getattr(tp, "armed", False))
    out["labels"] = ("DISARMED end-to-end dry run. A would-submit order is an "
                     "intent the transport declined to send, never a fill. "
                     "Nothing here is realized, filled or settled.")
    print("L5_PROBE_DRYRUN " + json.dumps(out, separators=(",", ":"),
                                          default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
