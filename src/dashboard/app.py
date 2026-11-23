"""Application bootstrap: one QApplication, one stylesheet, one window.

Kept separate from ``main_window`` so tests can build a window without creating
an application, and so the CLI can print ``--help`` without a display.
"""

from __future__ import annotations

import argparse
import signal as signal_module
import sys
from pathlib import Path

from . import TITLE

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Qt reads this before the first QApplication; ``offscreen`` lets CI and a
#: headless demo still exercise the whole widget tree.
OFFSCREEN = "offscreen"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_dashboard.py",
        description=(
            f"{TITLE}. A read-only operations view over the existing incident "
            "lifecycle: it drives the ESP32 through the same protocol as "
            "live_eye_shield_demo.py and never re-implements a safety rule."
        ),
    )

    parser.add_argument(
        "--demo-data",
        action="store_true",
        help="Run on deterministic fixtures: no camera and no serial port.",
    )
    parser.add_argument("--source", default="0", help="Camera index or video path.")
    parser.add_argument(
        "--weights", default="models/best_v2.pt", help="YOLO weights to load."
    )
    parser.add_argument(
        "--candidate-confidence",
        type=float,
        default=0.40,
        help="Minimum box confidence (demo preset A = 0.40).",
    )
    parser.add_argument(
        "--goggles-confidence",
        type=float,
        default=0.60,
        help="Class confidence for goggles (demo preset A = 0.60).",
    )
    parser.add_argument(
        "--no-goggles-confidence",
        type=float,
        default=0.55,
        help="Class confidence for no_goggles (demo preset A = 0.55).",
    )
    parser.add_argument("--iou", type=float, default=0.30, help="NMS IoU threshold.")
    parser.add_argument(
        "--device", default="auto", help="auto, cpu, mps or cuda:0."
    )
    parser.add_argument(
        "--serial-port",
        default=None,
        help="ESP32 port, e.g. /dev/cu.usbserial-10. Omit to run view-only.",
    )
    parser.add_argument(
        "--confirmation-seconds",
        type=float,
        default=2.0,
        help="Continuous violation required before the alarm latches.",
    )
    parser.add_argument(
        "--clearance-seconds",
        type=float,
        default=2.0,
        help="Continuous compliance required before recovery is accepted.",
    )
    parser.add_argument(
        "--max-runtime",
        type=float,
        default=0.0,
        help="Close after N seconds (0 = run until closed).",
    )
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Render without a display, for automated smoke tests.",
    )
    parser.add_argument(
        "--no-blynk",
        action="store_true",
        help="Disable the Blynk cloud bridge (monitoring/notification).",
    )

    return parser


def _parse_source(raw: str) -> int | str:
    try:
        return int(raw)
    except ValueError:
        return raw


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.offscreen:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", OFFSCREEN)

    from PySide6.QtWidgets import QApplication

    from .branding import ensure_branding_dir, header_notice
    from .main_window import MainWindow
    from .theme import STYLESHEET

    ensure_branding_dir()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Smart Eye-Shield Safety Monitor")
    app.setApplicationDisplayName(TITLE)
    app.setStyleSheet(STYLESHEET)

    window = MainWindow(
        demo_data=args.demo_data,
        source=_parse_source(args.source),
        weights=args.weights,
        candidate_confidence=args.candidate_confidence,
        goggles_confidence=args.goggles_confidence,
        no_goggles_confidence=args.no_goggles_confidence,
        iou=args.iou,
        device=args.device,
        serial_port=args.serial_port,
        confirmation_seconds=args.confirmation_seconds,
        clearance_seconds=args.clearance_seconds,
        max_runtime=args.max_runtime,
        blynk_enabled=not args.no_blynk,
    )

    notice = header_notice()

    if notice:
        window._log("operator", notice, source="operator")

    window.show()

    # Ctrl-C in the launching terminal must close the window cleanly rather than
    # leaving a half-dead Qt loop holding the serial port.
    signal_module.signal(signal_module.SIGINT, lambda *_: window.close())

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
