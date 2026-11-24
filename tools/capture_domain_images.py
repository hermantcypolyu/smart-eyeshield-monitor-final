"""Capture target-domain webcam images for model fine-tuning."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--class-name",
        required=True,
        choices=("goggles", "no_goggles"),
    )

    parser.add_argument(
        "--source",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--target",
        type=int,
        default=30,
    )

    args = parser.parse_args()

    output = Path("data/domain_adaptation") / args.class_name
    output.mkdir(parents=True, exist_ok=True)

    existing = sorted(output.glob("*.jpg"))
    count = len(existing)

    camera = cv2.VideoCapture(args.source)

    if not camera.isOpened():
        raise SystemExit(
            f"ERROR: cannot open camera {args.source}"
        )

    print("=" * 60)
    print(f"Class: {args.class_name}")
    print(f"Output: {output}")
    print("SPACE: save one image")
    print("Q or ESC: quit")
    print("=" * 60)

    try:
        while True:
            ok, frame = camera.read()

            if not ok:
                print("ERROR: camera frame unavailable")
                break

            preview = frame.copy()

            lines = [
                f"CLASS: {args.class_name}",
                f"SAVED: {count}/{args.target}",
                "SPACE: save | Q: quit",
                "Change pose/distance before each image",
            ]

            y = 35

            for line in lines:
                cv2.putText(
                    preview,
                    line,
                    (20, y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                y += 32

            cv2.imshow(
                "Domain Adaptation Capture",
                preview,
            )

            key = cv2.waitKey(1) & 0xFF

            if key == ord(" "):
                filename = (
                    f"{args.class_name}_webcam_{count:03d}.jpg"
                )

                destination = output / filename

                if cv2.imwrite(str(destination), frame):
                    print(f"Saved: {destination}")
                    count += 1
                    time.sleep(0.20)
                else:
                    print(f"ERROR: could not save {destination}")

                if count >= args.target:
                    print("Target reached.")
                    break

            elif key in (ord("q"), 27):
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()

    print(f"Finished. Total saved: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
