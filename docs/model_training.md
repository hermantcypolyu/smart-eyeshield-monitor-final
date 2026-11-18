# Phase 2B — Model Training

Status: **pipeline prepared and locally validated. No training has been run. No
performance is claimed.**

This document defines the training protocol, the exact Colab steps, and the
evidence that must be produced before any metric may be reported. It contains
**no measured numbers**, because none exist yet. If a number appears in this
project's reports, it came from `experiments/ai_metrics.csv`, which is written
only by `training/export_metrics.py` from a real evaluation report.

---

## 1. What is being trained

| Item | Value |
| --- | --- |
| Task | Object detection (single stage) |
| Architecture | Ultralytics YOLOv8n (`yolov8n.pt` COCO weights) |
| Classes | `0 goggles`, `1 no_goggles` (from [`config/classes.yaml`](../config/classes.yaml)) |
| Dataset | [`data/processed/eye_shield_baseline/`](../data/processed/eye_shield_baseline/) |
| Images | 240 total — 160 train / 40 val / 40 test, balanced 120/120 |
| Image size | 640 |
| Max epochs | 60 |
| Early-stopping patience | 10 |
| Seed | 42 |
| Deterministic | yes (`deterministic=True`) |
| Batch | derived from GPU memory (`auto_batch`) |

Class semantics are not negotiable. Class 0 means *genuine safety goggles worn
over the eyes*; class 1 means *the eye region is assessable but goggles are
absent or worn incorrectly*. Inverting them would silently invert every safety
decision downstream, so the pipeline refuses to run against a mismatched
`data.yaml`.

---

## 2. The locked test split

The test split is the only honest estimate of field performance, so it is
protected structurally rather than by convention.

**Training cannot see the test split.** `train_yolov8n.write_train_yaml()` emits
a training YAML containing exactly four keys — `path`, `train`, `val`, `names`.
There is no `test:` key at all. Ultralytics resolves splits from that file, so
the trainer has no route to `test/images`. This is enforced by
`tests/test_training_pipeline.py::test_training_yaml_never_mentions_the_test_split`.

**Evaluation deliberately redirects the `val` key.** `model.val()` always reads
whatever the YAML calls `val`. `evaluate_model.build_test_yaml()` therefore
writes an evaluation YAML whose `val:` points at `test/images`, leaving the
on-disk dataset untouched. Covered by
`test_eval_yaml_points_val_at_the_test_split` and
`test_eval_yaml_does_not_modify_the_on_disk_dataset`.

**The test split may not be used for:** training, early stopping, confidence
threshold selection, augmentation selection, or hyper-parameter tuning. It is
evaluated **once**, after training is final.

> If you look at test metrics and then re-train, the split is no longer locked.
> Any result after that point must be reported as contaminated, in the notes
> column of `experiments/ai_metrics.csv`.

---

## 3. Files

| File | Purpose |
| --- | --- |
| `training/train_yolov8n.py` | Validates the dataset, writes the training-only YAML, trains. `--dry-run` validates without training. |
| `training/evaluate_model.py` | Evaluates `best.pt` once on the locked test split; writes `experiments/model_eval_test.json`. |
| `training/export_metrics.py` | Appends a measured report to `experiments/ai_metrics.csv`. Refuses fabricated or malformed input. |
| `training/colab_training.ipynb` | The GPU runbook. Installs a pinned Ultralytics, trains, evaluates, packages artifacts. |
| `docs/model_training.md` | This document. |

### Local commands

```bash
# Validate the dataset and print the resolved run configuration. No training.
.venv/bin/python training/train_yolov8n.py --dry-run

# Train locally (requires a real accelerator; see section 7).
.venv/bin/python training/train_yolov8n.py --device auto --batch auto

# Evaluate once on the locked test split.
.venv/bin/python training/evaluate_model.py --weights models/best.pt

# Record the measured result. Append-only.
.venv/bin/python training/export_metrics.py --report experiments/model_eval_test.json
```

---

## 4. Exact Colab steps

The local machine has no CUDA GPU, so formal training happens in Colab.

### Step 1 — build the dataset ZIP on the Mac

Dataset images and labels are Git-ignored, so they are not in the repository.
Build the upload ZIP:

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject
rm -f /tmp/eye_shield_baseline.zip
cd data/processed && zip -qr /tmp/eye_shield_baseline.zip eye_shield_baseline \
  -x 'eye_shield_baseline/artifacts/*' && cd -
