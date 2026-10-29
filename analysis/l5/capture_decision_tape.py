#!/usr/bin/env python3
"""Append-only, deduped capture of the live decision tape.

WHY THIS EXISTS. Campaign 8's economics reconciled perfectly because cash and
fill records were pulled while it ran, but its L5_LIVE_DECISION tape rolled
off Railway retention before capture, so Campaign 8 has NO recoverable
refusal classification. Retention for that label was observed at roughly 1.6
hours, which is shorter than a 4-hour authorization window.

USAGE. Feed it the raw JSON a Railway get-logs call saved to disk:

    python3 -I analysis/l5/capture_decision_tape.py \
        analysis/l5/campaign9/tape_decisions.jsonl raw1.json raw2.json ...

It is idempotent: records already present are skipped, so overlapping polls
are safe and the poll interval only has to beat retention, not align to it.
Dedupe key is (utc, label, coid|slug|window) because the harness can emit the
same record twice and a timestamp alone collides across markets.
"""
import json
import os
import sys


def key(rec):
    return (str(rec.get("utc") or rec.get("t") or ""),
            str(rec.get("label") or rec.get("kind") or ""),
            str(rec.get("coid") or rec.get("slug") or rec.get("window") or ""))


def existing(path):
    seen = set()
    if not os.path.exists(path):
        return seen
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(key(json.loads(line)))
            except ValueError:
                continue
    return seen


def records(raw_path):
    """Yield the JSON payload of each log message in a get-logs dump."""
    with open(raw_path) as fh:
        blob = json.load(fh)
    for entry in blob.get("deploy") or ():
        msg = entry.get("message") or ""
        brace = msg.find("{")
        if brace < 0:
            continue
        label = msg[:brace].strip()
        try:
            rec = json.loads(msg[brace:])
        except ValueError:
            # a message truncated by the log harness is NOT silently dropped:
            # losing an audit line is the thing this script exists to prevent.
            sys.stderr.write("UNPARSEABLE %s %s\n"
                             % (entry.get("timestamp"), label))
            continue
        if not isinstance(rec, dict):
            continue
        rec.setdefault("label", label)
        rec.setdefault("_log_ts", entry.get("timestamp"))
        yield rec


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    out_path, raws = argv[1], argv[2:]
    seen = existing(out_path)
    before = len(seen)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    added = 0
    with open(out_path, "a") as fh:
        for raw in raws:
            for rec in records(raw):
                k = key(rec)
                if k in seen:
                    continue
                seen.add(k)
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                added += 1
    print("%s: %d already present, %d appended, %d total"
          % (out_path, before, added, before + added))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
