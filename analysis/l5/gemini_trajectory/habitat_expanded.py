"""Habitat score on the COMPLETE canonical Gemini 5m tape, BTC and ETH.
Identical frozen score, identical L5 reference, no Gemini tuning."""
import collections, gzip, json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..", "..", "..")))
from src import l5_habitat_score as hs                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CANON = os.path.join(HERE, "gemini_5m_canonical.jsonl.gz")
REF = os.path.join(HERE, "..", "habitat", "l5_reference.json")
DUR_S, TICK, MIN_SPAN = 300.0, 0.01, 60.0


def q(v, p):
    v = sorted(x for x in v if x is not None)
    return round(v[min(len(v) - 1, int(p * (len(v) - 1)))], 4) if v else None


def main():
    ref = json.load(open(REF))
    obs = collections.defaultdict(list)
    asset = {}
    for line in gzip.open(CANON, "rt"):
        r = json.loads(line)
        if r["kind"] != "bbo":
            continue
        tte = r.get("tte_s")
        # IN-WINDOW ONLY: outside its own 300 s a 5m symbol carries a static
        # placeholder quote that drives price_span to 0
        if tte is None or not (0.0 <= tte <= DUR_S):
            continue
        bid, ask = r.get("bid"), r.get("ask")
        if bid is None or ask is None:
            continue
        asset[r["market"]] = r.get("asset")
        obs[r["market"]].append({
            "t": r["t"], "price": (bid + ask) / 2.0,
            "two_sided": bid > 0 and ask > 0,
            "spread_ticks": round((ask - bid) / TICK, 2),
            "tte_s": tte,
            "depth_at_touch": (None if r.get("bid_sz") is None
                               or r.get("ask_sz") is None
                               else min(r["bid_sz"], r["ask_sz"]))})
    scored = collections.defaultdict(list)
    limited = collections.Counter()
    for m, v in obs.items():
        v.sort(key=lambda o: o["t"])
        f = hs.window_features(v)
        if not f.get("usable") or f["span_s"] < MIN_SPAN:
            continue
        cand = {k: x for k, x in f.items() if not k.startswith("_")}
        cand["observations_per_min"] = round(f["observations"] /
                                             (f["span_s"] / 60.0), 4)
        s = hs.score(cand, ref)
        for lf in (s.get("sampling_limited_features") or []):
            limited[lf] += 1
        scored[asset.get(m)].append((m, s, f))
    scored["BOTH"] = scored["BTC"] + scored["ETH"]
    print(f"{'':26}{'BTC':>12}{'ETH':>12}{'BOTH':>12}")
    print("-" * 62)

    def row(label, fn):
        print(f"{label:26}" + "".join(f"{str(fn(scored[g])):>12}"
                                      for g in ("BTC", "ETH", "BOTH")))
    row("windows scored", lambda v: len(v))
    row("score p25", lambda v: q([s["habitat_score_on_observed"] for _, s, _ in v], .25))
    row("score p50", lambda v: q([s["habitat_score_on_observed"] for _, s, _ in v], .50))
    row("score p75", lambda v: q([s["habitat_score_on_observed"] for _, s, _ in v], .75))
    row("coverage p50", lambda v: q([s["coverage"] for _, s, _ in v], .50))
    row("discounted p50", lambda v: q([s["coverage_discounted_score"] for _, s, _ in v], .50))
    row("obs/min p50", lambda v: q([s["observations_per_min"] for _, s, _ in v], .50))
    row("span_s p50", lambda v: q([f["span_s"] for _, _, f in v], .50))
    for feat in [x["name"] for x in hs.CALIBRATED_FEATURES]:
        row("  " + feat[:24], lambda v, ff=feat: q(
            [(s["per_feature"].get(ff) or {}).get("score") for _, s, _ in v], .50))
    print(f"\nsampling_limited: {dict(limited)}")
    print("prior curated-extract figure: Gemini 5m p50 0.3944 over 97 windows")


if __name__ == "__main__":
    main()
