"""Frozen prospective funnel, full research universe vs deployable universe.

FIRST FILL -> COMPLETE RECOMMENDED -> COMPATIBLE OPPOSING FLOW -> QUEUE CLEAR
-> MATCHED INVENTORY

Model A is FROZEN. Nothing here is a model input, a selector or a threshold.
The deployable universe is a REPORTING filter applied after the fact; the full
research universe is preserved unchanged alongside it.
SHADOW/MODELED: no order was submitted, nothing is filled, settled or realized.
"""
import sys, json, collections, statistics as stt, datetime, time
sys.path.insert(0, '.')
import l5_seq_replay as R
import horizon as H

iso = lambda t: datetime.datetime.utcfromtimestamp(float(t)).strftime("%H:%M:%SZ")


def med(xs):
    return stt.median(xs) if xs else None


def block(eps, label):
    n = len(eps)
    rec = [e for e in eps if e["complete_eligible"]]
    flw = [e for e in eps if e.get("t_compat_flow")]
    qcl = [e for e in eps if e.get("t_qa_opposite_fill")]
    opp = [e for e in eps if e["t_first_opposite"]]
    mat = [e for e in eps if e["matched_qty"] > 0]
    lat = sorted(float(e["t_first_opposite"]) - float(e["t_first"]) for e in opp)
    bs = sorted(float(e["pair_basis"]) for e in mat if e["pair_basis"])
    ext = sorted(e["extend_fills_before_opposite"] for e in eps)
    unres = [e for e in eps if not e["closed"] and e["residual_qty"] > 0]
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    return dict(label=label, n=n, rec=len(rec), flow=len(flw), queue=len(qcl),
                opp=len(opp), matched=len(mat),
                mqty=sum(e["matched_qty"] for e in mat),
                conv100=round(100.0 * len(mat) / n, 1) if n else None,
                lat50=round(q(lat, .5), 0) if lat else None,
                lat90=round(q(lat, .9), 0) if lat else None,
                ext50=q(ext, .5), extmax=(ext[-1] if ext else None),
                basis=round(med(bs), 4) if bs else None,
                basis_min=(bs[0] if bs else None),
                gross=round(1.0 - med(bs), 4) if bs else None,
                unres=len(unres),
                res_qty=med([e["residual_qty"] for e in unres]) if unres else None,
                res50=round(q(sorted(e["residual_s"] for e in unres), .5), 0) if unres else None,
                res90=round(q(sorted(e["residual_s"] for e in unres), .9), 0) if unres else None)


HDR = ("%-26s %5s %6s %6s %6s %6s %7s %8s %8s %7s %8s %7s %7s %7s")
COLS = ("cohort", "1stFl", "COMPL", "FLOW", "QCLR", "MATCH", "mQty",
        "conv/100", "lat_p50", "medEXT", "basis", "gross", "unres", "resDur")


def line(b):
    return HDR % (b["label"], b["n"], b["rec"], b["flow"], b["queue"],
                  b["matched"], b["mqty"],
                  b["conv100"] if b["conv100"] is not None else "-",
                  ("%.0fs" % b["lat50"]) if b["lat50"] is not None else "-",
                  b["ext50"] if b["ext50"] is not None else "-",
                  ("%.4f" % b["basis"]) if b["basis"] else "-",
                  ("%+.4f" % b["gross"]) if b["gross"] else "-",
                  b["unres"],
                  ("%.0fs" % b["res50"]) if b["res50"] is not None else "-")
