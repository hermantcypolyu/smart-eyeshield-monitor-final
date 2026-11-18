"""Reproducible YOLOv8n training entry point for the eye-shield compliance model.

Trains on ``train`` and ``val`` only. The locked ``test`` split is never opened
here -- not for training, not for early stopping, not for threshold or
hyper-parameter selection. Test evaluation lives exclusively in
:mod:`training.evaluate_model`, which must be run once after training is final.

Design notes
------------
* The dataset ``data.yaml`` shipped with the processed baseline records an
  absolute ``path:`` specific to the machine that generated it, and it also
  declares ``test:``. This module never trains against that file directly.
  :func:`write_train_yaml` emits a temporary YAML containing only ``path``,
  ``train``, ``val`` and ``names``. That makes "the test split cannot influence
  training" a structural property rather than a promise.
* ``ultralytics`` and ``torch`` are imported lazily so the pure validation
  helpers stay unit-testable on a machine without the heavy stack.
* Actual package versions and the fully resolved training arguments are written
  next to the run so a result can always be traced back to its conditions.

Usage
-----
    python training/train_yolov8n.py --dataset-root data/processed/eye_shield_baseline
    python training/train_yolov8n.py --dataset-root /content/eye_shield_baseline \\
        --device 0 --batch auto
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# --------------------------------------------------------------------------- #
# Constants -- frozen training protocol
# --------------------------------------------------------------------------- #

SEED = 42
IMGSZ = 640
EPOCHS = 60
PATIENCE = 10
PRETRAINED = "yolov8n.pt"

#: The only legal class mapping for this project (mirrors config/classes.yaml).
EXPECTED_CLASSES: Dict[int, str] = {0: "goggles", 1: "no_goggles"}

#: Splits that training is allowed to touch.
TRAINING_SPLITS: Tuple[str, ...] = ("train", "val")

#: Splits that must never be visible to the training process.
FORBIDDEN_SPLITS: Tuple[str, ...] = ("test",)

DEFAULT_PROJECT = "runs/eye_shield"
DEFAULT_NAME = "yolov8n_baseline"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


# --------------------------------------------------------------------------- #
# Pure helpers (unit-testable without ultralytics/torch)
# --------------------------------------------------------------------------- #


class DatasetError(RuntimeError):
    """Raised when the dataset on disk is not the approved two-class baseline."""


def load_dataset_yaml(yaml_path: Path) -> Dict[str, Any]:
    """Load and structurally validate a YOLO ``data.yaml``.

    Raises :class:`DatasetError` if the file is missing, unparseable, or does
    not declare exactly the two approved classes.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.is_file():
        raise DatasetError(f"dataset yaml not found: {yaml_path}")

    with yaml_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise DatasetError(f"dataset yaml is not a mapping: {yaml_path}")

    found = normalise_names(data.get("names"))
    if found != EXPECTED_CLASSES:
        raise DatasetError(
            "dataset class names do not match the approved mapping.\n"
            f"  expected: {EXPECTED_CLASSES}\n"
            f"  found:    {found}"
        )
    return data


def normalise_names(names: Any) -> Dict[int, str]:
    """Coerce YOLO ``names`` (list or dict, str or int keys) to ``{int: str}``."""
    if names is None:
        return {}
    if isinstance(names, dict):
        return {int(key): str(value) for key, value in names.items()}
    if isinstance(names, (list, tuple)):
        return {index: str(value) for index, value in enumerate(names)}
    raise DatasetError(f"unsupported names type: {type(names).__name__}")


def resolve_dataset_root(yaml_path: Path, data: Dict[str, Any]) -> Path:
    """Resolve the dataset root the way Ultralytics does.

    An existing absolute ``path:`` wins; otherwise the value is resolved
    relative to the directory holding the YAML.
    """
    yaml_path = Path(yaml_path)
    declared = data.get("path")
    if declared:
        candidate = Path(str(declared)).expanduser()
        if candidate.is_absolute() and candidate.is_dir():
            return candidate.resolve()
    return yaml_path.parent.resolve()


def split_image_dir(root: Path, split: str) -> Path:
    """Return the image directory for ``split`` under ``root``."""
    return Path(root) / split / "images"


def count_images(directory: Path) -> int:
    """Count image files in ``directory`` (non-recursive)."""
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    return sum(1 for item in directory.iterdir()
               if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES)


def count_labels(directory: Path) -> int:
    """Count ``.txt`` label files in ``directory`` (non-recursive)."""
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    return sum(1 for item in directory.iterdir()
               if item.is_file() and item.suffix.lower() == ".txt")


