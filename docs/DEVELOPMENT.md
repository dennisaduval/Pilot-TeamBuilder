# Pilot TeamBuilder ongoing development record

## 2026-11-26 — TB-001 / 0.1.0.dev0

### Goal and production contract

Build a separate offline production module for hundreds of composite teams.
The final panorama is landscape 26 × 8 inches / 7800 × 2400 at 300 PPI. Left
24 × 8 inches is artwork, right 2 × 8 is a rotated team identification strip.
Only one JPEG is rendered for later upload/sample routing. Athlete placement is
22 × 6 inches, initially x=1–23, y=0.75–6.75 inches (center 12,3.75).
All source photography must remain unchanged. Production defaults should be
easy to use with large previews and saved job/template settings.

### Repository review and provenance

- Reviewed `dennisaduval/VolumePilot` main at
  `01891a60e2417a7e72d8cebdd5b9c51770ea2092`: root README and architecture
  decisions 0001 and 0006. Capture uses .NET/Avalonia, local SQLite, ULIDs;
  HQ integration uses versioned contracts and offline idempotent operations.
- Reviewed `dennisaduval/Pilot-PanoBuilder` main at
  `58da7ef9f18deab9b4b2f79167eb6e313a09ef5e`: pyproject, contribution workflow,
  template catalog, face geometry, image engine, print layouts, model, workflow.
  Its current Python/Pillow baseline is a practical fit for the image engine.
- Reused/adapted PanoBuilder's font fitting and rotated right strip drawing in
  `identification.py`. JPEG encoding retains quality 95, no chroma subsampling,
  and 300-PPI metadata. Its decode cache design informs the two-layer cache.
- PanoBuilder's portrait head normalization and single-best-face detector are
  deliberately not used for team scaling or detection: both conflict with
  preserving physical proportions or identifying multiple heads for review.
- Reviewed local Pilot Hotfolder config, files, samples, and engine conventions:
  per-job folders, staged publication, stable snapshots, SQLite history. These
  are future workflow adapter candidates, not dependencies of the pure engine.
- Repository access uses the connected GitHub interface; direct shell Git/network
  access cannot resolve DNS in this environment. Local files are an additive
  module staged for a feature branch, not a full checked-out VolumePilot tree.
- The recovery ZIP could not be downloaded because of shell DNS restrictions;
  current PanoBuilder source was reviewed directly through GitHub instead.
- The supplied row CSV was read in full from its current source. The packaged
  table preserves all 45 records, including 17=(8,9), 33=(12,11,10),
  39=(14,13,12), 42=(15,14,13), and 45=(15,15,15).

### Architectural decisions

Separate Python package at the root of `dennisaduval/Pilot-TeamBuilder`, with pure serializable
domain geometry, a pure composition module, filesystem/template/render adapters,
and a development CLI. No changes to existing applications or shared runtime
dependencies. Keep future UI, detection, persistence, and routing behind adapters.
No cloud calls or model downloads are required by this milestone.

`source_scale` means extraction pixels per original common capture pixel.
Crop translation alone does not change that scale. A different resize requires
explicit metadata; unknown values block composition. `relative_height` is a
validated ranking measurement supplied by the caller, not inferred from canvas
or alpha height. The detection/metadata adapter must establish both later.

Source coordinates must be EXIF-oriented pixels. Body bounds contain the full
visible silhouette; head bounds include hair. Current head rectangles also serve
as conservative face-protection regions; a later adapter must distinguish
anatomical faces from hair, eyes, shoulders, feet, and reflection boundaries.
Inputs with multiple heads are held for correction, never silently reduced to
the largest face. The renderer verifies source dimensions and alpha bounds.

Normalize source resizing back to capture coordinates, then apply exactly one
team scale to every normalized athlete. Global scale is limited by both full
body bounds and configured placement bounds. Natural starting size uses median
body height only to choose a common group size; it does not rank or individually
resize athletes. Smaller teams remain centered. Group scale is a fit fraction
in (0,1]. Offsets reserve space within the area; manual offsets that escape it
produce explicit review issues. UI controls should use 1/8-inch steps; subtle
V step remains finer (0.025 inch default, 0.25-inch edge cap).

