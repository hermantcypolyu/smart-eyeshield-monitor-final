# Phase 2C - Domain-V2 Fine-Tuning

Status: **runbook prepared and locally validated. No fine-tuning has been run.
No V2 performance is claimed.**

This document is the prose companion to
[`training/colab_domain_v2_finetuning.ipynb`](../training/colab_domain_v2_finetuning.ipynb).
It defines what the run does, the exact Colab steps, the settings that are fixed
in advance, and the evidence that must exist before any V2 number may be
reported. It contains **no measured numbers**, because none exist yet.

V1 performance lives in `experiments/ai_metrics.csv` (row AI-01). V2 numbers
come only from `domain_v2_test_report.json`, produced by the notebook. The two
never share a file and never share a schema.

---

## 1. Why this run exists

The V1 detector was trained on the 240-image Phase 2B baseline and reaches
P 0.8040 / R 0.7993 / F1 0.8016 / mAP50 0.8439 / mAP50-95 0.4867 on its locked
40-image test split. On live webcam frames it exhibits a **false-safe** failure:
a bare-eye face is occasionally reported as `goggles`.

A false safe is the worst possible error for this device. `goggles` means
"shield present, do not warn"; `no_goggles` means "warn". If a bare face is read
as `goggles`, the alarm never fires. V1's aggregate F1 does not expose this,
because the baseline was captured under different lighting, framing, and camera
geometry than the deployment position.

The remedy is a small, deliberate **transfer-learning** step: start from the
already-trained V1 checkpoint and adapt it to 60 newly captured webcam images
(Domain V2) that share the deployment camera's actual appearance.

---

## 2. Ground rules

1. **Start from V1, never from COCO.** The initial weights are
   `models/best.pt` (YOLOv8n, already fine-tuned on the eye-shield task). The
   COCO seed checkpoint is not used anywhere in this runbook.
2. **`models/best.pt` is read-only.** V2 is written to `best_v2.pt`. V1 remains
   the committed baseline and the fallback for the live demo.
3. **The locked test split is not touched.** No image moves, no label changes,
   no re-split. The on-disk dataset is never modified; only the YAML handed to
   the validator differs.
4. **No V2 number is quoted from a training log.** Metrics come from the report
   JSON written in section 15 of the notebook, and from nowhere else.
5. **`experiments/ai_metrics.csv` is not appended to.** That file is append-only
   with a frozen AI-01 column set, written only by
   `training/export_metrics.py`. A V2 fine-tuning result is not an AI-01
   experiment and must not be recorded as one.
6. **No fabricated metrics.** If a value is unavailable it is written as `null`.
   A `NaN` in a report is a bug, not a result.

---

## 3. What you upload

Exactly two files, uploaded in section 3 of the notebook:

| File | Size | Contents |
| --- | --- | --- |
| `eye_shield_domain_v2.zip` | 7,920,538 B | 300 images, 314 boxes, archive root `eye_shield_domain_v2/` |
| `best.pt` | 6,508,202 B | V1 checkpoint, SHA-256 `9366b73b35d4f820af401808aa4e0e58cb29a7b557991b123e5cd6a813fa3591` |

The ZIP is produced locally by
[`tools/build_domain_v2_dataset.py`](../tools/build_domain_v2_dataset.py). It
carries a machine-specific absolute `path:` in its shipped `data.yaml`, so the
notebook **regenerates** that YAML for Colab rather than trusting it.

The notebook prints the `best.pt` checksum and warns (does not fail) if it
differs from the recorded V1 file, so a wrong upload is visible immediately.

---

## 4. Dataset V2 in numbers

Verified by the notebook's integrity cell against the real archive:

| Split | Images | Label files | Boxes | `goggles` | `no_goggles` |
| --- | --- | --- | --- | --- | --- |
| train | 202 | 202 | 214 | 107 | 107 |
| val | 52 | 52 | 52 | 26 | 26 |
| test | 46 | 46 | 48 | 23 | 25 |
| **total** | **300** | **300** | **314** | **214** | **216** |

Integrity checks that must all report zero: empty label files, malformed rows,
out-of-range class ids, non-positive box width/height, coordinates outside
`[0, 1]`, and images without labels or labels without images (checked in both
directions).

Class composition: 240 images from the Phase 2B baseline (re-labelled through
the mapping `0 eyes_with_goggles -> goggles`, `1 eyes_without_goggles ->
no_goggles`; classes 2 and 3 are dropped) plus 60 freshly captured webcam images
split 42 train / 12 val / 6 test.