def validate_split_counts(root: Path) -> Dict[str, Dict[str, int]]:
    """Verify every split has images and a matching label for each sample.

    Raises :class:`DatasetError` when a split is empty or when image and label
    counts disagree. Returns ``{split: {"images": n, "labels": n}}``.
    """
    root = Path(root)
    report: Dict[str, Dict[str, int]] = {}
    for split in TRAINING_SPLITS + FORBIDDEN_SPLITS:
        images = count_images(split_image_dir(root, split))
        labels = count_labels(root / split / "labels")
        if images == 0:
            raise DatasetError(f"split '{split}' has no images under {root}")
        if images != labels:
            raise DatasetError(
                f"split '{split}' image/label mismatch: {images} images vs "
                f"{labels} labels"
            )
        report[split] = {"images": images, "labels": labels}
    return report


def write_train_yaml(root: Path, destination: Path) -> Path:
    """Write a training-only ``data.yaml`` that has no ``test:`` key.

    This is the mechanism that keeps the locked test split out of training: the
    trainer receives a dataset definition that does not mention it.
    """
    root = Path(root).resolve()
    payload = {
        "path": str(root),
        "train": "train/images",
        "val": "val/images",
        "names": dict(EXPECTED_CLASSES),
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, default_flow_style=False)
    return destination


def auto_batch(device: str) -> int:
    """Pick a batch size from available accelerator memory.

    Deliberately conservative. YOLOv8n at 640 px is small, but the source
    images are upscaled from roughly 270x179 and Colab GPUs are shared. An OOM
    mid-run costs the whole run, so under-shooting is preferred.
    """
    try:
        import torch
    except ImportError:  # pragma: no cover - defensive
        return 8

    if device in ("cpu", "mps") or not torch.cuda.is_available():
        return 8

    try:
        index = 0 if device in ("0", "cuda", "cuda:0") else int(str(device).split(":")[-1])
        total_gb = torch.cuda.get_device_properties(index).total_memory / (1024 ** 3)
    except Exception:  # pragma: no cover - defensive
        return 16

    if total_gb >= 35:      # A100 40 GB
        return 64
    if total_gb >= 20:      # A100 20 GB / L4
        return 48
    if total_gb >= 14:      # T4 16 GB, the common free Colab GPU
        return 32
    if total_gb >= 7:
        return 16
    return 8


