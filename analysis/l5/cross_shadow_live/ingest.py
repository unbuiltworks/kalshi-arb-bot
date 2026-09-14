"""Ingest cross-shadow rows from Railway log dumps into a durable cohort store.

READ-ONLY. Reads log JSON the MCP tool already saved to disk and appends
de-duplicated per-COMPLETE observations to cohort.jsonl. Nothing here touches
production, submits anything, or reinterprets a shadow row -- it only carries
the fields the probe already emitted.
"""
from __future__ import annotations
import glob, json, os, sys

SCR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(SCR, "cohort.jsonl")

OBS_FIELDS = ("slug", "bid", "ask", "bid_qty", "ask_qty", "both_sides_served",
              "book_source", "status_utc", "probe_receipt_utc",
              "authenticated_age_s", "probe_transport_delta_s",
              "age_at_probe_receipt_s", "order_buys", "executable_depth_field",
              "executable_price_field", "executable_depth",
              "executable_depth_status", "executable_complement_price")
LIVE_FIELDS = ("verdict", "first_stop", "quote_age_s", "intended_qty",
               "prospective_pair_cost_max", "would_submit", "shadow_only",
               "live_pair_ceiling_unchanged")
ECON_FIELDS = ("pair_cost", "gross_locked_pair_pnl", "confirmed_taker_fee",
               "maker_rebate_forgone", "net_locked_pair_pnl",
               "net_positive_after_confirmed_costs", "taker_price",
               "held_avg_cost", "maker_price_forgone", "contracts")

def load_store():
    rows = {}
    if os.path.exists(STORE):
        for line in open(STORE):
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[r["order_id"]] = r
    return rows

def ingest(paths):
    rows = load_store()
    before = len(rows)
    for path in paths:
        try:
            doc = json.load(open(path))
        except Exception:
            continue
        for entry in doc.get("deploy") or []:
            ts, msg = entry.get("timestamp"), entry.get("message", "")
            if "cross_shadow_live" not in msg:
                continue
            try:
                j = json.loads(msg.split(" ", 1)[1])
            except Exception:
                continue
            if j.get("kind") != "session_end":
                continue
            inv = j.get("inventory") or {}
            for o in (j.get("orders") or []):
                cs = o.get("cross_shadow_live")
                if not cs:
                    continue
                obs = (o.get("recorder_book") or {}).get(
                    "cross_shadow_observation") or {}
                econ = cs.get("economics") or {}
                row = {
                    "session_end_ts": ts,
                    "commit": j.get("commit"),
                    "slug": j.get("slug"),
                    "session_status": j.get("status"),
                    "order_id": o.get("order_id"),
                    "stage": o.get("stage"),
                    "side": o.get("side"),
                    "maker_px": o.get("px"),
                    "qty": o.get("qty"),
                    "filled_qty": o.get("filled_qty"),
                    "end_reason": o.get("end_reason"),
                    "outcome": o.get("outcome"),
                    "session_inv_residual": inv.get("residual"),
                    "session_inv_up": inv.get("up"),
                    "session_inv_dn": inv.get("dn"),
                }
                for f in OBS_FIELDS:
                    row["obs_" + f] = obs.get(f)
                for f in LIVE_FIELDS:
                    row[f] = cs.get(f)
                for f in ECON_FIELDS:
                    row["econ_" + f] = econ.get(f)
                if row["order_id"]:
                    rows[row["order_id"]] = row
    with open(STORE, "w") as fh:
        for oid in sorted(rows, key=lambda k: (rows[k].get("session_end_ts") or "", k)):
            fh.write(json.dumps(rows[oid], separators=(",", ":")) + "\n")
    return before, len(rows)

LEDGER = os.path.join(SCR, "ledger.jsonl")
LEDGER_KINDS = ("settlement", "settled", "realized", "session_end",
                "incentive", "incentives", "reward", "rewards",
                "settlement_audit", "risk_set_alarm")

def ingest_ledger(paths):
    """Venue-side facts, kept in a SEPARATE store from the shadow cohort.

    Settlement / realized / incentive rows are ACTUAL outcomes. They are never
    merged with cross-shadow economics; the report prints them as their own
    ledgers precisely so a theoretical number can never be added to a real one.
    """
    rows = {}
    if os.path.exists(LEDGER):
        for line in open(LEDGER):
            if line.strip():
                r = json.loads(line)
                rows[r["_key"]] = r
    before = len(rows)
    for path in paths:
        try:
            doc = json.load(open(path))
        except Exception:
            continue
        for entry in doc.get("deploy") or []:
            ts, msg = entry.get("timestamp"), entry.get("message", "")
            try:
                j = json.loads(msg.split(" ", 1)[1])
            except Exception:
                continue
            kind = str(j.get("kind") or "")
            if not any(k in kind for k in LEDGER_KINDS):
                continue
            r = {k: v for k, v in j.items()
                 if k != "orders" and not isinstance(v, (list, dict))}
            if kind == "session_end":
                r["inventory"] = j.get("inventory")
                r["fill_reconciliation_agrees"] = (
                    (j.get("fill_reconciliation") or {}).get("venue_vs_ledger")
                    or {}).get("agrees")
            r["_ts"] = ts
            r["_kind"] = kind
            r["_key"] = f"{ts}|{kind}|{j.get('slug')}"
            rows[r["_key"]] = r
    with open(LEDGER, "w") as fh:
        for k in sorted(rows):
            fh.write(json.dumps(rows[k], separators=(",", ":")) + "\n")
    return before, len(rows)


if __name__ == "__main__":
    paths = sys.argv[1:] or sorted(glob.glob(
        "/root/.claude/projects/-home-user-kalshi-arb-bot/*/tool-results/"
        "mcp-Railway-get-logs-*.txt"))
    b, a = ingest(paths)
    lb, la = ingest_ledger(paths)
    print(f"cohort: {b} -> {a} observations (+{a-b}) from {len(paths)} dump(s)")
    print(f"ledger: {lb} -> {la} venue/outcome rows (+{la-lb})")
