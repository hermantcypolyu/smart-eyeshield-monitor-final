#!/usr/bin/env python3
"""Phase 0 camera check.

Opens the configured USB webcam, captures a short burst of frames, measures the
achievable capture FPS, and optionally writes a still for manual inspection.

This script deliberately does NOT load the model: Phase 0 only proves that the
capture path and configuration work. Inference is added in Phase 3.

Usage:
    python tools/camera_check.py                # 30 frames, no file written
    python tools/camera_check.py --frames 60
    python tools/camera_check.py --save-preview out/preview.jpg
    python tools/camera_check.py --list-devices
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

MAX_CONSECUTIVE_FAILURES = 10


def list_devices(max_index: int = 6) -> int:
    import cv2

    print(f"Probing camera indices 0..{max_index - 1} (2 s each)...")
    found = 0
    for index in range(max_index):
        cap = cv2.VideoCapture(index)
        try:
            if not cap.isOpened():
                print(f"  [{index}] not available")
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                print(f"  [{index}] open — first frame {w}x{h}")
                found += 1
            else:
                print(f"  [{index}] opened but produced no frame")
        finally:
            cap.release()
    print(f"{found} usable device(s).")
    return 0 if found else 1


def run_check(
    index: int, width: int, height: int, frames: int, save_preview: Path | None
) -> int:
    try:
        import cv2
    except ImportError as exc:
        print(f"ERROR: OpenCV is not installed: {exc}", file=sys.stderr)
        print("Fix: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"ERROR: could not open camera index {index}.", file=sys.stderr)
        print(
            "Checks: is another app holding the camera? Is it connected? "
            "Try `python tools/camera_check.py --list-devices`.",
            file=sys.stderr,
        )
        return 1

    try:
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        # Not every backend reports a FOURCC, and some report 0. Only render the
        # codec when all four bytes are printable, so the output cannot show
        # mojibake like 'ÿÿÿÿ' that looks like a failure but is not.
        raw = bytes((fourcc >> (8 * i)) & 0xFF for i in range(4))
        codec = raw.decode("ascii") if raw.isascii() and raw.strip(b"\x00 ") else "n/a"
        print(
            f"Camera {index} opened (native {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
            f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}, codec {codec})."
        )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # Discard the first frames: auto-exposure and white balance need to settle
        # or the measured FPS and the preview image are unrepresentative.
        warmup = min(10, max(3, frames // 3))
        for _ in range(warmup):
            cap.read()

        timestamps: list[float] = []
        last_frame = None
        consecutive_failures = 0

        for _ in range(frames):
            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"ERROR: {consecutive_failures} consecutive frame reads "
                        "failed — camera likely disconnected or in use.",
                        file=sys.stderr,
                    )
                    return 1
                continue
            consecutive_failures = 0
            timestamps.append(time.monotonic())
            last_frame = frame

        if len(timestamps) < 2 or last_frame is None:
            print("ERROR: too few frames captured to measure FPS.", file=sys.stderr)
            return 1

        elapsed = timestamps[-1] - timestamps[0]
        fps = (len(timestamps) - 1) / elapsed if elapsed > 0 else 0.0
        mean_interval_ms = 1000.0 * elapsed / (len(timestamps) - 1)
        actual_h, actual_w = last_frame.shape[:2]

        print(
            f"Captured {len(timestamps)}/{frames} frames at {actual_w}x{actual_h}: "
            f"{fps:.1f} FPS (mean interval {mean_interval_ms:.1f} ms)."
        )
        if fps < 15:
            print(
                "WARNING: capture FPS is below the 15 FPS goal. Check lighting, "
                "USB bandwidth, and whether another app is using the camera."
            )

        if save_preview is not None:
            save_preview.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(save_preview), last_frame):
                print(f"ERROR: could not write {save_preview}", file=sys.stderr)
                return 1
            print(f"Preview written to {save_preview}")

        print("Camera check PASSED.")
        return 0
    finally:
        cap.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the USB webcam capture path.")
    parser.add_argument("--index", type=int, default=None, help="override camera index")
    parser.add_argument("--width", type=int, default=None, help="override width")
    parser.add_argument("--height", type=int, default=None, help="override height")
    parser.add_argument("--frames", type=int, default=30, help="frames to capture")
    parser.add_argument(
        "--save-preview",
        type=Path,
        default=None,
        help="write the last captured frame to this path",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="probe camera indices and exit"
    )
    args = parser.parse_args()

    if args.list_devices:
        return list_devices()

    index, width, height = args.index, args.width, args.height
    if index is None or width is None or height is None:
        # Only require config for values the caller did not override, so the tool
        # still works before config/app.yaml exists.
        try:
            import config as config_module

            cfg = config_module.load_config()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: could not load configuration: {exc}", file=sys.stderr)
            return 2
        index = cfg.camera.index if index is None else index
        width = cfg.camera.width if width is None else width
        height = cfg.camera.height if height is None else height

    if args.frames < 2:
        print("ERROR: --frames must be at least 2.", file=sys.stderr)
        return 2

    return run_check(index, width, height, args.frames, args.save_preview)


if __name__ == "__main__":
    sys.exit(main())
