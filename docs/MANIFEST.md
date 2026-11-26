# Team manifest v1

This developer-facing contract connects extracted PNGs, capture provenance,
detections and the composition engine. Inventory does not guess physical height
or assume images were extracted without resizing. The saved manifest is the
source of truth for athlete IDs and manual corrections; use inventory to start
a new team, not to overwrite an edited manifest.

```json
{
  "schema_version": 1,
  "team_id": "team-001",
  "team_name": "Example Team",
  "source_folder": "./Extracted/Example Team",
  "athletes": [
    {
      "id": "athlete-001",
      "file": "Example_Player_team.png",
      "capture": {
        "geometry_id": "station-10-fixed-camera-calibration-1",
        "source_scale": 1.0,
        "relative_height": 1650.0,
        "provenance": "Operator verified original capture and extraction resize metadata"
      },
      "manual_head": [155, 380, 245, 470]
    }
  ],
  "settings": {"head_spacing_in": 0.75, "x_offset_in": 0.125},
  "corrections": {
    "rows": [["athlete-001"]],
    "offsets_in": {"athlete-001": [0.125, 0.0]}
  }
}
```

The numbers are illustrative, not a real calibration. All entries must reference
distinct nonempty IDs and relative PNG paths inside the selected source folder.
Relative source-folder paths resolve against the manifest's parent. All PNGs
under that folder must be listed, including deliberately excluded files. Unknown
fields, schema versions, non-finite values and path traversal are rejected.
No symlink input paths are allowed. PNG decode budgets are 128 MiB compressed
and 64 megapixels decoded; animated, fully transparent and invalid files fail
preflight. Alpha bounds include every nonzero pixel for safe group containment.

`source_scale` is **extraction pixels / common original capture pixels**, uniform
in both dimensions. 1.0 is valid only when confirmed. A uniform 50% extraction
resize is 0.5. Cropping translation does not change scale. Unknown/nonuniform
resizing cannot be inferred reliably from head or canvas size. Never assign 1.0
simply to make preflight pass. All included athletes must share a documented
common `geometry_id`; different camera geometry requires calibration first.

`relative_height` is an independently verified ranking measurement in the same
units for the team, such as calibrated head-to-floor span from original captures.
It is not the PNG canvas or alpha-box height. It determines ordering; `source_scale`
restores common capture proportions. Automatic recovery of crop metadata, floor
anchors and physical height remains future work. `provenance` records how these
values were confirmed; it is not automated proof of calibration quality.

Omit `manual_head` to use automatic Mac detection. When provided, it is an
explicit operator-reviewed hair-inclusive head rectangle in **EXIF-oriented
source pixels**, not a detector confidence override. A manual correction replaces
automatic detection for that athlete but leaves raw cached detections intact.
Reviewers must confirm the PNG contains the intended single person. Do not use
manual head rectangles to hide a second person; exclude the wrong image instead.

To exclude a file, set `excluded: true` and a nonempty `exclusion_reason`.
Its result stays visible in the report. Restore it by clearing that flag; source
images and prior metadata remain unchanged. Manual rows contain every included
athlete exactly once in back-to-front, left-to-right order. `offsets_in` is keyed
by athlete ID, in inches. Exclusions are specified on entries, not duplicated in
the corrections object. No general job configuration/editor exists yet.

The detector analyzes a neutral-matted image with maximum edge 1600 pixels,
maps every observation independently back to oriented source coordinates, and
never selects just the largest face. Zero/multiple faces and confidence below
0.8 require review. Hair measurement is an alpha-based estimate near the face;
truncated or exceptionally tall head windows require review. No eye/shoulder/foot
landmarks or photographic accuracy claims are made in this milestone.

The cache key includes SHA-256 of source bytes, native helper bytes, OS version,
compute mode and preprocessing revision. It stores raw face rectangles including
empty results, not images, capture metadata or manual corrections. Invalid cached
JSON is discarded; detector runtime failures are not cached. A successful input
change or helper revision produces a new key. Cache access is per serial worker;
bounded concurrent processing will require a connection per worker.

Final manifest rendering verifies SHA-256 against the very bytes it decodes. A
source change after analysis blocks publication even if size/alpha bounds match.
The output is one full 7800 × 2400 JPEG; `analysis.json` records review issues and
stage timings. Outputs are exclusive and cannot overwrite earlier runs. This CLI
is a preflight/draft tool, not an unattended hotfolder or routing service.
