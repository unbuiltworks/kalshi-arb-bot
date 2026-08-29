"""Print a GitHub Actions step summary for an L5 export run."""

from __future__ import annotations

import json
import os

CACHE = os.path.join("data", "l5", "cache")
OUT = os.path.join("data", "l5")


def main() -> None:
    print("## L5 Polymarket export\n")

    manifest_path = os.path.join(CACHE, "fetch_manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        print("### Retrieval\n")
        print("| source | records | pages | truncated |")
        print("| --- | --- | --- | --- |")
        for entry in manifest.get("endpoints", []):
            note = entry.get("truncation_reason", "") if entry.get("truncated") else "no"
            print(f"| {entry['name']} | {entry['records']} | {entry['pages']} | {note} |")
        print()

    analysis_path = os.path.join(OUT, "l5_analysis.json")
    if os.path.exists(analysis_path):
        with open(analysis_path, encoding="utf-8") as handle:
            analysis = json.load(handle)
        print("### Coverage\n")
        print("```json")
        print(json.dumps(analysis.get("coverage", {}), indent=2))
        print("```")


if __name__ == "__main__":
    main()
