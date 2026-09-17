"""Collection -> analysis handoff, with no manual pause. OWNER 2026-09-17 (5).

    close -> gzip -> hash -> preserve raw provenance -> analyze -> verdict

Run it against a collector's output directory; it blocks until that collector
stops writing, then does the rest itself. The point is to remove idle time
between a tape closing and its analysis existing -- not to remove any check.

PROVENANCE IS PRESERVED, NOT REPLACED. The raw tape stays exactly where the
collector wrote it. The .gz is an additional artifact, and the manifest records
the sha256 of BOTH, plus row counts and the byte size, so a later reader can
prove the analysed file is the file that was collected. Nothing is deleted.

THE ANALYSIS IS ALREADY FROZEN. This module chooses no metric and no threshold;
it calls gemini_flow / the candidate sweep, both written before the tapes
closed. A handoff that could redefine the question would defeat freezing it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

QUIET_SECONDS = 90.0          # no growth for this long == the collector is done
POLL_SECONDS = 15.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path: Path) -> int:
    op = gzip.open if path.suffix == ".gz" else open
    n = 0
    with op(path, "rt", encoding="utf-8", errors="replace") as fh:  # type: ignore
        for _ in fh:
            n += 1
    return n


def wait_until_quiet(d: Path, pattern: str, timeout_s: float = 7200.0,
                     quiet_s: float = QUIET_SECONDS) -> list:
    """Block until every matching file has stopped growing.

    Size-based rather than process-based on purpose: it works whether the
    collector exited cleanly, crashed, or is still finishing a final flush."""
    deadline = time.time() + timeout_s
    last: dict = {}
    stable_since: Optional[float] = None
    while time.time() < deadline:
        now = {p: p.stat().st_size for p in sorted(d.glob(pattern))}
        if now and now == last:
            stable_since = stable_since or time.time()
            if time.time() - stable_since >= quiet_s:
                return sorted(now)
        else:
            stable_since = None
        last = now
        time.sleep(POLL_SECONDS)
    return sorted(last)


def preserve(paths: list, out_dir: Path, label: str) -> dict:
    """gzip + hash + manifest. The raw file is never touched."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for p in paths:
        gz = p.with_suffix(p.suffix + ".gz")
        if not gz.exists():
            with p.open("rb") as src, gzip.open(gz, "wb") as dst:
                shutil.copyfileobj(src, dst)
        entries.append({
            "raw": str(p), "raw_sha256": sha256(p),
            "raw_bytes": p.stat().st_size, "raw_rows": rows(p),
            "gz": str(gz), "gz_sha256": sha256(gz),
            "gz_bytes": gz.stat().st_size,
        })
    manifest = {
        "label": label,
        "closed_utc": datetime.now(timezone.utc).isoformat(),
        "files": entries,
        "labels": ("raw tape preserved in place; .gz is an additional "
                   "artifact; both hashed so the analysed file is provably "
                   "the collected file"),
    }
    mp = out_dir / ("manifest_%s.json" % label)
    mp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def analyze_gemini(paths: list, out_dir: Path) -> dict:
    from analysis.l5.sweep import gemini_flow as gf
    hot = gf.flow(gf.read_tape(paths))
    base = gf.flow(gf.read_tape(sorted(
        Path("data/policy_d_port_ext").glob("tape_*.jsonl"))))
    result = {"hot": hot, "overnight_baseline": base,
              "comparison": gf.compare(hot, base), "verdict": gf.verdict(hot)}
    (out_dir / "gemini_flow_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--pattern", default="tape_*.jsonl")
    ap.add_argument("--label", required=True)
    ap.add_argument("--analysis", choices=("gemini", "none"), default="none")
    ap.add_argument("--timeout-s", type=float, default=7200.0)
    a = ap.parse_args()

    d = Path(a.dir)
    print("ON_CLOSE waiting for %s/%s to go quiet" % (d, a.pattern), flush=True)
    paths = wait_until_quiet(d, a.pattern, a.timeout_s)
    if not paths:
        print("ON_CLOSE no files matched; nothing to preserve", flush=True)
        return 1
    man = preserve(paths, d, a.label)
    print("ON_CLOSE_MANIFEST " + json.dumps(
        {"label": a.label, "files": len(man["files"]),
         "rows": sum(e["raw_rows"] for e in man["files"])}), flush=True)
    if a.analysis == "gemini":
        res = analyze_gemini(paths, d)
        print("ON_CLOSE_VERDICT " + json.dumps(res["verdict"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
