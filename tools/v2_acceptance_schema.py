"""Shared schema, validation and scoring for the V2 live acceptance run.

This module is pure logic with no camera, model or serial dependency so
that it can be unit tested and reused by:

- ``tools/run_v2_live_acceptance.py``   (manual trial logger)
- ``tools/summarise_v2_live_acceptance.py`` (promotion gate)
- ``tools/live_eye_shield_demo.py``     (CSV row writer)

The V2 acceptance run is a *measurement*. It never changes thresholds,
never rewrites recorded rows, and never promotes a model by itself.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ACCEPTANCE_CSV_PATH = PROJECT_ROOT / "experiments" / "v2_live_acceptance.csv"

# The header is written verbatim and must not be reordered or renamed.
CSV_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "timestamp",
    "model",
    "weights_sha256",
    "condition",
    "pose",
    "expected_state",
    "observed_state",
    "top_class",
    "top_confidence",
    "false_safe",
    "false_alarm",
    "unknown",
    "duplicate_boxes",
    "application_fps",
    "threshold_preset",
    "notes",
)

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

# What the person in front of the camera is actually doing.
CONDITIONS: tuple[str, ...] = ("goggles", "no_goggles", "empty")

POSES: tuple[str, ...] = ("front", "left", "right", "none")

# Application states a trial may be scored as.
OBSERVED_STATES: tuple[str, ...] = (
    "UNKNOWN",
    "SAFE",
    "VIOLATION_PENDING",
    "VIOLATION_CONFIRMED",
    "SAFE_CLEAR_PENDING",
)

# The state each condition is *supposed* to reach.
EXPECTED_STATE_BY_CONDITION: dict[str, str] = {
    "goggles": "SAFE",
    "no_goggles": "VIOLATION_CONFIRMED",
    "empty": "UNKNOWN",
}

# Condition -> the pose values that are legal for it.
ALLOWED_POSES: dict[str, tuple[str, ...]] = {
    "goggles": ("front", "left", "right"),
    "no_goggles": ("front", "left", "right"),
    "empty": ("none",),
}

THRESHOLD_PRESETS: tuple[str, ...] = ("A", "B")

# Real top-class label written when no candidate box survived, so the
# column is never blank.
NO_DETECTION_CLASS = "none"

# ---------------------------------------------------------------------------
# Protocol definition (authoritative)
# ---------------------------------------------------------------------------

# condition -> pose -> number of trials
PROTOCOL: tuple[tuple[str, str, int], ...] = (
    ("goggles", "front", 10),
    ("goggles", "left", 5),
    ("goggles", "right", 5),
    ("no_goggles", "front", 10),
    ("no_goggles", "left", 5),
    ("no_goggles", "right", 5),
    ("empty", "none", 5),
)

TOTAL_TRIALS = sum(count for _, _, count in PROTOCOL)

MIN_GOGGLES_SAFE = 18
MIN_NO_GOGGLES_CONFIRMED = 18
MIN_EMPTY_UNKNOWN = 5
MIN_MEAN_FPS = 10.0
MAX_FALSE_SAFE = 0
MAX_CRASHES = 0

VERDICT_ELIGIBLE = "V2 ELIGIBLE FOR PROMOTION"
VERDICT_NOT_APPROVED = "V2 NOT APPROVED"

# Operational notes may use any characters that survive a CSV round trip,
# but they must never be allowed to smuggle in a spreadsheet formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class AcceptanceError(ValueError):
    """Raised when a row or CSV is not acceptable."""


def protocol_totals() -> dict[tuple[str, str], int]:
    """Return the required trial count per (condition, pose)."""

    return {(condition, pose): count for condition, pose, count in PROTOCOL}


# ---------------------------------------------------------------------------
# Trial ids
# ---------------------------------------------------------------------------

_TRIAL_ID_PATTERN = re.compile(r"^V2-(?P<seq>\d{3})$")


def format_trial_id(sequence: int) -> str:
    """Render a 1-based sequence number as a trial id."""

    if sequence < 1:
        raise AcceptanceError(
            f"trial sequence must be >= 1, got {sequence}"
        )

    return f"V2-{sequence:03d}"


def parse_trial_id(trial_id: str) -> int:
    """Return the 1-based sequence number encoded in a trial id."""

    match = _TRIAL_ID_PATTERN.match(trial_id.strip())

    if match is None:
        raise AcceptanceError(
            f"invalid trial id: {trial_id!r} (expected V2-NNN)"
        )

    sequence = int(match.group("seq"))

    if not 1 <= sequence <= TOTAL_TRIALS:
        raise AcceptanceError(
            f"trial id {trial_id!r} is outside V2-001.."
            f"V2-{TOTAL_TRIALS:03d}"
        )

    return sequence


# ---------------------------------------------------------------------------
# File digests
# ---------------------------------------------------------------------------

def sha256_of_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file.

    Provenance is computed, never typed, so a recorded digest cannot
    disagree with the weights actually used.
    """

    import hashlib

    digest = hashlib.sha256()

    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Scoring rules
