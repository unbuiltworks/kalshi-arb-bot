"""What does the price do after it jumps? From jump_recorder.py output.

For every jump of >= 2 ticks between consecutive readings (at most 5 s
apart) in an in-play market, place a HYPOTHETICAL resting bid on the side
the jump runs into, k ticks behind the best price before the jump
(k = 1, 2, 3): YES for a down-jump, NO for an up-jump. It counts as filled
when the price has moved strictly through it. Then: the value of the side
bought against the fill price 5 s, 30 s, 2 min and 5 min later (mid price).

This is a model of the fill, not a fill: it ignores queue position (a real
order at the back of its level fills later or not at all) and assumes our
size does not move the market. It answers one question: is there a
reversal to earn at each depth, before any reward.
usage: jump_reversal.py <recorded.jsonl>"""
import collections
import json
import statistics as st
import sys

TICK = 0.01
HOR = (5, 30, 120, 300)


def load(path):
    series = collections.defaultdict(list)
    for line in open(path):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if "s" not in d or d.get("b") is None or d.get("a") is None:
            continue
        if d.get("st") not in (None, "MARKET_STATE_OPEN"):
            continue
        if not (0 < d["b"] < d["a"] < 1):
            continue
        series[d["s"]].append((d["t"], d["b"], d["a"]))
    return series


def mid_at(rows, i, t):
    """Mid at the first reading at or after time t (None if the series
    ends or there is a gap of more than 20 s there)."""
    j = i
    while j < len(rows) and rows[j][0] < t:
        j += 1
    if j >= len(rows) or rows[j][0] - t > 20:
        return None
    return (rows[j][1] + rows[j][2]) / 2


def study(series):
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    jumps = collections.Counter()
    hours = 0.0
    for slug, rows in series.items():
        rows.sort()
        sport = slug.split("-")[1]
        for i in range(1, len(rows)):
            t0, b0, a0 = rows[i - 1]
            t1, b1, a1 = rows[i]
            if t1 - t0 <= 5:
                hours += (t1 - t0) / 3600
            if t1 - t0 > 5 or a0 - b0 > 0.03:
                continue
            down = round((b0 - b1) / TICK)
            up = round((a1 - a0) / TICK)
            if down >= 2:
                jumps[(sport, "down", min(down, 6))] += 1
                for k in (1, 2, 3):
                    px = b0 - k * TICK
                    if b1 < px - 1e-9:                # traded through
                        for h in HOR:
                            m = mid_at(rows, i, t1 + h)
                            if m is not None:
                                out[(sport, k)][h].append(m - px)
            if up >= 2:
                jumps[(sport, "up", min(up, 6))] += 1
                for k in (1, 2, 3):
                    offer = a0 + k * TICK             # NO bid at 1 - offer
                    if a1 > offer + 1e-9:
                        for h in HOR:
                            m = mid_at(rows, i, t1 + h)
                            if m is not None:
                                out[(sport, k)][h].append(offer - m)
    return out, jumps, hours


def main():
    series = load(sys.argv[1])
    out, jumps, hours = study(series)
    print("markets:", len(series), " market-hours:", round(hours, 1))
    by = collections.Counter()
    for (sp, d, n), c in jumps.items():
        by[sp] += c
    print("jumps of 2+ ticks by sport:", dict(by))
    print("\nsport depth | horizon: n, mean c, median c, share positive")
    for (sp, k) in sorted(out):
        cells = []
        for h in HOR:
            xs = out[(sp, k)][h]
            if len(xs) < 5:
                cells.append("%4ds: n=%d" % (h, len(xs)))
                continue
            se = st.pstdev(xs) / (len(xs) ** 0.5)
            cells.append("%4ds: n=%d mean %+.2f (se %.2f) med %+.2f pos %.0f%%"
                         % (h, len(xs), 100 * st.mean(xs), 100 * se,
                            100 * st.median(xs),
                            100 * sum(x > 0 for x in xs) / len(xs)))
        print(sp, "k=%d" % k, "|", " | ".join(cells))
    allk = collections.defaultdict(lambda: collections.defaultdict(list))
    for (sp, k), hs in out.items():
        for h, xs in hs.items():
            allk[k][h] += xs
    print("\nall sports")
    for k in sorted(allk):
        cells = []
        for h in HOR:
            xs = allk[k][h]
            if len(xs) < 5:
                cells.append("%4ds: n=%d" % (h, len(xs)))
                continue
            se = st.pstdev(xs) / (len(xs) ** 0.5)
            cells.append("%4ds: n=%d mean %+.2f (se %.2f) pos %.0f%%"
                         % (h, len(xs), 100 * st.mean(xs), 100 * se,
                            100 * sum(x > 0 for x in xs) / len(xs)))
        print("k=%d" % k, "|", " | ".join(cells))


if __name__ == "__main__":
    main()
