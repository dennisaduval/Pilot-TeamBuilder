# Desktop review — TB-003

The first TeamBuilder desktop is a local Tk application using the existing engine,
not a web UI or a photograph-layout mock. It is a development review build; no
production installer, Developer ID/notarization, hotfolder watcher or automatic routing is included.

For a first look, click **Demo** in the desktop toolbar. It supplies synthetic
athletes and templates so the preview/export tools can be tested without setup.

## Review a job

1. Choose **New job**, select the incoming team folder or collection of team
   folders, then save the job JSON somewhere outside the incoming folder.
   **Import team** can instead reuse a verified CLI manifest and its measurements.
2. The left queue lists teams and quantities. Choose **Templates** for the current
   team's template folder; default filenames follow the existing `_default`
   conventions. Choose **Output** for completed images. **Row CSV** optionally
   selects an external authoritative distribution table.
3. Use **Confirm original canvas & scale** only if the selected team shares the
   fixed capture geometry and extraction preserved canvas and scale. This records
   scale 1.0 for that team; another extraction scale must come from a reviewed
   manifest. The initial desktop cannot recover arbitrary crop/resize metadata.
4. Analyze the selected team. Native Mac detection runs off the UI thread; missing
   or multiple faces remain review items. On other platforms, draw manual heads.
5. Select an athlete. In **Athlete**, drag a **Head box** around hair/accessories.
   Use **Height top** to mark the anatomical head top and **Feet** to mark floor
   contact above the reflection. These two anchors establish relative height in
   common capture coordinates; they do not resize the athlete independently.
   Previously supplied verified relative-height values remain usable.
6. The complete group appears when every included source passes preflight. Use
   **Composition** for spacing, row separation, inverted-V, global size/offsets
   and placement-area controls. Distances are inches, usually 1/8-inch steps;
   subtle V steps use finer increments and row separation uses head units.
7. Change row counts in back-to-front order; use **Move**, arrows, **Taller** or
   **Shorter** for manual assignment. Click/drag an athlete's head in the panorama
   to move it in 1/8-inch steps. Exclude/restore preserves its source geometry,
   height anchors and offsets. Exclusion resets manual rows to automatic counts.
8. Save the job to retain manual edits. The app prompts on closing with unsaved
   changes, and refuses to overwrite job files changed by another writer.
   **Save ▾ → Save a copy** retains your edits when the original job has an external
   conflict. Both editing panels scroll to remain accessible on smaller laptops.

## Export

**Approve & export JPEG** is enabled only for a current successful source/layout
preview with valid templates. Conservative face-overlap warnings require explicit
review confirmation. Out-of-area placements block export and cannot be approved
away. Source-level failures cannot be overridden into a partial team photograph.

The worker rechecks manifest/layout/source hashes and template versions against
the reviewed preview, then renders/verifies a 7800 × 2400 / 300-PPI JPEG.
Numbered filenames refuse overwrites and intentionally allow a new version on
another deliberate export. Source images stay unchanged. The job's export history
records successful exports; save the job to retain it.

During export the app defers editing actions and waits before closing. A file or
network failure leaves a readable error and no false completed record. Filesystems
without the renderer's required atomic hard-link publication fail explicitly.
Retry/recovery, multi-destination delivery and print idempotency remain TB-004.

## Implementation and validation

- `review.py`: UI-independent job persistence and non-destructive commands.
- `measurements.py`: bounded per-worker source/body/head geometry cache. Current
  file version and detector identity invalidate measurements; final export hashes
  the actual decoded bytes. No full-size PNGs remain in this measurement cache.
- `desktop_engine.py`: one-worker analysis, bounded 64-MiB thumbnail cache,
  1560 × 480 previews, template cache and reviewed-export signature.
- `app.py`: Tk widgets only on the main thread, worker results polled with `after`,
  stale preview results closed/discarded, and one pending replacement request.

Layout-only edits reuse measured geometry and decoded small previews. Changing a
manual head reuses source bounds; changed files invalidate caches. Cache rebuilds
remain CPU/image-I/O work, so first analysis can take longer than later layout edits.

Linux CI exercises the actual Tk window under Xvfb with synthetic images: open a
job, display a team, change composition, save, deliberately export a full JPEG,
and capture a synthetic screenshot as an artifact. Cross-platform tests cover the
model, persistence, cache reuse, file-change preflight and native Mac helper.
No private customer photography is sent to CI. Real image/head accuracy and native
Mac desktop usability still need field validation before release.

The interface follows the supplied VolumePilot guide's navy/royal/orange/sky/cloud/
instrument palette, sparse orange actions and Inter-first typography with native
fallbacks. This milestone uses text branding; a compact approved logo variant,
native application icon, production packaging and final Mac accessibility review remain open.

## Apple Silicon preview bundle

macOS CI uses pinned PyInstaller 6.22.3 to build an arm64 `.app`, bundling the
native detector, authoritative row CSV and desktop runtime. It checks the bundle's
ad-hoc signature and launches the actual frozen binary with a hidden startup smoke
test before producing a symlink/permission-preserving ZIP. The artifact contains
that ZIP; extract the outer artifact download, then the inner ZIP to obtain the app.

No Python or Xcode installation is required on the test Mac. This is not a
notarized production release and may require an explicit macOS security allowance.
Private photography still needs local field validation. For reproducible builds
on an Apple Silicon Mac with Python and Xcode tools:

```sh
python -m pip install -e . PyInstaller==6.22.3
python scripts/build_macos_preview.py
```

The builder intentionally fails on other architectures rather than mislabeling
an Intel binary. Source installations use the repository helper; frozen apps use
the helper inside the bundle. Demo assets persist under application support so
a saved demo job does not break when the application closes.
