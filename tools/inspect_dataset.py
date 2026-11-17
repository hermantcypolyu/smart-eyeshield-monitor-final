#!/usr/bin/env python3
"""Read-only audit of the source Roboflow dataset export.

Phase 1 deliverable (plan.md section 11). This tool answers *what is in the
archive*; it never answers *what the labels should be*. It performs no
relabelling, no class remapping, no resizing, no augmentation and no training.

Guarantees
----------
1. The source ZIP is opened ``"rb"`` and never written, renamed, moved or
   deleted. Its SHA-256 is recorded before any extraction happens.
2. Extraction goes to a temporary directory that is outside the repository by
   default, so no extracted imagery can ever be staged by accident.
3. Nothing is written inside ``data/raw/``.
4. A full audit writes only the committed ``data/reviewed/audit/*.{json,csv}``
   records plus an ignored ``artifacts/`` directory for rendered images.
5. Invalid ZIP, missing/short ``data.yaml``, unusable split structure and
   malformed metadata are fatal: a readable message is printed and the exit
   code is non-zero. Tracebacks are never shown to the operator.
6. Two consecutive runs produce byte-identical structured outputs; only the
   timestamps in ``audit_run_metadata.json`` are allowed to differ.

Memory strategy
---------------
Images are processed one at a time (``zipfile.open`` stream -> ``cv2.imdecode``
-> hashes -> discard). Only fixed-width integer/float records are retained, so
peak memory is independent of dataset size.

Usage
-----
    python tools/inspect_dataset.py --zip data/raw/roboflow_safety_glasses_original.zip
    python tools/inspect_dataset.py --zip <zip> --seed 1337 --no-contact-sheets

Exit codes
----------
    0  audit completed, no blocking problem
    2  invalid input (ZIP/YAML/structure/metadata)   -> nothing reliable to audit
    3  audit completed but raised blocking findings  -> see dataset_summary.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import random
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

PHASE = 1

# --------------------------------------------------------------------------
# Constants fixed by the Phase 1 specification. Changing any of these changes
# the audit, so they are named and reported rather than buried in the code.
# --------------------------------------------------------------------------

#: Deterministic default for the manual-review sample and contact-sheet picks.
DEFAULT_SEED = 1337

#: Near-duplicate decision threshold, in bits, over a 64-bit dHash.
#: 0 bits = identical hash (caught by SHA-256 anyway); 10 bits allows for
#: re-encoded/re-saved copies of the same source frame while staying far from
#: the ~32-bit distance typical of unrelated images. Documented in
#: docs/dataset_audit.md; reported in every run by audit_run_metadata.json.
DEFAULT_NEAR_DUP_BITS = 10

#: dHash is computed on this fixed square re-sample so hashes are comparable
#: across images that were exported at different resolutions.
HASH_SIDE = 8

#: A box smaller than this fraction of the frame is too small to carry a
#: reliable compliance decision (plan.md section 5.3).
#: Recorded as an *observation*, never used to drop or rewrite a label.
MIN_BOX_AREA_RATIO = 0.02

#: Plan section 5.5 requires an independent test split near 10%.
TARGET_TEST_SHARE = 0.10

#: Plan section 5.4 targets.
TARGET_MIN_IMAGES = 500
TARGET_MAX_IMAGES = 800
TARGET_MIN_INSTANCES_PER_CLASS = 250

#: The two classes plan.md section 5.2 actually requires.
REQUIRED_CLASSES = ("goggles", "no_goggles")

IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
)

#: Roboflow appends a random suffix to exported stems: ``foo_jpg.rf.abc123``.
#: Stripping it recovers the underlying source-file stem, which is what leaks
#: between splits when a single clip was exported twice.
_ROBOFLOW_SUFFIX = re.compile(r"_((?:jpg|jpeg|png|bmp|webp))\.rf\.[0-9a-fA-F]+$")

#: Trailing numeric frame counters: ``IMG_4868_28-31sec`` -> ``IMG_4868``.
_TRAILING_FRAME_NO = re.compile(r"[_-](\d{1,4})$")

#: Cap on the deterministic manual-review sample (the queue is a sample, not a
#: re-annotation of the dataset).
MAX_REVIEW_ROWS = 400


class AuditInputError(Exception):
    """Unrecoverable problem with the input. Reported without a traceback."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def pair_key(name: str) -> str:
    """Exact stem used to pair an image with its label.

    This must NOT be normalised: two distinct exports of the same source file
    (``SG10_jpg.rf.<hashA>`` and ``SG10_jpg.rf.<hashB>``) are two different
    images that each own their own label file. Pairing on the normalised stem
    would make one of them look unlabelled.
    """
    return Path(Path(name).name).stem


