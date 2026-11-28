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
| Front-row framing range | At the foreground cover line, show approximately 50%–75% of every front-row source silhouette: waist-up through three-quarter length |
| Foreground cover line | Placement-area bottom by default (6¾ in); Advanced accepts an explicit inch value, 0 follows the area bottom |
| Multi-row size reference | Prefer 90% of the waist-up framing cap, before width/top constraints |
| Single-row head position | Bottom of the lowest top band is ⅛ in above the rectangle's horizontal center guide |
| Multiple-row head position | Same front-row anchor; upper/back top bands remain below the rectangle's top boundary |
| Horizontal fit | Maximize full silhouette width within framing and top bounds; small teams stay centered when waist-up prevents further growth |
| Hero formation | Center first by height rank, center painted last; top bands descend at every outward slot |
| Proportions | One identical pixel-to-output scale for every included athlete; no individual resizing or stretching |
| Lower bodies | Intact behind the foreground; only the outer artwork canvas limits rendered overflow |

Dennis confirmed the waist-up to three-quarter range on 2026-11-28. These are
geometric estimates from full-body alpha bounds, not anatomical landmark detection
or subject crops. The foreground still controls actual visible body length. Similar
standing poses, clean extractions and unchanged capture scale are the intended input.
Hair, bows, shadows, reflections and unusually bent poses can affect the estimate.

The optional **Foreground cover line** is a shared job setting in ⅛-inch increments.
Use it when the foreground hides bodies above/below the placement-box bottom. It
is a reference only: it never crops subjects or changes the template. Center guides
show it as an orange dashed line; no guides appear in the JPEG. Irregular or partly
transparent foregrounds cannot be represented by a single exact cutoff, so verify
the representative preview before starting production.

The selected scale is the greatest one meeting all of these constraints:

1. The waist-up boundary for every front-row silhouette at the cover line,
   including actual hero steps, individual moves and clamped group Y translation.
2. The width of the complete group, including intended overlap, staggering and
   individual moves.
3. The distance between the front-row anchor and the highest back/top band.
4. The vertical envelope of every top band.

Edges are linear functions of scale plus fixed inch-valued moves. Width/top
inequalities give the safe scale domain. The anchored/clamped vertical translation
is piecewise linear; candidate edges can exchange order only at their intersections.
Within each interval, waist and three-quarter inequalities are solved exactly.
The largest feasible scale is chosen within the job's size preference. There is no
iterative image analysis, face-model call or repeated source decode.

At 100%, a single-row team reaches its true width, waist or top limit. Multi-row
teams prefer a modestly smaller framing cap. The size slider is a preference within
the legal framing window: settings below/above the window stop at three-quarter/
waist-up respectively. Increasing above 100% can relax the multi-row reduction,
but cannot hide athletes above waist-up. An earlier team's scale never caps a later one.

If exact CSV rows, differing source heights, manual moves or width/top bounds make
the three-quarter minimum size impossible, preserve all included athletes and the
CSV distribution. Choose the largest safe waist/width/top fit and display an
informational note that looser framing is required. This is not a production hold.
An extreme downward translation is limited if it would place even the heads on
the cover line. Invalid/incompatible geometry still has an explicit reset remedy.

Reports include limiting scales, the cover-line position, minimum/maximum front-row
visible fractions and informational fit notes. Optional legacy face/full-body modes
retain their earlier sizing contract; the new window applies to default fast mode.

Job X/Y offsets translate the fitted group within safe boundaries; the fitter
re-evaluates framing when the vertical position changes. Ordinary individual moves
participate in fitting. Conflicting extreme
moves have a specific **Reset placement** remedy. Manual rows/ranks/exclusions are
preserved; preview-team-specific corrections are not copied onto unrelated teams.

All CSV sizes 1–45, plus 64/100 athletes, are tested for a centered group, common
scale, unchanged front-row anchor, top/width bounds, and saturation of a real size
limit. Additional checks cover medium teams growing beyond the old fixed cap,
small-team waist stops, vertical/manual moves, explicit foreground lines, mixed
heights/wide poses, job size bounds and multi-row spacing. Version-5 migration
retains manual edits, job defaults and delivery receipts; changing fitting rules
does not automatically reprint completed teams. Deliberate Rebuild is available.