# ---------------------------------------------------------------------------

def is_false_safe(condition: str, observed_state: str) -> bool:
    """True when the person has no goggles but the app reports SAFE.

    This is the failure the whole V2 run exists to eliminate, so it is
    counted strictly: a *confirmed* SAFE state after removal is a false
    safe, and so is a lingering SAFE_CLEAR_PENDING, because that state is
    only ever reachable from a previously SAFE reading.
    """

    return condition == "no_goggles" and observed_state in (
        "SAFE",
        "SAFE_CLEAR_PENDING",
    )


def is_false_alarm(condition: str, observed_state: str) -> bool:
    """True when a compliant scene is reported as a confirmed violation."""

    return condition in ("goggles", "empty") and (
        observed_state == "VIOLATION_CONFIRMED"
    )


def is_unknown(condition: str, observed_state: str) -> bool:
    """True when the trial ended in UNKNOWN."""

    return observed_state == "UNKNOWN"


def is_correct(condition: str, observed_state: str) -> bool:
    """True when the observed state matches the expected state."""

    return observed_state == EXPECTED_STATE_BY_CONDITION[condition]


def count_duplicate_overlaps(
    accepted: Sequence[tuple[str, float, tuple[float, float, float, float]]],
    iou_threshold: float = 0.30,
) -> int:
    """Count same-class accepted boxes that still overlap heavily.

    This is a *measurement only*. It reports how many accepted boxes
    share a class with an earlier accepted box whose IoU exceeds
    ``iou_threshold``. It never removes a box and never re-runs NMS, so
    the frame decision is unaffected. A count above zero means NMS left
    stacked duplicate detections that the IoU setting did not suppress.
    """

    duplicates = 0

    for index, (class_name, _, box) in enumerate(accepted):
        for earlier_class, _, earlier_box in accepted[:index]:
            if earlier_class != class_name:
                continue

            if (
                intersection_over_union(box, earlier_box)
                > iou_threshold
            ):
                duplicates += 1
                break

    return duplicates


def intersection_over_union(
    first: Sequence[float],
    second: Sequence[float],
) -> float:
    """Return IoU for two ``(x1, y1, x2, y2)`` boxes."""

    first_x1, first_y1, first_x2, first_y2 = (float(v) for v in first)
    second_x1, second_y1, second_x2, second_y2 = (
        float(v) for v in second
    )

    inter_x1 = max(first_x1, second_x1)
    inter_y1 = max(first_y1, second_y1)
    inter_x2 = min(first_x2, second_x2)
    inter_y2 = min(first_y2, second_y2)

    inter_width = max(0.0, inter_x2 - inter_x1)
    inter_height = max(0.0, inter_y2 - inter_y1)
    intersection = inter_width * inter_height

    if intersection <= 0.0:
        return 0.0

    first_area = max(
        0.0, (first_x2 - first_x1) * (first_y2 - first_y1)
    )
    second_area = max(
        0.0, (second_x2 - second_x1) * (second_y2 - second_y1)
    )

    union = first_area + second_area - intersection

    if union <= 0.0:
        return 0.0

    return intersection / union


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _clean(value: object) -> str:
    """Normalise a CSV cell to a stripped string."""

    if value is None:
        return ""

    return str(value).strip()


