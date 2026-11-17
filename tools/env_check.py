#!/usr/bin/env python3
"""One-command environment check (Phase 0 acceptance gate).

Verifies Python version, third-party imports, config validity, and the
project's directory layout. Prints every failure rather than stopping at the
first one, so a broken environment can be repaired in one pass.

Usage:
    python tools/env_check.py            # full check
    python tools/env_check.py --quiet    # only failures + one summary line
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import platform
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

MIN_PYTHON = (3, 10)
MAX_PYTHON_EXCLUSIVE = (3, 13)

REQUIRED_MODULES = {
    "yaml": "PyYAML",
    "numpy": "numpy",
    "cv2": "opencv-python",
    "serial": "pyserial",
    "dotenv": "python-dotenv",
    "pytest": "pytest",
    "torch": "torch",
    "ultralytics": "ultralytics",
}

REQUIRED_DIRS = [
    "config",
    "data/raw",
    "data/reviewed",
    "data/splits",
    "data/test_cases",
    "docs",
    "experiments",
    "firmware/esp32_eye_shield_monitor",
    "models",
    "src",
    "tests",
    "tests/fixtures",
    "tools",
    "training",
]

REQUIRED_FILES = [
    "README.md",
    "plan.md",
    ".gitignore",
    ".env.example",
    "requirements.txt",
    "config/app.example.yaml",
    "config/classes.yaml",
    # Phase 0 documentation set (plan section 4); placeholders are acceptable
    # for later-phase documents, but the file must exist so the structure the
    # plan specifies is verified rather than assumed.
    "docs/architecture.md",
    "docs/annotation_guideline.md",
    "docs/protocol.md",
    "docs/blynk_setup.md",
    "docs/test_plan.md",
    "docs/demo_runbook.md",
    "docs/troubleshooting.md",
    "docs/decision_log.md",
    "docs/ai_usage_log.md",
    "data/README.md",
    "models/README.md",
    "experiments/README.md",
    "experiments/ai_metrics.csv",
    "experiments/system_trials.csv",
    # Phase 0 tooling and tests.
    "src/config.py",
    "tools/env_check.py",
    "tools/camera_check.py",
    "tests/test_config.py",
]


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.passes: list[str] = []

    def check(self, label: str, fn) -> None:
        try:
            detail = fn()
        except Exception as exc:  # noqa: BLE001 - report, never crash the check
            self.failures.append(f"{label}: {exc}")
            return
        self.passes.append(f"{label}: {detail}" if detail else label)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        self.failures.append(message)


def check_python(ck: Checker) -> None:
    version = sys.version_info
    if version[:2] < MIN_PYTHON:
        ck.fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
            f"running {platform.python_version()}"
        )
    elif version[:2] >= MAX_PYTHON_EXCLUSIVE:
        ck.fail(
            f"Python {platform.python_version()} is newer than the tested range "
            f"(< {MAX_PYTHON_EXCLUSIVE[0]}.{MAX_PYTHON_EXCLUSIVE[1]}). PyTorch "
            f"{'and Ultralytics ' if version[:2] >= (3, 13) else ''}wheels are not "
            "available/verified. Use Python 3.12."
        )
    else:
        ck.passes.append(
            f"Python {platform.python_version()} ({platform.machine()}) "
            "@ acceptable version"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the eye-shield environment.")
    parser.add_argument("--quiet", action="store_true", help="print failures only")
    args = parser.parse_args()

    ck = Checker()

    if not args.quiet:
        print(f"Eye-Shield environment check — project root: {PROJECT_ROOT}")
        print("-" * 72)

    check_python(ck)

    def _check_modules() -> str:
        missing = []
        versions = []
        for module, package in REQUIRED_MODULES.items():
            try:
                importlib.import_module(module)
            except ImportError:
                missing.append(package)
                continue
            # Read the version from the installed distribution rather than from
            # the module attribute: not every package exposes __version__ (for
            # example python-dotenv), and defaulting to "?" would hide a broken
            # pin behind a passing check.
            try:
                version = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                version = "unknown (distribution not found)"
            versions.append(f"{package}=={version}")
        if missing:
            raise RuntimeError(
                "missing package(s): "
                + ", ".join(missing)
                + " — run: python -m pip install -r requirements.txt"
            )
        return ", ".join(versions)

    ck.check("Dependencies", _check_modules)

    def _check_layout() -> str:
        missing = [d for d in REQUIRED_DIRS if not (PROJECT_ROOT / d).is_dir()]
        missing += [
            f for f in REQUIRED_FILES if not (PROJECT_ROOT / f).is_file()
        ]
        if missing:
            raise RuntimeError("missing path(s): " + ", ".join(missing))
        return f"{len(REQUIRED_DIRS)} directories, {len(REQUIRED_FILES)} files present"

    ck.check("Repository layout", _check_layout)

    def _check_config() -> str:
        import config as config_module

        source = config_module.DEFAULT_CONFIG_PATH
        if not source.exists():
            raise config_module.ConfigError(
                f"config/app.yaml is absent ({source}). This is REQUIRED for the "
                "application; create it with: "
                "cp config/app.example.yaml config/app.yaml"
            )
        cfg = config_module.load_config()
        classes = config_module.load_classes()
        names = ", ".join(f"{c.class_id}:{c.name}" for c in classes.classes.values())
        return (
            f"{cfg.source_path.name} valid; classes [{names}]; "
            f"conf={cfg.filter.confirmation_seconds}s "
            f"ratio={cfg.filter.violation_ratio} "
            f"clear={cfg.filter.clearance_seconds}s"
        )

    ck.check("Configuration", _check_config)

    def _check_model() -> str:
        import config as config_module

        cfg = config_module.load_config()
        if not cfg.model.path.exists():
            raise RuntimeError(
                f"model weights not found at {cfg.model.path} — expected until "
                "Phase 2 produces best.pt"
            )
        return str(cfg.model.path)

    ok_before = len(ck.failures)
    ck.check("Model weights", _check_model)
    if len(ck.failures) > ok_before:
        # Missing weights are expected before the training phase completes.
        ck.failures.pop()
        ck.warn(
            "Model weights not present — expected until Phase 2 training "
            "produces models/best.pt"
        )

    def _check_torch_backend() -> str:
        import torch

        if torch.backends.mps.is_available():
            return "MPS (Apple GPU) available"
        if torch.cuda.is_available():
            return f"CUDA available ({torch.cuda.get_device_name(0)})"
        return "CPU only — inference will be slower than the 15 FPS goal"

    ck.check("Torch backend", _check_torch_backend)

    if not args.quiet:
        for line in ck.passes:
            print(f"  PASS  {line}")
        for line in ck.warnings:
            print(f"  WARN  {line}")
        for line in ck.failures:
            print(f"  FAIL  {line}")
        print("-" * 72)

    status = "OK" if not ck.failures else "FAILED"
    print(
        f"Environment check {status}: {len(ck.passes)} passed, "
        f"{len(ck.warnings)} warning(s), {len(ck.failures)} failure(s)"
    )
    if ck.failures and not args.quiet:
        print("Run `python tools/env_check.py --verbose-trace` for stack traces.")
    return 0 if not ck.failures else 1


if __name__ == "__main__":
    if "--verbose-trace" in sys.argv:
        try:
            sys.exit(main())
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            sys.exit(2)
    sys.exit(main())
