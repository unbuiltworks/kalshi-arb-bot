#!/usr/bin/env python3
"""Shadow S1 cohort accounting, gated on MEASUREMENT VALIDITY first.

PROVENANCE AND INSTRUMENTATION ONLY. This reads tapes and writes
COHORT_PROGRESS.json. It imports nothing from the S1 collector, cannot
influence placement, quantity, the fill classifier, the residual cap, the
TTE rule or replenishment, and does not touch the candidate fingerprint.

WHY IT EXISTS. The inline aggregator it replaces globbed every tape in the
directory, so the 27 INCOMPLETE / 114 NO_ELIGIBLE_MARKET rows produced by
two defective discovery implementations were being counted into the live
cohort, and a stale COHORT_PROGRESS could be read as a current result.
Every number here is therefore stamped with the run that produced it and
attributed per tape.

THE GATE ORDER IS THE POINT. Strategy statistics are reported only after
measurement validity is established, in this order:

  1. market/window identities and timestamps
  2. discovery rejection counters
  3. successful /book reads on BOTH tokens
  4. valid book observations
  5. proof hypothetical UP/DOWN orders were instantiated
  6. only then evidenced/uncertain fills and inventory

A market failing any of 1-5 is an INFRASTRUCTURE result and does not
count toward the cohort, whatever its fill count. A market that obtained
no valid book is never recorded as a fill-free market.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import gzip
import json
import os
import re
import sys

SHADOW_DIR = "data/l5/shadow"
MANIFEST = "analysis/l5/shadow/RUN_MANIFEST.json"
OUT = "analysis/l5/shadow/COHORT_PROGRESS.json"
COHORT_TARGET = 25
TAPE_RE = re.compile(r"shadow_(\d{8}T\d{6}Z)\.jsonl(?:\.gz)?$")


def parse_stamp(name):
    m = TAPE_RE.search(name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=dt.timezone.utc)


def parse_iso(v):
    if not v:
        return None
    return dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)


def load_manifest():
    if not os.path.exists(MANIFEST):
        return []
    return json.load(open(MANIFEST, encoding="utf-8")).get("runs", [])


def attribute(stamp, runs):
    """The run whose window contains this tape's start stamp."""
    best = None
    for r in runs:
        st = parse_iso(r.get("started_utc"))
        if st is None or stamp < st:
            continue
        en = parse_iso(r.get("ended_utc"))
        if en is not None and stamp > en:
            continue
        if best is None or st > parse_iso(best["started_utc"]):
            best = r
    return best


def read_tape(path):
    op = gzip.open if path.endswith(".gz") else open
    rows = []
    try:
        with op(path, "rt", errors="ignore") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    except Exception as exc:                                   # noqa: BLE001
        return None, str(exc)
    return rows, None


def summarise(rows):
    """Per-market validity, in the mandated gate order."""
    ident = {}
    snaps = collections.Counter()
    both_tok = collections.Counter()
    both_bids = collections.Counter()
    placed = collections.defaultdict(set)
    book_err = collections.Counter()
    disc_rejects = []
    terminals = []
    for r in rows:
        k = r.get("kind")
        mk = r.get("market")
        if k == "shadow_snapshot":
            snaps[mk] += 1
            # ROW SHAPE, read off the collector rather than assumed: the
            # snapshot carries dynamic per-outcome keys "<outcome>_bid" /
            # "<outcome>_ask", NOT a "sides" map and NOT tok0/tok1. The
            # collector emits a snapshot ONLY when BOTH tokens' books were
            # read (a failure sinks book_error and continues), so a
            # snapshot row is itself the proof of a two-sided read.
            bidk = [key for key in r if key.endswith("_bid")]
            if len(bidk) >= 2:
                both_tok[mk] += 1
                if all(r.get(key) is not None for key in bidk):
                    both_bids[mk] += 1
            if r.get("t") is not None:
                d = ident.setdefault(mk, {})
                d["first_t"] = min(d.get("first_t", r["t"]), r["t"])
                d["last_t"] = max(d.get("last_t", r["t"]), r["t"])
                d["outcomes"] = sorted(
                    key[:-4] for key in bidk)
        elif k == "shadow_order_placed":
            # The outcome lives inside the nested "order" payload
            # (Order.row), not at the top level.
            o = r.get("order")
            nm = (o or {}).get("outcome") if isinstance(o, dict) else None
            placed[mk].add(str(nm if nm is not None else "?"))
        elif k == "book_error":
            book_err[mk] += 1
        elif k == "discovery_error":
            disc_rejects.append({"why": r.get("why"),
                                 "discovered": r.get("discovered"),
                                 "t": r.get("t")})
        elif k == "market_terminal":
            terminals.append(r)
    out = []
    for t in terminals:
        mk = t.get("market")
        polls = t.get("polls") or 0
        reads = t.get("successful_reads") or 0
        obs_both = both_tok.get(mk, 0)
        sides = sorted(placed.get(mk, ()))
        # GATES, in order. The first failure decides, so a later-stage
        # zero can never be reported as the finding.
        if mk is None:
            gate = "GATE1_NO_MARKET_IDENTITY"
        elif polls == 0:
            gate = "GATE3_NO_POLLS"
        elif reads == 0:
            gate = "GATE3_NO_SUCCESSFUL_BOOK_READ"
        elif obs_both == 0:
            gate = "GATE4_NO_TWO_SIDED_OBSERVATION"
        elif "?" in sides:
            gate = "GATE5_ORDER_OUTCOME_UNREADABLE"
        elif len(sides) < 2:
            gate = "GATE5_ORDERS_NOT_INSTANTIATED_BOTH_SIDES"
        else:
            gate = "VALID_MEASUREMENT"
        out.append({
            "market": mk,
            "reported_status": t.get("status"),
            "window": ident.get(mk, {}),
            "polls": polls,
            "successful_book_reads": reads,
            "snapshots": snaps.get(mk, 0),
            "observations_with_both_token_books": obs_both,
            "observations_with_a_bid_on_both_outcomes": both_bids.get(mk, 0),
            "book_errors": book_err.get(mk, 0),
            "hypothetical_orders_instantiated_on": sides,
            "measurement_gate": gate,
            # Strategy statistics are carried but are MEANINGLESS unless
            # the gate reads VALID_MEASUREMENT.
            "evidenced_fills": t.get("evidenced_fills"),
            "uncertain_fills": t.get("uncertain_fills"),
            "inventory": t.get("inventory"),
            "matched": t.get("matched"),
            "residual": t.get("residual"),
            "cost": t.get("cost"),
        })
    return out, disc_rejects


