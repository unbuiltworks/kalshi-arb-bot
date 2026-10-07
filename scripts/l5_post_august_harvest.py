#!/usr/bin/env python3
"""Harvest the POST-AUGUST gap in L5's public activity feed. READ-ONLY.

WHY THIS EXISTS. The canonical corpus (`data/l5/raw/activity.jsonl` +
`activity_gap.jsonl.gz`) stops at 2026-08-31 -- `analysis/l5/CANON_REGIMES.md`
says so in its own coverage caveat. Two later harvests exist and are NOT
re-fetched or overwritten by this script:

  * `data/l5/sep2026/l5_activity_sep2026_raw.json.gz`  -> 2026-09-01..09-10
  * `data/l5/sep2026/l5_activity_sep2223_raw.json.gz`  -> 2026-09-22..09-23
  * `data/l5/raw/sep/l5_sep23_25_daily.json`           -> 2026-09-23..09-25

Measured day coverage therefore leaves exactly two holes, which are the only
thing this script fetches:

  * 2026-09-11 .. 2026-09-21   (11 days)
  * 2026-09-26 .. present      (through the run date)

IDENTITY. `data-api.polymarket.com` keys `/activity` on the PROXY WALLET, not
the profile handle. Passing the handle returns HTTP 400
`{"error":"required query param 'user' not provided"}` -- the venue reports a
non-address as ABSENT, which reads as "did not trade" and is a trap. Only the
address below is ever sent. Identity of the returned records comes from
PROVENANCE: the envelope records the exact `params.user` that produced it.

PAGINATION. `?user=&limit=&offset=` caps out around offset 5,500, which for
this account reaches back only ~9 hours (`data/l5/sep2026/README.md`). So the
harvest WINDOWS by `start`/`end` and pages inside each window, and asserts
that no window terminated while still returning full pages -- a silent
offset-cap truncation is the failure mode that would understate L5's volume
and must be loud, not inferred.

APPEND-ONLY. Output is one envelope per line into a dated JSONL under
`data/l5/raw/post_august/`. Nothing existing is read for writing, modified or
deleted. Re-running writes a NEW file; dedupe happens downstream in
`scripts/l5_post_august_report.py`, never by dropping a file.

NO PROFIT CLAIM. Every record here is another trader's public activity. None
of it is submitted, filled, settled or realized profit for our account, and
L5's REWARD / MAKER_REBATE / TAKER_REBATE rows are HIS incentive cashflows --
not evidence that any equivalent program applies to us.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ADDRESS = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (research; read-only)"}
TYPES = ("TRADE", "REDEEM", "REWARD", "MAKER_REBATE", "TAKER_REBATE")

# Holes measured against the three existing post-August harvests. Fixed
# literals, not derived at runtime, so the fetch span is auditable from source.
GAPS = [
    ("2026-09-11", 1789084800, 1790035200),   # 09-11 .. 09-21 inclusive
    ("2026-09-26", 1790380800, None),         # 09-26 .. --until
]
WINDOW_S = 3 * 3600   # 3-hour slices, the span the Sep harvest proved workable
PAGE = 500
OFFSET_CEILING = 5500  # observed venue cap; a window that reaches it is FLAGGED


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def get(url: str, tries: int = 6):
    """Return (status, payload, error). Never raises; the envelope records it."""
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as fh:
                return fh.status, json.loads(fh.read().decode()), ""
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()[:300]
            except Exception:
                pass
            # A 4xx is the venue's answer, not a transport hiccup: do not retry.
            if 400 <= exc.code < 500:
                return exc.code, None, "HTTP %d %s" % (exc.code, body)
            last = "HTTP %d %s" % (exc.code, body)
        except Exception as exc:  # transport / proxy / timeout
            last = "%s: %s" % (type(exc).__name__, exc)
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return 0, None, last


def harvest_window(kind: str, start: int, end: int, out, stats) -> None:
    offset = 0
    while True:
        url = ("%s/activity?user=%s&limit=%d&offset=%d&type=%s&start=%d&end=%d"
               % (BASE, ADDRESS, PAGE, offset, kind, start, end))
        status, payload, err = get(url)
        env = {
            "source": url,
            "params": {"user": ADDRESS, "limit": PAGE, "offset": offset,
                       "type": kind, "start": start, "end": end},
            "window_start_utc": iso(start),
            "window_end_utc": iso(end),
            "status": status,
            "error": err,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "n_rows": len(payload) if isinstance(payload, list) else None,
            "payload": payload,
            "tag": "post_august_gap",
        }
        out.write(json.dumps(env, separators=(",", ":")) + "\n")
        out.flush()
        stats["envelopes"] += 1
        if status != 200 or not isinstance(payload, list):
            stats["failed_envelopes"] += 1
            stats["errors"].append({"url": url, "status": status, "error": err})
            return
        stats["rows"] += len(payload)
        if len(payload) < PAGE:
            return
        offset += PAGE
        if offset > OFFSET_CEILING:
            # Full pages all the way to the venue's cap: this window is
            # TRUNCATED and its volume is a floor, not a count. Loud, not silent.
            stats["truncated_windows"].append(
                {"type": kind, "start": iso(start), "end": iso(end),
                 "offset_reached": offset})
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default=None,
                    help="UTC date exclusive upper bound, YYYY-MM-DD; "
                         "default = start of the current UTC day + 1")
    ap.add_argument("--outdir", default="data/l5/raw/post_august")
    args = ap.parse_args()

    if args.until:
        until = int(dt.datetime.strptime(args.until, "%Y-%m-%d")
                    .replace(tzinfo=dt.timezone.utc).timestamp())
    else:
        now = dt.datetime.now(dt.timezone.utc)
        until = int((now.replace(hour=0, minute=0, second=0, microsecond=0)
                     + dt.timedelta(days=1)).timestamp())

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = outdir / ("l5_activity_post_august_%s.jsonl" % stamp)

    stats = {"envelopes": 0, "failed_envelopes": 0, "rows": 0,
             "truncated_windows": [], "errors": [], "windows": 0}

    print("harvest -> %s" % path)
    with open(path, "w", encoding="utf-8") as out:
        for label, gstart, gend in GAPS:
            gend = until if gend is None else min(gend, until)
            if gend <= gstart:
                print("  gap %s: nothing to fetch (end <= start)" % label)
                continue
            print("  gap %s: %s -> %s" % (label, iso(gstart), iso(gend)))
            w = gstart
            while w < gend:
                we = min(w + WINDOW_S, gend)
                stats["windows"] += 1
                for kind in TYPES:
                    harvest_window(kind, w, we, out, stats)
                w = we
                if stats["windows"] % 8 == 0:
                    print("    .. through %s  rows=%d envelopes=%d"
                          % (iso(w), stats["rows"], stats["envelopes"]))

    summary = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "address_used": ADDRESS,
        "handle_never_sent": "l5zn1bwom8etsk",
        "file": str(path),
        "until_utc": iso(until),
        "gaps_requested": [{"label": a, "start_utc": iso(b),
                            "end_utc": iso(until if c is None else min(c, until))}
                           for a, b, c in GAPS],
        "window_seconds": WINDOW_S,
        "types": list(TYPES),
        **{k: v for k, v in stats.items()},
    }
    (outdir / ("HARVEST_SUMMARY_%s.json" % stamp)).write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items()
                      if k != "gaps_requested"}, indent=2)[:2000])

    if stats["rows"] == 0:
        print("::error::ZERO rows fetched. This is a FETCH FAILURE, not "
              "evidence that L5 stopped trading. Do not report it as silence.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
