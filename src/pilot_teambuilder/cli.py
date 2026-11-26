"""Offline team inventory, manifest preflight, and synthetic demonstration."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from PIL import Image

from .composition import compose
from .analysis import analyze_team
from .detection import VisionDetector
from .detection_cache import DetectionCache
from .manifests import inventory, load_manifest
from .model import CompositionSettings, Corrections
from .rendering import discover_templates, render
from .rows import RowTable
from .synthetic import athletes, write_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", type=int, metavar="ATHLETES")
    mode.add_argument("--inspect", type=Path, metavar="TEAM_FOLDER")
    mode.add_argument("--manifest", type=Path, metavar="TEAM_MANIFEST")
    parser.add_argument("--output", type=Path, required=True, help="New result directory")
    parser.add_argument("--rows", type=Path, default=Path(__file__).parent / "data/row_counts.csv")
    parser.add_argument("--templates", type=Path, help="Optional template directory; enables draft JPEG rendering")
    parser.add_argument("--face-helper", type=Path)
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--refresh-detections", action="store_true")
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    table = RowTable.load(args.rows)
    if args.inspect:
        try:
            manifest = inventory(args.inspect)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "team-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Inventoried {len(manifest['athletes'])} PNGs. Review capture metadata in team-manifest.json.")
        return
    if args.manifest:
        try:
            manifest = load_manifest(args.manifest)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        if args.output.resolve().is_relative_to(manifest.source_folder):
            parser.error("Output must be outside the source folder to avoid ingesting generated files.")
        args.output.mkdir(parents=True, exist_ok=False)
        cache_path = args.cache or args.manifest.parent / ".pilot-teambuilder/detections.sqlite3"
        with DetectionCache(cache_path) as cache:
            result = analyze_team(manifest, table, VisionDetector(args.face_helper, args.cpu_only),
                                  cache, args.refresh_detections)
        if result.layout is not None and args.templates:
            try:
                bg, fg = discover_templates(args.templates)
                result.report["render"] = render(result.layout, result.sources, bg, fg, manifest.team_name,
                                                  args.output / "team.jpg", expected_hashes=result.hashes)
            except (OSError, ValueError) as error:
                result.report["status"] = "review"
                result.report["issues"].append("Draft render failed: " + str(error))
        (args.output / "analysis.json").write_text(json.dumps(result.report, indent=2) + "\n")
        print(json.dumps({"status": result.report["status"], "cache_hits": result.report["cache_hits"],
                          "issues": result.report["issues"]}, indent=2))
        if result.report["status"] == "review":
            raise SystemExit(2)
        return
    people = athletes(args.demo)
    layout = compose(people, table)
    args.output.mkdir(parents=True, exist_ok=False)
    sources = write_sources(args.output / "sources", people)
    background, foreground = args.output / "background.jpg", args.output / "foreground.png"
    with Image.new("RGB", (7200, 2400), "#0B1F3A") as image:
        image.save(background)
    with Image.new("RGBA", (7200, 2400)) as image:
        image.save(foreground)
    result = render(layout, sources, background, foreground, "SYNTHETIC DEMO TEAM",
                    args.output / "team.jpg")
    record = {"schema_version": 1, "input_kind": "synthetic",
              "athletes": [asdict(a) for a in people],
              "settings": asdict(CompositionSettings()), "corrections": asdict(Corrections()),
              "sources": {key: str(path.relative_to(args.output)) for key, path in sources.items()},
              "layout": asdict(layout), "render": result}
    (args.output / "layout.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
