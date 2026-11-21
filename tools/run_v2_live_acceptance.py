"""Guided, one-trial-at-a-time logger for the V2 live acceptance run.

This tool never touches the camera, the model or the ESP32. It is a
deliberate human-in-the-loop recorder: the operator performs one physical
trial with ``tools/live_eye_shield_demo.py``, reads the result off the
overlay, and then records it here.

Design rules
------------
- One trial per invocation. There is no batch mode.
- Every row must be confirmed by typing ``APPEND``. There is no
  ``--yes`` bypass, because the whole point of this file is that a human
  looked at the trial and vouched for it.
- The CSV is append-only. Existing rows are never edited or removed.
- ``weights_sha256`` is computed from the weights file, never typed, so
  the recorded provenance cannot drift from the weights actually used.
- ``expected_state``, ``false_safe``, ``false_alarm`` and ``unknown`` are
  derived, never typed, so they cannot be made to disagree with the
  recorded condition and observed state.

Usage
-----
    python tools/run_v2_live_acceptance.py --status
    python tools/run_v2_live_acceptance.py --next
    python tools/run_v2_live_acceptance.py --preset A
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.v2_acceptance_schema import (
    ACCEPTANCE_CSV_PATH,
    CONDITIONS,
    EXPECTED_STATE_BY_CONDITION,
    NO_DETECTION_CLASS,
    OBSERVED_STATES,
    PROTOCOL,
    THRESHOLD_PRESETS,
    TOTAL_TRIALS,
    AcceptanceError,
    append_row,
    format_trial_id,
    is_false_alarm,
    is_false_safe,
    is_unknown,
    protocol_totals,
    read_rows,
    sha256_of_file,
    validate_row,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = PROJECT_ROOT / "models" / "best_v2.pt"

# The only weights this run is allowed to record, unless the operator
# explicitly opts out.
V2_WEIGHTS_SHA256 = (
    "fec02c88f4236d85d4e913df57638a96cf0a35498f6a65c413b7fe14077f22fc"
)
V1_WEIGHTS_SHA256 = (
    "9366b73b35d4f820af401808aa4e0e58cb29a7b557991b123e5cd6a813fa3591"
)

CONFIRM_TOKEN = "APPEND"


# ---------------------------------------------------------------------------
# Protocol progression
# ---------------------------------------------------------------------------

def next_trial(
    rows: list[dict[str, str]],
) -> tuple[str, str, str] | None:
    """Return ``(trial_id, condition, pose)`` for the next trial.

    The condition and pose come from the first protocol slot that has not
    reached its required count. The trial id is the lowest number not
    already recorded, so a slot filled out of order can never hand out a
    duplicate id. ``None`` means the protocol is complete.
    """

    coverage = protocol_totals()
    for key in coverage:
        coverage[key] = 0

    used = set()

    for row in rows:
        used.add(row["trial_id"])
        key = (row["condition"], row["pose"])

        if key in coverage:
            coverage[key] += 1

    trial_id = None

    for sequence in range(1, TOTAL_TRIALS + 1):
        candidate = format_trial_id(sequence)

        if candidate not in used:
            trial_id = candidate
            break

    for condition, pose, required in PROTOCOL:
        if coverage[(condition, pose)] < required:
            return (
                trial_id or format_trial_id(TOTAL_TRIALS),
                condition,
                pose,
            )

    return None


def coverage_report(rows: list[dict[str, str]]) -> str:
    """Render protocol coverage as a table."""

    coverage = protocol_totals()
    for key in coverage:
        coverage[key] = 0

    for row in rows:
        key = (row["condition"], row["pose"])

        if key in coverage:
            coverage[key] += 1

    lines = [
        f"Recorded trials: {len(rows)}/{TOTAL_TRIALS}",
        "",
        f"{'condition':<12}{'pose':<8}{'done':>6}{'need':>6}  status",
        "-" * 46,
    ]

    for condition, pose, required in PROTOCOL:
        recorded = coverage[(condition, pose)]
        status = "ok" if recorded == required else "TODO"

        lines.append(
            f"{condition:<12}{pose:<8}{recorded:>6}{required:>6}  {status}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------

def build_row(
    *,
    trial_id: str,
    condition: str,
    pose: str,
    observed_state: str,
    top_class: str,
    top_confidence: float,
    duplicate_boxes: int,
    application_fps: float,
    preset: str,
    weights: Path,
    weights_digest: str,
    notes: str = "",
) -> dict[str, str]:
    """Assemble a CSV row, deriving every value that must not be typed."""

    return {
        "trial_id": trial_id,
        "timestamp": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "model": Path(weights).name,
        "weights_sha256": weights_digest,
        "condition": condition,
        "pose": pose,
        "expected_state": EXPECTED_STATE_BY_CONDITION[condition],
        "observed_state": observed_state,
        "top_class": top_class,
        "top_confidence": f"{top_confidence:.4f}",
        "false_safe": "1" if is_false_safe(
            condition, observed_state
        ) else "0",
        "false_alarm": "1" if is_false_alarm(
            condition, observed_state
        ) else "0",
        "unknown": "1" if is_unknown(
            condition, observed_state
        ) else "0",
        "duplicate_boxes": str(duplicate_boxes),
        "application_fps": f"{application_fps:.2f}",
        "threshold_preset": preset,
        "notes": notes,
    }


def demo_command(
    *,
    condition: str,
    pose: str,
    preset: str,
    weights: Path,
    trial_id: str,
    source: str = "0",
) -> str:
    """Return the exact demo command that produces this trial."""

    thresholds = {
        "A": ("0.40", "0.60", "0.55", "0.30"),
        "B": ("0.50", "0.65", "0.60", "0.30"),
    }[preset]

    candidate, goggles, no_goggles, iou = thresholds

    return (
        f".venv/bin/python tools/live_eye_shield_demo.py "
        f"--source {source} "
        f"--weights {_relative(weights)} "
        f"--candidate-confidence {candidate} "
        f"--goggles-confidence {goggles} "
        f"--no-goggles-confidence {no_goggles} "
        f"--iou {iou} "
        f"--device auto "
        f"--log-csv {_relative(ACCEPTANCE_CSV_PATH)} "
        f"--trial-id {trial_id} "
        f"--condition {condition} "
        f"--pose {pose}"
    )


def _relative(path: Path) -> str:
    """Render a path relative to the project root when it lives inside."""

    resolved = Path(path).resolve()

    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


# ---------------------------------------------------------------------------
# Interactive recording
# ---------------------------------------------------------------------------

def _prompt(
    label: str,
    default: str,
    input_fn,
) -> str:
    """Prompt once and fall back to a default on empty input."""

    suffix = f" [{default}]" if default else ""
    answer = input_fn(f"{label}{suffix}: ").strip()

    return answer or default


def record_trial(
    *,
    args: argparse.Namespace,
    input_fn=input,
    print_fn=print,
) -> int:
    """Interactively record exactly one trial."""

    csv_path = Path(args.csv)
    rows = read_rows(csv_path)

    suggested = next_trial(rows)

    if suggested is None and not args.trial_id:
        print_fn(
            f"All {TOTAL_TRIALS} protocol trials are already recorded. "
            "Nothing left to log."
        )
        return 1

    default_trial_id, default_condition, default_pose = (
        suggested if suggested else ("", "", "")
    )

    print_fn("=" * 62)
    print_fn("V2 LIVE ACCEPTANCE - SINGLE TRIAL RECORD")
    print_fn("=" * 62)
    print_fn("")
    print_fn("Run the trial first, then record what the overlay showed.")
    print_fn("")

    trial_id = _prompt(
        "Trial id", args.trial_id or default_trial_id, input_fn
    )
    condition = _prompt(
        "Condition (goggles/no_goggles/empty)",
        args.condition or default_condition,
        input_fn,
    )
    pose = _prompt(
        "Pose (front/left/right/none)",
        args.pose or default_pose,
        input_fn,
    )
    observed_state = _prompt(
        "Observed application state "
        f"({'/'.join(OBSERVED_STATES)})",
        "",
        input_fn,
    )
    top_class = _prompt(
        "Top accepted class (goggles/no_goggles/none)",
        NO_DETECTION_CLASS,
        input_fn,
    )
    top_confidence = _prompt("Top confidence (0-1)", "0.0", input_fn)
    duplicate_boxes = _prompt("Duplicate accepted boxes seen", "0", input_fn)
    application_fps = _prompt("Application FPS", "0.0", input_fn)
    notes = _prompt("Notes", "", input_fn)

    try:
        row = build_row(
            trial_id=trial_id,
            condition=condition,
            pose=pose,
            observed_state=observed_state,
            top_class=top_class,
            top_confidence=float(top_confidence),
            duplicate_boxes=int(float(duplicate_boxes)),
            application_fps=float(application_fps),
            preset=args.preset,
            weights=Path(args.weights),
            weights_digest=args.weights_digest,
            notes=notes,
        )
        row = validate_row(
            row,
            known_trial_ids=(entry["trial_id"] for entry in rows),
        )
    except AcceptanceError as error:
        print_fn(f"\nERROR: {error}")
        print_fn("Nothing was written.")
        return 1

    print_fn("")
    print_fn("ROW TO APPEND")
    print_fn("-" * 62)

    for column, value in row.items():
        print_fn(f"  {column:<20} {value}")

    print_fn("-" * 62)

    if row["false_safe"] == "1":
        print_fn("WARNING: this trial is a FALSE SAFE.")
        print_fn("It will be recorded. Do not retry or delete it.")

    print_fn("")
    answer = input_fn(
        f"Type {CONFIRM_TOKEN} to append, anything else to discard: "
    ).strip()

    if answer != CONFIRM_TOKEN:
        print_fn("Discarded. Nothing was written.")
        return 1

    try:
        append_row(row, csv_path)
    except AcceptanceError as error:
        print_fn(f"ERROR: {error}")
        return 1

    print_fn(f"Appended {row['trial_id']} to {csv_path}")

    remaining = next_trial(read_rows(csv_path))

    if remaining is None:
        print_fn("Protocol complete. Run the summariser next.")
    else:
        print_fn(
            f"Next trial: {remaining[0]} "
            f"({remaining[1]}, {remaining[2]})"
        )

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record one V2 live-acceptance trial at a time. "
            "Never touches the camera or the ESP32."
        )
    )

    parser.add_argument(
        "--csv",
        default=str(ACCEPTANCE_CSV_PATH),
        help="Acceptance CSV to append to",
    )

    parser.add_argument(
        "--weights",
        default=str(DEFAULT_WEIGHTS),
        help="Weights whose SHA-256 is recorded",
    )

    parser.add_argument(
        "--preset",
        default="A",
        choices=THRESHOLD_PRESETS,
        help="Threshold preset used for this run",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Show protocol coverage and exit",
    )

    parser.add_argument(
        "--next",
        action="store_true",
        help="Show the next trial and the exact demo command",
    )

    parser.add_argument(
        "--source",
        default="0",
        help="Camera source shown in the --next command",
    )

    parser.add_argument(
        "--trial-id",
        default="",
        help="Override the suggested trial id",
    )

    parser.add_argument(
        "--condition",
        default="",
        choices=("", *CONDITIONS),
        help="Override the suggested condition",
    )

    parser.add_argument(
        "--pose",
        default="",
        choices=("", "front", "left", "right", "none"),
        help="Override the suggested pose",
    )

    parser.add_argument(
        "--allow-other-weights",
        action="store_true",
        help=(
            "Allow recording a weights file that is not the V2 "
            "checkpoint. Refused by default."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    weights = Path(args.weights)

    if not weights.is_file():
        print(f"ERROR: weights not found: {weights}")
        return 1

    args.weights_digest = sha256_of_file(weights)

    if (
        args.weights_digest != V2_WEIGHTS_SHA256
        and not args.allow_other_weights
    ):
        print(
            "ERROR: these weights are not the V2 checkpoint."
        )
        print(f"  file    : {weights}")
        print(f"  sha256  : {args.weights_digest}")

        if args.weights_digest == V1_WEIGHTS_SHA256:
            print("  This is V1 (models/best.pt).")

        print(
            "Refusing to record a V2 acceptance trial with them. "
            "Use --allow-other-weights only for a deliberate "
            "V1 comparison run."
        )
        return 1

    csv_path = Path(args.csv)

    try:
        rows = read_rows(csv_path)
    except AcceptanceError as error:
        print(f"ERROR: {error}")
        return 1

    if args.status:
        print(coverage_report(rows))
        return 0

    if args.next:
        upcoming = next_trial(rows)

        if upcoming is None:
            print("Protocol complete. Run the summariser next.")
            return 0

        trial_id, condition, pose = upcoming

        print(f"Next trial : {trial_id}")
        print(f"Condition  : {condition}")
        print(f"Pose       : {pose}")
        print("")
        print("Run exactly:")
        print("")
        print(
            demo_command(
                condition=condition,
                pose=pose,
                preset=args.preset,
                weights=weights,
                trial_id=trial_id,
                source=args.source,
            )
        )
        print("")
        print(
            "Then record it with: "
            f".venv/bin/python tools/run_v2_live_acceptance.py "
            f"--preset {args.preset}"
        )
        return 0

    return record_trial(args=args)


if __name__ == "__main__":
    raise SystemExit(main())
