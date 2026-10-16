#!/usr/bin/env python3
"""Reconcile PENDING shadow records against positive settlement evidence.

Reads the shadow store and `L5_LIVE_SETTLED` evidence lines, re-prices only
the records whose residual is unresolved, and writes the store back. Running
it twice is a no-op: a record already carrying `settlement_evidence` is
declined `ALREADY_RECONCILED`, so nothing can be counted twice.

  python3 scripts/l5_shadow_settlement_reconcile.py <evidence...> \
      [--store PATH] [--dry-run]

Only `resolution_source: VENUE_SETTLEMENT_PX` with a yes_value of exactly 0
or 1 is accepted. An expired book price is not a settlement.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import l5_shadow_policy_eval as E          # noqa: E402

DEFAULT_STORE = "data/l5/shadow/policy_eval.jsonl"


def harvest_settlements(texts):
    """{market: evidence} from L5_LIVE_SETTLED lines. Venue source only."""
    out = {}
    for text in texts:
        msgs = []
        try:
            msgs = [e.get("message", "")
                    for e in (json.loads(text).get("deploy") or [])]
        except Exception:
            msgs = text.splitlines()
        for m in msgs:
            tag, _, rest = m.partition(" ")
            if tag != "L5_LIVE_SETTLED":
                continue
            try:
                j = json.loads(rest)
            except Exception:
                continue
            mk = j.get("market")
            if not mk:
                continue
            out[mk] = {"resolution_source": j.get("resolution_source"),
                       "yes_value": j.get("yes_value"),
                       "observed_at": j.get("utc") or j.get("t")}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence", nargs="*")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    texts = []
    for src in a.evidence:
        texts.append(sys.stdin.read() if src == "-" else open(src).read())
    settle = harvest_settlements(texts)

    rows = []
    with open(a.store) as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    changed, census = 0, {}
    out = []
    for r in rows:
        basis = E.settlement_basis(r)
        if basis != E.BASIS_PENDING:
            census[basis] = census.get(basis, 0) + 1
            out.append(r)
            continue
        new = E.reconcile_settlement(r, settle.get(r.get("market")))
        if new.get("reconciled"):
            changed += 1
        else:
            k = "PENDING:%s" % new.get("reconcile_reason")
            census[k] = census.get(k, 0) + 1
        out.append(new)
    if not a.dry_run and changed:
        with open(a.store, "w") as fh:
            for r in out:
                fh.write(json.dumps(r, default=str) + "\n")
    print("records %d  reconciled %d%s" % (
        len(rows), changed, "  (dry run, not written)" if a.dry_run else ""))
    print("basis census: %s" % json.dumps(census, indent=2))
    print(json.dumps(E.summarize(out), indent=2, default=str))


if __name__ == "__main__":
    main()
