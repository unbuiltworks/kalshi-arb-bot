"""LAYER-1 external BTC collector for the LEAD/LAG study. READ-ONLY.

THREE LAYERS, THREE ARTIFACTS, NEVER MERGED AT COLLECTION TIME:

  LAYER 1  external spot/futures            <- THIS FILE
  LAYER 2  the published LS-LRRR-XBTUSD     <- btc5m_recorder's index_price
  LAYER 3  the Gemini BTC05M prediction book<- btc5m_recorder's checkpoints
                                               and p_depth rows

Layers 2 and 3 are NOT reimplemented here. The recorder already collects
them, is read-only, is proven in production and has an AST guard against any
order path; duplicating it would create a second, unproven source of the
same truth. This file collects LAYER 1 ONLY, and the analysis joins the
three artifacts afterwards. That is also what keeps the layers structurally
distinct rather than distinct by promise.

EXTERNAL SPOT IS NEVER A SETTLEMENT REFERENCE. Nothing in this file may be
substituted for the authoritative LRRR; it is an independent layer whose
whole purpose is to be compared against it.

ADMISSIBILITY IS ENFORCED, NOT ASSUMED. Only venues the probe admitted are
polled, and the probe's verdict is written into the tape header so a later
analysis can see exactly which feeds were trusted and why.

EVERY ROW CARRIES BOTH CLOCKS. The venue's own source timestamp (with its
identified unit) AND our local receipt time, plus the sequence id where the
venue publishes one and the full bid/ask/size touch. NO HINDSIGHT
ALIGNMENT: nothing is shifted, interpolated or back-dated at collection.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

from src.gemini_research import leadlag_probe as PROBE

LABEL = "GEMINI_LEADLAG_LAYER1_COLLECTOR_V1"
DEFAULT_HZ = 1.0
#: a poll whose round trip exceeds this is recorded but flagged: a slow read
#: cannot support a sub-second lead/lag claim
SLOW_RTT_S = 0.75


class Sink:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.fh = open(path, "a", encoding="utf-8")
        self.n = 0

    def write(self, row: Dict[str, Any]) -> None:
        self.fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        self.n += 1
        if self.n % 200 == 0:
            self.fh.flush()
            os.fsync(self.fh.fileno())

    def close(self) -> None:
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.fh.close()


def poll(c: Dict[str, Any]) -> Dict[str, Any]:
    """One read of one venue, with both clocks and no alignment."""
    t0 = time.time()
    row: Dict[str, Any] = {"kind": "l1_quote", "venue": c["venue"],
                           "instrument": c["instrument"],
                           "local_send_epoch_s": t0}
    try:
        req = urllib.request.Request(c["url"], headers={
            "User-Agent": "gemini-leadlag-collector/1 (read-only research)"})
        with urllib.request.urlopen(req, timeout=10.0) as r:
            body = r.read()
            status = r.status
    except Exception as exc:                                   # noqa: BLE001
        row.update({"ok": False, "why": "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:160])})
        return row
    recv = time.time()
    row.update({"ok": status == 200, "status": status,
                "local_receipt_epoch_s": recv,
                "rtt_s": round(recv - t0, 4),
                "rtt_is_slow": (recv - t0) > SLOW_RTT_S})
    try:
        j = json.loads(body)
    except ValueError:
        row.update({"ok": False, "why": "body was not JSON"})
        return row
    raw_ts = None
    for f in (c.get("ts_fields") or ()):
        v = PROBE._dig(j, (f,)) if isinstance(f, str) else PROBE._dig(j, f)
        if v not in (None, ""):
            raw_ts = v
            row["source_timestamp_field"] = f
            break
    row["source_timestamp_raw"] = raw_ts
    row["clock"] = PROBE.classify_unit(raw_ts, recv)
    row.update({
        "bid": PROBE._dig(j, c["bid"]),
        "ask": PROBE._dig(j, c["ask"]),
        "bid_size": (PROBE._dig(j, c["bid_size"]) if c.get("bid_size")
                     else None),
        "ask_size": (PROBE._dig(j, c["ask_size"]) if c.get("ask_size")
                     else None),
        "sequence_id": (PROBE._dig(j, c["seq"]) if c.get("seq") else None),
        "raw": j,
    })
    if c.get("basis"):
        row["basis_warning"] = c["basis"]
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/gemini_leadlag")
    ap.add_argument("--duration-s", type=float, default=600.0)
    ap.add_argument("--hz", type=float, default=DEFAULT_HZ)
    ap.add_argument("--probe-out", default=None)
    a = ap.parse_args(argv)

    verdict = PROBE.run()
    if a.probe_out:
        os.makedirs(os.path.dirname(a.probe_out) or ".", exist_ok=True)
        json.dump(verdict, open(a.probe_out, "w", encoding="utf-8"),
                  indent=2, sort_keys=True, default=str)
    admitted = [c for c in PROBE.CANDIDATES
                if c["venue"] in verdict["admissible_venues"]]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sink = Sink(os.path.join(a.outdir, "leadlag_l1_%s.jsonl" % stamp))
    sink.write({"kind": "collector_start", "label": LABEL,
                "utc": verdict["utc"], "hz": a.hz,
                "duration_s": a.duration_s,
                "admissibility_verdict": verdict,
                "admitted_venues": [c["venue"] for c in admitted],
                "rejected_venues": verdict["rejected"],
                "layers": ("LAYER 1 ONLY. Layers 2 (published LRRR) and 3 "
                           "(Gemini BTC05M book) come from the proven "
                           "btc5m_recorder and are joined in analysis, never "
                           "merged here."),
                "no_hindsight_alignment": ("both clocks are recorded raw; "
                                           "nothing is shifted, "
                                           "interpolated or back-dated")})
    if not admitted:
        sink.write({"kind": "collector_stop", "why": (
            "NO ADMISSIBLE FEED. Every candidate failed a stated "
            "requirement; collecting an inadmissible feed would be worse "
            "than collecting nothing."), "rejected": verdict["rejected"]})
        sink.close()
        print(json.dumps({"label": LABEL, "admitted": [],
                          "rejected": verdict["rejected"],
                          "rows": sink.n}, sort_keys=True))
        return 1
    deadline = time.time() + a.duration_s
    period = 1.0 / max(a.hz, 0.01)
    n = 0
    while time.time() < deadline:
        cycle = time.time()
        for c in admitted:
            sink.write(poll(c))
            n += 1
        rest = period - (time.time() - cycle)
        if rest > 0:
            time.sleep(rest)
    sink.write({"kind": "collector_stop", "rows": sink.n, "polls": n,
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    sink.close()
    print(json.dumps({"label": LABEL,
                      "admitted": [c["venue"] for c in admitted],
                      "rejected": verdict["rejected"],
                      "rows": sink.n, "polls": n}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
