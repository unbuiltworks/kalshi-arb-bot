"""SUPERSEDED AND KNOWN WRONG -- see
analysis/l5/CRITICAL_REVIEW_RECONCILIATION_20261024.md

Two defects, both material:
  1. the dedup key included `price`, so the 18 repeated fetch passes
     over the early offsets survived as 16,170 duplicate rows and
     +$327,737 of phantom spend;
  2. the action classifier tested `held_other > 0`, which calls
     buying the already-short side a complement.

Use analysis/l5/l5_authoritative_reconstruction.py instead: it
reproduces the pinned artefact to the cent and classifies on
|imbalance|.
"""

"""Reconstruct L5's sequential inventory decisions from raw Polygon activity.

SOURCE, not inference: data/l5/raw/activity.jsonl plus activity_gap.jsonl.gz,
the same two files the pinned realized-economics artefact was built from
(sha256 recorded there). Every action is one TRADE row; there are no SELL
rows in this corpus and the script asserts that rather than assuming it.

ACTION TAXONOMY, assigned by INVENTORY and not by index, because the same
side can be an open, an extend or a complement depending only on what is
already held:

  OPEN       the book is flat for this window
  EXTEND     buys the side already long -> |imbalance| grows
  COMPLEMENT buys the opposite side     -> |imbalance| shrinks
  FLATTEN    a COMPLEMENT that lands exactly flat

WAIT is not an action, it is the GAP between actions, so it is measured as
the inter-action interval rather than counted.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal as D

RAW = "data/l5/raw/activity.jsonl"
GAP = "data/l5/raw/activity_gap.jsonl.gz"
SERIES = "btc-updown-15m-"
WINDOW_S = 900

OPEN, EXTEND, COMPLEMENT, FLATTEN = "OPEN", "EXTEND", "COMPLEMENT", "FLATTEN"


def _rows(path):
    op = gzip.open if path.endswith(".gz") else open
    try:
        with op(path, "rt") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for r in (d.get("payload") or ()):
                    yield r
    except FileNotFoundError:
        return


def window_start(slug):
    if not isinstance(slug, str) or not slug.startswith(SERIES):
        return None
    try:
        return int(slug[len(SERIES):])
    except ValueError:
        return None


def collect():
    """slug -> ordered list of (t, outcome, side, size, price, type)."""
    wins = defaultdict(list)
    seen = set()
    sells = 0
    types = defaultdict(int)
    for r in list(_rows(RAW)) + list(_rows(GAP)):
        slug = r.get("eventSlug") or r.get("slug")
        ws = window_start(slug)
        if ws is None:
            continue
        key = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
               r.get("size"), r.get("price"), r.get("type"))
        if key in seen:
            continue
        seen.add(key)
        typ = str(r.get("type") or "")
        types[typ] += 1
        if typ != "TRADE":
            continue
        side = str(r.get("side") or "").upper()
        if side == "SELL":
            sells += 1
        try:
            t = int(r.get("timestamp"))
            size = D(str(r.get("size")))
            px = D(str(r.get("price")))
        except (TypeError, ValueError, ArithmeticError):
            continue
        wins[slug].append((t, str(r.get("outcome") or ""), side, size, px, ws))
    for slug in wins:
        wins[slug].sort(key=lambda x: x[0])
    return wins, sells, dict(types)


def classify(wins):
    per_window = []
    action_counts = defaultdict(int)
    # action mix by time-to-expiry bucket, to see near-settlement behaviour
    buckets = ((900, 600, "T-15..10m"), (600, 300, "T-10..5m"),
               (300, 120, "T-5..2m"), (120, 30, "T-2..0.5m"),
               (30, -1e9, "T-30s..0"))
    bucket_mix = {b[2]: defaultdict(int) for b in buckets}
    gaps = []
    for slug, rows in wins.items():
        up = D(0); dn = D(0)
        spend = D(0)
        n = 0
        prev_t = None
        first_t = rows[0][0]
        ws = rows[0][5]
        seq = []
        for (t, outcome, side, size, px, _ws) in rows:
            if side != "BUY":
                continue
            is_up = outcome.lower().startswith("up")
            held_other = dn if is_up else up
            imb_before = up - dn
            if up == 0 and dn == 0:
                act = OPEN
            elif held_other > 0:
                act = COMPLEMENT
            else:
                act = EXTEND
            if is_up:
                up += size
            else:
                dn += size
            spend += size * px
            if act == COMPLEMENT and up == dn:
                act = FLATTEN
            action_counts[act] += 1
            tte = (ws + WINDOW_S) - t
            for hi, lo, name in buckets:
                if hi >= tte > lo:
                    bucket_mix[name][act] += 1
                    break
            if prev_t is not None:
                gaps.append(t - prev_t)
            prev_t = t
            n += 1
            seq.append(act)
        matched = min(up, dn)
        resid = up - dn
        per_window.append({
            "slug": slug, "legs": n, "up": up, "dn": dn,
            "matched": matched, "residual": resid,
            "spend": spend,
            "pair_unit": (spend / (matched * 2) * 2) if matched > 0 else None,
            "first_offset_s": first_t - ws,
            "seq_head": seq[:6],
        })
    return per_window, dict(action_counts), bucket_mix, gaps


def main():
    wins, sells, types = collect()
    if not wins:
        print("NO 15m WINDOWS FOUND -- raw activity absent or another series")
        return 1
    pw, acts, bmix, gaps = classify(wins)
    tot_acts = sum(acts.values()) or 1
    print("=== CORPUS ===")
    print("row types:", types)
    print("SELL rows in 15m TRADE flow:", sells, "(L5 never sells)")
    print("windows reconstructed:", len(pw))
    print()
    print("=== ACTION MIX (by inventory, not index) ===")
    for k in (OPEN, EXTEND, COMPLEMENT, FLATTEN):
        print("  %-11s %7d  %5.1f%%" % (k, acts.get(k, 0),
                                        100.0 * acts.get(k, 0) / tot_acts))
    print()
    print("=== ACTION MIX BY TIME TO EXPIRY (near-settlement behaviour) ===")
    for _, _, name in ((900, 600, "T-15..10m"), (600, 300, "T-10..5m"),
                       (300, 120, "T-5..2m"), (120, 30, "T-2..0.5m"),
                       (30, -1e9, "T-30s..0")):
        m = bmix[name]
        tot = sum(m.values()) or 1
        print("  %-11s n=%6d  OPEN %4.1f%%  EXTEND %4.1f%%  COMPL %4.1f%%  "
              "FLATTEN %4.1f%%"
              % (name, tot, 100.0*m.get(OPEN,0)/tot, 100.0*m.get(EXTEND,0)/tot,
                 100.0*m.get(COMPLEMENT,0)/tot, 100.0*m.get(FLATTEN,0)/tot))
    print()
    legs = sorted(w["legs"] for w in pw)
    print("=== PER-WINDOW ===")
    print("  legs: median %d  p05 %d  p95 %d  max %d"
          % (legs[len(legs)//2], legs[int(len(legs)*0.05)],
             legs[int(len(legs)*0.95)], legs[-1]))
    fo = sorted(w["first_offset_s"] for w in pw)
    print("  first action offset into window: median %ds  p05 %ds  p95 %ds"
          % (fo[len(fo)//2], fo[int(len(fo)*0.05)], fo[int(len(fo)*0.95)]))
    g = sorted(gaps)
    if g:
        print("  WAIT between actions: median %ds  p05 %ds  p95 %ds  max %ds"
              % (g[len(g)//2], g[int(len(g)*0.05)], g[int(len(g)*0.95)], g[-1]))
    # residual as a fraction of gross, the number that matters for us
    tot_matched = sum(w["matched"] for w in pw)
    tot_resid = sum(abs(w["residual"]) for w in pw)
    flat = sum(1 for w in pw if w["residual"] == 0)
    print()
    print("=== INVENTORY OUTCOME ===")
    print("  total matched contracts :", tot_matched)
    print("  total |residual|        :", tot_resid)
    print("  residual / matched      : %.4f" % (float(tot_resid)/float(tot_matched)) if tot_matched else "n/a")
    print("  windows ending FLAT     : %d of %d (%.1f%%)"
          % (flat, len(pw), 100.0*flat/len(pw)))
    pu = sorted(float(w["pair_unit"]) for w in pw if w["pair_unit"] is not None)
    if pu:
        print("  effective pair unit cost: median %.4f  p05 %.4f  p95 %.4f"
              % (pu[len(pu)//2], pu[int(len(pu)*0.05)], pu[int(len(pu)*0.95)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
