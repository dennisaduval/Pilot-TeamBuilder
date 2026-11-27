"""Build an ad-hoc-signed Apple Silicon developer preview on a Mac."""

import platform
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

from pilot_teambuilder import __version__


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("Build this preview on an Apple Silicon Mac.")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(root / "scripts/build_face_helper.py")], check=True
    )
    with tempfile.TemporaryDirectory(prefix="teambuilder-build-") as temporary:
        temp = Path(temporary)
        launcher = temp / "launcher.py"
        launcher.write_text("from pilot_teambuilder.app import main\nmain()\n")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--windowed",
                "--name",
                "PilotTeamBuilder",
                "--target-arch",
                "arm64",
                "--osx-bundle-identifier",
                "com.volumepilot.teambuilder.preview",
                "--collect-data",
                "pilot_teambuilder",
                "--add-binary",
                str(root / "bin/TeamFaceDetector") + ":bin",
                "--distpath",
                str(root / "dist"),
                "--workpath",
                str(temp / "work"),
                "--specpath",
                str(temp),
                str(launcher),
            ],
            check=True,
        )
    app = root / "dist/PilotTeamBuilder.app"
    executable = app / "Contents/MacOS/PilotTeamBuilder"
    if not list(app.rglob("row_counts.csv")) or not list(app.rglob("TeamFaceDetector")):
        raise SystemExit("Build is missing the row table or native face detector.")
    info_path = app / "Contents/Info.plist"
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    info.update(
        CFBundleDisplayName="Pilot TeamBuilder",
        CFBundleShortVersionString=__version__.split(".dev")[0],
        CFBundleVersion=__version__.split(".dev")[0],
        NSHighResolutionCapable=True,
        LSMinimumSystemVersion="13.0",
    )
    with info_path.open("wb") as handle:
        plistlib.dump(info, handle)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    subprocess.run([str(executable), "--smoke-test"], check=True, timeout=45)
    archive = root / f"dist/Pilot-TeamBuilder-{__version__}-macOS-arm64-preview.zip"
    subprocess.run(
        ["ditto", "-c", "-k", "--keepParent", str(app), str(archive)], check=True
    )
    print(archive)


if __name__ == "__main__":
    main()
