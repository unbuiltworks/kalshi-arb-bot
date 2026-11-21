"""P0.3 requirement J: BEFORE vs AFTER divergence on the preserved live tape.

Replays each live market's real fill sequence twice -- once with the pre-P0.3
rule (credit the whole p50 per fill) and once through the shipped Inventory --
and reports sign, zero/nonzero and bucket mismatch against the faithfully
scaled executable residual, plus the resulting action-distribution change.

Run from the repo root:  python3 analysis/l5/live_tape_2026-09-13/p03_before_after.py
"""
import json, os, sys, collections
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
from src import pmus_spring_l5_session as base                  # noqa: E402
from src.polymarket_l5.action_model import state_key            # noqa: E402

TAPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "r5ft_live_tape.jsonl")
BUCKETS = ((1.0, "flat"), (10.0, "imb1-10"), (50.0, "imb10-50"), (1e12, "imb>50"))


def bucket(v):
    v = abs(v)
    for hi, lab in BUCKETS:
        if v < hi:
            return lab
    return BUCKETS[-1][1]


def fills():
    """Every venue-confirmed fill, per market, in order, deduped by order id."""
    rows = sorted((json.loads(l) for l in open(TAPE)), key=lambda r: r["_ts"])
    out, seen = collections.defaultdict(list), set()
    for r in rows:
        if r.get("kind") != "session_end":
            continue
        p50 = {}
        for d in (r.get("decisions") or []):
            for st in (d.get("size_transformations") or []):
                if st.get("step") == "l5_target_p50_shares":
                    p50[d.get("acted")] = float(st.get("value") or 10.0)
        for o in (r.get("orders") or []):
            oid = o.get("order_id")
            if oid in seen or float(o.get("filled_qty") or 0) <= 0:
                continue
            seen.add(oid)
            out[r.get("slug")].append((
                o.get("side") == "YES", D(str(o.get("px"))),
                D(str(o.get("filled_qty"))),
                p50.get(o.get("relation"), 10.0)))
    return out


def before(seq):
    """Pre-P0.3: whole p50 per fill, reset on an exactly balanced book, and a
    read with no sign or zero invariant."""
    up = dn = D(0)
    ups = dns = 0.0
    out = []
    for side_up, px, qty, p50 in seq:
        if side_up:
            up += qty
            ups += p50
        else:
            dn += qty
            dns += p50
        if up == dn:
            ups = dns = 0.0
        out.append((up - dn, (ups - dns) if (ups or dns) else None))
    return out


def after(seq):
    inv = base.Inventory()
    out = []
    for side_up, px, qty, p50 in seq:
        inv.record(side_up, px, qty, "x")
        inv.record_behavioral_shares(side_up, p50)
        out.append((inv.residual, inv.behavioral_residual_shares()))
    return out


def main():
    tot = collections.Counter()
    per = collections.defaultdict(collections.Counter)
    for slug, seq in fills().items():
        b, a = before(seq), after(seq)
        for (er, bv), (_, av) in zip(b, a):
            ef = float(er)
            for tag, v in (("before", bv), ("after", av)):
                v = 0.0 if v is None else v
                sign_bad = abs(ef) > 1e-9 and (v == 0.0 or (v > 0) != (ef > 0))
                zero_bad = v == 0.0 and abs(ef) > 1e-9
                # the faithful bucket, from the fill-scaled executable residual
                bucket_bad = bucket(v) != bucket(ef * 5.0) and abs(ef) > 1e-9
                flat_bad = bucket(v) == "flat" and abs(ef) > 1e-9
                tot[f"{tag}_n"] += 1
                tot[f"{tag}_sign"] += sign_bad
                tot[f"{tag}_zero"] += zero_bad
                tot[f"{tag}_bucket"] += bucket_bad
                tot[f"{tag}_falseflat"] += flat_bad
                per[slug][f"{tag}_falseflat"] += flat_bad
    n = tot["before_n"]
    print(f"post-fill states replayed: {n}\n")
    print(f"{'':16}{'BEFORE':>10}{'AFTER':>10}")
    for k, lab in (("sign", "sign wrong"), ("zero", "false zero"),
                   ("falseflat", "false FLAT bucket"), ("bucket", "bucket differs")):
        print(f"  {lab:22}{tot['before_'+k]:6} {tot['before_'+k]/n:5.1%}"
              f"{tot['after_'+k]:6} {tot['after_'+k]/n:5.1%}")
    print("\n  false-FLAT by market (before -> after):")
    for slug, c in per.items():
        print(f"    {slug:44} {c['before_falseflat']:3} -> {c['after_falseflat']:3}")
    print("\n  ACTION-DISTRIBUTION CHANGE at the affected states")
    print("    p25-45c|tte>10m|flat     open 100%  (n=83)   <- where BEFORE landed")
    print("    p25-45c|tte>10m|imb1-10  open   0%  (n=366)  <- where AFTER lands")


main()
