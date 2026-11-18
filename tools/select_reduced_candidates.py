"""Build the reduced human-review candidate queue for Construction-PPE.

Phase 2B, Part 2. The full screen (``tools/screen_construction_ppe.py``)
produced 1,098 relevant boxes. Asking a human to adjudicate all of them is
wasteful, so this tool narrows the pool to the images actually worth a look.

What it does *not* do, deliberately:

* it does not build a training dataset, remap classes, or create a split;
* it does not decide pose, occlusion, or rear-facing status.

Pose and occlusion are semantic judgements. This tool cannot see images, so it
applies only measurements it can defend -- box size, box area, border clipping,
and a size-normalised sharpness score -- and it labels every retained row with
whatever it could not determine, so the human knows what to check.

Selection rules (from the task specification):

``goggles``
    minimum side >= 48 px, no duplicate/leakage group member, no border-clipped
    box, then the best 100 by a documented composite rank.

``no_goggle``
    minimum side >= 48 px, no duplicate/leakage group member; keep everything
    that survives (expected maximum ~30).

Outputs
-------
``reduced_candidate_queue.csv``
    filename, source_split, source_class, minimum_side_px, box_area_ratio,
    duplicate_group, recommended_action, human_decision, notes

``artifacts/reduced_goggles_candidates.png``
``artifacts/reduced_no_goggle_candidates.png``
    Concise contact sheets for the retained rows.
"""

from __future__ import annotations

import csv
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import screen_construction_ppe as screen  # noqa: E402

ZIP_PATH = ROOT / "data" / "raw" / "construction_ppe_original.zip"
OUT_DIR = ROOT / "data" / "reviewed" / "construction_ppe_screen"
ARTIFACT_DIR = OUT_DIR / "artifacts"

# -- selection thresholds ------------------------------------------------
MIN_SIDE_PX = 48.0          # plan/provisional screening rule for a usable box
BORDER_FRACTION = 0.01      # a box this close to the edge is treated as clipped
GOGGLES_LIMIT = 100         # provisional queue cap for the strong class
SHARPNESS_TILE = 160        # crops are resized here before scoring

# A crop scoring below the pool's 10th percentile is flagged for a blur check.
SHARPNESS_FLAG_PERCENTILE = 10.0

QUEUE_COLUMNS = [
    "filename",
    "source_split",
    "source_class",
    "minimum_side_px",
    "box_area_ratio",
    "duplicate_group",
    "recommended_action",
    "human_decision",
    "notes",
]


class SelectionError(Exception):
    """Raised when the archive cannot be screened as expected."""


# --------------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------------


def sharpness(archive: "screen.SourceArchive", record: "screen.ImageRecord", box: "screen.Box") -> float | None:
    """Variance of Laplacian on the crop, resized to a fixed tile first.

    Resizing before scoring matters: Laplacian variance scales with content
    size, so scoring crops at native resolution would rank large boxes as sharp
    purely for being large. A fixed tile removes that bias and leaves a
    comparable blur signal.
    """
    import cv2
    import numpy as np

    payload = archive.read_bytes(record.image_member)
    if not payload:
        return None
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None

    width, height = record.image_width_px, record.image_height_px
    x1 = int(round((box.cx - box.width / 2) * width))
    x2 = int(round((box.cx + box.width / 2) * width))
    y1 = int(round((box.cy - box.height / 2) * height))
    y2 = int(round((box.cy + box.height / 2) * height))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None

    crop = cv2.resize(
        image[y1:y2, x1:x2],
        (SHARPNESS_TILE, SHARPNESS_TILE),
        interpolation=cv2.INTER_AREA,
    )
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def border_clipped(record: "screen.ImageRecord", box: "screen.Box") -> bool:
    """True when any box edge sits within ``BORDER_FRACTION`` of the frame edge."""
    width, height = record.image_width_px, record.image_height_px
    left = (box.cx - box.width / 2) * width
    right = (box.cx + box.width / 2) * width
    top = (box.cy - box.height / 2) * height
    bottom = (box.cy + box.height / 2) * height
    return (
        left <= width * BORDER_FRACTION
        or top <= height * BORDER_FRACTION
        or right >= width * (1 - BORDER_FRACTION)
        or bottom >= height * (1 - BORDER_FRACTION)
    )


