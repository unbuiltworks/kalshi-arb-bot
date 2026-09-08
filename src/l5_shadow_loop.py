"""L5 CONTINUOUS SHADOW LOOP -- the permanent, isolated 24/7 research path.

OWNER directive (issue #167 comment 5574408755, item 5): the research
object that compares L5's newest public activity with the frozen model
must live in its OWN process, never inside the execution-data recorder
(which starved today under a 5.7M-decision in-process shadow), and must
NEVER rewrite the live model. This module is that process:

    new L5 public activity            (src.polymarket_l5.ops.run_collect,
        |                              the existing read-only collector)
        v
    normalized sequential state       (VenueAdapter + canonical_row + the
        |                              SAME state_key / relation / v2_dist
        |                              the live decision path imports)
        v
    rolling action-by-action compare  (what frozen model v2 / lookup v3
        |                              recommended vs what L5 did; tallies
        |                              per base-state bucket)
        v
    drift metrics + alarm             (l5_model_drift_check.compare, state
        |                              mix shift, agreement floor -> stdout
        |                              L5_SHADOW_ALARM {json} + durable file)
        v
    evidence-backed PROPOSAL          (candidate lookup v{N+1} deltas +
                                       validation through the FROZEN
                                       fidelity spec; "promotion":
                                       "REQUIRES_OWNER_DECISION")

Isolation guarantees (asserted in code, see FROZEN_PATHS / _guard_write):
  * every write goes under L5_SHADOW_STATE_DIR (default /data/l5_shadow);
    a write to any frozen artifact or anywhere under analysis/l5 raises
    FrozenPathViolation before touching the filesystem;
  * the frozen identities (model sha, lookup version, fidelity spec freeze
    time) are read at boot and re-verified every cycle;
  * memory is bounded: a rolling window of L5_SHADOW_WINDOW_DAYS days of
    decision rows, hard-capped at L5_SHADOW_MAX_ROWS (oldest evicted, a
    note is written); the full canonical history is never loaded;
  * no credentials: only the public activity endpoint the collector
    already uses; --dry-run makes no network call at all (fixture from the
    first rows of the canonical sequence, read locally).

    python3 -m src.l5_shadow_loop                 # 24/7 (Railway)
    python3 -m src.l5_shadow_loop --once          # one cycle, then exit
    python3 -m src.l5_shadow_loop --dry-run       # fixture, no network
"""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import json
import os
import resource
import signal
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional

# --- read-only imports of the existing decision / research path -------------
from src.polymarket_l5 import config as l5cfg
from src.polymarket_l5 import ops
from src.polymarket_l5.action_model import (SIZE_BUCKETS, _bucket, _relation,
                                            action_key, state_key)
from src.polymarket_l5.realtime_mirror import WindowState, canonical_row
from src import pmus_l5_persistence_v2 as pv2
from src import l5_model_drift_check as drift
from src import l5_shadow_scoring as scoring
from src import l5_fidelity_gate as gate
from src import pmus_sports_l5_decision as live_decision

# The shadow normalizes with the very objects the live decision path uses.
assert live_decision.state_key is state_key, "live path state_key diverged"
assert live_decision.pv2 is pv2, "live path lookup module diverged"

LABELS = ("READ-ONLY 24/7 L5 shadow research; separate process; never "
          "writes the frozen model/lookup/spec; proposals require an "
          "explicit owner decision")

# --- frozen identities: read, verified, never written -----------------------
FROZEN_PATHS = tuple(sorted({
    pv2.ARTIFACT.resolve(),            # analysis/l5/l5_action_model_v2.json (lookup v3)
    gate.MODEL_PATH.resolve(),
    gate.SPEC_PATH.resolve(),          # analysis/l5/l5_fidelity_gate.json (frozen spec)
    drift.MODEL_PATH.resolve(),
    drift.OUT_PATH.resolve(),          # the scheduled drift artifact is not ours to write
    ops.ACTION_MODEL.resolve(),        # analysis/l5/l5_action_model.json (v1)
    ops.SEQ_CSV.resolve(),             # canonical history
    ops.SEQ_META.resolve(),
    ops.BEHAVIOR.resolve(),
}, key=str))
FROZEN_DIR = l5cfg.OUTPUT_DIR.resolve()          # analysis/l5/**
REPO_ROOT = l5cfg.REPO_ROOT.resolve()
EXPECTED_MODEL_SHA_DEFAULT = "bbb55bf90638d2bd"
CANONICAL_LOOKUP_VERSION = gate.CANONICAL_LOOKUP_VERSION
assert CANONICAL_LOOKUP_VERSION == pv2.CANONICAL_LOOKUP_VERSION

RELATIONS = ("open", "extend", "complete", "sell")
DAY = 86400.0


class FrozenPathViolation(RuntimeError):
    """Raised before any write that would touch a frozen artifact."""


# --- configuration (all env-tunable; documented in L5_SHADOW_ARCHITECTURE.md)
DEFAULTS: Dict[str, Any] = {
    "L5_SHADOW_STATE_DIR": "/data/l5_shadow",
    "L5_SHADOW_INTERVAL_S": 300,
    "L5_SHADOW_WINDOW_DAYS": 14,
    "L5_SHADOW_MAX_ROWS": 200000,
    "L5_SHADOW_MIN_DECISIONS": 200,      # evidence floor before behavioral alarms fire
    "L5_SHADOW_MIN_AGREEMENT": 0.55,     # rolling relation top-1 agreement floor
    "L5_SHADOW_STATE_SHIFT_L1": 0.40,    # L1(rolling base-state mix, frozen base mix)
    "L5_SHADOW_DRIFT_WEIGHTED_L1": drift.DRIFT_WEIGHTED_FLAG,   # 0.10
    "L5_SHADOW_OFF_DIST_MAX_PCT": 5.0,   # == frozen spec off_distribution_rate.max_pct
    "L5_SHADOW_STALE_S": 21600,          # no new L5 activity for 6h
    "L5_SHADOW_MAX_RSS_MB": 1500,
    "L5_SHADOW_HOLDOUT_DAYS": 3,
    "L5_SHADOW_PROPOSAL_MIN_GAP_S": 86400,
    "L5_SHADOW_EXPECTED_MODEL_SHA": EXPECTED_MODEL_SHA_DEFAULT,
    "L5_SHADOW_VENUE": "polymarket",
    "L5_SHADOW_WALLET": ops.L5_WALLET,
    "L5_SHADOW_COLLECT_FAILS_ALARM": 3,
}


