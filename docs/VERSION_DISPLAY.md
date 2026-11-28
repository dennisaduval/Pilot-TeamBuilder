# VolumePilot module version display convention

Every Pilot module should show its product name and actual package/build version
in a persistent header or footer, including offline operation and screenshots.
Use the application's build metadata as the source; do not maintain a second
hard-coded UI version. Preserve prerelease suffixes so developer previews cannot
be confused with event releases. Include the same value in the native window title
where applicable. Longer build/revision information can live in About/logs.

TeamBuilder implements this convention using `pilot_teambuilder.__version__` in
its header and native window title starting with 0.4.0.dev0. Applying the same
convention to Capture, PanoBuilder and HQ is a separate repository/release task;
this TeamBuilder change does not modify or rebuild those applications.
