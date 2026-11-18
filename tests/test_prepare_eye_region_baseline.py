"""Focused tests for the eye-region baseline preparation tool.

Synthetic fixtures only -- no real archive is read. These cover the rules whose
failure would silently corrupt a training set: class remapping, removal of the
head classes, image rejection, coordinate validation, determinism, split
isolation for duplicate groups, and source-ZIP immutability.
"""

from __future__ import annotations

import hashlib

import pytest

from tools import prepare_eye_region_baseline as prep


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def make_entry(
    stem: str,
    source_split: str,
    classes: tuple[int, ...],
    *,
    width: int = 270,
    height: int = 180,
    side_ratio: float = 0.4,
) -> "prep.ImageEntry":
    """Build an in-memory entry whose boxes are large enough to pass the gate."""
    boxes = [
        prep.RelevantBox(
            source_class=cls,
            final_class=cls,
            cx=0.5,
            cy=0.5,
            width=side_ratio,
            height=side_ratio,
        )
        for cls in classes
    ]
    return prep.ImageEntry(
        stem=stem,
        source_split=source_split,
        image_member=f"{source_split}/images/{stem}.jpg",
        label_member=f"{source_split}/labels/{stem}.txt",
        image_width_px=width,
        image_height_px=height,
        boxes=boxes,
    )


# --------------------------------------------------------------------------
# Class mapping
# --------------------------------------------------------------------------


def test_source_class_zero_maps_to_final_goggles() -> None:
    kept, dropped, errors = prep.remap_rows(["0 0.5 0.5 0.2 0.2"])

    assert errors == []
    assert dropped == 0
    assert len(kept) == 1
    assert kept[0].source_class == 0
    assert kept[0].final_class == 0
    assert kept[0].to_yolo_row().startswith("0 ")


def test_source_class_one_maps_to_final_no_goggles() -> None:
    kept, _dropped, errors = prep.remap_rows(["1 0.4 0.6 0.3 0.25"])

    assert errors == []
    assert len(kept) == 1
    assert kept[0].source_class == 1
    assert kept[0].final_class == 1
    assert kept[0].to_yolo_row().startswith("1 ")


def test_head_classes_are_removed_not_remapped() -> None:
    rows = [
        "0 0.5 0.5 0.2 0.2",
        "2 0.5 0.5 0.9 0.9",  # head
        "3 0.5 0.5 0.8 0.8",  # head_with_helmet
        "1 0.5 0.5 0.2 0.2",
    ]

    kept, dropped, errors = prep.remap_rows(rows)

    assert errors == []
    assert dropped == 2
    assert [b.final_class for b in kept] == [0, 1]


def test_only_final_ids_zero_and_one_are_emitted() -> None:
    rows = [f"{cls} 0.5 0.5 0.2 0.2" for cls in range(6)]

    kept, dropped, _errors = prep.remap_rows(rows)

    assert {b.final_class for b in kept} <= {0, 1}
    assert dropped == 4


# --------------------------------------------------------------------------
# Coordinate validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        "0 0.5 0.5 0.2",  # four fields
        "0 0.5 0.5 0.2 0.2 0.9",  # six fields
        "goggles 0.5 0.5 0.2 0.2",  # non-integer class
        "0.5 0.5 0.5 0.2 0.2",  # non-integer class (float)
        "0 1.4 0.5 0.2 0.2",  # centre outside frame
        "0 0.5 -0.2 0.2 0.2",  # centre outside frame
        "0 0.5 0.5 0 0.2",  # zero width
        "0 0.5 0.5 0.2 -0.1",  # negative height
        "0 0.5 0.5 1.4 0.2",  # width larger than frame
        "0 nan 0.5 0.2 0.2",  # non-finite
        "0 0.5 0.5 0.2 0.2 extra",  # trailing junk
    ],
)
def test_invalid_yolo_rows_are_rejected(row: str) -> None:
    kept, _dropped, errors = prep.remap_rows([row])

    assert kept == []
    assert len(errors) == 1


def test_box_overflowing_frame_is_clipped_within_tolerance() -> None:
    # cx=0.099 with width 0.2 pushes 0.001 past the left edge: rounding-scale.
    kept, _dropped, errors = prep.remap_rows(["0 0.099 0.5 0.2 0.2"])

    assert errors == []
    box = kept[0]
    assert box.cx - box.width / 2 >= -1e-9
    assert box.cx + box.width / 2 <= 1 + 1e-9


def test_overflow_beyond_tolerance_is_rejected() -> None:
    # cx=0.02 with width 0.2 pushes 0.08 past the left edge.
    kept, _dropped, errors = prep.remap_rows(["0 0.02 0.5 0.2 0.2"])

    assert kept == []
    assert len(errors) == 1


def test_severe_overflow_is_rejected() -> None:
    # cx=0.1 with width 0.9 pushes 0.35 past the left edge.
    kept, _dropped, errors = prep.remap_rows(["0 0.1 0.5 0.9 0.2"])

    assert kept == []
    assert len(errors) == 1