def load_config(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    cfg: Dict[str, Any] = {}
    for k, d in DEFAULTS.items():
        v = env.get(k)
        if v is None:
            cfg[k] = d
        elif isinstance(d, bool):
            cfg[k] = v.strip().lower() in ("1", "true", "yes")
        elif isinstance(d, int):
            cfg[k] = int(float(v))
        elif isinstance(d, float):
            cfg[k] = float(v)
        else:
            cfg[k] = v
    return cfg


# --- small utilities ---------------------------------------------------------

def _utc(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts,
                                  tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts,
                                  tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")


def _sha16(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def rss_mb() -> Dict[str, float]:
    """Current RSS from /proc when available, peak from getrusage."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 1024.0 if sys.platform.startswith("linux") else peak / (1024.0 ** 2)
    cur_mb = peak_mb
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    cur_mb = float(line.split()[1]) / 1024.0
                    break
    except OSError:
        pass
    return {"rss_mb": round(cur_mb, 1), "rss_peak_mb": round(peak_mb, 1)}


def _frozen_identity() -> Dict[str, Any]:
    spec = gate.load_spec()
    return {
        "model_path": str(pv2.ARTIFACT), "model_sha256_16": _sha16(pv2.ARTIFACT),
        "lookup_version": CANONICAL_LOOKUP_VERSION,
        "spec_path": str(gate.SPEC_PATH), "spec_sha256_16": _sha16(gate.SPEC_PATH),
        "spec_frozen_utc": spec.get("frozen_utc"),
        "frozen_paths": [str(p) for p in FROZEN_PATHS],
    }


# --- the loop ----------------------------------------------------------------

class WindowCtx:
    """Sequential state of one settlement window (bounded, evicted after
    settlement): the mirror's WindowState plus what the next transition
    needs from the previous fill."""
    __slots__ = ("state", "prev", "prev_prev_residual", "window_end", "window_start")

    def __init__(self, window_start: int, window_end: int) -> None:
        self.state = WindowState()
        self.prev: Optional[Dict[str, Any]] = None
        self.prev_prev_residual = 0.0
        self.window_start = window_start
        self.window_end = window_end


class ShadowLoop:
    def __init__(self, cfg: Mapping[str, Any], state_dir: Path, *,
                 dry_run: bool = False,
                 now_fn: Optional[Callable[[], float]] = None,
                 model: Optional[Mapping[str, Any]] = None,
                 spec: Optional[Mapping[str, Any]] = None,
                 out: Callable[[str], None] = print) -> None:
        self.cfg = dict(cfg)
        self.state_dir = Path(state_dir).resolve()
        self.dry_run = dry_run
        self.now = now_fn or time.time
        self.out = out
        if self.state_dir == FROZEN_DIR or FROZEN_DIR in self.state_dir.parents:
            raise FrozenPathViolation(f"state dir {self.state_dir} is under {FROZEN_DIR}")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.model = dict(model) if model is not None else pv2.load_v2()
        self.spec = dict(spec) if spec is not None else gate.load_spec()
        self.model_sha = str(self.model.get("_sha256_16") or "")
        self.adapter = ops.ADAPTERS[self.cfg["L5_SHADOW_VENUE"]]
        self.identity_boot = _frozen_identity()
        # bounded memory: rolling decision rows + their fingerprints
        self.rows: Deque[Dict[str, Any]] = deque()
        self.fps: set = set()
        self.windows: Dict[str, WindowCtx] = {}
        self.file_offsets: Dict[str, int] = {}
        self.cycle = 0
        self.evicted_total = 0
        self.last_activity_ts: Optional[float] = None
        self.last_collect: Dict[str, Any] = {}
        self.collect_failures = 0
        self.active_alarms: Dict[str, float] = {}
        self.alarms_total = 0
        self.last_proposal_ts: Optional[float] = None
        self.last_proposal_path: Optional[str] = None
        self.stop = False
        self._load_loop_state()
        self._bind_collector_paths()

    # ---------------- isolation ----------------
    def _guard_write(self, path: Path) -> Path:
        p = Path(path).resolve()
        if p in FROZEN_PATHS or p == FROZEN_DIR or FROZEN_DIR in p.parents:
            raise FrozenPathViolation(f"refusing to write frozen path {p}")
        if self.state_dir not in p.parents:
            raise FrozenPathViolation(f"refusing to write outside state dir: {p}")
        return p

    def _write_json(self, path: Path, obj: Any) -> Path:
        p = self._guard_write(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1, default=str, sort_keys=False),
                       encoding="utf-8")
        tmp.replace(p)
        return p

    def _append_jsonl(self, path: Path, obj: Any) -> None:
        p = self._guard_write(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")

    def _bind_collector_paths(self) -> None:
        """The existing collector keeps module-level path constants that its
        functions read at call time; point them at OUR state dir so its
        cursor/day files/heartbeat live on this service's volume, isolated
        from the research service's own copy. No collector code changes."""
        ops_dir = self.state_dir / "ops"
        ops.OPS_DIR = ops_dir
        ops.FAIL_DIR = ops_dir / "failures"
        ops.STATE = ops_dir / "state.json"
        ops.HEARTBEAT = ops_dir / "heartbeat.json"
        ops_dir.mkdir(parents=True, exist_ok=True)

    def verify_frozen(self) -> Dict[str, Any]:
        now_id = _frozen_identity()
        expected = self.cfg["L5_SHADOW_EXPECTED_MODEL_SHA"]
        ok = (now_id["model_sha256_16"] == self.identity_boot["model_sha256_16"]
              == self.model_sha
              and now_id["spec_sha256_16"] == self.identity_boot["spec_sha256_16"]
              and (not expected or self.model_sha == expected))
        return {"unchanged_since_boot": ok, "model_sha256_16": self.model_sha,
                "expected_model_sha": expected,
                "lookup_version": CANONICAL_LOOKUP_VERSION,
                "spec_frozen_utc": now_id["spec_frozen_utc"]}

    # ---------------- durable loop state ----------------
    @property
    def loop_state_path(self) -> Path:
        return self.state_dir / "loop_state.json"

    def _load_loop_state(self) -> None:
        try:
            st = json.loads(self.loop_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.cycle = int(st.get("cycles") or 0)
        self.evicted_total = int(st.get("evicted_total") or 0)
        self.alarms_total = int(st.get("alarms_total") or 0)
        self.last_proposal_ts = st.get("last_proposal_ts")
        self.last_proposal_path = st.get("last_proposal_path")
        self.last_activity_ts = st.get("last_activity_ts")

    def _save_loop_state(self) -> None:
        self._write_json(self.loop_state_path, {
            "utc": _utc(self.now()), "cycles": self.cycle,
            "evicted_total": self.evicted_total, "alarms_total": self.alarms_total,
            "last_proposal_ts": self.last_proposal_ts,
            "last_proposal_path": self.last_proposal_path,
            "last_activity_ts": self.last_activity_ts,
            "labels": LABELS})

    # ---------------- 1. ingest ----------------
    def collect(self) -> Dict[str, Any]:
        """Call the EXISTING collector (never re-implemented). Dry-run never
        reaches here."""
        if self.dry_run:
            return {"skipped": "dry_run"}
        try:
            rep = ops.run_collect(self.cfg["L5_SHADOW_VENUE"], self.cfg["L5_SHADOW_WALLET"])
            self.collect_failures = 0
            return rep
        except Exception as exc:                        # noqa: BLE001
            self.collect_failures += 1
            self._write_json(self.state_dir / "failures" /
                             f"collect_{_stamp(self.now())}.json",
                             {"utc": _utc(self.now()), "error": repr(exc),
                              "traceback": traceback.format_exc()[-4000:]})
            return {"error": repr(exc), "consecutive_failures": self.collect_failures}

    def _window_floor(self) -> float:
        return self.now() - self.cfg["L5_SHADOW_WINDOW_DAYS"] * DAY

    def stream_new_rows(self) -> List[Dict[str, Any]]:
        """Stream ONLY unread bytes of the day files inside the rolling
        window (append-only files, per-file byte offsets); never the whole
        history."""
        floor = self._window_floor()
        out: List[Dict[str, Any]] = []
        for path in sorted(ops.OPS_DIR.glob("activity_*.jsonl")):
            try:
                day_ts = datetime.strptime(path.stem.split("_")[1], "%Y%m%d") \
                    .replace(tzinfo=timezone.utc).timestamp()
            except (IndexError, ValueError):
                continue
            if day_ts + DAY < floor:
                continue
            key = str(path)
            off = self.file_offsets.get(key, 0)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < off:                     # rotated/truncated: reread
                off = 0
            if size == off:
                continue
            with path.open("r", encoding="utf-8") as fh:
                fh.seek(off)
                for line in fh:
                    if not line.endswith("\n"):   # partial write in flight
                        break
                    off += len(line.encode("utf-8"))
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("type") != "TRADE":
                        continue
                    ts = float(r.get("timestamp") or 0)
                    if ts < floor:
                        continue
                    fp = r.get("_fp") or ops._fingerprint(r)
                    if fp in self.fps:
                        continue
                    r["_fp"] = fp
                    out.append(r)
            self.file_offsets[key] = off
        out.sort(key=lambda r: (float(r.get("timestamp") or 0), r.get("_fp") or ""))
        return out

    # ---------------- 2. normalize + 3. compare ----------------
    def decision_for(self, prev: Mapping[str, Any], prev_prev_residual: float,
                     nxt: Mapping[str, Any], ctx_start: int) -> Dict[str, Any]:
        """One (state-before, L5 action) transition scored against the
        frozen lookup with the live path's own functions."""
        resid = float(prev["residual_qty_signed"])
        base = state_key(float(prev["price"]), float(nxt["seconds_to_settlement"]), resid)
        rel = _relation(resid, nxt["outcome"], nxt["side"])
        action = action_key(rel, float(nxt["quantity"]))
        last_rel = _relation(prev_prev_residual, prev["outcome"], prev["side"])
        last_size = _bucket(float(prev["quantity"]), SIZE_BUCKETS)
        d = pv2.v2_dist(self.model, base, last_rel, last_size)
        rels = pv2.relation_mass(d["dist"])
        ranked = sorted(rels.items(), key=lambda kv: (-kv[1], kv[0]))
        modal = max(d["dist"], key=d["dist"].get) if d["dist"] else None
        return {
            "ts": int(nxt["timestamp"]), "day": _day(float(nxt["timestamp"])),
            "t0": ctx_start, "window": nxt["window_id"], "fp": nxt.get("_fp"),
            "base": base, "full_key": d["state"], "last_rel": last_rel,
            "last_size": last_size, "residual_units": resid,
            "action": action, "rel": rel,
            "rec_rel": ranked[0][0], "modal": modal,
            "p_action": round(float(d["dist"].get(action, 0.0)), 6),
            "p_rel": round(float(rels.get(rel, 0.0)), 6),
            "p_complete": round(float(rels.get("complete", 0.0)), 6),
            "p_extend": round(float(rels.get("extend", 0.0)), 6),
            "off": bool(d["backed_off"]), "state_n": int(d["n"]),
            "lookup_version": CANONICAL_LOOKUP_VERSION, "model_sha": self.model_sha,
        }

    def absorb(self, raw_rows: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
        """Normalize raw activity into sequential per-window state and score
        every transition. Bounded: rows deque + window contexts only."""
        fills = decisions = skipped = 0
        for r in raw_rows:
            win = self.adapter.parse_window(r)
            if win is None:
                skipped += 1
                continue
            win_d = {"asset": win.asset, "duration_minutes": win.duration_minutes,
                     "window_start": win.window_start, "window_end": win.window_end,
                     "slug": win.window_id}
            ctx = self.windows.get(win.window_id)
            if ctx is None:
                ctx = self.windows[win.window_id] = WindowCtx(win.window_start, win.window_end)
            row = canonical_row(dict(r), win_d, ctx.state, self.adapter)
            if row is None:
                skipped += 1
                continue
            row["_fp"] = r.get("_fp")
            fills += 1
            ts = float(row["timestamp"])
            self.last_activity_ts = max(self.last_activity_ts or 0.0, ts)
            if ctx.prev is not None:
                dec = self.decision_for(ctx.prev, ctx.prev_prev_residual, row, ctx.window_start)
                self.rows.append(dec)
                decisions += 1
            if row.get("_fp"):
                self.fps.add(row["_fp"])
            ctx.prev_prev_residual = float(ctx.prev["residual_qty_signed"]) if ctx.prev else 0.0
            ctx.prev = {k: row[k] for k in ("residual_qty_signed", "price", "outcome",
                                             "side", "quantity", "timestamp")}
        evicted = self.enforce_bounds()
        return {"fills": fills, "decisions": decisions, "skipped": skipped,
                "evicted": evicted}

    def enforce_bounds(self) -> int:
        """Rolling time window, then the hard row cap (oldest first). Window
        contexts are dropped once their settlement is an hour behind."""
        now = self.now()
        floor = self._window_floor()
        dropped_time = 0
        while self.rows and self.rows[0]["ts"] < floor:
            self._drop_left()
            dropped_time += 1
        cap = int(self.cfg["L5_SHADOW_MAX_ROWS"])
        dropped_cap = 0
        while len(self.rows) > cap:
            self._drop_left()
            dropped_cap += 1
        if dropped_cap:
            self.evicted_total += dropped_cap
            self._append_jsonl(self.state_dir / "notes.jsonl", {
                "utc": _utc(now), "kind": "memory_cap_eviction",
                "evicted_rows": dropped_cap, "cap": cap,
                "rows_in_memory": len(self.rows), "evicted_total": self.evicted_total,
                "note": "oldest decision rows dropped; the durable day files keep them"})
        for slug in [s for s, c in self.windows.items() if now > c.window_end + 3600]:
            del self.windows[slug]
        # fingerprints track rows; a window's own fills are bounded by the
        # window contexts, so cap the fp set to the rows we still hold
        if len(self.fps) > cap * 2:
            self.fps = {d["fp"] for d in self.rows if d.get("fp")}
        return dropped_time + dropped_cap

    def _drop_left(self) -> None:
        d = self.rows.popleft()
        if d.get("fp"):
            self.fps.discard(d["fp"])

    # ---------------- 4. drift metrics ----------------
    def metrics(self) -> Dict[str, Any]:
        rows = list(self.rows)
        n = len(rows)
        per_state: Dict[str, Dict[str, Any]] = {}
        agree_rel = agree_exact = off = 0
        p_sum = 0.0
        obs_rel: Counter = Counter()
        rec_rel: Counter = Counter()
        state_mix: Counter = Counter()
        for d in rows:
            e = per_state.setdefault(d["base"], {
                "n": 0, "agree": 0, "diverge": 0, "off": 0, "p_action_sum": 0.0,
                "observed": {r: 0 for r in RELATIONS},
                "recommended": {r: 0 for r in RELATIONS}})
            e["n"] += 1
            state_mix[d["base"]] += 1
            same = d["rel"] == d["rec_rel"]
            e["agree" if same else "diverge"] += 1
            e["off"] += int(d["off"])
            e["p_action_sum"] += d["p_action"]
            e["observed"][d["rel"]] = e["observed"].get(d["rel"], 0) + 1
            e["recommended"][d["rec_rel"]] = e["recommended"].get(d["rec_rel"], 0) + 1
            agree_rel += same
            agree_exact += d["action"] == d["modal"]
            off += int(d["off"])
            p_sum += d["p_action"]
            obs_rel[d["rel"]] += 1
            rec_rel[d["rec_rel"]] += 1
        for e in per_state.values():
            e["agreement"] = round(e["agree"] / e["n"], 4)
            e["mean_p_action"] = round(e.pop("p_action_sum") / e["n"], 4)
        # base-state mix: rolling window vs the frozen model's base counts
        frozen_base = {k: sum(v.values()) for k, v in (self.model.get("base") or {}).items()}
        fb_tot = float(sum(frozen_base.values()) or 1)
        mix_l1 = None
        if n:
            keys = set(state_mix) | set(frozen_base)
            mix_l1 = round(sum(abs(state_mix.get(k, 0) / n - frozen_base.get(k, 0) / fb_tot)
                               for k in keys), 4)
        floor = self._window_floor()
        drift_rep = drift.compare(
            self.model, [{"t0": d["t0"], "base": d["base"], "action": d["action"]} for d in rows],
            since_utc=_utc(floor))
        decs = [{"p_complete": d["p_complete"], "p_extend": d["p_extend"],
                 "sampled": d["rel"], "acted": d["rel"],
                 "residual_units": d["residual_units"], "off_distribution": d["off"]}
                for d in rows if d["rel"] != "sell"]
        behavioral = scoring.action_scoring([{"decisions": decs}]) if decs else {"scored_decisions": 0}
        spec_view = gate.score_pmus_shadow(
            {"behavioral_layer": behavioral,
             "lookup_provenance": {"lookup_version_gate": CANONICAL_LOOKUP_VERSION,
                                   "model_sha": self.model_sha},
             "markets_with_decisions": len({d["window"] for d in rows})}, self.spec)
        top = sorted(per_state.items(), key=lambda kv: -kv[1]["n"])[:20]
        return {
            "rows": n, "windows_in_memory": len(self.windows),
            "windows_scored": len({d["window"] for d in rows}),
            "window_floor_utc": _utc(floor),
            "relation_agreement": round(agree_rel / n, 4) if n else None,
            "exact_agreement": round(agree_exact / n, 4) if n else None,
            "mean_prob_of_observed_action": round(p_sum / n, 4) if n else None,
            "off_distribution_pct": round(100.0 * off / n, 2) if n else None,
            "observed_relation_freq": {r: round(obs_rel[r] / n, 4) for r in RELATIONS} if n else None,
            "recommended_relation_freq": {r: round(rec_rel[r] / n, 4) for r in RELATIONS} if n else None,
            "state_mix_l1_vs_frozen": mix_l1,
            "states_in_window": len(per_state),
            "drift": {k: drift_rep.get(k) for k in
                      ("weighted_l1", "states_scored", "states_new_unseen_by_model",
                       "verdict", "new_transitions")},
            "drift_flagged_states": drift_rep.get("flagged_states"),
            "behavioral": {k: behavioral.get(k) for k in
                           ("scored_decisions", "l1_sampled_vs_l5", "post_fill",
                            "off_distribution_rate_pct")},
            "frozen_spec_view": {k: spec_view.get(k) for k in
                                 ("status", "decisions", "action_distribution_relation",
                                  "post_fill_complete_gap", "off_distribution_rate")},
            "top_states": [{"state": s, **e} for s, e in top],
            "_per_state": per_state,
        }

    def alarms_for(self, m: Mapping[str, Any], frozen: Mapping[str, Any],
                   mem: Mapping[str, Any]) -> List[Dict[str, Any]]:
        cfg = self.cfg
        n = int(m.get("rows") or 0)
        enough = n >= int(cfg["L5_SHADOW_MIN_DECISIONS"])
        out: List[Dict[str, Any]] = []

        def add(kind: str, **fields: Any) -> None:
            out.append({"kind": kind, **fields})
        if enough and m.get("relation_agreement") is not None \
                and m["relation_agreement"] < float(cfg["L5_SHADOW_MIN_AGREEMENT"]):
            add("AGREEMENT_BELOW_FLOOR", value=m["relation_agreement"],
                threshold=cfg["L5_SHADOW_MIN_AGREEMENT"], n=n)
        if enough and m.get("state_mix_l1_vs_frozen") is not None \
                and m["state_mix_l1_vs_frozen"] > float(cfg["L5_SHADOW_STATE_SHIFT_L1"]):
            add("STATE_MIX_SHIFT", value=m["state_mix_l1_vs_frozen"],
                threshold=cfg["L5_SHADOW_STATE_SHIFT_L1"], n=n)
        dw = (m.get("drift") or {}).get("weighted_l1")
        if dw is not None and dw >= float(cfg["L5_SHADOW_DRIFT_WEIGHTED_L1"]):
            add("RELATION_DRIFT", value=dw, threshold=cfg["L5_SHADOW_DRIFT_WEIGHTED_L1"],
                verdict=(m.get("drift") or {}).get("verdict"), n=n)
        if enough and m.get("off_distribution_pct") is not None \
                and m["off_distribution_pct"] > float(cfg["L5_SHADOW_OFF_DIST_MAX_PCT"]):
            add("OFF_DISTRIBUTION_HIGH", value=m["off_distribution_pct"],
                threshold=cfg["L5_SHADOW_OFF_DIST_MAX_PCT"], n=n)
        if (not self.dry_run and self.last_activity_ts
                and self.now() - self.last_activity_ts > float(cfg["L5_SHADOW_STALE_S"])):
            add("ACTIVITY_STALE", last_activity_utc=_utc(self.last_activity_ts),
                stale_s=round(self.now() - self.last_activity_ts), threshold=cfg["L5_SHADOW_STALE_S"])
        if mem.get("rss_mb", 0) > float(cfg["L5_SHADOW_MAX_RSS_MB"]):
            add("RSS_HIGH", value=mem["rss_mb"], threshold=cfg["L5_SHADOW_MAX_RSS_MB"])
        if not frozen.get("unchanged_since_boot"):
            add("FROZEN_IDENTITY_MISMATCH", **{k: frozen.get(k) for k in
                                              ("model_sha256_16", "expected_model_sha")})
        if self.collect_failures >= int(cfg["L5_SHADOW_COLLECT_FAILS_ALARM"]):
            add("COLLECTOR_FAILING", consecutive_failures=self.collect_failures)
        return out

    def emit_alarms(self, alarms: List[Dict[str, Any]]) -> None:
        now = self.now()
        kinds = {a["kind"] for a in alarms}
        for a in alarms:
            rising = a["kind"] not in self.active_alarms
            since = self.active_alarms.setdefault(a["kind"], now)
            payload = {"utc": _utc(now), "since_utc": _utc(since), "cycle": self.cycle,
                       "rising_edge": rising, "model_sha": self.model_sha,
                       "lookup_version": CANONICAL_LOOKUP_VERSION, **a}
            self.out("L5_SHADOW_ALARM " + json.dumps(payload, default=str))
            if rising:
                self.alarms_total += 1
                self._write_json(self.state_dir / "alarms" /
                                 f"alarm_{_stamp(now)}_{a['kind']}.json", payload)
            self._append_jsonl(self.state_dir / "alarms" / "alarms.jsonl", payload)
        for k in [k for k in self.active_alarms if k not in kinds]:
            self.out("L5_SHADOW_ALARM_CLEARED " + json.dumps(
                {"utc": _utc(now), "kind": k, "since_utc": _utc(self.active_alarms[k])}))
            del self.active_alarms[k]

    # ---------------- 5. proposal ----------------
    def _holdout_cut(self) -> Dict[str, Any]:
        rows = list(self.rows)
        if not rows:
            return {"cut_ts": None, "rule": "no_rows"}
        cut = self.now() - float(self.cfg["L5_SHADOW_HOLDOUT_DAYS"]) * DAY
        n_hold = sum(1 for d in rows if d["ts"] >= cut)
        least = max(1, len(rows) // 10)        # both sides need >= 10% of the rows
        if least <= n_hold <= len(rows) - least:
            return {"cut_ts": cut, "rule": f"last_{self.cfg['L5_SHADOW_HOLDOUT_DAYS']}_days"}
        ts_sorted = sorted(d["ts"] for d in rows)
        cut = ts_sorted[int(len(ts_sorted) * 0.7)]
        return {"cut_ts": cut, "rule": "time_quantile_0.7 (window too short/lopsided "
                                       "for the day rule)"}

    @staticmethod
    def _score(model: Mapping[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rows)
        if not n:
            return {"n": 0}
        p_sum = 0.0
        agree = exact = off = 0
        decs = []
        for d in rows:
            dd = pv2.v2_dist(model, d["base"], d["last_rel"], d["last_size"])
            rels = pv2.relation_mass(dd["dist"])
            rec = sorted(rels.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            modal = max(dd["dist"], key=dd["dist"].get)
            p_sum += dd["dist"].get(d["action"], 0.0)
            agree += rec == d["rel"]
            exact += modal == d["action"]
            off += int(dd["backed_off"])
            if d["rel"] != "sell":
                decs.append({"p_complete": rels.get("complete", 0.0),
                             "p_extend": rels.get("extend", 0.0),
                             "sampled": d["rel"], "acted": d["rel"],
                             "residual_units": d["residual_units"],
                             "off_distribution": dd["backed_off"]})
        return {"n": n, "mean_prob_of_observed_action": round(p_sum / n, 4),
                "relation_agreement": round(agree / n, 4),
                "exact_agreement": round(exact / n, 4),
                "off_distribution_pct": round(100.0 * off / n, 2),
                "behavioral": scoring.action_scoring([{"decisions": decs}]) if decs
                else {"scored_decisions": 0}}

    def build_candidate(self, train: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Frozen counts + the rolling window's new transitions, the SAME
        recipe as the frozen artifact (counts on base|last_rel|last_size,
        shrinkage at lookup time). Written only under the state dir."""
        cand = {"full": copy.deepcopy(self.model.get("full") or {}),
                "base": copy.deepcopy(self.model.get("base") or {}),
                "marginal": dict(self.model.get("marginal") or {}),
                "cadence_modal": dict(self.model.get("cadence_modal") or {})}
        deltas: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for d in train:
            cand["full"].setdefault(d["full_key"], {})
            cand["full"][d["full_key"]][d["action"]] = cand["full"][d["full_key"]].get(d["action"], 0) + 1
            cand["base"].setdefault(d["base"], {})
            cand["base"][d["base"]][d["action"]] = cand["base"][d["base"]].get(d["action"], 0) + 1
            cand["marginal"][d["action"]] = cand["marginal"].get(d["action"], 0) + 1
            deltas[d["full_key"]][d["action"]] += 1
        cand.update({
            "generated_utc": _utc(self.now()),
            "formulation": self.model.get("formulation"),
            "n_transitions": int(self.model.get("n_transitions") or 0) + len(train),
            "n_full_states": len(cand["full"]),
            "lookup_version_candidate": CANONICAL_LOOKUP_VERSION + 1,
            "derived_from_model_sha": self.model_sha,
            "labels": "CANDIDATE lookup; NOT the live model; requires owner promotion",
        })
        delta_rows = []
        for key, acts in deltas.items():
            frozen_c = self.model.get("full", {}).get(key) or {}
            cand_c = cand["full"][key]
            fm = max(frozen_c, key=frozen_c.get) if frozen_c else None
            cm = max(cand_c, key=cand_c.get)
            delta_rows.append({"full_key": key, "added": dict(acts),
                               "frozen_n": sum(frozen_c.values()), "candidate_n": sum(cand_c.values()),
                               "frozen_modal": fm, "candidate_modal": cm,
                               "modal_changed": fm != cm, "state_new": not frozen_c})
        delta_rows.sort(key=lambda r: -sum(r["added"].values()))
        return {"candidate": cand, "deltas": delta_rows}

    def build_proposal(self, m: Mapping[str, Any], reason: str) -> Optional[Path]:
        cut = self._holdout_cut()
        if cut["cut_ts"] is None:
            return None
        rows = list(self.rows)
        train = [d for d in rows if d["ts"] < cut["cut_ts"]]
        hold = [d for d in rows if d["ts"] >= cut["cut_ts"]]
        built = self.build_candidate(train)
        cand = built["candidate"]
        frozen_s = self._score(self.model, hold)
        cand_s = self._score(cand, hold)
        prov = {"lookup_version_gate": CANONICAL_LOOKUP_VERSION}
        gate_frozen = gate.score_pmus_shadow(
            {"behavioral_layer": frozen_s.get("behavioral"), "lookup_provenance": prov}, self.spec)
        gate_cand = gate.score_pmus_shadow(
            {"behavioral_layer": cand_s.get("behavioral"), "lookup_provenance": prov}, self.spec)
        dims = ("action_distribution_relation", "post_fill_complete_gap", "off_distribution_rate")

        def fails(g: Mapping[str, Any]) -> List[str]:
            return [k for k in dims if (g.get(k) or {}).get("status") == "FAIL"]
        # evidence floor: the frozen spec's own minimum scored decisions on
        # the holdout, and the loop's decision floor on the train side
        min_hold = int((self.spec.get("minimum_evidence") or {}).get("live_scored_decisions") or 0)
        min_train = int(self.cfg["L5_SHADOW_MIN_DECISIONS"])
        insufficient = len(hold) < max(1, min_hold) or len(train) < max(1, min_train)
        meets = (not insufficient
                 and cand_s["mean_prob_of_observed_action"] >= frozen_s["mean_prob_of_observed_action"]
                 and cand_s["off_distribution_pct"] <= frozen_s["off_distribution_pct"]
                 and not fails(gate_cand))
        verdict = ("INSUFFICIENT_EVIDENCE" if insufficient else
                   "CANDIDATE_VALIDATED_OOS" if meets else "CANDIDATE_NOT_BETTER")
        stamp = _stamp(self.now())
        version = CANONICAL_LOOKUP_VERSION + 1
        cand_path = self._write_json(
            self.state_dir / "proposals" / f"lookup_v{version}_candidate_{stamp}.json", cand)
        cand_sha = _sha16(cand_path)
        modal_changes = sum(1 for r in built["deltas"] if r["modal_changed"])
        proposal = {
            "kind": "l5_lookup_update_proposal",
            "utc": _utc(self.now()), "reason": reason,
            "promotion": "REQUIRES_OWNER_DECISION",
            "promotion_path": ("owner reviews this file; if adopted, a versioned commit adds "
                               f"analysis/l5/l5_action_model_v2.v{version}.json (the candidate "
                               "artifact, sha recorded), bumps CANONICAL_LOOKUP_VERSION in "
                               "src/l5_fidelity_gate.py and src/pmus_l5_persistence_v2.py, and "
                               "the probe redeploys on that reviewed frozen-file change only; "
                               "this process never performs any of those steps"),
            "frozen_identity": self.identity_boot,
            "frozen_paths_never_written": [str(p) for p in FROZEN_PATHS],
            "proposed_lookup_version": version,
            "candidate_artifact": {"path": str(cand_path), "sha256_16": cand_sha,
                                   "n_transitions": cand["n_transitions"],
                                   "n_full_states": cand["n_full_states"]},
            "evidence": {
                "window_floor_utc": m.get("window_floor_utc"),
                "rows_in_window": len(rows), "train_transitions": len(train),
                "holdout_transitions": len(hold), "holdout_rule": cut["rule"],
                "holdout_cut_utc": _utc(cut["cut_ts"]),
                "states_touched": len(built["deltas"]),
                "states_new_to_model": sum(1 for r in built["deltas"] if r["state_new"]),
                "modal_action_changes": modal_changes,
                "rolling_metrics": {k: m.get(k) for k in
                                    ("relation_agreement", "exact_agreement",
                                     "mean_prob_of_observed_action", "off_distribution_pct",
                                     "state_mix_l1_vs_frozen", "drift")},
                "drift_flagged_states": m.get("drift_flagged_states"),
            },
            "lookup_deltas": built["deltas"][:500],
            "lookup_deltas_truncated": max(0, len(built["deltas"]) - 500),
            "validation": {
                "spec_path": str(gate.SPEC_PATH),
                "spec_frozen_utc": self.spec.get("frozen_utc"),
                "spec_no_tuning_rule": self.spec.get("no_tuning_rule"),
                "frozen_on_holdout": {**{k: v for k, v in frozen_s.items() if k != "behavioral"},
                                      "frozen_spec_dimensions": {k: gate_frozen.get(k) for k in dims},
                                      "failing": fails(gate_frozen)},
                "candidate_on_holdout": {**{k: v for k, v in cand_s.items() if k != "behavioral"},
                                         "frozen_spec_dimensions": {k: gate_cand.get(k) for k in dims},
                                         "failing": fails(gate_cand)},
                "bar": ("candidate mean prob of observed action >= frozen AND off-distribution "
                        "pct <= frozen AND no FAIL dimension under the frozen fidelity spec "
                        "(L5_EVOLUTION_LOOP step 5)"),
                "evidence_minimums": {"holdout_min_scored_decisions": min_hold,
                                      "train_min_decisions": min_train,
                                      "source": "frozen spec minimum_evidence.live_scored_decisions "
                                                "+ L5_SHADOW_MIN_DECISIONS"},
                "candidate_meets_bar": bool(meets),
                "verdict": verdict,
            },
            "labels": LABELS,
        }
        path = self._write_json(
            self.state_dir / "proposals" / f"lookup_v{version}_proposal_{stamp}.json", proposal)
        self.last_proposal_ts = self.now()
        self.last_proposal_path = str(path)
        self.out("L5_SHADOW_PROPOSAL " + json.dumps(
            {"utc": proposal["utc"], "path": str(path), "verdict": verdict,
             "promotion": "REQUIRES_OWNER_DECISION", "proposed_lookup_version": version,
             "train": len(train), "holdout": len(hold)}))
        self.out("L5_SHADOW_PROPOSAL_SUMMARY " + json.dumps(
            self.proposal_summary(proposal, built["deltas"], train, hold, frozen_s, cand_s),
            default=str))
        return path

    def proposal_summary(self, proposal: Mapping[str, Any], deltas: List[Dict[str, Any]],
                         train: List[Dict[str, Any]], hold: List[Dict[str, Any]],
                         frozen_s: Mapping[str, Any], cand_s: Mapping[str, Any]) -> Dict[str, Any]:
        """One owner-readable stdout summary of a written proposal (OWNER
        5577286614 item 4). Built only from objects the proposal already
        holds -- no full-history object, the top-10 deltas carry <= 16
        action probabilities each."""
        def _dist(counts: Mapping[str, int]) -> Dict[str, float]:
            tot = float(sum(counts.values()) or 1)
            return {a: round(c / tot, 4) for a, c in sorted(counts.items(), key=lambda kv: -kv[1])}

        def _span(rows: List[Dict[str, Any]]) -> Optional[List[str]]:
            if not rows:
                return None
            lo = hi = rows[0]["ts"]
            for d in rows:
                lo, hi = min(lo, d["ts"]), max(hi, d["ts"])
            return [_utc(lo), _utc(hi)]

        def _view(s: Mapping[str, Any]) -> Dict[str, Any]:
            beh = s.get("behavioral") or {}
            return {"relation_agreement": s.get("relation_agreement"),
                    "exact_agreement": s.get("exact_agreement"),
                    "mean_prob_of_observed_action": s.get("mean_prob_of_observed_action"),
                    "relation_l1_observed_vs_expected": beh.get("l1_sampled_vs_l5"),
                    "post_fill_complete_gap_pp": (beh.get("post_fill") or {}).get("complete_freq_gap_pp_sampled"),
                    "off_distribution_pct": s.get("off_distribution_pct")}
        top = []
        for r in deltas[:10]:
            key = r["full_key"]
            frozen_c = (self.model.get("full") or {}).get(key) or {}
            cand_c = dict(frozen_c)
            for a, c in r["added"].items():
                cand_c[a] = cand_c.get(a, 0) + c
            top.append({"key": key, "n": {"frozen": r["frozen_n"], "candidate": r["candidate_n"],
                                          "added": sum(r["added"].values())},
                        "frozen_dist": _dist(frozen_c), "candidate_dist": _dist(cand_c),
                        "frozen_relation": pv2.relation_mass(_dist(frozen_c)) if frozen_c else {},
                        "candidate_relation": pv2.relation_mass(_dist(cand_c)),
                        "modal_changed": r["modal_changed"], "state_new": r["state_new"]})
        ev = proposal.get("evidence") or {}
        val = proposal.get("validation") or {}
        return {
            "utc": proposal.get("utc"), "proposed_lookup_version": proposal.get("proposed_lookup_version"),
            "verdict": val.get("verdict"), "promotion": "REQUIRES_OWNER_DECISION",
            "frozen_model_sha": self.model_sha, "lookup_version": CANONICAL_LOOKUP_VERSION,
            "candidate_sha": (proposal.get("candidate_artifact") or {}).get("sha256_16"),
            "train": len(train), "holdout": len(hold),
            "train_span_utc": _span(train), "holdout_span_utc": _span(hold),
            "holdout_cut_utc": ev.get("holdout_cut_utc"), "holdout_rule": ev.get("holdout_rule"),
            "window_floor_utc": ev.get("window_floor_utc"),
            "frozen_on_holdout": _view(frozen_s), "candidate_on_holdout": _view(cand_s),
            "states_touched": ev.get("states_touched"), "modal_action_changes": ev.get("modal_action_changes"),
            "top_lookup_key_deltas": top,
            "path": self.last_proposal_path,
        }

    def maybe_propose(self, m: Mapping[str, Any], alarms: List[Dict[str, Any]],
                      force: bool = False) -> Optional[Path]:
        kinds = {a["kind"] for a in alarms}
        trig = [k for k in ("RELATION_DRIFT", "AGREEMENT_BELOW_FLOOR", "STATE_MIX_SHIFT")
                if k in kinds]
        if not (force or trig):
            return None
        if (not force and self.last_proposal_ts
                and self.now() - self.last_proposal_ts < float(self.cfg["L5_SHADOW_PROPOSAL_MIN_GAP_S"])):
            return None
        return self.build_proposal(m, reason="forced" if force and not trig else ",".join(trig))

    # ---------------- 6. heartbeat + cycle ----------------
    def run_cycle(self, force_proposal: bool = False) -> Dict[str, Any]:
        t_start = time.monotonic()
        self.cycle += 1
        collect = self.collect()
        self.last_collect = collect
        new_raw = self.stream_new_rows()
        ingest = self.absorb(new_raw)
        m = self.metrics()
        frozen = self.verify_frozen()
        mem = rss_mb()
        alarms = self.alarms_for(m, frozen, mem)
        self.emit_alarms(alarms)
        proposal = self.maybe_propose(m, alarms, force=force_proposal)
        latency_ms = round((time.monotonic() - t_start) * 1000.0, 1)
        per_state = m.pop("_per_state")
        self._write_json(self.state_dir / "tally_latest.json", {
            "utc": _utc(self.now()), "cycle": self.cycle, "rows": m["rows"],
            "per_state": per_state, "lookup_version": CANONICAL_LOOKUP_VERSION,
            "model_sha": self.model_sha})
        hb = {
            "utc": _utc(self.now()), "cycle": self.cycle, "status": "ok",
            "dry_run": self.dry_run, "read_only": True, "labels": LABELS,
            **mem, "rows_in_memory": m["rows"], "row_cap": self.cfg["L5_SHADOW_MAX_ROWS"],
            "windows_in_memory": m["windows_in_memory"], "evicted_total": self.evicted_total,
            "last_activity_ts": self.last_activity_ts,
            "last_activity_utc": _utc(self.last_activity_ts) if self.last_activity_ts else None,
            "cycle_latency_ms": latency_ms, "ingest": ingest, "collect": collect,
            "frozen": frozen, "metrics": m,
            "alarms_active": sorted(self.active_alarms), "alarms_total": self.alarms_total,
            "last_proposal_path": self.last_proposal_path,
            "proposal_written_this_cycle": str(proposal) if proposal else None,
            "thresholds": {k: self.cfg[k] for k in self.cfg if k.startswith("L5_SHADOW_")
                           and k not in ("L5_SHADOW_WALLET", "L5_SHADOW_STATE_DIR")},
        }
        self._write_json(self.state_dir / "heartbeat.json", hb)
        self._append_jsonl(self.state_dir / "heartbeat_history.jsonl", {
            k: hb[k] for k in ("utc", "cycle", "rss_mb", "rows_in_memory", "cycle_latency_ms",
                               "last_activity_utc", "alarms_active")})
        self._save_loop_state()
        self.out("L5_SHADOW_STATUS " + json.dumps({
            "utc": hb["utc"], "cycle": self.cycle, "rss_mb": mem["rss_mb"],
            "rows": m["rows"], "new_fills": ingest["fills"], "new_decisions": ingest["decisions"],
            "agreement": m["relation_agreement"], "mean_p": m["mean_prob_of_observed_action"],
            "off_pct": m["off_distribution_pct"], "drift_l1": m["drift"]["weighted_l1"],
            "drift": m["drift"]["verdict"], "state_mix_l1": m["state_mix_l1_vs_frozen"],
            "alarms": sorted(self.active_alarms), "latency_ms": latency_ms,
            "last_activity": hb["last_activity_utc"]}, default=str))
        return hb

    def run(self, once: bool = False, force_proposal: bool = False) -> int:
        interval = float(self.cfg["L5_SHADOW_INTERVAL_S"])

        def _stop(signum, _frame):        # noqa: ANN001
            self.out(f"L5_SHADOW_STOP signal={signum}")
            self.stop = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):    # not the main thread
                pass
        self.out("L5_SHADOW_BOOT " + json.dumps({
            "utc": _utc(self.now()), "state_dir": str(self.state_dir), "dry_run": self.dry_run,
            "interval_s": interval, "window_days": self.cfg["L5_SHADOW_WINDOW_DAYS"],
            "max_rows": self.cfg["L5_SHADOW_MAX_ROWS"], "model_sha": self.model_sha,
            "lookup_version": CANONICAL_LOOKUP_VERSION,
            "spec_frozen_utc": self.spec.get("frozen_utc"), "labels": LABELS}))
        while not self.stop:
            try:
                self.run_cycle(force_proposal=force_proposal)
            except FrozenPathViolation:
                raise
            except Exception as exc:                  # noqa: BLE001
                self._write_json(self.state_dir / "failures" / f"cycle_{_stamp(self.now())}.json",
                                 {"utc": _utc(self.now()), "cycle": self.cycle, "error": repr(exc),
                                  "traceback": traceback.format_exc()[-4000:]})
                self.out("L5_SHADOW_CYCLE_ERROR " + json.dumps({"cycle": self.cycle, "error": repr(exc)}))
                if once:
                    return 1
            force_proposal = False
            if once:
                return 0
            deadline = time.monotonic() + interval
            while not self.stop and time.monotonic() < deadline:
                self._progress_tick()
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return 0

    # -- sports event-progress observation, riding this process's spare time --
    #
    # A separate concern from L5 drift and kept separate in code: it writes
    # its own tape, keeps its own cadence and cannot raise into the L5 cycle.
    # It lives here rather than in the probe because a gateway read per
    # decision would put latency on the live order path, and rather than in a
    # new service because this one is already the isolated shadow process.
    def _progress_tick(self) -> None:
        if self.dry_run:
            return                      # --dry-run makes no network call
        obs = getattr(self, "_progress_obs", None)
        if obs is None:
            try:
                from src import pmus_progress_tape as ptape
                # keep the tape on the same durable volume as the rest of
                # this service's state unless it was pointed somewhere else
                os.environ.setdefault("PMUS_PROGRESS_TAPE_DIR",
                                      str(self.state_dir / "progress"))
                obs = ptape.Observer()
            except Exception as exc:              # noqa: BLE001
                self.out("L5_SHADOW_ALARM " + json.dumps(
                    {"alarm": "progress_observer_unavailable",
                     "error": repr(exc)[:200]}))
                self._progress_obs = False
                return
            self._progress_obs = obs
            self.out("PMUS_PROGRESS_BOOT " + json.dumps(
                {"tick_s": obs.tick_s, "seeded_slugs": len(obs.last),
                 "read_only": True, "host": "l5-shadow-loop",
                 "code_sha": ptape.code_sha16(), "emits_rows": True},
                sort_keys=True))
        if obs is False:
            return
        try:
            row = obs.maybe_tick()
        except Exception as exc:                  # noqa: BLE001
            self.out("PMUS_PROGRESS_ALARM " + json.dumps(
                {"alarm": "tick_failed", "error": repr(exc)[:200]}))
            return
        if row is not None and (row["rows"] or row.get("diag_error")):
            self.out("PMUS_PROGRESS " + json.dumps(row, sort_keys=True))


# --- dry-run fixture: first rows of the canonical sequence, read locally ------

def build_fixture_from_canonical(csv_path: Path = ops.SEQ_CSV, n_rows: int = 500,
                                 wallet: str = ops.L5_WALLET) -> List[Dict[str, Any]]:
    """Raw-activity-shaped rows (the collector's day-file vocabulary) rebuilt
    from the canonical sequence so the dry-run exercises the real adapter +
    canonical_row + lookup path with zero network."""
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    out: List[Dict[str, Any]] = []
    with opener(csv_path, "rt", newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if i >= n_rows:
                break
            raw = {"proxyWallet": wallet, "timestamp": int(float(row["timestamp"])),
                   "type": "TRADE", "slug": row["market_slug"], "outcome": row["outcome"],
                   "side": row["side"], "price": float(row["price"]),
                   "size": float(row["quantity"]), "usdcSize": float(row["dollars"]),
                   "transactionHash": row["transaction_hash"], "outcomeIndex": 0,
                   "_venue": "polymarket", "_collected_utc": "fixture"}
            raw["_fp"] = ops._fingerprint(raw)
            out.append(raw)
    return out


def write_fixture_day_files(rows: Iterable[Mapping[str, Any]], ops_dir: Path) -> int:
    """Lay the fixture out exactly as the collector would (append-only day
    files), so stream_new_rows reads it through the production path."""
    ops_dir.mkdir(parents=True, exist_ok=True)
    by_day: Dict[str, List[str]] = defaultdict(list)
    for r in rows:
        by_day[_day(float(r["timestamp"]))].append(json.dumps(r, separators=(",", ":")))
    for day, lines in by_day.items():
        (ops_dir / f"activity_{day}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sum(len(v) for v in by_day.values())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="fixture from the canonical sequence; no network; one cycle; "
                         "forces a proposal so the whole path is exercised")
    ap.add_argument("--state-dir", default=None, help="overrides L5_SHADOW_STATE_DIR")
    ap.add_argument("--interval", type=float, default=None, help="overrides L5_SHADOW_INTERVAL_S")
    ap.add_argument("--propose", action="store_true", help="force a proposal this cycle")
    ap.add_argument("--fixture-rows", type=int, default=500)
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.interval is not None:
        cfg["L5_SHADOW_INTERVAL_S"] = args.interval
    now_fn: Optional[Callable[[], float]] = None
    if args.dry_run:
        state_dir = Path(args.state_dir or tempfile.mkdtemp(prefix="l5_shadow_dryrun_"))
        fixture = build_fixture_from_canonical(n_rows=args.fixture_rows)
        if not fixture:
            print("L5_SHADOW_DRYRUN_ERROR no fixture rows")
            return 2
        anchor = max(float(r["timestamp"]) for r in fixture) + 60.0
        now_fn = lambda: anchor                       # noqa: E731
        loop = ShadowLoop(cfg, state_dir, dry_run=True, now_fn=now_fn)
        n = write_fixture_day_files(fixture, ops.OPS_DIR)
        print("L5_SHADOW_DRYRUN " + json.dumps({"fixture_rows": n, "anchor_utc": _utc(anchor),
                                                "state_dir": str(loop.state_dir),
                                                "network": "none"}))
        return loop.run(once=True, force_proposal=True)
    state_dir = Path(args.state_dir or cfg["L5_SHADOW_STATE_DIR"])
    loop = ShadowLoop(cfg, state_dir)
    return loop.run(once=args.once, force_proposal=args.propose)


if __name__ == "__main__":
    raise SystemExit(main())
