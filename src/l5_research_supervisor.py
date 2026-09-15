"""ONE process that owns every L5 research stream, and accounts for its own gaps.

WHY A SUPERVISOR AND NOT THREE SERVICES. Three collectors is three things to
deploy, three restart policies and three ways to be quietly dead. One process
owns them, so there is one deployment, one volume and one place that knows
whether we were watching.

THE PROBLEM THIS EXISTS TO SOLVE, and it is not "processes crash". On
2026-09-15 the container was reclaimed at 14:27:14Z. Two collectors died with
it, their durable JSONL survived, and nothing noticed for nine minutes. The
stores then contained a stretch of time with no observations in it -- which is
INDISTINGUISHABLE, to every reader downstream, from a stretch of time in which
L5 did nothing and no quote filled.

So the contract here is not uptime. It is that DOWNTIME IS RECORDED AS
DOWNTIME:

    boot            every start, with the gap since each stream last wrote
    coverage_gap    an explicit, durable "we were not watching from A to B"
    stream_exit     a child died, with its exit code
    stream_restart  and when it came back
    heartbeat       so a silent store can be distinguished from a stopped one

An analysis that finds no fills in a window must be able to ask whether anyone
was looking, and `analysis/l5/coverage.py` answers from these markers.

RESUME, NEVER RESET. Every stream is started against the SAME out-dir it used
before, so its append-only store continues. Nothing here truncates, rotates or
rewrites a store.

READ ONLY, STRUCTURALLY. This module imports no executor, no signer and no
credential reader, and the image it ships in copies none. A test asserts the
import closure; there is no order verb anywhere in it.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

SCHEMA = "l5_research_coverage/v1"
COVERAGE = "coverage.jsonl"
HEARTBEAT_S = 60.0
# The preregistered chain-observer gate, reported on a slow cadence. It is a
# READ over the volume's own stores -- no network, no stream -- and it is
# emitted continuously so the exact metrics are visible as the sample
# accumulates, rather than only when someone remembers to ask.
GATE_S = 600.0
# a child that exits is restarted, but not in a tight loop: a persistent
# failure should be visible in the markers rather than hidden by churn
BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)

# name -> (module, state subdirectory, the file whose last row dates the stream)
STREAMS: Dict[str, tuple] = {
    "exec_shadow": ("src.l5_shadow.service", "l5-shadow-state", "routes.jsonl"),
    "gemini_depth": ("analysis.l5.gemini_depth.probe", "gemdepth",
                     "samples.jsonl"),
    "chain_validate": ("analysis.l5.chain_observer.validate", "chainval",
                       "validations.jsonl"),
}


def _last_row_time(path: str) -> Optional[float]:
    """Timestamp of the newest record in an append-only store, or None.

    Read from the file's own last line rather than from its mtime: mtime moves
    when anything touches the file, while the row is what an analysis actually
    sees, and the gap has to be measured in the same units the analysis reads.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            back = min(size, 65536)
            fh.seek(size - back)
            tail = fh.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        for k in ("t", "observed_at", "decided_at", "evaluated_at",
                  "resolved_at"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


class Supervisor:
    def __init__(self, state_root: str, *, hours: float = 0.0) -> None:
        self.root = state_root
        self.deadline = (time.time() + hours * 3600) if hours else None
        self.procs: Dict[str, subprocess.Popen] = {}
        self.fails: Dict[str, int] = {n: 0 for n in STREAMS}
        self.next_try: Dict[str, float] = {n: 0.0 for n in STREAMS}
        self.started_at = time.time()
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------- markers

    def mark(self, kind: str, **fields: Any) -> Dict[str, Any]:
        rec = {"schema": SCHEMA, "kind": kind, "t": time.time(), **fields}
        try:
            with open(os.path.join(self.root, COVERAGE), "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass                      # a marker we cannot write is still logged
        print("L5_SUPERVISOR " + json.dumps(rec, default=str), flush=True)
        return rec

    def last_heartbeat(self) -> Optional[float]:
        """When this supervisor was last demonstrably alive.

        Read from our OWN coverage log, which is written on a fixed cadence
        regardless of whether any venue did anything. That independence is the
        point -- see `boot` for why a stream's own store cannot answer this.
        """
        path = os.path.join(self.root, COVERAGE)
        if not os.path.exists(path):
            return None
        best = None
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 200000))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        for line in reversed(tail):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") in ("heartbeat", "shutdown", "boot"):
                t = r.get("t")
                if isinstance(t, (int, float)):
                    best = float(t)
                    break
        return best

    def boot(self) -> None:
        """Record the gap since each stream last wrote, BEFORE starting them.

        This is the only moment the gap is knowable: once a stream restarts it
        begins appending again and the hole closes silently.

        BUT A STREAM'S OWN STORE CANNOT MEASURE DOWNTIME, and reading it as if
        it could is an error in the opposite direction from the one this whole
        mechanism exists to prevent. `exec_shadow` writes a row only when L5
        acts, so its 49.8s "gap" across the 15:35Z redeploy was mostly L5 being
        quiet -- while `gemini_depth`, which samples at a fixed 2Hz, showed the
        true 2.3s. Treating the first as downtime would fire coverage_gap
        markers during quiet markets, and a marker that cries wolf is a marker
        nobody reads when it matters.

        So downtime comes from OUR OWN heartbeat, which ticks regardless of the
        venues, and the per-stream figures are reported as what they are: an
        upper bound that conflates our absence with the market's quiet.
        """
        gaps = {}
        for name, (_mod, sub, fname) in STREAMS.items():
            last = _last_row_time(os.path.join(self.root, sub, fname))
            gaps[name] = {
                "last_row_utc": last,
                "gap_s": (round(self.started_at - last, 1) if last else None),
                "store": f"{sub}/{fname}",
            }
        hb = self.last_heartbeat()
        downtime = (round(self.started_at - hb, 1) if hb else None)
        self.mark("boot", pid=os.getpid(), streams=sorted(STREAMS),
                  since_last_row=gaps,
                  last_supervisor_signal_utc=hb,
                  downtime_s=downtime,
                  labels=("downtime_s is the REAL gap: this boot minus the "
                          "supervisor's own last heartbeat, which ticks "
                          "regardless of what the venues did. since_last_row "
                          "is per stream and is an UPPER BOUND -- a stream "
                          "that writes only on events (exec_shadow) cannot "
                          "distinguish our absence from a quiet market, while "
                          "a fixed-cadence sampler (gemini_depth) can. No "
                          "conclusion of NO ACTION or NO FILL may be drawn "
                          "over downtime_s."))
        # the marker fires on OUR downtime, not on a venue's quiet spell: a
        # coverage_gap that appears whenever L5 stops trading is noise, and a
        # marker that cries wolf is one nobody reads when it matters.
        if downtime and downtime > HEARTBEAT_S * 2:
            self.mark("coverage_gap", stream="__supervisor__",
                      from_utc=hb, to_utc=self.started_at, gap_s=downtime,
                      per_stream_upper_bounds={k: v["gap_s"]
                                               for k, v in gaps.items()},
                      labels=("NOT WATCHING between these instants -- measured "
                              "from our own heartbeat, so this is our absence "
                              "and not the market's quiet."))

    # ------------------------------------------------------------ children

    def _cmd(self, name: str) -> List[str]:
        mod, sub, _f = STREAMS[name]
        return [sys.executable, "-m", mod, "--out-dir",
                os.path.join(self.root, sub)]

    def start(self, name: str) -> None:
        if time.time() < self.next_try[name]:
            return
        _mod, sub, _f = STREAMS[name]
        os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        try:
            p = subprocess.Popen(self._cmd(name), stdout=sys.stdout,
                                 stderr=subprocess.STDOUT)
        except Exception as exc:                              # noqa: BLE001
            self.mark("stream_start_failed", stream=name,
                      err=f"{type(exc).__name__}: {exc}")
            self.fails[name] += 1
            self.next_try[name] = time.time() + self._backoff(name)
            return
        self.procs[name] = p
        self.mark("stream_restart" if self.fails[name] else "stream_start",
                  stream=name, pid=p.pid, out_dir=os.path.join(self.root, sub),
                  consecutive_failures=self.fails[name],
                  labels="resumed against the existing store; nothing reset")

    def _backoff(self, name: str) -> float:
        i = min(self.fails[name], len(BACKOFF_S)) - 1
        return BACKOFF_S[max(0, i)]

    def reap(self) -> None:
        for name in list(self.procs):
            p = self.procs[name]
            rc = p.poll()
            if rc is None:
                continue
            self.procs.pop(name, None)
            self.fails[name] += 1
            self.next_try[name] = time.time() + self._backoff(name)
            self.mark("stream_exit", stream=name, pid=p.pid, returncode=rc,
                      consecutive_failures=self.fails[name],
                      labels=("this stream stopped observing at this instant; "
                              "the interval until stream_restart is a coverage "
                              "gap"))

    def stop(self, *_a: Any) -> None:
        self.mark("shutdown", labels=("supervisor asked to stop; children are "
                                      "terminated and the gap starts here"))
        for name, p in list(self.procs.items()):
            try:
                p.terminate()
            except Exception:                                 # noqa: BLE001
                pass
        raise SystemExit(0)

    # ---------------------------------------------------------------- loop

    def report_gate(self) -> Optional[dict]:
        """Print the preregistered gate's current standing.

        A REPORT, never an action: it cannot promote anything, and the
        observer stays unpromoted regardless of what it says. Failing to
        produce it is logged rather than fatal -- a reporting bug must not
        take down the collectors it reports on.
        """
        try:
            from analysis.l5.chain_observer import gate as G
            rep = G.report(self.root)
        except Exception as exc:                              # noqa: BLE001
            print("L5_GATE " + json.dumps(
                {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:200]}),
                flush=True)
            return None
        print("L5_GATE " + json.dumps({
            "ok": True, "PROMOTE": rep["PROMOTE"],
            "passing": f"{rep['gates_passing']}/{rep['gates_total']}",
            "blocking": rep["blocking"],
            "joined": rep["gates"]["G-C1_sample_size"]["evidence"]["joined"],
            "threshold": G.MIN_JOINED,
            "attributed": rep["gates"]["G-C4_attribution"]["evidence"].get(
                "attributed"),
            "price_rate": rep["gates"]["G-C4_attribution"]["evidence"].get(
                "price_rate"),
            "size_rate": rep["gates"]["G-C4_attribution"]["evidence"].get(
                "size_rate"),
            "feed_only": rep["gates"]["G-C2_no_unexplained_misses"][
                "evidence"]["feed_only"],
            "chain_only_unresolved": rep["gates"][
                "G-C3_chain_only_classified"]["evidence"]["unresolved"],
            "retractions": rep["gates"]["G-C5_finality"]["evidence"].get(
                "retraction_events"),
            "runs_dropped_unobserved": rep["gates"]["G-C7_coverage"][
                "evidence"].get("runs_dropped_as_unobserved"),
            "excluded_pre_qualifying": rep["excluded_as_pre_qualifying"],
            "scope": ("shadow observation clock only; this report promotes "
                      "nothing and does not enable live trading"),
        }, default=str), flush=True)
        return rep

    def run(self) -> int:
        self.boot()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        last_hb = 0.0
        last_gate = 0.0
        while self.deadline is None or time.time() < self.deadline:
            for name in STREAMS:
                if name not in self.procs:
                    self.start(name)
            self.reap()
            now = time.time()
            if now - last_hb >= HEARTBEAT_S:
                last_hb = now
                self.mark("heartbeat",
                          uptime_s=round(now - self.started_at, 1),
                          running=sorted(self.procs),
                          not_running=sorted(set(STREAMS) - set(self.procs)),
                          failures=dict(self.fails))
            if now - last_gate >= GATE_S:
                last_gate = now
                self.report_gate()
            time.sleep(2.0)
        self.stop()
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", default="/state/l5-research")
    ap.add_argument("--hours", type=float, default=0.0)
    a = ap.parse_args(argv)
    return Supervisor(a.state_root, hours=a.hours).run()


if __name__ == "__main__":
    raise SystemExit(main())
