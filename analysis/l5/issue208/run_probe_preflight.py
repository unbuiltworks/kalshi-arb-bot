"""Run the probe preflight, or the post-settlement audit, from the shell.

`src/l5_probe_preflight.py` is pure and takes its evidence as arguments,
which makes it testable but not yet RUNNABLE by anyone but a test. This is
the entry point, so the owner or the deployment lane can check readiness
against live evidence without going through me.

USAGE, both read-only:

    # before the window -- paste one L5_LIVE_HB line into hb.json
    python3 analysis/l5/issue208/run_probe_preflight.py preflight \
        --heartbeat hb.json --market cpc-btc-updown-15m-...-1200z \
        --bid 0.62 --ask 0.63 --depth 2

    # after settlement -- hb.json plus the store's own state file
    python3 analysis/l5/issue208/run_probe_preflight.py audit \
        --heartbeat hb.json --inventory /data/.../inventory.json \
        --market cpc-btc-updown-15m-...-1200z

It builds the depth-2 plan with the REAL `TS.build_orders` so the plan
checked is the plan the engine would place, reads the store state as JSON
rather than importing an executor, and exits non-zero when the verdict is
not a pass -- so it can gate a script.

IT CHANGES NOTHING. No transport, no order, no limit, no gate. The exit
code is an opinion about evidence, never an authorization.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src import l5_probe_preflight as PF          # noqa: E402
from src import l5_two_sided_probe as TS          # noqa: E402


def _load(path: Optional[str]) -> Optional[Dict[str, Any]]:
    """A heartbeat line or a state file. Accepts a bare JSON object or a
    log line with a label in front of it, because that is how it is
    actually copied out of the tape."""
    if not path:
        return None
    raw = open(path, encoding="utf-8").read().strip()
    i = raw.find("{")
    if i < 0:
        raise SystemExit("no JSON object in %s" % path)
    return json.loads(raw[i:])


def _plan(a: argparse.Namespace, market: str) -> Optional[Dict[str, Any]]:
    """`market` is passed explicitly as a STRING.

    `--market` is `action="append"`, so `a.market` is a LIST, and the
    first version of this passed that list straight into `build_orders`,
    which refused every plan with NOT_THE_AUTHORIZED_MARKET. The checks
    all read correctly and the plan rows all failed -- a failure mode that
    looks exactly like a misconfigured environment, which is the worst
    possible disguise for a bug in the tool you reach for when you are
    already unsure of your configuration.
    """
    if a.bid is None or a.ask is None:
        return None
    return TS.build_orders(
        market=str(market), bid=a.bid, ask=a.ask,
        bid_depth=a.bid_depth, ask_depth=a.ask_depth,
        tick=a.tick, min_qty=a.min_qty, depth_ticks=int(a.depth),
        cash_envelope=D(a.cash), max_worst_case_loss=D(a.loss),
        budget_headroom=D(a.cash), book_age_s=D(a.book_age),
        gap_suspected=bool(a.gap))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("preflight", "audit", "window"))
    ap.add_argument("--lead-min", default="10",
                    help="window mode: minimum minutes of lead time")
    ap.add_argument("--heartbeat",
                    help="file holding one L5_LIVE_HB line or object")
    ap.add_argument("--market", action="append", default=[],
                    help="the EXACT pre-registered slug; repeatable")
    ap.add_argument("--inventory", help="the store's state file (audit)")
    ap.add_argument("--depth", default="2")
    ap.add_argument("--bid"), ap.add_argument("--ask")
    ap.add_argument("--bid-depth", default="500")
    ap.add_argument("--ask-depth", default="500")
    ap.add_argument("--tick", default="0.01")
    ap.add_argument("--min-qty", default="1")
    ap.add_argument("--cash", default="12")
    ap.add_argument("--loss", default="1.00")
    ap.add_argument("--book-age", default="0.4")
    ap.add_argument("--gap", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.mode == "window":
        # THE NEXT ELIGIBLE WINDOW, AND THE APPROVAL DOCUMENT FOR IT.
        #
        # Hand-computing a slug and an ISO timestamp under time pressure is
        # how the wrong window gets authorized, and `window_starts` is an
        # exact-match allowlist -- one wrong minute and every window is
        # refused WINDOW_NOT_IN_AUTHORIZED_SET. So this emits both from one
        # clock read. It writes nothing and approves nothing: the document
        # is only an approval once the OWNER puts it in the store.
        now = dt.datetime.now(dt.timezone.utc)
        lead = dt.timedelta(minutes=float(a.lead_min))
        start = now.replace(minute=(now.minute // 15) * 15, second=0,
                            microsecond=0)
        while start - now < lead:
            start += dt.timedelta(minutes=15)

        def iso(d):
            return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")

        slug = "cpc-btc-updown-15m-%s-%02d%02dz" % (
            start.strftime("%Y-%m-%d"), start.hour, start.minute)
        doc = {"approved": True,
               "nonce": "probe-depth2-%s" % start.strftime("%Y%m%d-%H%Mz"),
               "series": "cpc-btc-updown-15m-",
               "issued_utc": iso(now),
               "expires_utc": iso(start + dt.timedelta(minutes=30)),
               "max_windows": 1,
               "max_total_exposure_usd": "1.00",
               "max_daily_loss_usd": "5.00",
               "window_starts": [iso(start)]}
        if a.json:
            print(json.dumps({"slug": slug, "window_start_utc": iso(start),
                              "lead_s": int((start - now).total_seconds()),
                              "approval_document": doc}, indent=1))
        else:
            print("now                 %s" % iso(now))
            print("window start (UTC)  %s" % iso(start))
            print("lead time           %d s" % (start - now).total_seconds())
            print("slug                %s" % slug)
            print("\nL5_TS_MARKET=%s" % slug)
            print("L5_TS_EXPERIMENT_ID=%s" % doc["nonce"])
            print("\nL5_CAMPAIGN_APPROVAL (owner sets this; one line):")
            print(json.dumps(doc, separators=(",", ":")))
            print("\nNOTE: window_starts is an EXACT-MATCH allowlist. Any "
                  "other window is refused\n      "
                  "WINDOW_NOT_IN_AUTHORIZED_SET, which is the one-window "
                  "cap enforced by the\n      authorization layer rather "
                  "than by convention.")
        return 0

    if not a.heartbeat:
        print("REFUSED: --heartbeat is required for preflight and audit.")
        return 2
    hb = _load(a.heartbeat) or {}
    markets = [m for m in a.market if str(m).strip()]
    if not markets:
        print("REFUSED: no --market given. The probe's exact slug must be "
              "pre-registered; an unregistered market cannot be scored.")
        return 2

    if a.mode == "preflight":
        cal = hb.get("fill_calibration")
        if len(markets) > 1:
            print("REFUSED: a plan is priced for ONE market; pass a single "
                  "--market for preflight.")
            return 2
        plan = _plan(a, markets[0])
        out = PF.preflight(heartbeat=hb, plan=plan,
                           markets=markets,
                           calibration=cal if isinstance(cal, Mapping)
                           else None,
                           expect_depth_ticks=int(a.depth))
        # Exact-market evidence is mandatory for a live-window GO verdict.
        # A caller-supplied plan cannot substitute for the executor's slug.
        window = PF.exact_window_consistency(
            heartbeat=hb, plan=plan or {}, market=markets[0])
        out["checks"].extend(window["checks"])
        out["failed"].extend(window["failed"])
        out["ok"] = out["ok"] and window["ok"]
    else:
        inv = _load(a.inventory) or {}
        lat = (hb.get("completion_latency") or {})
        per = {m.get("market"): m for m in (lat.get("markets") or [])
               if isinstance(m, Mapping)}
        one = next((per[m] for m in markets if m in per), None)
        out = PF.audit(
            markets=markets, heartbeat=hb,
            leg_cost=inv.get("leg_cost") or {},
            positions=inv.get("positions") or {},
            fills=[dict(o, venue_order_id=o.get("venue_order_id"))
                   for o in (inv.get("submitted") or {}).values()
                   if str(o.get("market") or "") in markets] or None,
            latency=one,
            live_orders=inv.get("live_orders") or {},
            restarts=((hb.get("fill_calibration") or {})
                      .get("orders_straddling_a_restart")),
            fees=None,
            # POSITIVE EVIDENCE, read straight off the store's state: how
            # many of OUR fills built each leg, and whether adoption ever
            # replaced our book for this market. Both live in the state
            # file, so the audit no longer depends on the heartbeat's
            # unknown-basis list having caught up.
            fill_count=inv.get("fill_count") or {},
            gross_quarantine=inv.get("gross_quarantine") or {})

    if a.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        head = out.get("verdict") or ("PASS" if out["ok"] else "REFUSED")
        print("%s  ok=%s" % (head, out["ok"]))
        for r in out["checks"]:
            mark = {True: "ok  ", False: "FAIL", PF.UNKNOWN: "?   "}.get(
                r["ok"], "?   ")
            extra = {k: v for k, v in r.items()
                     if k not in ("check", "ok") and v not in (None, [], {})}
            print("  %s %-46s %s" % (mark, r["check"],
                                     json.dumps(extra, default=str)[:140]
                                     if extra else ""))
        if out["failed"]:
            print("FAILED: %s" % ", ".join(out["failed"]))
        if out["unknown"]:
            print("UNKNOWN (never a pass): %s" % ", ".join(out["unknown"]))
        print(out["labels"])
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
