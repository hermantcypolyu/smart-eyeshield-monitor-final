# Dataset Audit — Phase 1

**Project:** EIE3127 Smart Biohazard & Laser Lab Eye-Shield Monitor
**Phase:** 1 — Dataset Audit and Preparation
**Tool:** `tools/inspect_dataset.py` (read-only, reproducible)
**Report status:** audit complete; dataset **rejected for training**. No training,
no relabelling, and no split changes were performed.

Every number below is reproduced by running the auditor; the machine-readable
counterpart of each figure is named in the "Evidence" line.

---

## 1. Source archive identity

| Property | Value |
|---|---|
| Path | `data/raw/roboflow_safety_glasses_original.zip` |
| Size | 25,717,848 bytes |
| SHA-256 | `39afc3ac747c281e6165732f82f512fb6fcbbe15f0a0e7d58015d901c3e2cf53` |
| Members | 805 |
| Licence (declared) | CC BY 4.0 |
| Tracking | untracked and git-ignored (`data/**/*.zip`) |

The archive is treated as immutable. The auditor opens it with
`zipfile.ZipFile(..., "r")` and never writes to it; the checksum above was
re-computed after the audit run and is unchanged.

Evidence: `data/reviewed/audit/dataset_summary.json` → `source`.

**Provenance — recorded, not blocking.** The archive on disk is a fork/export of
the dataset named in the plan. The relationship is now recorded and closed:

| Field | Value |
|---|---|
| Original source | `workspace-3vzwb/safety-glasses-dataset` |
| Fork / export workspace | `eie3360-fwbnu` |
| Licence | CC BY 4.0 |

The export identifies itself as workspace `eie3360-fwbnu`, project
`safety-glasses-dataset-zyrcw`. The differing workspace name is the expected
consequence of exporting a fork, not a dataset substitution. This is **not a
blocker**: it requires no further download and no new fork. Attribution must name
the original source `workspace-3vzwb/safety-glasses-dataset` under CC BY 4.0 in
the final submission.

## 2. Declared metadata

`data.yaml` declares `nc: 5` and `names: ['boots', 'glasses', 'gloves', 'helmet',
'vest']`, with `train: ../train/images`, `val: ../valid/images`,
`test: ../test/images`. `nc` equals `len(names)`, so the file is internally
consistent on that point. Roboflow `version: 1`, licence CC BY 4.0.

Two `README` members are present (`README.roboflow.txt`, `README.dataset.txt`)
alongside three duplicate `data.yaml` entries in the archive listing; the auditor
reads the first and records the duplicates rather than assuming uniqueness.

Evidence: `dataset_summary.json` → `metadata`, `source.duplicate_archive_members`.

## 3. Inventory: images, labels, instances

| Split | Images | Readable | Label files | Instances | Empty labels | Malformed rows | Missing labels | Orphan labels |
|---|---|---|---|---|---|---|---|---|
| train | 301 | 301 | 301 | 534 | 1 | 1 | 0 | 0 |
| valid | 82 | 82 | 82 | 200 | 0 | 1 | 0 | 0 |
| test | 17 | 17 | 17 | 36 | 0 | 0 | 0 | 0 |
| **total** | **400** | **400** | **400** | **770** | **1** | **2** | **0** | **0** |

All 400 images decode successfully — there are **no corrupt or unreadable
images** (`corrupt_images.csv` contains only its header). Every image has a
matching label file and every label file has a matching image, so the
image/label pairing is structurally complete.

Evidence: `dataset_summary.json` → `splits`, `source_manifest.csv`,
`image_statistics.csv`, `corrupt_images.csv`.

## 4. Split sizes and the test-set shortfall

Shares are 75.25 % train / 20.50 % valid / 4.25 % test. `plan.md` §5.5 asks for
roughly 10 % test. The test split holds **17 images and 36 instances**, which is
far too small to support any later evaluation claim.

**Blocking.** Recorded in `blocking_findings`.

## 5. Class distribution

