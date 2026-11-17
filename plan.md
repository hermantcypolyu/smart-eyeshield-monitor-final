# Full-Feature Prototype Development Plan

## Project

**Title:** Smart Biohazard & Laser Lab Eye-Shield Monitor: Automated Safety Goggles Compliance with ESP32 Latching Alarms and IoT Mobile Verification

**Course:** EIE3127 AI-enabled IoT Project

**Development priority:** Build and verify the complete working prototype first. This is not an MVP plan. All proposed core features must work end to end before extensive optimisation, exhaustive dataset tooling, or report polishing.

---

## 1. Primary Objective

Deliver a reliable, demonstrable prototype with this complete workflow:

```text
USB Webcam
    -> custom-trained YOLOv8n goggles/no_goggles detection
    -> confidence and 2-second temporal filtering
    -> incident creation
    -> newline-delimited JSON over USB serial
    -> ESP32-S3 latched alarm
    -> red/green traffic-light LEDs and passive buzzer
    -> Blynk mobile notification
    -> separate Silence, Acknowledge, and conditional Reset controls
    -> event logging and visible system status
```

The prototype is only considered feature-complete when every component above works together on the final laptop and hardware.

---

## 2. Non-Negotiable Features

The development agent must implement all of the following. These are not optional stretch goals.

### 2.1 AI detection

- Custom YOLOv8n model.
- Final classes: `goggles` and `no_goggles`.
- Live USB webcam inference.
- Bounding boxes, class labels, confidence, measured FPS, and current decision state displayed locally.
- `UNKNOWN` state when visual evidence is insufficient.
- Absence of a goggles detection must never automatically mean `no_goggles`.

### 2.2 Temporal validation

- A violation must persist for approximately 2.0 seconds before alarm creation.
- Use monotonic elapsed time rather than assuming a fixed frame rate.
- Tolerate brief missed detections using a rolling observation window.
- Initial target: at least 80% valid `no_goggles` observations within the recent 2.0-second window.
- A short violation below the threshold must not trigger an incident.
- Return to safe observation must persist for a configurable clearance period before reset becomes eligible.

### 2.3 Incident management

- Unique `incident_id` for every confirmed incident.
- Only one active incident in the prototype.
- No repeated incident creation for every frame.
- No new incident until the previous incident has been validly reset.
- Log incident creation, serial transmission, hardware acknowledgement, commands, reset result, and faults.

### 2.4 Serial communication

- Mac/laptop and ESP32-S3 communicate through USB serial at an initial target of 115200 baud.
- UTF-8 newline-delimited JSON.
- Required message types: handshake, heartbeat, violation, safety state, acknowledgement, hardware state, and reset result.
- Duplicate incident suppression.
- Invalid JSON handling.
- Limited retry for unacknowledged incident messages.
- Automatic reconnection and state synchronisation after serial interruption.
- Serial disconnection must not clear a latched alarm.

### 2.5 ESP32-S3 hardware alarm

Hardware:

- ESP32-S3 UNO development board.
- Green traffic-light LED on GPIO11.
- Red traffic-light LED on GPIO12.
- Passive buzzer signal on GPIO14.
- Common ground between the relevant modules.

Required firmware states:

- `BOOT`
- `SAFE`
- `ALARM_LATCHED`
- `SILENCED`
- `ACKNOWLEDGED`
- `READY_TO_RESET`
- `FAULT`

Required behaviour:

- `SAFE`: green on, red off, buzzer off.
- Confirmed violation: red on, green off, buzzer active.
- Alarm remains latched after the visual violation disappears.
- Silence stops only the buzzer.
- Acknowledge records human response but does not indicate safety.
- Green must not represent merely acknowledged status.
- Reset is accepted only when acknowledged, safe-clear is complete, and the laptop link is healthy.
- Wi-Fi/Blynk failure must not disable the local alarm.
- Firmware must use non-blocking `millis()` logic rather than long `delay()` calls.

### 2.6 Blynk

Suggested virtual datastreams:

- V0: ESP32 system state.
- V1: incident ID.
- V2: confidence.
- V3: Silence button.
- V4: Acknowledge button.
- V5: Reset button.
- V6: laptop/serial health.
- V7: latest event timestamp.

Required behaviour:

