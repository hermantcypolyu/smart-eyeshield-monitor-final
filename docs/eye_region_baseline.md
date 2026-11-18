# Phase 2B — Eye-Region Baseline Dataset

Status: **prepared, validated, awaiting human contact-sheet review. No training performed.**

Tool: [`tools/prepare_eye_region_baseline.py`](../tools/prepare_eye_region_baseline.py)

```bash
.venv/bin/python tools/prepare_eye_region_baseline.py
```

The tool is idempotent and regenerates the whole tree from the immutable
source archive. It never opens the archive for writing and re-verifies the
SHA-256 at the end of the run.

---

## 1. Source

| Item | Value |
| --- | --- |
| Archive | `data/raw/eye_region_goggles_source_original.zip` |
| SHA-256 (before) | `1ae9c3a835ddc6a5f0d899fce193133add8348d7b74e1dd47adcbbd56673c533` |
| SHA-256 (after) | `1ae9c3a835ddc6a5f0d899fce193133add8348d7b74e1dd47adcbbd56673c533` |
| Bytes | 10,201,796 |
| ZIP entries | 2,582 |
| Byte-for-byte unchanged | **yes** (`source_zip_unchanged: true`) |

The archive is Git-ignored and was never renamed, modified, or deleted.

### Source `data.yaml` (matched exactly)

```
nc: 4
names: [eyes_with_goggles, eyes_without_goggles, head, head_with_helmet]
```

The tool parses the names out of the archive and asserts they match the
expected list; a mismatch aborts the run rather than silently remapping.

### Mapping applied

| Source | Name | Action |
| --- | --- | --- |
| 0 | `eyes_with_goggles` | → final **0** `goggles` |
| 1 | `eyes_without_goggles` | → final **1** `no_goggles` |
| 2 | `head` | dropped |
| 3 | `head_with_helmet` | dropped |

Licence and required attribution are recorded in
[`data/processed/eye_shield_baseline/attribution.md`](../data/processed/eye_shield_baseline/attribution.md) —
**CC BY 4.0**, source workspace `eie3360-fwbnu`, project
`safety-goggles-btrg5-ctdvk` version 1.

---

## 2. Original relevant counts by split

Counts are of images containing at least one relevant (*mapped*) box.
Images whose only boxes were `head`/`head_with_helmet` are not counted.
Measured independently from the archive, ignoring the stray
`export/images` + `export/labels` pair that belongs to no split.

| Source split | Relevant images | `eyes_with_goggles` | `eyes_without_goggles` | Both |
| --- | --- | --- | --- | --- |
| train | 891 | 268 | 625 | 6 |
| valid | 255 | 73 | 187 | 0 |
| test | 126 | 39 | 87 | 0 |
| **total** | **1,272** | **380** | **899** | **6** |

(The per-class columns sum to more than the image column because an image
may contain several boxes, and the 6 "both" images appear in both class
columns.)

The 6 "both" images were excluded outright — an image cannot carry two
contradictory compliance labels for the same eye region.

### Eligible pool after the 24 px size gate

Most source images are unusable at this resolution. Applying the gate
(§6) and the one-image-per-class rule leaves:

| Split | `goggles` eligible | `no_goggles` eligible |
| --- | --- | --- |
| train | 153 | 278 |
| valid | 53 | 84 |
| test | 24 | 21 |

These are the pools the quota selection actually draws from.

---

## 3. Retained dataset — 240 images, balanced

| Split | `goggles` images | `no_goggles` images | Total |
| --- | --- | --- | --- |
| train | 80 | 80 | 160 |
| val | 20 | 20 | 40 |
| test | 20 | 20 | 40 |
| **total** | **120** | **120** | **240** |

This meets the stated cap (≤120 per class) and sits inside the 180–240
target. Both splits hit the *upper* quota, so the 90-per-class minimum is
satisfied with margin.

`val` is written from the source `valid` split. The source split has been
**preserved**: no image moved between `train`/`valid`/`test`.

---

## 4. Box counts by class and split

| Split | `goggles` boxes | `no_goggles` boxes |
| --- | --- | --- |
| train | 86 | 86 |
| val | 20 | 20 |
| test | 20 | 22 |
| **total** | **126** | **128** |

Multi-box images (13 of 240): 12 have exactly 2 relevant boxes, one
(`goggle-140`) has 3. These are images where more than one eye region is
annotated; both boxes are retained, which is correct, but see §11.

---

## 5. Box geometry — minimum-side distribution per class and split

Values are `min(box_width_px, box_height_px)`.

