"""Fast alpha-based formation. All source pixels receive the same team scale."""

from math import isfinite
from statistics import median

from .framing import choose_scale, scale_windows
from .model import ART_SIZE, PPI, Layout, Placement, Rect


def compose_silhouettes(active, rows, counts, settings, corrections, fixed_scale):
    from .composition import transform

    area = settings.area
    if fixed_scale is not None and (not isfinite(fixed_scale) or fixed_scale <= 0):
        raise ValueError("Job size must be positive and finite. Reset automatic fit.")
    # A common pitch preserves natural overlap: 15% at the default setting.
    pitch = median(a.body.width for a in active) * (1 - settings.overlap_fraction)
    pitch = max(pitch, max(a.head.width for a in active) * 1.05)
    representative_head = median(a.head.height for a in active)
    equal = len(rows) > 1 and len(set(counts)) == 1
    raw = []
    for ri, row in enumerate(rows):
        center = (len(row) - 1) / 2
        max_edge = max(0, center - (0.5 if len(row) % 2 == 0 else 0))
        step = min(settings.v_step_in, settings.v_max_drop_in / max_edge) if max_edge else 0
        for slot, a in enumerate(row):
            edge = max(0, abs(slot - center) - (0.5 if len(row) % 2 == 0 else 0))
            dx, dy = corrections.offsets_in.get(a.id, (0, 0))
            if not all(isfinite(v) for v in (dx, dy)):
                raise ValueError("Individual moves must be finite inches. Reset placement.")
            x = (slot - center) * pitch + (pitch / 2 if equal and ri % 2 else 0) - a.head.center_x
            # Align actual alpha tops, not differently sized head boxes. Every
            # successive slot is lower, without a flat outer section at the cap.
            y = ri * representative_head * settings.row_separation_heads - a.head.top
            raw.append((a, ri, slot, x, y, dx * PPI, (dy + edge * step) * PPI))

    def placed(scale):
        result = []
        for a, ri, slot, x, y, dx, dy in raw:
            px, py = x * scale + dx, y * scale + dy
            result.append((a, ri, slot, px, py, transform(a.body, scale, px, py), transform(a.head, scale, px, py)))
        return result

    anchor = (area.top + area.bottom) / 2 - PPI / 8
    # Each edge is linear in scale: source_coordinate * scale + inch_offset.
    # Pairwise inequalities produce the exact feasible interval, avoiding a
    # shrink-only heuristic and preserving the front-row center-line anchor.
    lower = 0.0

    def limit(upper_edges, lower_edges, available):
        nonlocal lower
        bound = float("inf")
        for upper_coord, upper_move in upper_edges:
            for lower_coord, lower_move in lower_edges:
                slope, offset = upper_coord - lower_coord, upper_move - lower_move
                if slope > 0:
                    bound = min(bound, (available - offset) / slope)
                elif slope < 0:
                    lower = max(lower, (available - offset) / slope)
                elif offset > available + 1e-7:
                    raise ValueError("Individual moves exceed the placement box. Choose Reset placement.")
        return bound

    left_edges = [(x + a.body.left, dx) for a, _, _, x, _, dx, _ in raw]
    right_edges = [(x + a.body.right, dx) for a, _, _, x, _, dx, _ in raw]
    top_edges = [(y + a.head.top, dy) for a, _, _, _, y, _, dy in raw]
    bottom_edges = [(y + a.head.bottom, dy) for a, _, _, _, y, _, dy in raw]
    front_edges = [(y + a.head.bottom, dy) for a, ri, _, _, y, _, dy in raw if ri == len(rows) - 1]
    front = [(a, y, dy) for a, ri, _, _, y, _, dy in raw if ri == len(rows) - 1]
    cover_line = settings.foreground_line_in * PPI or area.bottom
    waist = [(y + a.body.top + a.body.height * 0.5, dy, 1) for a, y, dy in front]
    quarter = [(y + a.body.top + a.body.height * 0.75, dy, -1) for a, y, dy in front]
    anchor_lines = [(-coord, anchor - move) for coord, move in front_edges]

    def lowest(lines, scale):
        return min(lines, key=lambda line: line[0] * scale + line[1])

    limits = {"width": limit(right_edges, left_edges, area.width),
              "head_top": limit(front_edges, top_edges, anchor - area.top),
              "head_envelope": limit(bottom_edges, top_edges, area.height)}
    hard_cap = min(limits[k] for k in ("width", "head_top", "head_envelope"))
    if not isfinite(hard_cap) or hard_cap <= 0 or lower > hard_cap + 1e-7:
        raise ValueError("Individual moves leave no room for this formation. Choose Reset placement.")
    preferred_lines = [(m, b + settings.y_offset_in * PPI) for m, b in anchor_lines]
    top_lines = [(-coord, area.top - move) for coord, move in top_edges]
    bottom_lines = [(-coord, area.bottom - move) for coord, move in bottom_edges]

    def translation(scale):
        preferred = lowest(preferred_lines, scale)
        floor = max(top_lines, key=lambda line: line[0] * scale + line[1])
        ceiling = lowest(bottom_lines, scale)
        return lowest([max([preferred, floor], key=lambda line: line[0] * scale + line[1]), ceiling], scale)

    lines = preferred_lines + top_lines + bottom_lines
    waist_windows = scale_windows(lines, translation, waist, cover_line)
    waist_cap = max((hi for _, hi in waist_windows), default=0)
    limited_vertical_move = False
    if waist_cap <= 0 and settings.y_offset_in > 0:
        # A downward move can put even the heads on the cover line. Limit that
        # requested translation instead of holding an otherwise usable team.
        preferred_lines = anchor_lines
        lines = preferred_lines + top_lines + bottom_lines
        waist_windows = scale_windows(lines, translation, waist, cover_line)
        waist_cap = max((hi for _, hi in waist_windows), default=0)
        limited_vertical_move = True
    if not isfinite(waist_cap) or waist_cap <= 0:
        raise ValueError("Cannot fit waist-up framing. Check the foreground cover line and source silhouettes.")
    windows = scale_windows(lines, translation, waist + quarter, cover_line, lower=lower, upper=hard_cap)
    reference = min((lo for lo, _ in windows), default=0)
    if not windows:
        # Large teams or strongly differing silhouettes can make the 3/4 floor
        # impossible with the exact CSV rows. Retain bounds/all athletes and
        # explain the looser framing without blocking automatic output.
        windows = [(max(lo, lower), min(hi, hard_cap)) for lo, hi in waist_windows
                   if max(lo, lower) <= min(hi, hard_cap) and min(hi, hard_cap) > 0]
    if not windows:
        raise ValueError("Placement leaves no room above the foreground cover line. Choose Reset placement or adjust the cover line.")
    # A saved style preference is constrained by the framing window. At 100%,
    # single-row teams reach the largest legal width/waist/top limit. Multi-row
    # framing starts 10% smaller, unless width/top already impose a lower cap.
    desired = min(waist_cap, max(reference, waist_cap * settings.group_scale * (0.9 if len(rows) > 1 else 1)))
    limits["front_framing"] = desired
    scale = choose_scale(windows, desired)
    points = placed(scale)
    left, right = min(p[5].left for p in points), max(p[5].right for p in points)
    top, bottom = min(p[6].top for p in points), max(p[6].bottom for p in points)
    if right - left > area.width + 1 or bottom - top > area.height + 1:
        raise ValueError("Individual moves exceed the whole placement box. Choose Reset placement to restore the formation.")
    ox = area.center_x - (left + right) / 2 + settings.x_offset_in * PPI
    # Match the established design anchor; bodies extend intact behind the FG.
    front_bottom = max(p[6].bottom for p in points if p[1] == len(rows) - 1)
    oy = anchor - front_bottom + (0 if limited_vertical_move else settings.y_offset_in * PPI)
    ox = min(max(ox, area.left - left), area.right - right)
    oy = min(max(oy, area.top - top), area.bottom - bottom)
    points.sort(key=lambda p: (p[1], -abs(p[2] - (counts[p[1]] - 1) / 2), p[0].relative_height, p[0].id))
    placements = []
    for a, ri, slot, x, y, _, _ in points:
        x, y = x + ox, y + oy
        placements.append(Placement(a.id, ri, slot, scale, x, y,
                                    transform(a.body, scale, x, y), transform(a.head, scale, x, y),
                                    a.image_size, a.body))
    fractions = [(cover_line - p.body.top) / p.body.height for p in placements if p.row == len(rows) - 1]
    notes = ("The downward move was limited to retain waist-up framing.",) if limited_vertical_move else ()
    if max(fractions) > 0.75 + 1e-7:
        notes += ("The selected rows and placement limits require more than three-quarter framing for this team.",)
    return Layout(tuple(placements), counts, scale, (), clip=Rect(0, 0, *ART_SIZE),
                  reference_scale=waist_cap, fit_limits=limits,
                  framing={"cover_line_in": cover_line / PPI,
                           "minimum_front_fraction": min(fractions), "maximum_front_fraction": max(fractions)},
                  fit_notes=notes)