- One mobile event/notification per newly latched incident.
- Duplicate packets must not create duplicate notifications.
- Silence, Acknowledge, and Reset must remain separate commands.
- Unsafe reset must be rejected with a clear state/result.
- Dashboard status must reflect the ESP32 state, not an optimistic local button state.
- Local alarming continues if Wi-Fi or Blynk is unavailable.

### 2.7 Logging and operator interface

The laptop interface must show:

- AI boxes, labels, and confidence.
- Current observation: `SAFE`, `PENDING`, `VIOLATION`, or `UNKNOWN`.
- Pending violation duration.
- Active incident ID.
- Serial connection and heartbeat status.
- ESP32-reported alarm state.
- Measured FPS.

Store:

- JSONL event log.
- CSV system-trial log.
- Optional event image only when explicitly enabled.
- No default continuous video recording.

---

## 3. Development Strategy

### Core rule

Build a complete vertical slice early, then replace temporary parts with the real model and robust logic.

A complete vertical slice means:

```text
Temporary/manual test trigger
    -> Python incident manager
    -> JSON serial
    -> ESP32 latched alarm
    -> LEDs and buzzer
    -> Blynk notification and controls
    -> safe conditional reset
```

This enables the full hardware and IoT workflow to be proven while dataset preparation and model training continue.

### Do not do these before the prototype works

- Do not spend days building a production-grade dataset analysis platform.
- Do not create excessive unit tests for trivial getters or placeholders.
- Do not optimise every parameter before full integration.
- Do not create optional dashboards, multi-camera tracking, face recognition, or unrelated PPE functions.
- Do not polish the final report before real measurements exist.
- Do not use or test any 7B model.

---

## 4. Repository Structure

Retain the existing repository. Reuse its verified Phase 0 environment, configuration, tests, and documentation.

Expected important paths:

```text
config/app.yaml
config/classes.yaml
data/raw/roboflow_safety_glasses_original.zip
data/processed/
models/best.pt
src/main.py
src/camera.py
src/detector.py
src/observations.py
src/temporal_filter.py
src/incident_manager.py
src/serial_protocol.py
src/serial_transport.py
src/event_logger.py
src/overlay.py
src/health_monitor.py
firmware/esp32_eye_shield_monitor/
training/train_yolov8n.py
training/evaluate_model.py
training/colab_training.ipynb
tests/
tools/
experiments/
docs/
```

Do not redesign verified Phase 0 code unless an actual blocker is demonstrated.

---

## 5. Revised Phases

## Phase 1: Minimum Necessary Dataset Audit

### Purpose

Establish whether the available Roboflow dataset can contribute useful goggles images and identify the minimum relabelling/data-collection work required. Do not turn this phase into a large standalone software project.

### Required actions

1. Verify ZIP integrity and calculate SHA-256.
2. Read `data.yaml` and confirm class IDs.
3. Count images and labels by train/validation/test split.
4. Validate image-label pairs.
5. Detect corrupt images and invalid YOLO rows.
6. Count annotations by class and split.
7. Check exact duplicate images across splits.
8. Use a simple perceptual-hash check for obvious near-duplicate leakage.
9. Produce representative annotation contact sheets, especially for `glasses`.
10. Manually inspect whether `glasses` means safety goggles, ordinary glasses, sunglasses, or a mixture.
11. Confirm that the source dataset has no direct `no_goggles` class.
12. Recommend which source images can be reused and what new images are required.

### Required outputs

- `docs/dataset_audit.md`
- `docs/annotation_guideline.md`
- `data/reviewed/audit/dataset_summary.json`
- `data/reviewed/audit/class_counts.csv`
- `data/reviewed/audit/annotation_issues.csv`
- `data/reviewed/audit/duplicates.csv`
- `data/reviewed/audit/manual_review_queue.csv`
- A small number of useful contact sheets in an ignored audit-artifacts directory.

### Essential tests only

Automated tests should cover:

- Valid annotation.
- Missing image or label.
- Corrupt image.
- Invalid class ID or coordinate.
- Exact cross-split duplicate.
- Deterministic outputs.
- Original ZIP remains unchanged.

Approximately 8 to 12 focused tests are sufficient. Do not chase a high test count.

### Stop condition

Phase 1 ends when the agent can state:

- Which images are usable.
- Whether manual relabelling is required.
- How many new `no_goggles` and hard-negative images are needed.
- Whether the existing split is trustworthy.

