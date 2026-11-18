"""Tests for the Phase 2B suitability screen (``tools/screen_construction_ppe.py``).

Every fixture is synthesised in ``tmp_path``. No test opens the real 648 MB
Construction-PPE archive, so the suite stays fast, offline, and free of any
redistribution question: the synthetic images are generated here.

Coverage is deliberately narrow. The screen's job is to measure box geometry
and route images to a human, so only the guarantees that could silently produce
a wrong number are tested:

* only the two relevant classes are counted;
* pixel size and area ratio are computed from the real image dimensions;
* thresholds categorise on the intended quantity;
* missing and invalid label data is reported, not guessed around;
* exact duplicates are grouped;
* fixed-seed sampling is reproducible;
* the source ZIP is never modified.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import screen_construction_ppe as screen  # noqa: E402


# --------------------------------------------------------------------------
# Synthetic fixture builders
# --------------------------------------------------------------------------

DATA_YAML = (
    "path: .\n"
    "train: images/train\n"
    "val: images/val\n"
    "names:\n"
    "  0: helmet\n"
    "  1: gloves\n"
    "  2: vest\n"
    "  3: boots\n"
    "  4: goggles\n"
    "  5: Person\n"
    "  6: no_helmet\n"
    "  7: no_goggle\n"
    "  8: no_gloves\n"
)


def make_image(width: int = 200, height: int = 100, seed: int = 0) -> bytes:
    """Deterministic PNG bytes whose pattern depends on ``seed``."""
    import cv2

    rng = np.random.default_rng(seed)
    image = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


def build_zip(
    path: Path,
    entries: dict[str, bytes | str],
    *,
    include_yaml: bool = True,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        if include_yaml:
            handle.writestr("construction-ppe/data.yaml", DATA_YAML)
        for member, payload in entries.items():
            handle.writestr(member, payload)
    return path


def image_member(split: str, stem: str) -> str:
    return f"construction-ppe/images/{split}/{stem}.png"


def label_member(split: str, stem: str) -> str:
    return f"construction-ppe/labels/{split}/{stem}.txt"


# --------------------------------------------------------------------------
# 1. Class filtering
# --------------------------------------------------------------------------


def test_only_relevant_classes_are_counted(tmp_path: Path) -> None:
    """Unrelated PPE rows are ignored; both relevant ids survive."""
    labels = (
        "0 0.5 0.5 0.2 0.2\n"   # helmet        -> ignored
        "4 0.5 0.5 0.30 0.20\n"  # goggles       -> kept
        "5 0.5 0.5 0.9 0.9\n"    # Person        -> ignored
        "7 0.2 0.2 0.10 0.10\n"  # no_goggle     -> kept
    )
    path = build_zip(
        tmp_path / "a.zip",
        {
            image_member("train", "img1"): make_image(),
            label_member("train", "img1"): labels,
        },
    )
    out = screen.screen(path, tmp_path / "out", write_sheets=False)
    counts = out["counts"]

    assert counts["relevant_boxes_total"] == 2
    assert counts["goggles_boxes"] == 1
    assert counts["no_goggle_boxes"] == 1
    assert counts["images_with_both_classes"] == 1
    assert out["source"]["relevant_source_class_ids"] == {"goggles": 4, "no_goggle": 7}


# --------------------------------------------------------------------------
# 2. Pixel-size calculation
# --------------------------------------------------------------------------


def test_pixel_size_uses_real_image_dimensions() -> None:
    """A 0.25x0.10 normalised box on 200x100 px is 50x10 px."""
    box = screen.Box(
        source_class="goggles",
        source_id=4,
        cx=0.5,
        cy=0.5,
        width=0.25,
        height=0.10,
        image_width_px=200,
        image_height_px=100,
    )
    assert box.box_width_px == pytest.approx(50.0)
    assert box.box_height_px == pytest.approx(10.0)
    assert box.minimum_side_px == pytest.approx(10.0)


# --------------------------------------------------------------------------
# 3. Area-ratio calculation
# --------------------------------------------------------------------------


def test_area_ratio_is_box_area_over_image_area() -> None:
    """50x10 px against a 200x100 px image is 500/20000 = 2.5%."""
    box = screen.Box(
        source_class="goggles",
        source_id=4,
        cx=0.5,
        cy=0.5,
        width=0.25,
        height=0.10,
        image_width_px=200,
        image_height_px=100,
    )
    assert box.box_area_px == pytest.approx(500.0)
    assert box.box_area_ratio == pytest.approx(0.025)


# --------------------------------------------------------------------------
# 4. Threshold categorisation
# --------------------------------------------------------------------------


def test_thresholds_categorise_on_the_intended_quantity() -> None:
    """A 30 px side survives >=16/>=24 but not >=32/>=48."""
    boxes = [
        screen.Box("goggles", 4, 0.5, 0.5, 0.30, 0.30, 100, 100),
        screen.Box("goggles", 4, 0.5, 0.5, 0.60, 0.60, 100, 100),
    ]
    rows = {r["threshold_value"]: r["boxes_surviving"]
            for r in screen.threshold_rows(boxes, "goggles")}

    assert rows[">=16px"] == 2
    assert rows[">=24px"] == 2
    assert rows[">=32px"] == 1
    assert rows[">=48px"] == 1
    # 0.09 ratio = 9%; 0.36 ratio = 36%.
    assert rows[">=0.25%"] == 2
    assert rows[">=3%"] == 2


def test_size_band_boundaries_are_provisional_but_exact() -> None:
    def band(side_px: float) -> str:
        return screen.Box(
            "goggles", 4, 0.5, 0.5, side_px / 1000.0, side_px / 1000.0, 1000, 1000
        ).size_band()

    assert band(23.9) == "TOO_SMALL"
    assert band(24.0) == "REVIEW"
    assert band(47.9) == "REVIEW"
    assert band(48.0) == "STRONG"


# --------------------------------------------------------------------------
# 5. Missing image-label pair
# --------------------------------------------------------------------------


def test_missing_label_is_recorded_not_silently_dropped(tmp_path: Path) -> None:
    path = build_zip(
        tmp_path / "a.zip",
        {image_member("train", "orphan"): make_image()},
    )
    out = screen.screen(path, tmp_path / "out", write_sheets=False)

    assert out["counts"]["missing_labels"] == 1
    assert out["counts"]["images_with_relevant_box"] == 0
    issues = (tmp_path / "out" / "annotation_issues.csv").read_text()
    assert "missing_label" in issues
    assert "orphan" in issues


# --------------------------------------------------------------------------
# 6. Invalid YOLO row
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected_type"),
    [
        ("4 0.5 0.5 0.2", "label_row_field_count"),
        ("four 0.5 0.5 0.2 0.2", "label_row_class_not_integer"),
        ("4 0.5 0.5 abc 0.2", "label_row_non_numeric"),
        ("4 0.5 0.5 -0.2 0.2", "label_row_non_positive_size"),
        ("4 1.5 0.5 0.2 0.2", "label_row_centre_out_of_range"),
    ],
)
def test_invalid_rows_are_reported(tmp_path: Path, row: str, expected_type: str) -> None:
    path = build_zip(
        tmp_path / "a.zip",
        {
            image_member("train", "bad"): make_image(),
            label_member("train", "bad"): row + "\n",
        },
    )
    out = screen.screen(path, tmp_path / "out", write_sheets=False)

    issues = (tmp_path / "out" / "annotation_issues.csv").read_text()
    assert expected_type in issues
    assert out["counts"]["relevant_boxes_total"] == 0
    assert out["annotation_issues_total"] >= 1


# --------------------------------------------------------------------------
# 7. Exact duplicate detection
# --------------------------------------------------------------------------


def test_exact_duplicates_are_grouped(tmp_path: Path) -> None:
    """Identical bytes in two splits form one duplicate group."""
    payload = make_image(seed=7)
    path = build_zip(
        tmp_path / "a.zip",
        {
            image_member("train", "dupe_a"): payload,
            label_member("train", "dupe_a"): "4 0.5 0.5 0.3 0.3\n",
            image_member("val", "dupe_b"): payload,
            label_member("val", "dupe_b"): "4 0.5 0.5 0.3 0.3\n",
            image_member("train", "unique"): make_image(seed=99),
            label_member("train", "unique"): "4 0.5 0.5 0.3 0.3\n",
        },
    )
    out = screen.screen(path, tmp_path / "out", write_sheets=False)

    assert out["duplicates"]["exact_groups"] == 1
    csv_text = (tmp_path / "out" / "exact_duplicates.csv").read_text()
    assert "dupe_a" in csv_text and "dupe_b" in csv_text
    # The duplicate spans train and val, so it is also a leakage candidate.
    assert out["duplicates"]["cross_split_candidates"] == 1


# --------------------------------------------------------------------------
# 8. Deterministic random sampling
# --------------------------------------------------------------------------


def test_random_sampling_is_deterministic_and_order_independent() -> None:
    """Same seed, same picks, regardless of the order handed in."""
    image = screen.ImageRecord("s", "train", "m", "l", 100, 100)
    boxes = [
        screen.Box("goggles", 4, i / 10.0, 0.5, 0.1, 0.1, 100, 100)
        for i in range(1, 9)
    ]
    entries = [(image, box) for box in boxes]

    first = screen.pick_random(entries, 4, "salt")
    second = screen.pick_random(list(reversed(entries)), 4, "salt")

    key = lambda item: (item[0].stem, item[1].cx)  # noqa: E731
    assert [key(e) for e in first] == [key(e) for e in second]
    assert len(first) == 4


def test_order_key_is_stable_across_processes() -> None:
    """Guards against reintroducing hash()-based ordering, which is salted."""
    assert screen.order_key(1, "abc") == screen.order_key(1, "abc")
    assert screen.order_key(1, "abc") != screen.order_key(2, "abc")


# --------------------------------------------------------------------------
# 9. Source ZIP remains unchanged
# --------------------------------------------------------------------------


def test_screen_does_not_modify_source_zip(tmp_path: Path) -> None:
    path = build_zip(
        tmp_path / "a.zip",
        {
            image_member("train", "img1"): make_image(),
            label_member("train", "img1"): "4 0.5 0.5 0.3 0.3\n",
        },
    )
    before_bytes = path.read_bytes()
    before_hash = screen.sha256_file(path)
    before_stat = path.stat()

    out = screen.screen(path, tmp_path / "out", write_sheets=False)

    assert path.read_bytes() == before_bytes
    assert screen.sha256_file(path) == before_hash
    assert path.stat().st_mtime == before_stat.st_mtime
    assert path.stat().st_size == before_stat.st_size
    assert out["source"]["sha256_before"] == out["source"]["sha256_after"]
    assert out["source"]["unchanged"] is True


def test_verify_unchanged_detects_tampering(tmp_path: Path) -> None:
    """A modified archive is caught rather than silently measured against."""
    path = build_zip(
        tmp_path / "a.zip",
        {
            image_member("train", "img1"): make_image(),
            label_member("train", "img1"): "4 0.5 0.5 0.3 0.3\n",
        },
    )
    archive = screen.SourceArchive(path)
    with path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(screen.ScreenError, match="changed during screening"):
        archive.verify_unchanged()