def percentile_rank(values: Sequence[float], value: float) -> float:
    """Fraction of ``values`` at or below ``value``, in [0, 1]."""
    if not values:
        return 0.0
    below = sum(1 for v in values if v <= value)
    return below / len(values)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; mirrors ``numpy.percentile`` default."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (q / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


# --------------------------------------------------------------------------
# Candidate assembly
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    record: "screen.ImageRecord"
    box: "screen.Box"
    sharpness: float | None = None
    score: float = 0.0
    action: str = "INCLUDE_CANDIDATE"
    notes: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return f"{self.record.stem}.txt"


@dataclass
class Funnel:
    """Accounting of every box removed on the way to the queue."""

    source_class: str
    total_boxes: int = 0
    removed_size: int = 0
    removed_duplicate: int = 0
    removed_clipped: int = 0
    retained: int = 0
    capped: int = 0


def collect(
    records: Sequence["screen.ImageRecord"],
    source_class: str,
    duplicate_stems: set[str],
    group_of: dict[str, str],
    archive: "screen.SourceArchive",
) -> tuple[list[Candidate], Funnel]:
    """Apply the size/duplicate/border funnel for one class."""
    funnel = Funnel(source_class=source_class)
    kept: list[Candidate] = []

    for record in records:
        for box in record.boxes:
            if box.source_class != source_class:
                continue
            funnel.total_boxes += 1

            if box.minimum_side_px < MIN_SIDE_PX:
                funnel.removed_size += 1
                continue
            if record.stem in duplicate_stems:
                funnel.removed_duplicate += 1
                continue
            if border_clipped(record, box):
                funnel.removed_clipped += 1
                continue

            kept.append(
                Candidate(
                    record=record,
                    box=box,
                    sharpness=sharpness(archive, record, box),
                )
            )

    return kept, funnel


def rank(candidates: list[Candidate]) -> None:
    """Score by the mean percentile rank of size, area, and sharpness.

    A single composite keeps the ordering explainable: a candidate is good when
    it is large *and* wide in frame *and* sharp, and no one of those can carry a
    weak candidate on its own. Ties break on stem so the result is stable.
    """
    sides = [c.box.minimum_side_px for c in candidates]
    areas = [c.box.box_area_ratio for c in candidates]
    sharp = [c.sharpness for c in candidates if c.sharpness is not None]

    for candidate in candidates:
        side_rank = percentile_rank(sides, candidate.box.minimum_side_px)
        area_rank = percentile_rank(areas, candidate.box.box_area_ratio)
        if candidate.sharpness is None:
            sharp_rank = 0.0
        else:
            sharp_rank = percentile_rank(sharp, candidate.sharpness)
        candidate.score = (side_rank + area_rank + sharp_rank) / 3.0

    candidates.sort(key=lambda c: (-c.score, c.record.stem))


