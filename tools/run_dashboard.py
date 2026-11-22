#!/usr/bin/env python3
"""Launch the Smart Eye-Shield Safety Operations Centre.

Example - full live demo, same thresholds as the terminal demo::

    python tools/run_dashboard.py \
        --source 0 \
        --weights models/best_v2.pt \
        --serial-port /dev/cu.usbserial-10

Example - rehearsal with no hardware attached::

    python tools/run_dashboard.py --demo-data

The dashboard is a view. It never retrains, never changes a threshold and never
silences an alarm on the way out, so a red lamp stays red after the window
closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dashboard.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
