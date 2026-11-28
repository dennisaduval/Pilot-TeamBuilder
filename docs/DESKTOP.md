# TeamBuilder 0.8.0 — automatic team production

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
2. **Preview team…** selects one team's PNG folder, independently of Incoming.
   It may be outside the hotfolder; external sources are marked **Preview only**
   and never exported automatically. Existing corrections are retained on re-selection.
3. **Job setup…** selects templates, row CSV, output and optional upload/printing
   destinations. Its preview-team field can also select the setup example. Choose
   **Save & preview** to return to the preview with production still paused.
4. Tune job-wide vertical spacing, athlete overlap, hero step, group size and
   position against the preview. Keep **Player PNGs keep their original size**
   enabled for the confirmed original-canvas capture workflow.
5. Explicitly press **Start automatic processing** when satisfied. The watcher
   waits for stable folders, places/renders each team, and routes its verified JPEG.
   **Pause** prevents new work; a team already publishing/delivering finishes safely.

Opening jobs, choosing folders and saving setup never start watching automatically.
A preview inside Incoming becomes a normal team when you explicitly start; a
preview outside Incoming is excluded from all automatic output/routing.

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

The **Layout** sliders set job-wide row separation, inverted-V step, athlete overlap (15% initially),
athlete size (25–300%), and visible **Team up/down** and **Team left/right** offsets
in 1/8-inch steps. **Center guides** show the artwork horizontal center and the
placement-area vertical center; guides never appear in exported images.
Area controls remain under Advanced. **Auto**
follows the selected CSV exactly; requested/manual rows are explicit per-team
exceptions. Layout edits reuse geometry and thumbnail caches. Pending edits
invalidate export until the new preview is ready.

Fast transparency placement is the default. Athlete PNGs remain intact: the foreground hides lower bodies. There is no crop
checkbox and athletes are not clipped to the placement rectangle. Only the outer
24 × 8 artwork boundary limits rendered pixels, protecting the identification strip.

The center athlete is the hero. Slots are assigned tallest-first, center-out, and
painted outside-first so the hero appears in front. Each successive outward slot
has a lower silhouette top. The center-to-edge cap distributes the slope across
all slots, avoiding a flat outer section. Even rows share the two center heights,
with the taller center painting last. Exact incoming pixel scales are retained;
no athlete is individually enlarged, shrunk or stretched. Under the confirmed
fixed-capture workflow, the silhouette's top coordinate supplies the height rank.

One standard pitch is based on typical silhouette width and the job's **Athlete
overlap** percentage (15% initially). Each team independently chooses its largest
allowed scale. Its front row's tallest silhouette supplies a three-quarter-height
size reference; multi-row framing starts 10% smaller. Width and the top boundary
can reduce that reference, preserving the front-row anchor 1/8 inch above the center
guide. A previous team's size does not restrict later teams. The UI names the
limiting boundary when athletes cannot grow further. See [fitting rules](FITTING.md).

Every athlete in a team always shares one identical scale. Small teams stay centered
rather than spreading unnaturally. Normal moves are fitted automatically. Full lower
bodies extend behind the foreground. **Reset placement** clears selected-team offsets
and job X/Y offsets, preserving other edits and completed receipts. **Reset automatic
fit** restores 100% group size; it does not calibrate later teams from the first one.

Existing jobs migrate once to fast transparency placement and a visible hero step,
preserving corrections and delivery history. Placement is based on alpha geometry,
not anatomical head detection: bows, hair, raised arms and stray extraction pixels
can affect ordering. Use similar poses/clothing and the optional correction tools
where needed. The foreground determines visible body length; there is no lower-body
crop. Center guides never appear in finished JPEGs.

Changes affect previews and queued work. Completed deliveries are not repeated
when settings change. **Job → Rebuild selected team** deliberately creates a new
version and may deliver/print again after monitoring starts. This is intentional,
not an automatic retry of completed output.

## Automatic measurements and exceptions

Fast mode decodes each PNG once to get alpha bounds, a top-band center and a
relative height rank. It never invokes Vision, requests a head box or rejects an
athlete for face confidence. The small top band is a placement guide, not a claimed
anatomical head measurement. Body overlap is intentional, so fast mode does not
hold teams on speculative face-obstruction tests. It cannot certify face visibility
or discover a multi-person source; use optional face mode when that verification
is required. No sources are silently omitted.

Disable **Fast placement from transparency** in Job setup for the previous face-based
workflow. Its single-face confidence threshold and sampled face-coverage checks
remain available. The default relaxed face threshold is 0.5. Manual head corrections,
height ranks, exclusions, row/slot assignment and offsets remain non-destructive.

Invalid/duplicate PNGs, changed fixed-capture canvas geometry, missing/ambiguous
templates and unavailable destinations still have concrete recovery messages.
Held teams retry when their corrections, settings or templates change. Completed
deliveries stay protected from repeats; intentional Rebuild creates a new version.

## Performance

Automatic production reuses its calculated layout and does not build a preview
image on the processing worker. Selected teams still have previews in the UI.
A bounded disposable **2-GiB local disk cache per worker** stores decoded RGBA
silhouettes in original coordinates. It does not alter incoming PNGs. Final render
verifies the current original PNG hash, memory-maps one cached athlete at a time,
and resamples only artwork pixels. Cache eviction/unavailable temp storage falls
back to verified original PNG loading. Cached and uncached JPEGs are byte-identical,
including crash-recovery rerenders. The cache is removed when its worker closes.

Preview assets are separately bounded to 64 MiB. Live edits reuse geometry and
thumbnails. Source measurement, rendering and cache-hit timings appear in structured
production logs. The 20-second copy-settle interval is separate from processing time.

For a read-only local loading comparison after installing the package:

```sh
python scripts/benchmark_png_loading.py --team '/path/to/team' --limit 4
```

The baseline excludes face detection. Results depend on PNG size, storage and hardware;
this comparison does not measure Apple Vision or other software.

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
