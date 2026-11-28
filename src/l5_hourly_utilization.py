"""R5F hourly utilization: per UTC hour, what the venue offered, what the
probe scored, admitted, quoted, filled and settled, the refusal reasons
that dominated, and -- when the venue was busy and the probe idle -- the
exact link that was binding.

OWNER (#167 5577286614 item 6): "Report each hour: live events/markets
observed, on-distribution candidates, admissions, orders, fills,
settlements, and the top refusal reasons. If the venue is busy but R5F is
idle, identify the exact broken/binding link."

SOURCE. Railway log dumps ({"deploy": [{"timestamp", "message"}]}) of the
probe, any mix of filters and windows; rows are de-duplicated by
(timestamp, message) so overlapping pulls are safe, and the table can be
regenerated from a fresh pull at any check-in:

    python3 -m src.l5_hourly_utilization --logs <dir>/*.txt --out analysis/l5

ROWS READ (kind, all PMUS_L5_CAMPAIGN):
  selection      game_progress.live_events (live events the recorder saw),
                 n_rows (universe rows scored), admitted[], refused[].reason,
                 chosen
  funnel         the campaign's cumulative counters (in_distribution etc.);
                 per-hour deltas with reset handling (a restart zeroes them)
  session_start / resume_start   sessions (new / resumed) and the held slug
  session_end    submitted, filled_actions, status, stop_reason, and the
                 in-session decision refusals (last 8 decisions per session)
  settlement     settled == true rows
  status_unavailable / no_candidate_alarm / campaign_* / venue_*   links

ON-DISTRIBUTION. The recorder's funnel counts in_distribution as every
scored row (its off_distribution key is absent from refused rows), so the
table derives it: n_rows minus rows refused for
strict_evidence_off_distribution_or_unknown.

IDLE. A minute is idle when no session was active (session_start /
resume_start -> the next session_end of that slug, capped at 15 min) and
no selection chose a market. For idle minutes in an hour whose selection
rows saw live_events > 0, the binding link is the refusal reason with the
largest share of refused rows in those minutes, together with the
funnel's not_started reasons and any status_unavailable rows; when no
selection row exists in the idle minutes at all, the link is the process
state (daily stop, campaign wait, recorder unavailable).
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.l5_r4_action_comparison import PREFIX

OUT_DIR = os.path.join("analysis", "l5")
SESSION_CAP_S = 15 * 60
OFF_DIST_REASON = "strict_evidence_off_distribution_or_unknown"
LINK_KINDS = ("status_unavailable", "no_candidate_alarm", "campaign_state_restored",
              "campaign_start", "campaign_continuation_start", "venue_outage_start",
              "venue_recovered", "boot_gate_waiting", "recovery_gate_waiting",
              "concurrency_breach", "rung_markets_reached", "advance_gate")


def _ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m:
        s = m.group(1) + (m.group(2) or "")[:7] + m.group(3)
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _hour(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def _minute(t: float) -> int:
    return int(t // 60)


def expand(paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            out.extend(os.path.join(p, n) for n in sorted(os.listdir(p)) if n.endswith((".txt", ".json")))
        else:
            out.append(p)
    return out


def read_rows(paths: Sequence[str]) -> Tuple[List[dict], dict]:
    """Every parseable PMUS_L5_CAMPAIGN row once: {t, ts, kind, j}."""
    seen = set()
    rows: List[dict] = []
    stats = {"files": 0, "files_unreadable": [], "campaign_rows": 0, "duplicates": 0,
             "truncated_rows": 0, "kinds": Counter()}
    for p in expand(paths):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            stats["files_unreadable"].append(p)
            continue
        stats["files"] += 1
        for e in d.get("deploy") or []:
            m = e.get("message") or ""
            if not m.startswith(PREFIX):
                continue
            key = (e.get("timestamp"), hash(m))
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)
            stats["campaign_rows"] += 1
            try:
                j = json.loads(m[len(PREFIX):])
            except json.JSONDecodeError:
                stats["truncated_rows"] += 1
                continue
            t = _ts(e.get("timestamp"))
            if t is None:
                continue
            stats["kinds"][j.get("kind")] += 1
            rows.append({"t": t, "ts": e.get("timestamp"), "kind": j.get("kind"), "j": j})
    rows.sort(key=lambda r: r["t"])
    stats["kinds"] = dict(stats["kinds"].most_common())
    return rows, stats


# ------------------------------ sessions ------------------------------

def active_intervals(rows: Sequence[dict]) -> List[Tuple[float, float, str]]:
    """(start, end, slug): session_start / resume_start -> the next
    session_end of the same slug, capped at SESSION_CAP_S."""
    out = []
    ends = defaultdict(list)
    for r in rows:
        if r["kind"] == "session_end":
            ends[r["j"].get("slug")].append(r["t"])
    for r in rows:
        if r["kind"] in ("session_start", "resume_start"):
            slug = r["j"].get("slug")
            later = [t for t in ends.get(slug, []) if t >= r["t"]]
            end = min(later) if later else r["t"] + SESSION_CAP_S
            out.append((r["t"], min(end, r["t"] + SESSION_CAP_S), slug))
    return out


def funnel_deltas(rows: Sequence[dict]) -> Dict[str, Dict[str, int]]:
    """Per-hour increments of the cumulative funnel counters; a decrease
    (process restart) restarts the baseline at zero."""
    keys = ("cycles", "universe_rows", "in_distribution", "admitted", "sessions", "submitted", "filled")
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: {k: 0 for k in keys})
    prev = {k: 0 for k in keys}
    for r in rows:
        if r["kind"] != "funnel":
            continue
        cur = {k: int(r["j"].get(k) or 0) for k in keys}
        reset = any(cur[k] < prev[k] for k in keys)
        base = {k: 0 for k in keys} if reset else prev
        for k in keys:
            out[_hour(r["t"])][k] += max(0, cur[k] - base[k])
        prev = cur
    return out


# ------------------------------ hourly ------------------------------

def hourly_table(rows: Sequence[dict]) -> List[dict]:
    if not rows:
        return []
    by_hour: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_hour[_hour(r["t"])].append(r)
    intervals = active_intervals(rows)
    fd = funnel_deltas(rows)
    active_minutes = set()
    for a, b, _ in intervals:
        for m in range(_minute(a), _minute(b) + 1):
            active_minutes.add(m)
    out = []
    for hour in sorted(by_hour):
        hr = by_hour[hour]
        sels = [r for r in hr if r["kind"] == "selection"]
        live = [int((r["j"].get("game_progress") or {}).get("live_events") or 0) for r in sels]
        refused = Counter()
        n_rows = 0
        off = 0
        admitted_rows = 0
        chosen = 0
        admitted_slugs: Counter = Counter()
        chosen_minutes = set()
        for r in sels:
            j = r["j"]
            n_rows += int(j.get("n_rows") or 0)
            admitted_rows += len(j.get("admitted") or [])
            if j.get("chosen"):
                chosen += 1
                admitted_slugs[j["chosen"]] += 1
                chosen_minutes.add(_minute(r["t"]))
            for x in j.get("refused") or []:
                key = str(x.get("reason") or "").split(":")[0]
                refused[key] += 1
                if key == OFF_DIST_REASON:
                    off += 1
        ends = [r for r in hr if r["kind"] == "session_end"]
        submitted = sum(int(r["j"].get("submitted") or 0) for r in ends)
        fills = sum(int(r["j"].get("filled_actions") or 0) for r in ends)
        in_session_refusals: Counter = Counter()
        stop_reasons: Counter = Counter()
        for r in ends:
            for d in r["j"].get("decisions") or []:
                if d.get("acted") == "hold" and d.get("refusal"):
                    ref = str(d["refusal"])
                    in_session_refusals[ref.split(":")[0] + (":" + ref.split(":")[1] if ref.startswith("complement_not_priceable") else "")] += 1
            if r["j"].get("stop_reason"):
                stop_reasons[str(r["j"]["stop_reason"]).split(":")[0]] += 1
            if r["j"].get("status") == "DAILY_LOSS_STOP":
                stop_reasons["DAILY_LOSS_STOP"] += 1
        settled = [r for r in hr if r["kind"] == "settlement" and r["j"].get("settled")]
        starts = sum(1 for r in hr if r["kind"] == "session_start")
        resumes = sum(1 for r in hr if r["kind"] == "resume_start")
        links = Counter(r["kind"] for r in hr if r["kind"] in LINK_KINDS)
        not_started: Counter = Counter()
        last_funnel = next((r["j"] for r in reversed(hr) if r["kind"] == "funnel"), None)
        # coverage: minutes between the first and the last row in the hour
        t0, t1 = hr[0]["t"], hr[-1]["t"]
        minutes = list(range(_minute(t0), _minute(t1) + 1))
        idle = [m for m in minutes if m not in active_minutes and m not in chosen_minutes]
        idle_sels = [r for r in sels if _minute(r["t"]) in idle]
        idle_live = [int((r["j"].get("game_progress") or {}).get("live_events") or 0) for r in idle_sels]
        idle_refused: Counter = Counter()
        for r in idle_sels:
            for x in r["j"].get("refused") or []:
                idle_refused[str(x.get("reason") or "").split(":")[0]] += 1
        binding = None
        if idle:
            if idle_sels and max(idle_live or [0]) > 0:
                tot = sum(idle_refused.values())
                top = idle_refused.most_common(1)[0] if idle_refused else (None, 0)
                binding = {"link": "admission", "dominant_refusal": top[0],
                           "share": round(top[1] / tot, 4) if tot else None,
                           "refused_rows_in_idle_minutes": tot,
                           "selection_cycles_in_idle_minutes": len(idle_sels),
                           "live_events_max": max(idle_live),
                           "admitted_rows_in_idle_minutes": sum(len(r["j"].get("admitted") or []) for r in idle_sels),
                           "not_started": (last_funnel or {}).get("not_started"),
                           "status_unavailable_rows": links.get("status_unavailable", 0),
                           "reading": "the venue was busy and every scored row was refused at admission; the named "
                                      "reason is the largest share of refused rows in the idle minutes"}
            elif idle_sels:
                binding = {"link": "venue_quiet", "live_events_max": max(idle_live or [0]),
                           "selection_cycles_in_idle_minutes": len(idle_sels),
                           "reading": "selection ran but the recorder saw no live events"}
            else:
                stop_t = next((r["t"] for r in ends if r["j"].get("status") == "DAILY_LOSS_STOP"), None)
                after_stop = [m for m in idle if stop_t is not None and m >= _minute(stop_t)]
                if stop_t is not None and len(after_stop) == len(idle):
                    state = "DAILY_LOSS_STOP"
                elif links.get("status_unavailable"):
                    state = "status_unavailable (recorder unreadable)"
                else:
                    state = ("no selection cycle in these minutes: settlement audits / resume waits between "
                             "sessions" + (f"; {len(after_stop)} of them after DAILY_LOSS_STOP" if after_stop else ""))
                binding = {"link": "process", "state": state, "idle_minutes_utc": [
                    datetime.fromtimestamp(m * 60, timezone.utc).strftime("%H:%M") for m in idle],
                           "reading": "no selection cycle ran in the idle minutes"}
        out.append({
            "hour_utc": hour,
            "coverage": {"first_row": hr[0]["ts"], "last_row": hr[-1]["ts"], "minutes_covered": len(minutes),
                         "rows": len(hr)},
            "live_events": {"max": max(live) if live else None, "min": min(live) if live else None,
                            "last": live[-1] if live else None, "selection_cycles": len(sels)},
            "universe_rows_scored": n_rows,
            "on_distribution_rows": n_rows - off,
            "off_distribution_refused": off,
            "admitted_rows": admitted_rows,
            "admissions_chosen": chosen,
            "admitted_slugs": dict(admitted_slugs.most_common()),
            "sessions": {"started": starts, "resumed": resumes, "ended": len(ends)},
            "orders_submitted": submitted,
            "fills": fills,
            "settlements": len(settled),
            "settled_slugs": [r["j"].get("slug") for r in settled],
            "top_refusals_admission": refused.most_common(3),
            "all_refusals_admission": dict(refused.most_common()),
            "top_refusals_in_session": in_session_refusals.most_common(3),
            "session_stop_reasons": dict(stop_reasons.most_common()),
            "funnel_delta": fd.get(hour),
            "links": dict(links.most_common()),
            "idle_minutes": len(idle),
            "binding_link_when_idle": binding,
        })
    return out


def report(paths: Sequence[str]) -> dict:
    rows, stats = read_rows(paths)
    table = hourly_table(rows)
    return {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source": {"paths": [str(p) for p in paths], **stats},
            "hours": table,
            "labels": {"on_distribution_rows": "n_rows minus rows refused for " + OFF_DIST_REASON +
                       " (the funnel's in_distribution counts every row)",
                       "idle_minutes": "minutes with no active session and no admission, within the hour's covered minutes",
                       "binding_link_when_idle": "admission = the dominant refusal reason of the idle minutes' selection "
                                                 "cycles; process = no selection cycle ran; venue_quiet = no live events",
                       "hours_absent": "an hour with no pulled rows is not listed"}}


def render_md(rep: Mapping[str, Any]) -> str:
    L = ["# R5F hourly utilization", "",
         f"Generated {rep['generated_utc']} from {rep['source']['files']} dump files, "
         f"{rep['source']['campaign_rows']} campaign rows ({rep['source']['duplicates']} duplicates dropped, "
         f"{rep['source']['truncated_rows']} truncated). Regenerate: "
         "`python3 -m src.l5_hourly_utilization --logs <dir>/*.txt --out analysis/l5`.", "",
         "| hour (UTC) | covered min | live events (max/last) | universe rows | on-dist rows | admitted rows | admissions | sessions new/resumed | orders | fills | settlements | top refusals (admission) | top refusals (in session) | idle min | binding link when idle |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for h in rep["hours"]:
        le = h["live_events"]
        b = h["binding_link_when_idle"] or {}
        if b.get("link") == "admission":
            link = f"admission: {b['dominant_refusal']} ({b['share']} of {b['refused_rows_in_idle_minutes']} refused rows, live {b['live_events_max']})"
        elif b.get("link") == "process":
            link = f"process: {b['state']}"
        elif b:
            link = f"{b['link']}"
        else:
            link = ""
        L.append(f"| {h['hour_utc']} | {h['coverage']['minutes_covered']} | {le['max']}/{le['last']} | {h['universe_rows_scored']} | "
                 f"{h['on_distribution_rows']} | {h['admitted_rows']} | {h['admissions_chosen']} | "
                 f"{h['sessions']['started']}/{h['sessions']['resumed']} | {h['orders_submitted']} | {h['fills']} | {h['settlements']} | "
                 f"{'; '.join(f'{k} {v}' for k, v in h['top_refusals_admission'])} | "
                 f"{'; '.join(f'{k} {v}' for k, v in h['top_refusals_in_session'])} | {h['idle_minutes']} | {link} |")
    L += ["", "Per-hour detail:", ""]
    for h in rep["hours"]:
        L.append(f"- {h['hour_utc']}: admitted {h['admitted_slugs']}; settled {h['settled_slugs']}; "
                 f"session stops {h['session_stop_reasons']}; links {h['links']}; funnel delta {h['funnel_delta']}; "
                 f"all admission refusals {h['all_refusals_admission']}")
    L += ["", "Labels: " + "; ".join(f"{k} = {v}" for k, v in rep["labels"].items()), ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args(argv)
    rep = report(a.logs)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "r5f_utilization_hourly.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    with open(os.path.join(a.out, "R5F_UTILIZATION_HOURLY.md"), "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    print(json.dumps({"hours": [h["hour_utc"] for h in rep["hours"]], "rows": rep["source"]["campaign_rows"],
                      "duplicates": rep["source"]["duplicates"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
