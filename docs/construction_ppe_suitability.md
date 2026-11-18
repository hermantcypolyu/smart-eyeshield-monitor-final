# Construction-PPE: Quick Suitability Screen

**Status:** screening complete; **verdict provisional pending your visual review of the contact sheets.**
**Scope:** Phase 2B preliminary work only. No dataset was built, no class was remapped, no split was created, no model was trained.
**Tool:** `tools/screen_construction_ppe.py` (read-only; decodes images from ZIP bytes in memory and never extracts to disk).
**Run metadata:** `data/reviewed/construction_ppe_screen/audit_run_metadata.json`.

---

## 1. Source archive and integrity

| Item | Value |
|---|---|
| Path | `data/raw/construction_ppe_original.zip` |
| Size | 647,666,037 bytes |
| Entries | 2,004 (1,000 images + 1,000 labels + `data.yaml` + `README.md` + `LICENSE.txt` + `manifest.jsonl`) |
| SHA-256 **before** screening | `e5817aa9c73f33e525d76ca6bc6af4e4eac9dcea9bcfef1cfd1a71c09fbc1232` |
| SHA-256 **after** screening | `e5817aa9c73f33e525d76ca6bc6af4e4eac9dcea9bcfef1cfd1a71c09fbc1232` |
| Unchanged | **Yes** — byte-for-byte identical; the tool opened the file read-only and records the before/after hash itself |

Figure reproducibility of the source hash: independently re-computed with `sha256sum` after the run, identical. The archive is Git-ignored and was not renamed, moved, or re-downloaded.

## 2. Licence and required attribution

The archive's own `README.md` states: images and annotations are licensed **CC BY 4.0**, and every one of the 1,000 images carries annotations produced by the Ultralytics team (human-collected plus generated images, expert annotation plus AI auto-annotation, November 2026). The archive also ships the full CC BY 4.0 legal text as `construction-ppe/LICENSE.txt`, which was inspected and matches.

Required attribution, as given verbatim inside the archive:

> Ultralytics Construction-PPE Dataset, Ultralytics team, November 2026, CC BY 4.0.

Any redistributed subset or adapted derivative must credit that attribution, link the licence, and **indicate changes**. If any Construction-PPE images are later redistributed, the derived dataset inherits CC BY 4.0 obligations. No source image, extracted image, or contact sheet is committed to this repository.

## 3. Source class IDs (confirmed from `data.yaml`)

`construction-ppe/data.yaml` declares **12** classes. The two relevant ones are:

| Source name in `data.yaml` | Source ID | Final class (plan) |
|---|---|---|
| `goggles` | **4** | 0 `goggles` |
| `no_goggle` | **7** | 1 `no_goggles` |

Note the source spelling is singular **`no_goggle`**; the project's final class is `no_goggles`. The remaining 10 classes (`helmet`, `gloves`, `vest`, `boots`, `Person`, `no_helmet`, `no_gloves`, `no_boots`, `forklift`, `no_vest`) are unrelated and are ignored throughout.

Source splits: `train` (800 images) and `val` (200 images). **There is no source test split**, so any final test partition would have to be carved out of these — a leakage risk this screen quantifies in §6.

## 4. Relevant image and box counts

| Metric | `goggles` (id 4) | `no_goggle` (id 7) | All relevant |
|---|---|---|---|
| Boxes | **751** | **347** | **1,098** |
| Images containing the class | **539** | **283** | 779 |
| Boxes in source `train` | 612 | 265 | 877 |
| Boxes in source `val` | 139 | 82 | 221 |
| Images with a relevant box, `train` | — | — | 624 |
| Images with a relevant box, `val` | — | — | 155 |

- Archive holds 1,000 images; **779** carry at least one relevant box and **221** carry none.
- **43 images contain both classes.**
- Boxes per image is close to one, so box counts and image counts stay in step: goggles 1.39 boxes/image, no_goggle 1.23 boxes/image.

## 5. Size thresholds by class

Box geometry is computed from each image's true pixel dimensions, read at decode time.

### 5a. By box, minimum-side survivorship

| Class | Total | ≥16 px | ≥24 px | ≥32 px | ≥48 px |
|---|---|---|---|---|---|
| `goggles` | 751 | 749 (99.73%) | 743 (98.93%) | 740 (98.54%) | **689 (91.74%)** |
| `no_goggle` | 347 | 321 (92.51%) | 242 (69.74%) | 155 (44.67%) | **33 (9.51%)** |
| All relevant | 1,098 | 1,070 (97.45%) | 985 (89.71%) | 895 (81.51%) | 722 (65.76%) |

### 5b. By box, area-ratio survivorship

