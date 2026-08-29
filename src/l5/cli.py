"""Command line entry point: `python -m src.l5 <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from src.l5 import config, pipeline


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-dir", default=config.CACHE_DIR)
    parser.add_argument("--output-dir", default=config.OUTPUT_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.l5",
        description=(
            "Read-only exporter/analyzer for one Polymarket trader's BTC/ETH 5m and 15m "
            "Up/Down history. Makes GET requests to public endpoints only."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="populate the raw JSONL cache from the public APIs")
    _add_common(fetch)
    fetch.add_argument("--wallet", default=config.TARGET_WALLET)
    fetch.add_argument("--profile", default=config.TARGET_PROFILE)
    fetch.add_argument("--start-ts", type=int, default=None)
    fetch.add_argument("--end-ts", type=int, default=None)
    fetch.add_argument(
        "--append", action="store_true", help="keep existing cache files instead of truncating"
    )

    ingest = sub.add_parser(
        "ingest", help="add user-downloaded data-api JSON pages to the raw cache"
    )
    _add_common(ingest)
    ingest.add_argument("files", nargs="+", help="JSON files saved from the public data API")
    ingest.add_argument("--wallet", default=config.TARGET_WALLET)

    build = sub.add_parser("build", help="cache -> CSVs (offline)")
    _add_common(build)

    analyze = sub.add_parser("analyze", help="cache -> CSVs + analysis + findings (offline)")
    _add_common(analyze)
    analyze.add_argument("--findings", default="L5_STRATEGY_FINDINGS.md")
    analyze.add_argument("--no-findings", action="store_true")

    every = sub.add_parser("all", help="fetch, then build and analyze")
    _add_common(every)
    every.add_argument("--wallet", default=config.TARGET_WALLET)
    every.add_argument("--profile", default=config.TARGET_PROFILE)
    every.add_argument("--findings", default="L5_STRATEGY_FINDINGS.md")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in ("fetch", "all"):
        manifest = pipeline.run_fetch(
            args.cache_dir,
            wallet=args.wallet,
            profile=args.profile,
            start_ts=getattr(args, "start_ts", None),
            end_ts=getattr(args, "end_ts", None),
            reset=not getattr(args, "append", False),
        )
        print(json.dumps({"fetch_manifest": manifest}, indent=2)[:4000])

    if args.command == "ingest":
        from src.l5.userjson import ingest_files

        stats = ingest_files(args.files, args.cache_dir, wallet=args.wallet)
        print(json.dumps({"ingest": stats.as_dict()}, indent=2))

    if args.command == "build":
        result = pipeline.build(args.cache_dir, args.output_dir)
        print(json.dumps(
            {"rows_written": result["rows_written"],
             "normalize_stats": result["normalize_stats"],
             "markets_in_scope": result["markets_in_scope"]},
            indent=2,
        ))

    if args.command in ("analyze", "all"):
        findings = None if getattr(args, "no_findings", False) else args.findings
        result = pipeline.analyze(args.cache_dir, args.output_dir, findings)
        print(json.dumps(
            {"rows_written": result["rows_written"],
             "normalize_stats": result["normalize_stats"],
             "analysis_path": result["analysis_path"],
             "findings_path": result["findings_path"],
             "coverage": result["analysis"]["coverage"]},
            indent=2,
            default=str,
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
