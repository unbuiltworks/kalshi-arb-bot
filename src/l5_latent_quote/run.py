"""Prospective shadow collector. READ-ONLY. Emits hypothetical quotes only.

Usage:  python3 -m src.l5_latent_quote.run --minutes 20
Writes one JSONL per UTC day under config.OUT_DIR. Touches nothing else.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, time
from . import config as C, feed, state as S, models, arms, lifecycle as L


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def _norm_trades(raw):
    out = []
    for r in raw or []:
        try:
            out.append({"t": float(r["timestamp"]), "p": float(r["price"]),
                        "q": float(r["size"]),
                        "up": str(r.get("outcome", "")).lower().startswith("up"),
                        "buy": str(r.get("side", "")).upper() == "BUY",
                        "w": str(r.get("proxyWallet", "")).lower()})
        except Exception:                                  # noqa: BLE001
            continue
    out.sort(key=lambda x: x["t"])
    return out


def _norm_l5(raw, ws):
    out = []
    for r in raw or []:
        if str(r.get("slug", "")) != f"{C.SERIES_SLUG_PREFIX}{ws}":
            continue
        if str(r.get("type", "")).upper() != "TRADE":
            continue
        try:
            out.append({"t": float(r["timestamp"]), "p": float(r["price"]),
                        "q": float(r["size"]),
                        "up": str(r.get("outcome", "")).lower().startswith("up"),
                        "buy": str(r.get("side", "")).upper() == "BUY"})
        except Exception:                                  # noqa: BLE001
            continue
    out.sort(key=lambda x: x["t"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--out-dir", default=C.OUT_DIR)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    deadline = time.time() + a.minutes * 60.0

    live = {}                      # oid -> shadow order
    by_key = {}                    # (model, arm, side) -> the one resting order
    n_reprice = 0
    shadow_fills = []
    prev_dec = None
    mkt = None
    n_dec = n_place = 0
    day_seen = []

    while time.time() < deadline:
        now = time.time()
        ws = feed.window_start(now)
        if mkt is None or mkt["window_start"] != ws:
            try:
                mkt = feed.market_for_window(ws)
            except Exception as e:                          # noqa: BLE001
                print(f"  market resolve failed: {e}"); time.sleep(5); continue
            if mkt is None:
                time.sleep(5); continue
            for o in live.values():
                L.cancel_for_reprice(o, now)
            live, by_key, shadow_fills, prev_dec = {}, {}, [], None
            d = _day(now)
            if d not in day_seen:
                day_seen.append(d)
            print(f"\n== window {mkt['slug']}  day {d} "
                  f"({'DISCOVERY' if len(day_seen) <= C.DISCOVERY_DAYS else 'VALIDATION'})")

        try:
            bk_up = feed.book(mkt["token_up"])
            bk_dn = feed.book(mkt["token_dn"])
            trades = _norm_trades(feed.market_trades(mkt["condition_id"], limit=500))
            l5raw = feed.l5_activity(limit=100)
        except Exception as e:                              # noqa: BLE001
            print(f"  read failed: {e}"); time.sleep(C.BOOK_POLL_S); continue

        # LEAKAGE GATE: strictly before the decision instant
        tr_prior = [x for x in trades if x["t"] < now]
        l5_prior = _norm_l5(l5raw, ws)
        l5_prior = [x for x in l5_prior if x["t"] < now]

        s = S.build(now, mkt, bk_up, bk_dn, l5_prior, tr_prior, shadow_fills, prev_dec)
        preds = models.predict_all(s)
        n_dec += 1

        rec = {"kind": "decision", "t": now, "day": _day(now),
               "class": "DISCOVERY" if len(day_seen) <= C.DISCOVERY_DAYS else "VALIDATION",
               "state": s, "predictions": preds, "quotes": {}, "labels": "SHADOW ONLY -- "
               "no order exists, nothing submitted, filled, settled or realized"}

        for mname, p in preds.items():
            if p["abstain"]:
                rec["quotes"][mname] = {"ABSTAIN": True}
                continue
            bk = bk_up if p["side"] == "up" else bk_dn
            qs = arms.build(p["side"], s, bk)
            rec["quotes"][mname] = qs
            if mname == "S5_FULL":                       # one arm set carried forward
                for arm, q in qs.items():
                    if not (q and q.get("status") == "PLACEABLE"):
                        continue
                    key = (mname, arm, p["side"])
                    cur = by_key.get(key)
                    if cur is not None and cur["state"] in (L.RESTING, L.PARTIAL):
                        if abs(cur["price"] - q["price"]) < 1e-9:
                            continue                     # D2: unchanged price keeps resting
                        L.cancel_for_reprice(cur, now)    # D2: explicit, never silent
                        n_reprice += 1
                    o = L.place(now, mkt["slug"], mname, arm, p["side"], q)
                    live[o["oid"]] = o
                    by_key[key] = o
                    n_place += 1

        for o in list(live.values()):
            bk = bk_up if o["side"] == "up" else bk_dn
            L.observe(o, now, trades, bk)
            if o["state"] == L.FULL and not any(
                    f.get("oid") == o["oid"] for f in shadow_fills):
                shadow_fills.append({"oid": o["oid"], "up": o["side"] == "up",
                                     "p": o["price"], "q": o["filled_q"]})

        rec["n_decisions"] = n_dec
        rec["n_placements_new"] = n_place
        rec["n_repriced"] = n_reprice
        rec["n_live"] = sum(1 for o in live.values() if o["state"] in (L.RESTING, L.PARTIAL))
        rec["fill_state_is_provisional"] = True   # D1: window-close pass is authoritative
        rec["orders"] = [{k: v for k, v in o.items() if k != "events"} |
                         {"events": o["events"]} for o in live.values()]
        with open(os.path.join(a.out_dir, f"shadow_{_day(now)}.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        prev_dec = s
        time.sleep(max(0.0, C.DECISION_S - (time.time() - now)))

    print(f"\ndecisions={n_dec} new_placements={n_place} repriced={n_reprice} days={day_seen}")
    print("SHADOW ONLY. No order was created, submitted, previewed or priced for transmission.")


if __name__ == "__main__":
    main()