| Class | Total | ≥0.25% | ≥0.5% | ≥1% | ≥2% | ≥3% |
|---|---|---|---|---|---|---|
| `goggles` | 751 | 618 (82.29%) | 290 (38.62%) | 66 (8.79%) | 9 (1.20%) | 1 (0.13%) |
| `no_goggle` | 347 | **15 (4.32%)** | 1 (0.29%) | 1 (0.29%) | 1 (0.29%) | 1 (0.29%) |
| All relevant | 1,098 | 633 (57.65%) | 291 (26.50%) | 67 (6.10%) | 10 (0.91%) | 2 (0.18%) |

### 5c. By image (decision-relevant, since each image is a candidate sample)

| Class | ≥24 px | ≥32 px | ≥48 px | ≥0.25% | ≥0.5% |
|---|---|---|---|---|---|
| `goggles` | 535 | 534 | 508 | 480 | 252 |
| `no_goggle` | 210 | 140 | **30** | **15** | 1 |

### 5d. Size bands (provisional, size-only)

| Class | `STRONG` ≥48 px | `REVIEW` 24–48 px | `TOO_SMALL` <24 px |
|---|---|---|---|
| `goggles` | 689 | 54 | 8 |
| `no_goggle` | **33** | 209 | 105 |

### 5e. The decisive finding: the two classes are annotated at different scales

| Statistic | `goggles` | `no_goggle` | Ratio |
|---|---|---|---|
| Median minimum side | **79.4 px** | **29.9 px** | **2.65×** |
| Median box area | **0.434%** | **0.069%** | **6.26×** |
| Median box W×H | 153.4 × 79.5 px | 63.4 × 30.7 px | — |
| Median normalised W×H | 0.0906 × 0.0467 | 0.0350 × 0.0180 | — |
| Median aspect ratio | 1.95 | 2.06 | ≈1 |

Aspect ratios are near-identical, but absolute scale differs by a factor of ~2.65 per side and ~6.3 in area. **This is not a distance artefact of a shared annotation policy** — if both classes were drawn around the same anatomical unit, their scale distributions would overlap. They do not.

Corroborating containment measurement (all 1,098 boxes were independently re-measured for this report):

- **751/751 `goggles`** and **347/347 `no_goggle`** boxes have their centre inside a `Person` box — both classes track a person, consistently.
- Median box height as a fraction of the containing Person-box height: `goggles` **0.0561**, `no_goggle` **0.0270**.
- A canonical head occupies roughly 0.133 of a full-body person box. So these boxes are **≈2.4× (`goggles`) to ≈4.9× (`no_goggle`) shorter than a head/face region.**
- Median box top sits at 0.087 / 0.084 of person height below the person-box top — i.e. at head height, as expected for eyewear.

**Interpretation, carefully bounded:** both classes are drawn at *eyewear scale and head-level position*, not at head/face-region scale and not at person scale. `goggles` is annotated markedly tighter around the eyewear than `no_goggle`. Per your instruction, **no semantic conclusion is drawn from geometry alone** — see §8.

## 6. Duplicates, near-duplicates, and leakage

| Finding | Count |
|---|---|
| Corrupt / unreadable images | **0** |
| Missing image–label pairs | **0** |
| Invalid YOLO rows | **0** |
| Exact duplicate groups (identical SHA-256) | **0** |
| Near-duplicate groups (dHash ≤10 bits, 64-bit) | **9** |
| Cross-split leakage candidates | **5** |

- **Zero exact duplicates.** Every one of the 1,000 images is byte-unique.
- **9 near-duplicate groups** (2–3 members each). 5 of them straddle `train`/`val` and are the leakage candidates (`split_leakage_candidates.csv`, each row tagged with its detection `basis`).
- All leakage is **source-split `train` ↔ `val`**, consistent with the archive having no independent test split. Any final split must be group-aware over these clusters or the same scene will appear in both train and test.
- Adjacent-numbered filenames (`ultralytics-ppe-nov2026-NNN`) were checked in Phase 1 and are **not** near-duplicates (min dHash 18, median 31) — the numbering is shuffled, not a video sequence, so no frame-adjacency grouping is justified. The 9 groups above are detected from image content, not from filename adjacency.
- Leakage count here is 5 after de-duplicating findings that the exact- and near-duplicate detectors would otherwise both report; see §12.

## 7. Annotation errors

**None found.** Across all 1,000 label files and 1,098 relevant rows:

- every row has exactly 5 fields;
- class IDs parse as integers and are canonical;
- coordinates parse as finite floats;
- width and height are strictly positive;
- centres lie within [0,1];
- box edges lie within the image with a 1e-4 tolerance.

`annotation_issues.csv` contains a header only. This is a genuine pass, not a skipped check: the same validator is unit-tested against malformed rows in `tests/test_screen_construction_ppe.py`.

