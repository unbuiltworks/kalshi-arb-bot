"""Stage F -- verify the package before anyone uploads it.

Checks, and fails loudly on any of them:

  1. every file the READMEs and prompts reference actually exists;
  2. the fill parts contain exactly 760,817 rows in total, with no
     duplicate (event_slug, trade_seq) and no window split across parts;
  3. each part is under the upload size cap;
  4. the window summary row count matches the distinct windows in the
     parts;
  5. per-fill inventory is internally consistent: matched_qty ==
     min(cum_up, cum_down) and effective_pair_cost ==
     matched_spend_fifo/matched_qty, on every row;
  6. the case-study count and index agree;
  7. no credential, private key, account identifier of OURS, or
     infrastructure detail (Railway/GitHub ids, hostnames, tokens) has
     leaked into any shipped file;
  8. SHA256SUMS is written and self-consistent.

Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import hashlib
import json
import os
import re
import sys

EXPECT_FILLS = 760817
EXPECT_WINDOWS = 17046
MAX_PART_BYTES = 12 * 1024 * 1024
TOL = 1e-4

# Patterns that must never appear in a shipped file. L5's own public
# address is EXPECTED (it is the subject, and it is public); ours is not.
FORBIDDEN = [
    (r"BEGIN [A-Z ]*PRIVATE KEY", "private key block"),
    (r"KALSHI-ACCESS-(KEY|SIGNATURE)", "Kalshi auth header"),
    (r"\bRAILWAY_(TOKEN|API_TOKEN|SSH_PRIVATE_KEY[A-Z0-9_]*)\b",
     "Railway secret name"),
    (r"\bghp_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "email address"),
    (r"\b(?:railway\.app|up\.railway\.app)\b", "Railway hostname"),
    # Any UUID. Our Railway project / environment / service ids are UUIDs;
    # nothing in this package legitimately is (market ids and transaction
    # hashes are plain hex, not UUID-shaped), so a generic match is both
    # safer than listing the ids and keeps them out of this file too.
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
     "UUID (possible infrastructure id)"),
]
TEXT_EXT = {".md", ".json", ".txt", ".py"}

# Phrases that would reveal the package authors' own prior analysis to a
# model this package exists to keep blind. Checked separately from the
# credential scan because the failure mode is different: not a security
# leak, a contamination leak.
CONTAMINATION = [
    (r"MIN_GAP_S|L5_BAND|L5_COMPRESS_TRIGGER|L5_EXTEND_BUDGET",
     "prior-analysis constant"),
    (r"CANONICAL_L5_MODEL|CANON_REGIMES|CANON_ECONOMICS",
     "prior-analysis document name"),
]
# Saying a prior analysis EXISTS is required by the blind-branch design
# (BRANCH_SCOPE.md and README_UPLOAD.md both must say so). Only its
# CONTENT may not leak, so there is deliberately no pattern for the mere
# mention. This file is itself exempt: it has to contain the patterns it
# searches for.
CONTAMINATION_EXEMPT = {"60_verify_package.py"}


def fail(msgs, m):
    msgs.append(m)
    print("  FAIL " + m)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    a = ap.parse_args()
    P = a.package
    bad = []

    print("1. required files")
    required = [
        "README_UPLOAD.md", "BRANCH_SCOPE.md", "DATA_DICTIONARY.md",
        "LIMITS_OF_PUBLIC_FILLS.md",
        "PROMPT_GROK.md", "PROMPT_PERPLEXITY.md", "CORPUS_PROFILE.json",
        "NORMALIZE_REPORT.json", "cashflow.csv.gz", "settlement.csv",
        "dataset/l5_window_summary.csv.gz",
        "dataset/l5_fills_enriched_SAMPLE.csv.gz",
        "dataset/INVENTORY_REPORT.json", "dataset/RESHARD_REPORT.json",
        "case_studies/CASE_STUDY_INDEX.md",
        "case_studies/CASE_STUDIES_ALL.md",
        "hypothesis_tests/HYPOTHESIS_TEMPLATE.json",
        "plain/README.md", "plain/l5_window_summary.csv",
        "plain/l5_fills_sample.csv",
        "hypothesis_tests/EXAMPLE_H001_pnl_by_duration.json",
        "hypothesis_tests/EXAMPLE_H002_fill_size_by_asset.json",
        "scripts/10_normalize_corpus.py",
        "scripts/20_reconstruct_inventory.py",
        "scripts/25_reshard.py", "scripts/30_case_studies.py",
        "scripts/40_corpus_profile.py", "scripts/50_test_hypothesis.py",
        "scripts/60_verify_package.py", "scripts/70_plain_text_views.py",
        "scripts/80_grok_predictions.py",
        "external_review/README.md",
        "external_review/GROK_ROUND1_RESULTS.md",
        "external_review/GROK_PREDICTIONS_RESULT.json",
        "external_review/GROK_ROUND2_RESULTS.md",
        "external_review/GROK_ROUND2_PREDICTIONS_RESULT.json",
        "scripts/81_grok_round2_predictions.py",
        "scripts/82_null_model_test.py",
        "external_review/NULL_MODEL_DECISIVE_TEST.md",
        "external_review/NULL_MODEL_TEST_RESULT.json",
        "scripts/83_price_acceptance.py",
        "external_review/PRICE_ACCEPTANCE_FINDINGS.md",
        "external_review/PRICE_ACCEPTANCE_RESULT.json",
    ]
    for r in required:
        if not os.path.exists(os.path.join(P, r)):
            fail(bad, f"missing required file: {r}")
    print(f"  checked {len(required)} required paths")

    print("2/3/4/5. fill parts, counts, sizes, inventory consistency")
    parts = sorted(glob.glob(os.path.join(
        P, "dataset", "fills", "l5_fills_enriched_part*.csv.gz")))
    if not parts:
        fail(bad, "no fill parts found")
    total = 0
    seen_pairs = set()
    window_of_part = {}
    windows = set()
    inv_bad = 0
    for p in parts:
        b = os.path.getsize(p)
        if b > MAX_PART_BYTES:
            fail(bad, f"{os.path.basename(p)} is {b} bytes, over the cap")
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                total += 1
                k = (r["event_slug"], r["trade_seq"])
                if k in seen_pairs:
                    fail(bad, f"duplicate (event_slug, trade_seq) {k}")
                seen_pairs.add(k)
                windows.add(r["event_slug"])
                prev = window_of_part.setdefault(r["event_slug"], p)
                if prev != p:
                    fail(bad, f"window {r['event_slug']} split across parts")
                try:
                    up = float(r["cum_up_qty"])
                    dn = float(r["cum_down_qty"])
                    mq = float(r["matched_qty"])
                    if abs(mq - min(up, dn)) > TOL:
                        inv_bad += 1
                    if r["effective_pair_cost"]:
                        pc = float(r["effective_pair_cost"])
                        ms = float(r["matched_spend_fifo"])
                        if mq > TOL and abs(pc - ms / mq) > TOL:
                            inv_bad += 1
                except (TypeError, ValueError):
                    inv_bad += 1
    if total != EXPECT_FILLS:
        fail(bad, f"fill rows = {total}, expected {EXPECT_FILLS}")
    if len(windows) != EXPECT_WINDOWS:
        fail(bad, f"windows = {len(windows)}, expected {EXPECT_WINDOWS}")
    if inv_bad:
        fail(bad, f"{inv_bad} rows fail the inventory identity checks")
    print(f"  {len(parts)} parts, {total} fills, {len(windows)} windows, "
          f"inventory identity violations: {inv_bad}")

    wsum = os.path.join(P, "dataset", "l5_window_summary.csv.gz")
    nw = 0
    wslugs = set()
    with gzip.open(wsum, "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            nw += 1
            wslugs.add(r["event_slug"])
    if nw != EXPECT_WINDOWS or wslugs != windows:
        fail(bad, f"window summary has {nw} rows / "
                  f"{len(wslugs ^ windows)} slug mismatches")
    print(f"  window summary rows {nw}, slug sets match: {wslugs == windows}")

    print("5b. plain views agree with their gzipped sources")
    import io
    pw = os.path.join(P, "plain", "l5_window_summary.csv")
    with open(pw, newline="", encoding="utf-8") as fh:
        pr = list(csv.DictReader(fh))
    if len(pr) != EXPECT_WINDOWS or {r["event_slug"] for r in pr} != windows:
        fail(bad, f"plain window summary has {len(pr)} rows / slug mismatch")
    ps = os.path.join(P, "plain", "l5_fills_sample.csv")
    with open(ps, newline="", encoding="utf-8") as fh:
        sr = list(csv.DictReader(fh))
    stray = {r["event_slug"] for r in sr} - windows
    if stray:
        fail(bad, f"plain fill sample has {len(stray)} unknown windows")
    sbad = sum(1 for r in sr
               if (r["event_slug"], r["trade_seq"]) not in seen_pairs)
    if sbad:
        fail(bad, f"{sbad} plain-sample rows are not in the full table")
    print(f"  plain window summary {len(pr)} rows, "
          f"plain sample {len(sr)} rows, all present in the full table")

    print("6. case studies")
    cs = sorted(glob.glob(os.path.join(P, "case_studies",
                                       "CASE_[0-9][0-9]_*.md")))
    idx = open(os.path.join(P, "case_studies", "CASE_STUDY_INDEX.md"),
               encoding="utf-8").read()
    n_idx = len([l for l in idx.splitlines()
                 if l.startswith("| ") and "`CASE" not in l
                 and re.match(r"\| \d+ \|", l)])
    if len(cs) != n_idx:
        fail(bad, f"{len(cs)} case files but {n_idx} index rows")
    if not 20 <= len(cs) <= 30:
        fail(bad, f"{len(cs)} case studies, brief asked for 20-30")
    print(f"  {len(cs)} case study files, {n_idx} index rows")

    print("7. no credentials / infrastructure / our own identifiers")
    scanned = 0
    for root, _, files in os.walk(P):
        for fn in files:
            fp = os.path.join(root, fn)
            if os.path.splitext(fn)[1] not in TEXT_EXT:
                continue
            scanned += 1
            txt = open(fp, encoding="utf-8", errors="ignore").read()
            for pat, label in FORBIDDEN:
                m = re.search(pat, txt)
                if m:
                    fail(bad, f"{label} in {os.path.relpath(fp, P)}: "
                              f"{m.group(0)[:40]!r}")
    print(f"  scanned {scanned} text files")

    print("7b. no contamination from our own prior analysis")
    hits = 0
    for root, _, files in os.walk(P):
        for fn in files:
            if os.path.splitext(fn)[1] not in TEXT_EXT:
                continue
            if fn in CONTAMINATION_EXEMPT:
                continue
            fp = os.path.join(root, fn)
            txt = open(fp, encoding="utf-8", errors="ignore").read()
            for pat, label in CONTAMINATION:
                m = re.search(pat, txt)
                if m:
                    hits += 1
                    fail(bad, f"{label} in {os.path.relpath(fp, P)}: "
                              f"{m.group(0)[:50]!r}")
    print(f"  contamination hits: {hits}")

    print("8. checksums")
    lines = []
    for root, _, files in sorted(os.walk(P)):
        for fn in sorted(files):
            if fn == "SHA256SUMS":
                continue
            fp = os.path.join(root, fn)
            h = hashlib.sha256()
            with open(fp, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {os.path.relpath(fp, P)}")
    with open(os.path.join(P, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  wrote SHA256SUMS over {len(lines)} files")

    total_bytes = sum(os.path.getsize(os.path.join(r, f))
                      for r, _, fs in os.walk(P) for f in fs)
    print(f"\npackage: {len(lines) + 1} files, "
          f"{total_bytes / 1e6:.1f} MB total")
    if bad:
        print(f"\nVERIFICATION FAILED: {len(bad)} problem(s)")
        return 1
    print("\nVERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
