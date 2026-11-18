"""Prepare the two-class eye-region YOLOv8n baseline dataset.

Reads ``data/raw/eye_region_goggles_source_original.zip`` read-only and writes a
clean, balanced ``goggles`` / ``no_goggles`` dataset to
``data/processed/eye_shield_baseline``.

Scope discipline
----------------
* The source ZIP is never modified. Its SHA-256 is captured before and after,
  and any change aborts the run.
* Only source classes 0 and 1 are kept. Source classes 2 (``head``) and 3
  (``head_with_helmet``) are dropped from the processed copies.
* No model training happens here.

Why the size gate is 24 px, not 48 px
-------------------------------------
The plan asks for a minimum relevant box side of at least 48 px "where
possible". These images are small -- the most common size is 270x179 px -- so an
absolute 48 px box is 18% of the frame width, which almost no annotated eye
region reaches: only 65 ``goggles`` images qualify, far below the 90-image
per-class floor. A 24 px floor is 8.9% of frame width and keeps 230 ``goggles``
images, enough to fill every quota with headroom.

The gate therefore sets the *eligibility* floor, and selection then ranks
candidates by size so the delivered dataset preferentially contains the largest,
most readable boxes. The actual delivered distribution is reported rather than
assumed.

Leakage handling
----------------
The source archive has real cross-split near-duplicates (4 pairs at 8x8 dHash
<= 6 and normalised pixel RMSE <= 20). Because the source split is otherwise
worth preserving, leakage is resolved by keeping the group in its lowest-priority
split and dropping the duplicated members from the others, rather than by
resplitting the whole archive. Test is treated as untouchable: a group member is
never removed from test to satisfy train.

Usage
-----
    .venv/bin/python tools/prepare_eye_region_baseline.py
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]

ZIP_PATH = ROOT / "data" / "raw" / "eye_region_goggles_source_original.zip"
OUT_DIR = ROOT / "data" / "processed" / "eye_shield_baseline"
ARTIFACT_DIR = OUT_DIR / "artifacts"

#: Source class id -> final class id. Everything else is dropped.
SOURCE_TO_FINAL: dict[int, int] = {0: 0, 1: 1}

#: Names the source data.yaml must declare, in this exact order.
EXPECTED_SOURCE_NAMES = [
    "eyes_with_goggles",
    "eyes_without_goggles",
    "head",
    "head_with_helmet",
]

FINAL_NAMES = {0: "goggles", 1: "no_goggles"}

#: Source classes deliberately discarded.
DROPPED_SOURCE_NAMES = ["head", "head_with_helmet"]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

#: Eligibility floor for the minimum side of a relevant box, in pixels.
GATE_PX = 24.0

#: Per-class, per-split delivery quotas. Sum = 120 images per class.
QUOTAS: dict[str, dict[str, int]] = {
    "train": {"goggles": 80, "no_goggles": 80},
    "val": {"goggles": 20, "no_goggles": 20},
    "test": {"goggles": 20, "no_goggles": 20},
}

SOURCE_SPLIT_TO_FINAL = {"train": "train", "valid": "val", "test": "test"}

#: Split preference order when resolving a duplicate group. Earlier wins.
SPLIT_PRIORITY = ["test", "val", "train"]

# --- near-duplicate detection -------------------------------------------
DHASH_SIDE = 8
DHASH_MAX_HAMMING = 6
NRMSE_DUPLICATE = 20.0
COMPARE_SIDE = 64

# --- label families whose class assignment conflicts with their name -----
# Files named ``eye-glasses-*`` are overwhelmingly class 1 and ``goggle-*``
# overwhelmingly class 0, but a minority carry the opposite label. Those
# minority assignments are the most likely genuine annotation errors, so they
# are excluded from the baseline and reported for human confirmation.
SUSPECT_FAMILY_BY_CLASS = {0: "eye-glasses", 1: "goggle"}


class PreparationError(Exception):
    """Raised when the archive cannot be prepared as specified."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def label_family(stem: str) -> str:
    """Strip the Roboflow ``.rf.<hash>`` suffix and trailing index digits."""
    base = re.sub(r"\.rf\.[0-9a-f]+$", "", stem)
    return re.sub(r"-?\d+.*$", "", base).rstrip("-_")


def safe_short(stem: str, limit: int = 22) -> str:
    """A filesystem-safe, human-readable short id for contact-sheet captions."""
    base = re.sub(r"\.rf\.[0-9a-f]+$", "", stem)
    return base if len(base) <= limit else base[: limit - 1] + "~"


# --------------------------------------------------------------------------
# Source model
# --------------------------------------------------------------------------