def main() -> int:
    runs = load_manifest()
    tapes = sorted(glob.glob(os.path.join(SHADOW_DIR, "shadow_*.jsonl")) +
                   glob.glob(os.path.join(SHADOW_DIR, "shadow_*.jsonl.gz")))
    per_tape, counted, quarantined = [], [], []
    for path in tapes:
        stamp = parse_stamp(path)
        if stamp is None:
            continue
        run = attribute(stamp, runs)
        if run is None:
            # A tape no manifest run claims is of UNKNOWN provenance, so it
            # is quarantined rather than counted. Local preflight tapes
            # land here, and so would a tape from a run nobody recorded.
            run = {"validity": "INVALID_UNATTRIBUTED_TAPE",
                   "why": "no manifest run's window contains this tape's "
                          "start stamp"}
        rows, err = read_tape(path)
        if rows is None:
            # Unreadable is UNREADABLE. Never a zero.
            per_tape.append({"tape": os.path.basename(path),
                             "read_error": err,
                             "status": "TAPE_UNREADABLE"})
            continue
        mkts, disc = summarise(rows)
        entry = {
            "tape": os.path.basename(path),
            "tape_stamp_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": run.get("run_id"),
            "run_number": run.get("run_number"),
            "sha": run.get("sha"),
            "run_validity": run.get("validity"),
            "markets": mkts,
            "discovery_errors": disc,
            "gate_counts": dict(collections.Counter(
                m["measurement_gate"] for m in mkts)),
        }
        per_tape.append(entry)
        if str(run.get("validity") or "").startswith("INVALID"):
            # Keep the COUNTS and the reason, drop the per-market detail:
            # these tapes contain no book read, so their rows carry no
            # measurement, and 3,000 lines of them would bury the live
            # cohort. The tapes themselves stay on disk, unaltered.
            entry["markets_excluded"] = len(entry.pop("markets"))
            entry["detail_withheld"] = (
                "per-market rows omitted: this run is marked invalid, so "
                "its rows are not measurements. The raw tape is retained "
                "and unmodified.")
            quarantined.append(entry)
        else:
            counted.append(entry)

    valid = [m for e in counted for m in e["markets"]
             if m["measurement_gate"] == "VALID_MEASUREMENT"]
    complete = [m for m in valid if m["reported_status"] == "COMPLETE"]
    infra = [m for e in counted for m in e["markets"]
             if m["measurement_gate"] != "VALID_MEASUREMENT"]
    out = {
        "produced_at_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "produced_by_run_id": os.environ.get("GITHUB_RUN_ID"),
        "produced_by_sha": os.environ.get("GITHUB_SHA"),
        "produced_by_run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "tapes_read": [e["tape"] for e in per_tape],
        "candidate_fingerprint": (
            "441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b"
            "6fe"),
        "cohort_target": COHORT_TARGET,
        "COMPLETE_markets": len(complete),
        "valid_measurement_markets": len(valid),
        "infrastructure_failures_not_counted": len(infra),
        "quarantined_tapes": [
            {"tape": e["tape"], "run_id": e["run_id"],
             "validity": e["run_validity"],
             "markets_excluded": e.get("markets_excluded", 0)}
            for e in quarantined],
        "evidenced_fills_valid_markets_only": sum(
            m["evidenced_fills"] or 0 for m in valid),
        "uncertain_fills_valid_markets_only": sum(
            m["uncertain_fills"] or 0 for m in valid),
        "gate_counts_counted_tapes": dict(collections.Counter(
            m["measurement_gate"] for e in counted for m in e["markets"])),
        "per_tape": per_tape,
        "note": ("Measurement validity is established BEFORE strategy "
                 "statistics. A market failing gates 1-5 is an "
                 "infrastructure result and does not count toward the "
                 "cohort whatever its fill count; missing or unreadable "
                 "data is never a zero fill. Only COMPLETE markets that "
                 "also pass every gate count. Tapes from runs the "
                 "manifest marks INVALID are quarantined, not summed, so "
                 "a defective run cannot masquerade as a current result. "
                 "Uncertain fills are excluded from primary economics."),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    brief = {k: out[k] for k in (
        "produced_by_run_id", "COMPLETE_markets",
        "valid_measurement_markets", "infrastructure_failures_not_counted",
        "gate_counts_counted_tapes", "evidenced_fills_valid_markets_only",
        "uncertain_fills_valid_markets_only")}
    print(json.dumps(brief, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
