"""CLI for the L5 study.

    python -m src.polymarket_l5.cli fetch     # network: harvest public APIs into the raw cache
    python -m src.polymarket_l5.cli build     # offline: raw cache -> the three CSVs
    python -m src.polymarket_l5.cli analyze   # offline: CSVs -> stats JSON
    python -m src.polymarket_l5.cli report    # offline: stats -> L5_STRATEGY_FINDINGS.md
    python -m src.polymarket_l5.cli all       # fetch + build + analyze + report

`build`, `analyze` and `report` never touch the network, so the whole analysis
is reproducible from the cached raw responses alone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from . import config
from .analyze import analyze
from .cache import RawCache
from .export import build
from .fetch import run_fetch
from .findings import write_findings
from .timeutil import now_iso


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except Exception:  # pragma: no cover - provenance is best effort
        return ""


def load_manifest(output_dir: Path) -> Dict[str, Any]:
    path = Path(output_dir) / config.MANIFEST_JSON
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_manifest(manifest: Dict[str, Any], output_dir: Path) -> Path:
    path = Path(output_dir) / config.MANIFEST_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = now_iso()
    manifest["code_version"] = _git_sha()
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def _fetch_status(report: Dict[str, Any]) -> tuple[str, str]:
    activity = report.get("activity", {}) or {}
    if activity.get("items", 0) > 0:
        return "ok", f"{activity['items']} activity rows over {activity.get('pages', 0)} page(s)"
    reason = activity.get("stopped_because", "unknown")
    if str(reason).startswith("http_") or "transport error" in str(reason):
        return "failed", f"/activity unreachable: {reason}"
    return "empty", f"/activity returned no rows: {reason}"


def cmd_fetch(args: argparse.Namespace) -> Dict[str, Any]:
    cache = RawCache(Path(args.raw_dir))
    report = run_fetch(args.address, cache=cache, fresh=not args.append)
    status, note = _fetch_status(report)
    manifest = load_manifest(Path(args.output_dir))
    manifest.update(
        {
            "address": args.address,
            "profile_slug": config.TARGET_PROFILE_SLUG,
            "fetch": report,
            "fetch_status": status,
            "fetch_note": note,
            "raw_dir": str(args.raw_dir),
        }
    )
    save_manifest(manifest, Path(args.output_dir))
    print(f"fetch: {status} -- {note}")
    return manifest


def cmd_build(args: argparse.Namespace) -> Dict[str, Any]:
    cache = RawCache(Path(args.raw_dir))
    report = build(cache=cache, output_dir=Path(args.output_dir))
    manifest = load_manifest(Path(args.output_dir))
    manifest["build"] = report
    manifest.setdefault("fetch_status", "not-run")
    manifest.setdefault("fetch_note", "no fetch recorded in this manifest")
    save_manifest(manifest, Path(args.output_dir))
    print(json.dumps(report["rows_written"], indent=2))
    return manifest


def cmd_analyze(args: argparse.Namespace) -> Dict[str, Any]:
    results = analyze(Path(args.output_dir))
    manifest = load_manifest(Path(args.output_dir))
    manifest["coverage"] = results.get("coverage", {})
    save_manifest(manifest, Path(args.output_dir))
    print(json.dumps(results.get("coverage", {}), indent=2, default=str))
    return manifest


def cmd_report(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir)
    stats_path = output_dir / config.STATS_JSON
    results = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else analyze(output_dir)
    manifest = load_manifest(output_dir)
    target = write_findings(
        results,
        manifest,
        output_dir=output_dir,
        findings_path=Path(args.findings_path) if args.findings_path else None,
    )
    print(f"wrote {target}")
    return manifest


def cmd_all(args: argparse.Namespace) -> Dict[str, Any]:
    cmd_fetch(args)
    cmd_build(args)
    cmd_analyze(args)
    return cmd_report(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Polymarket L5 window study")
    parser.add_argument("command", choices=["fetch", "build", "analyze", "report", "all"])
    parser.add_argument("--address", default=config.TARGET_ADDRESS)
    parser.add_argument("--raw-dir", default=str(config.RAW_DIR))
    parser.add_argument("--output-dir", default=str(config.OUTPUT_DIR))
    parser.add_argument("--findings-path", default="")
    parser.add_argument(
        "--append",
        action="store_true",
        help="keep existing raw cache files instead of starting a fresh harvest",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "fetch": cmd_fetch,
        "build": cmd_build,
        "analyze": cmd_analyze,
        "report": cmd_report,
        "all": cmd_all,
    }
    handlers[args.command](args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