@dataclass
class RelevantBox:
    """One retained annotation, in normalised YOLO coordinates."""

    source_class: int
    final_class: int
    cx: float
    cy: float
    width: float
    height: float

    def to_yolo_row(self) -> str:
        """Emit a final-class YOLO row from the validated, clipped geometry."""
        return (
            f"{self.final_class} {self.cx:.6f} {self.cy:.6f} "
            f"{self.width:.6f} {self.height:.6f}"
        )


@dataclass
class ImageEntry:
    stem: str
    source_split: str
    image_member: str
    label_member: str
    image_width_px: int = 0
    image_height_px: int = 0
    boxes: list[RelevantBox] = field(default_factory=list)
    dropped_box_count: int = 0
    dhash: int | None = None

    @property
    def final_split(self) -> str:
        return SOURCE_SPLIT_TO_FINAL[self.source_split]

    def classes(self) -> set[int]:
        return {b.final_class for b in self.boxes}

    def best_side_px(self, final_class: int) -> float:
        sides = [
            min(b.width * self.image_width_px, b.height * self.image_height_px)
            for b in self.boxes
            if b.final_class == final_class
        ]
        return max(sides) if sides else 0.0

    def best_area_ratio(self, final_class: int) -> float:
        areas = [b.width * b.height for b in self.boxes if b.final_class == final_class]
        return max(areas) if areas else 0.0


@dataclass
class Rejection:
    filename: str
    source_split: str
    reason: str
    detail: str = ""

    def as_row(self) -> list[str]:
        return [self.filename, self.source_split, self.reason, self.detail]


# --------------------------------------------------------------------------
# Annotation parsing and validation (pure, unit-testable)
# --------------------------------------------------------------------------


class InvalidRow(ValueError):
    """A YOLO row that cannot be trusted."""


#: A box may extend past the frame edge by up to this normalised amount before
#: it is treated as corrupt. Small overshoots are ordinary annotation rounding
#: (the archive's worst mapped-class overshoot is under 0.005% of the frame) and
#: are clipped rather than rejected, because rejecting the whole image would
#: throw away a perfectly usable eye region.
CLIP_TOLERANCE = 0.01


def parse_class(raw: str) -> int:
    """Extract and validate the integer class id from a YOLO row."""
    parts = raw.split()
    if len(parts) != 5:
        raise InvalidRow(f"expected 5 fields, got {len(parts)}")
    try:
        return int(parts[0])
    except ValueError as exc:
        raise InvalidRow(f"non-integer class id {parts[0]!r}") from exc


def parse_row(raw: str) -> tuple[int, float, float, float, float]:
    """Parse and validate one YOLO row's class and geometry.

    Enforces: exactly five fields; integer class id; numeric coordinates;
    centre inside [0, 1]; positive width and height no larger than the frame.
    A box overflowing the frame edge by more than ``CLIP_TOLERANCE`` is
    rejected; a smaller overshoot is clipped to the frame and accepted.
    """
    parts = raw.split()
    if len(parts) != 5:
        raise InvalidRow(f"expected 5 fields, got {len(parts)}")

    try:
        class_id = int(parts[0])
    except ValueError as exc:
        raise InvalidRow(f"non-integer class id {parts[0]!r}") from exc

    try:
        cx, cy, width, height = (float(v) for v in parts[1:])
    except ValueError as exc:
        raise InvalidRow(f"non-numeric coordinate in {raw!r}") from exc

    for name, value in (("cx", cx), ("cy", cy), ("w", width), ("h", height)):
        if not math.isfinite(value):
            raise InvalidRow(f"non-finite {name}")

    if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
        raise InvalidRow(f"centre outside frame: {cx}, {cy}")
    if width <= 0.0 or height <= 0.0:
        raise InvalidRow(f"non-positive extent: {width}, {height}")
    if width > 1.0 or height > 1.0:
        raise InvalidRow(f"extent larger than frame: {width}, {height}")

    x_lo, x_hi = cx - width / 2, cx + width / 2
    y_lo, y_hi = cy - height / 2, cy + height / 2
    overflow = max(0.0, -x_lo, x_hi - 1.0, -y_lo, y_hi - 1.0)
    if overflow > CLIP_TOLERANCE:
        raise InvalidRow(f"box overflows frame by {overflow:.4f}")

    # Clip any tolerated overshoot back inside the frame, preserving the centre.
    x_lo, x_hi = max(0.0, x_lo), min(1.0, x_hi)
    y_lo, y_hi = max(0.0, y_lo), min(1.0, y_hi)
    cx = (x_lo + x_hi) / 2
    cy = (y_lo + y_hi) / 2
    width = x_hi - x_lo
    height = y_hi - y_lo

    return class_id, cx, cy, width, height


