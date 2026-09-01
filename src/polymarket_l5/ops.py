"""24/7 READ-ONLY operations layer for the canonical L5 pipeline.

Issue #167 operating requirement: the research system must run continuously
WITHOUT an agent session being open. This module is the workhorse each
scheduled invocation runs — Claude is not the always-on process; ordinary
durable infrastructure (GitHub Actions schedule, or any cron) invokes these
one-shot, restart-safe subcommands:

  collect     incremental public-activity collection: fetch the wallet's
              newest /activity rows, append only unseen fills to an
              append-only dated JSONL (raw-source preservation), update the
              cursor state, write a heartbeat. Idempotent: re-running never
              duplicates a row (fingerprint dedup against the day files).
  shadow      incremental replay/shadow comparison: run newly collected
              fills through the venue adapter + canonical inventory state,
              then score the session against the historical behavior
              baselines (both-sides rate, pair cost) and the action model
              (state coverage, mean probability of observed actions).
              Behavioral comparison only — no P&L is claimed, nothing here
              is "realized" economics.
  regression  periodic full-replay check: verify the committed canonical
              dataset hash matches its meta, rebuild the action model and
              compare, and run the deterministic test suite. Writes a
              regression report artifact.
  status      print heartbeat + cursor state + most recent failure artifact.

Durability contract:
  * state:      data/l5/ops/state.json          (cursor, counters)
  * raw rows:   data/l5/ops/activity_YYYYMMDD.jsonl (append-only, per day,
                one JSON row per fill, verbatim API payload + provenance)
  * shadow:     data/l5/ops/shadow_YYYYMMDD.jsonl
  * heartbeat:  data/l5/ops/heartbeat.json      (status, lag, counters)
  * failures:   data/l5/ops/failures/failure_<utc>.json (exception + context,
                retained for review; a failure also flips heartbeat status)

Market-agnostic: fills are interpreted through the VenueAdapter registry
(--venue), so future adapters (other categories or venues) reuse the same
collection/shadow/heartbeat machinery. The only Polymarket-specific piece
is the activity source URL, isolated in SOURCES.

SAFETY: read-only public HTTP. No authentication, no order transport, no
credentials, nothing that can trade.

    python3 -m src.polymarket_l5.ops collect
    python3 -m src.polymarket_l5.ops shadow
    python3 -m src.polymarket_l5.ops regression
    python3 -m src.polymarket_l5.ops status
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config
from .adapters import DEFAULT_ADAPTER, PolymarketUpDownAdapter, VenueAdapter
from .realtime_mirror import WindowState, canonical_row
from . import action_model as am

OPS_DIR = config.DATA_DIR / "ops"
FAIL_DIR = OPS_DIR / "failures"
STATE = OPS_DIR / "state.json"
HEARTBEAT = OPS_DIR / "heartbeat.json"
BEHAVIOR = config.OUTPUT_DIR / "l5_behavior.json"
ACTION_MODEL = config.OUTPUT_DIR / "l5_action_model.json"
SEQ_META = config.OUTPUT_DIR / "l5_canonical_sequence.meta.json"
SEQ_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"

L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"

ADAPTERS: Dict[str, VenueAdapter] = {"polymarket": PolymarketUpDownAdapter()}
SOURCES = {
    "polymarket": ("https://data-api.polymarket.com/activity?user={wallet}"
                   "&limit=500&offset={offset}&sortBy=TIMESTAMP"
                   "&sortDirection=DESC"),
}
MAX_COLLECT_PAGES = 8          # 4000 rows >> any sane invocation gap


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fingerprint(row: Dict[str, Any]) -> str:
    key = "|".join(str(row.get(k, "")) for k in
                   ("transactionHash", "timestamp", "outcome", "outcomeIndex",
                    "price", "size", "type"))
    return hashlib.blake2b(key.encode(), digest_size=12).hexdigest()


def _read_state() -> Dict[str, Any]:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            pass
    return {"last_ts": 0, "rows_collected_total": 0, "runs": 0}


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(path)


def _heartbeat(status: str, **fields: Any) -> None:
    _write_json(HEARTBEAT, {"utc": _utcnow(), "status": status,
                            "read_only": True, **fields})


def _failure(stage: str, exc: BaseException, **ctx: Any) -> Path:
    FAIL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = FAIL_DIR / f"failure_{stamp}_{stage}.json"
    _write_json(path, {"utc": _utcnow(), "stage": stage,
                       "error": repr(exc),
                       "traceback": traceback.format_exc()[-4000:],
                       "context": ctx})
    _heartbeat("error", stage=stage, error=repr(exc),
               failure_artifact=str(path))
    return path


def _day_file(prefix: str, ts: float) -> Path:
    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
    return OPS_DIR / f"{prefix}_{day}.jsonl"


def _existing_fingerprints(prefix: str, ts_values: Iterable[float]) -> set:
    """Fingerprints already stored in the day files the given timestamps
    map to (plus each day's neighbors, for midnight-boundary rows)."""
    paths = set()
    for ts in ts_values:
        for delta in (-86400.0, 0.0, 86400.0):
            paths.add(_day_file(prefix, ts + delta))
    fps: set = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                fps.add(json.loads(line).get("_fp"))
            except json.JSONDecodeError:
                continue
    return fps


def _fetch(url: str, retries: int = 3) -> Optional[Any]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "kalshi-arb-bot/l5-ops (read-only research collector)"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


# ---------------- collect ----------------

def run_collect(venue: str, wallet: str) -> Dict[str, Any]:
    state = _read_state()
    now = time.time()
    template = SOURCES[venue]
    fetched: List[Dict[str, Any]] = []
    oldest_fetched = None
    for page in range(MAX_COLLECT_PAGES):
        rows = _fetch(template.format(wallet=wallet, offset=page * 500))
        if not isinstance(rows, list) or not rows:
            break
        page_ts = [float(r.get("timestamp") or 0) for r in rows
                   if isinstance(r, dict)]
        oldest_fetched = min(page_ts) if page_ts else oldest_fetched
        fetched.extend(r for r in rows if isinstance(r, dict))
        # stop once the page is entirely before our cursor (with overlap slack)
        if page_ts and min(page_ts) < state.get("last_ts", 0) - 120:
            break
    seen = _existing_fingerprints(
        "activity", (float(r.get("timestamp") or now) for r in fetched))
    new_rows: List[Dict[str, Any]] = []
    for r in fetched:
        fp = _fingerprint(r)
        if fp in seen:
            continue
        seen.add(fp)
        r["_fp"] = fp
        r["_venue"] = venue
        r["_collected_utc"] = _utcnow()
        new_rows.append(r)
    gap_warning = (oldest_fetched is not None and state.get("last_ts")
                   and oldest_fetched > state["last_ts"] + 1
                   and state.get("rows_collected_total", 0) > 0)
    new_rows.sort(key=lambda r: float(r.get("timestamp") or 0))
    by_file: Dict[Path, List[str]] = {}
    for r in new_rows:
        by_file.setdefault(_day_file("activity", float(r.get("timestamp") or now)),
                           []).append(json.dumps(r, separators=(",", ":")))
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    for path, lines in by_file.items():
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    if new_rows:
        state["last_ts"] = max(float(r.get("timestamp") or 0) for r in new_rows)
    state["rows_collected_total"] = state.get("rows_collected_total", 0) + len(new_rows)
    state["runs"] = state.get("runs", 0) + 1
    state["last_run_utc"] = _utcnow()
    _write_json(STATE, state)
    report = {
        "rows_new": len(new_rows),
        "cursor_ts": state["last_ts"],
        "cursor_lag_s": round(now - state["last_ts"], 1) if state["last_ts"] else None,
        "rows_total": state["rows_collected_total"],
        "runs": state["runs"],
        "possible_gap_before_oldest_fetch": bool(gap_warning),
    }
    _heartbeat("ok", stage="collect", **report)
    return report


# ---------------- shadow ----------------

def run_shadow(venue: str) -> Dict[str, Any]:
    adapter = ADAPTERS[venue]
    state = _read_state()
    shadow_cursor = state.get("shadow_ts", 0.0)
    now = time.time()
    rows: List[Dict[str, Any]] = []
    for path in sorted(OPS_DIR.glob("activity_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "TRADE" and float(r.get("timestamp") or 0) > shadow_cursor:
                rows.append(r)
    rows.sort(key=lambda r: float(r.get("timestamp") or 0))

    states: Dict[str, WindowState] = {}
    canon: List[Dict[str, Any]] = []
    for r in rows:
        win = adapter.parse_window(r)
        if win is None:
            continue
        win_d = {"asset": win.asset, "duration_minutes": win.duration_minutes,
                 "window_start": win.window_start, "window_end": win.window_end,
                 "slug": win.window_id}
        st = states.setdefault(win.window_id, WindowState())
        row = canonical_row(r, win_d, st, adapter)
        if row is not None:
            canon.append(row)

    # score vs historical baselines + action model
    per_window: Dict[str, Dict[str, Any]] = {}
    for row in canon:
        per_window[row["window_id"]] = row
    both = sum(1 for r in per_window.values()
               if r["cum_up_qty"] > 0 and r["cum_down_qty"] > 0)
    pcs = sorted(r["matched_pair_unit_cost"] for r in per_window.values()
                 if r.get("matched_pair_unit_cost") is not None)
    model = json.loads(ACTION_MODEL.read_text()) if ACTION_MODEL.exists() else None
    probs: List[float] = []
    off_dist = 0
    if model:
        by_win: Dict[str, List[Dict[str, Any]]] = {}
        for row in canon:
            by_win.setdefault(row["window_id"], []).append(row)
        for wrows in by_win.values():
            for prev, nxt in zip(wrows, wrows[1:]):
                sk = am.state_key(prev["price"], nxt["seconds_to_settlement"],
                                  prev["residual_qty_signed"])
                ak = am.action_key(
                    am._relation(prev["residual_qty_signed"], nxt["outcome"],
                                 nxt["side"]), nxt["quantity"])
                scored = am.compare_action(model, sk, ak)
                if not scored["observed_by_l5"]:
                    off_dist += 1
                elif scored["probability"] is not None:
                    probs.append(scored["probability"])
    hist = {}
    if BEHAVIOR.exists():
        b = json.loads(BEHAVIOR.read_text())
        hist = {"both_sides_pct": b.get("q1_both_sides_pct"),
                "median_pair_cost": (b.get("q2_pair_cost_distribution") or {}).get("median")}
    report = {
        "utc": _utcnow(),
        "fills_processed": len(canon),
        "windows": len(per_window),
        "live_both_sides_pct": round(100 * both / len(per_window), 1)
            if per_window else None,
        "live_median_pair_cost": round(pcs[len(pcs) // 2], 4) if pcs else None,
        "historical_baseline": hist,
        "action_model": {
            "decisions_scored": len(probs) + off_dist,
            "mean_prob_of_observed_action": round(
                sum(probs) / len(probs), 4) if probs else None,
            "off_distribution_decisions": off_dist,
        },
        "note": ("behavioral comparison only; windows may be mid-flight; "
                 "no P&L is claimed"),
    }
    if canon:
        state["shadow_ts"] = max(r["timestamp"] for r in canon)
    state["last_shadow_utc"] = _utcnow()
    _write_json(STATE, state)
    if canon:
        sf = _day_file("shadow", now)
        with sf.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, separators=(",", ":")) + "\n")
    _heartbeat("ok", stage="shadow", **{k: report[k] for k in
                                        ("fills_processed", "windows")})
    return report


# ---------------- regression ----------------

def run_regression() -> Dict[str, Any]:
    report: Dict[str, Any] = {"utc": _utcnow(), "checks": {}}
    meta = json.loads(SEQ_META.read_text())
    digest = hashlib.sha256(gzip.open(SEQ_CSV, "rb").read()).hexdigest()[:16]
    report["checks"]["canonical_sha_matches_meta"] = (
        digest == meta.get("content_sha256_16"))
    committed = json.loads(ACTION_MODEL.read_text())
    rebuilt = am.build_model(since_epoch=committed.get("since_epoch"))
    report["checks"]["action_model_reproducible"] = (
        rebuilt["n_decisions"] == committed["n_decisions"]
        and rebuilt["n_states"] == committed["n_states"])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_l5_canonical.py",
         "tests/test_l5_realtime_mirror.py",
         "tests/test_polymarket_l5_disciplined_clone.py", "-q"],
        capture_output=True, text=True, timeout=600)
    report["checks"]["tests_pass"] = proc.returncode == 0
    report["tests_tail"] = proc.stdout.strip().splitlines()[-1:] \
        if proc.stdout else []
    report["ok"] = all(report["checks"].values())
    path = OPS_DIR / f"regression_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    _write_json(path, report)
    _heartbeat("ok" if report["ok"] else "error", stage="regression",
               **report["checks"])
    return report


def run_status() -> Dict[str, Any]:
    out = {"heartbeat": json.loads(HEARTBEAT.read_text())
           if HEARTBEAT.exists() else None,
           "state": _read_state()}
    fails = sorted(FAIL_DIR.glob("failure_*.json")) if FAIL_DIR.exists() else []
    out["failures_retained"] = len(fails)
    if fails:
        out["latest_failure"] = json.loads(fails[-1].read_text())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("collect", "shadow", "regression", "status"))
    ap.add_argument("--venue", default="polymarket", choices=sorted(ADAPTERS))
    ap.add_argument("--wallet", default=L5_WALLET)
    args = ap.parse_args()
    try:
        if args.cmd == "collect":
            report = run_collect(args.venue, args.wallet)
        elif args.cmd == "shadow":
            report = run_shadow(args.venue)
        elif args.cmd == "regression":
            report = run_regression()
        else:
            report = run_status()
    except Exception as exc:                      # noqa: BLE001
        path = _failure(args.cmd, exc)
        print(json.dumps({"status": "error", "failure_artifact": str(path),
                          "error": repr(exc)}))
        return 1
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