| ID | Class | Instances | Images containing | Median area ratio | Share under 2 % area | In project scheme |
|---|---|---|---|---|---|---|
| 0 | boots | 1 | 1 | 0.0103 | 100 % | no |
| 1 | glasses | 435 | 397 | 0.0064 | 78.9 % | no |
| 2 | gloves | 212 | 134 | 0.0177 | 60.9 % | no |
| 3 | helmet | 27 | 25 | 0.1176 | 3.7 % | no |
| 4 | vest | 95 | 94 | 0.0672 | 1.1 % | no |

`boots` is effectively a single annotated instance, so the export is in practice
a four-class dataset with a stray label.

Evidence: `class_counts.csv`, `dataset_summary.json` → `class_audit`.

## 6. The required two-class scheme is absent

The project needs exactly two classes, `goggles` and `no_goggles`
(`REQUIRED_CLASSES`). The export has **neither**: `missing_required_classes` is
`["goggles", "no_goggles"]` and `has_no_goggles_class` is `false`.

`no_goggles` is a *negative* class and cannot be derived from the absence of a
`glasses` box — a frame with no annotation may simply be unannotated. The
auditor therefore reports the absence and never synthesises the class.

**Blocking.** This single finding makes the archive unusable for the project as
exported, independent of every other issue.

## 7. `glasses` is not `goggles`

The `glasses` class merges genuine safety goggles with prescription spectacles
and sunglasses. Box geometry is the reason this class cannot be reused as-is for
the compliance labels this project needs:

- median area ratio **0.0064** of the frame, i.e. roughly 0.6 % of the image;
- median aspect ratio **1.86** (wide and short);
- **78.9 %** of `glasses` boxes are below the 2 % area floor.

These are eyewear-level boxes. They are **incompatible with the proposed
head/face-region compliance labels**, which require a box covering the worn
region on a person rather than the eyewear object alone. Geometry alone cannot
establish whether a given image is a product cut-out or a worn item, so the
semantic content of the `glasses` images **requires human visual review** before
any reuse decision is made.

Consequence: `glasses` **must not** be silently renamed to `goggles`. Doing so
would train the safe/violation decision on the wrong concept, at the wrong box
granularity.

## 8. Annotation quality

Two malformed rows, both polygon/segmentation rows (9 fields) that cannot be
parsed as YOLO detection rows (5 fields):

| Split | File | Detail |
|---|---|---|
| train | `1396_jpg.rf.d142b4780ad667d22672efd3125bca09.txt` | line 1: 9 fields |
| valid | `1346_jpg.rf.c053300d8a0e8c275584d9cddb672fee.txt` | line 1: 9 fields |

Also recorded: 9 `box_exceeds_frame` warnings (a box partly outside the frame)
and 1 `empty_label_file` warning (train,
`prescription-safety-glasses-on-face_6_jpeg_jpg.rf.6e0a9a217300ea20426b7043f66baf09.txt`).
These are warnings, not blocking findings.

Evidence: `annotation_issues.csv`, `annotation_statistics.csv`.

## 9. Duplicates

- **Exact duplicates (SHA-256): 0 groups.** No two images are byte-identical.
- **Near duplicates (dHash-64, Hamming ≤ 10): 377 pairs.**

The high near-duplicate count is expected: much of this export is sampled from
video, so consecutive frames are legitimately almost identical. The auditor
records them rather than deleting them, and the count alone is not treated as a
defect.

Evidence: `exact_duplicates.csv`, `near_duplicates.csv`,
`dataset_summary.json` → `duplicates`.

## 10. Split leakage

**59 leakage groups**, of which:

| Kind | Groups |
|---|---|
| `near_duplicate_across_splits` | 49 |
| `adjacent_frame_group_across_splits` | 5 |
| `source_stem_across_splits` | 5 |

The five source-stem groups are `SG6` and `SG8` (train+test) and `SG11`, `SG2`,
`SG14` (train+valid). A test image that shares a source stem with a training
image leaks: any later test score would be optimistic.

The auditor **reports only**; `leakage.action_taken` is
`"none - reported only, splits were not modified"`.

Evidence: `split_leakage.csv`, `dataset_summary.json` → `leakage`.

## 11. Compliance mapping

`compliance_mapping.decision` is `unmapped_pending_human_review`, and every
source class is `unmapped`. No source class maps to the project's
safe/violation decision, because no source class is `goggles` or `no_goggles`.

