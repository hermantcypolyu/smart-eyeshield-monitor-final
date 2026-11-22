"""Shared fixtures for the dashboard tests.

Qt is put into ``offscreen`` mode before any widget is built, so the suite runs
without a display and without stealing focus on a machine where the developer
happens to have the real dashboard open.

Nothing here touches a camera, a serial port or the network: the dashboard's own
adapters are deterministic, and these tests only ever exercise them.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    """One ``QApplication`` for the whole session.

    Qt aborts if a second one is created, so this is session-scoped and every
    widget in the suite shares it.
    """

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    yield app


@pytest.fixture
def window(qapp):
    """A fully built window in demo-data mode, closed on teardown.

    ``autostart=False`` with ``demo_data=True`` starts nothing at all: the
    fixture storyline is driven by the test, not by a background camera thread.
    """

    from src.dashboard.main_window import MainWindow

    widget = MainWindow(demo_data=True, autostart=False)
    widget.show()

    yield widget

    widget.close()
    qapp.processEvents()


@pytest.fixture
def live_window(qapp):
    """A window wired for live operation with no workers started.

    This is the configuration the safety tests need: the real protocol path,
    the real lifecycle, and a fake port the test controls.
    """

    from src.dashboard.main_window import MainWindow

    widget = MainWindow(demo_data=False, autostart=False)
    # Simulate a healthy camera: these tests are about the protocol path, not
    # about a webcam, and a window with no frames is correctly in FAULT.
    widget._camera_ok = True
    widget._model_ok = True
    widget._serial_ok = True
    widget.paint_timer.stop()
    widget.poll_timer.stop()

    yield widget

    widget.close()
    qapp.processEvents()
