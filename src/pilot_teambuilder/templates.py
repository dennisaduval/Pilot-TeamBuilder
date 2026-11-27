"""Job template selection using filename color tokens, never team folder names."""

import re
from pathlib import Path

COLORS = {
    "orange", "aqua", "green", "pink", "red", "blue", "royal", "navy",
    "purple", "yellow", "gold", "black", "white", "gray", "grey", "teal",
    "maroon", "burgundy", "silver", "lime", "turquoise", "cyan", "coral",
}


def token(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold()).replace("grey", "gray")


def catalog(folder: Path, *, explicit_default=False):
    result = {}
    pattern = re.compile(r"^Team[ _-]+(Background|Foreground)(?:[ _-]+(.+))?$", re.I)
    for path in sorted(folder.iterdir()):
        match = pattern.fullmatch(path.stem)
        if not match or not path.is_file():
            continue
        role = match[1].lower()
        if path.suffix.lower() not in ({".jpg", ".jpeg"} if role == "background" else {".png"}):
            continue
        parts = [p for p in re.split(r"[_ -]+", match[2] or "") if p]
        variants = [p for p in parts if p.casefold() != "default"]
        key = token("".join(variants)) if variants else "default"
        if role in result.setdefault(key, {}):
            if key == "default" and explicit_default:
                continue
            raise ValueError(f"Duplicate {key} {role}: select an explicit pair or rename duplicates.")
        result[key][role] = path
    return result


def select_templates(job, team):
    folder = Path(team.get("templates") or job["templates"])
    pairs = catalog(folder, explicit_default=bool(job.get("background") and job.get("foreground"))) if folder.is_dir() else {}
    known = COLORS | set(pairs)
    seen, uncolored = set(), 0
    for entry in team["manifest"]["athletes"]:
        if entry.get("excluded"):
            continue
        tokens = [token(t) for t in re.split(r"[_ ]+", Path(entry["file"]).stem)]
        # Capture appends the color suffix. Names such as Mary Green must not
        # override it or become a second color. Allow known processing markers
        # after that suffix, but never search through athlete names/IDs.
        while tokens and tokens[-1] in {"team", "primary", "p", "share", "extracted", "cutout"}:
            tokens.pop()
        color = tokens[-1] if tokens and tokens[-1] in known and tokens[-1] != "default" else None
        if color:
            seen.add(color)
        else:
            uncolored += 1
    if len(seen) > 1 or (seen and uncolored):
        raise ValueError("Athlete files have mixed or missing color identifiers; use one color for the whole team.")
    variant = next(iter(seen), "default")
    if variant == "default" and job.get("background") and job.get("foreground"):
        return Path(job["background"]), Path(job["foreground"]), variant
    pair = pairs.get(variant, {})
    if set(pair) != {"background", "foreground"}:
        suffix = "_default" if variant == "default" else "_" + variant.title()
        raise ValueError(f"Missing {variant} template pair. Expected Team Background{suffix}.jpg and Team Foreground{suffix}.png, or select both default files in Job setup.")
    return pair["background"], pair["foreground"], variant