| Split | Class | n | min | median | max |
| --- | --- | --- | --- | --- | --- |
| train | goggles | 80 | 28.7 | 46.5 | 131.3 |
| train | no_goggles | 80 | 25.7 | 42.2 | 101.5 |
| val | goggles | 20 | 44.0 | 60.2 | 111.5 |
| val | no_goggles | 20 | 40.3 | 47.5 | 121.1 |
| test | goggles | 20 | 29.4 | 42.5 | 104.5 |
| test | no_goggles | 20 | 29.1 | 36.6 | 60.2 |

Every retained image has at least one box ≥ the size gate (§6). No retained
image keeps only a sub-gate box.

---

## 6. Selection rationale — why the gate is 24 px, not 48 px

The source images are small (~270×179 px). A 48 px minimum side is therefore
**~18 % of the frame width** — an unusually strict requirement for this
archive. Measured distinct-image pools before selection:

| Gate | `goggles` (train/valid/test) | `no_goggles` |
| --- | --- | --- |
| ≥ 48 px | 65 total (42 / 18 / 5) | 39 |
| ≥ 32 px | 149 total (97 / 36 / 16) | — |
| ≥ 28 px | 183 total (121 / 42 / 20) | — |
| ≥ 24 px | 230 total (153 / 53 / 24) | 383 |

A 48 px gate cannot supply 90 images per class — the user's own stated
minimum — let alone 120. A 32 px gate would leave the test split with only
16 `goggles` images, below the 20 required by the stated 120-per-class
split target.

The gate was therefore set to **24 px**, chosen so that *every* split/class
quota is filled with headroom, and candidates are then **ranked by box size**
so the delivered set skews large (train medians 46.5 / 42.2 px). This is a
deliberate trade-off and is the main quality limitation of the baseline; it
is recorded as decision D-2B-03 in the decision log.

Consequence: the smallest retained boxes (~26–29 px) will still be marginal
when shown full-screen on a phone and observed through the USB webcam. §12
asks the user to adjudicate these.

---

## 7. Rejections

`rejected_candidates.csv` holds **1,047** rows, one per rejected image.

| Reason | Count | Meaning |
| --- | --- | --- |
| `below_size_gate` | 657 | best relevant box < 24 px |
| `not_selected_quota_full` | 338 | eligible, but lost the size-ranked quota contest |
| `suspect_label_family` | 25 | class contradicts the filename family (§10) |
| `no_relevant_box` | 15 | image existed but carried only `head`/`head_with_helmet` |
| `both_classes_present` | 6 | image carried both class 0 and class 1 |
| `duplicate_group_other_split` | 4 | near-duplicate anchored in a higher-priority split |
| `duplicate_of_selected_image` | 2 | exact duplicate of an already-selected image |

`not_selected_quota_full` is not a defect — those images are *usable* and are
merely surplus to the 120-per-class cap.

**Annotation errors: 0.** No retained row failed five-field parsing,
integer-class parsing, numeric parsing, positive width/height, or the
0–1 bounds check after clipping.

---

## 8. Duplicates and leakage

Detection: 8×8 dHash (`hamming ≤ 6`) **and** normalised RMSE ≤ 20 on a
64×64 greyscale thumbnail. Hash alone is far too permissive on this archive
(at hamming ≤ 10 the median RMSE of nominal "matches" was 68/255), so both
tests must agree.

| Metric | Value |
| --- | --- |
| Near-duplicate pairs found | 9 |
| Group components | 9 |
| Exact-duplicate pairs | 1 |
| Cross-split groups | 4 |
| Members dropped for leakage | 4 |

Raw near-duplicates are listed in `artifacts/sheet_duplicate_groups.png`.

**Leakage resolution.** Rather than resplitting the archive, each duplicate
group is anchored in the highest-priority split (`test` > `val` > `train`)
and copies in other splits are dropped. Within a single split, only the
best-ranked member of a group is kept. This keeps the source split intact.

Verified on the final 240-image set:

- cross-split filename-stem overlap: **0**
- cross-split identical SHA-256: **0**
- near-duplicate pairs surviving inside the final set: **0**

---

## 9. `suspect_label_family` — a heuristic, not a verified error

Filename prefixes cluster strongly by class: `goggle-*` is overwhelmingly
class 0 and `eye-glasses-*` / `no-glasses-*` overwhelmingly class 1.
The **opposite** pairings — a `goggle-*` file annotated class 1, or an
`eye-glasses-*` file annotated class 0 — are the statistically likeliest
sources of annotation error, so they were excluded rather than trained on.

Excluded: 24 `goggle-*`-named files annotated `no_goggles`, and 1
`eye-glasses-*`-named file annotated `goggles`.

