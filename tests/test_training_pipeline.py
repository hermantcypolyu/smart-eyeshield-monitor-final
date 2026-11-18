"""Focused tests for the Phase 2B training pipeline helpers.

These cover the logic that decides *whether* training or evaluation is
allowed to proceed, and the integrity of the metrics export. They do not
touch torch or ultralytics, so they run on any machine.

Deliberately not tested: the actual training loop, Ultralytics internals, or
any numeric model outcome. Those are produced by a real run and cannot be
asserted in advance.
"""

from __future__ import annotations

import csv
import json

import pytest
import yaml

from training import export_metrics as exporter
from training import train_yolov8n as trainer


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_dataset(root, train=3, val=2, test=2, names=None):
    """Create a minimal dataset tree with real image/label file pairs."""
    names = names or {"0": "goggles", "1": "no_goggles"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "data.yaml").write_text(
        yaml.safe_dump(
            {
                "path": str(root),
                "train": "train/images",
                "val": "val/images",
                "test": "test/images",
                "names": {int(k): v for k, v in names.items()},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for split, count in (("train", train), ("val", val), ("test", test)):
        images = root / split / "images"
        labels = root / split / "labels"
        images.mkdir(parents=True, exist_ok=True)
        labels.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (images / f"img{index}.jpg").write_bytes(b"x")
            (labels / f"img{index}.txt").write_text("0 0.5 0.5 0.2 0.2\n",
                                                    encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# Class contract
# --------------------------------------------------------------------------- #


def test_valid_dataset_yaml_is_accepted(tmp_path):
    root = _make_dataset(tmp_path / "ds")
    data = trainer.load_dataset_yaml(root / "data.yaml")
    assert trainer.normalise_names(data["names"]) == trainer.EXPECTED_CLASSES


def test_wrong_class_names_are_rejected(tmp_path):
    root = _make_dataset(tmp_path / "ds", names={"0": "goggles", "1": "head"})
    with pytest.raises(trainer.DatasetError, match="class names"):
        trainer.load_dataset_yaml(root / "data.yaml")


def test_swapped_class_order_is_rejected(tmp_path):
    """A silent inversion would turn every safe frame into a violation."""
    root = _make_dataset(tmp_path / "ds",
                         names={"0": "no_goggles", "1": "goggles"})
    with pytest.raises(trainer.DatasetError):
        trainer.load_dataset_yaml(root / "data.yaml")


def test_missing_dataset_yaml_raises(tmp_path):
    with pytest.raises(trainer.DatasetError, match="not found"):
        trainer.load_dataset_yaml(tmp_path / "nope" / "data.yaml")


def test_names_as_list_is_normalised():
    assert trainer.normalise_names(["goggles", "no_goggles"]) == trainer.EXPECTED_CLASSES


def test_names_with_string_keys_are_normalised():
    assert trainer.normalise_names({"0": "goggles", "1": "no_goggles"}) == \
        trainer.EXPECTED_CLASSES


# --------------------------------------------------------------------------- #
# Split counting
# --------------------------------------------------------------------------- #


def test_split_counts_reports_each_split(tmp_path):
    root = _make_dataset(tmp_path / "ds", train=4, val=2, test=3)
    counts = trainer.validate_split_counts(root)
    assert counts["train"] == {"images": 4, "labels": 4}
    assert counts["val"] == {"images": 2, "labels": 2}
    assert counts["test"] == {"images": 3, "labels": 3}


def test_image_label_mismatch_raises(tmp_path):
    root = _make_dataset(tmp_path / "ds")
    (root / "train" / "labels" / "img0.txt").unlink()
    with pytest.raises(trainer.DatasetError, match="mismatch"):
        trainer.validate_split_counts(root)


def test_empty_split_raises(tmp_path):
    root = _make_dataset(tmp_path / "ds")
    for item in (root / "val" / "images").iterdir():
        item.unlink()
    for item in (root / "val" / "labels").iterdir():
        item.unlink()
    with pytest.raises(trainer.DatasetError, match="no images"):
        trainer.validate_split_counts(root)


# --------------------------------------------------------------------------- #
# The locked-test guarantee
# --------------------------------------------------------------------------- #


def test_training_yaml_never_mentions_the_test_split(tmp_path):
    """This is the structural guarantee that the test split stays locked."""
    root = _make_dataset(tmp_path / "ds")
    destination = trainer.write_train_yaml(root, tmp_path / "out" / "train.yaml")

    payload = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert "test" not in payload
    assert set(payload) == {"path", "train", "val", "names"}
    # The raw text must not declare a test split either, so a careless
    # grep-based audit is not misled.
    text = destination.read_text(encoding="utf-8")
    assert "test:" not in text
    assert "test/images" not in text


def test_training_yaml_records_the_expected_classes(tmp_path):
    root = _make_dataset(tmp_path / "ds")
    destination = trainer.write_train_yaml(root, tmp_path / "train.yaml")
    payload = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert {int(k): v for k, v in payload["names"].items()} == trainer.EXPECTED_CLASSES


def test_training_yaml_points_at_the_resolved_root(tmp_path):
    root = _make_dataset(tmp_path / "ds")
    destination = trainer.write_train_yaml(root, tmp_path / "train.yaml")
    payload = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert payload["path"] == str(root.resolve())


def test_eval_yaml_points_val_at_the_test_split(tmp_path):
    """model.val() only reads 'val', so evaluation redirects it deliberately."""
    root = _make_dataset(tmp_path / "ds")
    from training import evaluate_model as ev
    destination = ev.build_test_yaml(root, tmp_path / "eval.yaml")
    payload = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert payload["val"] == "test/images"
    assert payload["test"] == "test/images"


def test_eval_yaml_does_not_modify_the_on_disk_dataset(tmp_path):
    root = _make_dataset(tmp_path / "ds")
    before = (root / "data.yaml").read_text(encoding="utf-8")
    from training import evaluate_model as ev
    ev.build_test_yaml(root, tmp_path / "eval.yaml")
    assert (root / "data.yaml").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# Batch sizing
# --------------------------------------------------------------------------- #


def test_auto_batch_is_positive_on_any_device():
    for device in ("cpu", "mps", "0"):
        assert trainer.auto_batch(device) >= 1


def test_auto_batch_is_conservative_without_cuda():
    # No CUDA on the test machine -> the small fallback, regardless of device.
    assert trainer.auto_batch("0") in (8, 16)


# --------------------------------------------------------------------------- #
# Evaluation report validation
# --------------------------------------------------------------------------- #


def _report(**overrides):
    base = {
        "weights": "runs/a/best.pt",
        "dataset_root": "data/processed/eye_shield_baseline",
        "test_split": "test",
        "test_images": 40,
        "split_counts": {"train": {"images": 160}, "val": {"images": 40}},
        "device": "cuda:0",
        "classes": {"0": "goggles", "1": "no_goggles"},
        "caveat": "measured once",
        "metrics": {
            "per_class": {},
            "overall": {
                "precision": 0.9,
                "recall": 0.85,
                "f1": 0.873,
                "mAP50": 0.9,
                "mAP50_95": 0.61,
            },
            "speed": {"fps": 30.0},
        },
    }
    base.update(overrides)
    return base


def test_valid_report_loads(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    assert exporter.load_report(path)["test_images"] == 40


def test_report_without_metrics_is_rejected(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"test_images": 4}), encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="missing"):
        exporter.load_report(path)


def test_report_missing_an_overall_metric_is_rejected(tmp_path):
    report = _report()
    del report["metrics"]["overall"]["mAP50"]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="mAP50"):
        exporter.load_report(path)


def test_invalid_json_is_rejected(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="not valid JSON"):
        exporter.load_report(path)


# --------------------------------------------------------------------------- #
# Metrics export
# --------------------------------------------------------------------------- #


def test_row_matches_the_committed_column_order(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    row = exporter.build_row(exporter.load_report(path), "AI-01")
    assert list(row) == exporter.COLUMNS


def test_row_preserves_measured_values(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    row = exporter.build_row(exporter.load_report(path), "AI-01")
    assert row["precision"] == "0.9000"
    assert row["recall"] == "0.8500"
    assert row["map50"] == "0.9000"
    assert row["map50_95"] == "0.6100"


def test_append_creates_header_once(tmp_path):
    csv_path = tmp_path / "ai_metrics.csv"
    row = {key: "x" for key in exporter.COLUMNS}
    exporter.append_row(csv_path, row)
    exporter.append_row(csv_path, row)
    lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(exporter.COLUMNS)
    assert len(lines) == 3          # header + two data rows, header not repeated


def test_append_refuses_a_hand_edited_header(tmp_path):
    csv_path = tmp_path / "ai_metrics.csv"
    csv_path.write_text("wrong,header\n", encoding="utf-8")
    row = {key: "x" for key in exporter.COLUMNS}
    with pytest.raises(exporter.ExportError, match="header"):
        exporter.append_row(csv_path, row)


def test_dry_run_writes_nothing(tmp_path):
    csv_path = tmp_path / "ai_metrics.csv"
    row = {key: "x" for key in exporter.COLUMNS}
    exporter.append_row(csv_path, row, dry_run=True)
    assert not csv_path.exists()


def test_committed_metrics_csv_header_matches_the_schema():
    """Guards against the schema drifting away from the committed evidence file."""
    from pathlib import Path
    committed = Path("experiments/ai_metrics.csv")
    if not committed.is_file():
        pytest.skip("ai_metrics.csv not present")
    with committed.open("r", encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert header == exporter.COLUMNS