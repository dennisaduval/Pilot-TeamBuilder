# TeamBuilder 0.6.0 — automatic team production

Pilot TeamBuilder is an offline hotfolder application. Set up a job once, start
monitoring, and drop team folders into the incoming folder. Valid teams render
and deliver automatically; only exceptions need an operator. Source PNGs are
never changed, moved or deleted. This is a developer preview, not an event release.

## Start a job

1. **Choose hotfolder…** selects the incoming folder. It may be empty. Real
   team folders are discovered at any depth. Container folders are not combined
   into one team. Common image wrappers such as Edited PNG/Images keep their
   parent team's name. A folder containing PNGs directly is also supported.
   Folder names are used verbatim as the printed team names, including prefixes.
2. **Job setup…** selects the template folder, optional explicit default background
   and foreground, row CSV, completed-output location, and optional upload/sample
   delivery folders. The bundled row CSV is the initial selection; another CSV can
   be selected for this job. Settings and queue state save automatically.
3. Enable **Player PNGs keep their original size** only for
   the matching capture workflow. Dennis confirmed this for the supplied sample
   batch. This describes player photographs, not template dimensions, and can be
   disabled for resized/cropped source workflows.
4. **Start automatic processing** watches for stable folders, analyzes all included
   sources, checks layout/template quality and exports valid teams automatically.
   **Pause** prevents new work; a team already publishing/delivering finishes safely.

The header displays the actual application package version. **Job ▾** holds saved
jobs, Save a copy, Demo and deliberate Rebuild. There is no manifest-import action
in the production screen. JSON is internal saved state; team lists come from folders.

## Templates and filename colors

Either select the default files directly in Job setup (any names), or use:

```text
Team Background_default.jpg
Team Foreground_default.png
Team Background_Orange.jpg
Team Foreground_Orange.png
Team Background_Aqua.jpg
Team Foreground_Aqua.png
```

Matching is case-insensitive. `_Default` and color/default combinations such as
`Team Background_Orange_default.jpg` work. Backgrounds must be JPEG, foregrounds
must have PNG transparency in the artwork. Each template can be either landscape
24 × 8-inch artwork or a full 26 × 8-inch print sheet, at any matching resolution
(e.g. 7200 × 2400 or 7800 × 2400 pixels). Background and foreground may use different
supported canvas sizes. Minor pixel-rounding differences are accepted.

For a full-print template, the LEFT 24 inches become the artwork; the rightmost
2 inches are excluded from both design layers. The app adds its identification
strip separately, so template content there cannot cover the team name or distort
the artwork. No extra 24-inch JPEG is created. Final output remains 7800 × 2400 at
300 PPI. Source templates remain unchanged. Templates are validated before use and
cached by file version.

Color selection reads the appended FILENAME color suffix, such as `_Orange`, `_Aqua`, `_Green`;
a later `_team` token is allowed. Earlier athlete-name tokens (Green, White, etc.)
never override the suffix. A color in the team folder name has no authority.
When a complete matching color pair exists, it is used. Otherwise the selected
explicit default pair, or the automatically discovered default pair, is used.
The main template label identifies this fallback. Explicitly selecting default
files also permits a consistent default design for mixed-color source labels.
Missing all usable pairs or ambiguous template roles still produces a clear error. Custom colors are discovered
from the template catalog; common colors are recognized even when their pair is missing.
The main screen shows the actual selected pair for the selected team.

## Preview and job-wide layout

The panorama is the main screen. The correction pane is hidden until **Fix a
detection / placement…** or a source selection requests it. **Sources & issues**
shows specific error text, not just a generic review flag. Reliable detections
require no individual approval. A failed team displays every source in a labeled
source overview instead of a blank preview; this is not a finished composite.

The **Layout** sliders set job-wide row separation, inverted-V step, head spacing,
athlete size (25–300%), and visible **Team up/down** and **Team left/right** offsets
in 1/8-inch steps. **Center guides** show the artwork horizontal center and the
placement-area vertical center; guides never appear in exported images.
Area controls remain under Advanced. **Auto**
follows the selected CSV exactly; requested/manual rows are explicit per-team
exceptions. Layout edits reuse geometry and thumbnail caches. Pending edits
invalidate export until the new preview is ready.

Athlete PNGs remain intact: the foreground hides lower bodies. There is no crop
checkbox and athletes are not clipped to the placement rectangle. Only the outer
24 × 8 artwork boundary limits rendered pixels, protecting the identification strip.

Automatic sizing uses a three-quarter-body framing estimate and fits the team's
width inside the placement box. Small teams stay naturally centered. Front-row
head bottoms anchor 1/8 inch above the horizontal center guide, independently of
scale. Multi-row teams start 10% smaller, then shrink further when necessary so
all head tops remain inside the placement box. Horizontal spacing remains adjustable.
Up/down offsets are independent; manual moves outside head/width limits need correction.