def _validate_note(note: str) -> None:
    if note.startswith(_FORMULA_PREFIXES):
        raise AcceptanceError(
            "notes must not start with a spreadsheet formula "
            f"character: {note!r}"
        )


def validate_row(row: dict[str, str], known_trial_ids: Iterable[str] = ()) -> dict[str, str]:
    """Validate one row and return it with clean string values.

    ``known_trial_ids`` lets the caller reject duplicate trial ids when
    appending to an existing file.
    """

    cleaned = {column: _clean(row.get(column)) for column in CSV_COLUMNS}

    missing = [
        column
        for column in CSV_COLUMNS
        if column != "notes" and not cleaned[column]
    ]

    if missing:
        raise AcceptanceError(
            "missing required values for: " + ", ".join(missing)
        )

    _validate_note(cleaned["notes"])

    # trial_id
    sequence = parse_trial_id(cleaned["trial_id"])

    if any(
        existing == cleaned["trial_id"]
        for existing in known_trial_ids
    ):
        raise AcceptanceError(
            f"duplicate trial id: {cleaned['trial_id']}"
        )

    # condition / pose / expected_state
    condition = cleaned["condition"]

    if condition not in CONDITIONS:
        raise AcceptanceError(
            f"invalid condition: {condition!r} "
            f"(expected one of {', '.join(CONDITIONS)})"
        )

    pose = cleaned["pose"]

    if pose not in POSES:
        raise AcceptanceError(
            f"invalid pose: {pose!r} "
            f"(expected one of {', '.join(POSES)})"
        )

    if pose not in ALLOWED_POSES[condition]:
        raise AcceptanceError(
            f"pose {pose!r} is not valid for condition "
            f"{condition!r}"
        )

    expected = cleaned["expected_state"]

    if expected != EXPECTED_STATE_BY_CONDITION[condition]:
        raise AcceptanceError(
            f"expected_state {expected!r} does not match the "
            f"contract for {condition!r} "
            f"({EXPECTED_STATE_BY_CONDITION[condition]})"
        )

    # observed_state
    observed = cleaned["observed_state"]

    if observed not in OBSERVED_STATES:
        raise AcceptanceError(
            f"invalid observed_state: {observed!r} "
            f"(expected one of {', '.join(OBSERVED_STATES)})"
        )

    # threshold preset
    preset = cleaned["threshold_preset"]

    if preset not in THRESHOLD_PRESETS:
        raise AcceptanceError(
            f"invalid threshold_preset: {preset!r} "
            f"(expected one of {', '.join(THRESHOLD_PRESETS)})"
        )

    # top_class
    top_class = cleaned["top_class"]

    if top_class not in ("goggles", "no_goggles", NO_DETECTION_CLASS):
        raise AcceptanceError(
            f"invalid top_class: {top_class!r}"
        )

    # numeric columns
    top_confidence = _as_float(cleaned["top_confidence"], "top_confidence")

    if not 0.0 <= top_confidence <= 1.0:
        raise AcceptanceError(
            "top_confidence must be between 0 and 1"
        )

    application_fps = _as_float(
        cleaned["application_fps"], "application_fps"
    )

    if application_fps < 0.0:
        raise AcceptanceError("application_fps must not be negative")

    duplicate_boxes = _as_int(
        cleaned["duplicate_boxes"], "duplicate_boxes"
    )

    if duplicate_boxes < 0:
        raise AcceptanceError(
            "duplicate_boxes must not be negative"
        )

    # derived boolean columns must agree with the recorded states
    for column, rule in (
        ("false_safe", is_false_safe),
        ("false_alarm", is_false_alarm),
        ("unknown", is_unknown),
    ):
        recorded = _as_flag(cleaned[column])

        if recorded != rule(condition, observed):
            raise AcceptanceError(
                f"{column} is {int(recorded)} but the "
                f"{condition!r}/{observed!r} pair implies "
                f"{int(rule(condition, observed))}"
            )

    # weights_sha256
    digest = cleaned["weights_sha256"].lower()

    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AcceptanceError(
            "weights_sha256 must be a 64-character hex digest"
        )

    return cleaned


