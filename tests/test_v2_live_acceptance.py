"""Focused tests for the V2 live-acceptance tooling.

These cover the pure logic that decides whether a row may be recorded and
whether a completed run may promote the model. They never open a camera,
never open a serial port, never load a model and never run a trial, so they
run on any machine.

Deliberately not tested: the demo's video loop, the overlay rendering, or
any live measurement. Those require physical hardware and a human operator.
"""

from __future__ import annotations

import csv

import pytest

from tools.live_eye_shield_demo import TrialSession
from tools.run_v2_live_acceptance import build_row, next_trial
from tools.v2_acceptance_schema import (
    CSV_COLUMNS,
    NO_DETECTION_CLASS,
    PROTOCOL,
    THRESHOLD_PRESETS,
    TOTAL_TRIALS,
    VERDICT_ELIGIBLE,
    VERDICT_NOT_APPROVED,
    AcceptanceError,
    append_row,
    count_duplicate_overlaps,
    format_trial_id,
    intersection_over_union,
    is_false_alarm,
    is_false_safe,
    is_unknown,
    parse_trial_id,
    read_rows,
    summarise,
    validate_row,
    write_empty_schema,
)

V2_DIGEST = "fec02c88f4236d85d4e913df57638a96cf0a35498f6a65c413b7fe14077f22fc"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _row(**overrides) -> dict[str, str]:
    """Return a valid row, with individual fields overridable."""

    row = {
        "trial_id": "V2-001",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "model": "best_v2.pt",
        "weights_sha256": V2_DIGEST,
        "condition": "no_goggles",
        "pose": "front",
        "expected_state": "VIOLATION_CONFIRMED",
        "observed_state": "VIOLATION_CONFIRMED",
        "top_class": "no_goggles",
        "top_confidence": "0.9000",
        "false_safe": "0",
        "false_alarm": "0",
        "unknown": "0",
        "duplicate_boxes": "0",
        "application_fps": "25.00",
        "threshold_preset": "A",
        "notes": "",
    }
    row.update(overrides)

    return row


def _protocol_rows(
    *,
    observed_for,
    preset: str = "A",
) -> list[dict[str, str]]:
    """Build a full, valid 45-row run.

    ``observed_for`` maps ``(condition, pose)`` to the observed state used
    for every trial in that slot.
    """

    rows: list[dict[str, str]] = []
    sequence = 0

    for condition, pose, count in PROTOCOL:
        observed = observed_for[(condition, pose)]

        for _ in range(count):
            sequence += 1
            rows.append(
                validate_row(
                    _row(
                        trial_id=format_trial_id(sequence),
                        condition=condition,
                        pose=pose,
                        expected_state={
                            "goggles": "SAFE",
                            "no_goggles": "VIOLATION_CONFIRMED",
                            "empty": "UNKNOWN",
                        }[condition],
                        observed_state=observed,
                        top_class=(
                            "goggles"
                            if condition == "goggles"
                            else "no_goggles"
                            if condition == "no_goggles"
                            else NO_DETECTION_CLASS
                        ),
                        false_safe="1"
                        if is_false_safe(condition, observed)
                        else "0",
                        false_alarm="1"
                        if is_false_alarm(condition, observed)
                        else "0",
                        unknown="1" if is_unknown(condition, observed) else "0",
                        threshold_preset=preset,
                    )
                )
            )

    return rows


ALL_CORRECT = {
    ("goggles", "front"): "SAFE",
    ("goggles", "left"): "SAFE",
    ("goggles", "right"): "SAFE",
    ("no_goggles", "front"): "VIOLATION_CONFIRMED",
    ("no_goggles", "left"): "VIOLATION_CONFIRMED",
    ("no_goggles", "right"): "VIOLATION_CONFIRMED",
    ("empty", "none"): "UNKNOWN",
}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_header_is_stable_and_17_columns():
    assert len(CSV_COLUMNS) == 17
    assert CSV_COLUMNS[0] == "trial_id"
    assert CSV_COLUMNS[-1] == "notes"
    assert set(CSV_COLUMNS) == set(_row())


def test_protocol_totals_45():
    assert TOTAL_TRIALS == 45
    assert sum(count for _, _, count in PROTOCOL) == 45
    assert len(THRESHOLD_PRESETS) == 2


def test_trial_id_round_trip_and_rejection():
    assert format_trial_id(1) == "V2-001"
    assert format_trial_id(45) == "V2-045"
    assert parse_trial_id("V2-007") == 7

    for bad in ("V2-000", "V2-046", "v2-001", "V2-1", "V2-0001", "001", ""):
        with pytest.raises(AcceptanceError):
            parse_trial_id(bad)


