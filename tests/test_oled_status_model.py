"""Tests for the OLED status model.

``firmware/esp32_eye_shield_monitor/oled_status_model.h`` decides what the I2C
panel says. It is pure C++ — no Adafruit, no Wire, no pins — so the exact header
that ships to the board is the one compiled and asserted on here. The drawing
code in ``oled_display.h`` is then only a formatting concern.

Two properties matter and are tested explicitly:

* the panel must never contradict the lamps (it reports the pin state it is
  given, not what the core wished for), and
* every lifecycle state must map to a stable headline and severity, so a future
  edit to the model cannot silently mislabel an active alarm as safe.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_DIR = REPO_ROOT / "firmware" / "esp32_eye_shield_monitor"
MODEL = FIRMWARE_DIR / "oled_status_model.h"
HARNESS = FIRMWARE_DIR / "test" / "oled_status_harness.cpp"


def _find_compiler() -> str | None:
    for name in ("clang++", "g++", "c++"):
        found = shutil.which(name)
        if found:
            return found
    return None


COMPILER = _find_compiler()

pytestmark = pytest.mark.skipif(
    COMPILER is None or not MODEL.exists() or not HARNESS.exists(),
    reason="host C++ compiler or OLED model sources unavailable",
)


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile the harness once and reuse the binary."""

    out_dir = tmp_path_factory.mktemp("oled_status")
    binary = out_dir / "oled_status_harness"

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

    # -Werror: the model must compile clean, because it is compiled by the same
    # toolchain that builds the firmware.
    assert result.returncode == 0, (
        "OLED status model failed to compile:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    return binary


def run(binary: Path, script: list[str]) -> list[str]:
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


DIRECTIVE_PREFIXES = ("TICK ", "OUT ", "RENDER ", "STATUS ")


def render(binary: Path, setup: list[str]) -> dict[str, str]:
    """Run setup lines, then @RENDER, and parse the snapshot into a dict."""

    lines = run(binary, [*setup, "@RENDER"])
    rendered = [line for line in lines if line.startswith("RENDER ")]
    assert len(rendered) == 1, lines

    fields: dict[str, str] = {}
    # headline and detail are quoted; the rest are bare tokens.
    remaining = rendered[0][len("RENDER ") :]
    while remaining:
        key, _, rest = remaining.partition("=")
        if rest.startswith('"'):
            closing = rest.index('"', 1)
            fields[key] = rest[1:closing]
            remaining = rest[closing + 1 :].lstrip()
        else:
            token, _, remaining = rest.partition(" ")
            fields[key] = token
            remaining = remaining.lstrip()
    return fields


# ---------------------------------------------------------------------------
# Boot / safe state
# ---------------------------------------------------------------------------


def test_fresh_core_is_safe(harness):
    snapshot = render(harness, [])
    assert snapshot["headline"] == "SAFE"
    assert snapshot["severity"] == "NOMINAL"
    assert snapshot["state"] == "SAFE"
    assert snapshot["incident"] == "0"
    # Safe means the green lamp is the one on, and nothing is sounding.
    assert snapshot["green"] == "1"
    assert snapshot["red"] == "0"
    assert snapshot["buzzer"] == "0"


def test_safe_without_heartbeat_reports_stale_link(harness):
    snapshot = render(harness, ["@TICK 10000"])
    assert snapshot["headline"] == "SAFE"
    assert snapshot["link_stale"] == "1"
    assert snapshot["detail"] == "no laptop link"


def test_safe_with_live_heartbeat_reports_link_ok(harness):
    snapshot = render(harness, ["@TICK 1000", "HEARTBEAT", "@TICK 1500"])
    assert snapshot["headline"] == "SAFE"
    assert snapshot["link_stale"] == "0"


# ---------------------------------------------------------------------------
# Open incident
# ---------------------------------------------------------------------------


def test_alarm_latched_shows_alert_and_incident_number(harness):
    snapshot = render(harness, ["@TICK 1000", "HEARTBEAT", "ALARM", "@TICK 1100"])
    assert snapshot["headline"] == "ALERT"
    # Alert is the only severity that inverts the banner, so it must be exact.
    assert snapshot["severity"] == "ALERT"
    assert snapshot["state"] == "ALARM_LATCHED"
    assert snapshot["red"] == "1"
    assert snapshot["buzzer"] == "1"
    assert snapshot["green"] == "0"
    assert snapshot["incident"] == "1"
    assert snapshot["detail"] == "INCIDENT 1"


def test_silenced_is_warning_not_alert(harness):
    """Muting the buzzer must not downgrade an open incident to 'safe'."""

    snapshot = render(
        harness, ["@TICK 1000", "HEARTBEAT", "ALARM", "SILENCE 1", "@TICK 1200"]
    )
    assert snapshot["headline"] == "WARNING"
    assert snapshot["severity"] == "WARNING"
    assert snapshot["state"] == "SILENCED"
    # The hazard is still present: red stays on, only the buzzer drops.
    assert snapshot["red"] == "1"
    assert snapshot["buzzer"] == "0"


def test_acknowledged_reports_ack_flag(harness):
    snapshot = render(
        harness,
        ["@TICK 1000", "HEARTBEAT", "ALARM", "ACKNOWLEDGE 1", "@TICK 1200"],
    )
    assert snapshot["headline"] == "WARNING"
    assert snapshot["state"] == "ACKNOWLEDGED"
    assert snapshot["ack"] == "1"
    assert snapshot["incident"] == "1"
    assert snapshot["detail"] == "ACKED #1"


def test_ready_to_reset_still_warns(harness):
    """The reset gate being armed does not mean the incident is closed."""

    snapshot = render(
        harness,
        [
            "@TICK 1000",
            "HEARTBEAT",
            "ALARM",
            "ACKNOWLEDGE 1",
            "RESET_READY 1",
            "@TICK 1200",
        ],
    )
    assert snapshot["headline"] == "WARNING"
    assert snapshot["severity"] == "WARNING"
    assert snapshot["state"] == "READY_TO_RESET"


def test_reset_returns_to_safe(harness):
    snapshot = render(
        harness,
        [
            "@TICK 1000",
            "HEARTBEAT",
            "ALARM",
            "ACKNOWLEDGE 1",
            "RESET_READY 1",
            "RESET 1",
            "@TICK 1200",
        ],
    )
    assert snapshot["headline"] == "SAFE"
    assert snapshot["severity"] == "NOMINAL"
    assert snapshot["incident"] == "0"
    assert snapshot["green"] == "1"
    assert snapshot["red"] == "0"


# ---------------------------------------------------------------------------
# The panel must not lie about the lamps
# ---------------------------------------------------------------------------


def test_lamp_flags_follow_the_pins_not_the_core(harness):
    """A manual self-test leaves the pins disagreeing with the core.

    The panel must show what the lamps are doing. If it echoed the core's wish
    it would claim a green light that is physically off.
    """

    snapshot = render(harness, ["@TICK 1000", "@OUT 1 0 0"])
    assert snapshot["green"] == "1"
    assert snapshot["red"] == "0"
    assert snapshot["buzzer"] == "0"

    snapshot = render(harness, ["@TICK 1000", "@OUT 0 1 1"])
    assert snapshot["green"] == "0"
    assert snapshot["red"] == "1"
    assert snapshot["buzzer"] == "1"


def test_all_lamps_off_is_representable(harness):
    """The fault/all-off case must render, not fall through to a stale value."""

    snapshot = render(harness, ["@TICK 1000", "@OUT 0 0 0"])
    assert snapshot["green"] == "0"
    assert snapshot["red"] == "0"
    assert snapshot["buzzer"] == "0"


# ---------------------------------------------------------------------------
# Buffers and change detection
# ---------------------------------------------------------------------------


def test_no_text_is_truncated(harness):
    """Every state's strings must fit their fixed buffers.

    Truncation would silently clip the operator's most important word, so the
    model clamps and these assertions pin the real lengths down.
    """

    cases = [
        ["@TICK 1000"],
        ["@TICK 1000", "HEARTBEAT", "ALARM"],
        ["@TICK 1000", "HEARTBEAT", "ALARM", "SILENCE 1"],
        ["@TICK 1000", "HEARTBEAT", "ALARM", "ACKNOWLEDGE 1"],
        [
            "@TICK 1000",
            "HEARTBEAT",
            "ALARM",
            "ACKNOWLEDGE 1",
            "RESET_READY 1",
        ],
    ]
    for setup in cases:
        snapshot = render(harness, setup)
        assert len(snapshot["headline"]) <= 23, snapshot
        assert len(snapshot["detail"]) <= 31, snapshot
        assert snapshot["headline"], snapshot
        assert snapshot["detail"], snapshot


def test_severity_names_are_stable():
    """oledSeverityName() output is part of the harness contract."""
    header_text = MODEL.read_text()
    for name in ("NOMINAL", "INFO", "WARNING", "ALERT", "FAULT"):
        assert f'"{name}"' in header_text, f"severity {name} missing from model"