**This is an inference from filenames, not a verified label defect.** The
source annotator may have been correct in every case — e.g. a clip named
`goggle-124` may simply contain a person *not* wearing the goggles in that
frame. All 25 are rendered on `artifacts/sheet_rejected_questionable.png`
for the user to adjudicate. If they are in fact correct, they can be
promoted back into the dataset by relaxing `SUSPECT_FAMILY_BY_CLASS`.

All 240 retained images have a filename family *consistent* with their
class (120 `goggle-*` → class 0; 88 `eye-glasses-*` + 32 `no-glasses-*` →
class 1).

---

## 10. Contact sheets

Generated images are Git-ignored and live under:

```
data/processed/eye_shield_baseline/artifacts/
```

| File | Size | Contents |
| --- | --- | --- |
| `sheet_goggles_train.png` | 1152×1362 | 80 selected `goggles` training images |
| `sheet_no_goggles_train.png` | 1152×1362 | 80 selected `no_goggles` training images |
| `sheet_test_locked.png` | 1992×1362 | the 40 locked test images |
| `sheet_rejected_questionable.png` | 1152×1362 | the 25 `suspect_label_family` files |
| `sheet_duplicate_groups.png` | 872×1362 | all 9 duplicate groups |

Each tile shows a safe short filename, source split, source class, final
class, the relevant bounding box, and minimum box side in pixels.

---

## 11. What still needs the user's eyes

I cannot see images, so the following are explicitly **not** decided by this
document. They are decisions only the user can make.

1. **Consistent annotation semantics.** The two source classes are
   *structurally* consistent — both are eye-region boxes in the same unit
   from the same annotator. Whether they are *semantically* consistent
   (i.e. class 1 really frames bare eyes and class 0 really frames goggles)
   requires visual confirmation on the three selected sheets.
2. **Ordinary prescription glasses inside `goggles`.** This project's
   `goggles` class should mean *safety eye-shield*. If the source's
   `eyes_with_goggles` includes ordinary spectacles, the model will learn to
   accept everyday glasses as compliant, which is a false-negative risk in a
   laser-lab context. This cannot be determined from filenames or geometry.
   Inspect `sheet_goggles_train.png` specifically for thin metal frames,
   tinted fashion lenses, and reading glasses.
3. **The 25 `suspect_label_family` files** (§9) — are they mislabelled or
   correctly labelled?
4. **The smallest retained boxes** — train minimum sides are 28.7 px
   (`goggles`) and 25.7 px (`no_goggles`). Are these legible on a phone
   screen through the webcam, or should the gate be raised to 32 px even at
   the cost of the 120-per-class quota?
5. **Multi-box images** — 13 images carry 2–3 boxes. Confirm both regions are
   genuinely eye regions and not duplicate annotations of the same region.

---

## 12. Suitability for the first YOLOv8n baseline

**Ready for a first baseline run, subject to §11.**

Reasons supporting a "go":

- 240 images, perfectly balanced 120/120, split 80/20/20 per class.
- Zero annotation errors; zero empty labels; zero orphans.
- Zero exact or near-duplicate leakage across splits.
- Boxes are eye-region level, which is the granularity the compliance rule
  needs, and median box sides (≈42–60 px) are workable at 640 px inference.
- CC BY 4.0 attribution recorded.

Known limitations to carry into the training report:

- Absolute dataset size is small; expect the model to be the weakest link.
- Source images are low resolution (~270×179 px), so training will upscale.
- The 24 px gate admits some small boxes (§6).
- Label-family exclusions (§9) remove 25 images that may be valid.

These are *expected* for a four-week mini project. They are not blockers.

---

## 13. Validation performed

| Check | Result |
| --- | --- |
| Source SHA-256 unchanged | PASS |
| Source `data.yaml` class IDs match | PASS |
| Every image has a label / every label has an image | PASS (240/240) |
| Exactly five fields per row | PASS |
| Final class IDs ∈ {0, 1} | PASS |
| Coordinates numeric and in-bounds | PASS |
| Positive width and height | PASS |
| No empty label file | PASS |
| No exact duplicate across splits | PASS |
| No near-duplicate across splits | PASS |
| CC BY 4.0 attribution recorded | PASS |
| Focused tests | 33 passed |
| Full suite | **191 passed** |
| `tools/env_check.py` | 5 passed, 1 warning (no weights — expected) |

---

## 14. What was NOT done

- No YOLO training. No `best.pt`.
- No modification to Phase 2A firmware.
- No use of Construction-PPE or the rejected Safety Glasses export.
- No additional dataset downloaded.
- No JSON serial integration, no Blynk, no 7B model.

Training is blocked pending the user's review of the contact sheets.
