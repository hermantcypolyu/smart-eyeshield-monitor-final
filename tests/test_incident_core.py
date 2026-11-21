"""Tests for the firmware's text-protocol incident core.

``firmware/esp32_eye_shield_monitor/incident_core.h`` holds the ESP32's own
command parsing, latch rules and reset gating as plain C++ with no Arduino
dependency. That was a deliberate choice: it means the code under test here is
byte-for-byte the code that ships to the board, not a Python re-implementation
that could drift from it.

The header is compiled with the host compiler into a tiny stdin/stdout driver
(``firmware/esp32_eye_shield_monitor/test/incident_core_harness.cpp``) and each
test feeds it a command script. If no C++ compiler is available the module is
skipped rather than failing, so the suite still runs on a machine without one —
but CI and this repository's own verification do have one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = REPO_ROOT / "firmware" / "esp32_eye_shield_monitor"
HEADER = FIRMWARE_DIR / "incident_core.h"
HARNESS = FIRMWARE_DIR / "test" / "incident_core_harness.cpp"


def _find_compiler() -> str | None:
    for name in ("clang++", "g++", "c++"):
        found = shutil.which(name)
        if found:
            return found
    return None


COMPILER = _find_compiler()

pytestmark = pytest.mark.skipif(
    COMPILER is None or not HEADER.exists() or not HARNESS.exists(),
    reason="host C++ compiler or firmware core sources unavailable",
)


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile the harness once per session and reuse the binary."""

    out_dir = tmp_path_factory.mktemp("incident_core")
    binary = out_dir / "incident_core_harness"

    result = subprocess.run(
        [
            COMPILER,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O1",
            str(HARNESS),
            "-I",
            str(FIRMWARE_DIR),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
    )

    # -Werror matters: a firmware warning is a real review item, so the tests
    # must not pass while the header compiles dirty.
    assert result.returncode == 0, (
        "firmware core failed to compile:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    return binary


def run(binary: Path, script: list[str]) -> list[str]:
    """Feed command lines to the harness and return one reply line each."""

    payload = "\n".join(script) + "\n"
    result = subprocess.run(
        [str(binary)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


#: Lines the harness emits for '#'-free directives (@TICK/@YELLOW). They are
#: acknowledgements of test setup, not replies to a command.
DIRECTIVE_PREFIXES = ("TICK ", "YELLOW ")


def replies(lines: list[str]) -> list[str]:
    """Drop directive echoes so index 0 is always the first real reply."""

    return [line for line in lines if not line.startswith(DIRECTIVE_PREFIXES)]


def reply_for(line: str) -> str:
    """Strip the '| state=... ' suffix the harness appends for diagnostics."""

    return line.split(" | state=")[0]


def state_of(line: str) -> str:
    for part in line.split("| "):
        if part.startswith("state="):
            return part.split()[0].removeprefix("state=")
    raise AssertionError(f"no state in harness output: {line!r}")


def flag_of(line: str, name: str) -> bool:
    for part in line.split():
        if part.startswith(f"{name}="):
            return part.split("=", 1)[1] == "1"
    raise AssertionError(f"no {name} flag in harness output: {line!r}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_alarm_parses(harness):
    assert reply_for(run(harness, ["ALARM"])[0]) == "OK ALARM_LATCHED"


@pytest.mark.parametrize(
    "text",
    ["SAFE", "safe", "Safe", "  SAFE  ", "\tSAFE\t"],
)
def test_parsing_is_case_and_whitespace_insensitive(harness, text):
    assert reply_for(run(harness, [text])[0]) == "OK SAFE"


def test_acknowledge_alias_ack(harness):
    lines = run(harness, ["ALARM", "ACK"])
    assert reply_for(lines[1]) == "OK ACKNOWLEDGED"


def test_unknown_command_is_rejected_not_ignored(harness):
    assert reply_for(run(harness, ["NOT_A_COMMAND"])[0]) == "ERR UNKNOWN_COMMAND"


def test_blank_line_is_silent(harness):
    assert run(harness, [""])[0] == "NONE"


def test_reset_ready_accepts_optional_incident_id(harness):
    lines = run(harness, ["ALARM", "ACKNOWLEDGE", "RESET_READY 1"])
    assert reply_for(lines[2]) == "OK RESET_READY"


def test_reset_ready_rejects_stale_incident_id(harness):
    """A token minted for a previous episode must not reopen this one."""

    lines = run(harness, ["ALARM", "ACKNOWLEDGE", "RESET_READY 99"])
    assert reply_for(lines[2]) == "ERR RESET_REJECTED NOT_SAFE_RECOVERED"


def test_overlong_line_is_truncated_safely(harness):
    """A runaway 400-char line must not overflow the parse buffer."""

    line = "A" * 400
    assert reply_for(run(harness, [line])[0]) == "ERR UNKNOWN_COMMAND"


# ---------------------------------------------------------------------------
# Latch rules
# ---------------------------------------------------------------------------


def test_alarm_sounds_red_and_buzzer(harness):
    line = run(harness, ["ALARM"])[0]
    assert flag_of(line, "red") is True
    assert flag_of(line, "buzzer") is True
    assert flag_of(line, "green") is False


def test_alarm_is_idempotent_within_one_episode(harness):
    lines = run(harness, ["ALARM", "ALARM", "ALARM"])
    assert all(reply_for(line) == "OK ALARM_LATCHED" for line in lines)
    # Same incident: the repeat must not allocate a second id.
    assert "incident=1" in lines[-1]


def test_safe_is_rejected_while_incident_active(harness):
    lines = run(harness, ["ALARM", "SAFE"])
    assert reply_for(lines[1]) == "ERR SAFE_REJECTED INCIDENT_ACTIVE"
    assert flag_of(lines[1], "red") is True


def test_safe_cannot_bypass_latch_however_often_repeated(harness):
    lines = run(harness, ["ALARM"] + ["SAFE"] * 50)
    assert flag_of(lines[-1], "red") is True
    assert state_of(lines[-1]) == "ALARM_LATCHED"


def test_silence_clears_buzzer_only(harness):
    lines = run(harness, ["ALARM", "SILENCE"])
    assert reply_for(lines[1]) == "OK SILENCED"
    assert flag_of(lines[1], "red") is True
    assert flag_of(lines[1], "buzzer") is False


def test_silence_without_incident_rejected(harness):
    assert reply_for(run(harness, ["SILENCE"])[0]) == "ERR SILENCE_REJECTED NO_INCIDENT"


# ---------------------------------------------------------------------------
# Acknowledge and reset gating
# ---------------------------------------------------------------------------


def test_acknowledge_keeps_red_on(harness):
    lines = run(harness, ["ALARM", "ACKNOWLEDGE"])
    assert reply_for(lines[1]) == "OK ACKNOWLEDGED"
    assert flag_of(lines[1], "red") is True
    assert flag_of(lines[1], "buzzer") is False


def test_acknowledge_without_incident_rejected(harness):
    assert (
        reply_for(run(harness, ["ACKNOWLEDGE"])[0])
        == "ERR ACKNOWLEDGE_REJECTED NO_INCIDENT"
    )


def test_reset_without_incident_rejected(harness):
    assert reply_for(run(harness, ["RESET"])[0]) == "ERR RESET_REJECTED NO_INCIDENT"


def test_reset_requires_acknowledge(harness):
    """Reset must not work straight from a fresh alarm."""

    lines = run(harness, ["ALARM", "RESET"])
    assert reply_for(lines[1]) == "ERR RESET_REJECTED NOT_ACKNOWLEDGED"
    assert flag_of(lines[1], "red") is True


def test_reset_requires_safe_recovered_token(harness):
    """Acknowledge alone is not enough: the laptop must have seen recovery."""

    lines = run(harness, ["ALARM", "ACKNOWLEDGE", "RESET"])
    assert reply_for(lines[2]) == "ERR RESET_REJECTED NOT_SAFE_RECOVERED"


def test_reset_happy_path_returns_to_safe(harness):
    """All five preconditions met: incident, ack, token, safe, live link."""

    lines = replies(
        run(
            harness,
            [
                "@TICK 1000",
                "HEARTBEAT",
                "ALARM",
                "ACKNOWLEDGE",
                "RESET_READY 1",
                "RESET",
            ],
        )
    )
    assert reply_for(lines[4]) == "OK RESET_ACCEPTED"
    assert state_of(lines[4]) == "SAFE"
    assert flag_of(lines[4], "green") is True
    assert flag_of(lines[4], "red") is False
    assert flag_of(lines[4], "buzzer") is False
    assert "incident=0" in lines[4]


def test_heartbeat_then_reset_accepted(harness):
    lines = replies(
        run(
            harness,
            ["@TICK 500", "HEARTBEAT", "ALARM", "ACKNOWLEDGE", "RESET_READY 1", "RESET"],
        )
    )
    assert reply_for(lines[4]) == "OK RESET_ACCEPTED"


def test_reset_rejected_when_heartbeat_stale(harness):
    """A dead laptop must not be able to claim a clean reset.

    The firmware heard from the host at t=0, then the host went quiet. At
    t=10 s the link is stale, so RESET is refused even though every other
    condition holds.
    """

    lines = replies(
        run(
            harness,
            [
                "@TICK 0",
                "HEARTBEAT",
                "ALARM",
                "ACKNOWLEDGE",
                "RESET_READY 1",
                "@TICK 10000",
                "RESET",
            ],
        )
    )
    assert reply_for(lines[4]) == "ERR RESET_REJECTED LINK_OR_HEALTH_INVALID"
    assert flag_of(lines[4], "red") is True


def test_reset_never_seen_heartbeat_is_rejected(harness):
    lines = run(harness, ["ALARM", "ACKNOWLEDGE", "RESET_READY 1", "RESET"])
    assert reply_for(lines[3]) == "ERR RESET_REJECTED LINK_OR_HEALTH_INVALID"


# ---------------------------------------------------------------------------
# ALL_OFF must never be a shortcut
# ---------------------------------------------------------------------------


def test_all_off_refused_during_incident(harness):
    lines = run(harness, ["ALARM", "ALL_OFF"])
    assert reply_for(lines[1]) == "ERR ALL_OFF_DIAGNOSTIC_ONLY INCIDENT_ACTIVE"
    assert flag_of(lines[1], "red") is True


def test_all_off_allowed_when_clean(harness):
    lines = run(harness, ["ALL_OFF"])
    assert reply_for(lines[0]) == "OK ALL_OFF"
    assert flag_of(lines[0], "red") is False
    assert flag_of(lines[0], "green") is False
    assert flag_of(lines[0], "buzzer") is False


# ---------------------------------------------------------------------------
# Re-arm behaviour
# ---------------------------------------------------------------------------


def test_renewed_alarm_after_acknowledge_rearms_buzzer(harness):
    lines = run(harness, ["ALARM", "ACKNOWLEDGE", "ALARM"])
    assert reply_for(lines[2]) == "OK ALARM_LATCHED"
    assert flag_of(lines[2], "buzzer") is True
    # Same episode: the re-arm must not invent a new incident id.
    assert "incident=1" in lines[2]


def test_renewed_alarm_invalidates_previous_reset_token(harness):
    """A stale RESET_READY must not survive a re-arm."""

    lines = run(
        harness,
        ["ALARM", "ACKNOWLEDGE", "RESET_READY 1", "ALARM", "RESET"],
    )
    assert reply_for(lines[4]) == "ERR RESET_REJECTED NOT_ACKNOWLEDGED"


def test_second_incident_gets_new_id(harness):
    lines = replies(
        run(
            harness,
            [
                "@TICK 100",
                "ALARM",
                "ACKNOWLEDGE",
                "RESET_READY 1",
                "HEARTBEAT",
                "RESET",
                "ALARM",
            ],
        )
    )
    assert reply_for(lines[4]) == "OK RESET_ACCEPTED"
    assert "incident=2" in lines[5]


# ---------------------------------------------------------------------------
# Yellow lamp
# ---------------------------------------------------------------------------


def test_yellow_disabled_by_default(harness):
    """docs/hardware_bringup.md records the yellow input as NOT CONNECTED."""

    assert reply_for(run(harness, ["YELLOW_STEADY"])[0]) == "ERR YELLOW_DISABLED"


def test_yellow_accepts_commands_once_enabled(harness):
    lines = run(harness, ["@YELLOW 1", "YELLOW_OFF", "YELLOW_STEADY"])
    assert lines[0] == "YELLOW 1"
    # Yellow replies are advisory in this stage; the important part is that the
    # disabled error is gone.
    assert "YELLOW_DISABLED" not in lines[1]
    assert "YELLOW_DISABLED" not in lines[2]


# ---------------------------------------------------------------------------
# Output invariants
# ---------------------------------------------------------------------------


def test_green_and_red_are_never_both_on(harness):
    """The sketch's applyOutputs forbids it; the core must never ask for it."""

    script = [
        "SAFE",
        "ALARM",
        "SILENCE",
        "ACKNOWLEDGE",
        "RESET_READY 1",
        "HEARTBEAT",
        "RESET",
        "ALARM",
    ]
    for line in run(harness, script):
        if line.startswith(("OK", "ERR")):
            assert not (
                flag_of(line, "green") and flag_of(line, "red")
            ), f"green+red requested together: {line}"


def test_silenced_and_acknowledged_states_are_red_not_green(harness):
    for command in ("SILENCE", "ACKNOWLEDGE"):
        lines = run(harness, ["ALARM", command])
        assert flag_of(lines[1], "red") is True
        assert flag_of(lines[1], "green") is False


def test_rejected_commands_do_not_change_outputs(harness):
    """A refusal is a no-op, so the lamp cannot flicker on a bad command."""

    lines = replies(run(harness, ["ALARM", "SAFE", "ALL_OFF", "RESET"]))
    assert len(lines) == 4
    alarm_flags = (flag_of(lines[0], "red"), flag_of(lines[0], "buzzer"))
    for line in lines[1:]:
        assert (flag_of(line, "red"), flag_of(line, "buzzer")) == alarm_flags