Do not perform exhaustive statistical analysis that does not affect these decisions.

---

## Phase 2A: Hardware Bring-Up in Parallel

This phase may begin as soon as the ESP32-S3 and modules are available. It does not depend on model training.

### Actions

1. Confirm Arduino IDE can compile and upload to the ESP32-S3.
2. Identify the actual serial device on macOS.
3. Test green LED GPIO11 independently.
4. Test red LED GPIO12 independently.
5. Test the passive buzzer GPIO14 using PWM/tone.
6. Confirm active-high or active-low LED behaviour.
7. Confirm module supply voltage and current requirements.
8. Test red LED and buzzer together.
9. Record wiring and verified output behaviour.

### Acceptance

- Each output works independently.
- No GPIO is overloaded.
- No long blocking delays are required.
- The wiring diagram matches the actual hardware.

---

## Phase 2B: Dataset Construction and YOLOv8n Training

### Dataset construction

Create a two-class dataset using the same semantic box unit for both classes, preferably the sufficiently visible head/face region:

- `goggles`
- `no_goggles`

Do not use tiny goggles-only boxes for one class and whole-person boxes for the other.

Include:

- Genuine laboratory/industrial safety goggles.
- People without safety goggles.
- Ordinary prescription glasses.
- Sunglasses.
- Face masks.
- Face shields where relevant.
- Front, side, and downward views.
- Bright, ordinary, and dim lighting.
- Different distances and backgrounds.
- Empty scenes as negative images where supported by the training format.

Practical starting target:

- 500 to 800 reviewed images if achievable.
- At least approximately 250 useful instances per class.
- Three to five participants for self-collected data.
- Two to three safety-goggle designs.

If fewer images are available, proceed with a smaller honest prototype dataset and document the limitation. Do not invent data.

### Split rule

Create train/validation/test splits by participant, source video, or recording session where possible. Do not place adjacent frames from one video into different splits.

Suggested split:

- Train: 70%.
- Validation: 20%.
- Independent test: 10%.

Lock the test set before final model tuning.

### Training

Primary formal training environment: Google Colab GPU.

Local Apple Silicon MPS may be used for smoke tests and as a fallback.

Baseline:

- `yolov8n.pt`
- Image size: 640.
- Maximum approximately 100 epochs with early stopping.
- Fixed recorded seed.
- Batch size selected for available memory.
- Save actual arguments, curves, confusion matrix, PR curves, and `best.pt`.

### Evaluation

Report actual:

- Per-class precision.
- Per-class recall.
- Per-class F1.
- mAP@0.5.
- mAP@0.5:0.95.
- Confusion matrix.
- Representative false positives and false negatives.
- Local inference FPS and latency.

Targets are goals, not guaranteed results:

- Precision at least 0.90.
- Recall at least 0.85.
- F1 at least 0.87.
- mAP@0.5 at least 0.90.
- At least 15 FPS on the final laptop.

### Acceptance

- `models/best.pt` loads locally without cloud inference.
- Class IDs match `config/classes.yaml`.
- Independent evaluation is reproducible.
- Failures and limitations are retained, not hidden.

---

## Phase 3: Complete Laptop AI Application

Implement real, working versions of:

- `camera.py`
- `detector.py`
- `observations.py`
- `temporal_filter.py`
- `incident_manager.py`
- `event_logger.py`
- `overlay.py`
- `health_monitor.py`
- `main.py`

### Required behaviour

1. Open live webcam or a recorded test video.
2. Load `models/best.pt`.
3. Run YOLOv8n inference.
4. Map detections to safe/violation/unknown observations.
5. Apply the real-time 2-second filter.
6. Create exactly one incident for a sustained violation.
7. Display complete operator overlay.
8. Log important transitions.
9. Continue operating when no valid face/head observation exists, but display `UNKNOWN` rather than false safe.

### Focused tests

Test:

- Just below 2.0 seconds.
- At exactly/above the threshold.
- Variable frame intervals.
- Brief missed detections.
- Safe interruption.
- Unknown observation.
- Clearance timer.
- One incident per episode.
- No new incident before reset.

### Acceptance

- Short violation does not create an incident.
- Sustained violation creates one incident.
- Unknown does not become safe or violation automatically.
- Recorded test video produces repeatable behaviour.
- Live webcam inference is stable enough for demonstration.

---

