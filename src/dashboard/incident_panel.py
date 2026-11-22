"""Incident panel: the open incident's evidence and its confirmation numbers.

Kept separate from the safety state because the two answer different questions.
The state panel says *what is true now*; this one says *what happened* - which
incident, when it was confirmed, how long it took, and how many times it re-armed.

The confirmation duration is shown as the value frozen at latch time, never a
live recomputation. That was a real defect at frame level: a timer that kept
being recomputed displayed ``0.00`` for a confirmed incident, which reads as "no
evidence" when the evidence was exactly 2.00 s.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from . import theme
from .state_models import DashboardSnapshot
from .widgets import Card, KeyValueRow, StatusPill, divider


class IncidentPanel(Card):
    """Open incident identity, timings and re-arm count."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Incident record",
            caption="The confirmation duration is the value frozen when the "
            "incident latched. It is never recomputed from the current frame.",
            parent=parent,
        )

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        self.status_pill = StatusPill("NO OPEN INCIDENT", theme.GREY)
        header_layout.addWidget(self.status_pill, 0, Qt.AlignLeft)
        self.body.addWidget(header)

        self.rows = {
            "id": KeyValueRow("Incident id"),
            "confirmed_after": KeyValueRow("Confirmed after"),
            "pending": KeyValueRow("Pending now"),
            "clear": KeyValueRow("Clearance elapsed"),
            "rearm": KeyValueRow("Re-arms"),
            "acknowledged": KeyValueRow("Acknowledged"),
            "recovered": KeyValueRow("Recovery proven"),
            "violation_ratio": KeyValueRow("Violation ratio"),
        }

        for row in self.rows.values():
            self.body.addWidget(row)

        self.body.addWidget(divider())

        note = QLabel(
            "Acknowledge silences the buzzer. It never restores green - only an "
            "accepted reset does that, and only after every precondition holds."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.body.addWidget(note)
        self.body.addStretch(1)

    def update_from(self, snapshot: DashboardSnapshot) -> None:
        lifecycle = snapshot.lifecycle
        incident = lifecycle.incident_id

        if incident is None:
            self.status_pill.set_status("NO OPEN INCIDENT", theme.GREY)
            self.rows["id"].set_value("none", accent=theme.TEXT_MUTED)

            # Clear the incident-scoped numbers so a closed incident cannot leave
            # its timings on screen looking current.
            for key in ("confirmed_after", "pending", "clear"):
                self.rows[key].set_value("--")
            self.rows["rearm"].set_value(str(lifecycle.rearm_count))
            self.rows["acknowledged"].set_value("--")
            self.rows["recovered"].set_value("--")
            self.rows["violation_ratio"].set_value(
                f"{lifecycle.violation_ratio * 100:.0f}%"
            )
            return

        self.status_pill.set_status(f"INCIDENT {incident} OPEN", theme.RED)
        self.rows["id"].set_value(str(incident), accent=theme.RED)

        self.rows["confirmed_after"].set_value(
            (
                f"{lifecycle.confirmed_after_seconds:.2f} s"
                if lifecycle.confirmed_after_seconds is not None
                else "--"
            ),
            accent=theme.RED,
        )

        self.rows["pending"].set_value(
            (
                f"{lifecycle.pending_elapsed:.2f} s"
                if lifecycle.pending_elapsed is not None
                else "--"
            ),
            accent=theme.AMBER if lifecycle.pending_elapsed is not None else None,
        )

        self.rows["clear"].set_value(
            (
                f"{lifecycle.safe_clear_elapsed:.2f} s"
                if lifecycle.safe_clear_elapsed is not None
                else "--"
            ),
            accent=theme.CYAN if lifecycle.safe_clear_elapsed is not None else None,
        )

        self.rows["rearm"].set_value(
            str(lifecycle.rearm_count),
            accent=theme.AMBER if lifecycle.rearm_count else None,
        )
        self.rows["acknowledged"].set_value(
            "yes" if lifecycle.acknowledged else "no",
            accent=theme.ORANGE if lifecycle.acknowledged else theme.TEXT_MUTED,
        )
        self.rows["recovered"].set_value(
            "yes" if lifecycle.safe_recovered else "no",
            accent=theme.CYAN if lifecycle.safe_recovered else theme.TEXT_MUTED,
        )
        self.rows["violation_ratio"].set_value(
            f"{lifecycle.violation_ratio * 100:.0f}%"
            f"  (needs {snapshot.gauge.violation_ratio_required * 100:.0f}%)"
        )
