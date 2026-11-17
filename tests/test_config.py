"""Phase 0 tests: the configuration contract.

These tests cover the Phase 0 acceptance requirements that can be checked without
hardware:

* a valid configuration loads and is exposed as immutable typed values;
* invalid values raise ``ConfigError`` naming the exact dotted key, so a bad
  config produces a readable message instead of a traceback (plan section 11,
  Phase 0 acceptance: "Configuration errors produce readable messages");
* the class map cannot silently invert compliance, because swapping "safe" and
  "violation" would turn an eye-shield monitor into a hazard;
* no secret or machine-local file is tracked by Git.

The key names asserted here are exactly those of ``config/app.example.yaml``,
which in turn follows plan section 14. If this file and the plan disagree, the
plan wins and this file is wrong.

Run with::

    python -m pytest tests/test_config.py -q
"""

from __future__ import annotations

import copy
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from src.config import (
    AppConfig,
    ClassMap,
    ConfigError,
    load_classes,
    load_config,
)

# --------------------------------------------------------------------------- #
# Fixtures: the plan section 14 schema
# --------------------------------------------------------------------------- #

BASE_CONFIG = {
    "camera": {"index": 0, "width": 1280, "height": 720},
    "model": {
        "path": "models/best.pt",
        "image_size": 640,
        "confidence_threshold": 0.55,
        "iou_threshold": 0.45,
    },
    "observation": {"minimum_box_area_ratio": 0.01},
    "filter": {
        "confirmation_seconds": 2.0,
        "window_seconds": 2.0,
        "violation_ratio": 0.80,
        "clearance_seconds": 2.0,
    },
    "serial": {
        "port": "auto",
        "baudrate": 115200,
        "heartbeat_seconds": 2.0,
        "ack_timeout_seconds": 1.0,
        "retry_count": 3,
    },
    "logging": {"directory": "logs", "save_event_images": False},
}

BASE_CLASSES = {
    "protocol_version": 1,
    "classes": {
        0: {"name": "goggles", "compliance": "safe"},
        1: {"name": "no_goggles", "compliance": "violation"},
    },
    "unmapped_class_policy": "unknown",
}

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture()
def app_file(tmp_path: Path) -> Path:
    return _write(tmp_path, "app.yaml", copy.deepcopy(BASE_CONFIG))


@pytest.fixture()
def classes_file(tmp_path: Path) -> Path:
    return _write(tmp_path, "classes.yaml", copy.deepcopy(BASE_CLASSES))


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_valid_config_loads(app_file: Path) -> None:
    cfg = load_config(app_file)

    assert isinstance(cfg, AppConfig)
    assert cfg.camera.index == 0
    assert cfg.camera.width == 1280
    assert cfg.camera.height == 720
    assert cfg.model.image_size == 640
    assert cfg.model.confidence_threshold == pytest.approx(0.55)
    assert cfg.model.iou_threshold == pytest.approx(0.45)
    assert cfg.observation.minimum_box_area_ratio == pytest.approx(0.01)
    assert cfg.filter.confirmation_seconds == pytest.approx(2.0)
    assert cfg.filter.window_seconds == pytest.approx(2.0)
    assert cfg.filter.violation_ratio == pytest.approx(0.80)
    assert cfg.filter.clearance_seconds == pytest.approx(2.0)
    assert cfg.serial.port == "auto"
    assert cfg.serial.baudrate == 115200
    assert cfg.serial.heartbeat_seconds == pytest.approx(2.0)
    assert cfg.serial.ack_timeout_seconds == pytest.approx(1.0)
    assert cfg.serial.retry_count == 3
    assert cfg.logging.directory.name == "logs"
    assert cfg.logging.save_event_images is False
    assert cfg.source_path == app_file


def test_config_is_immutable(app_file: Path) -> None:
    """Safety-relevant values must not be mutable after validation."""
    cfg = load_config(app_file)

    with pytest.raises(Exception):
        cfg.filter.confirmation_seconds = 0.0  # type: ignore[misc]
    with pytest.raises(Exception):
        cfg.model.confidence_threshold = 0.99  # type: ignore[misc]


def test_shipped_example_config_loads() -> None:
    """The committed example must pass the same validation a user will hit."""
    example = REPO_ROOT / "config" / "app.example.yaml"
    assert example.is_file(), "config/app.example.yaml must be committed"

    cfg = load_config(example)
    assert cfg.camera.index == 0
    assert cfg.model.image_size == 640
    assert cfg.serial.baudrate == 115200
    assert cfg.filter.window_seconds >= cfg.filter.confirmation_seconds


