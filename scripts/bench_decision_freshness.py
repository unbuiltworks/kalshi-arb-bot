"""R5's 468-tick workload, with the real limiter, before and after.

The venue is mocked at a fixed network latency; the RATE LIMITER IS THE REAL
ONE (`l5_pmus_transport.RateLimiter`, rps=1.5, burst=4.0) with a virtual
clock, because that limiter -- not the network -- is what produced R5's
1.39-6.98 s book age. Its `before()` sleeps inside the decision path.

Nothing here touches LOCK5. The only variables are how many authoritative
status reads a tick spends and whether they overlap.
"""
import statistics
from typing import List

from src import l5_pmus_transport as TP

NET_MS = 80.0                 # measured R5 mandate->arrival, 80-85 ms
TICKS = 468                   # R5's own tick count
READS_PER_TICK = 1112 / 468   # R5's measured 2.376 status reads per tick


class VClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))


def run(*, reads_per_tick: float, fanout: int, label: str,
        writes_per_tick: int = 2):
    """Simulate one window's decision loop against the real limiter."""
    c = VClock()
    rl = TP.RateLimiter(now_fn=c.now, sleep_fn=c.sleep)
    ages: List[float] = []
    book_t = 0.0
    BOOK_HZ = 2.0             # R5 journalled 2000 book states over ~900 s x2
    whole = int(reads_per_tick)
    frac = reads_per_tick - whole
    for i in range(TICKS):
        # the freshest book available when this tick begins
        book_t = c.t - (c.t % (1.0 / BOOK_HZ))
        n = whole + (1 if (i * frac) % 1.0 < frac else 0)
        if fanout <= 1:
            for _ in range(n):
                rl.before(write=False)
                c.t += NET_MS / 1000.0
        else:
            # overlapping reads still take their tokens from ONE bucket; only
            # the network time is shared.
            for _ in range(n):
                rl.before(write=False)
            c.t += NET_MS / 1000.0 * (1.0 if n else 0.0)
        ages.append((c.t - book_t) * 1000.0)
        # A SUBMITTING TICK COSTS TWO WRITES, not one: submit_body does
        # POST /v1/order/preview and then POST /v1/orders. Writes do not
        # sleep in the limiter, but they DRAIN the same bucket, so the next
        # read waits longer. Modelling one write understated the floor.
        for _ in range(writes_per_tick):
            rl.before(write=True)
            c.t += NET_MS / 1000.0
    ages.sort()
    return {
        "label": label, "ticks": TICKS,
        "reads_per_tick": round(reads_per_tick, 3),
        "median": ages[len(ages) // 2],
        "p95": ages[int(0.95 * len(ages))],
        "max": ages[-1],
        "throttled_waits": rl.counts["throttled_waits"],
        "total_wait_s": round(rl.total_wait_s, 1),
        "wall_s": round(c.t, 1),
    }


if __name__ == "__main__":
    # AFTER: the terminal-verdict cache removes the repeat reads. R5's 1,112
    # reads were resolve_open_orders (one per nonterminal order) PLUS a cancel
    # pre-check and a cancel-failure verdict per side. With terminal verdicts
    # cached within the pass, the repeats collapse to the nonterminal orders
    # only -- R5 held at most 2 nonterminal orders at a time, so ~1.0/tick.
    rows = [
        run(reads_per_tick=READS_PER_TICK, fanout=1,
            label="BEFORE  2.38 reads/tick, serial"),
        run(reads_per_tick=1.0, fanout=1,
            label="AFTER   1.00 reads/tick (cache), serial"),
        run(reads_per_tick=READS_PER_TICK, fanout=4,
            label="FANOUT ONLY 2.38 reads/tick, 4 threads"),
        run(reads_per_tick=1.0, fanout=4,
            label="BOTH    1.00 reads/tick, 4 threads"),
    ]
    print("%-42s %9s %9s %9s %10s %9s"
          % ("variant", "median", "p95", "max", "waits", "wall_s"))
    for r in rows:
        print("%-42s %8.0fms %8.0fms %8.0fms %10d %9.1f"
              % (r["label"], r["median"], r["p95"], r["max"],
                 r["throttled_waits"], r["wall_s"]))
    print()
    print("R5 MEASURED book age at decision: 1390-6981 ms")
    print("ACCEPTANCE: p95 book age at decision < 1000 ms")
    for r in rows:
        print("  %-42s p95 %7.0fms  %s"
              % (r["label"], r["p95"], "PASS" if r["p95"] < 1000 else "FAIL"))