These rules were visually informed by the supplied handmade composites. The 75%
body estimate is a sizing target, not a crop or anatomical landmark guarantee;
the foreground determines how much body is visible. Each team uses one global
scale after source-resize normalization, preserving relative proportions. Job-wide
controls apply consistently, while row count and available width can reduce each
team's automatic fit. This replaces the earlier fixed 85% largest-team calibration.
**Reset automatic fit** restores 100% sizing and selects the largest queued team.
Existing jobs migrate once, retaining corrections, source metadata and completed
production receipts. Completed output is never reprinted merely by opening a job.

Changes affect previews and queued work. Completed deliveries are not repeated
when settings change. **Job → Rebuild selected team** deliberately creates a new
version and may deliver/print again after monitoring starts. This is intentional,
not an automatic retry of completed output.

## Automatic measurements and exceptions

Under confirmed fixed camera/original-canvas geometry, reliable single-face
positions rank stature against a COMMON capture-plane reference.
**Relaxed automatic detection** is enabled by default: one face at confidence
0.5 or higher is accepted, and a clipped hair-analysis window does not force
manual review. Turn it off in Job setup for the previous strict 0.8 policy. An anatomical
top estimate from the face excludes hair from height ranking. This is a relative
ranking proxy, not a physical height measurement. Rendered athlete proportions
remain unchanged and everyone shares the same capture scale. Changed canvas sizes,
unknown resizing and missing/multiple/very-low-confidence faces remain exceptions. Their provenance and errors are recorded; no source is omitted.
Held teams retry automatically when their corrections, job settings or template
files change. Unchanged errors do not repeat detection on every poll. Completed
team deliveries remain protected from repeats.
An operator-corrected head box can supply a position-based height estimate in
relaxed mode too; individual height-top/feet markers are optional refinements.
The **Player PNGs keep original size** checkbox describes source photographs,
not template dimensions. Missing/multiple faces, very low confidence, changed
canvases and invalid sources still require correction rather than omission.
Manual height ranks, exclusions, row/slot assignment and individual offsets
remain available for recovery.

Face coverage is checked against actual foreground-athlete alpha at reduced
resolution around the face, replacing conservative full-body-box overlap warnings.
This remains a sampled visibility check; photographic quality needs native-Mac
validation with the private test photographs before production use.

## Transfers, output, recovery and delivery

The default settling interval is 20 seconds without ANY file-list/size/mtime/ctime
change. Temporary transfer files block a team. Source versions/hashes are checked
again during rendering and immediately before publication. A stability interval
cannot prove that a copy paused longer than the interval has finished. For an
unambiguous handoff, enable **Require team.ready**, and create that marker only
after copying all files (or atomically rename a completed folder into Incoming).
The quiet interval still applies after the marker arrives.

One production worker renders at most one team per poll; a separate bounded
preview worker keeps selection responsive. Geometry, small previews and source
hashes are cached with file-version invalidation. Sources are decoded serially
for full rendering. No network service or model download is used.

A SQLite ledger records input identities, processing ownership, output receipts
and per-destination publication. The master is one verified 7800 × 2400 JPEG at
300 PPI; optional destinations receive byte-verified copies of that same file.
Outputs use deterministic content-derived names, never overwrite existing files
and never repeat completed deliveries. A printer consuming its file does not
cause an automatic resend. Recovered interrupted masters are re-rendered to a
separate candidate and compared before reuse. Missing/changed completed masters
are held rather than automatically reprinted. Disconnected destinations retain
verified masters and retry with a delay.

An interrupted print publication can be ambiguous: if the published file is gone,
it might already have printed. That case is held for operator resolution rather
than risking duplicate printing. Network filesystems lacking atomic hard-link
publication fail explicitly. There is no automatic source-folder archival in this
increment; originals remain in Incoming. Keep completed and delivery folders
outside Incoming. Structured logs and the durable ledger are under application
support for the incoming job.

## Validation and current limits

Tests cover automatic no-approval composition, transfer settling/ready markers,
late source changes, color matching/missing colors, shared scale, empty hotfolders,
full JPEG dimensions/PPI, routing, consumed print files, duplicate prevention,
interrupted output recovery and exception source previews. Native Apple Vision
and the bundled arm64 app are checked in macOS CI; the real Tk UI is exercised
under Xvfb in Linux CI. Private customer photography is never sent to CI.

The Apple Silicon bundle targets macOS 13+. It is ad-hoc signed and unnotarized.
Extract the GitHub artifact ZIP and its inner ZIP to obtain the app. No Python or
Xcode installation is needed. This increment does not certify real-photo stature
ranking, floor/reflection treatment, face visibility or production throughput.
Arbitrary crop/resize calibration and automatic source archival remain future work.
