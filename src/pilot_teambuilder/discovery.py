"""Consistent source discovery without treating macOS archive metadata as photos."""

from pathlib import Path


def is_system_entry(relative: Path) -> bool:
    """Ignore known Finder/AppleDouble entries, not arbitrary hidden athlete PNGs."""
    return any(part == "__MACOSX" or part == ".DS_Store" or part.startswith("._")
               for part in relative.parts)


def source_paths(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*")
                  if not is_system_entry(p.relative_to(folder)))


def png_paths(folder: Path) -> list[Path]:
    return [p for p in source_paths(folder) if p.is_file() and p.suffix.lower() == ".png"]
