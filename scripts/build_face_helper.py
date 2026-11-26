"""Build a native-architecture offline Vision helper on macOS; no downloads."""

from pathlib import Path
import platform
import subprocess


def main():
    if platform.system() != "Darwin":
        raise SystemExit("Build the Vision helper on macOS with Xcode Command Line Tools installed.")
    root = Path(__file__).resolve().parents[1]
    destination = root / "bin/TeamFaceDetector"
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["xcrun", "swiftc", "-O", "-target", platform.machine() + "-apple-macosx13.0",
                    str(root / "native/TeamFaceDetector.swift"), "-o", str(destination)], check=True)
    print(destination)


if __name__ == "__main__":
    main()
