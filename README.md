# Pilot TeamBuilder

**VolumePilot — MORE PHOTOS. LESS CHAOS.**

Repository: [dennisaduval/Pilot-TeamBuilder](https://github.com/dennisaduval/Pilot-TeamBuilder).

Version **0.5.0.dev0**, milestones TB-001 through TB-004. Includes an initial
desktop review application, composition engine, manifest preflight and offline
detection. Hotfolder processing and verified delivery are implemented; production field validation remains outstanding.

## Automatic production desktop

**Choose hotfolder → Job setup → Start automatic processing.** Team folders are
team names; reliable teams render and deliver automatically without per-athlete
approval. Saved job-wide settings include templates, color variants, the selected
row CSV, common athlete size (up to 300%), placement and delivery locations.
The visible header/window version identifies the actual installed build.
Nested team folders are discovered recursively. Relaxed detection accepts ordinary
moderate-confidence poses, while selected default templates provide an explicit
fallback for missing color designs. Waist-up framing, visible up/down and left/right
controls, and preview-only center guides support full-body captures in a torso design.

Under the confirmed fixed-camera/original-canvas workflow, face positions provide
relative stature ranking while athletes retain one common capture scale. Missing,
multiple or uncertain detections and color/template errors are exceptions. The
large preview shows composites for valid teams and a labeled source overview for
held teams. Recovery controls are available on demand.

The watcher waits for stable folders, supports an explicit team.ready marker,
checks sources before publication, tracks processing/delivery in SQLite, prevents
duplicate output/printing and resumes interrupted work. One 7800 × 2400 / 300-PPI
JPEG is rendered and verified, then copied to configured destinations. Sources
remain untouched in Incoming; automatic source archival is not yet implemented.

After installing the editable package, use `pilot-teambuilder-desktop` or
`pilot-teambuilder-desktop --demo`. Apple Silicon CI builds a self-contained app
including the native offline detector; no Python or Xcode is needed on the test
Mac. The app is ad-hoc signed, unnotarized and still requires real-photo/event
validation. See the [desktop guide](docs/DESKTOP.md) for templates, color naming,
scale, transfer handoff and print-recovery behavior.

## Run and test

Python 3.13+ and Pillow 11.3.0 are required. From the repository root:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
pilot-teambuilder --demo 42 --output /tmp/teambuilder-demo
```

Without installation (when Pillow is already installed):

```sh
PYTHONPATH=src python -m pilot_teambuilder.cli --demo 42 --output /tmp/teambuilder-demo
```

The output directory must be new. The harness writes synthetic source PNGs,
placeholder templates, **one 7800 × 2400 JPEG at 300 PPI**, and `layout.json` with
input geometry, corrections, settings, layout, review flags, and stage timings.
It performs no upload, printing, archiving, or hotfolder monitoring.

## Analyze extracted PNGs

Create a new inventory without modifying source images:

```sh
pilot-teambuilder --inspect /path/to/team --output /path/to/new-inventory
```

The generated `team-manifest.json` lists every PNG. Confirm each athlete's
capture geometry/resize scale and relative-height measurement; an unknown value
remains an explicit review item. These fields are developer inputs for now; the
planned review interface will replace manual JSON editing.

On a Mac with Xcode Command Line Tools installed, build the native helper once:

```sh
python scripts/build_face_helper.py
```

Analyze a manifest; use `--templates` to optionally render a draft panorama:

```sh
pilot-teambuilder --manifest /path/to/team-manifest.json --output /path/to/new-analysis
pilot-teambuilder --manifest /path/to/team-manifest.json --output /path/to/new-draft --templates /path/to/Templates/Team
```

Each invocation needs a new output directory outside the source folder. It writes
`analysis.json` with every athlete's status, all detected faces, source hashes,
geometry, layout, issues and timings. A source-level problem blocks the entire
layout. A valid layout with possible face obstructions may render a **review
draft** and exits with code 2. Exit code 0 means preflight found no current review
issues; it is not photographic approval. No routing or print side effects occur.

The Mac helper uses Apple's installed Vision framework with automatic compute
selection and a CPU fallback, requires no network/model download, and returns all
face observations. `--cpu-only` forces CPU execution; `--face-helper` selects an
explicit binary (needed when using a non-editable installed wheel).
Windows/Linux support reviewed `manual_head` geometry in this milestone; no
automatic detector is bundled for those platforms.

Content/OS/helper/preprocessing keyed detections are stored in a local SQLite
cache beside the manifest in `.pilot-teambuilder/`. `--cache` selects a different
database; `--refresh-detections` bypasses cached face results. Manual head edits
never replace cached raw detections. See [manifest contract](docs/MANIFEST.md).

For private local image batches, run the non-destructive inspection harness:

```sh
python scripts/inspect_sample_batch.py /path/to/private-test-images --report /path/to/new-inspection.json
```

It verifies PNG decoding, counts every image by team, checks content duplicates,
records alpha bounds at multiple thresholds, and exports only selected capture
hints. It performs no face detection, uploads or source modifications. Known
macOS archive metadata (`__MACOSX`, `._*`, `.DS_Store`) is ignored consistently
by inventory and preflight; other hidden athlete PNGs remain included.

Use `--rows /path/to/row_counts.csv` to select an external row table.

## Implemented

- Exact supplied CSV distributions for 1–45 athletes; all occupied rows ≤16.
- Height sorting; tallest back row; center-out row ordering; deterministic ties.
- Head-centered spacing, subtle inverted-V formation, median head-based row
  separation, equal-row staggering, outside-first/center-last painting.
- Common capture-coordinate normalization, one global team scale, proportional
  placement within the default 22 × 6-inch area at (1, 0.75) inches.
- Inch-based composition settings and explicit manual row/slot assignment,
  exclusions/restoration, and per-athlete inch offsets in the engine API.
- Case-insensitive `_default` template discovery, duplicate detection, 24 × 8 artwork / 26 × 8
  print-template handling, alpha validation, and two-layer decoded cache.
- Serial PNG compositing, rotated identification strip adapted from PanoBuilder,
  verified JPEG publication, source-bound checks, no overwrite, stage timing.
- Conservative face-obstruction review flags and hard failures for missing,
  multiple, low-confidence heads or unknown capture scale/height.

## Architecture and continuation

See [development record](docs/DEVELOPMENT.md) for repository review, source
provenance, extension algorithm, assumptions, validation, and milestone backlog.
No existing Capture, HQ, PanoBuilder, or Hotfolder code is changed.