def annotate(candidates: list[Candidate], pool_sharpness: Sequence[float]) -> None:
    """Attach the human-facing caveats. Only measured facts go in ``notes``."""
    floor = percentile(list(pool_sharpness), SHARPNESS_FLAG_PERCENTILE)

    for candidate in candidates:
        sharp_text = (
            f"{candidate.sharpness:.0f}" if candidate.sharpness is not None else "unmeasurable"
        )
        candidate.notes.append(
            f"side {candidate.box.minimum_side_px:.0f}px; "
            f"area {100 * candidate.box.box_area_ratio:.2f}%; "
            f"sharpness {sharp_text}"
        )

        if candidate.sharpness is None or candidate.sharpness < floor:
            candidate.action = "VISUAL_CHECK_REQUIRED"
            candidate.notes.append(
                f"sharpness below class p{SHARPNESS_FLAG_PERCENTILE:.0f} ({floor:.0f}) "
                "-> check blur before keeping"
            )
        else:
            candidate.action = "INCLUDE_CANDIDATE"

        # Never let a machine-sounding note imply a pose judgement was made.
        candidate.notes.append("pose/occlusion NOT machine-determined - confirm front-facing")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write_queue(path: Path, candidates: Sequence[Candidate], group_of: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(QUEUE_COLUMNS)
        for candidate in candidates:
            writer.writerow([
                candidate.filename,
                candidate.record.source_split,
                candidate.box.source_class,
                round(candidate.box.minimum_side_px, 2),
                round(candidate.box.box_area_ratio, 8),
                group_of.get(candidate.record.stem, ""),
                candidate.action,
                "",  # human_decision: left for the human, always
                "; ".join(candidate.notes),
            ])


def render(
    archive: "screen.SourceArchive",
    destination: Path,
    candidates: Sequence[Candidate],
    title: str,
    *,
    side: int,
    columns: int,
) -> bool:
    entries = [(c.record, c.box) for c in candidates]
    return screen.render_sheet(
        archive, destination, entries, title, side=side, columns=columns
    )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def main() -> int:
    if not ZIP_PATH.exists():
        raise SelectionError(f"source archive missing: {ZIP_PATH}")

    archive = screen.SourceArchive(ZIP_PATH)
    names = screen.parse_names(archive.read_text("construction-ppe/data.yaml"), "data.yaml")
    relevant = screen.resolve_relevant_ids(names)

    issues: list[screen.Issue] = []
    records, _tally = screen.load_records(archive, relevant, issues)
    if issues:
        raise SelectionError(f"{len(issues)} annotation issue(s); resolve before selecting")

    # Duplicate/leakage exclusion works at image level: an image in a near-dup
    # group cannot be half-used, so every box on it goes.
    near_groups = screen.near_duplicate_groups(records)
    group_of: dict[str, str] = {}
    duplicate_stems: set[str] = set()
    for group in near_groups:
        gid = screen.sha256_bytes(group[0].stem.encode())[:12]
        for record in group:
            duplicate_stems.add(record.stem)
            group_of[record.stem] = gid

    results: dict[str, tuple[list[Candidate], Funnel]] = {}
    for cls in screen.RELEVANT_NAMES:
        results[cls] = collect(records, cls, duplicate_stems, group_of, archive)

    # Rank and cap.
    goggles, goggles_funnel = results["goggles"]
    pool_sharp = [c.sharpness for c in goggles if c.sharpness is not None]
    rank(goggles)
    if len(goggles) > GOGGLES_LIMIT:
        goggles_funnel.capped = len(goggles) - GOGGLES_LIMIT
        goggles = goggles[:GOGGLES_LIMIT]
    annotate(goggles, pool_sharp)
    goggles_funnel.retained = len(goggles)

    no_goggle, no_goggle_funnel = results["no_goggle"]
    ng_pool_sharp = [c.sharpness for c in no_goggle if c.sharpness is not None]
    rank(no_goggle)
    annotate(no_goggle, ng_pool_sharp)
    no_goggle_funnel.retained = len(no_goggle)

    write_queue(OUT_DIR / "reduced_candidate_queue.csv", goggles + no_goggle, group_of)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    render(
        archive,
        ARTIFACT_DIR / "reduced_goggles_candidates.png",
        goggles,
        f"Reduced goggles candidates (n={len(goggles)}) - confirm pose/occlusion visually",
        side=250,
        columns=5,
    )
    render(
        archive,
        ARTIFACT_DIR / "reduced_no_goggle_candidates.png",
        no_goggle,
        f"Reduced no_goggle candidates (n={len(no_goggle)}) - confirm pose/occlusion visually",
        side=300,
        columns=5,
    )

    if not archive.verify_unchanged():
        raise SelectionError("source archive changed during selection")

    for funnel in (goggles_funnel, no_goggle_funnel):
        print(
            f"{funnel.source_class:10s} total={funnel.total_boxes:4d} "
            f"-size={funnel.removed_size:4d} -dup={funnel.removed_duplicate:3d} "
            f"-clip={funnel.removed_clipped:3d} -cap={funnel.capped:3d} "
            f"-> retained={funnel.retained:4d}"
        )
    for cls, rows in (("goggles", goggles), ("no_goggle", no_goggle)):
        flagged = sum(1 for c in rows if c.action == "VISUAL_CHECK_REQUIRED")
        print(f"{cls:10s} visual_check_required={flagged}")
    print(f"queue     : {OUT_DIR / 'reduced_candidate_queue.csv'}")
    print(f"sheets    : {ARTIFACT_DIR}")
    print(f"zip sha   : {archive.sha256_before}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