## 8. Visual semantic review — **requires your eyes; not yet concluded**

**I cannot view images.** Attempting to open the PNGs returns an image-input-unavailable error, so I have no visual evidence and will not manufacture any. Everything below is either measured geometry (§5) or explicitly deferred to you.

What the geometry *establishes* (this is safe to state):

- Neither class is annotated at **person** scale.
- Neither class is annotated at **head/face-region** scale — both are materially shorter than a head.
- Both classes sit at **head height** within a `Person` box.
- The two classes are **not** annotated at the *same* scale: `no_goggle` is about 2.65× smaller per side than `goggles`.

What the geometry **cannot** establish, and what you must judge on the sheets:

- Whether `goggles` boxes tightly enclose the goggles object, or enclose an eye/upper-face band that happens to be eyewear-sized.
- Whether `no_goggle` boxes enclose a bare eye region, a whole upper face, or a deliberately looser "no equipment here" region.
- Whether `no_goggle` frames are front-facing, side-facing, or rear-facing; and how often the eyes are actually visible.
- Whether the small-box population is distant-but-usable or simply unusable.

**The head/face-region compliance labels proposed in `plan.md` are incompatible with these box sizes.** A model trained to predict a ~30 px eyewear patch for `no_goggle` and a ~79 px eyewear patch for `goggles` would not be detecting "a person whose face region is or is not protected"; it would be reproducing two different, class-dependent annotation conventions. That mismatch is the central risk, and it is a labelling-convention risk rather than a mere resolution risk.

Contact sheets for this judgement (12 tiles each, tile caption carries filename / split / class / box outline / minimum side px / area %):

| Sheet | Path (Git-ignored) |
|---|---|
| Largest `goggles` | `.../artifacts/contact_sheet_01_largest_goggles.png` |
| Medium `goggles` | `.../artifacts/contact_sheet_03_medium_goggles.png` |
| Smallest `goggles` | `.../artifacts/contact_sheet_05_smallest_goggles.png` |
| Random `goggles` (fixed seed) | `.../artifacts/contact_sheet_07_random_goggles.png` |
| Largest `no_goggle` | `.../artifacts/contact_sheet_01_largest_no_goggle.png` |
| Medium `no_goggle` | `.../artifacts/contact_sheet_03_medium_no_goggle.png` |
| Smallest `no_goggle` | `.../artifacts/contact_sheet_05_smallest_no_goggle.png` |
| Random `no_goggle` (fixed seed) | `.../artifacts/contact_sheet_07_random_no_goggle.png` |
| Near-duplicate groups | `.../artifacts/contact_sheet_09_near_duplicate_groups.png` |
| Cross-split leakage groups | `.../artifacts/contact_sheet_10_split_leakage_groups.png` |

Full prefix: `data/reviewed/construction_ppe_screen/artifacts/`

## 9. Estimated useful candidates

Per-class counts that clear each bar (image-level, so each row is a sample you could actually use):

| Class | ≥48 px (strong) | ≥24 px (incl. review band) | ≥0.25% area |
|---|---|---|---|
| `goggles` | **508 images** | 535 images | 480 images |
| `no_goggle` | **30 images** | 210 images | **15 images** |

Against `plan.md`'s target of **≥~250 useful instances per class**:

- **`goggles` clears the bar comfortably** — 508 images at ≥48 px, before any semantic review.
- **`no_goggle` does not** — 30 images at ≥48 px, 15 at ≥0.25% area. Even the optimistic ≥24 px reading gives 210, and that population is mostly in the 24–48 px band the screening rules call marginal and which still needs semantic review.

Treat these as **upper bounds**: every one of them is subject to your visual judgement, and no semantic filtering has happened yet. The realistic count after review will be lower.

## 10. Suitability verdict

**`SUPPLEMENTARY_ONLY`** — provisional.

Reasoning:

- The **`goggles` class** is a genuine, large, cleanly annotated asset (751 boxes, 0 annotation errors, 0 duplicates, 508 images at ≥48 px). It is immediately useful and carries real value for this project.
- The **`no_goggle` class** cannot meet the plan's per-class target at any size bar that keeps the eyes legible, and is annotated at roughly 2.65× smaller scale than `goggles`. Using it as the sole violation source would build a model whose negative class is defined by a different convention than its positive class.
- Therefore the archive is **not a `MAIN_SOURCE` for the required two-class head/face-region task**, but it is **not `REJECTED`** either: its `goggles` class is too good to discard, and its `no_goggle` boxes are still a legitimate source of *candidate* negatives pending review.

