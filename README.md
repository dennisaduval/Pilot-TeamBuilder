# Pilot TeamBuilder

**VolumePilot — MORE PHOTOS. LESS CHAOS.**

Repository: [dennisaduval/Pilot-TeamBuilder](https://github.com/dennisaduval/Pilot-TeamBuilder).

Version **0.3.1.dev0**, milestones TB-001 through TB-003. Includes an initial
desktop review application, composition engine, manifest preflight and offline
detection. Production watching/routing and packaged field releases remain in development.

## Desktop review

After installing the editable package, launch:

```sh
pilot-teambuilder-desktop
pilot-teambuilder-desktop --demo
pilot-teambuilder-desktop --job /path/to/saved-job.json
```

Tk/Tcl is required (normally supplied with the desktop Python distribution;
Linux users may need their distribution's tkinter package). On a Mac, build the
native face helper with `python scripts/build_face_helper.py` before automatic
detection. The synthetic demo works on every supported platform without it.
The packaged desktop also has a **Demo** toolbar button for testing without commands.

The desktop includes a team queue, large panoramic preview, athlete list,
inch-based composition controls, and a portrait editor for head boxes and
anatomical head/foot anchors. Reviewers can change height order, row counts,
row/slot assignments, exclusions and individual placement without changing PNGs.
Saved jobs retain manual corrections, source calibration, template presets and
export history. Current source preflight and conservative visibility checks still
apply; physical-height recovery is not automatic in this milestone.

Use **Choose team folder…** to select athlete PNGs or the parent of several team
folders; no JSON is required. **Open saved job…** restores saved corrections.
**Import manifest…** is the advanced option for a verified CLI manifest.
The **Layout** tab beneath the large panorama has live row-spacing, inverted-V,
horizontal-spacing and group-size sliders, plus requested rows and a distribution
readout. **Auto** follows the supplied CSV; explicit requested rows preserve every
included athlete while enforcing 16 per row. **Advanced** retains exact inch fields
and manual row distributions.
Choose templates and a completed-image folder, confirm unchanged capture scale
only when verified, analyze, and correct review items. **Approve & export JPEG**
creates a new numbered 26 × 8-inch JPEG after rechecking the reviewed sources and
templates. It never sends files to a printer or upload service. Save the job to
retain edits. See [desktop guide](docs/DESKTOP.md).

Mac CI also builds an Apple Silicon `.app` preview, including Python/Tk, the row
table and the native detector. It verifies the bundle and performs a startup smoke
test before uploading the ZIP as a workflow artifact. No Python/Xcode installation
is needed to run that preview. It has an ad-hoc signature, not a Developer ID or
Apple notarization; it is a development build for testing, not an event release.

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
- Case-insensitive `_default` template discovery, duplicate detection, 3:1
  validation, alpha validation, and two-layer decoded cache.
- Serial PNG compositing, rotated identification strip adapted from PanoBuilder,
  verified JPEG publication, source-bound checks, no overwrite, stage timing.
- Conservative face-obstruction review flags and hard failures for missing,
  multiple, low-confidence heads or unknown capture scale/height.

## Architecture and continuation

See [development record](docs/DEVELOPMENT.md) for repository review, source
provenance, extension algorithm, assumptions, validation, and milestone backlog.
No existing Capture, HQ, PanoBuilder, or Hotfolder code is changed.