def _as_float(value: str, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AcceptanceError(f"{name} must be numeric: {value!r}")


def _as_int(value: str, name: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise AcceptanceError(f"{name} must be an integer: {value!r}")


def _as_flag(value: str) -> bool:
    normalised = value.strip().lower()

    if normalised in ("1", "true", "yes"):
        return True

    if normalised in ("0", "false", "no", ""):
        return False

    raise AcceptanceError(f"expected a 0/1 flag, got {value!r}")


# ---------------------------------------------------------------------------
# CSV input / output
# ---------------------------------------------------------------------------

def read_rows(path: Path = ACCEPTANCE_CSV_PATH) -> list[dict[str, str]]:
    """Read recorded trials, rejecting a wrong header."""

    path = Path(path)

    if not path.is_file():
        return []

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)

        if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
            raise AcceptanceError(
                f"{path} header does not match the required "
                f"{len(CSV_COLUMNS)}-column schema"
            )

        return [
            {column: _clean(row.get(column)) for column in CSV_COLUMNS}
            for row in reader
        ]


def append_row(
    row: dict[str, str],
    path: Path = ACCEPTANCE_CSV_PATH,
) -> dict[str, str]:
    """Validate and append one row. Existing rows are never modified."""

    path = Path(path)
    existing = read_rows(path)
    cleaned = validate_row(
        row,
        known_trial_ids=(entry["trial_id"] for entry in existing),
    )

    write_header = not path.is_file() or path.stat().st_size == 0

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))

        if write_header:
            writer.writeheader()

        writer.writerow(cleaned)

    return cleaned


def write_empty_schema(path: Path = ACCEPTANCE_CSV_PATH) -> None:
    """Create the CSV with only the header, if it does not exist yet."""

    path = Path(path)

    if path.is_file() and path.stat().st_size > 0:
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(list(CSV_COLUMNS))


# ---------------------------------------------------------------------------
# Summary and promotion gate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    """One requirement of the promotion gate."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class AcceptanceSummary:
    """Aggregate statistics for a recorded acceptance run."""

    rows: int
    presets: tuple[str, ...]
    model_names: tuple[str, ...]
    weights_digests: tuple[str, ...]
    coverage: dict[tuple[str, str], int]
    missing_trials: int
    duplicate_trial_ids: tuple[str, ...]
    false_safe: int
    false_alarm: int
    unknown: int
    goggles_safe: int
    goggles_total: int
    no_goggles_confirmed: int
    no_goggles_total: int
    empty_unknown: int
    empty_total: int
    mean_fps: float
    duplicates_total: int
    trials_with_duplicates: int
    gates: tuple[GateResult, ...]

    @property
    def verdict(self) -> str:
        return VERDICT_ELIGIBLE if all(
            gate.passed for gate in self.gates
        ) else VERDICT_NOT_APPROVED

    @property
    def approved(self) -> bool:
        return all(gate.passed for gate in self.gates)


