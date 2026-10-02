"""Prospective L5 latent-quote shadow collector. READ-ONLY SIDECAR.

Emits hypothetical quotes only. There is no order path in this package.

Provenance emitted on every record: SESSION_ID, CODE_SHA, PREREG_HASH, UTC.
Output is append-only. Each record is also echoed to stdout under
`L5LQ_ROW ` so the tape survives without a volume (the pattern already used by
pmus-btc15m-tape and btc-ref-collector).

Label causality: every L5 label carries BOTH its exchange `event_t` and our
`recv_t`. A feature may use a label only when `recv_t <= tau`, so a label that
arrives late can score an earlier prediction but can never alter the earlier
state. Scoring uses `event_t`.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, subprocess, sys, time, uuid
from . import config as C, feed, state as S, models, arms, lifecycle as L

PREREG = "analysis/l5/latent_quote/PREREGISTRATION.md"
ROW = "L5LQ_ROW "


def _utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def code_sha():
    v = os.environ.get("L5LQ_CODE_SHA")
    if v:
        return v
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "UNKNOWN"
    except Exception:                                      # noqa: BLE001
        return "UNKNOWN"


def prereg_hash():
    for p in (PREREG, os.path.join(os.path.dirname(__file__), "..", "..", PREREG)):
        try:
            return hashlib.sha256(open(p, "rb").read()).hexdigest()
        except Exception:                                  # noqa: BLE001
            continue
    return os.environ.get("L5LQ_PREREG_HASH", "UNKNOWN")


class Sink:
    """Append-only, bounded, with stdout echo for log persistence."""

    def __init__(self, out_dir, max_mb=512):
        self.dir = out_dir
        self.max = max_mb * 1024 * 1024
        os.makedirs(out_dir, exist_ok=True)
        self.dropped = 0

    def write(self, rec):
        line = json.dumps(rec, separators=(",", ":"))
        print(ROW + line, flush=True)                      # durable without a volume
        p = os.path.join(self.dir, f"shadow_{rec['day']}.jsonl")
        try:
            if os.path.exists(p) and os.path.getsize(p) > self.max:
                self.dropped += 1
                return
            with open(p, "a") as fh:
                fh.write(line + "\n")
        except Exception:                                  # noqa: BLE001
            self.dropped += 1


def _norm_trades(raw, recv_t):
    out = []
    for r in raw or []:
        try:
            out.append({"t": float(r["timestamp"]), "recv_t": recv_t,
                        "p": float(r["price"]), "q": float(r["size"]),
                        "up": str(r.get("outcome", "")).lower().startswith("up"),
                        "buy": str(r.get("side", "")).upper() == "BUY",
                        "w": str(r.get("proxyWallet", "")).lower()})
        except Exception:                                  # noqa: BLE001
            continue
    out.sort(key=lambda x: x["t"])
    return out


def _norm_l5(raw, ws, recv_t):
    """Both timestamps retained. recv_t gates FEATURES; event_t gates SCORING."""
    out = []
    for r in raw or []:
        if str(r.get("slug", "")) != f"{C.SERIES_SLUG_PREFIX}{ws}":
            continue
        if str(r.get("type", "")).upper() != "TRADE":
            continue
        try:
            out.append({"t": float(r["timestamp"]), "recv_t": recv_t,
                        "p": float(r["price"]), "q": float(r["size"]),
                        "up": str(r.get("outcome", "")).lower().startswith("up"),
                        "buy": str(r.get("side", "")).upper() == "BUY"})
        except Exception:                                  # noqa: BLE001
            continue
    out.sort(key=lambda x: x["t"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float,
                    default=float(os.environ.get("L5LQ_MINUTES", "0") or 0))
    ap.add_argument("--out-dir", default=os.environ.get("L5LQ_OUT_DIR", C.OUT_DIR))
    a = ap.parse_args(argv)

    session_id = os.environ.get("L5LQ_SESSION_ID") or uuid.uuid4().hex[:16]
    sha, ph = code_sha(), prereg_hash()
    sink = Sink(a.out_dir)
    prov = {"session_id": session_id, "code_sha": sha, "prereg_hash": ph}

    # ---- restart detection, persisted beside the tape
    marker = os.path.join(a.out_dir, "session_state.json")
    prior = None
    try:
        prior = json.load(open(marker))
    except Exception:                                      # noqa: BLE001
        prior = None
    started = time.time()
    boundary = {"kind": "PROSPECTIVE_SAMPLE_START", **prov,
                "day": _day(started), "t": started, "utc": _utc(started),
                "restart_of_prior_session": (prior or {}).get("session_id"),
                "prior_last_heartbeat_utc": (prior or {}).get("last_heartbeat_utc"),
                "observation_gap_s": (None if not prior else
                                      round(started - prior.get("last_heartbeat_t", started), 1)),
                "lifetime_arms": list(C.LIFETIME_ARMS),
                "price_arms": list(C.ARMS), "side_models": list(C.SIDE_MODELS),
                "execution_models": list(C.EXECUTION_MODELS),
                "decision_s": C.DECISION_S, "book_poll_s": C.BOOK_POLL_S,
                "note": "SHADOW ONLY -- no order exists. A restart CENSORS any "
                        "queue state that spanned the gap; queue accounting "
                        "restarts fresh."}
    sink.write(boundary)
    print(f"PROSPECTIVE_SAMPLE_START={_utc(started)}\nCODE_SHA={sha}\n"
          f"PREREG_HASH={ph}\nSESSION_ID={session_id}", flush=True)

    live, ghosts = {}, []
    by_key = {}
    shadow_fills = []
    prev_dec = None
    mkt = None
    n_dec = n_place = n_cancel = n_ghost = 0
    day_seen = []
    deadline = (started + a.minutes * 60.0) if a.minutes > 0 else None

    while deadline is None or time.time() < deadline:
        now = time.time()
        ws = feed.window_start(now)
        if mkt is None or mkt["window_start"] != ws:
            try:
                nm = feed.market_for_window(ws)
            except Exception as e:                          # noqa: BLE001
                sink.write({"kind": "read_error", **prov, "day": _day(now),
                            "t": now, "utc": _utc(now), "err": str(e)[:200]})
                time.sleep(5); continue
            if nm is None:
                time.sleep(5); continue
            if mkt is not None:
                for o in list(live.values()):
                    bkz = {"bids": [], "asks": []}
                    oc, g = L.cancel(o, now, "WINDOW_END", prev_dec or {}, bkz)
                    n_cancel += 1
                    if g: ghosts.append(g); n_ghost += 1
            mkt = nm
            live, by_key, shadow_fills, prev_dec = {}, {}, [], None
            d = _day(now)
            if d not in day_seen:
                day_seen.append(d)

        try:
            recv0 = time.time()
            bk_up = feed.book(mkt["token_up"])
            bk_dn = feed.book(mkt["token_dn"])
            tr_raw = feed.market_trades(mkt["condition_id"], limit=500)
            l5_raw = feed.l5_activity(limit=100)
            recv_t = time.time()
        except Exception as e:                              # noqa: BLE001
            sink.write({"kind": "read_error", **prov, "day": _day(now), "t": now,
                        "utc": _utc(now), "err": str(e)[:200]})
            time.sleep(C.BOOK_POLL_S); continue

        tau = recv_t
        trades = _norm_trades(tr_raw, recv_t)
        l5_all = _norm_l5(l5_raw, ws, recv_t)
        # LEAKAGE GATES, both required for a FEATURE
        tr_feat = [x for x in trades if x["t"] < tau and x["recv_t"] <= tau]
        l5_feat = [x for x in l5_all if x["t"] < tau and x["recv_t"] <= tau]

        s = S.build(tau, mkt, bk_up, bk_dn, l5_feat, tr_feat, shadow_fills, prev_dec)
        preds = models.predict_all(s)
        n_dec += 1

        rec = {"kind": "decision", **prov, "day": _day(tau), "t": tau,
               "utc": _utc(tau),
               "class": "DISCOVERY" if len(day_seen) <= C.DISCOVERY_DAYS else "VALIDATION",
               "slug": mkt["slug"], "state": s, "predictions": preds,
               "label_gate": {"tau": tau, "n_l5_labels_feature_eligible": len(l5_feat),
                              "n_l5_labels_seen": len(l5_all),
                              "max_label_recv_t": max((x["recv_t"] for x in l5_all), default=None),
                              "rule": "feature requires event_t < tau AND recv_t <= tau"},
               "quotes": {}, "labels": "SHADOW ONLY -- no order exists, nothing "
                                       "submitted, filled, settled or realized"}

        for mname, p in preds.items():
            if mname not in C.EXECUTION_MODELS:
                continue
            if p["abstain"]:
                rec["quotes"][mname] = {"ABSTAIN": True}
                continue
            bk = bk_up if p["side"] == "up" else bk_dn
            qs = arms.build(p["side"], s, bk)
            rec["quotes"][mname] = qs
            s["_pred_p_up"], s["_pred_conf"] = p["p_up"], p["confidence"]
            for arm, q in qs.items():
                if not (q and q.get("status") == "PLACEABLE"):
                    continue
                pc_proj = None
                ob = s.get("l5_obs_dn_basis" if p["side"] == "up" else "l5_obs_up_basis")
                if ob is not None:
                    pc_proj = q["price"] + ob
                rule_ok = (pc_proj is None) or (pc_proj <= C.PAIR_COST_CAP + 1e-9)
                for lt in C.LIFETIME_ARMS:
                    key = (mname, arm, lt, p["side"])
                    cur = by_key.get(key)
                    if cur is not None and cur["state"] in (L.RESTING, L.PARTIAL):
                        ask = s.get(f"{cur['side']}_ask")
                        passive_ok = ask is None or cur["price"] < ask - 1e-9
                        rep, why = L.should_reprice(cur, now, p["side"], s,
                                                    passive_ok, rule_ok)
                        if not rep:
                            continue
                        oc, g = L.cancel(cur, now, why, s, bk)
                        n_cancel += 1
                        if g: ghosts.append(g); n_ghost += 1
                    o = L.place(now, mkt["slug"], mname, arm, lt, p["side"], q, s)
                    live[o["oid"]] = o
                    by_key[key] = o
                    n_place += 1

        for o in list(live.values()):
            bk = bk_up if o["side"] == "up" else bk_dn
            L.observe(o, now, trades, bk)
            if o["state"] == L.FULL and o["lifetime_arm"] == "R1_STICKY" \
                    and o["side_model"] == "S5_FULL" and o["price_arm"] == "P1_AT_BID" \
                    and not any(f["oid"] == o["oid"] for f in shadow_fills):
                shadow_fills.append({"oid": o["oid"], "up": o["side"] == "up",
                                     "p": o["price"], "q": o["filled_q"]})
        ghosts = [g for g in ghosts if now <= g.get("ghost_observe_until", 0)]
        for g in ghosts:
            bk = bk_up if g["side"] == "up" else bk_dn
            L.observe(g, now, trades, bk)

        rec["counts"] = {"decisions": n_dec, "placements_new": n_place,
                         "cancellations": n_cancel, "ghosts_created": n_ghost,
                         "live_orders": sum(1 for o in live.values()
                                            if o["state"] in (L.RESTING, L.PARTIAL)),
                         "ghosts_observing": len(ghosts),
                         "unique_days": len(day_seen), "sink_dropped": sink.dropped}
        rec["orders"] = list(live.values())
        rec["ghost_orders"] = ghosts
        rec["fill_state_is_provisional"] = True
        sink.write(rec)
        prev_dec = s

        if n_dec % 12 == 0:
            hb = {"kind": "heartbeat", **prov, "day": _day(now), "t": now,
                  "utc": _utc(now), "counts": rec["counts"]}
            sink.write(hb)
            try:
                json.dump({"session_id": session_id, "code_sha": sha,
                           "last_heartbeat_t": now, "last_heartbeat_utc": _utc(now)},
                          open(marker, "w"))
            except Exception:                              # noqa: BLE001
                pass
        time.sleep(max(0.0, C.DECISION_S - (time.time() - now)))

    print(f"decisions={n_dec} placements={n_place} cancels={n_cancel} "
          f"ghosts={n_ghost} days={day_seen}", flush=True)


if __name__ == "__main__":
    main()
