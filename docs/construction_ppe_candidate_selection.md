# Construction-PPE Reduced Candidate Selection

Phase 2B, Part 2. This document reduces the 1,098-row Construction-PPE screen
queue to a list short enough to review by hand, and records exactly what was
filtered and why.

**Verdict carried forward:** `SUPPLEMENTARY_ONLY`. This was confirmed by the
human visual review of the Phase 2B Part 1 contact sheets, not derived here.

**Status of this document:** the queue is provisional. No training dataset has
been built, no class remapping has been performed, no split has been created, and
no model has been trained.

---

## 1. What the human review decided

The reviewer inspected the Part 1 contact sheets and reported:

- annotations are **eyewear-level boxes**, not consistent head/face-region boxes;
- many examples are distant, side-facing, partially obscured, or dominated by
  construction context;
- the smallest `goggles` examples are unsuitable;
- `no_goggle` supply is especially weak;
- the dataset **must not** be the main training source.

The task therefore became reduction rather than assembly: find the small number
of genuinely usable images and quantify the shortfall.

## 2. Reproducible tooling

```
tools/select_reduced_candidates.py     # builds the queue and both contact sheets
tests/test_select_reduced_candidates.py # 11 focused tests
```

Rerun with:

```
.venv/bin/python tools/select_reduced_candidates.py
```

The tool reuses the Part 1 screen module rather than reimplementing ZIP access,
YOLO parsing, or geometry. It re-runs the whole pipeline from the immutable
archive, so the queue can always be regenerated from source.

Determinism was verified by running the tool twice and comparing the output
byte-for-byte: **identical**.

## 3. Selection funnel

Rules applied in order, per box:

1. class is `goggles` or `no_goggle`;
2. `minimum_side_px >= 48`;
3. the image is not a member of a near-duplicate or exact-duplicate group;
4. no box edge lies within 1% of the frame edge (border clipping).

For `goggles`, survivors are then ranked and capped at 100. For `no_goggle`,
all survivors are kept.

| class | total boxes | − size (<48 px) | − duplicate | − border clip | − cap | **retained** |
|---|---|---|---|---|---|---|
| `goggles` | 751 | 62 | 12 | 7 | 570 | **100** |
| `no_goggle` | 347 | 314 | 3 | 0 | 0 | **30** |
| **total** | **1,098** | **376** | **15** | **7** | **570** | **130** |

Read the `goggles` cap carefully: 570 boxes were not rejected as *unusable*,
they were dropped only because the requested provisional queue is 100 images.
They remain available if the pool needs to grow later.

### Boxes removed for size

- `goggles`: 62 of 751 (8.3%) fall below 48 px minimum side.
- `no_goggle`: 314 of 347 (**90.5%**) fall below 48 px minimum side.

The asymmetry is the core finding. `no_goggle` is not merely under-represented;
the overwhelming majority of its annotations are too small to be legible after
the phone-screen → USB-webcam round trip.

### Boxes removed for duplicate/leakage risk

- `goggles`: 12 boxes across the near-duplicate groups.
- `no_goggle`: 3 boxes.
- Border-clipped: 7 `goggles`, 0 `no_goggle`.

Exclusion is applied at **image** level, not box level. An image that belongs to
a duplicate group is removed entirely: half-using such an image would still leak
across a future split.

The queue contains **0** rows carrying a `duplicate_group` value, confirming no
group member survived.

## 4. Retained candidates

| class | n | min side | median side | max side | min area | median area | max area |
|---|---|---|---|---|---|---|---|
| `goggles` | 100 | 84.8 px | 127.8 px | 242.2 px | 0.51% | 0.92% | 2.87% |
| `no_goggle` | 30 | 48.9 px | 60.4 px | 112.7 px | 0.09% | 0.23% | 3.08% |

By original split (informational only — no new split is created here):

| class | train | val |
|---|---|---|
| `goggles` | 76 | 24 |
| `no_goggle` | 24 | 6 |

Every retained `goggles` box has a minimum side of at least 84.8 px, comfortably
above the 48 px floor, because the cap selected only the strongest quartile.
Retained `no_goggle` boxes cluster just above the floor — the median is 60.4 px,
so half of them sit in the 48–60 px band.

## 5. Quantity requiring visual rejection

The tool cannot judge pose, occlusion, or rear-facing status. Those are semantic
properties of image content, and this pipeline is measurement-only. Two honest
categories:

**Measurably flagged — 3 rows** (`recommended_action = VISUAL_CHECK_REQUIRED`).
All are `no_goggle`, all flagged on a size-normalised sharpness score below the
class 10th percentile:

| filename | side | area % | sharpness |
|---|---|---|---|
| `ultralytics-ppe-nov2026-303` | 75.8 px | 0.33% | 8 |
| `ultralytics-ppe-nov2026-167` | 59.4 px | 0.15% | 6 |
| `ultralytics-ppe-nov2026-555` | 53.3 px | 0.09% | 3 |

**Not machine-determinable — all 130 rows.** Every row carries the note
`pose/occlusion NOT machine-determined - confirm front-facing`. The reviewer's
own finding was that the population is largely distant, side-facing, or
obscured, so a meaningful number of the 100 `goggles` rows will still fail on
sight. I cannot predict that number without seeing the images.

### A note on the sharpness measure