def summarise(
    rows: Sequence[dict[str, str]],
    crashed_trials: int = 0,
    unresolved_contract_failures: int = 0,
) -> AcceptanceSummary:
    """Compute every required statistic and the promotion gate."""

    trials = [dict(row) for row in rows]

    ids = [row["trial_id"] for row in trials]
    unique_ids = set(ids)
    duplicate_trial_ids = tuple(
        sorted(
            {
                trial_id
                for trial_id in ids
                if ids.count(trial_id) > 1
            }
        )
    )

    coverage = {key: 0 for key in protocol_totals()}

    for row in trials:
        key = (row["condition"], row["pose"])

        if key in coverage:
            coverage[key] += 1

    missing_trials = sum(
        max(0, required - coverage[key])
        for key, required in protocol_totals().items()
    )

    def count(predicate) -> int:
        return sum(1 for row in trials if predicate(row))

    false_safe = count(
        lambda row: row["condition"] == "no_goggles"
        and row["observed_state"] in ("SAFE", "SAFE_CLEAR_PENDING")
    )

    false_alarm = count(
        lambda row: row["condition"] in ("goggles", "empty")
        and row["observed_state"] == "VIOLATION_CONFIRMED"
    )

    unknown = count(lambda row: row["observed_state"] == "UNKNOWN")

    goggles_total = count(lambda row: row["condition"] == "goggles")
    goggles_safe = count(
        lambda row: row["condition"] == "goggles"
        and row["observed_state"] == "SAFE"
    )

    no_goggles_total = count(
        lambda row: row["condition"] == "no_goggles"
    )
    no_goggles_confirmed = count(
        lambda row: row["condition"] == "no_goggles"
        and row["observed_state"] == "VIOLATION_CONFIRMED"
    )

    empty_total = count(lambda row: row["condition"] == "empty")
    empty_unknown = count(
        lambda row: row["condition"] == "empty"
        and row["observed_state"] == "UNKNOWN"
    )

    fps_values = [
        float(row["application_fps"])
        for row in trials
        if _as_float(row["application_fps"], "application_fps") > 0.0
    ]

    mean_fps = (
        sum(fps_values) / len(fps_values)
        if fps_values
        else 0.0
    )

    duplicates_total = sum(
        _as_int(row["duplicate_boxes"], "duplicate_boxes")
        for row in trials
    )

    trials_with_duplicates = count(
        lambda row: _as_int(row["duplicate_boxes"], "duplicate_boxes") > 0
    )

    presets = tuple(sorted({row["threshold_preset"] for row in trials}))
    model_names = tuple(sorted({row["model"] for row in trials}))
    weights_digests = tuple(
        sorted({row["weights_sha256"].lower() for row in trials})
    )

    gates = (
        GateResult(
            "all 45 trials recorded exactly once",
            len(trials) == TOTAL_TRIALS
            and not duplicate_trial_ids
            and missing_trials == 0,
            f"{len(trials)}/{TOTAL_TRIALS} rows, "
            f"{missing_trials} missing, "
            f"{len(duplicate_trial_ids)} duplicated ids",
        ),
        GateResult(
            "false-safe count is 0",
            false_safe <= MAX_FALSE_SAFE,
            f"{false_safe} false-safe (limit {MAX_FALSE_SAFE})",
        ),
        GateResult(
            "goggles reported SAFE at least 18/20",
            goggles_safe >= MIN_GOGGLES_SAFE,
            f"{goggles_safe}/{goggles_total} "
            f"(minimum {MIN_GOGGLES_SAFE})",
        ),
        GateResult(
            "no_goggles CONFIRMED at least 18/20",
            no_goggles_confirmed >= MIN_NO_GOGGLES_CONFIRMED,
            f"{no_goggles_confirmed}/{no_goggles_total} "
            f"(minimum {MIN_NO_GOGGLES_CONFIRMED})",
        ),
        GateResult(
            "empty scene UNKNOWN 5/5",
            empty_unknown >= MIN_EMPTY_UNKNOWN
            and empty_total == MIN_EMPTY_UNKNOWN,
            f"{empty_unknown}/{empty_total} "
            f"(required {MIN_EMPTY_UNKNOWN}/"
            f"{MIN_EMPTY_UNKNOWN})",
        ),
        GateResult(
            "no crashes",
            crashed_trials <= MAX_CRASHES,
            f"{crashed_trials} crashes (limit {MAX_CRASHES})",
        ),
        GateResult(
            "mean application FPS at least 10",
            mean_fps >= MIN_MEAN_FPS,
            f"{mean_fps:.2f} FPS (minimum {MIN_MEAN_FPS:g})",
        ),
        GateResult(
            "no unresolved model or class-contract failure",
            unresolved_contract_failures == 0,
            f"{unresolved_contract_failures} unresolved failures",
        ),
        GateResult(
            "single weights file and single threshold preset",
            len(weights_digests) == 1 and len(presets) == 1,
            f"{len(weights_digests)} weights digest(s), "
            f"preset(s) {', '.join(presets) or 'none'}",
        ),
    )

    return AcceptanceSummary(
        rows=len(trials),
        presets=presets,
        model_names=model_names,
        weights_digests=weights_digests,
        coverage=coverage,
        missing_trials=missing_trials,
        duplicate_trial_ids=duplicate_trial_ids,
        false_safe=false_safe,
        false_alarm=false_alarm,
        unknown=unknown,
        goggles_safe=goggles_safe,
        goggles_total=goggles_total,
        no_goggles_confirmed=no_goggles_confirmed,
        no_goggles_total=no_goggles_total,
        empty_unknown=empty_unknown,
        empty_total=empty_total,
        mean_fps=mean_fps,
        duplicates_total=duplicates_total,
        trials_with_duplicates=trials_with_duplicates,
        gates=gates,
    )


