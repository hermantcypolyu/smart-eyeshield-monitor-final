# Smart Biohazard & Laser Lab Eye-Shield Monitor

Automated safety-goggles compliance monitoring for laser and biohazard laboratories.
A laptop runs a custom YOLOv8n model on a USB webcam stream, confirms sustained
non-compliance with a time-based rule, and sends a structured alert over USB serial
to an ESP32-S3 that owns a **latched** red-LED + buzzer alarm. Blynk provides mobile
status and a constrained, conditionally-accepted reset.

**Course:** EIE3127 AI-enabled IoT Project
**Status:** Phase 0 complete (repository + reproducibility). No model, hardware, or
Blynk integration yet. Nothing in this README describes a measured result.

> This is an educational proof-of-concept. It is an additional monitoring aid and
> **not** a certified safety device. It does not replace trained supervisors,
> laboratory procedures, laser interlocks, risk assessments, or approved PPE
> procedures.

---

## 1. What the system will do

```text
USB Webcam → Laptop (OpenCV + YOLOv8n + time-based filter + incident manager)
           → newline-delimited JSON over USB serial
           → ESP32-S3 (latched alarm state machine, red/green LEDs, buzzer)
           → Blynk mobile dashboard (status, silence, acknowledge, conditional reset)
```

Key safety properties (design commitments, verified in later phases):

- **No detection is never treated as `no_goggles`.** Absence of evidence becomes
  `UNKNOWN`, not a violation and not "safe".
- **One incident per violation episode**, created only after a sustained violation.
- **The alarm latches on the ESP32.** Removing goggles/leaving frame does not clear it.
- **Silence, Acknowledge, and Reset are three distinct actions.**
- **Reset is rejected** unless the incident is acknowledged, the safe-clear interval
  has elapsed, and the laptop link is healthy.
- **Local alarming never depends on Blynk or Wi-Fi.**
- **All timing is `time.monotonic()`-based**, so a variable frame rate cannot change
  safety behaviour.

See [plan.md](./plan.md) for the full specification and per-phase acceptance gates.

---

## 2. Prerequisites

| Item | Requirement |
|---|---|
| Python | **3.12** (tested: 3.12.4). Python 3.13+ is **not** supported by the pinned PyTorch build. |
| OS | macOS / Linux / Windows. Verified on macOS arm64 (Apple M2). |
| Hardware (Phase 5+) | ESP32-S3 UNO, red LED on GPIO12, green LED on GPIO11, passive buzzer on GPIO14 |
| Camera | Any USB/built-in webcam reachable through OpenCV |

## 3. Quick start

```bash
git clone <repository-url>
cd AIOT-MiniProject

python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

cp config/app.example.yaml config/app.yaml
cp .env.example .env            # optional; only needed for keys/overrides

.venv/bin/python tools/env_check.py        # one-command environment check
.venv/bin/python tools/camera_check.py     # opens the webcam and measures capture FPS
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.

`tools/env_check.py` exits non-zero on any failure and prints every problem it
found in one pass (Python version, missing packages, repository layout, config
validity, torch backend). A missing `models/best.pt` is a **warning** until Phase 2.

### Expected Phase 0 output

```text
Environment check OK: 5 passed, 1 warning(s), 0 failure(s)
Camera check PASSED.
```

## 4. Configuration

`config/app.yaml` (git-ignored, copied from `config/app.example.yaml`) holds
camera, model, observation, temporal-filter, serial, and logging settings.
Every value is validated and bounded in [src/config.py](./src/config.py);
an out-of-range value stops startup with a message naming the exact key:

```text
config/app.yaml: 'filter.violation_ratio' must be between 0.1 and 1.0, got 1.4.
```

`config/classes.yaml` maps model class IDs to compliance meaning. Class `0` is
`goggles` (safe) and class `1` is `no_goggles` (violation). Unmapped classes are
always `UNKNOWN`.

Secrets (`BLYNK_AUTH_TOKEN`, Wi-Fi, Roboflow keys) never enter source control:
`.env` and `firmware/esp32_eye_shield_monitor/config.h` are both git-ignored.

## 5. Repository layout

| Path | Purpose |
|---|---|
| [config/](./config) | Validated application and class configuration |
| [data/](./data) | Dataset staging, splits, locked test set (payloads not committed) |
| [models/](./models) | Trained weights (`models/best.pt`, not committed) |
| [src/](./src) | Laptop application: capture, inference, filtering, incidents, serial, logging |
| [firmware/](./firmware) | ESP32-S3 Arduino sketch and documentation |
| [training/](./training) | Reproducible YOLOv8n training, evaluation, metric export |
| [tests/](./tests) | Unit and integration tests |
| [tools/](./tools) | Environment, camera, serial-mock, latency, and export utilities |
| [experiments/](./experiments) | Machine-readable experiment records (AI metrics, system trials) |
| [docs/](./docs) | Architecture, protocol, test plan, runbook, decision log, AI usage log |

Files under `src/`, `training/`, and some of `tools/` that belong to later phases
exist now as **documented stubs**: a docstring naming the phase, a `PHASE`
constant, and no behaviour. They are placeholders for the structure required by
plan §4, not partial implementations. Only `src/config.py`, `tools/env_check.py`,
and `tools/camera_check.py` contain working code in Phase 0.

## 6. Current phase status

| Phase | Name | Status |
|---|---|---|
| 0 | Repository and reproducibility | **Complete** |
| 1 | Dataset audit | Not started |
| 2 | YOLOv8n baseline | Not started |
| 3 | Laptop inference application | Not started |
| 4 | Serial protocol and simulator | Not started |
| 5 | ESP32 hardware node | Not started |
| 6 | Blynk integration | Not started |
| 7 | Full integration | Not started |
| 8 | Formal experiments | Not started |
| 9 | Demonstration and documentation | Not started |

No AI metrics, latency figures, or detection results have been produced yet, and
none are claimed.

## 7. Documentation

- [docs/architecture.md](./docs/architecture.md) — component boundaries and ownership
- [docs/protocol.md](./docs/protocol.md) — serial JSON schema
- [docs/annotation_guideline.md](./docs/annotation_guideline.md) — two-class labelling rules
- [docs/dataset_audit.md](./docs/dataset_audit.md) — Phase 1 audit of the source export (verdict: rejected for training)
- [docs/test_plan.md](./docs/test_plan.md) — Phase 0 checks and future test matrix
- [docs/troubleshooting.md](./docs/troubleshooting.md) — common setup failures
- [docs/blynk_setup.md](./docs/blynk_setup.md) — mobile dashboard (Phase 6 placeholder)
- [docs/demo_runbook.md](./docs/demo_runbook.md) — six-minute demo (Phase 9 placeholder)
- [docs/decision_log.md](./docs/decision_log.md) — recorded engineering decisions
- [docs/ai_usage_log.md](./docs/ai_usage_log.md) — generative-AI assistance record

## 8. Analysis questions this repository must be able to answer

The [plan](./plan.md#17-questions-the-agent-must-be-able-to-answer-from-the-code-and-records)
requires the code and records to justify: why "no detection" is not `no_goggles`,
why YOLOv8n, how leakage-free splits were made, how the confidence threshold was
chosen, why the filter is time-based, why the ESP32 owns the latch, what each
command does, and what happens on Wi-Fi/Blynk/camera/serial failure. Each answer
must point at a file, a test, or an experiment record — never an assumption.

## 9. Licence and data provenance

Dataset provenance, its licence, and collection consent will be documented in
Phase 1. Until then, no third-party dataset content is committed to this
repository.