def remap_rows(
    raw_rows: Iterable[str],
    mapping: dict[int, int] = SOURCE_TO_FINAL,
) -> tuple[list[RelevantBox], int, list[str]]:
    """Remap a label file's rows, dropping unmapped classes.

    Only rows whose class is actually retained are geometry-validated: a
    malformed ``head`` box cannot harm a dataset that contains no ``head``
    boxes, and rejecting the whole image over one would discard a usable eye
    region. Malformed rows in mapped classes are reported and never silently
    accepted.

    Returns ``(kept_boxes, dropped_count, errors)``.
    """
    kept: list[RelevantBox] = []
    dropped = 0
    errors: list[str] = []

    for raw in raw_rows:
        line = raw.strip()
        if not line:
            continue

        try:
            class_id = parse_class(line)
        except InvalidRow as exc:
            errors.append(f"{line!r}: {exc}")
            continue

        if class_id not in mapping:
            dropped += 1
            continue

        try:
            class_id, cx, cy, width, height = parse_row(line)
        except InvalidRow as exc:
            errors.append(f"{line!r}: {exc}")
            continue

        kept.append(
            RelevantBox(
                source_class=class_id,
                final_class=mapping[class_id],
                cx=cx,
                cy=cy,
                width=width,
                height=height,
            )
        )

    return kept, dropped, errors


# --------------------------------------------------------------------------
# Archive access
# --------------------------------------------------------------------------


