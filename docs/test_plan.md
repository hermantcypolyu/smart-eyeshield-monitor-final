# Test plan

## Phase 0 acceptance checks

These are the checks the Phase 0 gate is judged against. Run them in order.

```bash
.venv/bin/python tools/env_check.py     # must print "Environment check OK"
.venv/bin/python -m pytest -q           # config contract tests must pass
.venv/bin/python tools/camera_check.py  # manual: prints "Camera check PASSED."
```

| ID | Check | Method | Pass condition |
|---|---|---|---|
| ENV-01 | Clean-clone bootstrap | Fresh venv + `pip install -r requirements.txt` | Install succeeds, no build errors |
| ENV-02 | Environment check | `tools/env_check.py` | Exit code 0, 0 failures |
| ENV-03 | Camera tool runs | `tools/camera_check.py` | Exit code 0, reports FPS |
| CFG-01 | Valid config loads | `pytest tests/test_config.py` | All pass |
| CFG-02 | Invalid value rejected | `pytest tests/test_config.py -k bounds` | Raises `ConfigError` naming the key |
| SEC-01 | No secrets committed | `git ls-files` review + `.gitignore` | No `.env`, `config.h`, `app.yaml`, or keys |

`ENV-03` needs a physical camera and therefore cannot run headless; the automated
suite skips it and the result is recorded manually in the phase report.

### Recorded run — Phase 0 gate (2026-11-17)

Environment: Apple M2 (arm64), macOS, Python 3.12.4 from `/opt/homebrew/bin/python3.12`,
`.venv` with the pinned `requirements.txt`. Both the working tree and a fresh
`git clone` into `/tmp` were tested; the results below are identical for both.

| ID | Result | Evidence |
|---|---|---|
| ENV-01 | **PASS** | Clean clone + fresh venv + `pip install -r requirements.txt` exited 0 with no build errors (torch 2.15.0, torchvision 0.29.2, opencv-python 5.0.0.93, ultralytics 8.4.215 wheels installed) |
| ENV-02 | **PASS** | `Environment check OK: 5 passed, 1 warning(s), 0 failure(s)`, exit 0. The single warning is the intentionally absent `models/best.pt` |
| ENV-03 | **PASS** | `Camera 0 opened (native 1280x720, codec n/a)`, `Captured 10/10 frames at 1280x720: 29.9 FPS (mean interval 33.5 ms)`, `Camera check PASSED.`, exit 0 |
| CFG-01 | **PASS** | `108 passed` (`tests/test_config.py`), exit 0 |
| CFG-02 | **PASS** | `pytest tests/test_config.py -k bounds` — every case raises `ConfigError` naming the section and key |
| SEC-01 | **PASS** | `git ls-files` lists no `.env`, `config/app.yaml`, or `config.h`; `git check-ignore -v` confirms each is matched by an ignore rule |

Two failure paths were exercised deliberately, not assumed:

- `tools/env_check.py` in a fresh clone with no `config/app.yaml` prints
  `FAIL Configuration: config/app.yaml is absent (...). This is REQUIRED for the
  application; create it with: cp config/app.example.yaml config/app.yaml` and
  exits 1. A fresh user therefore gets a readable instruction, not a traceback.
- `tools/camera_check.py --index 9` prints
  `ERROR: could not open camera index 9.` plus remediation hints and exits 1.

Not covered by this run: any behaviour requiring `models/best.pt`, an ESP32, a
serial link, or Blynk. Those belong to Phases 2–6 and no claim is made about them.

## Future test matrix (from plan.md §12)

Not yet executed. Listed so later phases report against fixed IDs.

| Group | IDs |
|---|---|
| AI | AI-01 … AI-05 |
| System | SYS-01 … SYS-08 |
| Communication | COM-01 … COM-03 |
| IoT | IOT-01, IOT-02 |
| Robustness | ROB-01, ROB-02 |

## Automated test layout

| File | Phase introduced | Covers |
|---|---|---|
| `tests/test_config.py` | **0** | Config bounds, missing keys, class-map contract |
| `tests/test_temporal_filter.py` | 3 | Threshold boundaries, dropped frames, clearance |
| `tests/test_incident_manager.py` | 3 | One incident per episode, reset eligibility |
| `tests/test_serial_protocol.py` | 4 | Round trips, malformed JSON, duplicates, version mismatch |
| `tests/test_recorded_video.py` | 3 | Deterministic end-to-end behaviour on recorded clips |
| `tests/test_incident_core.py` | **4A** | Firmware incident state machine, compiled from `incident_core.h` and driven over stdin/stdout (37) |
| `tests/test_incident_lifecycle.py` | **4A** | Operator lifecycle: latch, acknowledge, recovery, reset handshake (38) |
| `tests/test_live_eye_shield_timer.py` | **4A** | Overlay timer semantics and frozen confirmation duration (27) |
| `tests/test_v2_live_acceptance.py` | **4A** | Acceptance schema, false-safe gate, promotion verdict (no camera or model) |

