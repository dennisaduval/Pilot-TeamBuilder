"""Exact scale windows for a head-anchored, foreground-covered formation.

Edges and translations are affine functions of the common team scale. A min/max
translation can change its active edge only where two candidate lines cross.
Within each interval all framing inequalities are linear; no image work is done.
"""

from math import isfinite


def scale_windows(lines, translation, constraints, cover_line, *, lower=0.0, upper=float("inf")):
    """Return scale intervals satisfying (edge + translation) <=/>= cover_line.

    Lines and edges are (source coefficient, fixed pixel move). A constraint's
    third value is 1 for <= (waist), -1 for >= (three-quarter reference).
    ``translation(scale)`` selects one of ``lines`` using min/max only.
    """
    if upper <= 0 or lower > upper:
        return []
    lines = list(dict.fromkeys(lines))
    cuts = {lower, upper}
    for i, (am, ab) in enumerate(lines):
        for bm, bb in lines[i + 1:]:
            if am != bm:
                crossing = (bb - ab) / (am - bm)
                if lower < crossing < upper:
                    cuts.add(crossing)
    cuts = sorted(cuts)
    result = []
    for start, end in zip(cuts, cuts[1:]):
        probe = (start + end) / 2 if isfinite(end) else start + max(1, abs(start))
        move_m, move_b = translation(probe)
        lo, hi = start, end
        for edge_m, edge_b, direction in constraints:
            slope = (edge_m + move_m) * direction
            offset = (edge_b + move_b - cover_line) * direction
            if slope > 0:
                hi = min(hi, -offset / slope)
            elif slope < 0:
                lo = max(lo, -offset / slope)
            elif offset > 1e-7:
                hi = -1
                break
        if hi > 0 and lo <= hi:
            if result and lo <= result[-1][1] + 1e-10:
                result[-1] = (result[-1][0], max(hi, result[-1][1]))
            else:
                result.append((lo, hi))
    # A single-point feasible domain has no segment to iterate.
    if lower == upper and upper > 0:
        m, b = translation(upper)
        if all(((em + m) * upper + eb + b - cover_line) * d <= 1e-7
               for em, eb, d in constraints):
            result.append((lower, upper))
    return result


def choose_scale(windows, preferred):
    """Honor a size preference within the feasible framing range."""
    below = [min(hi, preferred) for lo, hi in windows if min(hi, preferred) >= lo]
    return max(below) if below else min(lo for lo, _ in windows)