I am not raising this above `SUPPLEMENTARY_ONLY` on geometry alone, and I am not lowering it to `REJECTED` without your visual confirmation. If your review finds that `no_goggle` boxes are predominantly rear-facing or eye-occluded, the honest verdict for that class is `REJECTED` and the archive becomes goggles-supplement-only.

## 11. Is a different close-up dataset required?

**Yes, for `no_goggles` in particular.**

The project needs negatives where the eyes are large and legible — face-dominant frames at close range. This archive's negatives are `no_goggle` regions whose median minimum side is ~30 px inside 1360–2048 px images, i.e. people framed at mid-to-far distance amid helmets, vests, and construction background. No amount of retraining recovers eye detail that was never resolved in the source pixels.

For `goggles`, a second dataset is **not** strictly required: 508 usable images is a workable start.

Important constraint from your instructions: this screen did **not** download anything, and no additional dataset may be incorporated yet. Selecting and licensing a face-dominant close-up dataset is a separate decision that needs your approval.

## 12. Would phone-screen images stay large enough for the webcam demo?

**Marginal for `no_goggle`; plausible for `goggles`.** Flagged as an estimate, not a measurement — it should be confirmed with the real phone/webcam path.

Illustrative arithmetic, using median normalised box sizes on a phone screen whose displayed photo area is ~1,100 physical px wide:

| Class | Normalised W×H | On-screen box | Verdict |
|---|---|---|---|
| `goggles` | 0.0906 × 0.0467 | ~100 × ~51 px | Likely detectable |
| `no_goggle` | 0.0350 × 0.0180 | ~38 × ~20 px | Borderline |

A ~20 px-tall target on a phone screen, then re-imaged by a 1080p USB webcam that does not fill the frame with the phone, will land in the low tens of pixels. That is close to the resolution limit of the plan's own 640 px YOLO input, and it is the weakest link in the demonstration path. This is a second, independent reason to prefer face-dominant close-up negatives.

Mitigation if Construction-PPE is used for demo material: display single images full-screen (crop out surrounding construction context) rather than full scene photos.

## 13. Limitations of this screening

1. **No visual verification was performed by me.** Every semantic statement is deferred to you (§8). The verdict is conditioned on your review.
2. **Size is a proxy.** Minimum side and area ratio correlate with legibility but do not measure blur, occlusion, rear-facing poses, or lighting — all of which require sight.
3. **Near-duplicate detection is perceptual, not semantic.** dHash at ≤10/64 bits catches rescaled and re-encoded copies and near-identical scenes; it will miss same-scene images shot from a different angle, and it may group visually similar but distinct scenes. The 9 groups are candidates for inspection, not confirmed duplicates.
4. **No test split exists in the source**, so all leakage found is `train`/`val`; a future test partition inherits the same grouping problem and must reuse these clusters.
5. **`no_goggle` area statistics rest on a very thin tail.** Every area threshold from 0.5% to 3% is satisfied by exactly **one** box (`source-9ec80cefaef867295b0c9b5b6c7757c8`, 3.08%). That is a single unusually close subject, not evidence of a usable population — it should not be read as "1 usable high-area negative per threshold" in any meaningful sense.
6. **Annotator intent is unobservable.** Whether `no_goggle` was meant to mark "bare eyes", "no eyewear on this person", or something else cannot be recovered from the files; the archive README only says missing-PPE labels "refer to visible body regions".
7. **Single archive, single sampling.** No cross-dataset comparison was performed (and none was permitted).

## 14. Human decisions still required

`data/reviewed/construction_ppe_screen/manual_review_queue.csv` holds all **1,098** relevant boxes with `human_decision` **100% blank** for you to fill.

1. **Fill `human_decision`** for the boxes that matter most — start with the 30 `no_goggle` boxes at ≥48 px and the 33 in the `STRONG` band, then the 209 in the 24–48 px `REVIEW` band. `recommended_action` is provisional and size-only; override it freely.
2. **Confirm or reject the eyewear-vs-face reading** in §8. Specifically: is `goggles` an eyewear object, and is `no_goggle` the same kind of region or a looser one?
3. **Judge pose and eye visibility** on the `no_goggle` sheets — how many are front-facing with visible eyes versus side/rear/occluded? This decides whether `no_goggle` is salvageable at all.
4. **Confirm or overturn the `SUPPLEMENTARY_ONLY` verdict** in §10.
5. **Decide on a face-dominant close-up dataset** for the negative class (§11) — whether to source one, and under what licence.
6. **Confirm the demo legibility estimate** (§12) against the real phone-plus-webcam arrangement.
7. **Decide the final label convention**: keep both classes at the same semantic unit as `plan.md` requires, which — given §5e — implies re-labelling or discarding `no_goggle` rather than training on it as-is.

No decision above has been pre-empted, and no dataset, split, remap, or model was produced.