def test_shipped_class_map_loads() -> None:
    classes = REPO_ROOT / "config" / "classes.yaml"
    assert classes.is_file(), "config/classes.yaml must be committed"

    mapping = load_classes(classes)

    assert isinstance(mapping, ClassMap)
    assert mapping.protocol_version == 1
    assert mapping.name_for(0) == "goggles"
    assert mapping.name_for(1) == "no_goggles"
    assert mapping.compliance_for(0) == "safe"
    assert mapping.compliance_for(1) == "violation"


# --------------------------------------------------------------------------- #
# Missing file / section / key produce readable, actionable messages
# --------------------------------------------------------------------------- #


def test_missing_file_names_the_path_and_the_fix(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"

    with pytest.raises(ConfigError) as excinfo:
        load_config(missing)

    message = str(excinfo.value)
    assert "nope.yaml" in message
    # Must tell the user how to fix it, not merely that it failed.
    assert "app.example.yaml" in message


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    blank = tmp_path / "blank.yaml"
    blank.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(blank)


def test_non_mapping_root_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(bad)


def test_malformed_yaml_is_a_config_error(tmp_path: Path) -> None:
    """A YAML syntax error must surface as ConfigError, not a raw traceback."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("camera: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(bad)


@pytest.mark.parametrize(
    "section", ["camera", "model", "observation", "filter", "serial", "logging"]
)
def test_missing_section_is_named(tmp_path: Path, section: str) -> None:
    payload = copy.deepcopy(BASE_CONFIG)
    del payload[section]
    path = _write(tmp_path, "app.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert section in str(excinfo.value)


def test_section_that_is_not_a_mapping_is_named(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CONFIG)
    payload["filter"] = "two seconds"
    path = _write(tmp_path, "app.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "filter" in str(excinfo.value)


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("camera", "index"),
        ("camera", "width"),
        ("camera", "height"),
        ("model", "path"),
        ("model", "image_size"),
        ("model", "confidence_threshold"),
        ("model", "iou_threshold"),
        ("observation", "minimum_box_area_ratio"),
        ("filter", "confirmation_seconds"),
        ("filter", "window_seconds"),
        ("filter", "violation_ratio"),
        ("filter", "clearance_seconds"),
        ("serial", "port"),
        ("serial", "baudrate"),
        ("serial", "heartbeat_seconds"),
        ("serial", "ack_timeout_seconds"),
        ("serial", "retry_count"),
        ("logging", "directory"),
        ("logging", "save_event_images"),
    ],
)
def test_every_required_key_missing_is_named(
    tmp_path: Path, section: str, key: str
) -> None:
    """Deleting any single documented key must name that exact key."""
    payload = copy.deepcopy(BASE_CONFIG)
    del payload[section][key]
    path = _write(tmp_path, "app.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert key in message, f"message must name the offending key: {message!r}"
    assert section in message, f"message must name the section: {message!r}"


# --------------------------------------------------------------------------- #
# Out-of-range values are refused, naming the key
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("section", "key", "bad_value"),
    [
        ("camera", "index", -1),
        ("camera", "index", 17),
        ("camera", "width", 0),
        ("camera", "width", 100),
        ("camera", "width", 8000),
        ("camera", "height", 10),
        ("camera", "height", 5000),
        ("model", "image_size", 0),
        ("model", "image_size", 4096),
        ("model", "confidence_threshold", 0.0),
        ("model", "confidence_threshold", 1.0),
        ("model", "confidence_threshold", 1.4),
        ("model", "iou_threshold", -0.1),
        ("model", "iou_threshold", 1.01),
        ("observation", "minimum_box_area_ratio", 0.0),
        ("observation", "minimum_box_area_ratio", 1.5),
        ("filter", "confirmation_seconds", 0.0),
        ("filter", "confirmation_seconds", -2.0),
        ("filter", "window_seconds", 0.0),
        ("filter", "violation_ratio", 0.0),
        ("filter", "violation_ratio", 1.2),
        ("filter", "clearance_seconds", -1.0),
        ("serial", "baudrate", 0),
        ("serial", "baudrate", 99),
        ("serial", "heartbeat_seconds", 0.0),
        ("serial", "ack_timeout_seconds", 0.0),
        ("serial", "retry_count", -1),
        ("serial", "retry_count", 11),
    ],
)
def test_bounds_are_enforced_with_named_key(
    tmp_path: Path, section: str, key: str, bad_value: float
) -> None:
    payload = copy.deepcopy(BASE_CONFIG)
    payload[section][key] = bad_value
    path = _write(tmp_path, "bad.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert key in message, f"message must name the offending key: {message!r}"
    assert section in message, f"message must name the section: {message!r}"


@pytest.mark.parametrize(
    ("section", "key", "good_value"),
    [
        ("model", "confidence_threshold", 0.01),
        ("model", "confidence_threshold", 0.99),
        ("model", "iou_threshold", 0.01),
        ("model", "iou_threshold", 0.99),
        ("observation", "minimum_box_area_ratio", 0.0001),
        ("observation", "minimum_box_area_ratio", 0.5),
        ("filter", "violation_ratio", 0.1),
        ("filter", "violation_ratio", 1.0),
        ("serial", "retry_count", 0),
        ("serial", "retry_count", 10),
        ("camera", "index", 0),
    ],
)
def test_documented_boundaries_are_inclusive(
    tmp_path: Path, section: str, key: str, good_value: float
) -> None:
    """Values exactly on a documented bound must be accepted; otherwise the
    error message would advertise a range the loader itself rejects."""
    payload = copy.deepcopy(BASE_CONFIG)
    payload[section][key] = good_value
    path = _write(tmp_path, "ok.yaml", payload)

    cfg = load_config(path)
    section_obj = getattr(cfg, section)
    assert getattr(section_obj, key) == pytest.approx(float(good_value))


@pytest.mark.parametrize(
    ("section", "key", "bad_value"),
    [
        ("camera", "width", "eight hundred"),
        ("camera", "height", None),
        ("model", "image_size", "640"),
        ("filter", "violation_ratio", "0.8"),
        ("serial", "port", 1234),
        ("serial", "port", "   "),
        ("model", "path", ""),
        ("logging", "directory", ""),
        ("logging", "save_event_images", "yes"),
    ],
)
def test_wrong_types_are_rejected(
    tmp_path: Path, section: str, key: str, bad_value: object
) -> None:
    payload = copy.deepcopy(BASE_CONFIG)
    payload[section][key] = bad_value
    path = _write(tmp_path, "bad.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert key in str(excinfo.value)


@pytest.mark.parametrize(
    ("section", "key"),
    [("serial", "retry_count"), ("camera", "index"), ("model", "image_size")],
)
def test_boolean_is_not_accepted_as_integer(
    tmp_path: Path, section: str, key: str
) -> None:
    """YAML parses ``true`` as bool, and bool subclasses int in Python. A bool
    must not silently become 0/1 for a count or index."""
    payload = copy.deepcopy(BASE_CONFIG)
    payload[section][key] = True
    path = _write(tmp_path, "bad.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert key in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Cross-field rule: a confirmation period cannot exceed the observation window
# --------------------------------------------------------------------------- #


def test_confirmation_longer_than_window_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CONFIG)
    payload["filter"]["window_seconds"] = 1.0
    payload["filter"]["confirmation_seconds"] = 2.0
    path = _write(tmp_path, "bad.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "confirmation_seconds" in message
    assert "window_seconds" in message


def test_confirmation_equal_to_window_is_allowed(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CONFIG)
    payload["filter"]["window_seconds"] = 2.0
    payload["filter"]["confirmation_seconds"] = 2.0

    cfg = load_config(_write(tmp_path, "ok.yaml", payload))
    assert cfg.filter.confirmation_seconds == cfg.filter.window_seconds


# --------------------------------------------------------------------------- #
# Class map contract: compliance can never be inverted or defaulted to safe
# --------------------------------------------------------------------------- #


def test_unmapped_class_resolves_to_unknown_not_safe(classes_file: Path) -> None:
    """A detection whose class is absent from the map must be UNKNOWN.

    This is the single most important invariant in the project (plan section
    1.2): an unrecognised detection is not evidence of compliance.
    """
    mapping = load_classes(classes_file)

    assert mapping.compliance_for(99) == "unknown"
    assert mapping.name_for(99) == "unknown"
    assert mapping.compliance_for(-1) == "unknown"


def test_unknown_policy_never_becomes_safe(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["unmapped_class_policy"] = "safe"
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_classes(path)

    assert "unmapped_class_policy" in str(excinfo.value)


def test_omitted_unmapped_policy_defaults_to_unknown(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    del payload["unmapped_class_policy"]
    path = _write(tmp_path, "classes.yaml", payload)

    assert load_classes(path).unmapped_class_policy == "unknown"


@pytest.mark.parametrize("policy", ["violation", "SAFE", ""])
def test_invalid_unmapped_policy_is_rejected(tmp_path: Path, policy: str) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["unmapped_class_policy"] = policy
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError):
        load_classes(path)


def test_non_contiguous_class_ids_are_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"] = {
        0: {"name": "goggles", "compliance": "safe"},
        2: {"name": "no_goggles", "compliance": "violation"},
    }
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_classes(path)

    assert "contiguous" in str(excinfo.value).lower()


def test_class_ids_must_start_at_zero(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"] = {
        1: {"name": "goggles", "compliance": "safe"},
        2: {"name": "no_goggles", "compliance": "violation"},
    }
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError):
        load_classes(path)


def test_duplicate_class_names_are_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"][1]["name"] = "goggles"
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_classes(path)

    assert "goggles" in str(excinfo.value)


def test_missing_violation_class_is_rejected(tmp_path: Path) -> None:
    """With only a 'safe' class the monitor could never alarm, which must be a
    loud configuration error rather than a silently useless system."""
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"] = {0: {"name": "goggles", "compliance": "safe"}}
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError):
        load_classes(path)


def test_missing_safe_class_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"] = {0: {"name": "no_goggles", "compliance": "violation"}}
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError):
        load_classes(path)


def test_invalid_compliance_value_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"][0]["compliance"] = "probably_fine"
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError) as excinfo:
        load_classes(path)

    assert "compliance" in str(excinfo.value)


def test_empty_class_set_is_rejected(tmp_path: Path) -> None:
    payload = copy.deepcopy(BASE_CLASSES)
    payload["classes"] = {}
    path = _write(tmp_path, "classes.yaml", payload)

    with pytest.raises(ConfigError):
        load_classes(path)


def test_malformed_class_map_yaml_is_a_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "classes.yaml"
    bad.write_text("classes: [oops\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_classes(bad)


def test_missing_class_map_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_classes(tmp_path / "absent.yaml")

    assert "absent.yaml" in str(excinfo.value)


def test_class_map_is_immutable(classes_file: Path) -> None:
    mapping = load_classes(classes_file)

    with pytest.raises(Exception):
        mapping.unmapped_class_policy = "safe"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Path resolution precedence: argument, then env var, then default
# --------------------------------------------------------------------------- #


def test_env_var_is_used_when_no_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write(tmp_path, "via_env.yaml", copy.deepcopy(BASE_CONFIG))
    monkeypatch.setenv("EYE_SHIELD_CONFIG", str(path))

    cfg = load_config()
    assert cfg.camera.width == 1280


def test_explicit_argument_beats_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_path = _write(tmp_path, "env.yaml", copy.deepcopy(BASE_CONFIG))
    payload = copy.deepcopy(BASE_CONFIG)
    payload["camera"]["width"] = 640
    arg_path = _write(tmp_path, "arg.yaml", payload)

    monkeypatch.setenv("EYE_SHIELD_CONFIG", str(env_path))
    cfg = load_config(arg_path)

    assert cfg.camera.width == 640, "explicit argument must take precedence"


# --------------------------------------------------------------------------- #
# Secrets hygiene: Phase 0 acceptance "no secrets are committed"
# --------------------------------------------------------------------------- #


def test_no_secret_or_local_config_files_are_tracked() -> None:
    """``git ls-files`` must never list a real ``.env``, ``config/app.yaml``, or
    a firmware ``config.h``.

    Skips when not in a Git checkout so the suite also runs from an archive.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git not available")

    if out.returncode != 0:  # pragma: no cover
        pytest.skip("not a git checkout")

    tracked = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    forbidden: list[str] = []
    for name in tracked:
        if name == ".env":
            forbidden.append(name)
        elif name.startswith(".env.") and not name.endswith(".example"):
            forbidden.append(name)
        elif name == "config/app.yaml":
            forbidden.append(name)
        elif name.endswith("/config.h"):
            forbidden.append(name)

    assert not forbidden, f"secrets or local config are tracked: {forbidden}"


def test_gitignore_keeps_the_env_example_committable() -> None:
    """The ``.env.*`` rule must not swallow the committed template."""
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():  # pragma: no cover
        pytest.skip("no .gitignore")

    text = textwrap.dedent(gitignore.read_text(encoding="utf-8"))
    assert "!.env.example" in text


def test_gitignore_never_reincludes_model_weights() -> None:
    """Plan section 4 forbids committing large model artifacts.

    A ``!models/best.pt`` negation would silently defeat that rule and put a
    multi-megabyte binary in the repository.
    """
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():  # pragma: no cover
        pytest.skip("no .gitignore")

    for line in gitignore.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("!") and stripped.endswith((".pt", ".onnx", ".tflite")):
            pytest.fail(f"weights are re-included by: {stripped}")