# --------------------------------------------------------------------------- #
# validate_row
# --------------------------------------------------------------------------- #


def test_validate_row_accepts_a_correct_row():
    cleaned = validate_row(_row())
    assert cleaned["trial_id"] == "V2-001"
    assert cleaned["notes"] == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"condition": "sunglasses"},
        {"pose": "back"},
        {"condition": "empty", "pose": "front", "expected_state": "UNKNOWN"},
        {"observed_state": "SAFE-ish"},
        {"expected_state": "SAFE"},
        {"trial_id": "V2-999"},
        {"weights_sha256": "deadbeef"},
        {"top_confidence": "1.5"},
        {"top_class": "person"},
        {"threshold_preset": "C"},
        {"application_fps": "-1"},
        {"duplicate_boxes": "-1"},
        {"notes": "=cmd|' /c calc'!A1"},
    ],
)
def test_validate_row_rejects_bad_values(overrides):
    with pytest.raises(AcceptanceError):
        validate_row(_row(**overrides))


def test_validate_row_rejects_duplicate_trial_id():
    with pytest.raises(AcceptanceError):
        validate_row(_row(), known_trial_ids=["V2-001"])


@pytest.mark.parametrize(
    "flag,value",
    [("false_safe", "1"), ("false_alarm", "1"), ("unknown", "1")],
)
def test_validate_row_rejects_inconsistent_derived_flags(flag, value):
    with pytest.raises(AcceptanceError):
        validate_row(_row(**{flag: value}))


def test_validate_row_rejects_missing_required_value():
    with pytest.raises(AcceptanceError):
        validate_row(_row(observed_state=""))


# --------------------------------------------------------------------------- #
# Classifiers and geometry
# --------------------------------------------------------------------------- #


def test_false_safe_and_false_alarm_truth_table():
    # The critical case: bare eyes reported as compliant.
    assert is_false_safe("no_goggles", "SAFE")
    assert is_false_safe("no_goggles", "SAFE_CLEAR_PENDING")
    assert not is_false_safe("no_goggles", "VIOLATION_CONFIRMED")
    assert not is_false_safe("no_goggles", "UNKNOWN")
    assert not is_false_safe("goggles", "SAFE")

    assert is_false_alarm("goggles", "VIOLATION_CONFIRMED")
    assert is_false_alarm("empty", "VIOLATION_CONFIRMED")
    assert not is_false_alarm("goggles", "SAFE")
    assert not is_false_alarm("no_goggles", "VIOLATION_CONFIRMED")

    assert is_unknown("empty", "UNKNOWN")
    assert not is_unknown("goggles", "SAFE")