The score is Laplacian variance computed on each crop **resized to a fixed
160×160 tile** first. Resizing matters: at native resolution the score is
dominated by crop area, and small crops score low merely for being small. On the
first, unnormalised pass, `goggles` looked 4× sharper than `no_goggle` (p5 250
vs 62.6) purely because `goggles` crops are larger. The normalised score removes
that confound. It is a blur *hint*, not a verdict.

## 6. Are the retained boxes clear enough for a phone-screen demonstration?

Coldly, on the geometry: **`goggles` yes, `no_goggle` marginal.**

The demonstration chain is phone screen → air gap → USB webcam → YOLOv8n at 640.
What survives that chain depends on the box's *trained* resolution, not its
source resolution. Under the plan's plan.md target of detecting eye shields at
640 input:

- a `goggles` box at 128 px median side in a 640-wide inference frame maps to
  roughly 64 px of network input — adequate for a distinct object class;
- a `no_goggle` box at 60 px median side, in images where the subject occupies a
  small fraction of frame, will often fall below 30 px of network input after
  downscaling — at the edge of what YOLOv8n localises reliably.

This is the same weakness Phase 2B Part 1 flagged at population level, and the
48 px floor does not repair it: the floor selects the *least bad* `no_goggle`
examples rather than good ones. **Caveat:** this is an inference from geometry,
not from viewing the images. Only the reviewer's contact-sheet inspection can
settle whether a given `no_goggle` box reads as a visible, unoccluded face.

## 7. Remaining shortage against the 150–250 target

Plan-level requirement for a viable two-class baseline is roughly 150–250 usable
images per class.

| class | selected | short of 150 | short of 250 | verdict |
|---|---|---|---|---|
| `goggles` | 100 | 50 | 150 | recoverable from the 570 capped candidates, subject to visual pass |
| `no_goggle` | **30** | **120** | **220** | **critical — not recoverable from this archive** |

`goggles` is a scheduling problem: the capped 570 can be re-ranked upward if 100
proves too few. `no_goggle` is a data problem: after enforcing the 48 px floor
and removing duplicates, **only 30 candidates exist in the entire archive**.

No amount of re-ranking fixes that. It is a hard ceiling imposed by the source
data, and it means Construction-PPE cannot supply the `no_goggles` class even as
a supplementary source.

## 8. Close-up data still required

The blocker for Phase 2B is now specific and narrow:

> face-dominant close-up images of the eye region, both with and without
> eye shields.

The needed property is not "more images" but "images where the eye region is
large in frame". Construction-PPE is whole-scene construction photography;
re-scaling it cannot manufacture eye-region detail that was never captured.

For `no_goggles` specifically, the class is defined by the *absence* of an
object, so the annotation must sit on a face that is clearly visible and
unoccluded. Small, distant, or rear-facing faces are worthless here in a way
that a small `goggles` box is not: a small `goggles` box still contains goggles,
while a small `no_goggles` box frequently contains the back of a hard hat.

**No additional dataset has been downloaded, and none will be without approval.**

## 9. Files produced

Committed:

```
data/reviewed/construction_ppe_screen/reduced_candidate_queue.csv
docs/construction_ppe_candidate_selection.md
tools/select_reduced_candidates.py
tests/test_select_reduced_candidates.py
```

Git-ignored generated artifacts (regenerable, not committed):

```
data/reviewed/construction_ppe_screen/artifacts/reduced_goggles_candidates.png
data/reviewed/construction_ppe_screen/artifacts/reduced_no_goggle_candidates.png
```

Queue columns, exact order:

```
filename, source_split, source_class, minimum_side_px, box_area_ratio,
duplicate_group, recommended_action, human_decision, notes
```

`human_decision` is blank in all 130 rows for the reviewer to complete.

`recommended_action` values used: `INCLUDE_CANDIDATE` (127),
`VISUAL_CHECK_REQUIRED` (3).

## 10. Human decisions still required

1. Open `reduced_goggles_candidates.png` and
   `reduced_no_goggle_candidates.png`.
2. For each tile, reject rear-facing, heavily occluded, blurred, or
   visually unassessable subjects.
3. Fill `human_decision` in `reduced_candidate_queue.csv` (`keep` / `reject`).
4. Report the surviving count per class — this is the number that actually
   constrains Phase 2B.
5. Decide whether `goggles` should be expanded beyond 100 by raising the cap and
   re-ranking the 570 withheld candidates.
6. Approve or supply a close-up eye-region source for `no_goggles`.

## 11. Limitations of this selection

- **No semantic filtering was performed.** Pose, occlusion, and rear-facing
  status are undetermined for all 130 rows.
- The 48 px floor is a screening heuristic, not a measure of trainability.
- Sharpness is a Laplacian-variance proxy; it flags possible blur, it does not
  confirm it.
- Duplicate detection is perceptual-hash based (`HASH_SIDE = 8`,
  `HAMMING = 10`) and will miss re-crops and strong perspective changes.
- The `goggles` cap of 100 is an arbitrary queue size, not a data-quality
  boundary.
- Adjacent-numbered `ultralytics-ppe-nov2026-NNN` files were **not** treated as
  sequence groups: their filenames are shuffled, and Part 1 measured a minimum
  dHash distance of 18 and median of 31, which is inconsistent with video frames.
- No train/validation/test split exists yet, so cross-split leakage has not been
  re-tested at this stage.
