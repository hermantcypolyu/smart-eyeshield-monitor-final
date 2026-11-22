"""PySide6 operations-centre dashboard for the Smart Eye-Shield Safety Monitor.

This package is a *presentation layer*. It reads the incident lifecycle, the
temporal filter and the firmware protocol through their existing public APIs and
never re-implements their rules, so the dashboard cannot disagree with the demo
it is displaying.

Importing the package is cheap and Qt-free; ``src.dashboard.app`` creates the
``QApplication`` and ``src.dashboard.main_window.MainWindow`` is the window.
"""

from __future__ import annotations

__all__ = ["TITLE"]

#: Branding title, re-exported so ``tools/run_dashboard.py`` can print it without
#: importing a Qt module before a display exists.
TITLE = "Smart Eye-Shield Safety Operations Centre"