class SourceArchive:
    """Read-only view over the source ZIP."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise PreparationError(f"source archive missing: {path}")
        self.path = path
        self._zip = zipfile.ZipFile(path, "r")
        self.sha256_before = sha256_file(path)
        self.names = self._zip.namelist()

    def read_bytes(self, member: str) -> bytes:
        return self._zip.read(member)

    def read_text(self, member: str) -> str:
        return self._zip.read(member).decode("utf-8", "replace")

    def verify_unchanged(self) -> bool:
        """True when the archive on disk still hashes to its opening value."""
        return sha256_file(self.path) == self.sha256_before

    def close(self) -> None:
        self._zip.close()


def parse_data_yaml_names(text: str) -> list[str]:
    """Extract the ``names:`` list from a Roboflow-style data.yaml."""
    match = re.search(r"^names:\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise PreparationError("data.yaml has no inline 'names: [...]' list")

    body = match.group(1)
    names = [chunk.strip().strip("'\"") for chunk in body.split(",")]
    names = [n for n in names if n]
    if not names:
        raise PreparationError("data.yaml declares an empty names list")
    return names


def index_members(names: Sequence[str]) -> dict[str, dict[str, dict[str, str]]]:
    """Group ZIP members as ``split -> stem -> {'image'|'label': member}``."""
    pattern = re.compile(r"^(train|valid|test)/(images|labels)/(.+)$")
    index: dict[str, dict[str, dict[str, str]]] = {}

    for member in names:
        match = pattern.match(member)
        if not match:
            continue
        split, kind, rest = match.groups()
        stem, dot, suffix = rest.rpartition(".")
        if not dot:
            continue
        if kind == "images" and f".{suffix.lower()}" not in IMAGE_SUFFIXES:
            continue
        if kind == "labels" and suffix.lower() != "txt":
            continue
        key = "image" if kind == "images" else "label"
        index.setdefault(split, {}).setdefault(stem, {})[key] = member

    return index


def load_entries(
    archive: SourceArchive,
    index: dict[str, dict[str, dict[str, str]]],
) -> tuple[list[ImageEntry], list[Rejection], list[str]]:
    """Read every label file, remap classes, and keep images with retained boxes."""
    entries: list[ImageEntry] = []
    rejections: list[Rejection] = []
    annotation_errors: list[str] = []

    for source_split in ("train", "valid", "test"):
        for stem, pair in sorted(index.get(source_split, {}).items()):
            image_member = pair.get("image")
            label_member = pair.get("label")

            if image_member is None:
                rejections.append(
                    Rejection(f"{stem}.txt", source_split, "missing_image", "label without image")
                )
                continue
            if label_member is None:
                rejections.append(
                    Rejection(f"{stem}.jpg", source_split, "missing_label", "image without label")
                )
                continue

            boxes, dropped, errors = remap_rows(archive.read_text(label_member).splitlines())
            if errors:
                annotation_errors.extend(f"{source_split}/{stem}: {e}" for e in errors)
                rejections.append(
                    Rejection(
                        f"{stem}.txt",
                        source_split,
                        "invalid_yolo_row",
                        f"{len(errors)} bad row(s)",
                    )
                )
                continue

            if not boxes:
                rejections.append(
                    Rejection(
                        f"{stem}.txt",
                        source_split,
                        "no_relevant_box",
                        f"all {dropped} box(es) were classes 2/3",
                    )
                )
                continue

            entries.append(
                ImageEntry(
                    stem=stem,
                    source_split=source_split,
                    image_member=image_member,
                    label_member=label_member,
                    boxes=boxes,
                    dropped_box_count=dropped,
                )
            )

    return entries, rejections, annotation_errors


# --------------------------------------------------------------------------
# Image measurement and duplicates
# --------------------------------------------------------------------------


def measure_images(archive: SourceArchive, entries: list[ImageEntry]) -> list[Rejection]:
    """Populate image dimensions and dHash; reject undecodable images."""
    import cv2
    import numpy as np

    rejections: list[Rejection] = []
    survivors: list[ImageEntry] = []

    for entry in entries:
        payload = archive.read_bytes(entry.image_member)
        image = (
            cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if payload
            else None
        )
        if image is None:
            rejections.append(
                Rejection(
                    Path(entry.image_member).name,
                    entry.source_split,
                    "corrupt_image",
                    "could not be decoded",
                )
            )
            continue

        height, width = image.shape[:2]
        entry.image_width_px, entry.image_height_px = width, height

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (DHASH_SIDE + 1, DHASH_SIDE), interpolation=cv2.INTER_AREA)
        bits = (small[:, 1:] > small[:, :-1]).flatten()
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        entry.dhash = value
        survivors.append(entry)

    entries[:] = survivors
    return rejections


def normalised_rmse(a: "Any", b: "Any") -> float:
    import numpy as np

    return float(np.sqrt(((a - b) ** 2).mean()))


def near_duplicate_pairs(
    archive: SourceArchive,
    entries: list[ImageEntry],
) -> list[tuple[ImageEntry, ImageEntry, int, float]]:
    """Find genuine near-duplicates.

    A pair must pass both an 8x8 dHash Hamming test *and* a normalised pixel
    RMSE test on 64x64 greyscale thumbnails. The hash alone is far too loose on
    small images: at Hamming <= 10 the median pixel RMSE of "matches" is 68 on a
    0-255 scale, i.e. visibly different pictures. Requiring RMSE <= 20 removes
    that over-clustering.
    """
    import cv2
    import numpy as np

    thumbs: dict[str, "Any"] = {}
    for entry in entries:
        image = cv2.imdecode(
            np.frombuffer(archive.read_bytes(entry.image_member), dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        thumbs[entry.stem] = cv2.resize(
            image, (COMPARE_SIDE, COMPARE_SIDE), interpolation=cv2.INTER_AREA
        ).astype(np.float32)

    pairs: list[tuple[ImageEntry, ImageEntry, int, float]] = []
    ordered = sorted(entries, key=lambda e: (e.source_split, e.stem))

    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a, b = ordered[i], ordered[j]
            if a.dhash is None or b.dhash is None:
                continue
            hamming = bin(a.dhash ^ b.dhash).count("1")
            if hamming > DHASH_MAX_HAMMING:
                continue
            error = normalised_rmse(thumbs[a.stem], thumbs[b.stem])
            if error <= NRMSE_DUPLICATE:
                pairs.append((a, b, hamming, error))

    return pairs


def group_components(
    entries: list[ImageEntry],
    pairs: Sequence[tuple[ImageEntry, ImageEntry, int, float]],
) -> list[list[ImageEntry]]:
    """Union-find the near-duplicate pairs into groups of 2+ entries."""
    parent = {e.stem: e.stem for e in entries}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b, _h, _e in pairs:
        union(a.stem, b.stem)

    buckets: dict[str, list[ImageEntry]] = collections.defaultdict(list)
    for entry in entries:
        buckets[find(entry.stem)].append(entry)

    return [sorted(g, key=lambda e: (e.source_split, e.stem)) for g in buckets.values() if len(g) > 1]


def is_exact_duplicate_pair(a: ImageEntry, b: ImageEntry) -> bool:
    return a.dhash is not None and a.dhash == b.dhash


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def eligibility(
    entry: ImageEntry,
    final_class: int,
) -> tuple[bool, str]:
    """Decide whether an image may represent ``final_class``.

    Returns ``(ok, reason_if_not)``. Only objective checks live here; pose and
    occlusion are left to the human.
    """
    if final_class not in entry.classes():
        return False, "class_absent"

    # An image holding both classes cannot be attributed to a single class
    # without double counting, and it teaches ambiguous co-occurrence.
    if len(entry.classes()) > 1:
        return False, "both_classes_present"

    # A tiny box is not a usable eye region at 640 input.
    if entry.best_side_px(final_class) < GATE_PX:
        return False, "below_size_gate"

    # Name/label family disagreement, the likeliest annotation error.
    suspect = SUSPECT_FAMILY_BY_CLASS.get(final_class)
    if suspect and label_family(entry.stem) == suspect:
        return False, "suspect_label_family"

    return True, ""


def rank_key(entry: ImageEntry, final_class: int) -> tuple:
    """Largest box first, then largest area, then a stable tiebreak."""
    return (
        -entry.best_side_px(final_class),
        -entry.best_area_ratio(final_class),
        entry.stem,
    )


def resolve_leakage(
    groups: Sequence[Sequence[ImageEntry]],
) -> tuple[set[str], list[list[ImageEntry]]]:
    """Drop duplicate-group members so each group lives in one split only.

    The group is anchored in the highest-priority split it appears in
    (test > val > train) and the other splits' copies are dropped. A group that
    is confined to train is left alone: train-internal duplicates do not leak.
    """
    dropped: set[str] = set()
    report: list[list[ImageEntry]] = []

    for group in groups:
        splits = {e.source_split for e in group}
        if len(splits) < 2:
            continue
        anchor = min(splits, key=lambda s: SPLIT_PRIORITY.index(SOURCE_SPLIT_TO_FINAL[s]))
        for entry in group:
            if entry.source_split != anchor:
                dropped.add(entry.stem)
        report.append(list(group))

    return dropped, report


def select_split(
    entries: Sequence[ImageEntry],
    final_class: int,
    quota: int,
    excluded: set[str],
    group_of: dict[str, int] | None = None,
) -> tuple[list[ImageEntry], list[tuple[ImageEntry, str]]]:
    """Pick up to ``quota`` entries for one class in one split.

    Within a split, only the best-ranked member of each near-duplicate group is
    kept, so the delivered dataset never spends two slots on the same picture.
    """
    group_of = group_of or {}
    eligible: list[ImageEntry] = []
    rejected: list[tuple[ImageEntry, str]] = []

    for entry in entries:
        if entry.stem in excluded:
            rejected.append((entry, "duplicate_group_other_split"))
            continue
        ok, reason = eligibility(entry, final_class)
        if ok:
            eligible.append(entry)
        else:
            rejected.append((entry, reason))

    eligible.sort(key=lambda e: rank_key(e, final_class))

    chosen: list[ImageEntry] = []
    used_groups: set[int] = set()
    for entry in eligible:
        group = group_of.get(entry.stem)
        if group is not None and group in used_groups:
            rejected.append((entry, "duplicate_of_selected_image"))
            continue
        if len(chosen) >= quota:
            rejected.append((entry, "not_selected_quota_full"))
            continue
        if group is not None:
            used_groups.add(group)
        chosen.append(entry)

    return chosen, rejected


# --------------------------------------------------------------------------
# Dataset writing
# --------------------------------------------------------------------------


def write_dataset(
    archive: SourceArchive,
    selection: dict[str, dict[int, list[ImageEntry]]],
) -> None:
    """Materialise images and filtered labels under ``OUT_DIR``."""
    for final_split, per_class in selection.items():
        image_dir = OUT_DIR / final_split / "images"
        label_dir = OUT_DIR / final_split / "labels"
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)

        seen: set[str] = set()
        for entries in per_class.values():
            for entry in entries:
                if entry.stem in seen:
                    continue
                seen.add(entry.stem)

                suffix = Path(entry.image_member).suffix
                (image_dir / f"{entry.stem}{suffix}").write_bytes(
                    archive.read_bytes(entry.image_member)
                )
                rows = "\n".join(box.to_yolo_row() for box in entry.boxes) + "\n"
                (label_dir / f"{entry.stem}.txt").write_text(rows, encoding="utf-8")


def write_data_yaml(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                f"path: {OUT_DIR.as_posix()}",
                "train: train/images",
                "val: val/images",
                "test: test/images",
                "",
                "names:",
                "  0: goggles",
                "  1: no_goggles",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))


# --------------------------------------------------------------------------
# Contact sheets
# --------------------------------------------------------------------------


def render_sheet(
    archive: SourceArchive,
    destination: Path,
    entries: Sequence[tuple[ImageEntry, int]],
    title: str,
    *,
    side: int = 220,
    columns: int = 6,
) -> bool:
    """Draw a contact sheet of cropped boxes with captions.

    Each tile shows a safe short filename, the original split, the original
    class, the final class, the box, and the minimum side in pixels.
    """
    import cv2
    import numpy as np

    if not entries:
        return False

    pad = 6
    caption = 54
    rows = math.ceil(len(entries) / columns)
    canvas = np.full(
        (rows * (side + caption + pad) + pad + 26,
         columns * (side + pad) + pad, 3),
        24, dtype=np.uint8,
    )

    cv2.putText(
        canvas, title[:110], (pad, 20),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA,
    )

    for index, (entry, final_class) in enumerate(entries):
        image = cv2.imdecode(
            np.frombuffer(archive.read_bytes(entry.image_member), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            continue

        box = next((b for b in entry.boxes if b.final_class == final_class), entry.boxes[0])
        height, width = image.shape[:2]
        x1 = max(0, int((box.cx - box.width / 2) * width))
        x2 = min(width, int((box.cx + box.width / 2) * width))
        y1 = max(0, int((box.cy - box.height / 2) * height))
        y2 = min(height, int((box.cy + box.height / 2) * height))

        # Pad the crop so the eye region is visible in context, not alone.
        margin_x = int(0.35 * (x2 - x1))
        margin_y = int(0.35 * (y2 - y1))
        px1, px2 = max(0, x1 - margin_x), min(width, x2 + margin_x)
        py1, py2 = max(0, y1 - margin_y), min(height, y2 + margin_y)
        crop = image[py1:py2, px1:px2]
        if crop.size == 0:
            continue
        crop = cv2.resize(crop, (side, side), interpolation=cv2.INTER_LINEAR)

        # Redraw the box inside the padded crop.
        sx = side / max(1, px2 - px1)
        sy = side / max(1, py2 - py1)
        cv2.rectangle(
            crop,
            (int((x1 - px1) * sx), int((y1 - py1) * sy)),
            (int((x2 - px1) * sx), int((y2 - py1) * sy)),
            (0, 255, 0), 2,
        )

        row, column = divmod(index, columns)
        top = 26 + pad + row * (side + caption + pad)
        left = pad + column * (side + pad)
        canvas[top:top + side, left:left + side] = crop

        lines = [
            safe_short(entry.stem),
            f"{entry.source_split} -> {entry.final_split}  src cls {box.source_class} -> {final_class}",
            f"min side {min(box.width * width, box.height * height):.0f}px",
        ]
        for offset, text in enumerate(lines):
            cv2.putText(
                canvas, text, (left, top + side + 15 + offset * 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, (220, 220, 220), 1, cv2.LINE_AA,
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(destination), canvas))


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def run() -> int:
    archive = SourceArchive(ZIP_PATH)
    try:
        return _run_inner(archive)
    finally:
        archive.close()


def _run_inner(archive: SourceArchive) -> int:
    print(f"source zip : {ZIP_PATH}")
    print(f"sha256     : {archive.sha256_before}")

    # -- source classes -------------------------------------------------
    yaml_text = archive.read_text("data.yaml")
    names = parse_data_yaml_names(yaml_text)
    if names[:4] != EXPECTED_SOURCE_NAMES:
        raise PreparationError(
            f"source class names {names[:4]} do not match the confirmed "
            f"{EXPECTED_SOURCE_NAMES}"
        )
    print(f"classes    : {dict(enumerate(names[:4]))}")

    index = index_members(archive.names)
    entries, rejections, annotation_errors = load_entries(archive, index)
    print(f"entries    : {len(entries)} with at least one relevant box")

    rejections.extend(measure_images(archive, entries))
    if annotation_errors:
        print(f"annotation errors: {len(annotation_errors)} (see rejected_candidates.csv)")

    # -- duplicates ------------------------------------------------------
    pairs = near_duplicate_pairs(archive, entries)
    groups = group_components(entries, pairs)
    exact_pairs = sum(1 for a, b, h, e in pairs if h == 0 and e <= 0.5)
    dropped_for_leakage, leakage_report = resolve_leakage(groups)
    group_of: dict[str, int] = {}
    for group_index, group in enumerate(groups):
        for entry in group:
            group_of[entry.stem] = group_index
    print(f"near-dups  : {len(pairs)} pair(s), {len(groups)} group(s), "
          f"{exact_pairs} exact, {len(leakage_report)} cross-split group(s)")

    # -- selection -------------------------------------------------------
    selection: dict[str, dict[int, list[ImageEntry]]] = {}
    rejection_rows: list[Rejection] = list(rejections)

    for final_split in ("train", "val", "test"):
        source_split = {"train": "train", "val": "valid", "test": "test"}[final_split]
        pool = [e for e in entries if e.source_split == source_split]
        selection[final_split] = {}

        for final_class, quota in (
            (0, QUOTAS[final_split]["goggles"]),
            (1, QUOTAS[final_split]["no_goggles"]),
        ):
            chosen, rejected = select_split(
                pool, final_class, quota, dropped_for_leakage, group_of
            )
            selection[final_split][final_class] = chosen
            for entry, reason in rejected:
                # An image can be rejected for both classes; record it once per class.
                if reason == "class_absent":
                    continue
                rejection_rows.append(
                    Rejection(
                        f"{entry.stem}.txt",
                        entry.source_split,
                        reason,
                        f"final class {final_class} ({FINAL_NAMES[final_class]})",
                    )
                )
            print(f"  {final_split:5s} {FINAL_NAMES[final_class]:11s} "
                  f"quota={quota:3d} selected={len(chosen):3d}")

    # -- write -------------------------------------------------------------
    if OUT_DIR.exists():
        import shutil

        shutil.rmtree(OUT_DIR)
    write_dataset(archive, selection)
    write_data_yaml(OUT_DIR / "data.yaml")

    # -- manifests ---------------------------------------------------------
    manifest_rows = []
    for final_split, per_class in selection.items():
        for final_class, chosen in per_class.items():
            for entry in chosen:
                box = next(b for b in entry.boxes if b.final_class == final_class)
                manifest_rows.append([
                    f"{final_split}/images/{entry.stem}{Path(entry.image_member).suffix}",
                    final_split,
                    entry.source_split,
                    entry.stem,
                    FINAL_NAMES[final_class],
                    final_class,
                    box.source_class,
                    entry.image_width_px,
                    entry.image_height_px,
                    round(min(box.width * entry.image_width_px,
                              box.height * entry.image_height_px), 2),
                    round(box.width * box.height, 8),
                    label_family(entry.stem),
                ])

    write_csv(
        OUT_DIR / "source_manifest.csv",
        ["file", "final_split", "source_split", "source_stem", "final_class_name",
         "final_class_id", "source_class_id", "image_width_px", "image_height_px",
         "min_side_px", "area_ratio", "label_family"],
        manifest_rows,
    )

    test_rows = [r for r in manifest_rows if r[1] == "test"]
    write_csv(
        OUT_DIR / "test_manifest.csv",
        ["file", "final_split", "source_split", "source_stem", "final_class_name",
         "final_class_id", "source_class_id", "image_width_px", "image_height_px",
         "min_side_px", "area_ratio", "label_family"],
        test_rows,
    )

    # class_counts.csv: boxes per class per split, from the written labels.
    count_rows = []
    for final_split in ("train", "val", "test"):
        for final_class in (0, 1):
            label_dir = OUT_DIR / final_split / "labels"
            images = 0
            boxes = 0
            for label_file in sorted(label_dir.glob("*.txt")):
                rows = [r.split() for r in label_file.read_text().splitlines() if r.strip()]
                hit = [r for r in rows if int(float(r[0])) == final_class]
                if hit:
                    images += 1
                    boxes += len(hit)
            count_rows.append([final_split, final_class, FINAL_NAMES[final_class],
                               images, boxes])
    write_csv(
        OUT_DIR / "class_counts.csv",
        ["split", "final_class_id", "final_class_name", "images", "boxes"],
        count_rows,
    )

    seen_rejections: set[tuple[str, str]] = set()
    unique_rejections = []
    for rejection in rejection_rows:
        key = (rejection.filename, rejection.reason)
        if key in seen_rejections:
            continue
        seen_rejections.add(key)
        unique_rejections.append(rejection)
    write_csv(
        OUT_DIR / "rejected_candidates.csv",
        ["filename", "source_split", "reason", "detail"],
        [r.as_row() for r in unique_rejections],
    )

    rejection_tally = collections.Counter(r.reason for r in unique_rejections)
    summary = {
        "source_zip": ZIP_PATH.as_posix(),
        "source_zip_sha256": archive.sha256_before,
        "source_classes": dict(enumerate(names[:4])),
        "final_classes": {str(k): v for k, v in FINAL_NAMES.items()},
        "source_to_final_mapping": {"0": 0, "1": 1},
        "dropped_source_classes": DROPPED_SOURCE_NAMES,
        "size_gate_px": GATE_PX,
        "quotas": QUOTAS,
        "counts": {
            final_split: {
                FINAL_NAMES[final_class]: len(chosen)
                for final_class, chosen in per_class.items()
            }
            for final_split, per_class in selection.items()
        },
        "totals": {
            FINAL_NAMES[c]: sum(len(selection[s][c]) for s in selection)
            for c in (0, 1)
        },
        "duplicates": {
            "near_duplicate_pairs": len(pairs),
            "groups": len(groups),
            "exact_pairs": exact_pairs,
            "cross_split_groups": len(leakage_report),
            "members_dropped_for_leakage": len(dropped_for_leakage),
            "criteria": {
                "dhash_hamming_max": DHASH_MAX_HAMMING,
                "normalised_rmse_max": NRMSE_DUPLICATE,
            },
        },
        "rejections": dict(sorted(rejection_tally.items())),
        "rejections_total": len(unique_rejections),
        "annotation_errors": len(annotation_errors),
        "source_zip_unchanged": archive.verify_unchanged(),
    }
    (OUT_DIR / "split_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    # -- attribution -------------------------------------------------------
    (OUT_DIR / "attribution.md").write_text(
        "\n".join([
            "# Dataset Attribution",
            "",
            "## Original source",
            "",
            "- Dataset: Safety Goggles > eye-region-source-v1",
            "- Roboflow workspace: `eie3360-fwbnu`",
            "- Project: `safety-goggles-btrg5-ctdvk`, version 1",
            "- URL: https://universe.roboflow.com/eie3360-fwbnu/safety-goggles-btrg5-ctdvk/dataset/1",
            "- Licence: **CC BY 4.0**",
            "",
            "## Required attribution",
            "",
            "This processed dataset is a derivative of the source above. Any",
            "redistribution, publication, demonstration, or report must credit the",
            "original dataset and indicate that changes were made:",
            "",
            "- images were retained only where a `eyes_with_goggles` or",
            "  `eyes_without_goggles` annotation existed;",
            "- classes `head` and `head_with_helmet` were removed;",
            "- class ids were remapped from source 0/1 to final 0/1;",
            "- a subset was selected and split into train/val/test.",
            "",
            "CC BY 4.0: https://creativecommons.org/licenses/by/4.0/",
            "",
            "## Third-party rights",
            "",
            "The source images remain the property of their original creators. The",
            "CC BY 4.0 licence is asserted by the dataset publisher; this project",
            "makes no independent claim over the imagery.",
            "",
        ]),
        encoding="utf-8",
    )

    # -- contact sheets ----------------------------------------------------
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    render_sheet(
        archive,
        ARTIFACT_DIR / "sheet_goggles_train.png",
        [(e, 0) for e in selection["train"][0][:24]],
        f"Selected goggles TRAIN (showing 24 of {len(selection['train'][0])})",
    )
    render_sheet(
        archive,
        ARTIFACT_DIR / "sheet_no_goggles_train.png",
        [(e, 1) for e in selection["train"][1][:24]],
        f"Selected no_goggles TRAIN (showing 24 of {len(selection['train'][1])})",
    )
    render_sheet(
        archive,
        ARTIFACT_DIR / "sheet_test_locked.png",
        [(e, 0) for e in selection["test"][0]] + [(e, 1) for e in selection["test"][1]],
        "LOCKED TEST SET - do not tune on these",
    )

    suspect_entries = []
    for entry in entries:
        for final_class in sorted(entry.classes()):
            reject_reason = ""
            if len(entry.classes()) > 1:
                reject_reason = "both_classes_present"
            elif label_family(entry.stem) == SUSPECT_FAMILY_BY_CLASS.get(final_class):
                reject_reason = "suspect_label_family"
            if reject_reason:
                suspect_entries.append((entry, final_class))
    unique_suspect = []
    seen_stems = set()
    for entry, final_class in suspect_entries:
        if entry.stem in seen_stems:
            continue
        seen_stems.add(entry.stem)
        unique_suspect.append((entry, final_class))
    render_sheet(
        archive,
        ARTIFACT_DIR / "sheet_rejected_questionable.png",
        unique_suspect[:24],
        f"Questionable: name/label family conflict or both classes (n={len(unique_suspect)}, showing up to 24)",
    )

    group_tiles: list[tuple[ImageEntry, int]] = []
    for group in groups:
        for entry in group:
            group_tiles.append((entry, sorted(entry.classes())[0] if entry.classes() else 0))
    render_sheet(
        archive,
        ARTIFACT_DIR / "sheet_duplicate_groups.png",
        group_tiles[:24],
        f"Near-duplicate groups ({len(groups)} group(s), {len(group_tiles)} tiles, showing up to 24)",
    )

    print()
    print(f"total      : goggles={summary['totals']['goggles']} "
          f"no_goggles={summary['totals']['no_goggles']}")
    print(f"rejections : {dict(sorted(rejection_tally.items()))}")
    print(f"output     : {OUT_DIR}")
    print(f"sheets     : {ARTIFACT_DIR}")

    if not archive.verify_unchanged():
        raise PreparationError("source archive changed during preparation")
    print(f"zip intact : {archive.sha256_before}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    try:
        return run()
    except PreparationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
