"""Scrape PMUS_TAPE_ROW rows out of saved Railway log dumps into a git tape.

The collector echoes every row to stdout because the container filesystem is
unreachable from here. Railway log retention then destroys them, so rows are
pulled into git, which is durable and outside Railway entirely.

Idempotent: rows are keyed by (slug, kind, t) and appended only if unseen, so
overlapping log pulls are safe and ordering does not matter.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import sys
from typing import Iterable

TAPE = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
MARK = "PMUS_TAPE_ROW "


def rows_from(paths: Iterable[str]):
    for p in paths:
        try:
            blob = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        for e in blob.get("deploy") or []:
            m = e.get("message") or ""
            i = m.find(MARK)
            if i < 0:
                continue
            try:
                yield json.loads(m[i + len(MARK):])
            except Exception:
                continue


def key(r):
    return "%s|%s|%s" % (r.get("slug"), r.get("kind"), r.get("t"))


def main(argv):
    paths = []
    for a in argv[1:]:
        paths.extend(sorted(glob.glob(a)))
    if not paths:
        print("no log dumps given")
        return 2
    seen = set()
    if os.path.exists(TAPE):
        for line in io.open(TAPE, encoding="utf-8"):
            try:
                seen.add(key(json.loads(line)))
            except Exception:
                continue
    before = len(seen)
    added = 0
    os.makedirs(os.path.dirname(TAPE), exist_ok=True)
    with io.open(TAPE, "a", encoding="utf-8") as fh:
        for r in rows_from(paths):
            k = key(r)
            if k in seen:
                continue
            seen.add(k)
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            added += 1
    h = hashlib.sha256(io.open(TAPE, "rb").read()).hexdigest()
    slugs = {}
    for line in io.open(TAPE, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        slugs.setdefault(d.get("slug"), 0)
        slugs[d.get("slug")] += 1
    print(json.dumps({
        "had": before, "added": added, "total": before + added,
        "sha256": h, "slugs": len(slugs),
        "rows_by_slug": dict(sorted(((k or "?"), v) for k, v in slugs.items())[-6:]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