**The six webcam test stills are the entire target-domain test evidence.** They
are `goggles_webcam_001/024/028` and `no_goggles_webcam_003/007/018`. Any V2
metric is dominated by the 40 baseline-derived test images and is therefore a
weak estimate of deployment behaviour. That caveat is written into the report
JSON as a `caveat` field and must be quoted alongside every number.

---

## 5. The test-split firewall

The test split is the only honest estimate of field performance, so it is
structurally protected rather than protected by discipline.

`training/train_yolov8n.write_train_yaml()` emits only `path`, `train`, `val`,
and `names` - there is no `test:` key at all. `model.train()` therefore cannot
see the test images.

Evaluation is the mirror image. `model.val()` reads whatever split the YAML
calls `val`, so `training/evaluate_model.build_test_yaml()` writes a YAML whose
`val:` points at `test/images`. The dataset on disk is untouched.

The notebook implements both halves explicitly:

| File | Keys | Used for |
| --- | --- | --- |
| `/content/domain_v2_colab.yaml` | `path`, `train`, `val`, `test`, `names` | printed for inspection, section 7 |
| `/content/domain_v2_train_only.yaml` | `path`, `train`, `val`, `names` | passed to `model.train()`, section 9 |

The training cell re-asserts `"test" not in` the training YAML immediately
before it calls `model.train()`, so a later edit cannot silently reintroduce the
leak.

---

## 6. Colab steps