## Phase 4: Serial Protocol and Full ESP32 State Machine

### First integrate without YOLO

Use a manual trigger or `tools/serial_mock.py` to prove the complete serial/hardware workflow before connecting the AI output.

Example violation message:

```json
{"type":"violation","incident_id":1,"class":"no_goggles","confidence":0.91,"duration_ms":2100}
```

Expected ESP32 acknowledgement:

```json
{"type":"ack","incident_id":1,"state":"alarm_latched"}
```

### Required protocol

Laptop to ESP32:

- `hello`
- `heartbeat`
- `violation`
- `safety_state`

ESP32 to laptop:

- `hello_ack`
- `heartbeat_ack`
- `ack`
- `state`
- `reset_result`
- fault/status messages where needed

### Acceptance

- Valid violation latches red LED and buzzer.
- Duplicate incident ID does not duplicate the incident.
- Malformed JSON does not crash firmware.
- Silence affects only the buzzer.
- Acknowledge does not clear the incident.
- Safe message does not automatically clear the incident.
- Unsafe reset is rejected.
- Serial unplug/replug recovers without clearing a latched incident.

---

## Phase 5: Blynk Integration

### Actions

1. Create Blynk template and datastreams.
2. Store Wi-Fi credentials and auth token in ignored firmware configuration.
3. Report real ESP32 state to the dashboard.
4. Send one notification per incident.
5. Implement Silence, Acknowledge, and Reset.
6. Return reset success/rejection status.
7. Test Wi-Fi loss and reconnection.

### Acceptance

- One incident creates one notification.
- Duplicate serial packets do not duplicate notifications.
- Commands create the correct state transitions.
- Dashboard does not display false-safe state.
- Local alarm remains operational without Blynk.
- Reconnection does not clear an unresolved incident.

---

## Phase 6: Full End-to-End Prototype Integration

Connect the real pipeline:

```text
Webcam
    -> YOLOv8n
    -> temporal filter
    -> incident manager
    -> serial transport
    -> ESP32 state machine
    -> LEDs and buzzer
    -> Blynk
    -> human acknowledgement and conditional reset
```

### Required scenario

1. System starts healthy in `SAFE`.
2. Correct goggles maintain green safe state.
3. Removal for less than 2 seconds shows pending but triggers no alarm.
4. Sustained `no_goggles` creates an incident.
5. Red LED, buzzer, serial acknowledgement, and Blynk notification activate.
6. Wearing goggles again does not clear the latched alarm.
7. Silence stops only the buzzer.
8. Acknowledge records supervisor response.
9. Reset before safe-clear is rejected.
10. Reset after acknowledgement, safe-clear, and healthy link returns to `SAFE`.
11. Camera, serial, and Wi-Fi faults produce controlled behaviour.

### Prototype acceptance gate

The feature-complete prototype must pass at least 10 consecutive complete cycles without manually restarting Python or the ESP32.

Do not proceed to extensive formal experiments until this gate passes.

---

## Phase 7: Essential Reliability and Formal Testing

After all features work, measure the system. Keep testing focused on report-worthy evidence.

### AI tests

- Independent test-set metrics.
- Bright, normal, and dim lighting.
- Front, side, and downward pose.
- Ordinary glasses and sunglasses.
- Multiple distances.
- Representative false positives and false negatives.

### System tests

Where practical, run 20 repetitions for key tests:

| Test | Measurement |
|---|---|
| Violation under 2 seconds | False triggers |
| Sustained violation | Trigger success rate |
| End-to-end alarm | Mean, SD, minimum, maximum latency |
| Alarm after visual recovery | Latch retention |
| Silence | Buzzer off; red retained |
| Acknowledge | Correct unresolved state |
| Reset while unsafe | Rejection rate |
| Valid reset | Acceptance rate |
| Duplicate packet | Duplicate-event count |
| Serial interruption | Recovery behaviour/time |
| Wi-Fi interruption | Local-alarm continuity |
| Camera unavailable | Fault state; no false safe |

Every reported number must trace to raw CSV/JSONL records.

---

## Phase 8: Demonstration and Submission Readiness

### Six-minute demonstration

1. Briefly show architecture.
2. Start in safe state.
3. Show correct goggles detection.
4. Remove goggles for less than 2 seconds.
5. Sustain violation and trigger full alarm.
6. Show Blynk notification.
7. Restore goggles and prove alarm remains latched.
8. Silence buzzer.
9. Acknowledge incident.
10. Demonstrate rejected unsafe/premature reset.
11. Complete safe-clear and reset successfully.
12. Present actual AI and system results.