Head spacing is a requested minimum with a safety floor of 1.1 × the widest
normalized head. Vertical row separation is 1.5 × median normalized head height.
Equal-count rows alternate by half a head-spacing step; otherwise rows center.
The whole visible group, including staggering and wide poses, is centered and
scaled together. Exact final head spacing can shrink when the group must fit.

CSV extension above 45: start with the last defined distribution. Add one
athlete to the shortest row, with ties favoring the backmost row. When every row
reaches 16, add a row and evenly redistribute the new total, assigning remainders
back-to-front. Thus 46=(16,15,15), 48=(16,16,16), 49=(13,12,12,12),
65=(13,13,13,13,13). This is a deterministic extrapolation; exact CSV entries
always win. Missing entries below the largest defined count are errors.

Rendering decodes one athlete at a time and caches at most two artwork layers.
Cache ownership is single-worker; parallel team jobs need separate caches and
a bounded queue. Template ratio must be exactly 3:1; lower-resolution templates
are resized for synthetic use. Production preflight should flag upsampling.
Publication stages and fsyncs the JPEG, verifies decoding, then atomically links
it into place without replacement. Filesystems without hard links fail visibly;
network-safe delivery is a future routing concern. Existing files always block
publication, including deliberate rebuilds until versioned destinations exist.

### Validation

- 23 unit/integration/regression tests pass on the current host, Python 3.13.1,
  Pillow 11.3.0. All CSV team sizes plus 46,47,48,49,64,65,100,257 are exercised.
- Mathematical checks: all source-body transforms fit the area, per-athlete
  source normalization followed by common scale, exact row totals/capacity,
  deterministic assignment/order, centering, staggering, V and row separation.
- Regression fixture records the exact nine-athlete rank order and geometry.
- Integration verifies 7800 × 2400 RGB JPEG, 300 PPI, nonblank identification
  strip, foreground layering, unchanged source hashes, no overwrite, cleanup,
  source geometry mismatch rejection, template cache, invalid templates.
- Existing local Pilot Hotfolder suite: 13 tests pass; no source changes there.
- Synthetic 42-athlete full render succeeds with stage timings. This benchmark
  uses small synthetic PNGs and is not a production speed estimate.
- Python 3.13 CI tests passed on Linux, macos-latest and Windows for engine
  revision `be287fa709e87ffab13a3fdacc4c8d0033dd7748` in draft PR #25.
  macOS runner testing is not Apple Silicon photographic field validation.
- Initial Linux CI passed. The macos-14 job was cancelled before tests ran;
  its annotation is not available through the connector. Use macos-latest for
  the current supported runner; do not infer a code failure from that cancellation.

Repository handoff: `https://github.com/dennisaduval/VolumePilot/pull/25`, branch
`feat/teambuilder-tb001-foundation`. Changes are additive and unmerged. Broader
repository .NET/HQ checks were still running when this record was updated.

### Known limitations / outstanding work

TB-001 is a foundation milestone, not field-ready software. No production UI,
hotfolder watcher, database, crash recovery, detection model/helper, persistent
detection cache, network router, print deduplication or packaged Mac app exists.
Manual correction data is immutable and serializable, but there is no operator
editor or general-purpose saved-job loader yet. CLI is synthetic-demo-only.

Face preflight uses conservative head-vs-body rectangles; wide body boxes can
create false positives. It reports review issues rather than guaranteeing face
visibility. Renderer can write a flagged draft; no automatic approval/routing
exists. Add silhouette-aware face visibility before any unattended routing.
Do not treat bounding-box fit as proof of photographic quality.