1. Open [Google Colab](https://colab.research.google.com), then
   **File -> Upload notebook** and choose
   `training/colab_domain_v2_finetuning.ipynb`.
2. **Runtime -> Change runtime type -> T4 GPU**. Save.
3. **Runtime -> Run all.** The notebook hard-stops with a `RuntimeError` if CUDA
   is unavailable; it will not fine-tune on CPU.
4. When the upload dialog appears, select **both** `eye_shield_domain_v2.zip`
   and `best.pt`.
5. Let it run. Sections 1-10 are preparation and validation and finish in well
   under a minute; section 11 is the only long cell (25 epochs on 202 images).
6. Section 18 downloads `eye_shield_v2_results.zip` automatically.
7. Install locally with the five commands printed at the end of the notebook.
8. Run the live acceptance protocol (section 9 of this document) **before**
   promoting V2 or quoting any number.

Nothing in this list overwrites `models/best.pt`, the AI-01 metrics row, the
locked test dataset, or `training/colab_training.ipynb`.

---

## 7. Fixed training settings

All of these are decided before the run and are printed by the notebook:

| Setting | Value | Reason |
| --- | --- | --- |
| start weights | `/content/best.pt` (V1) | adapt the trained detector, do not relearn from COCO |
| `epochs` | 25 | 202 target images; 60 would overfit |
| `patience` | 8 | early stop on the val split |
| `imgsz` | 640 | identical geometry to V1 and the deployment pipeline |
| `batch` | 16 | fits a 16 GB T4 at 640 px |
| `seed` | 42 | reproducibility |
| `deterministic` | `True` | reproducibility |
| `device` | `0` | the T4 |
| `workers` | 2 | Colab-safe dataloader count |
| `project` / `name` | `/content/runs` / `eye_shield_domain_v2` | artifact location |
| `exist_ok` | `True` | the cell is re-runnable |
| `optimizer` | `auto` | Ultralytics picks for the schedule |
| `cache` | `False` | the dataset is small but the Colab disk is not the bottleneck |
| `plots` | `True` | curves and confusion matrix are evidence |
| `val` / `split` | `True` / `"val"` | early stopping uses val only |

`ultralytics==8.4.215` is pinned and installed with `%pip` (not `!pip`, which
does not interpolate the pin) so the version assert is meaningful.

If CUDA runs out of memory, set `BATCH = 8` and re-run that cell only. Change
`IMGSZ` only as a last resort, because image size changes the geometry the
deployment pipeline was tuned against.

---

## 8. Evaluation design

Two evaluations, in this order, and the test one runs exactly once:

1. **Validation split** (`domain_v2_val_eval`) - the split early stopping used.
   Reported for completeness only; it is not independent evidence.
2. **Test split** (`domain_v2_test_eval`) - the single target-domain estimate.
   Nothing is tuned after this cell.

Metrics are read from the Ultralytics `BoxMetrics` object the same way
`training/evaluate_model.extract_metrics()` does locally: `box.ap_class_index`,
`box.p`, `box.r`, `box.ap50`, `box.ap`, `box.mean_results()`, and
`results.speed`. F1 is computed as $2PR/(P+R)$ with an explicit $P+R=0$ guard,
and every value is checked finite before it is written.

Two reports are produced, in a schema that is deliberately **not** the AI-01
CSV schema:

- `/content/domain_v2_val_report.json`
- `/content/domain_v2_test_report.json`

Each report records the start-weight identity and SHA-256, the environment, the
split counts, per-class boxes and metrics, aggregate metrics, speed, the
confusion matrix, and the caveat string.

The confusion matrix matters more than the aggregates here: the
`no_goggles -> goggles` cell is the false-safe direction, and it is the number
to read first.

---

## 9. Live acceptance protocol

Offline metrics on 46 test images, 6 of which are webcam stills, cannot
establish that the false-safe bug is fixed. A live check is mandatory before any
V2 claim or promotion.

Run against V2 **without** overwriting V1:

```bash
WEIGHTS_PATH=models/best_v2.pt .venv/bin/python tools/live_eye_shield_demo.py
```

Required coverage, at the deployment camera position:

| Condition | Front | Left | Right |
| --- | --- | --- | --- |
| goggles worn correctly | 10 | 5 | 5 |
| bare eyes (no goggles) | 10 | 5 | 5 |
| empty scene (no person) | 5 | - | - |

Total: 45 observations.

Pass criteria:

- **Zero false safes.** A single bare-eye frame confirmed as `goggles` rejects
  the run, regardless of the offline metrics.
- No bare-eye frame reaches `VIOLATION_CONFIRMED` later than the configured
  `confirmation_seconds` (2.0 s).
- No goggle-wearing frame raises a false violation.
- Empty scenes stay `UNKNOWN` and never raise a violation.
- The five empty scenes confirm the model does not hallucinate a face.

Record the raw counts. A "looks better" impression is not evidence.

---

## 10. Result bundle

`/content/eye_shield_v2_results.zip` contains only files that actually exist;
missing artifacts are listed by name rather than stubbed. Expected manifest:

```
weights/best_v2.pt
weights/last_v2.pt
domain_v2_val_report.json
domain_v2_test_report.json
environment.json
train_args.json
plots/results.png
plots/results.csv
plots/confusion_matrix.png
plots/confusion_matrix_normalized.png
plots/BoxF1_curve.png
plots/BoxPR_curve.png
plots/BoxP_curve.png
plots/BoxR_curve.png
plots/labels.jpg
plots/args.yaml
predictions/...          (annotated webcam stills + contact sheet)
```

The curve files are globbed rather than hardcoded: Ultralytics writes
`BoxF1_curve.png` and friends with a fixed `Box` prefix, and a hardcoded name
would break silently if that ever changed.

---

## 11. Local install

```bash
unzip -o ~/Downloads/eye_shield_v2_results.zip -d /tmp/v2
ls -l /tmp/v2/weights/best_v2.pt
cp /tmp/v2/weights/best_v2.pt models/best_v2.pt     # only after live acceptance passes
WEIGHTS_PATH=models/best_v2.pt .venv/bin/python tools/live_eye_shield_demo.py
```

`models/best.pt` stays V1. It is never overwritten with V2.

---

## 12. Checklist before any V2 number is reported

- [ ] `environment.json` records the GPU actually used (T4 expected).
- [ ] `domain_v2_test_report.json` exists. It is the **only** source of V2
      numbers.
- [ ] The test split was evaluated **once**. If the model was re-trained after
      looking at test metrics, the split is contaminated and the report must say
      so.
- [ ] The `caveat` field is quoted with the numbers: 46 test images, of which 6
      are webcam stills.
- [ ] The confusion matrix was inspected, specifically `no_goggles -> goggles`.
- [ ] V1's AI-01 row in `experiments/ai_metrics.csv` is unchanged and no V2 row
      was appended.
- [ ] `models/best.pt` is still V1 (SHA-256 `9366b73b...fa3591`).
- [ ] Live acceptance ran with zero false safes.
- [ ] `training/colab_training.ipynb` is unmodified.

Until every box is ticked, V2 has no performance, only a hypothesis.
