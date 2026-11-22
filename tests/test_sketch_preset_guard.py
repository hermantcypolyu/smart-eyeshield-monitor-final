"""Source-level guard for the sketch defect that turned a latched alarm green.

The bug lived in ``esp32_eye_shield_monitor.ino``, which cannot be compiled on
the host (it needs the Arduino core), so ``test_incident_core.py`` cannot reach
it. These assertions read the sketch as text to pin the two properties whose
loss caused the reversion:

1. ``SAFE``, ``ALARM`` and ``SILENCE`` are not entries in ``PRESETS[]``. While
   they were, ``handleCommand()`` matched them before calling the lifecycle
   parser, so they painted the pins and left ``IncidentCore`` in ``Safe``. The
   next heartbeat then re-applied ``outputs()`` and the green lamp came back.
2. A preset is refused while an incident is open, and that refusal happens
   before any preset handler can run.

This is deliberately a text check rather than a behavioural one: it is the only
automated way to catch a reintroduction of the bypass without a board.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKETCH = (
    Path(__file__).resolve().parent.parent
    / "firmware"
    / "esp32_eye_shield_monitor"
    / "esp32_eye_shield_monitor.ino"
)

pytestmark = pytest.mark.skipif(
    not SKETCH.exists(), reason="sketch source not available"
)

SOURCE = SKETCH.read_text()


def _preset_names() -> set[str]:
    """The uppercase command names declared in the ``PRESETS[]`` initialiser."""

    # Match the initialiser only, so a name mentioned in a comment or in
    # printHelp()'s text cannot satisfy the assertion.
    match = re.search(r"PRESETS\[\]\s*=\s*\{(.*?)\};", SOURCE, re.S)
    assert match, "PRESETS[] initialiser not found"
    return set(re.findall(r'\{\s*"([A-Z_]+)"', match.group(1)))


@pytest.mark.parametrize("name", ["SAFE", "ALARM", "SILENCE"])
def test_lifecycle_words_are_not_pin_presets(name):
    assert name not in _preset_names()


def test_presets_are_still_available_for_bring_up():
    """The fix must remove the bypass, not the diagnostic pin control."""

    assert {"ALL_OFF", "GREEN_ON", "RED_ON", "BUZZER_OFF"} <= _preset_names()


def test_presets_are_refused_while_an_incident_is_open():
    # The guard, i.e. the incident-active branch that looks up a preset only to
    # refuse it. It must reply with the refusal and return before the applying
    # lookup below can run.
    guard = SOURCE.find(
        "if (g_core.incident_active()) {\n    const uint8_t blocked = findPreset(raw);"
    )
    assert guard != -1, "no preset refusal guard while an incident is open"

    dispatch = SOURCE.find("const uint8_t preset = findPreset(raw);")
    assert dispatch != -1, "handleCommand() no longer dispatches presets"
    assert guard < dispatch, "the guard must precede the preset dispatch"

    guarded = SOURCE[guard:dispatch]
    assert "kErrPresetIncidentActive" in guarded
    assert "return" in guarded


def test_sketch_prints_a_machine_readable_status_line():
    """The laptop correlates on STATUS, so the prefix must be exact."""

    assert "formatStatus" in SOURCE
    assert re.search(r'F\("STATUS "\)|"STATUS "', SOURCE), (
        "STATUS line is not prefixed with 'STATUS '"
    )
