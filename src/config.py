"""Typed, validated configuration loading for the eye-shield monitor.

Design rule: configuration errors must fail fast with a readable message rather
than silently degrading into unsafe behaviour. Every bound checked here exists
because an out-of-range value would change a *safety* decision.

This module has no third-party imports beyond PyYAML so that it can be
imported (and tested) without OpenCV, PyTorch, or a camera.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "app.example.yaml"
DEFAULT_CLASSES_PATH = PROJECT_ROOT / "config" / "classes.yaml"


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or out of bounds."""


@dataclass(frozen=True)
class CameraConfig:
    index: int
    width: int
    height: int


@dataclass(frozen=True)
class ModelConfig:
    path: Path
    image_size: int
    confidence_threshold: float
    iou_threshold: float


@dataclass(frozen=True)
class ObservationConfig:
    minimum_box_area_ratio: float


@dataclass(frozen=True)
class FilterConfig:
    confirmation_seconds: float
    window_seconds: float
    violation_ratio: float
    clearance_seconds: float


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    heartbeat_seconds: float
    ack_timeout_seconds: float
    retry_count: int


@dataclass(frozen=True)
class LoggingConfig:
    directory: Path
    save_event_images: bool


@dataclass(frozen=True)
class AppConfig:
    camera: CameraConfig
    model: ModelConfig
    observation: ObservationConfig
    filter: FilterConfig
    serial: SerialConfig
    logging: LoggingConfig
    source_path: Path = field(default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class ClassDefinition:
    class_id: int
    name: str
    compliance: str


@dataclass(frozen=True)
class ClassMap:
    protocol_version: int
    classes: dict[int, ClassDefinition]
    unmapped_class_policy: str

    def by_name(self) -> dict[str, ClassDefinition]:
        return {c.name: c for c in self.classes.values()}

    def name_for(self, class_id: int) -> str:
        """Class name for a model class index, or ``"unknown"`` if unmapped."""
        definition = self.classes.get(class_id)
        return definition.name if definition is not None else "unknown"

    def compliance_for(self, class_id: int) -> str:
        """Compliance meaning for a model class index.

        Returns ``"safe"``, ``"violation"``, or ``"unknown"``. An index that is
        absent from the map is ``"unknown"`` -- never ``"safe"``, because failing
        to recognise a class must not be reported as compliance.
        """
        definition = self.classes.get(class_id)
        return definition.compliance if definition is not None else "unknown"


def _require(mapping: dict[str, Any], section: str, key: str, path: Path) -> Any:
    if section not in mapping:
        raise ConfigError(
            f"{path}: missing section '{section}'. See config/app.example.yaml."
        )
    section_value = mapping[section]
    if not isinstance(section_value, dict):
        raise ConfigError(f"{path}: section '{section}' must be a mapping.")
    if key not in section_value:
        raise ConfigError(f"{path}: missing key '{section}.{key}'.")
    return section_value[key]


def _as_int(value: Any, key: str, path: Path, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: '{key}' must be an integer, got {value!r}.")
    if not minimum <= value <= maximum:
        raise ConfigError(
            f"{path}: '{key}' must be between {minimum} and {maximum}, got {value}."
        )
    return value


def _as_float(
    value: Any, key: str, path: Path, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: '{key}' must be a number, got {value!r}.")
    if not minimum <= float(value) <= maximum:
        raise ConfigError(
            f"{path}: '{key}' must be between {minimum} and {maximum}, got {value}."
        )
    return float(value)


def _as_bool(value: Any, key: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path}: '{key}' must be true or false, got {value!r}.")
    return value


def _as_port(value: Any, key: str, path: Path) -> str:
    """Validate the serial port selector.

    ``"auto"`` requests discovery; anything else must be a non-empty device
    path (e.g. ``/dev/tty.usbserial-0001`` or ``COM5``). A non-string or a
    blank string is rejected rather than coerced, because silently falling back
    to autodiscovery could open a different device than the operator intended.
    """
    if not isinstance(value, str):
        raise ConfigError(
            f"{path}: '{key}' must be a string device path or 'auto', "
            f"got {value!r}."
        )
    port = value.strip()
    if not port:
        raise ConfigError(
            f"{path}: '{key}' must not be blank; use 'auto' to autodetect the "
            "port, or give a device path such as '/dev/tty.usbserial-0001'."
        )
    return port


def _resolve_path(value: Any, key: str, path: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: '{key}' must be a non-empty path string.")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def load_classes(path: Path | str = DEFAULT_CLASSES_PATH) -> ClassMap:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Class map not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level.")

    protocol_version = raw.get("protocol_version")
    if not isinstance(protocol_version, int):
        raise ConfigError(f"{path}: 'protocol_version' must be an integer.")

    raw_classes = raw.get("classes")
    if not isinstance(raw_classes, dict) or not raw_classes:
        raise ConfigError(f"{path}: 'classes' must be a non-empty mapping.")

    classes: dict[int, ClassDefinition] = {}
    for class_id, entry in raw_classes.items():
        if not isinstance(class_id, int):
            raise ConfigError(f"{path}: class id {class_id!r} must be an integer.")
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: class {class_id} must be a mapping.")
        name = entry.get("name")
        compliance = entry.get("compliance")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{path}: class {class_id} needs a non-empty 'name'.")
        if compliance not in {"safe", "violation"}:
            raise ConfigError(
                f"{path}: class {class_id} 'compliance' must be 'safe' or "
                f"'violation', got {compliance!r}."
            )
        classes[class_id] = ClassDefinition(class_id, name.strip(), compliance)

    names = [c.name for c in classes.values()]
    if len(names) != len(set(names)):
        raise ConfigError(f"{path}: class names must be unique, got {names}.")
    if not any(c.compliance == "safe" for c in classes.values()) or not any(
        c.compliance == "violation" for c in classes.values()
    ):
        raise ConfigError(
            f"{path}: the class set must contain at least one 'safe' and one "
            "'violation' class."
        )
    if sorted(classes) != list(range(len(classes))):
        raise ConfigError(
            f"{path}: class ids must be contiguous from 0, got {sorted(classes)}."
        )

    policy = raw.get("unmapped_class_policy", "unknown")
    if policy != "unknown":
        raise ConfigError(
            f"{path}: 'unmapped_class_policy' must be 'unknown' "
            f"(unmapped classes may never be treated as safe), got {policy!r}."
        )

    return ClassMap(protocol_version, classes, policy)


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load and validate the application config.

    Resolution order for the path: explicit argument, then the
    ``EYE_SHIELD_CONFIG`` environment variable, then ``config/app.yaml``.
    """
    if path is None:
        path = os.environ.get("EYE_SHIELD_CONFIG") or DEFAULT_CONFIG_PATH
    path = Path(path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.exists():
        raise ConfigError(
            f"Config not found: {path}\n"
            f"Create it with: cp config/app.example.yaml config/app.yaml"
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level.")

    camera = CameraConfig(
        index=_as_int(_require(raw, "camera", "index", path), "camera.index", path, 0, 16),
        width=_as_int(_require(raw, "camera", "width", path), "camera.width", path, 160, 7680),
        height=_as_int(_require(raw, "camera", "height", path), "camera.height", path, 120, 4320),
    )

    confidence = _as_float(
        _require(raw, "model", "confidence_threshold", path),
        "model.confidence_threshold",
        path,
        0.01,
        0.99,
    )
    model = ModelConfig(
        path=_resolve_path(_require(raw, "model", "path", path), "model.path", path),
        image_size=_as_int(
            _require(raw, "model", "image_size", path), "model.image_size", path, 160, 1920
        ),
        confidence_threshold=confidence,
        iou_threshold=_as_float(
            _require(raw, "model", "iou_threshold", path),
            "model.iou_threshold",
            path,
            0.01,
            0.99,
        ),
    )

    observation = ObservationConfig(
        minimum_box_area_ratio=_as_float(
            _require(raw, "observation", "minimum_box_area_ratio", path),
            "observation.minimum_box_area_ratio",
            path,
            0.0001,
            0.5,
        )
    )

    filter_cfg = FilterConfig(
        confirmation_seconds=_as_float(
            _require(raw, "filter", "confirmation_seconds", path),
            "filter.confirmation_seconds",
            path,
            0.1,
            60.0,
        ),
        window_seconds=_as_float(
            _require(raw, "filter", "window_seconds", path),
            "filter.window_seconds",
            path,
            0.1,
            60.0,
        ),
        violation_ratio=_as_float(
            _require(raw, "filter", "violation_ratio", path),
            "filter.violation_ratio",
            path,
            0.1,
            1.0,
        ),
        clearance_seconds=_as_float(
            _require(raw, "filter", "clearance_seconds", path),
            "filter.clearance_seconds",
            path,
            0.1,
            60.0,
        ),
    )
    if filter_cfg.window_seconds < filter_cfg.confirmation_seconds:
        raise ConfigError(
            f"{path}: 'filter.window_seconds' ({filter_cfg.window_seconds}) must be "
            f">= 'filter.confirmation_seconds' ({filter_cfg.confirmation_seconds}); "
            "the ratio check must be able to see the whole confirmation period."
        )

    serial_cfg = SerialConfig(
        port=_as_port(_require(raw, "serial", "port", path), "serial.port", path),
        baudrate=_as_int(
            _require(raw, "serial", "baudrate", path), "serial.baudrate", path, 9600, 2000000
        ),
        heartbeat_seconds=_as_float(
            _require(raw, "serial", "heartbeat_seconds", path),
            "serial.heartbeat_seconds",
            path,
            0.2,
            60.0,
        ),
        ack_timeout_seconds=_as_float(
            _require(raw, "serial", "ack_timeout_seconds", path),
            "serial.ack_timeout_seconds",
            path,
            0.1,
            30.0,
        ),
        retry_count=_as_int(
            _require(raw, "serial", "retry_count", path), "serial.retry_count", path, 0, 10
        ),
    )

    log_dir_value = _require(raw, "logging", "directory", path)
    logging_cfg = LoggingConfig(
        directory=_resolve_path(log_dir_value, "logging.directory", path),
        save_event_images=_as_bool(
            _require(raw, "logging", "save_event_images", path),
            "logging.save_event_images",
            path,
        ),
    )

    return AppConfig(
        camera=camera,
        model=model,
        observation=observation,
        filter=filter_cfg,
        serial=serial_cfg,
        logging=logging_cfg,
        source_path=path,
    )
