"""The one accounting every BTC5m research result must use.

A result is what an EXECUTABLE RULE earns: settlement proceeds minus
entry cost minus fees plus credited rebates, summed over every fill the
rule makes, over every window the rule ran on (including windows where
it did nothing). Nothing is averaged per window, per cell or per signal
before being summed. Intervals resample WINDOWS, never fills.

Usage:
    led = Ledger(windows_run=all_window_ids)      # every window offered
    led.fill(window, shares, price, fee, won)     # one per fill
    print(led.report("rule name"))

`size_known` must be False whenever the size available at the price was
not observed; the report then says so on its face.
"""
import random


class Ledger:
    def __init__(self, windows_run, size_known=False):
        self.windows = list(dict.fromkeys(windows_run))
        if not self.windows:
            raise ValueError("a rule must state the windows it ran on")
        self.size_known = size_known
        self._w = {w: [0.0, 0.0, 0.0, 0.0, 0] for w in self.windows}
        #          shares cost  fees  proceeds fills

    def fill(self, window, shares, price, fee_usd, won, rebate_usd=0.0):
        if window not in self._w:
            raise KeyError("fill in a window the rule did not run on")
        if shares <= 0 or not (0 < price < 1):
            raise ValueError("bad fill")
        a = self._w[window]
        a[0] += shares
        a[1] += shares * price
        a[2] += fee_usd - rebate_usd
        a[3] += shares * (1.0 if won else 0.0)
        a[4] += 1

    @staticmethod
    def _tot(rows):
        sh = sum(r[0] for r in rows)
        cost = sum(r[1] for r in rows)
        fees = sum(r[2] for r in rows)
        proc = sum(r[3] for r in rows)
        return sh, cost, fees, proc

    def totals(self):
        sh, cost, fees, proc = self._tot(self._w.values())
        return {"windows_run": len(self.windows),
                "windows_traded": sum(1 for r in self._w.values() if r[4]),
                "fills": sum(r[4] for r in self._w.values()),
                "shares": sh, "cost": cost, "fees": fees, "proceeds": proc,
                "net": proc - cost - fees,
                "gross": proc - cost}

    def _boot(self, fn, n=3000, seed=17):
        rows = list(self._w.values())
        rng = random.Random(seed)
        k = len(rows)
        vals = []
        for _ in range(n):
            v = fn([rng.choice(rows) for _ in range(k)])
            if v is not None:
                vals.append(v)
        vals.sort()
        if len(vals) < n // 2:
            return None
        return vals[int(.025 * len(vals))], vals[int(.975 * len(vals)) - 1]

    def report(self, name):
        t = self.totals()
        if not t["shares"]:
            return "%s: no fills in %d windows" % (name, t["windows_run"])

        def per_share(rows):
            sh, cost, fees, proc = self._tot(rows)
            return (proc - cost - fees) / sh if sh else None

        def per_window(rows):
            sh, cost, fees, proc = self._tot(rows)
            return (proc - cost - fees) / len(rows)

        c1, c2 = self._boot(per_share), self._boot(per_window)
        f = lambda c: "[%+.4f,%+.4f]" % c if c else "[n/a]"
        return (
            "%s\n"
            "   windows run %d, traded %d; fills %d; shares %.2f%s\n"
            "   cost $%.2f  fees $%.2f  settlement proceeds $%.2f\n"
            "   NET $%+.2f (before fees $%+.2f)\n"
            "   per share %+.4f %s   per window run %+.4f %s" % (
                name, t["windows_run"], t["windows_traded"], t["fills"],
                t["shares"],
                "" if self.size_known else
                "  (SIZE AT THE PRICE NOT OBSERVED: assumed fillable)",
                t["cost"], t["fees"], t["proceeds"], t["net"], t["gross"],
                t["net"] / t["shares"], f(c1),
                t["net"] / t["windows_run"], f(c2)))