## 12. Size targets

`TARGET_MIN_IMAGES` 500, `TARGET_MAX_IMAGES` 800,
`TARGET_MIN_INSTANCES_PER_CLASS` 250. With 400 images the dataset is below the
minimum, and only `glasses` (435) clears the per-class instance floor. No class
reaches the target *within the required scheme*, since neither required class
exists.

## 13. Determinism and reproducibility

The auditor was run twice. All 13 records were compared; after masking only the
run timestamps (`generated_at_utc`, `started_at_utc`, `finished_at_utc`) and the
`arguments.out_dir` field, **every output is byte-identical**. The manual-review
queue is sampled with a fixed seed (`1337`) and then re-sorted, so it is stable
across runs and platforms.

Manual review queue size: 400 rows (`manual_review_queue.csv`), capped at
`MAX_REVIEW_ROWS`.

## 14. Automated verification

`tests/test_inspect_dataset.py` adds 23 tests over synthetic, self-generated
fixtures — no real dataset file is read, so the suite is offline and carries no
licensing obligation. Full suite: **131 passed** (108 Phase 0 + 23 Phase 1).

Phase 0 regression re-run after Phase 1 changes:

- `tools/env_check.py` → `Environment check OK: 5 passed, 1 warning(s), 0 failure(s)` (exit 0). The single warning is the expected "weights not present until Phase 2".
- `tools/camera_check.py` → 30.0 FPS at 1280×720, `Camera check PASSED` (exit 0).

## 15. Verdict

**`rejected_for_training`** — five blocking findings:

1. required classes `goggles` and `no_goggles` absent from `data.yaml`;
2. `data.yaml` declares 5 classes where the project scheme is exactly 2;
3. 59 split-leakage groups present;
4. test split holds 4.25 % of images against a ~10 % target;
5. 2 malformed annotation rows.

Items 1 and 2 are disqualifying on their own. Items 3–5 are repairable by
curation, but no repair was attempted here because that requires a human
decision about the safe/violation definition and the label granularity.

Provenance is **not** among the blocking findings: the fork/export relationship
is recorded in §1 and requires no re-download.

## 16. What a human must decide next

1. **The two classes.** Decide whether `goggles`/`no_goggles` can be sourced at
   all from this export, or whether a different acquisition is required. Absence
   of `glasses` boxes must not be read as `no_goggles`.
2. **Visual review of `glasses`.** Inspect the `glasses` images directly to
   determine what they actually depict and whether any are usable as
   `goggles` evidence at head/face-region granularity. Box statistics cannot
   answer this.
3. **The five leaking source groups** (`SG6`, `SG8`, `SG11`, `SG2`, `SG14`) and
   the 49 near-duplicate cross-split pairs: assign each source group to exactly
   one split.
4. **The two malformed rows** listed in §8: re-annotate as boxes or drop.
5. **The empty label file** in §8: confirm whether the frame genuinely contains
   no target, or is an annotation miss.

Files needing human review, by name:

- `train/1396_jpg.rf.d142b4780ad667d22672efd3125bca09.txt`
- `valid/1346_jpg.rf.c053300d8a0e8c275584d9cddb672fee.txt`
- `train/prescription-safety-glasses-on-face_6_jpeg_jpg.rf.6e0a9a217300ea20426b7043f66baf09.txt`
- the 9 `box_exceeds_frame` rows in `annotation_issues.csv`
- the 5 source-stem leakage groups in `split_leakage.csv`

**No further phase was started.** Training, relabelling, split rewriting, and
class remapping are all explicitly out of scope for this audit.

---

## Reproducing this audit

```bash
.venv/bin/python tools/inspect_dataset.py                  # writes data/reviewed/audit/
.venv/bin/python tools/inspect_dataset.py --strict-fail    # CI: exit 3 on blocking findings
.venv/bin/python -m pytest -q                              # 131 tests
```

Outputs are the 13 records in `data/reviewed/audit/`. Rendered contact sheets go
to `data/reviewed/audit/artifacts/`, which is git-ignored because it contains
dataset imagery. The contact sheets carry filename, split, class and box id only —
they are never annotated with a compliance verdict, since none has been decided.