Real cropped/resized input needs original geometry metadata, original captures,
or operator-calibrated capture scale. Current explicit scale/height values are
synthetic/manual and do not claim automatic physical-height measurement.
No Core ML/Metal/Vision acceleration or production benchmark has been attempted.
Template settings, ICC color management and very-long-name font sizing need
production validation. A single enormous PNG can still require substantial
memory; add pixel/memory budgets before accepting untrusted unattended inputs.

### Next independently testable milestones

1. **TB-002 Detection and source manifests:** versioned job/team manifest loader,
   all-head Apple Vision adapter with CPU fallback, alpha/body/hair geometry,
   capture crop/resize provenance, reduced-resolution coordinate mapping,
   confidence flags and content/model-version keyed persistent detection cache.
   Add missing/multiple-head, scale ambiguity and crop metadata fixtures.
2. **TB-003 Preview/review UI:** large panoramic preview, job/queue/status header,
   navy/royal/orange/sky/cloud/instrument palette, Inter fallback; 1/8-inch controls,
   head/height editing, rank/row/slot moves, exclusion/restore, individual offsets,
   saved template overrides, cached-layout rebuild, approve and intentional reprocess.
3. **TB-004 Production:** stable-folder/ready-manifest ingestion, validate every
   image, checksum deduplication, SQLite state/leases and crash recovery, job paths,
   single render with verified multi-destination routing, upload/sample/processed/
   review/log folders, no overwrites, no duplicate printing, interruption and
   disconnected-network fault tests. Idle-time alone is insufficient proof of
   a complete network transfer; support an explicit producer-ready handoff.
4. **TB-005 Field validation:** actual athlete PNGs, matching original captures,
   templates, expected composites; validate height accuracy, alpha-aware face
   visibility, realistic scale, colors, speed/memory on Apple Silicon, extended
   unattended events. Package a signed/notarized Mac application after validation.

Future HQ exchange should include schema revision, job/event/group/subject IDs,
installation identity, immutable source hashes and stable idempotency IDs, with
a local outbox. Do not share HQ database models or make field work cloud-dependent.

## 2026-11-26 — Dedicated repository migration

The canonical project repository is now `dennisaduval/Pilot-TeamBuilder`.
The source was transferred directly from VolumePilot PR #25 at pinned head
`64269d0a532c231d3f1d53ef4156fb8e11fa4245`.
Source, tests, CSV and pyproject move unchanged from `apps/PilotTeamBuilder/`
to this repository root. Only README/development paths and CI working-directory/
trigger configuration change. The new repository's existing .gitignore is retained.

The local computer connection became unavailable during migration, so the
transfer uses GitHub directly. Do not assume a new local checkout was created.
Run validation through this repository's Linux/macOS/Windows CI.

Use feature branches and reviewed pull requests into main. The historical
VolumePilot PR #25 is superseded by the foundation PR in this repository;
its branch is retained as a historical reference. Do not merge it into VolumePilot.
Future work resumes here with TB-002 (manifests, offline detection and cache).
The earlier milestone record describes the original implementation history.

## 2026-11-26 — TB-002 / 0.2.0.dev0

### Implemented increment

- Strict schema-v1 manifest parser, recursive PNG inventory and explicit
  per-athlete capture scale/relative height/geometry/provenance. Unknown values
  remain review items rather than canvas-based guesses. See MANIFEST.md.
- Manifest-to-analysis-to-layout CLI, optional single draft JPEG, complete
  per-athlete results, explicit exclusions, manual head/row/inch-offset records,
  duplicate-content and unlisted-PNG checks. All active inputs must pass source
  preflight before layout. Layout collision warnings remain review flags.
- Offline native Swift Vision helper, native-architecture macOS build script,
  all-face observation contract, revision 3, default automatic compute selection
  and explicit CPU retry. Reduced-resolution RGBA preprocessing with neutral
  matte and independent x/y source-coordinate mapping.
- Hair-inclusive alpha head estimates near detected faces, truncated-window
  review checks. Anatomical face rectangles remain separate in analysis reports.