def normalise_stem(name: str) -> str:
    """Reduce an exported filename to a comparable source stem.

    ``IMG_4868_28-31sec_jpg.rf.7458b7...jpg`` -> ``IMG_4868_28-31sec``
    ``1424_jpg.rf.f97f7b...jpg``              -> ``1424``
    """
    stem = Path(Path(name).name).stem
    stem = _ROBOFLOW_SUFFIX.sub("", stem)
    for suffix in ("_jpg", "_jpeg", "_png", "_bmp", "_webp"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def frame_group_key(stem: str) -> str:
    """Group key for leakage detection of adjacent video frames.

    A trailing numeric chunk is removed once so ``frame1197``/``frame1198``
    collapse to ``frame``.
    """
    base = stem.lower()
    return _TRAILING_FRAME_NO.sub("", base) or base


def dhash(gray: "np.ndarray", side: int = HASH_SIDE) -> int:
    """64-bit difference hash of an 8-bit grayscale image (row-difference)."""
    import cv2  # local import: keeps CLI import cost low and aids testing

    resized = cv2.resize(gray, (side + 1, side), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile; deterministic and dependency-free."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return float(ordered[lower])
    weight = pos - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def median(values: Sequence[float]) -> float:
    return quantile(values, 0.5)


def relpath(target: Path, start: Path) -> str:
    try:
        return target.resolve().relative_to(start.resolve()).as_posix()
    except ValueError:
        return target.as_posix()


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class ImageRecord:
    split: str
    member: str            # path inside the ZIP
    filename: str
    stem: str              # normalised source stem, used for leakage grouping
    pair_key: str = ""     # exact stem, used to pair an image with its label
    width: int = 0
    height: int = 0
    channels: int = 0
    bytes: int = 0
    sha256: str = ""
    dhash: int = -1
    ok: bool = True
    problem: str = ""


@dataclass
class LabelRecord:
    split: str
    member: str
    filename: str
    stem: str
    pair_key: str = ""
    exists: bool = True
    empty: bool = False
    rows: int = 0
    instances: int = 0
    malformed_rows: int = 0
    out_of_range_rows: int = 0
    zero_area_rows: int = 0
    duplicate_rows: int = 0
    negative_coord_rows: int = 0
    unknown_class_rows: int = 0
    class_ids: list[int] = field(default_factory=list)


@dataclass
class BoxRecord:
    split: str
    filename: str
    row: int
    class_id: int
    class_name: str
    cx: float
    cy: float
    w: float
    h: float
    area_ratio: float
    aspect: float
    small: bool


@dataclass
class Issue:
    category: str
    split: str
    filename: str
    detail: str
    severity: str = "error"


# --------------------------------------------------------------------------
# ZIP access
# --------------------------------------------------------------------------


class DatasetArchive:
    """Read-only view over the source ZIP."""

    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            raise AuditInputError(
                f"source archive not found: {path}\n"
                "Place the untouched Roboflow export there, or pass --zip <path>."
            )
        if not path.is_file():
            raise AuditInputError(f"source archive is not a file: {path}")
        if not zipfile.is_zipfile(path):
            raise AuditInputError(
                f"not a valid ZIP archive: {path}\n"
                "The file is corrupt, truncated, or not a ZIP. Do not unzip it "
                "by hand; re-download the original export."
            )
        try:
            # Mode "r" is zipfile's read-only mode; it never opens for writing.
            self._zip = zipfile.ZipFile(path, "r")
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            raise AuditInputError(f"cannot open ZIP {path}: {exc}") from exc
        self.names: list[str] = [n for n in self._zip.namelist() if not n.endswith("/")]
        self.name_set = set(self.names)
        # Duplicate member names are legal in ZIP but silently shadow each
        # other on extraction; report rather than hide.
        self.duplicate_members = sorted(
            name for name, count in Counter(self.names).items() if count > 1
        )

    def read_bytes(self, member: str) -> bytes:
        try:
            return self._zip.read(member)
        except KeyError as exc:
            raise AuditInputError(f"archive member vanished: {member}") from exc
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise AuditInputError(f"cannot read {member} from ZIP: {exc}") from exc

    def open_stream(self, member: str):
        """Streaming read, so a large image never sits in memory twice."""
        try:
            return self._zip.open(member, "r")
        except KeyError as exc:
            raise AuditInputError(f"archive member vanished: {member}") from exc

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "DatasetArchive":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def locate_metadata(archive: DatasetArchive) -> tuple[str, str, str]:
    """Find data.yaml and the README pair, tolerating a single wrapper folder."""
    names = archive.names

    def find(basename: str) -> str | None:
        exact = [n for n in names if n == basename]
        if exact:
            return exact[0]
        nested = sorted(
            (n for n in names if n.rsplit("/", 1)[-1] == basename),
            key=lambda n: n.count("/"),
        )
        return nested[0] if nested else None

    yaml_member = find("data.yaml") or find("data.yml")
    if yaml_member is None:
        raise AuditInputError(
            "the archive contains no data.yaml at its root.\n"
            "A valid Roboflow YOLOv8 export has data.yaml next to the split "
            "folders. Re-export as 'YOLOv8' and re-run."
        )
    readme_rf = find("README.roboflow.txt") or ""
    readme_ds = find("README.dataset.txt") or ""
    return yaml_member, readme_rf, readme_ds


def parse_data_yaml(text: str, member: str) -> dict[str, Any]:
    """Parse data.yaml, rejecting anything we cannot audit honestly."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AuditInputError(
            "PyYAML is required to read data.yaml but is not installed.\n"
            "Run: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc

    try:
        parsed = yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError plus exotic scanner errors
        raise AuditInputError(
            f"{member} is not valid YAML: {exc}\n"
            "Re-export the dataset; editing the label file by hand is not an audit."
        ) from exc

    if not isinstance(parsed, dict):
        raise AuditInputError(
            f"{member} did not parse to a mapping (got {type(parsed).__name__})."
        )

    names = parsed.get("names")
    if isinstance(names, dict):  # Ultralytics also accepts {0: 'a', 1: 'b'}
        try:
            names = [names[k] for k in sorted(names)]
        except (KeyError, TypeError) as exc:
            raise AuditInputError(
                f"{member}: 'names' mapping is not keyed 0..N-1."
            ) from exc
    if not isinstance(names, list) or not names:
        raise AuditInputError(
            f"{member} has no usable 'names' list. "
            "An unlabelled export cannot be audited."
        )
    if not all(isinstance(n, str) and n.strip() for n in names):
        raise AuditInputError(
            f"{member}: every entry in 'names' must be a non-empty string."
        )

    nc = parsed.get("nc", len(names))
    if not isinstance(nc, int) or isinstance(nc, bool):
        raise AuditInputError(f"{member}: 'nc' must be an integer, got {nc!r}.")
    if nc != len(names):
        raise AuditInputError(
            f"{member}: 'nc' is {nc} but 'names' lists {len(names)} classes. "
            "The metadata contradicts itself; class IDs cannot be trusted."
        )

    meta = parsed.get("roboflow") or {}
    if meta and not isinstance(meta, dict):
        raise AuditInputError(f"{member}: 'roboflow' block must be a mapping.")

    return {
        "names": list(names),
        "nc": nc,
        "member": member,
        "raw": parsed,
        "roboflow": meta if isinstance(meta, dict) else {},
        "train": parsed.get("train"),
        "val": parsed.get("val"),
        "test": parsed.get("test"),
    }


def discover_splits(
    archive: DatasetArchive, yaml_member: str
) -> dict[str, dict[str, list[str]]]:
    """Find every split that actually holds images, following YOLO conventions."""
    prefix = yaml_member.rsplit("/", 1)[0] + "/" if "/" in yaml_member else ""
    dirs: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"images": [], "labels": []}
    )

    for name in archive.names:
        if prefix and not name.startswith(prefix):
            continue
        parts = name[len(prefix):].split("/")
        if len(parts) < 3:
            continue
        split, kind = parts[0], parts[1]
        if kind not in ("images", "labels"):
            continue
        suffix = Path(parts[-1]).suffix.lower()
        if kind == "images" and suffix in IMAGE_SUFFIXES:
            dirs[split]["images"].append(name)
        elif kind == "labels" and suffix == ".txt":
            dirs[split]["labels"].append(name)

    splits = {s: v for s, v in dirs.items() if v["images"] or v["labels"]}
    if not splits:
        raise AuditInputError(
            "no <split>/images/ + <split>/labels/ folders were found in the archive.\n"
            "Expected a Roboflow YOLOv8 layout such as train/images/*.jpg. "
            "A COCO or VOC export cannot be audited by this tool."
        )
    for split in splits:
        splits[split]["images"].sort()
        splits[split]["labels"].sort()
    return splits


# --------------------------------------------------------------------------
# Image + annotation validation
# --------------------------------------------------------------------------


def audit_image(archive: DatasetArchive, member: str, split: str) -> ImageRecord:
    import cv2

    filename = member.rsplit("/", 1)[-1]
    record = ImageRecord(
        split=split,
        member=member,
        filename=filename,
        stem=normalise_stem(filename),
        pair_key=pair_key(filename),
    )
    try:
        with archive.open_stream(member) as handle:
            payload = handle.read()
        record.bytes = len(payload)
        record.sha256 = hashlib.sha256(payload).hexdigest()
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        del payload
        if image is None:
            record.ok = False
            record.problem = "decoder returned None (truncated or unsupported)"
            return record
        if image.ndim == 2:
            height, width = image.shape
            channels = 1
        else:
            height, width, channels = image.shape[0], image.shape[1], image.shape[2]
        record.height, record.width, record.channels = (
            int(height),
            int(width),
            int(channels),
        )
        if width <= 0 or height <= 0:
            record.ok = False
            record.problem = "zero-width or zero-height image"
            return record
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        record.dhash = dhash(gray)
    except AuditInputError:
        raise
    except Exception as exc:  # decoder crash on a hostile file
        record.ok = False
        record.problem = f"{type(exc).__name__}: {exc}"
    return record


def audit_label(
    archive: DatasetArchive,
    member: str,
    split: str,
    class_count: int,
) -> tuple[LabelRecord, list[BoxRecord], list[Issue]]:
    """Validate one YOLO label file against every structural rule we rely on."""
    filename = member.rsplit("/", 1)[-1]
    record = LabelRecord(
        split=split,
        member=member,
        filename=filename,
        stem=normalise_stem(filename),
        pair_key=pair_key(filename),
    )
    boxes: list[BoxRecord] = []
    issues: list[Issue] = []

    try:
        text = archive.read_bytes(member).decode("utf-8", errors="replace")
    except AuditInputError:
        raise
    except Exception as exc:
        record.exists = False
        issues.append(Issue("unreadable_label", split, filename, str(exc)))
        return record, boxes, issues

    seen_rows: set[tuple[Any, ...]] = set()
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        record.rows += 1
        fields = stripped.split()
        if len(fields) != 5:
            record.malformed_rows += 1
            kind = "polygon/segment row" if len(fields) > 5 else "incomplete row"
            issues.append(
                Issue(
                    "malformed_row",
                    split,
                    filename,
                    f"line {index}: {len(fields)} fields ({kind}); a YOLO detection "
                    f"row needs exactly 5 (class cx cy w h)",
                )
            )
            continue
        try:
            values = [float(v) for v in fields[1:]]
        except ValueError:
            record.malformed_rows += 1
            issues.append(
                Issue(
                    "malformed_row",
                    split,
                    filename,
                    f"line {index}: non-numeric value in {stripped!r}",
                )
            )
            continue
        try:
            class_id = int(fields[0])
        except ValueError:
            record.malformed_rows += 1
            issues.append(
                Issue(
                    "malformed_row",
                    split,
                    filename,
                    f"line {index}: class field {fields[0]!r} is not an integer",
                )
            )
            continue

        cx, cy, w, h = values
        if any(v < 0.0 for v in values):
            record.negative_coord_rows += 1
            issues.append(
                Issue(
                    "negative_coordinate",
                    split,
                    filename,
                    f"line {index}: negative coordinate in {stripped!r}",
                )
            )
        if any(v > 1.0 for v in values):
            record.out_of_range_rows += 1
            issues.append(
                Issue(
                    "out_of_range",
                    split,
                    filename,
                    f"line {index}: normalised value outside [0,1] in {stripped!r}",
                )
            )
        if w <= 0.0 or h <= 0.0:
            record.zero_area_rows += 1
            issues.append(
                Issue("zero_area_box", split, filename, f"line {index}: w={w} h={h}")
            )
        if (
            cx - w / 2 < -1e-6
            or cx + w / 2 > 1 + 1e-6
            or cy - h / 2 < -1e-6
            or cy + h / 2 > 1 + 1e-6
        ):
            issues.append(
                Issue(
                    "box_exceeds_frame",
                    split,
                    filename,
                    f"line {index}: box extends outside the image in {stripped!r}",
                    severity="warning",
                )
            )
        if class_id < 0 or class_id >= class_count:
            record.unknown_class_rows += 1
            issues.append(
                Issue(
                    "unknown_class_id",
                    split,
                    filename,
                    f"line {index}: class id {class_id} is outside 0..{class_count - 1}",
                )
            )

        key = (class_id, round(cx, 6), round(cy, 6), round(w, 6), round(h, 6))
        if key in seen_rows:
            record.duplicate_rows += 1
            issues.append(
                Issue(
                    "duplicate_row",
                    split,
                    filename,
                    f"line {index}: exact duplicate of an earlier row",
                    "warning",
                )
            )
        seen_rows.add(key)

        record.class_ids.append(class_id)
        record.instances += 1
        area = max(w, 0.0) * max(h, 0.0)
        boxes.append(
            BoxRecord(
                split=split,
                filename=filename,
                row=index,
                class_id=class_id,
                class_name="",  # filled by caller from data.yaml
                cx=cx,
                cy=cy,
                w=w,
                h=h,
                area_ratio=area,
                aspect=(w / h) if h > 0 else 0.0,
                small=area < MIN_BOX_AREA_RATIO,
            )
        )

    if record.rows == 0:
        record.empty = True
        issues.append(
            Issue(
                "empty_label_file",
                split,
                filename,
                "label file exists but has no rows",
                "warning",
            )
        )
    return record, boxes, issues


# --------------------------------------------------------------------------
# The audit itself
# --------------------------------------------------------------------------


def run_audit(
    zip_path: Path,
    out_dir: Path,
    artifacts_dir: Path | None,
    seed: int = DEFAULT_SEED,
    near_dup_bits: int = DEFAULT_NEAR_DUP_BITS,
    make_contact_sheets: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root or Path(__file__).resolve().parents[1]

    # Validate the source archive before touching it, so a bad path always
    # surfaces as a readable AuditInputError rather than a raw OSError.
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise AuditInputError(f"source archive not found: {zip_path}")
    if not zip_path.is_file():
        raise AuditInputError(f"source archive is not a file: {zip_path}")

    zip_sha256 = sha256_file(zip_path)
    zip_bytes = zip_path.stat().st_size

    with DatasetArchive(zip_path) as archive:
        yaml_member, readme_rf_member, readme_ds_member = locate_metadata(archive)
        meta = parse_data_yaml(
            archive.read_bytes(yaml_member).decode("utf-8", errors="replace"), yaml_member
        )
        class_names: list[str] = meta["names"]
        splits = discover_splits(archive, yaml_member)

        split_name_map: dict[str, str] = {}
        for raw_split in splits:
            lowered = raw_split.lower()
            if lowered in ("train", "training"):
                split_name_map[raw_split] = "train"
            elif lowered in ("valid", "val", "validation"):
                split_name_map[raw_split] = "valid"
            elif lowered in ("test", "testing"):
                split_name_map[raw_split] = "test"
            else:
                split_name_map[raw_split] = lowered
        unrecognised = sorted(
            {s for s in split_name_map.values() if s not in {"train", "valid", "test"}}
        )

        readme_text = ""
        if readme_rf_member:
            readme_text += archive.read_bytes(readme_rf_member).decode(
                "utf-8", errors="replace"
            )
        if readme_ds_member:
            readme_text += "\n" + archive.read_bytes(readme_ds_member).decode(
                "utf-8", errors="replace"
            )

        images: list[ImageRecord] = []
        labels: list[LabelRecord] = []
        boxes: list[BoxRecord] = []
        issues: list[Issue] = []
        missing_pairs: list[dict[str, str]] = []
        corrupt: list[ImageRecord] = []

        # --- per split, one image at a time -------------------------------
        for raw_split in sorted(splits):
            split = split_name_map[raw_split]
            label_by_stem: dict[str, str] = {}
            for member in splits[raw_split]["labels"]:
                label_by_stem.setdefault(pair_key(member.rsplit("/", 1)[-1]), member)

            for member in splits[raw_split]["images"]:
                record = audit_image(archive, member, split)
                images.append(record)
                if not record.ok:
                    corrupt.append(record)
                    issues.append(
                        Issue("corrupt_image", split, record.filename, record.problem)
                    )

                label_member = label_by_stem.pop(record.pair_key, None)
                if label_member is None:
                    missing_pairs.append(
                        {"split": split, "image": record.filename, "missing": "label"}
                    )
                    issues.append(
                        Issue(
                            "missing_label",
                            split,
                            record.filename,
                            "no matching .txt in labels/",
                        )
                    )
                    labels.append(
                        LabelRecord(
                            split=split,
                            member="",
                            filename=f"{record.pair_key}.txt",
                            stem=record.stem,
                            pair_key=record.pair_key,
                            exists=False,
                        )
                    )
                    continue

                label_record, label_boxes, label_issues = audit_label(
                    archive, label_member, split, len(class_names)
                )
                for box in label_boxes:
                    box.class_name = (
                        class_names[box.class_id]
                        if 0 <= box.class_id < len(class_names)
                        else "unknown"
                    )
                labels.append(label_record)
                boxes.extend(label_boxes)
                issues.extend(label_issues)

            for leftover_member in sorted(label_by_stem.values()):
                filename = leftover_member.rsplit("/", 1)[-1]
                missing_pairs.append(
                    {"split": split, "image": filename, "missing": "image"}
                )
                issues.append(
                    Issue(
                        "orphan_label",
                        split,
                        filename,
                        "label file with no matching image",
                    )
                )

        # --- exact duplicates ---------------------------------------------
        exact_duplicates: list[dict[str, Any]] = []
        by_sha: dict[str, list[ImageRecord]] = defaultdict(list)
        for record in images:
            if record.ok and record.sha256:
                by_sha[record.sha256].append(record)
        for digest, group in sorted(by_sha.items()):
            if len(group) > 1:
                ordered = sorted(group, key=lambda r: (r.split, r.filename))
                exact_duplicates.append(
                    {
                        "sha256": digest,
                        "count": len(ordered),
                        "splits": sorted({r.split for r in ordered}),
                        "files": ";".join(f"{r.split}/{r.filename}" for r in ordered),
                        "spans_splits": len({r.split for r in ordered}) > 1,
                    }
                )

        # --- near duplicates ----------------------------------------------
        near_duplicates: list[dict[str, Any]] = []
        hashable = [r for r in images if r.ok and r.dhash >= 0]
        # Bucket by the 16-bit prefix: two hashes within <= 10 bits necessarily
        # share it, turning the naive O(n^2) scan into a near-linear one.
        buckets: dict[int, list[ImageRecord]] = defaultdict(list)
        for record in hashable:
            buckets[record.dhash >> 48].append(record)
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    first, second = bucket[i], bucket[j]
                    distance = hamming(first.dhash, second.dhash)
                    if distance <= near_dup_bits:
                        near_duplicates.append(
                            {
                                "hamming_distance": distance,
                                "file_a": f"{first.split}/{first.filename}",
                                "file_b": f"{second.split}/{second.filename}",
                                "same_sha256": first.sha256 == second.sha256,
                                "crosses_split": first.split != second.split,
                                "stem_a": first.stem,
                                "stem_b": second.stem,
                            }
                        )
        near_duplicates.sort(
            key=lambda d: (d["hamming_distance"], d["file_a"], d["file_b"])
        )

        # --- leakage ------------------------------------------------------
        leakage_rows: list[dict[str, Any]] = []
        stem_splits: dict[str, set[str]] = defaultdict(set)
        stem_files: dict[str, list[str]] = defaultdict(list)
        for record in images:
            stem_splits[record.stem].add(record.split)
            stem_files[record.stem].append(f"{record.split}/{record.filename}")
        for stem in sorted(stem_splits):
            present = stem_splits[stem]
            if len(present) > 1:
                leakage_rows.append(
                    {
                        "kind": "source_stem_across_splits",
                        "key": stem,
                        "splits": "/".join(sorted(present)),
                        "file_count": len(stem_files[stem]),
                        "files": ";".join(sorted(stem_files[stem])),
                    }
                )

        group_splits: dict[str, set[str]] = defaultdict(set)
        group_files: dict[str, list[str]] = defaultdict(list)
        for record in images:
            key = frame_group_key(record.stem)
            group_splits[key].add(record.split)
            group_files[key].append(f"{record.split}/{record.filename}")
        for key in sorted(group_splits):
            present = group_splits[key]
            if len(present) <= 1:
                continue
            # Skip groups that are already fully explained by an exact stem
            # match, so the CSV does not report the same leak twice.
            if key in stem_splits and len(stem_splits[key]) > 1:
                continue
            leakage_rows.append(
                {
                    "kind": "adjacent_frame_group_across_splits",
                    "key": key,
                    "splits": "/".join(sorted(present)),
                    "file_count": len(group_files[key]),
                    "files": ";".join(sorted(group_files[key])),
                }
            )

        for entry in near_duplicates:
            if entry["crosses_split"]:
                leakage_rows.append(
                    {
                        "kind": "near_duplicate_across_splits",
                        "key": f"{entry['stem_a']}|{entry['stem_b']}",
                        "splits": "/".join(
                            sorted(
                                {
                                    entry["file_a"].split("/")[0],
                                    entry["file_b"].split("/")[0],
                                }
                            )
                        ),
                        "file_count": 2,
                        "files": f"{entry['file_a']};{entry['file_b']}",
                    }
                )

        # --- class audit (observation only: no inference) ------------------
        per_class: Counter[str] = Counter()
        per_class_split: dict[str, Counter[str]] = defaultdict(Counter)
        geometry: dict[str, list[BoxRecord]] = defaultdict(list)
        for box in boxes:
            per_class[box.class_name] += 1
            per_class_split[box.class_name][box.split] += 1
            geometry[box.class_name].append(box)

        class_rows: list[dict[str, Any]] = []
        for class_id, name in enumerate(class_names):
            entries = geometry.get(name, [])
            areas = [b.area_ratio for b in entries]
            aspects = [b.aspect for b in entries]
            class_rows.append(
                {
                    "class_id": class_id,
                    "class_name": name,
                    "in_required_scheme": name in REQUIRED_CLASSES,
                    "instances_total": per_class.get(name, 0),
                    "instances_train": per_class_split[name].get("train", 0),
                    "instances_valid": per_class_split[name].get("valid", 0),
                    "instances_test": per_class_split[name].get("test", 0),
                    "images_containing": len({(b.split, b.filename) for b in entries}),
                    "median_area_ratio": round(median(areas), 6) if areas else 0.0,
                    "median_aspect": round(median(aspects), 4) if aspects else 0.0,
                    "share_under_min_area": round(
                        sum(1 for b in entries if b.small) / len(entries), 4
                    )
                    if entries
                    else 0.0,
                    "reaches_target_instances": per_class.get(name, 0)
                    >= TARGET_MIN_INSTANCES_PER_CLASS,
                }
            )

        # --- manual review queue ------------------------------------------
        # Deterministically chosen. Every entry states a *question* for a human;
        # the tool never proposes a replacement label.
        candidates: list[dict[str, Any]] = []
        for box in sorted(boxes, key=lambda b: (b.class_name, b.split, b.filename, b.row)):
            if box.class_name == "glasses":
                reason = "verify_box_unit"
                question = (
                    "Does this box enclose a visible head/face region, or an "
                    "object/product? Plan section 5.3 requires the head/face unit."
                )
            elif box.small:
                reason = "box_below_min_area"
                question = "Is the head still large enough to judge goggle compliance?"
            else:
                continue
            candidates.append(
                {
                    "reason": reason,
                    "split": box.split,
                    "filename": box.filename,
                    "class_id": box.class_id,
                    "class_name": box.class_name,
                    "row": box.row,
                    "area_ratio": round(box.area_ratio, 6),
                    "aspect": round(box.aspect, 4),
                    "question_for_human": question,
                    "human_decision": "",
                }
            )
        if len(candidates) > MAX_REVIEW_ROWS:
            rng = random.Random(seed)
            candidates = rng.sample(candidates, MAX_REVIEW_ROWS)
            candidates.sort(
                key=lambda d: (
                    d["reason"],
                    d["class_name"],
                    d["split"],
                    d["filename"],
                    d["row"],
                )
            )

        # --- summarise -----------------------------------------------------
        split_summary: dict[str, Any] = {}
        all_splits = sorted({r.split for r in images} | {l.split for l in labels})
        for split in all_splits:
            split_images = [r for r in images if r.split == split]
            split_labels = [l for l in labels if l.split == split]
            split_summary[split] = {
                "images": len(split_images),
                "images_ok": sum(1 for r in split_images if r.ok),
                "labels": sum(1 for l in split_labels if l.exists),
                "label_files_empty": sum(1 for l in split_labels if l.empty),
                "instances": sum(1 for b in boxes if b.split == split),
                "malformed_rows": sum(l.malformed_rows for l in split_labels),
                "missing_labels": sum(
                    1
                    for p in missing_pairs
                    if p["split"] == split and p["missing"] == "label"
                ),
                "orphan_labels": sum(
                    1
                    for p in missing_pairs
                    if p["split"] == split and p["missing"] == "image"
                ),
            }

        dimensions = Counter((r.width, r.height) for r in images if r.ok)
        test_images = split_summary.get("test", {}).get("images", 0)
        test_share = (test_images / len(images)) if images else 0.0
        malformed_total = sum(l.malformed_rows for l in labels)

        blocking: list[str] = []
        warnings: list[str] = []
        missing_classes = [c for c in REQUIRED_CLASSES if c not in class_names]

        if missing_classes:
            blocking.append(
                "required class(es) absent from data.yaml: " + ", ".join(missing_classes)
            )
        if len(class_names) != 2:
            blocking.append(
                f"data.yaml declares {len(class_names)} classes; the project scheme "
                "is exactly 2 (goggles / no_goggles)"
            )
        if unrecognised:
            blocking.append(
                "unrecognised split folder(s): " + ", ".join(unrecognised)
            )
        if leakage_rows:
            blocking.append(f"{len(leakage_rows)} split-leakage group(s) present")
        if images and test_share < TARGET_TEST_SHARE:
            blocking.append(
                f"test split holds {test_share:.2%} of images; plan section 5.5 "
                f"requires about {TARGET_TEST_SHARE:.0%}"
            )
        if malformed_total:
            blocking.append(f"{malformed_total} malformed annotation row(s)")
        if corrupt:
            blocking.append(f"{len(corrupt)} corrupt/unreadable image(s)")
        if len(images) < TARGET_MIN_IMAGES:
            warnings.append(
                f"{len(images)} images is below the plan section 5.4 target of "
                f"{TARGET_MIN_IMAGES}-{TARGET_MAX_IMAGES}"
            )
        for class_name in class_names:
            if per_class.get(class_name, 0) < TARGET_MIN_INSTANCES_PER_CLASS:
                warnings.append(
                    f"class '{class_name}' has {per_class.get(class_name, 0)} "
                    f"instances; target is at least {TARGET_MIN_INSTANCES_PER_CLASS}"
                )

        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        summary = {
            "record_type": "dataset_summary",
            "phase": PHASE,
            "generated_at_utc": started,
            "source": {
                "zip_filename": zip_path.name,
                "zip_sha256": zip_sha256,
                "zip_size_bytes": zip_bytes,
                "archive_member_count": len(archive.names),
                "duplicate_archive_members": archive.duplicate_members,
                "data_yaml_member": yaml_member,
                "readme_roboflow_member": readme_rf_member,
                "readme_dataset_member": readme_ds_member,
            },
            "metadata": {
                "class_names": class_names,
                "class_count": len(class_names),
                "roboflow": meta["roboflow"],
                "declared_train": meta["train"],
                "declared_val": meta["val"],
                "declared_test": meta["test"],
                "readme_first_line": readme_text.splitlines()[0].strip()
                if readme_text.strip()
                else "",
            },
            "totals": {
                "images": len(images),
                "images_ok": sum(1 for r in images if r.ok),
                "label_files": sum(1 for l in labels if l.exists),
                "instances": len(boxes),
                "empty_label_files": sum(1 for l in labels if l.empty),
                "malformed_rows": malformed_total,
                "leakage_groups": len(leakage_rows),
                "exact_duplicate_groups": len(exact_duplicates),
                "near_duplicate_pairs": len(near_duplicates),
                "manual_review_rows": len(candidates),
            },
            "splits": split_summary,
            "split_shares": {
                split: round(info["images"] / len(images), 4) if images else 0.0
                for split, info in split_summary.items()
            },
            "class_audit": class_rows,
            "required_classes": list(REQUIRED_CLASSES),
            "missing_required_classes": missing_classes,
            "has_no_goggles_class": "no_goggles" in class_names,
            "compliance_mapping": {
                "decision": "unmapped_pending_human_review",
                "note": (
                    "No source class maps to the project's safe/violation decision. "
                    "The tool deliberately does not propose one: that requires human "
                    "verification of individual images (plan.md sections 5.2-5.3)."
                ),
                "per_class": {name: "unmapped" for name in class_names},
            },
            "duplicates": {
                "exact_hash": "sha256",
                "near_hash": "dhash64_row",
                "near_threshold_bits": near_dup_bits,
                "hash_side": HASH_SIDE,
                "exact_groups": len(exact_duplicates),
                "near_pairs": len(near_duplicates),
            },
            "leakage": {
                "group_count": len(leakage_rows),
                "kinds": sorted({row["kind"] for row in leakage_rows}),
                "action_taken": "none - reported only, splits were not modified",
            },
            "image_dimensions": [
                {"width": w, "height": h, "count": c}
                for (w, h), c in sorted(dimensions.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "parameters": {
                "seed": seed,
                "near_dup_bits": near_dup_bits,
                "min_box_area_ratio": MIN_BOX_AREA_RATIO,
                "target_test_share": TARGET_TEST_SHARE,
                "target_min_images": TARGET_MIN_IMAGES,
                "target_max_images": TARGET_MAX_IMAGES,
                "target_min_instances_per_class": TARGET_MIN_INSTANCES_PER_CLASS,
            },
            "blocking_findings": blocking,
            "warnings": warnings,
            "verdict": "usable_as_starting_source" if not blocking else "rejected_for_training",
            "suitability": {
                "as_starting_source": "not_usable" if missing_classes else "partial",
                "for_two_class_scheme": "not_usable",
                "for_annotation_unit_plan_section_5_3": "not_usable",
                "reason": (
                    "Source classes and annotation unit do not express the required "
                    "goggles / no_goggles decision over a visible head region."
                ),
            },
        }

        # --- write outputs -------------------------------------------------
        out_dir.mkdir(parents=True, exist_ok=True)
        label_lookup = {(l.split, l.pair_key): l for l in labels}
        write_json(out_dir / "dataset_summary.json", summary)
        write_source_manifest(out_dir / "source_manifest.csv", images, label_lookup)
        write_class_counts(out_dir / "class_counts.csv", class_rows, split_summary)
        write_image_statistics(out_dir / "image_statistics.csv", images)
        write_annotation_statistics(out_dir / "annotation_statistics.csv", labels)
        write_issues(out_dir / "annotation_issues.csv", issues)
        write_missing_pairs(out_dir / "missing_pairs.csv", missing_pairs)
        write_corrupt(out_dir / "corrupt_images.csv", corrupt)
        write_exact_duplicates(out_dir / "exact_duplicates.csv", exact_duplicates)
        write_near_duplicates(out_dir / "near_duplicates.csv", near_duplicates)
        write_leakage(out_dir / "split_leakage.csv", leakage_rows)
        write_review_queue(out_dir / "manual_review_queue.csv", candidates)

        sheets_written: list[str] = []
        if make_contact_sheets and artifacts_dir is not None:
            sheets_written = write_contact_sheets(
                archive, artifacts_dir, images, boxes, class_names, seed
            )

        run_metadata = {
            "record_type": "audit_run_metadata",
            "phase": PHASE,
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": "tools/inspect_dataset.py",
            "tool_version": "1.0.0",
            "arguments": {
                "zip": relpath(zip_path, repo_root),
                "out_dir": relpath(out_dir, repo_root),
                "artifacts_dir": relpath(artifacts_dir, repo_root)
                if artifacts_dir
                else None,
                "seed": seed,
                "near_dup_bits": near_dup_bits,
                "make_contact_sheets": make_contact_sheets,
            },
            "environment": {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "numpy_version": np.__version__,
                "opencv_version": _safe_version("cv2"),
                "pyyaml_version": _safe_version("yaml"),
            },
            "source_zip": {
                "filename": zip_path.name,
                "sha256": zip_sha256,
                "size_bytes": zip_bytes,
            },
            "determinism": {
                "seed": seed,
                "note": (
                    "All CSV/JSON outputs are sorted deterministically; only the "
                    "timestamps in this file differ between runs."
                ),
            },
            "outputs": sorted(
                [
                    "annotation_issues.csv",
                    "annotation_statistics.csv",
                    "class_counts.csv",
                    "corrupt_images.csv",
                    "dataset_summary.json",
                    "exact_duplicates.csv",
                    "image_statistics.csv",
                    "manual_review_queue.csv",
                    "missing_pairs.csv",
                    "near_duplicates.csv",
                    "source_manifest.csv",
                    "split_leakage.csv",
                ]
            ),
            "contact_sheets": [Path(p).name for p in sheets_written],
            "exit_code": 3 if blocking else 0,
        }
        write_json(out_dir / "audit_run_metadata.json", run_metadata)

        return {
            "summary": summary,
            "run_metadata": run_metadata,
            "issues": issues,
            "blocking": blocking,
            "warnings": warnings,
            "out_dir": out_dir,
            "artifacts_dir": artifacts_dir,
            "contact_sheets": sheets_written,
        }


def _safe_version(module_name: str) -> str:
    try:
        module = __import__(module_name)
        return str(getattr(module, "__version__", "unknown"))
    except Exception:  # pragma: no cover
        return "unavailable"


# --------------------------------------------------------------------------
# Writers. Every writer sorts its input, so runs are reproducible.
# --------------------------------------------------------------------------


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def write_source_manifest(
    path: Path,
    images: Sequence[ImageRecord],
    labels: dict[tuple[str, str], LabelRecord],
) -> None:
    header = [
        "split", "filename", "normalised_stem", "frame_group_key", "image_member",
        "label_member", "label_present", "image_sha256", "dhash64", "width",
        "height", "channels", "instances", "image_ok",
    ]
    rows = []
    for record in sorted(images, key=lambda r: (r.split, r.filename)):
        label = labels.get((record.split, record.pair_key))
        rows.append(
            [
                record.split,
                record.filename,
                record.stem,
                frame_group_key(record.stem),
                record.member,
                label.member if label else "",
                bool(label and label.exists),
                record.sha256,
                f"{record.dhash:016x}" if record.dhash >= 0 else "",
                record.width,
                record.height,
                record.channels,
                label.instances if label else 0,
                record.ok,
            ]
        )
    write_csv(path, header, rows)


def write_class_counts(
    path: Path,
    class_rows: Sequence[dict[str, Any]],
    split_summary: dict[str, Any],
) -> None:
    header = [
        "class_id", "class_name", "in_required_scheme", "instances_total",
        "instances_train", "instances_valid", "instances_test", "images_containing",
        "median_area_ratio", "median_aspect", "share_under_min_area",
        "reaches_target_instances", "compliance_decision",
    ]
    rows = [
        [
            row["class_id"], row["class_name"], row["in_required_scheme"],
            row["instances_total"], row["instances_train"], row["instances_valid"],
            row["instances_test"], row["images_containing"], row["median_area_ratio"],
            row["median_aspect"], row["share_under_min_area"],
            row["reaches_target_instances"], "unmapped_pending_human_review",
        ]
        for row in class_rows
    ]
    rows.append(
        [
            "", "__SPLIT_TOTALS__", "",
            sum(i["instances"] for i in split_summary.values()),
            split_summary.get("train", {}).get("instances", 0),
            split_summary.get("valid", {}).get("instances", 0),
            split_summary.get("test", {}).get("instances", 0),
            "", "", "", "", "", "",
        ]
    )
    write_csv(path, header, rows)


def write_image_statistics(path: Path, images: Sequence[ImageRecord]) -> None:
    header = [
        "split", "filename", "width", "height", "megapixels", "channels",
        "bytes", "sha256", "dhash64", "readable", "problem",
    ]
    rows = [
        [
            r.split, r.filename, r.width, r.height,
            round((r.width * r.height) / 1e6, 6), r.channels, r.bytes, r.sha256,
            f"{r.dhash:016x}" if r.dhash >= 0 else "", r.ok, r.problem,
        ]
        for r in sorted(images, key=lambda r: (r.split, r.filename))
    ]
    write_csv(path, header, rows)


def write_annotation_statistics(path: Path, labels: Sequence[LabelRecord]) -> None:
    header = [
        "split", "label_filename", "exists", "rows", "instances", "malformed_rows",
        "out_of_range_rows", "negative_coord_rows", "zero_area_rows",
        "duplicate_rows", "unknown_class_rows", "empty", "class_id_histogram",
    ]
    rows = []
    for record in sorted(labels, key=lambda r: (r.split, r.filename)):
        histogram = Counter(record.class_ids)
        rows.append(
            [
                record.split, record.filename, record.exists, record.rows,
                record.instances, record.malformed_rows, record.out_of_range_rows,
                record.negative_coord_rows, record.zero_area_rows,
                record.duplicate_rows, record.unknown_class_rows, record.empty,
                ";".join(f"{k}:{v}" for k, v in sorted(histogram.items())),
            ]
        )
    write_csv(path, header, rows)


def write_issues(path: Path, issues: Sequence[Issue]) -> None:
    header = ["severity", "category", "split", "filename", "detail"]
    order = {"error": 0, "warning": 1, "info": 2}
    rows = [
        [i.severity, i.category, i.split, i.filename, i.detail]
        for i in sorted(
            issues,
            key=lambda i: (
                order.get(i.severity, 9), i.category, i.split, i.filename, i.detail
            ),
        )
    ]
    write_csv(path, header, rows)


def write_missing_pairs(path: Path, pairs: Sequence[dict[str, str]]) -> None:
    header = ["split", "file", "missing_side"]
    rows = [
        [p["split"], p["image"], p["missing"]]
        for p in sorted(pairs, key=lambda p: (p["split"], p["image"], p["missing"]))
    ]
    write_csv(path, header, rows)


def write_corrupt(path: Path, corrupt: Sequence[ImageRecord]) -> None:
    header = ["split", "filename", "member", "bytes", "problem"]
    rows = [
        [r.split, r.filename, r.member, r.bytes, r.problem]
        for r in sorted(corrupt, key=lambda r: (r.split, r.filename))
    ]
    write_csv(path, header, rows)


def write_exact_duplicates(path: Path, groups: Sequence[dict[str, Any]]) -> None:
    header = ["sha256", "count", "splits", "spans_splits", "files"]
    rows = [
        [g["sha256"], g["count"], "/".join(g["splits"]), g["spans_splits"], g["files"]]
        for g in groups
    ]
    write_csv(path, header, rows)


def write_near_duplicates(path: Path, pairs: Sequence[dict[str, Any]]) -> None:
    header = [
        "hamming_distance", "same_sha256", "crosses_split", "file_a", "file_b",
        "stem_a", "stem_b",
    ]
    rows = [
        [
            p["hamming_distance"], p["same_sha256"], p["crosses_split"],
            p["file_a"], p["file_b"], p["stem_a"], p["stem_b"],
        ]
        for p in pairs
    ]
    write_csv(path, header, rows)


def write_leakage(path: Path, rows_in: Sequence[dict[str, Any]]) -> None:
    header = ["kind", "key", "splits", "file_count", "files"]
    rows = [
        [r["kind"], r["key"], r["splits"], r["file_count"], r["files"]]
        for r in sorted(rows_in, key=lambda r: (r["kind"], r["key"]))
    ]
    write_csv(path, header, rows)


def write_review_queue(path: Path, candidates: Sequence[dict[str, Any]]) -> None:
    header = [
        "reason", "split", "filename", "class_id", "class_name", "row",
        "area_ratio", "aspect", "question_for_human", "human_decision",
    ]
    rows = [
        [
            c["reason"], c["split"], c["filename"], c["class_id"], c["class_name"],
            c["row"], c["area_ratio"], c["aspect"], c["question_for_human"],
            c["human_decision"],
        ]
        for c in candidates
    ]
    write_csv(path, header, rows)


# --------------------------------------------------------------------------
# Contact sheets: geometry only, never a compliance label
# --------------------------------------------------------------------------


def write_contact_sheets(
    archive: DatasetArchive,
    artifacts_dir: Path,
    images: Sequence[ImageRecord],
    boxes: Sequence[BoxRecord],
    class_names: Sequence[str],
    seed: int,
    tiles: int = 12,
    columns: int = 3,
) -> list[str]:
    """Render sampled images with their boxes drawn.

    Each tile is annotated with filename, split, class name and class ID. No
    safe/unsafe word appears anywhere: compliance is a human decision, not
    something this tool may imply.
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return []

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    boxes_by_key: dict[tuple[str, str], list[BoxRecord]] = defaultdict(list)
    for box in boxes:
        boxes_by_key[(box.split, box.filename)].append(box)

    by_split: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in images:
        if record.ok:
            by_split[record.split].append(record)
    if not by_split:
        return []

    written: list[str] = []
    tile_side = 320
    for split in sorted(by_split):
        pool = sorted(by_split[split], key=lambda r: r.filename)
        rng = random.Random(f"{seed}:{split}")
        picks = pool if len(pool) <= tiles else rng.sample(pool, tiles)
        picks.sort(key=lambda r: r.filename)

        rows_needed = max(1, math.ceil(len(picks) / columns))
        canvas = np.full(
            (rows_needed * tile_side, columns * tile_side, 3), 32, dtype=np.uint8
        )

        for index, record in enumerate(picks):
            try:
                image = cv2.imdecode(
                    np.frombuffer(archive.read_bytes(record.member), dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
            except Exception:
                image = None
            if image is None:
                continue
            height, width = image.shape[:2]
            scale = min(tile_side / width, tile_side / height)
            resized = cv2.resize(
                image,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            for box in boxes_by_key.get((record.split, record.filename), []):
                x1 = int((box.cx - box.w / 2) * resized.shape[1])
                y1 = int((box.cy - box.h / 2) * resized.shape[0])
                x2 = int((box.cx + box.w / 2) * resized.shape[1])
                y2 = int((box.cy + box.h / 2) * resized.shape[0])
                cv2.rectangle(resized, (x1, y1), (x2, y2), (0, 255, 0), 2)
            row, column = divmod(index, columns)
            top, left = row * tile_side, column * tile_side
            canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized

            caption = f"class={class_names[0]} id=0" if class_names else "no classes"
            entries = boxes_by_key.get((record.split, record.filename))
            if entries:
                first = min(entries, key=lambda b: b.row)
                caption = f"class={first.class_name} id={first.class_id}"
            cv2.putText(
                canvas, record.filename[:34], (left + 4, top + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1,
            )
            cv2.putText(
                canvas, f"split={record.split} {caption}", (left + 4, top + 32),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 255), 1,
            )

        destination = artifacts_dir / f"contact_sheet_{split}.png"
        if cv2.imwrite(str(destination), canvas):
            written.append(destination.as_posix())
    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only audit of the source Roboflow YOLOv8 export (Phase 1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "This tool never relabels, remaps classes, augments, trains, or writes\n"
            "inside data/raw/. It reports facts and a human-review queue."
        ),
    )
    parser.add_argument(
        "--zip",
        dest="zip_path",
        default="data/raw/roboflow_safety_glasses_original.zip",
        help="source archive (opened read-only, never modified)",
    )
    parser.add_argument(
        "--out",
        dest="out_dir",
        default="data/reviewed/audit",
        help="directory for the committed json/csv records",
    )
    parser.add_argument(
        "--artifacts",
        dest="artifacts_dir",
        default="data/reviewed/audit/artifacts",
        help="git-ignored directory for rendered contact sheets",
    )
    parser.add_argument(
        "--seed",
        dest="seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"deterministic seed for sampling (default {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--near-dup-bits",
        dest="near_dup_bits",
        type=int,
        default=DEFAULT_NEAR_DUP_BITS,
        help=f"dHash Hamming distance treated as near-duplicate "
        f"(default {DEFAULT_NEAR_DUP_BITS})",
    )
    parser.add_argument(
        "--no-contact-sheets",
        dest="contact_sheets",
        action="store_false",
        help="skip rendering PNG contact sheets",
    )
    parser.add_argument(
        "--strict-fail",
        dest="strict_fail",
        action="store_true",
        help="exit non-zero when blocking findings exist (used by CI)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (repo_root / path)

    zip_path = resolve(args.zip_path)
    out_dir = resolve(args.out_dir)
    artifacts_dir = resolve(args.artifacts_dir) if args.contact_sheets else None

    if not 0 <= args.near_dup_bits <= 64:
        print(
            f"ERROR: --near-dup-bits must be within 0..64, got {args.near_dup_bits}.",
            file=sys.stderr,
        )
        return 2
    if artifacts_dir is not None and _is_inside(zip_path.parent, artifacts_dir):
        print(
            "ERROR: --artifacts must not be inside data/raw/. Extracted imagery is "
            "never written next to the immutable source archive.",
            file=sys.stderr,
        )
        return 2

    try:
        result = run_audit(
            zip_path=zip_path,
            out_dir=out_dir,
            artifacts_dir=artifacts_dir,
            seed=args.seed,
            near_dup_bits=args.near_dup_bits,
            make_contact_sheets=args.contact_sheets,
            repo_root=repo_root,
        )
    except AuditInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("Interrupted.", file=sys.stderr)
        return 130

    summary = result["summary"]
    totals = summary["totals"]
    blocking = result["blocking"]
    print("Dataset audit complete.")
    print(f"  source ZIP        : {summary['source']['zip_filename']}")
    print(f"  source SHA-256    : {summary['source']['zip_sha256']}")
    print(f"  images            : {totals['images']} ({totals['images_ok']} readable)")
    print(f"  label files       : {totals['label_files']}")
    print(f"  instances         : {totals['instances']}")
    print(
        f"  classes in yaml   : {summary['metadata']['class_count']} -> "
        f"{', '.join(summary['metadata']['class_names'])}"
    )
    print(
        f"  missing required  : "
        f"{', '.join(summary['missing_required_classes']) or 'none'}"
    )
    print(f"  malformed rows    : {totals['malformed_rows']}")
    print(f"  exact dups        : {totals['exact_duplicate_groups']} group(s)")
    print(
        f"  near-dups         : {totals['near_duplicate_pairs']} pair(s) "
        f"at <= {args.near_dup_bits} bits"
    )
    print(f"  leakage groups    : {totals['leakage_groups']}")
    print(f"  manual review row : {totals['manual_review_rows']}")
    print(f"  verdict           : {summary['verdict']}")
    if blocking:
        print("  blocking findings :")
        for finding in blocking:
            print(f"    - {finding}")
    print(f"  records written   : {relpath(out_dir, repo_root)}/")
    if args.contact_sheets and artifacts_dir is not None:
        print(
            f"  contact sheets    : {relpath(artifacts_dir, repo_root)}/ (git-ignored)"
        )

    if blocking and args.strict_fail:
        return 3
    return 0


def _is_inside(parent: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
