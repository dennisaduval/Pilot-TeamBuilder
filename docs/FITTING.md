# Preview defaults and automatic fitting

Choose the incoming hotfolder, then **Preview team…** (or the preview-folder field
in Job setup). The preview can be outside Incoming. **Save & preview**, opening a
job, selecting a folder, and adjusting controls never start production. Only the
main **Start automatic processing** action starts automatic output and routing.
An external preview is marked **Preview only** and never becomes a production
candidate. Moving/deleting that external folder after setup does not block saved
defaults or the watched teams; choose another preview when needed. A preview inside Incoming can be processed after Start like other teams.

Set overlap, row spacing, hero step, group size and position against the preview.
These style settings are job-wide. Row CSV/manual row overrides determine the row
counts; the fitter independently grows or shrinks every later team. An earlier
team's source-pixel scale is never a cap on the next team.

| Rule | Default behavior |
| --- | --- |
| Placement rectangle | 22 × 6 in: x = 1–23 in, y = ¾–6¾ in |
| Front-row size reference | Tallest visible source silhouette in the front row; 75% of its height maps to the box height at 100% group size |
| Multi-row size reference | 90% of the single-row framing cap, before width/top constraints |
| Single-row head position | Bottom of the lowest top band is ⅛ in above the rectangle's horizontal center guide |
| Multiple-row head position | Same front-row anchor; upper/back top bands remain below the rectangle's top boundary |
| Horizontal fit | Full silhouette widths fit inside the rectangle; group is centered by default |
| Hero formation | Center first by height rank, center painted last; top bands descend at every outward slot |
| Proportions | One identical pixel-to-output scale for every included athlete; no individual resizing or stretching |
| Lower bodies | Intact behind the foreground; only the outer artwork canvas limits rendered overflow |

“Three-quarter” is a sizing reference, not an anatomical crop. The foreground
controls actual visible body length. Fast mode uses transparency top bands, not
face detection. Similar poses and clean extractions are the intended input.

The selected scale is the greatest one meeting all of these constraints:

1. The front-row framing cap, including the operator's group-size setting and the
   multi-row factor.
2. The width of the complete group, including intended overlap, staggering and
   individual moves.
3. The distance between the front-row anchor and the highest back/top band.
4. The vertical envelope of every top band.

Edges are linear functions of scale plus fixed inch-valued moves. Pairwise edge
inequalities give an exact feasible interval; its upper endpoint supplies the
largest scale. This replaces iterative shrink-only fitting and fixed calibration
from the first processed team. The report retains all limiting scales, and the UI
identifies whether width, framing or the top boundary is limiting size.

Job X/Y offsets translate the fitted group within safe boundaries without changing
its scale. Ordinary individual moves participate in fitting. Conflicting extreme
moves have a specific **Reset placement** remedy. Manual rows/ranks/exclusions are
preserved; preview-team-specific corrections are not copied onto unrelated teams.

All CSV sizes 1–45, plus 64/100 athletes, are tested for a centered group, common
scale, unchanged front-row anchor, top/width bounds, and saturation of a real size
limit. Additional checks cover later teams growing beyond prior scales, front-row
rather than back-row framing, multi-row spacing and the 10% multi-row reference.
