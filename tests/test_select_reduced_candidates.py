"""Focused tests for the reduced-candidate selector.

Synthetic fixtures only. These cover the selection rules that could silently
break -- size gating, duplicate exclusion, border clipping, ranking stability,
and the guarantee that ``human_decision`` is never pre-filled.
"""

from __future__ import annotations

import csv

import pytest

from tools import screen_construction_ppe as screen
from tools import select_reduced_candidates as select


def make_box(cls: str, cls_id: int, cx: float, cy: float, w: float, h: float) -> "screen.Box":
    return screen.Box(
        source_class=cls,
        source_id=cls_id,
        cx=cx,
        cy=cy,
        width=w,
        height=h,
        image_width_px=1000,
        image_height_px=1000,
    )


def make_record(stem: str, boxes: list, split: str = "train") -> "screen.ImageRecord":
    return screen.ImageRecord(
        stem=stem,
        source_split=split,
        image_member=f"construction-ppe/images/{split}/{stem}.jpg",
        label_member=f"construction-ppe/labels/{split}/{stem}.txt",
        image_width_px=1000,
        image_height_px=1000,
        boxes=boxes,
        image_sha256=screen.sha256_bytes(stem.encode()),
        dhash_value=int(screen.sha256_bytes(stem.encode())[:16], 16),
    )


class FakeArchive:
    """Stands in for SourceArchive; every crop decode returns ``None``."""

    def read_bytes(self, member: str) -> bytes:
        return b""


def test_size_gate_rejects_below_threshold():
    """Boxes under 48 px minimum side never reach the queue."""
    small = make_box("goggles", 4, 0.5, 0.5, 0.03, 0.03)   # 30 px
    large = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)   # 80 px
    records = [make_record("a", [small]), make_record("b", [large])]

    kept, funnel = select.collect(records, "goggles", set(), {}, FakeArchive())

    assert [c.record.stem for c in kept] == ["b"]
    assert funnel.removed_size == 1
    assert funnel.total_boxes == 2


def test_other_class_boxes_ignored():
    """Selection for one class must not count the other class's boxes."""
    boxes = [make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08),
             make_box("no_goggle", 7, 0.4, 0.4, 0.08, 0.08)]
    kept, funnel = select.collect([make_record("a", boxes)], "goggles", set(), {}, FakeArchive())

    assert len(kept) == 1
    assert funnel.total_boxes == 1


def test_duplicate_group_member_excluded():
    """An image in a duplicate group is dropped even when its box is large."""
    box = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)
    records = [make_record("keep", [box]), make_record("dup", [box])]

    kept, funnel = select.collect(records, "goggles", {"dup"}, {}, FakeArchive())

    assert [c.record.stem for c in kept] == ["keep"]
    assert funnel.removed_duplicate == 1


def test_border_clipped_box_excluded():
    """A box touching the frame edge is removed."""
    edge = make_box("goggles", 4, 0.02, 0.5, 0.08, 0.08)   # left edge at -0.02
    centred = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)
    records = [make_record("edge", [edge]), make_record("mid", [centred])]

    kept, funnel = select.collect(records, "goggles", set(), {}, FakeArchive())

    assert [c.record.stem for c in kept] == ["mid"]
    assert funnel.removed_clipped == 1


def test_border_clipped_detects_each_edge():
    box = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)
    assert not select.border_clipped(make_record("mid", [box]), box)

    for dx, dy in ((0.02, 0.5), (0.98, 0.5), (0.5, 0.02), (0.5, 0.98)):
        probe = make_box("goggles", 4, dx, dy, 0.08, 0.08)
        assert select.border_clipped(make_record("x", [probe]), probe)


def test_queue_written_with_blank_human_decision(tmp_path):
    """The machine must never pre-fill the human's column."""
    box = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)
    candidates, _ = select.collect([make_record("a", [box])], "goggles", set(), {}, FakeArchive())
    select.annotate(candidates, [10.0])

    path = tmp_path / "queue.csv"
    select.write_queue(path, candidates, {})

    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["human_decision"] == ""
    assert list(rows[0]) == select.QUEUE_COLUMNS


def test_annotate_flags_blur_without_asserting_pose():
    """Low sharpness is flagged; pose is explicitly left to the human."""
    box = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)
    candidates, _ = select.collect([make_record("a", [box])], "goggles", set(), {}, FakeArchive())
    candidates[0].sharpness = 1.0

    select.annotate(candidates, [1.0, 50.0, 60.0, 70.0])

    assert candidates[0].action == "VISUAL_CHECK_REQUIRED"
    joined = " ".join(candidates[0].notes)
    assert "blur" in joined
    assert "NOT machine-determined" in joined


def test_annotate_keeps_measured_fields_when_sharpness_missing():
    """A None sharpness must not swallow the size/area note."""
    box = make_box("goggles", 4, 0.5, 0.5, 0.08, 0.08)
    candidates, _ = select.collect([make_record("a", [box])], "goggles", set(), {}, FakeArchive())
    candidates[0].sharpness = None

    select.annotate(candidates, [10.0])

    assert "side 80px" in candidates[0].notes[0]
    assert "unmeasurable" in candidates[0].notes[0]


def test_rank_is_deterministic_and_prefers_larger():
    """Same input twice gives the same order; bigger + sharper ranks first."""
    def build(stem, w, s):
        box = make_box("goggles", 4, 0.5, 0.5, w, w)
        candidate, _ = select.collect([make_record(stem, [box])], "goggles", set(), {}, FakeArchive())
        candidate[0].sharpness = s
        return candidate[0]

    def order():
        items = [build("b", 0.06, 20.0), build("a", 0.12, 90.0), build("c", 0.09, 50.0)]
        select.rank(items)
        return [c.record.stem for c in items]

    assert order() == order() == ["a", "c", "b"]


def test_percentile_matches_expected():
    values = [1.0, 2.0, 3.0, 4.0]
    assert select.percentile(values, 0) == 1.0
    assert select.percentile(values, 100) == 4.0
    assert select.percentile(values, 50) == pytest.approx(2.5)
    assert select.percentile([], 50) == 0.0


def test_percentile_rank_bounds():
    values = [2.0, 4.0, 6.0]
    assert select.percentile_rank(values, 1.0) == 0.0
    assert select.percentile_rank(values, 6.0) == 1.0
    assert select.percentile_rank([], 5.0) == 0.0