def collect_versions() -> Dict[str, Any]:
    """Capture the exact software stack so a run can be reproduced."""
    versions: Dict[str, Any] = {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    try:
        import numpy
        versions["numpy"] = numpy.__version__
    except ImportError:  # pragma: no cover
        versions["numpy"] = None
    try:
        import torch
        versions["torch"] = torch.__version__
        versions["cuda_available"] = bool(torch.cuda.is_available())
        versions["cuda_version"] = getattr(torch.version, "cuda", None)
        mps = getattr(torch.backends, "mps", None)
        versions["mps_available"] = bool(mps and mps.is_available())
    except ImportError:  # pragma: no cover
        versions["torch"] = None
    try:
        import ultralytics
        versions["ultralytics"] = ultralytics.__version__
    except ImportError:  # pragma: no cover
        versions["ultralytics"] = None
    try:
        import cv2
        versions["opencv"] = cv2.__version__
    except ImportError:  # pragma: no cover
        versions["opencv"] = None
    return versions


def describe_gpu() -> Dict[str, Any]:
    """Return GPU name, memory and count, or a CPU marker."""
    try:
        import torch
    except ImportError:  # pragma: no cover
        return {"name": None, "count": 0}
    if not torch.cuda.is_available():
        return {"name": None, "count": 0}
    props = torch.cuda.get_device_properties(0)
    return {
        "name": props.name,
        "count": torch.cuda.device_count(),
        "total_memory_gb": round(props.total_memory / (1024 ** 3), 2),
    }


def _pick_device() -> str:
    """Choose cuda:0 if present, else mps, else cpu."""
    try:
        import torch
    except ImportError:  # pragma: no cover
        return "cpu"
    if torch.cuda.is_available():
        return "0"
    mps = getattr(torch.backends, "mps", None)
    if mps and mps.is_available():
        return "mps"
    return "cpu"


@dataclass
class RunPaths:
    """Where a run writes its artifacts."""

    run_dir: Path

    @property
    def train_yaml(self) -> Path:
        return self.run_dir / "train_only_data.yaml"

    @property
    def environment(self) -> Path:
        return self.run_dir / "environment.json"

    @property
    def args(self) -> Path:
        return self.run_dir / "train_args.json"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def train(
    dataset_root: Path,
    device: str = "auto",
    batch: str = "auto",
    epochs: int = EPOCHS,
    patience: int = PATIENCE,
    imgsz: int = IMGSZ,
    project: str = DEFAULT_PROJECT,
    name: str = DEFAULT_NAME,
    workers: int = 2,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run the baseline training and return a summary of what happened.

    ``dry_run`` validates the dataset and resolves every argument, then stops
    before importing torch/ultralytics. It is what a machine without a GPU can
    legitimately do.
    """
    dataset_root = Path(dataset_root).resolve()
    dataset_yaml = dataset_root / "data.yaml"

    data = load_dataset_yaml(dataset_yaml)
    root = resolve_dataset_root(dataset_yaml, data)
    counts = validate_split_counts(root)

    resolved_device = _pick_device() if device == "auto" else device
    resolved_batch = auto_batch(resolved_device) if batch == "auto" else int(batch)

    run_dir = Path(project).resolve() / name
    paths = RunPaths(run_dir=run_dir)
    paths.run_dir.mkdir(parents=True, exist_ok=True)

    train_yaml = write_train_yaml(root, paths.train_yaml)

    versions = collect_versions()
    gpu = describe_gpu()
    environment = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "versions": versions,
        "gpu": gpu,
        "device_used": resolved_device,
        "dataset_root": str(root),
        "split_counts": counts,
    }
    paths.environment.write_text(json.dumps(environment, indent=2) + "\n",
                                 encoding="utf-8")

    train_args: Dict[str, Any] = {
        "data": str(train_yaml),
        "epochs": epochs,
        "patience": patience,
        "imgsz": imgsz,
        "batch": resolved_batch,
        "seed": SEED,
        "deterministic": True,
        "device": resolved_device,
        "project": str(Path(project).resolve()),
        "name": name,
        "exist_ok": True,
        "pretrained": True,
        "optimizer": "auto",
        "workers": workers,
        "cache": False,
        "plots": True,
        "save": True,
        "val": True,
        "split": "val",
    }
    paths.args.write_text(json.dumps(train_args, indent=2) + "\n", encoding="utf-8")

    summary: Dict[str, Any] = {
        "dataset_root": str(root),
        "split_counts": counts,
        "device": resolved_device,
        "batch": resolved_batch,
        "epochs": epochs,
        "patience": patience,
        "imgsz": imgsz,
        "seed": SEED,
        "run_dir": str(run_dir),
        "environment_json": str(paths.environment),
        "train_args_json": str(paths.args),
        "dry_run": dry_run,
    }

    print("=" * 72)
    print("Eye-Shield YOLOv8n baseline training")
    print("=" * 72)
    print(f"  dataset root : {root}")
    for split, stat in counts.items():
        print(f"  {split:<13}: {stat['images']} images / {stat['labels']} labels")
    print(f"  classes      : {EXPECTED_CLASSES}")
    print(f"  device       : {resolved_device}  gpu={gpu.get('name')}")
    print(f"  batch        : {resolved_batch}")
    print(f"  epochs       : {epochs} (patience {patience})")
    print(f"  imgsz/seed   : {imgsz} / {SEED}")
    print(f"  run dir      : {run_dir}")
    print("  NOTE: the test split is not referenced by the training YAML.")

    if dry_run:
        print("\nDRY RUN -- dataset validated, no training executed.")
        summary["status"] = "dry_run_ok"
        return summary

    from ultralytics import YOLO  # imported late: keeps dry-run and CI light

    model = YOLO(PRETRAINED)
    model.train(**train_args)

    summary["status"] = "trained"
    summary["best_weights"] = str(run_dir / "weights" / "best.pt")
    summary["last_weights"] = str(run_dir / "weights" / "last.pt")
    print("\nTraining finished. Artifacts under:", run_dir)
    print("Next: python training/evaluate_model.py --weights "
          f"{run_dir / 'weights' / 'best.pt'} --dataset-root {root}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train the two-class eye-shield YOLOv8n baseline.",
    )
    parser.add_argument("--dataset-root", default="data/processed/eye_shield_baseline",
                        help="processed dataset directory containing data.yaml")
    parser.add_argument("--device", default="auto",
                        help="'auto', 'cpu', 'mps', '0', ...")
    parser.add_argument("--batch", default="auto",
                        help="'auto' derives the batch from GPU memory, or an int")
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--patience", type=int, default=PATIENCE)
    parser.add_argument("--imgsz", type=int, default=IMGSZ)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the dataset and arguments, then stop")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = train(
            dataset_root=Path(args.dataset_root),
            device=args.device,
            batch=args.batch,
            epochs=args.epochs,
            patience=args.patience,
            imgsz=args.imgsz,
            project=args.project,
            name=args.name,
            workers=args.workers,
            dry_run=args.dry_run,
        )
    except DatasetError as error:
        print(f"DATASET ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())