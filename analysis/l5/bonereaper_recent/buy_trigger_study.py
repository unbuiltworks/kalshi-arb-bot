"""What is different about the seconds just before Bonereaper buys?

His buying is grouped into BURSTS (his fills on one side with no gap of
3 s or more). For the first second of each burst we look at what every
OTHER trader did in the seconds before it, and compare with CONTROL
seconds: every second of the same window, same minute, in which he was
not buying and had not bought in the previous 3 s, taking both sides.
The tape is stamped to the second, so "before" means strictly earlier
seconds; what happened inside the same second cannot be ordered.

Rows are plain averages over bursts / control side-seconds, by half.
"""
import bisect, collections, datetime as dt, gzip, json, re, sys

act, tape_path = sys.argv[1], sys.argv[2]
BR = collections.defaultdict(list)
for line in gzip.open(act, "rt"):
    r = json.loads(line)
    if re.match(r"btc-updown-5m-\d+$", r.get("slug") or "") and r["type"] == "TRADE":
        BR[r["slug"]].append((r["timestamp"], r["outcome"].upper(), r["usdc_size"], r["price"]))
day = lambda s: dt.datetime.fromtimestamp(int(s.rsplit("-", 1)[1]), dt.timezone.utc).strftime("%m-%d")
agg = {h: {k: collections.Counter() for k in ("burst", "ctrl")} for h in ("11-17/18", "11-19/20")}
gaps = collections.Counter(); first_t = collections.Counter(); bursts_per_w = []
for line in gzip.open(tape_path, "rt"):
    d = json.loads(line); s = d["slug"]
    if s not in BR:
        continue
    h = "11-17/18" if day(s) in ("11-17", "11-18") else "11-19/20"
    t0 = int(s.rsplit("-", 1)[1])
    # others' prints per second: buys in dollars by the side they favour, count, UP price
    buy = {"UP": collections.Counter(), "DOWN": collections.Counter()}
    cnt = collections.Counter(); big = collections.Counter(); pts, pxs = [], []
    for ts, oc, side, px, sz, isbr, _h in sorted(d["trades"]):
        oc = oc.upper()
        pts.append(ts); pxs.append(px if oc == "UP" else 1 - px)
        if isbr:
            continue
        fav = oc if side == "BUY" else ("DOWN" if oc == "UP" else "UP")
        usd = px * sz if side == "BUY" else (1 - px) * sz
        buy[fav][ts] += usd; cnt[ts] += 1; big[ts] = max(big[ts], usd)
    def price(t):
        i = bisect.bisect_right(pts, t) - 1
        return None if i < 0 else pxs[i]
    def feats(t, side):
        o = "DOWN" if side == "UP" else "UP"
        p1, p6, p11 = price(t - 1), price(t - 6), price(t - 11)
        sg = 1 if side == "UP" else -1
        return {
            "n": 1,
            "others_prints_prev_1s": cnt[t - 1],
            "others_prints_prev_5s": sum(cnt[t - k] for k in range(1, 6)),
            "others_usd_same_side_prev_1s": buy[side][t - 1],
            "others_usd_other_side_prev_1s": buy[o][t - 1],
            "others_usd_same_side_prev_5s": sum(buy[side][t - k] for k in range(1, 6)),
            "others_usd_other_side_prev_5s": sum(buy[o][t - k] for k in range(1, 6)),
            "big_print_500usd_prev_2s": int(max(big[t - 1], big[t - 2]) >= 500),
            "side_price_move_prev_5s": 0 if None in (p1, p6) else sg * (p1 - p6),
            "side_price_move_prev_10s": 0 if None in (p1, p11) else sg * (p1 - p11),
            "side_rose_1c_prev_5s": int(None not in (p1, p6) and sg * (p1 - p6) >= 0.01),
            "side_fell_1c_prev_5s": int(None not in (p1, p6) and sg * (p1 - p6) <= -0.01),
            "side_price_level": 0 if p1 is None else (p1 if side == "UP" else 1 - p1),
        }
    mine = sorted(BR[s]); busy = set()
    starts = []
    last = {}
    for ts, oc, usd, px in mine:
        for k in range(0, 4):
            busy.add(ts + k)
        if oc not in last or ts - last[oc] >= 3:
            starts.append((ts, oc))
        last[oc] = ts
    bursts_per_w.append(len(starts))
    ss = sorted(t for t, _ in starts)
    for a, b in zip(ss, ss[1:]):
        gaps[min(b - a, 60)] += 1
    if ss:
        first_t[max(min((ss[0] - t0) // 10 * 10, 60), -60)] += 1
    for ts, oc in starts:
        if 5 <= ts - t0 < 290:
            agg[h]["burst"].update(feats(ts, oc))
    for t in range(t0 + 5, t0 + 290):
        if t not in busy:
            for oc in ("UP", "DOWN"):
                agg[h]["ctrl"].update(feats(t, oc))
keys = ["others_prints_prev_1s", "others_prints_prev_5s", "others_usd_same_side_prev_1s", "others_usd_other_side_prev_1s",
        "others_usd_same_side_prev_5s", "others_usd_other_side_prev_5s", "big_print_500usd_prev_2s",
        "side_price_move_prev_5s", "side_price_move_prev_10s", "side_rose_1c_prev_5s", "side_fell_1c_prev_5s", "side_price_level"]
for h in agg:
    b, c = agg[h]["burst"], agg[h]["ctrl"]
    print("\n== %s: %d bursts, %d control side-seconds ==" % (h, b["n"], c["n"]))
    print("   %-34s %12s %12s %8s" % ("in the seconds before", "his bursts", "control", "ratio"))
    for k in keys:
        x, y = b[k] / b["n"], c[k] / c["n"]
        print("   %-34s %12.4f %12.4f %8s" % (k, x, y, "%.2f" % (x / y) if abs(y) > 1e-9 else "-"))
print("\nbursts per window: median %d" % sorted(bursts_per_w)[len(bursts_per_w) // 2])
tot = sum(gaps.values())
print("seconds between the starts of consecutive bursts (share): " + "  ".join("%ds %.0f%%" % (g, 100 * gaps[g] / tot) for g in sorted(gaps) if gaps[g] / tot >= 0.03))
tf = sum(first_t.values())
print("first burst relative to the window start (share): " + "  ".join("%+ds %.0f%%" % (g, 100 * first_t[g] / tf) for g in sorted(first_t)))
