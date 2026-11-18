"""Independent evaluation of a trained eye-shield model on the locked test split.

This module is the *only* place in the repository that reads the test split, and
it must be run once, after training is final. It does not tune anything: it
measures and reports.

What it produces
----------------
* per-class precision, recall, F1 (and overall means);
* mAP@0.5 and mAP@0.5:0.95, overall and per class;
* confusion matrix;
* inference speed (preprocess / inference / postprocess, ms per image) and
  derived FPS on the machine that ran the evaluation;
* a JSON file with every number, plus a copy of the conditions it was measured
  under.

No number is ever written that was not measured. If evaluation has not run,
this module produces no result file at all.

Usage
-----
    python training/evaluate_model.py --weights runs/eye_shield/yolov8n_baseline/weights/best.pt
    python training/evaluate_model.py --weights best.pt --device 0 --batch 16
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Reuse the validated dataset helpers so the class contract is enforced in one
# place rather than duplicated.
try:
    from training.train_yolov8n import (  # type: ignore[import-not-found]
        EXPECTED_CLASSES,
        DatasetError,
        collect_versions,
        count_images,
        describe_gpu,
        load_dataset_yaml,
        resolve_dataset_root,
        validate_split_counts,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from training.train_yolov8n import (  # type: ignore[import-not-found]
        EXPECTED_CLASSES,
        DatasetError,
        collect_versions,
        count_images,
        describe_gpu,
        load_dataset_yaml,
        resolve_dataset_root,
        validate_split_counts,
    )

#: The only split this module is permitted to evaluate.
TEST_SPLIT = "test"

DEFAULT_OUTPUT = "experiments/model_eval_test.json"


class EvaluationError(RuntimeError):
    """Raised when evaluation cannot produce a trustworthy result."""


def build_test_yaml(root: Path, destination: Path) -> Path:
    """Write an evaluation YAML whose ``val`` points at the locked test split.

    Ultralytics' ``model.val()`` always validates the split named ``val``. To
    evaluate the test split without renaming directories on disk, we emit a
    YAML in which ``val`` resolves to ``test/images``. The on-disk dataset is
    untouched.
    """
    root = Path(root).resolve()
    payload = {
        "path": str(root),
        "train": "train/images",   # required by the schema; never read in val-only mode
        "val": "test/images",
        "test": "test/images",
        "names": dict(EXPECTED_CLASSES),
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, default_flow_style=False)
    return destination


def _require_finite(value: Any, label: str) -> float:
    """Return ``value`` as a float, refusing NaN/inf so no fake number is stored."""
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise EvaluationError(f"{label} is not a finite number: {value!r}")
    return number


def extract_metrics(metrics: Any, names: Dict[int, str]) -> Dict[str, Any]:
    """Pull per-class and aggregate metrics out of an Ultralytics result object.

    ``metrics`` is the object returned by ``YOLO.val()``. Its ``box`` attribute
    is a ``Metric`` whose ``p``, ``r``, ``ap50`` and ``ap`` arrays are indexed by
    the *present* classes (``ap_class_index``), not by absolute class id, so the
    mapping must be resolved through that index.
    """
    box = metrics.box
    present = list(getattr(box, "ap_class_index", []) or [])

    per_class: Dict[str, Dict[str, float]] = {}
    for position, class_index in enumerate(present):
        class_index = int(class_index)
        precision = _require_finite(box.p[position], f"precision[{class_index}]")
        recall = _require_finite(box.r[position], f"recall[{class_index}]")
        ap50 = _require_finite(box.ap50[position], f"ap50[{class_index}]")
        ap = _require_finite(box.ap[position], f"ap[{class_index}]")
        f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
        per_class[names.get(class_index, str(class_index))] = {
            "class_id": class_index,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "mAP50": round(ap50, 6),
            "mAP50_95": round(ap, 6),
        }

    # Aggregate values: prefer the metric's own mean_result over recomputation.
    mean = box.mean_results() if hasattr(box, "mean_results") else None
    if mean and len(mean) >= 4:
        overall = {
            "precision": round(_require_finite(mean[0], "mean precision"), 6),
            "recall": round(_require_finite(mean[1], "mean recall"), 6),
            "mAP50": round(_require_finite(mean[2], "mean mAP50"), 6),
            "mAP50_95": round(_require_finite(mean[3], "mean mAP50-95"), 6),
        }
    else:  # pragma: no cover - defensive for API drift
        precisions = [v["precision"] for v in per_class.values()]
        recalls = [v["recall"] for v in per_class.values()]
        overall = {
            "precision": round(sum(precisions) / len(precisions), 6) if precisions else 0.0,
            "recall": round(sum(recalls) / len(recalls), 6) if recalls else 0.0,
            "mAP50": round(sum(v["mAP50"] for v in per_class.values()) / len(per_class), 6),
            "mAP50_95": round(sum(v["mAP50_95"] for v in per_class.values()) / len(per_class), 6),
        }
    overall["f1"] = (
        0.0
        if (overall["precision"] + overall["recall"]) == 0
        else round(
            2 * overall["precision"] * overall["recall"]
            / (overall["precision"] + overall["recall"]),
            6,
        )
    )

    speed = dict(getattr(metrics, "speed", {}) or {})
    total_ms = sum(float(speed.get(key, 0.0)) for key in
                   ("preprocess", "inference", "postprocess"))
    speed_report = {
        "preprocess_ms": round(float(speed.get("preprocess", 0.0)), 3),
        "inference_ms": round(float(speed.get("inference", 0.0)), 3),
        "postprocess_ms": round(float(speed.get("postprocess", 0.0)), 3),
        "total_ms_per_image": round(total_ms, 3),
        "fps": round(1000.0 / total_ms, 3) if total_ms > 0 else None,
    }

    return {
        "per_class": per_class,
        "overall": overall,
        "speed": speed_report,
    }


def evaluate(
    weights: Path,
    dataset_root: Path,
    device: str = "auto",
    batch: int = 16,
    imgsz: int = 640,
    output: Path = Path(DEFAULT_OUTPUT),
    run_name: str = "eye_shield_test_eval",
) -> Dict[str, Any]:
    """Evaluate ``weights`` once on the locked test split and return the report."""
    weights = Path(weights)
    if not weights.is_file():
        raise EvaluationError(f"weights not found: {weights}")

    dataset_root = Path(dataset_root).resolve()
    dataset_yaml = dataset_root / "data.yaml"
    data = load_dataset_yaml(dataset_yaml)
    root = resolve_dataset_root(dataset_yaml, data)
    counts = validate_split_counts(root)

    test_images = count_images(root / TEST_SPLIT / "images")
    if test_images == 0:
        raise EvaluationError(f"test split is empty under {root}")

    eval_yaml = build_test_yaml(root, Path(output).parent / "eval_test_split.yaml")

    resolved_device = device
    if device == "auto":
        try:
            import torch
            if torch.cuda.is_available():
                resolved_device = "0"
            else:
                mps = getattr(torch.backends, "mps", None)
                resolved_device = "mps" if (mps and mps.is_available()) else "cpu"
        except ImportError:  # pragma: no cover
            resolved_device = "cpu"

    from ultralytics import YOLO  # imported late so --help stays cheap

    model = YOLO(str(weights))
    names = {int(k): str(v) for k, v in (getattr(model, "names", None) or EXPECTED_CLASSES).items()}
    if names != EXPECTED_CLASSES:
        raise EvaluationError(
            f"model classes {names} do not match the project contract {EXPECTED_CLASSES}"
        )

    results = model.val(
        data=str(eval_yaml),
        split="val",          # 'val' resolves to test/images in eval_yaml
        imgsz=imgsz,
        batch=batch,
        device=resolved_device,
        plots=True,
        save_json=False,
        project=str(Path(output).parent.resolve() / "runs"),
        name=run_name,
        exist_ok=True,
        verbose=False,
    )

    metrics = extract_metrics(results, names)

    report: Dict[str, Any] = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(weights.resolve()),
        "dataset_root": str(root),
        "test_split": TEST_SPLIT,
        "test_images": test_images,
        "split_counts": counts,
        "imgsz": imgsz,
        "batch": batch,
        "device": resolved_device,
        "classes": {str(k): v for k, v in names.items()},
        "metrics": metrics,
        "confusion_matrix": _confusion_matrix(results),
        "versions": collect_versions(),
        "gpu": describe_gpu(),
        "host": platform.platform(),
        "caveat": (
            "Measured once on the locked test split. The test split was not used "
            "for training, early stopping, threshold selection, augmentation "
            "selection, or hyper-parameter tuning."
        ),
    }

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    _print_report(report)
    print(f"\nReport written to: {output}")
    return report


def _confusion_matrix(results: Any) -> Optional[List[List[float]]]:
    """Return the confusion matrix as nested lists, or None if unavailable."""
    matrix = getattr(results, "confusion_matrix", None)
    if matrix is None:
        return None
    raw = getattr(matrix, "matrix", None)
    if raw is None:
        return None
    try:
        return [[float(cell) for cell in row] for row in raw]
    except Exception:  # pragma: no cover - defensive
        return None


def _print_report(report: Dict[str, Any]) -> None:
    metrics = report["metrics"]
    print("=" * 72)
    print("Locked test-split evaluation")
    print("=" * 72)
    print(f"  weights : {report['weights']}")
    print(f"  test    : {report['test_images']} images")
    print(f"  device  : {report['device']}")
    print()
    print(f"  {'class':<14}{'P':>9}{'R':>9}{'F1':>9}{'mAP50':>9}{'mAP50-95':>11}")
    for name, values in metrics["per_class"].items():
        print(f"  {name:<14}{values['precision']:>9.4f}{values['recall']:>9.4f}"
              f"{values['f1']:>9.4f}{values['mAP50']:>9.4f}{values['mAP50_95']:>11.4f}")
    overall = metrics["overall"]
    print(f"  {'ALL':<14}{overall['precision']:>9.4f}{overall['recall']:>9.4f}"
          f"{overall['f1']:>9.4f}{overall['mAP50']:>9.4f}{overall['mAP50_95']:>11.4f}")
    speed = metrics["speed"]
    print()
    print(f"  speed   : {speed['inference_ms']} ms inference, "
          f"{speed['total_ms_per_image']} ms/image total, "
          f"{speed['fps']} FPS")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a trained eye-shield model on the locked test split.",
    )
    parser.add_argument("--weights", required=True, help="path to best.pt")
    parser.add_argument("--dataset-root", default="data/processed/eye_shield_baseline")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--name", default="eye_shield_test_eval")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evaluate(
            weights=Path(args.weights),
            dataset_root=Path(args.dataset_root),
            device=args.device,
            batch=args.batch,
            imgsz=args.imgsz,
            output=Path(args.output),
            run_name=args.name,
        )
    except (DatasetError, EvaluationError) as error:
        print(f"EVALUATION ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())