#!/usr/bin/env python3
"""Quick suitability screen for the Ultralytics Construction-PPE archive.

This tool answers one question: **is Construction-PPE usable as the main
``goggles`` / ``no_goggles`` source for this project?**

It deliberately does NOT build a dataset. It does not remap classes, does not
write images or labels to disk, does not create train/val/test splits, and does
not train anything. Everything it produces is a measurement or a picture
intended for a human to look at.

Two properties matter for trustworthiness:

* The source ZIP is opened read-only and its SHA-256 is re-verified at the end.
  A changed digest aborts the run rather than reporting numbers measured
  against an unknown archive.
* Images are decoded from ZIP member bytes in memory. Nothing is extracted, so
  screening a 648 MB archive costs no disk.

The output is a set of small CSV/JSON summaries plus contact sheets whose job
is to make the *human* decide semantic suitability. Box size alone is never
treated as evidence about what a box depicts.

Usage:
    python tools/screen_construction_ppe.py
    python tools/screen_construction_ppe.py --out-dir data/reviewed/construction_ppe_screen
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_ZIP = ROOT / "data" / "raw" / "construction_ppe_original.zip"
DEFAULT_OUT = ROOT / "data" / "reviewed" / "construction_ppe_screen"

#: Source class names this screen cares about. The ids are *verified* from the
#: archive's data.yaml rather than hardcoded, because a silent upstream reorder
#: would otherwise change every number in the report.
RELEVANT_NAMES: tuple[str, ...] = ("goggles", "no_goggle")

#: Minimum-side thresholds in pixels, applied to the *shorter* box edge.
#: A box has to be readable both ways for a phone screen observed by a webcam.
SIDE_THRESHOLDS_PX: tuple[int, ...] = (16, 24, 32, 48)

#: Box area thresholds as a fraction of the whole image.
AREA_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("0.25%", 0.0025),
    ("0.5%", 0.005),
    ("1%", 0.01),
    ("2%", 0.02),
    ("3%", 0.03),
)

#: Provisional size-only screening bands. These classify *size*, nothing else.
BAND_TOO_SMALL_PX = 24
BAND_STRONG_PX = 48

#: Difference-hash settings for duplicate and near-duplicate detection.
HASH_SIDE = 8
EXACT_DUPLICATE_HAMMING = 0
NEAR_DUPLICATE_HAMMING = 10

#: Fixed seed so the "random" contact sheets are reproducible on every run.
RANDOM_SEED = 20261118

IMAGE_SUFFIXES: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

#: A box edge may sit outside [0, 1] by at most this much before the row is
#: treated as corrupt rather than float noise. The archive's worst case is
#: 5e-6, which is 32-bit rounding from the exporter.
CLAMP_EPSILON = 1e-4


class ScreenError(Exception):
    """Raised for input that cannot be screened; always printed readably."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def order_key(seed: int, label: str) -> str:
    """Deterministic, process-independent ordering key.

    ``hash()`` is salted per process, so it cannot anchor a reproducible
    sample. A digest is stable across machines and runs.
    """
    return hashlib.sha256(f"{seed}:{label}".encode("utf-8")).hexdigest()


