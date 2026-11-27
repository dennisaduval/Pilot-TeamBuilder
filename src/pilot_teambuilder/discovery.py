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


def team_folders(root: Path) -> list[Path]:
    """Find real team leaves at any depth; never merge a container's teams."""
    ignored = {"templates", "completed", "completed teams", "upload", "samples"}
    wrappers = {"png", "pngs", "images", "photos", "extracted", "editedpng", "exports", "cutouts"}
    found = []

    def visit(folder):
        children = sorted(folder.iterdir())
        if any(p.is_file() and not is_system_entry(p.relative_to(root))
               and p.suffix.lower() == ".png" for p in children):
            team = folder
            while team != root and team.name.casefold().replace(" ", "").replace("_", "") in wrappers:
                team = team.parent
            found.append(team)
            return
        for child in children:
            if (child.is_dir() and not child.is_symlink()
                    and not is_system_entry(child.relative_to(root))
                    and child.name.casefold() not in ignored):
                visit(child)

    visit(root)
    return sorted(set(found))
