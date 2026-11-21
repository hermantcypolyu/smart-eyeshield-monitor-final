"""Compute the V2 live-acceptance statistics and promotion verdict.

Read-only. This tool never writes to the acceptance CSV, never edits a
recorded row and never promotes a model. It prints the statistics and one
of exactly two verdicts:

    V2 ELIGIBLE FOR PROMOTION
    V2 NOT APPROVED

Anything missing, ambiguous or contradictory resolves to
``V2 NOT APPROVED``: a run with incomplete coverage, a single false safe,
or more than one threshold preset can never be promoted.

Usage
-----
    python tools/summarise_v2_live_acceptance.py
    python tools/summarise_v2_live_acceptance.py --json
    python tools/summarise_v2_live_acceptance.py --crashes 0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.v2_acceptance_schema import (
    ACCEPTANCE_CSV_PATH,
    VERDICT_ELIGIBLE,
    VERDICT_NOT_APPROVED,
    AcceptanceError,
    format_summary,
    read_rows,
    summarise,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Summarise the V2 live-acceptance CSV and print the "
            "promotion verdict. Read-only."
        )
    )

    parser.add_argument(
        "--csv",
        default=str(ACCEPTANCE_CSV_PATH),
        help="Acceptance CSV to read",
    )

    parser.add_argument(
        "--crashes",
        type=int,
        default=0,
        help=(
            "Number of trial crashes observed during the run. "
            "Only pass 0 if no trial crashed."
        ),
    )

    parser.add_argument(
        "--contract-failures",
        type=int,
        default=0,
        help=(
            "Number of unresolved model or class-contract "
            "failures seen during the run."
        ),
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the report",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    csv_path = Path(args.csv)

    try:
        rows = read_rows(csv_path)
    except AcceptanceError as error:
        print(f"ERROR: {error}")
        print(VERDICT_NOT_APPROVED)
        return 2

    summary = summarise(
        rows,
        crashed_trials=args.crashes,
        unresolved_contract_failures=args.contract_failures,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "csv": str(csv_path),
                    "rows": summary.rows,
                    "model_names": list(summary.model_names),
                    "weights_sha256": list(summary.weights_digests),
                    "presets": list(summary.presets),
                    "coverage": {
                        f"{condition}/{pose}": recorded
                        for (condition, pose), recorded
                        in summary.coverage.items()
                    },
                    "missing_trials": summary.missing_trials,
                    "duplicate_trial_ids": list(
                        summary.duplicate_trial_ids
                    ),
                    "false_safe": summary.false_safe,
                    "false_alarm": summary.false_alarm,
                    "unknown": summary.unknown,
                    "goggles_safe": summary.goggles_safe,
                    "goggles_total": summary.goggles_total,
                    "no_goggles_confirmed": (
                        summary.no_goggles_confirmed
                    ),
                    "no_goggles_total": summary.no_goggles_total,
                    "empty_unknown": summary.empty_unknown,
                    "empty_total": summary.empty_total,
                    "mean_fps": round(summary.mean_fps, 2),
                    "trials_with_duplicates": (
                        summary.trials_with_duplicates
                    ),
                    "duplicates_total": summary.duplicates_total,
                    "gates": [
                        {
                            "name": gate.name,
                            "passed": gate.passed,
                            "detail": gate.detail,
                        }
                        for gate in summary.gates
                    ],
                    "approved": summary.approved,
                    "verdict": summary.verdict,
                },
                indent=2,
            )
        )
    else:
        print(format_summary(summary))

    # Exit 0 only when the model would be eligible for promotion, so a
    # scripted check cannot mistake "ran fine" for "approved".
    if summary.verdict == VERDICT_ELIGIBLE:
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