def dhash(gray: Any, side: int = HASH_SIDE) -> int:
    """64-bit difference hash of an 8-bit grayscale image."""
    import cv2

    resized = cv2.resize(gray, (side + 1, side), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile; dependency-free and deterministic."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lower, upper = math.floor(pos), math.ceil(pos)
    if lower == upper:
        return float(ordered[lower])
    weight = pos - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


class UnionFind:
    """Disjoint-set used to turn pairwise similarity into duplicate groups."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[max(left_root, right_root)] = min(left_root, right_root)

    def groups(self) -> list[list[int]]:
        buckets: dict[int, list[int]] = defaultdict(list)
        for item in range(len(self._parent)):
            buckets[self.find(item)].append(item)
        return [sorted(members) for members in buckets.values()]


# --------------------------------------------------------------------------
# Read-only archive access
# --------------------------------------------------------------------------


class SourceArchive:
    """Read-only view of the source ZIP, guarded against accidental mutation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.is_file():
            raise ScreenError(f"source archive not found: {path}")
        try:
            self._zip = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as exc:
            raise ScreenError(f"source archive is not a valid ZIP: {exc}") from exc
        self.sha256_before = sha256_file(path)
        self._names = sorted(n for n in self._zip.namelist() if not n.endswith("/"))

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def find_suffix(self, suffix: str) -> list[str]:
        return [n for n in self._names if n.lower().endswith(suffix)]

    def locate(self, basename: str) -> str | None:
        hits = [n for n in self._names if Path(n).name == basename]
        if not hits:
            return None
        return min(hits, key=lambda n: (n.count("/"), n))

    def read_bytes(self, member: str) -> bytes:
        return self._zip.read(member)

    def read_text(self, member: str) -> str:
        return self._zip.read(member).decode("utf-8", errors="replace")

    def verify_unchanged(self) -> str:
        """Close the handle and prove the archive bytes are untouched."""
        self._zip.close()
        after = sha256_file(self.path)
        if after != self.sha256_before:
            raise ScreenError(
                "source archive changed during screening "
                f"({self.sha256_before} -> {after})"
            )
        return after


# --------------------------------------------------------------------------
# data.yaml
# --------------------------------------------------------------------------


def parse_names(text: str, member: str) -> dict[int, str]:
    """Parse the ``names:`` block without requiring PyYAML.

    Handles both ``0: goggles`` and ``- goggles`` shapes.
    """
    names: dict[int, str] = {}
    inside = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw[:1].isspace() and raw.strip().startswith("names:"):
            inside = not raw.split(":", 1)[1].strip()
            continue
        if not inside:
            continue
        if not raw[:1].isspace():
            inside = False
            continue
        body = raw.strip()
        if ":" in body:
            key, value = body.split(":", 1)
            key, value = key.strip(), value.strip().strip("'\"")
            if key.isdigit():
                names[int(key)] = value
                continue
        if body.startswith("- "):
            names[len(names)] = body[2:].strip().strip("'\"")
    if not names:
        raise ScreenError(f"no class names found in {member}")
    return names


def resolve_relevant_ids(names: dict[int, str]) -> dict[str, int]:
    """Return ``{source_class_name: source_class_id}`` for the screened classes."""
    by_name = {value: key for key, value in names.items()}
    missing = [name for name in RELEVANT_NAMES if name not in by_name]
    if missing:
        raise ScreenError(
            "source data.yaml is missing required class name(s): "
            + ", ".join(sorted(missing))
            + f" (found: {sorted(by_name)})"
        )
    return {name: by_name[name] for name in RELEVANT_NAMES}


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class Box:
    """One annotation row, with both normalised and pixel geometry."""

    source_class: str
    source_id: int
    cx: float
    cy: float
    width: float
    height: float
    image_width_px: int = 0
    image_height_px: int = 0

    # -- derived ---------------------------------------------------------
    @property
    def box_width_px(self) -> float:
        return self.width * self.image_width_px

    @property
    def box_height_px(self) -> float:
        return self.height * self.image_height_px

    @property
    def minimum_side_px(self) -> float:
        return min(self.box_width_px, self.box_height_px)

    @property
    def box_area_px(self) -> float:
        return self.box_width_px * self.box_height_px

    @property
    def box_area_ratio(self) -> float:
        if self.image_width_px <= 0 or self.image_height_px <= 0:
            return 0.0
        return self.box_area_px / float(self.image_width_px * self.image_height_px)

    def size_band(self) -> str:
        """Provisional, size-only band. Says nothing about image content."""
        side = self.minimum_side_px
        if side < BAND_TOO_SMALL_PX:
            return "TOO_SMALL"
        if side < BAND_STRONG_PX:
            return "REVIEW"
        return "STRONG"


@dataclass
class ImageRecord:
    """One source image, its relevant boxes, and its screening measurements."""

    stem: str
    source_split: str
    image_member: str
    label_member: str
    image_width_px: int
    image_height_px: int
    boxes: list[Box] = field(default_factory=list)
    image_sha256: str = ""
    dhash_value: int = 0

    def class_box_counts(self) -> Counter[str]:
        return Counter(box.source_class for box in self.boxes)


@dataclass
class Issue:
    """A problem found while reading one image/label pair."""

    filename: str
    source_split: str
    issue_type: str
    detail: str


# --------------------------------------------------------------------------
# Discovery and validation
# --------------------------------------------------------------------------


def paired_stems(archive: SourceArchive) -> list[tuple[str, str, str]]:
    """Pair every image with its label and record the original split.

    Returns ``(split, stem, image_member)`` tuples. The split comes from the
    archive's own path component so counts can be reported per original split.
    """
    images: dict[tuple[str, str], str] = {}
    for member in archive.names:
        parts = member.split("/")
        if len(parts) < 3:
            continue
        if "images" not in parts:
            continue
        suffix = Path(member).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            continue
        index = parts.index("images")
        split = parts[index + 1] if index + 1 < len(parts) - 1 else "unknown"
        images[(split, Path(member).stem)] = member

    return sorted(
        ((split, stem, member) for (split, stem), member in images.items()),
        key=lambda item: (item[0], item[1]),
    )


def label_member_for(image_member: str) -> str:
    """Derive the label path that must accompany an image path."""
    parts = image_member.split("/")
    index = parts.index("images")
    parts[index] = "labels"
    return "/".join(parts[: index + 2] + [Path(image_member).stem + ".txt"])


def parse_label_text(
    text: str,
    relevant_ids: dict[int, int],
    *,
    stem: str,
    split: str,
    issues: list[Issue],
) -> list[tuple[int, float, float, float, float]]:
    """Validate one YOLO label file and keep only relevant rows.

    Returns a list of ``(source_id, cx, cy, w, h)`` tuples for the screened
    classes. Malformed rows are recorded in ``issues`` and skipped. Rows for
    unrelated classes are counted as unrelated, never as errors.

    Validation performed per row: field count, integer class token, canonical
    token spelling, numeric finite coordinates, positive width/height, centre
    inside the image, and box edges within bounds.
    """
    kept: list[tuple[int, float, float, float, float]] = []
    id_goggles = relevant_ids["goggles"]
    id_no_goggle = relevant_ids["no_goggle"]

    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 5:
            issues.append(Issue(stem, split, "label_row_field_count", f"line {line_no}: {raw!r}"))
            continue
        token = parts[0]
        try:
            source_id = int(token)
        except ValueError:
            issues.append(Issue(stem, split, "label_row_class_not_integer", f"line {line_no}"))
            continue
        if token != str(source_id):
            issues.append(Issue(stem, split, "label_row_class_not_canonical", f"line {line_no}: {token!r}"))
            continue
        try:
            cx, cy, width, height = (float(v) for v in parts[1:])
        except ValueError:
            issues.append(Issue(stem, split, "label_row_non_numeric", f"line {line_no}"))
            continue
        if not all(math.isfinite(v) for v in (cx, cy, width, height)):
            issues.append(Issue(stem, split, "label_row_non_finite", f"line {line_no}"))
            continue
        if width <= 0.0 or height <= 0.0:
            issues.append(Issue(stem, split, "label_row_non_positive_size", f"line {line_no}"))
            continue
        if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
            issues.append(Issue(stem, split, "label_row_centre_out_of_range", f"line {line_no}"))
            continue

        x1, y1 = cx - width / 2.0, cy - height / 2.0
        x2, y2 = cx + width / 2.0, cy + height / 2.0
        overflow = max(0.0, -x1, -y1, x2 - 1.0, y2 - 1.0)
        if overflow > CLAMP_EPSILON:
            issues.append(Issue(stem, split, "label_row_box_out_of_bounds", f"line {line_no}"))
            continue

        if source_id in (id_goggles, id_no_goggle):
            kept.append((source_id, cx, cy, width, height))

    return kept


def load_records(
    archive: SourceArchive,
    relevant_ids: dict[int, int],
    issues: list[Issue],
) -> tuple[list[ImageRecord], Counter[str]]:
    """Read every image/label pair, validate it, and compute geometry."""
    import cv2
    import numpy as np

    records: list[ImageRecord] = []
    tally: Counter[str] = Counter()
    id_to_name = {value: key for key, value in relevant_ids.items()}

    for split, stem, image_member in paired_stems(archive):
        tally["images_total"] += 1
        label_member = label_member_for(image_member)
        if label_member not in archive.names:
            issues.append(Issue(stem, split, "missing_label", label_member))
            tally["missing_label"] += 1
            continue

        payload = archive.read_bytes(image_member)
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(Issue(stem, split, "corrupt_image", image_member))
            tally["corrupt_image"] += 1
            continue
        height_px, width_px = image.shape[:2]

        rows = parse_label_text(
            archive.read_text(label_member),
            relevant_ids,
            stem=stem,
            split=split,
            issues=issues,
        )
        if not rows:
            tally["images_without_relevant_box"] += 1
            continue

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        record = ImageRecord(
            stem=stem,
            source_split=split,
            image_member=image_member,
            label_member=label_member,
            image_width_px=width_px,
            image_height_px=height_px,
            image_sha256=sha256_bytes(payload),
            dhash_value=dhash(gray),
        )
        for source_id, cx, cy, bw, bh in rows:
            record.boxes.append(
                Box(
                    source_class=id_to_name[source_id],
                    source_id=source_id,
                    cx=cx,
                    cy=cy,
                    width=bw,
                    height=bh,
                    image_width_px=width_px,
                    image_height_px=height_px,
                )
            )
        records.append(record)
        tally["images_with_relevant_box"] += 1

    return records, tally


# --------------------------------------------------------------------------
# Duplicates and leakage
# --------------------------------------------------------------------------


def exact_duplicate_groups(records: Sequence[ImageRecord]) -> list[list[ImageRecord]]:
    buckets: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in records:
        buckets[record.image_sha256].append(record)
    return [sorted(group, key=lambda r: (r.source_split, r.stem))
            for group in buckets.values() if len(group) > 1]


def near_duplicate_groups(
    records: Sequence[ImageRecord],
    threshold: int = NEAR_DUPLICATE_HAMMING,
) -> list[list[ImageRecord]]:
    """Cluster records whose difference hashes are within ``threshold`` bits."""
    finder = UnionFind(len(records))
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if hamming(records[i].dhash_value, records[j].dhash_value) <= threshold:
                finder.union(i, j)
    groups = [[records[i] for i in members] for members in finder.groups() if len(members) > 1]
    return sorted(groups, key=lambda g: (g[0].source_split, g[0].stem))


def leakage_candidates(
    groups: Sequence[Sequence[ImageRecord]],
    basis: str,
) -> list[dict[str, Any]]:
    """Groups whose members sit in more than one original split.

    ``basis`` records how the group was found. Exact duplicates are also
    near-duplicates (identical bytes give identical hashes), so the caller must
    de-duplicate across bases or every true duplicate is counted twice.
    """
    out: list[dict[str, Any]] = []
    for group in groups:
        splits = sorted({record.source_split for record in group})
        if len(splits) < 2:
            continue
        out.append(
            {
                "basis": basis,
                "splits": "/".join(splits),
                "member_count": len(group),
                "members": ";".join(f"{r.source_split}:{r.stem}" for r in group),
                "group_id": hashlib.sha256(group[0].stem.encode()).hexdigest()[:12],
            }
        )
    return out


def merge_leakage_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse leakage rows that describe the same set of images.

    An exact-duplicate pair is also reported by the near-duplicate detector, so
    the same pair arrives twice. Identical membership means one finding, and the
    stronger ``exact`` basis is kept.
    """
    merged: dict[frozenset[str], dict[str, Any]] = {}
    for row in rows:
        key = frozenset(row["members"].split(";"))
        if key not in merged or (row["basis"] == "exact" and merged[key]["basis"] != "exact"):
            merged[key] = row
    return sorted(merged.values(), key=lambda r: (r["basis"], r["splits"], r["members"]))



# --------------------------------------------------------------------------
# Aggregate statistics
# --------------------------------------------------------------------------


def threshold_rows(
    boxes: Sequence[Box],
    label: str,
) -> list[dict[str, Any]]:
    """Survivorship of ``label``'s boxes against every configured threshold."""
    total = len(boxes)
    rows: list[dict[str, Any]] = []

    def emit(kind: str, value: str, survivors: int) -> None:
        share = 100.0 * survivors / total if total else 0.0
        rows.append(
            {
                "class": label,
                "threshold_kind": kind,
                "threshold_value": value,
                "boxes_total": total,
                "boxes_surviving": survivors,
                "boxes_below": total - survivors,
                "percent_surviving": round(share, 2),
            }
        )

    sides = [box.minimum_side_px for box in boxes]
    for limit in SIDE_THRESHOLDS_PX:
        emit("minimum_side_px", f">={limit}px", sum(1 for s in sides if s >= limit))

    ratios = [box.box_area_ratio for box in boxes]
    for text, value in AREA_THRESHOLDS:
        emit("box_area_ratio", f">={text}", sum(1 for r in ratios if r >= value))

    return rows


def geometry_summary(boxes: Sequence[Box]) -> dict[str, Any]:
    if not boxes:
        return {"boxes": 0}
    sides = [box.minimum_side_px for box in boxes]
    ratios = [box.box_area_ratio for box in boxes]
    widths = [box.box_width_px for box in boxes]
    heights = [box.box_height_px for box in boxes]
    aspects = [box.width * box.image_width_px / (box.height * box.image_height_px)
               for box in boxes if box.height > 0]
    return {
        "boxes": len(boxes),
        "minimum_side_px": {
            "min": round(min(sides), 2),
            "p10": round(quantile(sides, 0.10), 2),
            "median": round(quantile(sides, 0.50), 2),
            "p90": round(quantile(sides, 0.90), 2),
            "max": round(max(sides), 2),
        },
        "box_width_px": {
            "median": round(quantile(widths, 0.50), 2),
        },
        "box_height_px": {
            "median": round(quantile(heights, 0.50), 2),
        },
        "box_area_ratio_percent": {
            "min": round(100 * min(ratios), 4),
            "p10": round(100 * quantile(ratios, 0.10), 4),
            "median": round(100 * quantile(ratios, 0.50), 4),
            "p90": round(100 * quantile(ratios, 0.90), 4),
            "max": round(100 * max(ratios), 4),
        },
        "aspect_ratio": {
            "median": round(quantile(aspects, 0.50), 3),
        },
        "size_band_counts": dict(Counter(box.size_band() for box in boxes)),
    }


def recommended_action(box: Box) -> str:
    """Provisional, size-only recommendation.

    Deliberately conservative: it only ever returns ``REJECT_TOO_SMALL`` when
    the measurement is unambiguous, and never claims ambiguity, occlusion, or
    rear-facing subjects. Those need eyes, so they go to ``REVIEW_MANUALLY``.
    """
    if box.minimum_side_px < BAND_TOO_SMALL_PX:
        return "REJECT_TOO_SMALL"
    return "KEEP_CANDIDATE"


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sorted_for_sheet(boxes: Sequence[tuple[ImageRecord, Box]], largest: bool) -> list[tuple[ImageRecord, Box]]:
    return sorted(
        boxes,
        key=lambda item: (
            -item[1].minimum_side_px if largest else item[1].minimum_side_px,
            item[0].stem,
        ),
    )


def pick_random(
    boxes: Sequence[tuple[ImageRecord, Box]],
    count: int,
    salt: str,
) -> list[tuple[ImageRecord, Box]]:
    """Deterministic fixed-seed sample, independent of input ordering."""
    ordered = sorted(
        boxes,
        key=lambda item: order_key(RANDOM_SEED, f"{salt}:{item[0].stem}:{item[1].cx:.6f}"),
    )
    return ordered[:count]


def render_sheet(
    archive: SourceArchive,
    destination: Path,
    entries: Sequence[tuple[ImageRecord, Box]],
    title: str,
    *,
    side: int = 340,
    columns: int = 3,
) -> bool:
    """Render tiles with filename, split, class, box, size and area captions."""
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover - environment guard
        return False

    if not entries:
        return False

    palette = {"goggles": (0, 220, 0), "no_goggle": (0, 0, 255)}
    rows_needed = max(1, math.ceil(len(entries) / columns))
    banner = 26
    canvas = np.full(
        (rows_needed * side + banner, columns * side, 3), 28, dtype=np.uint8
    )

    cv2.putText(canvas, title[:110], (6, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 1)

    for index, (record, box) in enumerate(entries):
        payload = archive.read_bytes(record.image_member)
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            continue
        height, width = image.shape[:2]
        scale = min(side / width, side / height)
        resized = cv2.resize(
            image,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        tile_h, tile_w = resized.shape[:2]

        x1 = int((box.cx - box.width / 2) * tile_w)
        y1 = int((box.cy - box.height / 2) * tile_h)
        x2 = int((box.cx + box.width / 2) * tile_w)
        y2 = int((box.cy + box.height / 2) * tile_h)
        cv2.rectangle(resized, (x1, y1), (x2, y2), palette.get(box.source_class, (255, 255, 255)), 2)

        row, column = divmod(index, columns)
        top = banner + row * side
        left = column * side
        canvas[top:top + tile_h, left:left + tile_w] = resized

        # A dark strip keeps the caption legible over any background.
        strip_top = top + tile_h - 46 if tile_h >= 46 else top
        cv2.rectangle(canvas, (left, strip_top), (left + tile_w, top + tile_h), (0, 0, 0), -1)
        cv2.putText(canvas, record.stem[:40], (left + 4, strip_top + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 255, 255), 1)
        cv2.putText(
            canvas,
            f"{record.source_split} | {box.source_class} | side {box.minimum_side_px:.0f}px | area {100 * box.box_area_ratio:.2f}%",
            (left + 4, strip_top + 28),
            cv2.FONT_HERSHEY_SIMPLEX, 0.34, (180, 255, 180), 1,
        )
        cv2.putText(
            canvas,
            f"box {box.box_width_px:.0f}x{box.box_height_px:.0f}px  img {record.image_width_px}x{record.image_height_px}",
            (left + 4, strip_top + 41),
            cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(destination), canvas))


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def screen(
    zip_path: Path,
    out_dir: Path,
    *,
    tiles_per_sheet: int = 12,
    write_sheets: bool = True,
) -> dict[str, Any]:
    """Run the full screen and write every output file."""
    archive = SourceArchive(zip_path)

    data_yaml_member = archive.locate("data.yaml")
    if data_yaml_member is None:
        raise ScreenError("archive has no data.yaml")
    names = parse_names(archive.read_text(data_yaml_member), data_yaml_member)
    relevant_ids = resolve_relevant_ids(names)

    issues: list[Issue] = []
    records, tally = load_records(archive, relevant_ids, issues)

    boxes_by_class: dict[str, list[tuple[ImageRecord, Box]]] = {n: [] for n in RELEVANT_NAMES}
    for record in records:
        for box in record.boxes:
            boxes_by_class[box.source_class].append((record, box))

    all_boxes = [box for items in boxes_by_class.values() for _, box in items]

    # ---- counts -------------------------------------------------------
    class_rows: list[list[Any]] = []
    split_names = sorted({record.source_split for record in records})
    for cls in RELEVANT_NAMES:
        items = boxes_by_class[cls]
        images_with = {record.stem for record, _ in items}
        row: list[Any] = [cls, relevant_ids[cls], len(items), len(images_with)]
        for split in split_names:
            row.append(sum(1 for r, _ in items if r.source_split == split))
        class_rows.append(row)

    both_classes = sum(
        1 for record in records
        if len({box.source_class for box in record.boxes}) == len(RELEVANT_NAMES)
    )

    # ---- per-box statistics ------------------------------------------
    box_rows: list[list[Any]] = []
    for record in records:
        for box in record.boxes:
            box_rows.append([
                record.stem,
                record.source_split,
                box.source_class,
                box.source_id,
                record.image_width_px,
                record.image_height_px,
                round(box.box_width_px, 2),
                round(box.box_height_px, 2),
                round(box.minimum_side_px, 2),
                round(box.box_area_px, 2),
                round(box.box_area_ratio, 8),
                round(box.width, 6),
                round(box.height, 6),
                box.size_band(),
            ])

    # ---- thresholds ---------------------------------------------------
    threshold_rows_all: list[dict[str, Any]] = []
    for cls in RELEVANT_NAMES:
        threshold_rows_all.extend(threshold_rows([b for _, b in boxes_by_class[cls]], cls))
    threshold_rows_all.extend(threshold_rows(all_boxes, "all_relevant"))

    # ---- duplicates ---------------------------------------------------
    exact_groups = exact_duplicate_groups(records)
    near_groups = near_duplicate_groups(records)
    leak_rows = merge_leakage_rows(
        leakage_candidates(exact_groups, "exact")
        + leakage_candidates(near_groups, "near")
    )

    # ---- manual review queue -----------------------------------------
    queue_rows: list[list[Any]] = []
    for record in records:
        for box in record.boxes:
            queue_rows.append([
                f"{record.stem}.txt",
                record.source_split,
                box.source_class,
                record.image_width_px,
                record.image_height_px,
                round(box.box_width_px, 2),
                round(box.box_height_px, 2),
                round(box.minimum_side_px, 2),
                round(box.box_area_px, 2),
                round(box.box_area_ratio, 8),
                recommended_action(box),
                "",  # human_decision: intentionally blank for the user
                "size-based recommendation only; semantic suitability needs visual review",
            ])

    # ---- write -------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)

    header_counts = ["source_class", "source_class_id", "box_count", "image_count"]
    header_counts += [f"boxes_in_{s}" for s in split_names]
    write_csv(out_dir / "relevant_class_counts.csv", header_counts, class_rows)

    write_csv(
        out_dir / "relevant_box_statistics.csv",
        [
            "filename", "source_split", "source_class", "source_class_id",
            "image_width_px", "image_height_px", "box_width_px", "box_height_px",
            "minimum_side_px", "box_area_px", "box_area_ratio",
            "normalised_width", "normalised_height", "size_band",
        ],
        box_rows,
    )

    write_csv(
        out_dir / "size_threshold_summary.csv",
        [
            "class", "threshold_kind", "threshold_value", "boxes_total",
            "boxes_surviving", "boxes_below", "percent_surviving",
        ],
        [
            [r["class"], r["threshold_kind"], r["threshold_value"], r["boxes_total"],
             r["boxes_surviving"], r["boxes_below"], r["percent_surviving"]]
            for r in threshold_rows_all
        ],
    )

    write_csv(
        out_dir / "manual_review_queue.csv",
        [
            "filename", "source_split", "source_class", "image_width_px",
            "image_height_px", "box_width_px", "box_height_px", "minimum_side_px",
            "box_area_px", "box_area_ratio", "recommended_action",
            "human_decision", "notes",
        ],
        queue_rows,
    )

    write_csv(
        out_dir / "annotation_issues.csv",
        ["filename", "source_split", "issue_type", "detail"],
        [[i.filename, i.source_split, i.issue_type, i.detail] for i in issues],
    )

    write_csv(
        out_dir / "exact_duplicates.csv",
        ["group_id", "member_count", "members"],
        [
            [hashlib.sha256(g[0].stem.encode()).hexdigest()[:12], len(g),
             ";".join(f"{r.source_split}:{r.stem}" for r in g)]
            for g in exact_groups
        ],
    )

    write_csv(
        out_dir / "near_duplicate_candidates.csv",
        ["group_id", "member_count", "splits", "members"],
        [
            [hashlib.sha256(g[0].stem.encode()).hexdigest()[:12], len(g),
             "/".join(sorted({r.source_split for r in g})),
             ";".join(f"{r.source_split}:{r.stem}" for r in g)]
            for g in near_groups
        ],
    )

    write_csv(
        out_dir / "split_leakage_candidates.csv",
        ["group_id", "basis", "splits", "member_count", "members"],
        [[r["group_id"], r["basis"], r["splits"], r["member_count"], r["members"]]
         for r in leak_rows],
    )

    # ---- contact sheets ----------------------------------------------
    sheets: list[str] = []
    if write_sheets:
        artifacts = out_dir / "artifacts"
        plan: list[tuple[str, list[tuple[ImageRecord, Box]]]] = []

        for cls in RELEVANT_NAMES:
            items = boxes_by_class[cls]
            ordered = sorted_for_sheet(items, largest=True)
            by_side = sorted(items, key=lambda i: (i[1].minimum_side_px, i[0].stem))
            middle_start = max(0, len(by_side) // 2 - tiles_per_sheet // 2)
            plan.extend([
                (f"01_largest_{cls}", ordered[:tiles_per_sheet]),
                (f"03_medium_{cls}", by_side[middle_start:middle_start + tiles_per_sheet]),
                (f"05_smallest_{cls}", ordered[-tiles_per_sheet:][::-1]),
                (f"07_random_{cls}", pick_random(items, tiles_per_sheet, cls)),
            ])

        near_entries: list[tuple[ImageRecord, Box]] = []
        for group in near_groups[:4]:
            for record in group:
                if record.boxes:
                    near_entries.append((record, record.boxes[0]))

        leak_entries: list[tuple[ImageRecord, Box]] = []
        leak_stems = {m.split(":", 1)[1] for row in leak_rows for m in row["members"].split(";")}
        for record in records:
            if record.stem in leak_stems and record.boxes:
                leak_entries.append((record, record.boxes[0]))

        plan.append(("09_near_duplicate_groups", near_entries[:tiles_per_sheet]))
        plan.append(("10_split_leakage_groups", leak_entries[:tiles_per_sheet]))

        for name, entries in plan:
            destination = artifacts / f"contact_sheet_{name}.png"
            if render_sheet(archive, destination, entries, name):
                sheets.append(destination.as_posix())

    after_sha = archive.verify_unchanged()

    # ---- summary ------------------------------------------------------
    def useful_counts() -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for cls in RELEVANT_NAMES:
            out[cls] = {
                "total": len(boxes_by_class[cls]),
                "strong_min_side_ge_48px": sum(
                    1 for _, b in boxes_by_class[cls] if b.minimum_side_px >= 48
                ),
                "review_24_to_48px": sum(
                    1 for _, b in boxes_by_class[cls] if 24 <= b.minimum_side_px < 48
                ),
                "too_small_lt_24px": sum(
                    1 for _, b in boxes_by_class[cls] if b.minimum_side_px < 24
                ),
            }
        return out

    summary = {
        "source": {
            "zip_path": zip_path.as_posix(),
            "sha256_before": archive.sha256_before,
            "sha256_after": after_sha,
            "unchanged": after_sha == archive.sha256_before,
            "data_yaml_member": data_yaml_member,
            "class_names_total": len(names),
            "relevant_source_class_ids": relevant_ids,
        },
        "counts": {
            "images_total_in_archive": tally["images_total"],
            "images_with_relevant_box": len(records),
            "images_with_both_classes": both_classes,
            "corrupt_images": tally["corrupt_image"],
            "missing_labels": tally["missing_label"],
            "images_without_relevant_box": tally["images_without_relevant_box"],
            "relevant_boxes_total": len(all_boxes),
            "goggles_boxes": len(boxes_by_class["goggles"]),
            "no_goggle_boxes": len(boxes_by_class["no_goggle"]),
            "goggles_images": len({r.stem for r, _ in boxes_by_class["goggles"]}),
            "no_goggle_images": len({r.stem for r, _ in boxes_by_class["no_goggle"]}),
        },
        "geometry": {
            "goggles": geometry_summary([b for _, b in boxes_by_class["goggles"]]),
            "no_goggle": geometry_summary([b for _, b in boxes_by_class["no_goggle"]]),
            "all_relevant": geometry_summary(all_boxes),
        },
        "thresholds": threshold_rows_all,
        "duplicates": {
            "exact_groups": len(exact_groups),
            "near_duplicate_groups": len(near_groups),
            "near_duplicate_threshold_bits": NEAR_DUPLICATE_HAMMING,
            "cross_split_candidates": len(leak_rows),
        },
        "annotation_issues_total": len(issues),
        "provisional_useful_candidates": useful_counts(),
        "contact_sheets": sheets,
        "notes": [
            "Box size is a screening signal only. No semantic claim is made here.",
            "recommended_action is provisional and based on minimum side length.",
            "Manual visual review of the contact sheets is required before any verdict.",
        ],
    }
    write_json(out_dir / "suitability_summary.json", summary)

    write_json(
        out_dir / "audit_run_metadata.json",
        {
            "tool": "tools/screen_construction_ppe.py",
            "random_seed": RANDOM_SEED,
            "near_duplicate_hamming_threshold": NEAR_DUPLICATE_HAMMING,
            "hash_side": HASH_SIDE,
            "side_thresholds_px": list(SIDE_THRESHOLDS_PX),
            "area_thresholds": [t for t, _ in AREA_THRESHOLDS],
            "size_bands": {
                "TOO_SMALL": f"< {BAND_TOO_SMALL_PX}px",
                "REVIEW": f"{BAND_TOO_SMALL_PX}..{BAND_STRONG_PX}px",
                "STRONG": f">= {BAND_STRONG_PX}px",
            },
            "source_sha256_before": archive.sha256_before,
            "source_sha256_after": after_sha,
            "source_unchanged": after_sha == archive.sha256_before,
            "extracted_to_disk": False,
            "classes_remapped": False,
            "splits_created": False,
        },
    )

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help="source archive")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--tiles", type=int, default=12, help="tiles per contact sheet")
    parser.add_argument("--no-contact-sheets", action="store_true", help="skip rendering")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = screen(
            args.zip,
            args.out_dir,
            tiles_per_sheet=args.tiles,
            write_sheets=not args.no_contact_sheets,
        )
    except ScreenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    counts = summary["counts"]
    print(f"source sha256 : {summary['source']['sha256_before']}")
    print(f"unchanged     : {summary['source']['unchanged']}")
    print(f"relevant ids  : {summary['source']['relevant_source_class_ids']}")
    print(f"images        : {counts['images_with_relevant_box']} relevant "
          f"of {counts['images_total_in_archive']}")
    print(f"goggles       : {counts['goggles_boxes']} boxes / {counts['goggles_images']} images")
    print(f"no_goggle     : {counts['no_goggle_boxes']} boxes / {counts['no_goggle_images']} images")
    print(f"both classes  : {counts['images_with_both_classes']}")
    print(f"exact dupes   : {summary['duplicates']['exact_groups']}")
    print(f"near dupes    : {summary['duplicates']['near_duplicate_groups']}")
    print(f"cross-split   : {summary['duplicates']['cross_split_candidates']}")
    print(f"issues        : {summary['annotation_issues_total']}")
    print(f"sheets        : {len(summary['contact_sheets'])}")
    print(f"output        : {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
