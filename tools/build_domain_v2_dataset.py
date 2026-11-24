"""Build the V2 domain-adaptation dataset.

Copies the existing clean 240-image baseline, then adds only the 60 newly
captured webcam images from the Roboflow V2 ZIP.

Source mapping:
    0 eyes_with_goggles    -> 0 goggles
    1 eyes_without_goggles -> 1 no_goggles

Classes 2 and 3 are discarded.
"""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import yaml


PROJECT = Path(__file__).resolve().parents[1]

BASELINE = PROJECT / "data/processed/eye_shield_baseline"
SOURCE_ZIP = PROJECT / "data/raw/eye_region_domain_v2_original.zip"
OUTPUT = PROJECT / "data/processed/eye_shield_domain_v2"

SPLIT_MAP = {
    "train": "train",
    "valid": "val",
    "test": "test",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def is_webcam_image(filename: str) -> bool:
    return (
        filename.startswith("goggles_webcam_")
        or filename.startswith("no_goggles_webcam_")
    )


def label_member_for_image(image_member: str) -> str:
    image_path = Path(image_member)

    parts = list(image_path.parts)
    parts[1] = "labels"

    return str(Path(*parts).with_suffix(".txt"))


def transform_label(text: str, source_name: str) -> tuple[str, Counter]:
    output_rows: list[str] = []
    counts: Counter = Counter()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        fields = line.split()

        if len(fields) != 5:
            raise ValueError(
                f"{source_name}:{line_number}: expected 5 fields, "
                f"received {len(fields)}"
            )

        try:
            source_class = int(fields[0])
            coordinates = [float(value) for value in fields[1:]]
        except ValueError as error:
            raise ValueError(
                f"{source_name}:{line_number}: invalid numeric value"
            ) from error

        if source_class not in (0, 1, 2, 3):
            raise ValueError(
                f"{source_name}:{line_number}: unexpected class "
                f"{source_class}"
            )

        # Drop head and head_with_helmet.
        if source_class in (2, 3):
            continue

        for value in coordinates:
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{source_name}:{line_number}: coordinate outside 0..1"
                )

        if coordinates[2] <= 0.0 or coordinates[3] <= 0.0:
            raise ValueError(
                f"{source_name}:{line_number}: non-positive box size"
            )

        final_class = source_class

        output_rows.append(
            f"{final_class} "
            + " ".join(f"{value:.6f}" for value in coordinates)
        )

        counts[final_class] += 1

    return "\n".join(output_rows) + ("\n" if output_rows else ""), counts


def count_dataset(root: Path) -> dict:
    summary: dict[str, dict] = {}

    for split in ("train", "val", "test"):
        images = list((root / split / "images").glob("*"))
        labels = list((root / split / "labels").glob("*.txt"))

        class_counts = Counter()

        for label_path in labels:
            for line in label_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    class_counts[int(line.split()[0])] += 1

        summary[split] = {
            "images": len(images),
            "labels": len(labels),
            "goggles_boxes": class_counts[0],
            "no_goggles_boxes": class_counts[1],
        }

    return summary


def main() -> int:
    if not BASELINE.is_dir():
        raise SystemExit(f"ERROR: baseline dataset missing: {BASELINE}")

    if not SOURCE_ZIP.is_file():
        raise SystemExit(f"ERROR: V2 ZIP missing: {SOURCE_ZIP}")

    source_hash_before = sha256(SOURCE_ZIP)

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)

    shutil.copytree(
        BASELINE,
        OUTPUT,
        ignore=shutil.ignore_patterns(
            "artifacts",
            "*.csv",
            "*.json",
            "attribution.md",
        ),
    )

    # Ensure output split structure exists.
    for split in ("train", "val", "test"):
        (OUTPUT / split / "images").mkdir(parents=True, exist_ok=True)
        (OUTPUT / split / "labels").mkdir(parents=True, exist_ok=True)

    copied_images = Counter()
    copied_boxes = Counter()
    skipped_empty = 0

    with ZipFile(SOURCE_ZIP) as archive:
        members = set(archive.namelist())

        image_members = sorted(
            name
            for name in members
            if "/images/" in name
            and Path(name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            and is_webcam_image(Path(name).name)
        )

        for image_member in image_members:
            source_split = image_member.split("/", 1)[0]

            if source_split not in SPLIT_MAP:
                continue

            final_split = SPLIT_MAP[source_split]
            label_member = label_member_for_image(image_member)

            if label_member not in members:
                raise ValueError(
                    f"Missing label for webcam image: {image_member}"
                )

            original_label = archive.read(label_member).decode("utf-8")
            transformed_label, box_counts = transform_label(
                original_label,
                label_member,
            )

            # Reject any image that has no class 0/1 box after filtering.
            if not transformed_label.strip():
                skipped_empty += 1
                continue

            image_name = Path(image_member).name
            label_name = Path(label_member).name

            image_destination = (
                OUTPUT / final_split / "images" / image_name
            )
            label_destination = (
                OUTPUT / final_split / "labels" / label_name
            )

            if image_destination.exists() or label_destination.exists():
                raise ValueError(
                    f"Unexpected filename collision: {image_name}"
                )

            image_destination.write_bytes(archive.read(image_member))
            label_destination.write_text(
                transformed_label,
                encoding="utf-8",
            )

            copied_images[final_split] += 1
            copied_boxes.update(box_counts)

    data_yaml = {
        "path": str(OUTPUT.resolve()),
        "train": "train/images",
        "val": "val/images",
        "test": "test/images",
        "names": {
            0: "goggles",
            1: "no_goggles",
        },
    }

    (OUTPUT / "data.yaml").write_text(
        yaml.safe_dump(data_yaml, sort_keys=False),
        encoding="utf-8",
    )

    source_hash_after = sha256(SOURCE_ZIP)

    if source_hash_before != source_hash_after:
        raise RuntimeError("ERROR: source ZIP changed during processing")

    summary = count_dataset(OUTPUT)

    print("=" * 68)
    print("V2 domain-adaptation dataset created")
    print("=" * 68)
    print(f"Source ZIP SHA-256: {source_hash_before}")
    print(f"Output: {OUTPUT}")
    print()
    print("New webcam images added:")
    for split in ("train", "val", "test"):
        print(f"  {split}: {copied_images[split]}")
    print(f"  skipped with no relevant boxes: {skipped_empty}")
    print()
    print("Final V2 dataset:")
    for split, values in summary.items():
        print(
            f"  {split}: "
            f"images={values['images']}, "
            f"labels={values['labels']}, "
            f"goggles_boxes={values['goggles_boxes']}, "
            f"no_goggles_boxes={values['no_goggles_boxes']}"
        )

    total_images = sum(item["images"] for item in summary.values())
    print(f"\nTotal images: {total_images}")

    if copied_images["train"] != 42:
        print(
            "WARNING: expected 42 new train webcam images, got "
            f"{copied_images['train']}"
        )

    if copied_images["val"] != 12:
        print(
            "WARNING: expected 12 new val webcam images, got "
            f"{copied_images['val']}"
        )

    if copied_images["test"] != 6:
        print(
            "WARNING: expected 6 new test webcam images, got "
            f"{copied_images['test']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