### Submission documentation

Prepare:

- Architecture diagram.
- State-machine diagram.
- Dataset source and licence attribution.
- Annotation rules.
- Training settings and actual metrics.
- End-to-end test results.
- Failure cases and limitations.
- Source-code ZIP.
- Hardware wiring.
- Blynk configuration instructions.
- AI usage disclosure.
- Contribution statement.
- Demo recovery checklist.

---

## 6. Priority Classification

### P0: Must work before anything else

- Webcam inference.
- Real `goggles`/`no_goggles` model.
- 2-second temporal filtering.
- Incident creation and suppression.
- Serial JSON.
- ESP32 alarm latching.
- Green/red LED behaviour.
- Passive buzzer.
- Silence/Acknowledge/conditional Reset.
- Blynk notification and controls.
- Fault-safe behaviour for camera, serial, and Wi-Fi.
- End-to-end logging.

### P1: Required for A/A+ evidence

- Independent AI metrics.
- Exact/near duplicate awareness and leakage-safe split.
- False-positive and false-negative analysis.
- FPS and latency.
- Repeated system trials.
- Clear limitations.
- Ten consecutive demo rehearsals.

### P2: Only after P0 and P1 pass

- More polished UI.
- Additional visual charts.
- Additional dataset tooling.
- Model/export format comparisons.
- Optional state persistence across ESP32 power loss.
- Additional notification channels.

---

## 7. Agent Working Rules

1. Read this file before each new phase.
2. Work on one phase or explicitly approved parallel phase at a time.
3. Produce working code, not only plans or placeholders.
4. Run the smallest meaningful tests after each change.
5. Do not claim hardware success without physical test evidence from the user or captured serial output.
6. Do not claim model metrics without real evaluation output.
7. Do not fabricate screenshots, notifications, latency, or experiment results.
8. Preserve working components; prefer small changes over large rewrites.
9. Keep secrets out of Git.
10. Update `docs/decision_log.md` only for material decisions.
11. Update `docs/ai_usage_log.md` with meaningful AI-assisted work and human verification.
12. Report blockers immediately and state exactly what physical action the user must perform.
13. Do not spend time maximising test counts. Test safety-critical transitions and likely failure modes.
14. Do not use or test any 7B model.

---

## 8. Definition of Feature-Complete Prototype

The prototype is feature-complete only when:

- A custom YOLOv8n model runs locally.
- Live webcam inference distinguishes `goggles`, `no_goggles`, and insufficient evidence.
- Sustained violation filtering works using elapsed time.
- Short violations are rejected.
- One incident produces one serial alert and one Blynk notification.
- ESP32 latches the physical alarm.
- Alarm remains after the visual scene becomes safe.
- Silence stops only the buzzer.
- Acknowledge does not falsely show safe.
- Reset is rejected until all safety conditions pass.
- A valid reset returns the system to green safe state.
- Serial interruption does not erase the incident.
- Wi-Fi/Blynk interruption does not stop local alarming.
- Camera failure does not display false safe.
- Event logs permit the incident timeline to be reconstructed.
- Ten consecutive full demonstration cycles pass without a software or firmware restart.

---

## 9. Immediate Instruction to the AI Agent

Continue from the repository's present state.

If the current Phase 1 audit is still running, finish only the minimum outputs required by Phase 1 of this revised plan. Do not add more optional analyses, contact sheets, output formats, or tests once the required decisions can be made.

Then report:

- Current completed audit findings.
- Remaining essential Phase 1 work only.
- Any human review required.
- Recommended final two-class dataset construction work.
- Exact files changed.
- Tests run.
- Commit hash.
- Whether Phase 2A hardware bring-up and Phase 2B dataset/model work can begin.

After approval, prioritise the complete feature path in this order:

```text
minimum dataset decision
-> hardware bring-up in parallel
-> final two-class dataset
-> YOLOv8n training
-> complete laptop AI application
-> serial and ESP32 state machine
-> Blynk
-> full end-to-end integration
-> formal testing
-> demo and submission
```

The target is a complete, reliable prototype with every proposed core feature working, not a reduced MVP and not an over-engineered production platform.