def test_intersection_over_union():
    assert intersection_over_union((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert intersection_over_union((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    # Half-overlap: 50/150.
    assert intersection_over_union(
        (0, 0, 10, 10), (0, 0, 10, 5)
    ) == pytest.approx(0.5)


def test_count_duplicate_overlaps_counts_only_same_class():
    stacked = [("no_goggles", 0.9, (0, 0, 10, 10))] * 2
    assert count_duplicate_overlaps(stacked) == 1

    separate = [
        ("no_goggles", 0.9, (0, 0, 10, 10)),
        ("no_goggles", 0.8, (50, 50, 60, 60)),
    ]
    assert count_duplicate_overlaps(separate) == 0

    mixed = [
        ("no_goggles", 0.9, (0, 0, 10, 10)),
        ("goggles", 0.8, (0, 0, 10, 10)),
    ]
    assert count_duplicate_overlaps(mixed) == 0


# --------------------------------------------------------------------------- #
# Summary and promotion gate
# --------------------------------------------------------------------------- #


def test_summarise_empty_is_not_approved():
    summary = summarise([])
    assert summary.rows == 0
    assert summary.missing_trials == 45
    assert summary.verdict == VERDICT_NOT_APPROVED
    assert not summary.approved


def test_summarise_complete_correct_run_is_eligible():
    summary = summarise(_protocol_rows(observed_for=ALL_CORRECT))
    assert summary.rows == 45
    assert summary.missing_trials == 0
    assert summary.false_safe == 0
    assert summary.false_alarm == 0
    assert summary.goggles_safe == 20
    assert summary.no_goggles_confirmed == 20
    assert summary.empty_unknown == 5
    assert summary.verdict == VERDICT_ELIGIBLE
    assert summary.approved


def test_summarise_single_false_safe_rejects_complete_run():
    rows = _protocol_rows(observed_for=ALL_CORRECT)

    # Flip exactly one bare-eyes trial to SAFE.
    index = next(
        i
        for i, row in enumerate(rows)
        if row["condition"] == "no_goggles"
    )
    rows[index] = validate_row(
        dict(
            rows[index],
            observed_state="SAFE",
            false_safe="1",
        )
    )

    summary = summarise(rows)

    assert summary.rows == 45
    assert summary.missing_trials == 0
    assert summary.false_safe == 1
    assert summary.no_goggles_confirmed == 19
    assert summary.verdict == VERDICT_NOT_APPROVED


def test_summarise_missing_trials_rejects():
    rows = _protocol_rows(observed_for=ALL_CORRECT)[:-1]
    summary = summarise(rows)
    assert summary.rows == 44
    assert summary.missing_trials == 1
    assert summary.verdict == VERDICT_NOT_APPROVED


def test_summarise_rejects_mixed_presets():
    rows = _protocol_rows(observed_for=ALL_CORRECT)
    rows[-1] = validate_row(dict(rows[-1], threshold_preset="B"))
    summary = summarise(rows)
    assert summary.verdict == VERDICT_NOT_APPROVED


def test_summarise_rejects_declared_crashes_and_contract_failures():
    rows = _protocol_rows(observed_for=ALL_CORRECT)
    assert summarise(rows).verdict == VERDICT_ELIGIBLE
    assert summarise(rows, crashed_trials=1).verdict == VERDICT_NOT_APPROVED
    assert (
        summarise(rows, unresolved_contract_failures=1).verdict
        == VERDICT_NOT_APPROVED
    )


def test_summarise_rejects_slow_fps():
    rows = [
        validate_row(dict(row, application_fps="3.00"))
        for row in _protocol_rows(observed_for=ALL_CORRECT)
    ]
    assert summarise(rows).verdict == VERDICT_NOT_APPROVED


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def test_write_empty_schema_is_header_only(tmp_path):
    path = tmp_path / "acceptance.csv"
    write_empty_schema(path)

    text = path.read_text(encoding="utf-8")
    assert text.count("\n") == 1
    assert text.strip().split(",") == list(CSV_COLUMNS)
    assert read_rows(path) == []


def test_append_row_is_append_only(tmp_path):
    path = tmp_path / "acceptance.csv"
    write_empty_schema(path)

    seen = []

    for sequence in (1, 2, 3):
        row = validate_row(
            _row(
                trial_id=format_trial_id(sequence),
                condition="goggles",
                expected_state="SAFE",
                observed_state="SAFE",
                top_class="goggles",
            )
        )
        append_row(row, path)
        seen.append(row)

    rows = read_rows(path)

    assert [row["trial_id"] for row in rows] == ["V2-001", "V2-002", "V2-003"]
    assert rows[0] == seen[0]
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(
        CSV_COLUMNS
    )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4


def test_append_row_rejects_duplicate_trial_id(tmp_path):
    path = tmp_path / "acceptance.csv"
    write_empty_schema(path)
    append_row(validate_row(_row(trial_id="V2-001")), path)

    with pytest.raises(AcceptanceError):
        append_row(_row(trial_id="V2-001"), path)


def test_read_rows_rejects_wrong_header(tmp_path):
    path = tmp_path / "acceptance.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    with pytest.raises(AcceptanceError):
        read_rows(path)


def test_committed_csv_is_header_only():
    """The repository CSV must ship with the schema and no results."""

    from tools.v2_acceptance_schema import ACCEPTANCE_CSV_PATH

    assert ACCEPTANCE_CSV_PATH.is_file()
    assert read_rows(ACCEPTANCE_CSV_PATH) == []

    with ACCEPTANCE_CSV_PATH.open(newline="", encoding="utf-8") as handle:
        assert tuple(next(csv.reader(handle))) == CSV_COLUMNS


# --------------------------------------------------------------------------- #
# Runner helpers
# --------------------------------------------------------------------------- #


def test_next_trial_walks_the_protocol_in_order():
    assert next_trial([]) == ("V2-001", "goggles", "front")

    rows = _protocol_rows(observed_for=ALL_CORRECT)
    assert next_trial(rows[:20]) == ("V2-021", "no_goggles", "front")
    assert next_trial(rows) is None


def test_next_trial_never_reuses_a_recorded_id():
    """An out-of-order slot must not hand back an id already in the CSV."""

    rows = [
        validate_row(
            _row(
                trial_id="V2-001",
                condition="goggles",
                expected_state="SAFE",
                observed_state="SAFE",
                top_class="goggles",
            )
        ),
        # Slot filled out of order, so the slot count no longer matches
        # the number of rows in the slot.
        validate_row(
            _row(
                trial_id="V2-002",
                condition="goggles",
                expected_state="SAFE",
                observed_state="SAFE",
                top_class="goggles",
            )
        ),
    ]

    trial_id, condition, pose = next_trial(rows)

    assert trial_id == "V2-003"
    assert (condition, pose) == ("goggles", "front")


def test_build_row_derives_flags_and_expected_state(tmp_path):
    weights = tmp_path / "best_v2.pt"
    weights.write_bytes(b"weights")

    row = build_row(
        trial_id="V2-001",
        condition="no_goggles",
        pose="front",
        observed_state="SAFE",
        top_class="goggles",
        top_confidence=0.87654,
        duplicate_boxes=2,
        application_fps=31.234,
        preset="A",
        weights=weights,
        weights_digest=V2_DIGEST,
    )

    assert row["model"] == "best_v2.pt"
    assert row["expected_state"] == "VIOLATION_CONFIRMED"
    assert row["false_safe"] == "1"
    assert row["false_alarm"] == "0"
    assert row["unknown"] == "0"
    assert row["top_confidence"] == "0.8765"
    assert row["application_fps"] == "31.23"
    validate_row(row)


# --------------------------------------------------------------------------- #
# Demo trial session
# --------------------------------------------------------------------------- #


def test_threshold_preset_labels():
    from tools.live_eye_shield_demo import threshold_preset_for

    assert threshold_preset_for(0.40, 0.60, 0.55, 0.30) == "A"
    assert threshold_preset_for(0.50, 0.65, 0.60, 0.30) == "B"
    assert threshold_preset_for(0.45, 0.60, 0.55, 0.30) == "custom"


def test_trial_session_worst_state_precedence():
    from tools.live_eye_shield_demo import Observation, State, measure_frame

    def frame(top_class, confidence, box=(0, 0, 10, 10)):
        if top_class == NO_DETECTION_CLASS:
            accepted = []
        else:
            accepted = [(top_class, confidence, box)]

        return measure_frame(accepted, Observation.UNKNOWN, 0.30)

    empty = TrialSession()
    assert empty.worst_state() == "UNKNOWN"

    confirmed_wins = TrialSession()
    confirmed_wins.record(frame("goggles", 0.90), State.SAFE, 30.0)
    confirmed_wins.record(frame("no_goggles", 0.80), State.VIOLATION_PENDING, 30.0)
    confirmed_wins.record(
        frame("no_goggles", 0.70), State.VIOLATION_CONFIRMED, 30.0
    )
    # A confirmed violation is never downgraded by a later SAFE frame.
    confirmed_wins.record(frame("goggles", 0.95), State.SAFE, 30.0)

    assert confirmed_wins.worst_state() == "VIOLATION_CONFIRMED"
    assert confirmed_wins.best_confidence_class == "goggles"
    assert confirmed_wins.best_confidence == pytest.approx(0.95)
    assert confirmed_wins.max_duplicates == 0
    assert confirmed_wins.mean_fps() == pytest.approx(30.0)
    assert confirmed_wins.frames == 4

    # Documented precedence: SAFE outranks an unconfirmed pending
    # violation, so a trial that ever settled on SAFE is scored SAFE.
    safe_wins = TrialSession()
    safe_wins.record(frame("no_goggles", 0.80), State.VIOLATION_PENDING, 30.0)
    safe_wins.record(frame("goggles", 0.90), State.SAFE, 30.0)
    assert safe_wins.worst_state() == "SAFE"

    clear_pending = TrialSession()
    clear_pending.record(frame("no_goggles", 0.80), State.SAFE_CLEAR_PENDING, 30.0)
    assert clear_pending.worst_state() == "SAFE_CLEAR_PENDING"


def test_trial_session_counts_duplicate_box_stacks():
    from tools.live_eye_shield_demo import Observation, measure_frame

    box = (0, 0, 10, 10)
    diagnostics = measure_frame(
        [("no_goggles", 0.9, box), ("no_goggles", 0.8, box)],
        Observation.VIOLATION,
        0.30,
    )

    session = TrialSession()
    from tools.live_eye_shield_demo import State

    session.record(diagnostics, State.VIOLATION_PENDING, 20.0)

    assert diagnostics.accepted_count == 2
    assert diagnostics.duplicate_boxes == 1
    assert session.max_duplicates == 1


def test_trial_session_empty_is_unknown():
    session = TrialSession()
    assert session.worst_state() == "UNKNOWN"
    assert session.best_confidence_class == NO_DETECTION_CLASS
    assert session.best_confidence == 0.0
    assert session.mean_fps() == 0.0