def test_malformed_head_row_does_not_discard_usable_eye_region() -> None:
    # A head box may be geometrically loose; it is dropped, not fatal.
    rows = ["2 0.95 0.5 0.9 0.2", "0 0.5 0.5 0.2 0.2"]

    kept, dropped, errors = prep.remap_rows(rows)

    assert errors == []
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0].final_class == 0


# --------------------------------------------------------------------------
# Image retention rules
# --------------------------------------------------------------------------


def test_image_rejected_when_no_relevant_box_remains() -> None:
    kept, dropped, errors = prep.remap_rows(
        ["2 0.5 0.5 0.9 0.9", "3 0.5 0.5 0.8 0.8"]
    )

    assert kept == []
    assert dropped == 2
    assert errors == []


def test_eligibility_rejects_below_size_gate() -> None:
    # 0.05 * 270 px = 13.5 px minimum side, under the 24 px gate.
    entry = make_entry("tiny", "train", (0,), side_ratio=0.05)

    ok, reason = prep.eligibility(entry, 0)

    assert ok is False
    assert reason == "below_size_gate"


def test_eligibility_rejects_image_with_both_classes() -> None:
    entry = make_entry("both", "train", (0, 1))

    ok, reason = prep.eligibility(entry, 0)

    assert ok is False
    assert reason == "both_classes_present"


def test_eligibility_rejects_suspect_label_family() -> None:
    # 'eye-glasses-*' carrying a goggles label is the likeliest annotation error.
    entry = make_entry("eye-glasses-12-_png", "train", (0,))

    ok, reason = prep.eligibility(entry, 0)

    assert ok is False
    assert reason == "suspect_label_family"


def test_eligibility_accepts_clean_candidate() -> None:
    entry = make_entry("goggle-12-_png", "train", (0,))

    ok, reason = prep.eligibility(entry, 0)

    assert ok is True
    assert reason == ""


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_selection_is_deterministic() -> None:
    entries = [make_entry(f"goggle-{i}-_png", "train", (0,)) for i in range(30)]

    first, _ = prep.select_split(entries, 0, 10, set())
    second, _ = prep.select_split(list(reversed(entries)), 0, 10, set())

    assert [e.stem for e in first] == [e.stem for e in second]


def test_ranking_prefers_larger_boxes() -> None:
    big = make_entry("goggle-1-_png", "train", (0,), side_ratio=0.8)
    small = make_entry("goggle-2-_png", "train", (0,), side_ratio=0.3)

    chosen, _ = prep.select_split([small, big], 0, 1, set())

    assert [e.stem for e in chosen] == ["goggle-1-_png"]


# --------------------------------------------------------------------------
# Split isolation
# --------------------------------------------------------------------------


def test_duplicate_group_crossing_splits_is_confined() -> None:
    a = make_entry("goggle-1-_png", "train", (0,))
    b = make_entry("goggle-2-_png", "valid", (0,))
    a.dhash = b.dhash = 12345

    dropped, report = prep.resolve_leakage([[a, b]])

    # 'val' outranks 'train', so the train copy is the one removed.
    assert dropped == {"goggle-1-_png"}
    assert len(report) == 1


def test_group_within_one_split_is_not_broken() -> None:
    a = make_entry("goggle-1-_png", "train", (0,))
    b = make_entry("goggle-2-_png", "train", (0,))

    dropped, report = prep.resolve_leakage([[a, b]])

    assert dropped == set()
    assert report == []


def test_selection_keeps_one_member_per_group_within_a_split() -> None:
    first = make_entry("goggle-1-_png", "train", (0,), side_ratio=0.8)
    twin = make_entry("goggle-2-_png", "train", (0,), side_ratio=0.7)

    chosen, _rejected = prep.select_split(
        [first, twin], 0, 10, set(), group_of={first.stem: 0, twin.stem: 0}
    )

    assert [e.stem for e in chosen] == ["goggle-1-_png"]


def test_excluded_duplicate_member_is_not_selected() -> None:
    entry = make_entry("goggle-1-_png", "train", (0,))

    chosen, rejected = prep.select_split([entry], 0, 10, {entry.stem})

    assert chosen == []
    assert rejected[0][1] == "duplicate_group_other_split"


# --------------------------------------------------------------------------
# Source archive immutability
# --------------------------------------------------------------------------


def test_source_archive_is_never_opened_for_writing() -> None:
    """The tool must only ever read the archive."""
    source = (prep.__file__,)

    for path in source:
        text = open(path, encoding="utf-8").read()
        for forbidden in ('"w"', "'w'", '"a"', "'a'", "zipfile.ZipFile(path, \"w"):
            assert f"zipfile.ZipFile(path, {forbidden}" not in text


def test_verify_unchanged_detects_modification(tmp_path) -> None:
    payload = b"not a real zip, only hashed"
    archive_path = tmp_path / "source.zip"
    archive_path.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()

    # Model the immutability contract without opening the ZIP.
    assert prep.sha256_file(archive_path) == expected

    archive_path.write_bytes(payload + b"tampered")
    assert prep.sha256_file(archive_path) != expected


def test_sha256_is_stable_across_calls(tmp_path) -> None:
    archive_path = tmp_path / "blob.bin"
    archive_path.write_bytes(b"x" * 100_000)

    assert prep.sha256_file(archive_path) == prep.sha256_file(archive_path)