### Phase 4A — what the incident tests actually prove

The firmware state machine and the operator lifecycle are two halves of one
contract, so they are tested separately and asserted against the **same** reply
strings, which live only in `incident_core.h` under `namespace reply`. A change
to a literal fails the Python tests rather than silently changing the wire
format.

The tests that matter most, stated as the property they protect:

| Property | Test |
| --- | --- |
| No ordinary `SAFE` clears a latched incident | `test_09_safe_frames_never_clear_a_latched_incident` |
| `UNKNOWN` is never reported as `SAFE` | `test_03_unknown_never_becomes_safe` |
| Acknowledge silences the buzzer but keeps red | `test_13_acknowledge_silences_buzzer_but_keeps_red` |
| Reset is refused without acknowledge | `test_19_reset_rejected_without_acknowledge` |
| Reset is refused without proven recovery | `test_20_reset_rejected_without_safe_recovery` |
| Reset is refused on a stale `RESET_READY` token | `test_22_reset_rejected_when_link_invalid` (core) |
| A completed handshake does not re-send `RESET` | `test_35_completed_handshake_does_not_re_send_reset` |
| A refused reset re-asserts state | `test_33_reset_refusal_releases_token_and_reasserts_state` |
| `ALL_OFF` cannot be used to extinguish a live alarm | `test_incident_core` — `ALL_OFF` refusal cases |

Firmware compilation is a separate gate and is not covered by pytest: see
`docs/hardware_bringup.md` §11.3 for the recorded `arduino-cli` result.

## Reporting rule

Every number reported for this project must be traceable to a raw record in
`experiments/` or a test run captured in this document. Results are never rounded
up, and failed cases are retained alongside successful ones.

---

# Phase 1 — Dataset audit tests

Phase 1 adds `tests/test_inspect_dataset.py`: 23 tests, all over synthetic
fixtures generated inside `tmp_path` at run time. No test opens the real source
archive, so the suite runs offline and reads no licensed imagery.

Full suite after Phase 1: **131 passed** (108 Phase 0 + 23 Phase 1).

## What is covered

| Area | Behaviour asserted |
|---|---|
| Read-only | Source ZIP is byte-identical and keeps its mtime after an audit; nothing is written beside it |
| Invalid input | Missing path, non-ZIP file, missing `data.yaml`, and `nc != len(names)` each raise a readable `AuditInputError`; CLI exits 2 with `ERROR:` and no traceback |
| Annotations | 9-field polygon row → blocking error; out-of-range coords flagged; empty label file is a warning only; unknown class id flagged |
| Pairing | Image without label reported; orphan label reported; **two exports of one source stem pair to their own labels** (regression guard for the exact-stem bug) |
| Duplicates | Byte-identical images produce exactly one exact-duplicate group |
| Leakage | A shared source stem across splits is reported as `source_stem_across_splits` |
| Class scheme | 5-class PPE export reports `goggles`/`no_goggles` missing and verdict `rejected_for_training` |
| Compliance | No class is auto-mapped; decision stays `unmapped_pending_human_review` |
| Determinism | Two runs with the same seed agree byte-for-byte after masking timestamps |
| Outputs | All 13 records are written |
| CLI guards | `--strict-fail` exits 3 on blocking findings; out-of-range `--near-dup-bits` and an `--artifacts` path inside `data/raw/` are both refused with exit 2 |

## Two bugs these tests caught

Both were found by running the tests, not by inspection:

1. `zipfile.ZipFile(path, "rb")` raises `ValueError` — the mode must be `"r"`.
   The auditor could not open any archive until this was fixed.
2. Pairing images to labels on a *normalised* stem collapsed
   `SG10_jpg.rf.<a>` and `SG10_jpg.rf.<b>` into one key, reporting 13 phantom
   missing labels and undercounting instances as 755 instead of 770. Pairing now
   uses the exact stem; normalisation is used only for leakage grouping.

## Phase 0 regression after Phase 1

- `tools/env_check.py` → `Environment check OK: 5 passed, 1 warning(s), 0 failure(s)`
- `tools/camera_check.py` → 30.0 FPS at 1280x720, `Camera check PASSED`

No Phase 0 test was modified.