- SQLite WAL cache keyed by source SHA-256, helper/OS/compute identity and
  preprocessing version. Cache survives restart; source/model changes invalidate;
  manual corrections preserve raw detections; failed requests are not cached.
- Bounded immutable PNG snapshots, transparency/dimension validation, source
  content hashes checked against bytes actually decoded during final rendering.
  Existing renderer now uses the same safe loader.
- Tests exercise preflight, metadata uncertainty, coordinate mapping, CPU retry,
  all-face preservation, cache reuse/invalidation/corruption/restart, duplicate
  input, exclusions, source changes, full CLI rendering and review exit codes.

### Validation and operational limits

51 tests discovered locally with Python 3.13 and Pillow 11.3.0: 50 pass; one native
macOS helper smoke test is skipped on Linux. CI builds the native helper and
runs default/CPU blank-image smoke tests on macOS; these do not establish actual
face detection accuracy. Initial revision f348286 passed all 49 then-current tests
on macOS CI, including compiling the Swift helper and default/CPU blank-image
execution; Windows/Linux CI also passed (native-only test skipped). Final revision
results are tracked in [PR #2](https://github.com/dennisaduval/Pilot-TeamBuilder/pull/2).

Final review also added a regression for tiny cutouts in very large transparent
canvases: render crops to actual alpha bounds before scaling, preventing enormous
temporary allocations while retaining source-coordinate placement. The PNG loader
converts Pillow decompression-limit failures into preflight issues. Extreme source
scale values produce explicit review errors instead of numeric overflow failures.

No actual athlete photographs are available. No automatic physical-height
estimation, crop metadata recovery, eye/shoulder/foot landmark adapter or
silhouette-aware face occlusion solver has been validated. Capture metadata must
be provided/reviewed explicitly. Automatic detection is Mac-only; other platforms
can use reviewed manual-head records. Python adapter tests use injected synthetic
faces; production confidence thresholds/hair bounds need real sample validation.
The Swift source is compiled/tested on macOS CI, not on this Linux workstation.
Native binary signing, notarization and app packaging remain future milestones.

The initial repo foundation PR #1 remains unmerged. Develop this increment on
its feature head with a stacked PR so foundation review can remain independent.
Merge #1 first, then retarget the TB-002 PR to main before merging it. No existing
VolumePilot/PanoBuilder application or main branch is modified by this increment.

Continue with TB-003: production desktop review UI, saved jobs/templates, large
panoramic preview and correction tools; keep source preflight/calibration visible.
Before unattended routing, complete TB-004 stable handoff/state/recovery/print
idempotency and refine alpha-aware face visibility. Real photographs/templates
will validate quality and timing when available, without blocking UI development.

### Native API references

- https://developer.apple.com/documentation/vision/vndetectfacerectanglesrequest
- https://developer.apple.com/documentation/vision/vnrequest/supportedcomputestagedevices
- https://developer.apple.com/documentation/vision/vnrequest/setcomputedevice(_:for:)
- https://developer.apple.com/documentation/vision/vnrequest/usescpuonly

Modern CPU assignment selects a supported CPU device per compute stage on macOS
14+. The legacy property is only used on macOS 13. Vision chooses its own devices
in default mode; this is not a promise that each operation uses a particular GPU
or Neural Engine. Benchmarking with real athlete PNGs remains necessary.

## 2026-11-26 — First real-image sample inspection

Actual private photography is now available; the earlier statement that samples
were unavailable describes the initial TB-002 implementation only.

- Inspected 44 extracted RGBA PNGs in four team folders, with counts 11, 11, 5,
  and 17. All decode successfully at 4000 × 6000 (24 MP); no content duplicates.
- The 17-image team exercises the exact (8,9) two-row CSV configuration; the
  other three teams exercise single-row configurations. All photos retain the
  same camera-model and 50-mm focal-length metadata. Matching EXIF is supporting
  evidence, not proof of identical distance, crop or physical height.
- The operator explicitly confirmed the original canvas and extraction scale
  were retained. Source scale 1.0 is therefore appropriate for THIS sample set;
  it must not become an unverified default for future jobs. Private local
  manifests record that confirmation; relative-height measurements remain unset
  pending face/head/foot validation. No height is inferred from raw alpha height.
- Visible cases include head bows, floor reflections, wide arms/poses and large
  relative athlete-size differences. Raw nonzero alpha reaches the canvas bottom
  in all 44 images, confirming that reflection extent is unsuitable as a foot
  anchor. Body content must be distinguished from reflections and accessories.
- Twenty PNGs contain faint alpha whose bounds differ by more than 32 pixels
  from an alpha>8 support mask; largest measured extent difference is 1175 pixels.
  These are extraction-residue diagnostics, not invalid images. No faint pixels,
  reflections or accessories were removed or altered during inspection. Keep
  raw/supported/anatomical bounds distinct before refining layout containment.
- Added a private local sample inspector with selected EXIF hints, alpha-threshold
  diagnostics, hashes, counts and per-image results. It never emits GPS, authors,
  camera serials or raw XMP. Full decoding plus hashing/diagnostics took about
  27 seconds on the Linux development host; this excludes face detection/rendering
  and is not an Apple Silicon production-speed estimate.
- Fixed source discovery so Finder metadata and AppleDouble archive sidecars
  cannot become extra athletes or invalidate an otherwise complete manifest.
  Ordinary hidden PNGs remain included, and invalid real PNGs stay visible as
  errors. Inventory and preflight use the same discovery rules.
- Validation: 56 tests discovered locally: 55 pass; native-Mac test skipped on
  Linux. Five new tests cover metadata sidecars, hidden athletes, end-to-end
  preflight consistency and non-destructive/private sample inspection. The samples
  themselves remain outside Git. Only anonymous aggregate findings and synthetic
  regression fixtures are committed; no customer images or private reports.

Native Vision cannot execute on this Linux host. This inspection does not claim
real-photo face/head accuracy or approved composite quality. Continue with the
review interface and run these private samples through the native detector on a
Mac when the application is ready; never upload customer photos to public CI.

## 2026-11-26 — TB-003 / 0.3.0.dev0 — Initial desktop review

Implemented a separate Tk review layer using PanoBuilder's existing desktop
pattern: main-thread widgets and queued background image work. Reviewed the
current PanoBuilder app and the supplied VolumePilot Brand Style Guide v2 before
implementation. Reused the existing composition, validation, detection, CSV,
template and final rendering modules; no changes to other VP applications.

Features: saved jobs/recent-job selection; team queue/status/counts; large actual
panorama preview and placement overlays; athlete list; visual hair/head rectangles
and anatomical top/foot anchors; manual height ranks, row distributions, row/slot
moves, exclusions/restoration, 1/8-inch individual dragging; group settings in
inches; per-team templates and remembered template composition presets; verified
numbered full-JPEG export and saved export history. See DESKTOP.md.

Added a bounded geometry cache so layout and metadata edits do not repeatedly
decode all PNGs. It stores only dimensions, bounds, heads and source hashes, keyed
by current file version and detector identity. Small preview assets have a strict
64-MiB LRU budget; one worker owns both caches, and at most one replacement task
waits behind the active task. Stale results are closed rather than displayed.
Disk face caching is retained. Manual measurement failures never become approved
metadata or cause silent image omission.

Export rechecks a signature over the actual manifest, layout, source hashes and
template versions. The renderer additionally checks template versions before and
after rendering and source hashes against the decoded bytes. Manual out-of-area
placements block export; conservative overlap warnings may be explicitly reviewed.
Saved jobs use staged atomic writes and reject conflicting external edits.

Local validation: 73 tests discovered, 71 pass, native-Mac and graphical-display
tests skipped on this Linux development session. New tests cover job round trips,
external file conflicts, source-normalized height anchors, non-destructive restore,
manual rows/ranks, template presets, measurement/thumbnail reuse, cache bounds,
stale-template export rejection, full-size export, out-of-area export rejection,
bounded worker replacement, startup retry and an actual Tk review/export flow.
The GUI smoke runs separately under Xvfb in Linux CI; remote CI results and the
synthetic screenshot artifact are linked from the TB-003 PR. Local Xorg initialized
but this command environment could not connect a GUI client; no local visual
inspection is claimed until the CI smoke/screenshot is reviewed.

Initial revision d8fab39 passed Linux, Windows and Apple Silicon Mac CI. The
actual Linux Tk open/edit/save/export smoke also passed; its synthetic screenshot
is retained as a workflow artifact. Follow-up adds verified manifest import and
an Apple Silicon preview bundler (PyInstaller 6.22.3), bundled helper/CSV resolution,
signature verification and frozen-app startup smoke. The bundle uses an ad-hoc
signature and is not Developer-ID signed/notarized. Packaging results are tracked
in PR #3; do not claim an event release or private-photo Mac validation.

Preview revision 7574a64 built successfully on the native arm64 macOS-26 runner,
passed bundle signature verification and launched the frozen desktop with its
bundled row table/helper. CI uploaded the resulting ZIP; no Python/Xcode setup
is required on the test Mac. The Linux GUI smoke and Windows/Mac tests also passed.
A Demo toolbar action provides the same synthetic review/export flow without CLI
commands. Final artifact/check URLs are kept in PR #3's description.

PR sequencing: this increment is based on PR #2 head 7e664ed, while #1 and #2
remain unmerged. TB-003 is a stacked PR against #2's feature branch. Merge #1,
retarget #2 to main and merge it, then retarget TB-003 to main before merging.

Limits: first developer desktop preview, no notarized production Mac app; no automatic
head-to-floor physical-height estimation or arbitrary crop/resize calibration;
no alpha-residue cleaning or silhouette-aware face solver; no hotfolder processing,
route/print queues, crash recovery, job database or multi-destination approval.
Network destinations are selected mounted folders. The initial manual review
flow requires verified calibration and anchors/height values. Preview/source
validation remains conservative. Real photo/Apple Silicon UI field tests are still
needed using the private sample archive. Continue with desktop refinement and
TB-004 production workflow rather than treating this version as event-ready.


## 2026-11-26 — TB-003 / 0.3.1.dev0 — Folder selection and live layout

User field feedback: "Import team" misleadingly required a JSON manifest and hid
direct PNG selection under "New job." Main toolbar now explicitly offers
Choose team folder and Open saved job. The advanced manifest importer remains
available under its accurate name. Direct folders or parent collections load
without JSON; the source list populates before background analysis completes.
Adding an existing source selects it without overwriting manual corrections;
distinct same-named source folders receive distinct job IDs. A real-folder
selection from the synthetic Demo starts a real job with fresh paths.

The large panorama now has Layout/Athletes tabs underneath. Layout follows the
user-supplied simulator reference: included athlete count, requested rows,
back-to-front distribution, and live sliders for row separation, inverted-V,
head spacing and group scale. Advanced inch controls and visual head/height
corrections remain available. Athlete count reflects actual included files.
Auto remains authoritative CSV behavior. Explicit requested rows are a saved
manual override: divide count by requested rows, put remainder backmost first,
validate nonempty rows and capacity 16. Exclusion returns to Auto. No source is
silently omitted and no anatomical height estimator has been invented.

Slider changes debounce for 180 ms and reuse existing geometry/thumbnail caches.
Pending changes disable approval, flush before save/team changes/close, and
require an updated preview before export. Tests added for direct directory
selection without a JSON dialog, preserving edits, same-name identities, valid
requested distributions through 257 athletes, saved override validation, cached
rebuilds and actual live-slider/row-picker interactions under Xvfb. Cross-platform
CI and the rebuilt native bundle are the validation gate for this increment.
Real photography still requires reviewed head/height data before composition.
