"""L5 vs PMUS state-normalised behavioural comparison.

Both sides reduced to the SAME event stream: an ordered list of ACQUISITIONS per
market, each with (t, side_yes, price_in_yes_terms). L5 comes from the public
canonical sequence (its own fills; its resting orders are NOT public). PMUS comes
from the arm-3 replay (B2 + queue-preserve + L5_HISTORICAL completion).

Every metric is computed at an EQUIVALENT INVENTORY STATE -- conditioned on the
same-side run length and on signed residual -- so the comparison is not a raw
average over two different market clocks.
"""
import gzip, csv, sys, json, pickle, collections, statistics as stt
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
CORPUS = "/home/user/kalshi-arb-bot/analysis/l5/l5_canonical_sequence.csv.gz"


def l5_sequences():
    w = collections.defaultdict(list)
    with gzip.open(CORPUS, "rt") as fh:
        for r in csv.DictReader(fh):
            if r["side"] != "BUY":
                continue
            p = float(r["price"])
            w[r["window_id"]].append({
                "t": float(r["timestamp"]), "seq": int(r["trade_seq"]),
                "sy": r["outcome"] == "Up", "px": p if r["outcome"] == "Up" else 1.0 - p,
                "own_px": p, "qty": float(r["quantity"]),
                "tte": float(r["seconds_to_settlement"] or 0.0),
                "lane": r["asset"] + r["duration_minutes"] + "m",
                "settle_up": r["settlement_outcome"] == "Up",
                "resid": float(r["residual_qty_signed"] or 0.0),
                "matched": float(r["matched_qty"] or 0.0)})
    for k in w:
        w[k].sort(key=lambda r: (r["seq"], r["t"]))
    return w


def pmus_sequences(res):
    out = {}
    for x in res:
        out[x["slug"]] = [{"t": a["t"], "sy": a["sy"], "px": a["px"] if a["sy"] else 1.0 - a["px"],
                           "own_px": a["px"], "qty": 1.0, "rel": a["rel"],
                           "resid": float(a["resid_after"]), "comp_live": a["comp_live"]}
                          for a in x["acqs"]]
    return out


def runs_and_switches(seqs):
    """Per decision point i>=1: run length of the side just before, and whether
    the NEXT acquisition flipped side. Conditioning uses fills 0..i-1 only."""
    by_run = collections.defaultdict(lambda: [0, 0])     # run -> [switch, total]
    runlens = []
    gaps = []
    moves = []
    for k, f in seqs.items():
        if len(f) < 2:
            continue
        run = 1
        for i in range(1, len(f)):
            sw = f[i]["sy"] != f[i - 1]["sy"]
            b = by_run[min(run, 6)]
            b[1] += 1
            if sw:
                b[0] += 1
            gaps.append(f[i]["t"] - f[i - 1]["t"])
            moves.append(abs(f[i]["px"] - f[i - 1]["px"]))
            if sw:
                runlens.append(run); run = 1
            else:
                run += 1
        runlens.append(run)
    return by_run, runlens, gaps, moves
