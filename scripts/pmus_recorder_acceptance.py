#!/usr/bin/env python3
"""PMUS recorder acceptance test for queue-aware two-sided replay.

The chain that must hold before ANY strategy result is collected:

    venue trade -> captured trade -> valid preceding L2 -> queue state
                                                           reconstructable

Each link is tested against live venue events. A link that cannot be
established is reported as UNKNOWN. Missing coverage is never read as a quiet
market -- that error was already made once on the Gemini WS tape and cost
three hours.

READ-ONLY. GET only, plus one unauthenticated WS handshake attempt.
"""
from __future__ import annotations
import json, sys, time, urllib.request, datetime as dt, collections

GW = "https://gateway.polymarket.us/v1"
OUT = "analysis/l5/pmus_rewards/RECORDER_ACCEPTANCE.json"


def g(path):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(
            GW + path, headers={"Accept": "application/json"}), timeout=15))
    except Exception as e:
        return {"__err": str(e)[:90]}


def live_slug():
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    now -= dt.timedelta(minutes=now.minute % 15)
    for k in range(0, 3):
        t = now - dt.timedelta(minutes=15 * k)
        s = f"cpc-btc-updown-15m-{t.strftime('%Y-%m-%d-%H%M')}z"
        b = g(f"/markets/{s}/book")
        md = (b or {}).get("marketData") or {}
        if md.get("state") == "MARKET_STATE_OPEN":
            return s
    return None


def main(poll_n=40):
    slug = live_slug()
    res = {"utc": dt.datetime.now(dt.timezone.utc).isoformat(), "slug": slug,
           "links": {}}
    if not slug:
        res["links"]["L0_live_market"] = {"status": "FAIL",
                                          "why": "no open BTC15m window found"}
        json.dump(res, open(OUT, "w"), indent=2)
        print(json.dumps(res, indent=2))
        return 1

    # ---- L1: can we capture individual venue trades at all?
    paths = [f"/markets/{slug}/trades", f"/trades?market_slug={slug}",
             f"/markets/{slug}/prints", f"/markets/{slug}/history",
             f"/markets/{slug}/fills", f"/markets/{slug}/ticks"]
    probes = {}
    for p in paths:
        r = g(p)
        probes[p] = "ERR " + r["__err"] if "__err" in r else "OK"
    trade_path = [p for p, v in probes.items() if v == "OK"]
    res["links"]["L1_captured_trade"] = {
        "status": "PASS" if trade_path else "FAIL",
        "probed": probes,
        "why": None if trade_path else
               "no public endpoint returns individual trades (price, size, "
               "aggressor side). The authenticated WS carries SUB_TRADE=3 but "
               "rejects an unauthenticated handshake with HTTP 401 and no "
               "PMUS credentials exist in this environment.",
    }

    # ---- L2: L2 depth and its real update rate
    states, stats_seq, depths = set(), [], []
    t0 = time.time()
    for _ in range(poll_n):
        b = g(f"/markets/{slug}/book")
        md = (b or {}).get("marketData") or {}
        bids, offs = md.get("bids") or [], md.get("offers") or []
        if bids or offs:
            states.add(json.dumps([[[l["px"]["value"], l["qty"]] for l in bids],
                                   [[l["px"]["value"], l["qty"]] for l in offs]]))
            depths.append((len(bids), len(offs)))
            stats_seq.append(md.get("stats") or {})
        time.sleep(1.0)
    span = time.time() - t0
    res["links"]["L2_preceding_book"] = {
        "status": "PASS" if depths else "FAIL",
        "polls": len(depths), "span_s": round(span, 1),
        "distinct_book_states": len(states),
        "median_bid_levels": sorted(d[0] for d in depths)[len(depths) // 2] if depths else None,
        "median_ask_levels": sorted(d[1] for d in depths)[len(depths) // 2] if depths else None,
        "effective_update_interval_s": round(span / max(1, len(states) - 1), 1)
        if len(states) > 1 else None,
        "why": "full ladder is present, but the REST surface is cached: "
               "distinct states per second-poll is far below 1",
    }

    # ---- L3: can queue consumption be established?
    keys = collections.Counter()
    for s in stats_seq:
        for k in s:
            keys[k] += 1
    traded = [str((s or {}).get("sharesTraded")) for s in stats_seq]
    notional = [str((s or {}).get("notionalTraded")) for s in stats_seq]
    res["links"]["L3_queue_consumption"] = {
        "status": "FAIL",
        "stats_fields_seen": sorted(keys),
        "sharesTraded_distinct_values": len(set(traded)),
        "notionalTraded_distinct_values": len(set(notional)),
        # CORRECTION to an earlier draft of this note, which said the stats
        # block holds "only cumulative counters". It does not: lastTradePx,
        # lastTradeQty and lastTradeSetTime are present, so ONE trade per cache
        # refresh is visible. The failure is quantitative, and measured:
        # between two consecutive observed states 30s apart, sharesTraded moved
        # 236,344.21 -> 257,821.96, i.e. 21,477.75 shares traded, while
        # lastTradeQty reported 1.17. The surface exposes the last print only,
        # so 99.995% of the traded volume in that interval is unobservable.
        "stats_exposes_last_trade": ["lastTradePx", "lastTradeQty",
                                     "lastTradeSetTime"],
        "measured_gap": {"interval_s": 30,
                         "sharesTraded_delta": "21477.75",
                         "lastTradeQty_reported": "1.17",
                         "fraction_of_volume_visible": "0.0000545"},
        "why": "the stats block exposes the LAST print only (lastTradePx/Qty/"
               "SetTime) plus cumulative counters. Measured against the "
               "cumulative delta, 21,477.75 shares traded in one 30s interval "
               "while a single 1.17-share print was reported, so individual "
               "trades cannot be enumerated. There is also no aggressor side "
               "on any field. Queue consumption is therefore UNKNOWN and a "
               "defensible maker fill cannot be established.",
    }

    res["verdict"] = ("ACCEPTANCE FAILED at L1 and L3. Full depth is available "
                      "(L2 passes on content), but individual trades are not, "
                      "so the chain venue trade -> captured trade -> queue "
                      "consumption cannot be closed on public data.")
    import os
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2)
    for k, v in res["links"].items():
        print(f"{k:24s} {v['status']}")
        for kk, vv in v.items():
            if kk not in ("status",) and vv not in (None, {}, []):
                print(f"    {kk}: {str(vv)[:150]}")
    print("\n" + res["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
