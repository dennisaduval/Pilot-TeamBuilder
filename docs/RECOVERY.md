# Live inventory and finished-image recovery

Folder discovery runs on its own metadata-only worker every **10 seconds**. It runs
while setup is paused and while rendering is busy; it never starts exports by itself.
Copy-settle/ready-marker checks still decide when production may safely render.

Additions appear automatically. Deleting a PNG removes its inventory entry and stale
corrections. Renaming a file preserves annotations when a stable filesystem identity
is available. The content-based production identity prevents a rename from causing
another render/print. Exact duplicate image bytes are rejected regardless of filename:
remove or exclude the copy to resolve that error. Adding a genuinely different athlete
creates a new composition version.

Exclude/restore can be used even when an invalid PNG prevents a layout. During a busy
automatic tick the app briefly pauses/cancels safely, applies the saved exclusion and
resumes the watcher. Preview-only setup remains paused. Manual edits to a completed
team use deliberate Rebuild before a new delivery, preserving duplicate-print safety.

The authoritative JPEG is stored locally in the job's application-support state,
under `Production/<job-id>/Masters`. These durable masters are separate from the
bounded disposable raw render cache. Configured Completed/Upload/Samples folders
receive independently verified copies, tracked once in the delivery ledger. Programs
may move or consume exported copies without making the protected master invalid.
Use **Job → Open finished image** to view the retained image.

For older receipts or a missing/corrupted protected master:

1. Look for an unchanged, byte-verified copy at its completed or recorded destinations.
2. Restore that copy into the protected local master store.
3. If no copy remains, rebuild a local image from available original sources and
   current settings. A notice identifies changed-settings recovery.
4. Keep existing completed delivery receipts; do not resend old printing copies.

Recovery retains changed local files separately and never overwrites untracked
external files. Network writes retry while the local master remains available.
A publication interrupted after a print destination may have consumed its file is
still ambiguous and requires operator review; it is never automatically resent.

Version 0.9 adds the confirmed waist-up to three-quarter fitting window. The new
fit does not invalidate completed delivery receipts or automatically resend prints.
Use deliberate Rebuild to deliver a completed team with the new sizing rules.