def format_summary(summary: AcceptanceSummary) -> str:
    """Render the summary exactly as the report expects it."""

    lines: list[str] = []

    lines.append("V2 LIVE ACCEPTANCE SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Trials recorded: {summary.rows}/{TOTAL_TRIALS}")

    models = ", ".join(summary.model_names) or "none"
    lines.append(f"Model(s): {models}")

    digests = ", ".join(
        digest[:12] for digest in summary.weights_digests
    ) or "none"
    lines.append(f"Weights SHA-256 prefix: {digests}")

    presets = ", ".join(summary.presets) or "none"
    lines.append(f"Threshold preset(s): {presets}")
    lines.append("")
    lines.append("Protocol coverage")
    lines.append("-" * 60)

    for condition, pose, required in PROTOCOL:
        recorded = summary.coverage[(condition, pose)]
        marker = "ok" if recorded == required else "MISSING"
        lines.append(
            f"  {condition:<11} {pose:<6} {recorded:>2}/{required:<2} {marker}"
        )

    if summary.duplicate_trial_ids:
        lines.append("")
        lines.append(
            "Duplicate trial ids: "
            + ", ".join(summary.duplicate_trial_ids)
        )

    lines.append("")
    lines.append("Outcome counts")
    lines.append("-" * 60)
    lines.append(f"  false-safe (no_goggles -> SAFE)      : {summary.false_safe}")
    lines.append(f"  false-alarm (goggles/empty -> CONFIRMED): {summary.false_alarm}")
    lines.append(f"  UNKNOWN                              : {summary.unknown}")
    lines.append(
        f"  goggles SAFE                         : "
        f"{summary.goggles_safe}/{summary.goggles_total}"
    )
    lines.append(
        f"  no_goggles VIOLATION_CONFIRMED       : "
        f"{summary.no_goggles_confirmed}/{summary.no_goggles_total}"
    )
    lines.append(
        f"  empty UNKNOWN                        : "
        f"{summary.empty_unknown}/{summary.empty_total}"
    )
    lines.append(f"  mean application FPS                 : {summary.mean_fps:.2f}")
    lines.append(
        f"  duplicate-box trials                 : "
        f"{summary.trials_with_duplicates} "
        f"({summary.duplicates_total} extra boxes)"
    )

    lines.append("")
    lines.append("Promotion gate")
    lines.append("-" * 60)

    for gate in summary.gates:
        status = "PASS" if gate.passed else "FAIL"
        lines.append(f"  [{status}] {gate.name}: {gate.detail}")

    lines.append("")
    lines.append(summary.verdict)

    return "\n".join(lines)
