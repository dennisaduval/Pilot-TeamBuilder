"""Fast alpha-based formation. All source pixels receive the same team scale."""

from math import isfinite
from statistics import median

from .model import ART_SIZE, PPI, Layout, Placement, Rect


def compose_silhouettes(active, rows, counts, settings, corrections, fixed_scale):
    from .composition import transform

    area = settings.area
    reference = fixed_scale if fixed_scale is not None else area.height / (median(a.body.height for a in active) * 0.75)
    if not isfinite(reference) or reference <= 0:
        raise ValueError("Job size must be positive and finite. Reset automatic fit.")
    desired = reference * settings.group_scale * (0.9 if len(rows) > 1 else 1)
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

    scale = desired
    # Fit after individual moves. Inch-valued offsets remain inch-valued. A
    # pathological move can be reset; ordinary moves never hold an entire team.
    for _ in range(12):
        points = placed(scale)
        width = max(p[5].right for p in points) - min(p[5].left for p in points)
        head_height = max(p[6].bottom for p in points) - min(p[6].top for p in points)
        ratio = min(1.0, area.width / width, area.height / head_height)
        if ratio >= 1 - 1e-9:
            break
        scale *= ratio * 0.999999
    points = placed(scale)
    left, right = min(p[5].left for p in points), max(p[5].right for p in points)
    top, bottom = min(p[6].top for p in points), max(p[6].bottom for p in points)
    if right - left > area.width + 1 or bottom - top > area.height + 1:
        raise ValueError("Individual moves exceed the whole placement box. Choose Reset placement to restore the formation.")
    ox = area.center_x - (left + right) / 2 + settings.x_offset_in * PPI
    # Match the established design anchor; bodies extend intact behind the FG.
    front_bottom = max(p[6].bottom for p in points if p[1] == len(rows) - 1)
    oy = (area.top + area.bottom) / 2 - PPI / 8 - front_bottom + settings.y_offset_in * PPI
    ox = min(max(ox, area.left - left), area.right - right)
    oy = min(max(oy, area.top - top), area.bottom - bottom)
    points.sort(key=lambda p: (p[1], -abs(p[2] - (counts[p[1]] - 1) / 2), p[0].relative_height, p[0].id))
    placements = []
    for a, ri, slot, x, y, _, _ in points:
        x, y = x + ox, y + oy
        placements.append(Placement(a.id, ri, slot, scale, x, y,
                                    transform(a.body, scale, x, y), transform(a.head, scale, x, y),
                                    a.image_size, a.body))
    return Layout(tuple(placements), counts, scale, (), clip=Rect(0, 0, *ART_SIZE), reference_scale=reference)
