"""Private local sample validation. Never uploads images or guesses physical height."""

import argparse
from collections import Counter
import json
from math import isfinite
from pathlib import Path
from time import perf_counter

from PIL import Image

from pilot_teambuilder.discovery import png_paths
from pilot_teambuilder.sources import load_png


def capture_hints(path: Path) -> dict:
    # Deliberate allowlist: never export GPS, authors, serial numbers or raw XMP.
    with Image.open(path) as image:
        exif = image.getexif()
        details = exif.get_ifd(34665)
        focal = details.get(37386)
        try:
            focal = float(focal) if focal is not None else None
        except (ValueError, TypeError, ZeroDivisionError):
            focal = None
        if focal is not None and not isfinite(focal):
            focal = None
        return {"camera_model": str(exif.get(272, "")), "focal_length_mm": focal,
                "metadata_pixel_dimensions": [details.get(40962), details.get(40963)],
                "orientation": exif.get(274), "dpi": image.info.get("dpi"),
                "has_icc_profile": bool(image.info.get("icc_profile")),
                "has_xmp": bool(image.info.get("xmp") or image.info.get("XML:com.adobe.xmp"))}


def inspect(root: Path) -> dict:
    root = root.resolve(strict=True)
    records = []
    seen = {}
    started = perf_counter()
    for path in png_paths(root):
        record = {"file": path.relative_to(root).as_posix(), "team": path.parent.name,
                  "status": "valid", "issues": []}
        records.append(record)
        tick = perf_counter()
        try:
            hints = capture_hints(path)
            with load_png(path) as source:
                record.update(image_size=source.image.size, sha256=source.sha256,
                              alpha_bounds=list(vars(source.body).values()), capture_hints=hints)
                with source.image.getchannel("A") as alpha:
                    for threshold in (8, 128):
                        with alpha.point(lambda value: 255 if value > threshold else 0) as mask:
                            record[f"alpha_bounds_above_{threshold}"] = mask.getbbox()
                # These are diagnostics, not body/head/foot detections. Retain all
                # original nonzero pixels; don't clean masks or remove reflections.
                support = record["alpha_bounds_above_8"]
                if support:
                    raw = record["alpha_bounds"]
                    extent = max(abs(a - b) for a, b in zip(raw, support))
                    record["faint_alpha_extent_px"] = extent
                    if extent > 32:
                        record["issues"].append("Faint alpha pixels expand silhouette bounds by over 32 pixels; review extraction residue.")
                if source.sha256 in seen:
                    record["issues"].append("Duplicate content of " + seen[source.sha256])
                seen[source.sha256] = record["file"]
        except (OSError, ValueError) as error:
            record["status"] = "invalid"
            record["issues"].append(str(error))
        record["inspection_seconds"] = perf_counter() - tick
    return {"schema_version": 1, "total_pngs": len(records),
            "teams": dict(Counter(r["team"] for r in records)),
            "valid_pngs": sum(r["status"] == "valid" for r in records),
            "images_with_diagnostics": sum(bool(r["issues"]) for r in records),
            "inspection_seconds": perf_counter() - started, "images": records,
            "limitations": ["No automatic face detection runs in this inspector.",
                            "Equal canvas dimensions and EXIF do not prove preserved extraction scale.",
                            "Alpha bounds include bows and reflections; they are not physical height."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Report already exists; choose a new destination.")
    result = inspect(args.root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({k:v for k,v in result.items() if k != "images"}, indent=2))


if __name__ == "__main__":
    main()
