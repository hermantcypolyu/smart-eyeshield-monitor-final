"""Append measured evaluation results to ``experiments/ai_metrics.csv``.

This module is deliberately append-only. It never overwrites an existing row
and never recomputes a number: it copies values produced by
:mod:`training.evaluate_model` (or a real Ultralytics run) into the project's
auditable metrics log, together with the conditions they were measured under.

``experiments/README.md`` states the rules; this module enforces them in code:

* append, never overwrite;
* no editing of recorded numbers;
* record the conditions;
* targets are intentions, not results -- so a row cannot be created from a
  target table, only from a measured report file.

Usage
-----
    python training/export_metrics.py --report experiments/model_eval_test.json
    python training/export_metrics.py --report report.json --test-id AI-01 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

METRICS_CSV = Path("experiments/ai_metrics.csv")

#: Column order must match the committed header. Never reorder in place.
COLUMNS: List[str] = [
    "test_id",
    "model",
    "dataset",
    "split",
    "train_images",
    "val_images",
    "test_images",
    "precision",
    "recall",
    "f1",
    "map50",
    "map50_95",
    "notes",
    "recorded_at",
]


class ExportError(RuntimeError):
    """Raised when a report cannot be exported without misrepresenting it."""


def load_report(report_path: Path) -> Dict[str, Any]:
    """Load and structurally validate an evaluation report."""
    report_path = Path(report_path)
    if not report_path.is_file():
        raise ExportError(f"report not found: {report_path}")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExportError(f"report is not valid JSON: {report_path}: {error}") from error

    for key in ("metrics", "test_images", "classes"):
        if key not in data:
            raise ExportError(
                f"report is missing '{key}'. Only a report produced by "
                "training/evaluate_model.py can be exported."
            )
    overall = data["metrics"].get("overall")
    if not isinstance(overall, dict):
        raise ExportError("report has no metrics.overall block")
    for key in ("precision", "recall", "f1", "mAP50", "mAP50_95"):
        if key not in overall:
            raise ExportError(f"report overall metrics are missing '{key}'")
    return data


def build_row(report: Dict[str, Any], test_id: str, notes: str = "") -> Dict[str, str]:
    """Convert a validated report into one ``ai_metrics.csv`` row."""
    overall = report["metrics"]["overall"]
    counts = report.get("split_counts", {})

    model_name = report.get("model") or _infer_model_name(report.get("weights", ""))
    dataset_name = report.get("dataset") or Path(str(report.get("dataset_root", "unknown"))).name

    note_parts = [part for part in [notes] if part]
    if report.get("caveat"):
        note_parts.append(str(report["caveat"]))
    if report.get("device"):
        note_parts.append(f"device={report['device']}")
    speed = report["metrics"].get("speed") or {}
    if speed.get("fps"):
        note_parts.append(f"fps={speed['fps']}")

    return {
        "test_id": test_id,
        "model": model_name,
        "dataset": dataset_name,
        "split": report.get("test_split", "test"),
        "train_images": str(counts.get("train", {}).get("images", "")),
        "val_images": str(counts.get("val", {}).get("images", "")),
        "test_images": str(report.get("test_images", "")),
        "precision": f"{float(overall['precision']):.4f}",
        "recall": f"{float(overall['recall']):.4f}",
        "f1": f"{float(overall['f1']):.4f}",
        "map50": f"{float(overall['mAP50']):.4f}",
        "map50_95": f"{float(overall['mAP50_95']):.4f}",
        "notes": "; ".join(note_parts),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _infer_model_name(weights: str) -> str:
    """Derive a readable model identifier from a weights path."""
    if not weights:
        return "unknown"
    stem = Path(str(weights)).stem
    parent = Path(str(weights)).parent.parent.name
    return f"{parent}/{stem}" if parent and parent not in ("", ".") else stem


def append_row(csv_path: Path, row: Dict[str, str], dry_run: bool = False) -> Path:
    """Append ``row`` to ``csv_path``, creating the header if needed.

    Refuses to write if the existing header does not match :data:`COLUMNS`,
    because silently reordering columns would corrupt every prior row.
    """
    csv_path = Path(csv_path)
    exists = csv_path.is_file()

    if exists:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), None)
        if header != COLUMNS:
            raise ExportError(
                "existing ai_metrics.csv header does not match the expected schema.\n"
                f"  expected: {COLUMNS}\n"
                f"  found:    {header}\n"
                "Refusing to append: the file may have been hand-edited."
            )

    if dry_run:
        print(f"DRY RUN -- would append to {csv_path}:")
        for key in COLUMNS:
            print(f"  {key:<12} = {row.get(key, '')}")
        return csv_path

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    return csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append a measured evaluation report to experiments/ai_metrics.csv",
    )
    parser.add_argument("--report", required=True, help="JSON from training/evaluate_model.py")
    parser.add_argument("--csv", default=str(METRICS_CSV))
    parser.add_argument("--test-id", default="AI-01")
    parser.add_argument("--notes", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = load_report(Path(args.report))
        row = build_row(report, args.test_id, args.notes)
        append_row(Path(args.csv), row, dry_run=args.dry_run)
    except ExportError as error:
        print(f"EXPORT ERROR: {error}", file=sys.stderr)
        return 2
    if not args.dry_run:
        print(f"Appended row {row['test_id']} to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())