unzip -l /tmp/eye_shield_baseline.zip | head
ls -lh /tmp/eye_shield_baseline.zip
```

The archive root must contain one directory, `eye_shield_baseline/`, holding
`data.yaml`, `train/`, `val/` and `test/`. Contact sheets are excluded — they are
review artifacts, not training data.

### Step 2 — open the notebook in Colab

Upload [`training/colab_training.ipynb`](../training/colab_training.ipynb) to
<https://colab.research.google.com>, or open it from Drive.

### Step 3 — select a GPU

`Runtime → Change runtime type → Hardware accelerator → T4 GPU`. Then
`Runtime → Run all`. Cell 2 prints the environment and warns loudly if CUDA is
missing — if you see that warning, stop and fix the runtime type before
continuing.

### Step 4 — run the cells in order

The notebook is self-documenting. In sequence it:

1. installs `ultralytics==8.4.215` (pinned to the locally verified version);
2. prints Python, PyTorch, CUDA, GPU name/memory and Ultralytics versions;
3. prompts for `eye_shield_baseline.zip`;
4. extracts it to `/content/eye_shield`;
5. validates `data.yaml` and asserts both class names;
6. counts train/val/test images and writes the test-free training YAML;
7. trains YOLOv8n on train + val only;
8. confirms `best.pt`, `last.pt`, `results.csv`, `results.png` and confusion
   matrices exist;
9. evaluates `best.pt` once on the locked test split;
10. exports per-class P/R/F1, mAP50, mAP50-95 and inference speed;
11. writes `/content/model_eval_test.json`;
12. packages everything into `/content/eye_shield_results.zip` and downloads it.

### Step 5 — bring the results home

Download `eye_shield_results.zip`. It contains `weights/best.pt`,
`weights/last.pt`, `results.csv`, `results.png`, the confusion matrices, the PR
curves, the locked-test evaluation plots, `model_eval_test.json` and
`environment.json`.

**Where `best.pt` must go locally:**

```bash
cp <extracted>/weights/best.pt /Users/hermanyeung/Downloads/AIOT-MiniProject/models/best.pt
```

`models/best.pt` is Git-ignored by design (`models/*.pt`) and must never be
committed. See [`models/README.md`](../models/README.md).

### Step 6 — record the metrics

```bash
cd /Users/hermanyeung/Downloads/AIOT-MiniProject
.venv/bin/python training/export_metrics.py \
  --report <extracted>/model_eval_test.json --dry-run   # preview first
.venv/bin/python training/export_metrics.py \
  --report <extracted>/model_eval_test.json
```

`export_metrics.py` enforces the append-only rule in
[`experiments/README.md`](../experiments/README.md): it will not append to a CSV
whose header differs from the committed schema, and it will not accept a report
lacking `metrics.overall`, so a row cannot be manufactured from a target table.

---

## 5. What must be reported after the run

Quote only values read from `model_eval_test.json`:

- environment (GPU name, memory, torch/Ultralytics versions);
- class-wise precision, recall and F1;
- mAP50 and mAP50-95, overall and per class;
- test inference speed (`preprocess`, `inference`, `postprocess`, total ms, FPS);
- train/val/test image counts actually used;
- the confusion matrix;
- the epoch at which early stopping fired, and the best epoch (read from
  `results.csv`, which records one row per epoch).

Project targets in `plan.md` are P ≥ 0.90, R ≥ 0.85, F1 ≥ 0.87, mAP@0.5 ≥ 0.90
and ≥ 15 FPS. These are **targets, not results**. If a measured value falls
short, the shortfall is reported, not smoothed over.

---

## 6. Reproducibility

- `seed=42` and `deterministic=True` are set in both the local script and the
  notebook. Full bitwise determinism on GPU is not guaranteed by PyTorch, so a
  re-run may differ in the last decimal places; that is expected and must be
  stated if a re-run is reported.
- The Ultralytics version is pinned to `8.4.215`, the version installed and
  verified locally.
- Every run records the actual package versions (`collect_versions`) and the
  full argument set, so a metric can always be traced to the stack that made it.
- `experiments/ai_metrics.csv` is append-only. Rows are never edited or deleted;
  a failed run stays in the record.

---

## 7. Local execution status

`training/train_yolov8n.py --dry-run` was run locally and passed:

```
dataset root : .../data/processed/eye_shield_baseline
train: 160 images / 160 labels
val  :  40 images /  40 labels
test :  40 images /  40 labels
classes: {0: 'goggles', 1: 'no_goggles'}
device : mps  batch: 8  epochs: 60 (patience 10)  imgsz/seed: 640/42
NOTE: the test split is not referenced by the training YAML.
DRY RUN -- dataset validated, no training executed.
```

This confirms dataset integrity, class contract and the test-split firewall. It
is **not** a training result.

This machine has **no CUDA GPU** (`torch.cuda.is_available()` → `False`; MPS is
available). Formal training was therefore not executed locally, per the
instruction to stop when no GPU training environment is available. The notebook
in section 4 is the intended execution path.

---

## 8. Not yet done

- Formal training has not run. No `best.pt` exists. No metric exists.
- Webcam application integration, newline-delimited JSON serial integration,
  Blynk, and end-to-end testing are out of scope for this phase.
- Phase 2A firmware is verified and committed. It is not modified by this work.
