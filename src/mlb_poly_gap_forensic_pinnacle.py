"""Targeted forensic replay of logged FanDuel↔Polymarket MLB moneyline gaps.

This is deliberately not a random backtest.  It spends historical Odds API
credits only on exact timestamps where the live watcher logged loud gaps, then
asks what Pinnacle's EU-region h2h fair value was at the corresponding historical
snapshot.  Polymarket executable depth is the depth captured by the watcher at
that instant; it is not reconstructed later.

Read-only. No order entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any

import httpx

URL = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds"
QUOTA_FLOOR = 800

TARGETS = [
    {
        "label": "PHI-SEA loud moneyline",
        "requested_ts": "2026-08-25T02:03:15Z",
        "team_a": "Philadelphia Phillies",
        "team_b": "Seattle Mariners",
        "subject": "Philadelphia Phillies",
        "watcher_poly_side": "NO",
        "watcher_poly_ask": 0.695,
        "watcher_poly_q1_depth": 5.0,
        "watcher_fd_semantic_action": "YES",
        "watcher_fd_implied_cost": 0.2173913043478261,
        "watcher_locked_profit": 0.07760869565217391,
    },
    {
        "label": "MIN-ATH 3.1c moneyline",
        "requested_ts": "2026-08-25T02:04:15Z",
        "team_a": "Minnesota Twins",
        "team_b": "Athletics",
        "subject": "Minnesota Twins",
        "watcher_poly_side": "NO",
        "watcher_poly_ask": 0.330,
        "watcher_poly_q1_depth": 763.42,
        "watcher_fd_semantic_action": "YES",
        "watcher_fd_implied_cost": 0.6289308176100629,
        "watcher_locked_profit": 0.031069182389937107,
    },
    {
        "label": "CHC-AZ loud moneyline",
        "requested_ts": "2026-08-25T02:14:17Z",
        "team_a": "Chicago Cubs",
        "team_b": "Arizona Diamondbacks",
        "subject": "Chicago Cubs",
        "watcher_poly_side": "NO",
        "watcher_poly_ask": 0.335,
        "watcher_poly_q1_depth": 82260.39,
        "watcher_fd_semantic_action": "YES",
        "watcher_fd_implied_cost": 0.6060606060606061,
        "watcher_locked_profit": 0.04893939393939394,
    },
    {
        "label": "PIT-SD loud moneyline",
        "requested_ts": "2026-08-25T03:06:19Z",
        "team_a": "Pittsburgh Pirates",
        "team_b": "San Diego Padres",
        "subject": "Pittsburgh Pirates",
        "watcher_poly_side": "NO",
        "watcher_poly_ask": 0.340,
        "watcher_poly_q1_depth": 130300.63,
        "watcher_fd_semantic_action": "YES",
        "watcher_fd_implied_cost": 0.46511627906976744,
        "watcher_locked_profit": 0.18488372093023256,
    },
    {
        "label": "CLE-LAA loud moneyline",
        "requested_ts": "2026-08-25T03:14:50Z",
        "team_a": "Cleveland Guardians",
        "team_b": "Los Angeles Angels",
        "subject": "Cleveland Guardians",
        "watcher_poly_side": "NO",
        "watcher_poly_ask": 0.085,
        "watcher_poly_q1_depth": 69888.91,
        "watcher_fd_semantic_action": "YES",
        "watcher_fd_implied_cost": 0.7575757575757576,
        "watcher_locked_profit": 0.15742424242424242,
    },
]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace(".", "").split())


def _event_matches(event: dict[str, Any], target: dict[str, Any]) -> bool:
    names = {_norm(event.get("home_team")), _norm(event.get("away_team"))}
    required = {_norm(target["team_a"]), _norm(target["team_b"])}
    if required <= names:
        return True
    # Athletics naming has changed across feeds; accept either city-less or
    # historical Oakland label only for this known target.
    if "athletics" in required:
        alt = {_norm(target["team_a"]), "oakland athletics"}
        return alt <= names or {_norm(target["team_a"]), "athletics"} <= names
    return False


def _pinnacle_fair(event: dict[str, Any]) -> dict[str, float] | None:
    book = next((b for b in event.get("bookmakers") or [] if str(b.get("key")) == "pinnacle"), None)
    if not isinstance(book, dict):
        return None
    market = next((m for m in book.get("markets") or [] if str(m.get("key")) == "h2h"), None)
    if not isinstance(market, dict):
        return None
    raw: dict[str, float] = {}
    for row in market.get("outcomes") or []:
        try:
            dec = float(row.get("price"))
            if dec > 1:
                raw[str(row.get("name"))] = 1.0 / dec
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if len(raw) != 2:
        return None
    total = sum(raw.values())
    if total <= 0:
        return None
    return {name: value / total for name, value in raw.items()}


def _subject_fair(fair: dict[str, float], subject: str) -> float | None:
    s = _norm(subject)
    for name, p in fair.items():
        if _norm(name) == s:
            return p
        if s == "athletics" and _norm(name) in {"oakland athletics", "athletics"}:
            return p
    return None


def main() -> None:
    key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not key:
        raise SystemExit("THE_ODDS_API_KEY is required")

    rows: list[dict[str, Any]] = []
    last_quota: dict[str, str] = {}
    with httpx.Client(timeout=30.0, follow_redirects=True) as http:
        for target in TARGETS:
            try:
                remaining_before = int(last_quota.get("remaining", "999999"))
            except ValueError:
                remaining_before = 999999
            if remaining_before <= QUOTA_FLOOR:
                rows.append({
                    **target,
                    "status": "SKIPPED_QUOTA_FLOOR",
                    "quota_floor": QUOTA_FLOOR,
                })
                break

            response = http.get(URL, params={
                "apiKey": key,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "date": target["requested_ts"],
            })
            last_quota = {
                "remaining": response.headers.get("x-requests-remaining", ""),
                "used": response.headers.get("x-requests-used", ""),
                "last_cost": response.headers.get("x-requests-last", ""),
            }
            if response.status_code != 200:
                rows.append({
                    **target,
                    "status": "HISTORICAL_HTTP_ERROR",
                    "http_status": response.status_code,
                    "quota": dict(last_quota),
                })
                continue

            body = response.json()
            events = body.get("data") or [] if isinstance(body, dict) else []
            event = next((e for e in events if isinstance(e, dict) and _event_matches(e, target)), None)
            snapshot_ts = str(body.get("timestamp") or "") if isinstance(body, dict) else ""
            if event is None:
                rows.append({
                    **target,
                    "status": "MATCHING_EVENT_NOT_FOUND",
                    "snapshot_ts": snapshot_ts,
                    "quota": dict(last_quota),
                })
                continue

            fair = _pinnacle_fair(event)
            if fair is None:
                rows.append({
                    **target,
                    "status": "PINNACLE_H2H_NOT_FOUND_IN_EU_SNAPSHOT",
                    "snapshot_ts": snapshot_ts,
                    "quota": dict(last_quota),
                })
                continue
            subject_fair = _subject_fair(fair, target["subject"])
            requested_dt = datetime.fromisoformat(target["requested_ts"].replace("Z", "+00:00")).astimezone(timezone.utc)
            try:
                snap_dt = datetime.fromisoformat(snapshot_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
                ref_age_s = (requested_dt - snap_dt).total_seconds()
            except Exception:
                ref_age_s = None

            poly_yes_from_no_ask = (
                1.0 - float(target["watcher_poly_ask"])
                if target["watcher_poly_side"] == "NO"
                else None
            )
            rows.append({
                **target,
                "status": "FORENSIC_SNAPSHOT_MATCHED",
                "snapshot_ts": snapshot_ts,
                "reference_age_s": ref_age_s,
                "commence_time": event.get("commence_time"),
                "pinnacle_fair": fair,
                "pinnacle_fair_subject": subject_fair,
                "poly_yes_bid_equivalent_from_no_ask": poly_yes_from_no_ask,
                "pinnacle_minus_poly_bid_equivalent": (
                    None if subject_fair is None or poly_yes_from_no_ask is None
                    else subject_fair - poly_yes_from_no_ask
                ),
                "fanduel_implied_minus_pinnacle": (
                    None if subject_fair is None
                    else float(target["watcher_fd_implied_cost"]) - subject_fair
                ),
                "quota": dict(last_quota),
                "query_contract": {
                    "regions": "eu",
                    "markets": "h2h",
                    "pinnacle_selected_from_response_only": True,
                },
                "read_only": True,
                "order_entry": False,
            })

    print(json.dumps({
        "status": "MLB_POLY_GAP_FORENSIC_PINNACLE",
        "design": "EXACT_LOGGED_GAP_TIMESTAMPS_NOT_RANDOM_SAMPLE",
        "snapshots_requested": len([r for r in rows if r.get("status") != "SKIPPED_QUOTA_FLOOR"]),
        "targets": rows,
        "quota": last_quota,
        "read_only": True,
        "order_entry": False,
    }, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
