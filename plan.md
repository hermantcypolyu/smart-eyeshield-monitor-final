# AI Agent Development Plan

## Project Identity

**Project title:** Smart Biohazard & Laser Lab Eye-Shield Monitor: Automated Safety Goggles Compliance with ESP32 Latching Alarms and IoT Mobile Verification

**Course:** EIE3127 AI-enabled IoT Project

**Primary objective:** Build a reliable proof-of-concept that detects whether a visible person is correctly wearing safety goggles, confirms a sustained violation, sends a structured alert to an ESP32-S3, latches a physical alarm, notifies a supervisor through Blynk, and permits safe human-controlled acknowledgement and reset.

**Target quality:** A/A+ standard. Prioritise reliability, defensible AI evaluation, complete integration, measurable results, clear documentation, and a stable live demonstration over unnecessary features.

---

## 1. Instructions to the Development Agent

Act as the principal software and embedded-systems engineer for this project. Develop the system incrementally, keep every stage runnable, and do not replace working modules with untested large rewrites.

Follow these rules throughout development:

1. Never fabricate training results, test results, hardware responses, screenshots, timings, or Blynk delivery records.
2. Never assume that failure to detect goggles proves a `no_goggles` violation.
3. Use a purpose-trained compliance model with consistent `goggles` and `no_goggles` labels.
4. Do not use or test any 7B model.
5. Use YOLOv8n as the primary model unless measured evidence supports a change.
6. Keep safety decisions deterministic and explainable. AI proposes the observed class; explicit software logic decides whether an incident is created.
7. The ESP32 alarm must remain latched after the visual violation disappears.
8. `Silence`, `Acknowledge`, and `Reset` must remain separate actions.
9. A remote reset must be rejected while a violation remains, while the safe-clear period is incomplete, or while the laptop-to-ESP32 link is unhealthy.
10. Local alarm operation must not depend on Blynk remaining connected.
11. Keep secrets outside source code. Do not commit Wi-Fi passwords, Blynk tokens, Roboflow keys, or private dataset credentials.
12. Add clear comments only where they explain design intent, safety behaviour, protocol rules, or non-obvious logic.
13. Record important decisions in `docs/decision_log.md`.
14. Record every experiment in a machine-readable CSV and a short Markdown note.
15. After each milestone, run the defined acceptance tests before moving to the next milestone.

---

## 2. System Scope

### 2.1 In scope

- Live USB webcam capture on a laptop.
- Custom-trained Ultralytics YOLOv8n inference.
- Two compliance labels: `goggles` and `no_goggles`.
- Confidence filtering and time-based temporal validation.
- Incident creation with unique incident IDs.
- Newline-delimited JSON communication through PySerial.
- ESP32-S3 alarm state machine.
- Red and green traffic-light LEDs.
- Passive buzzer with non-blocking tone generation.
- Blynk mobile status, notification, silence, acknowledge, and conditional reset.
- Local event logging.
- AI evaluation and end-to-end system testing.
- Reproducible setup and demonstration scripts.

### 2.2 Out of scope unless all core acceptance tests already pass

- Face recognition or worker identification.
- Cloud video upload.
- Automatic disciplinary decisions.
- Door locks, access-control systems, servos, gas sensors, or unrelated sensors.
- Multi-camera tracking.
- Production deployment claims.
- Medical, legal, or regulatory certification claims.

### 2.3 Safety and privacy boundaries

This is an educational proof-of-concept, not a certified safety device. It must be presented as an additional monitoring aid and not a replacement for trained supervisors, laboratory procedures, laser interlocks, risk assessments, or approved PPE procedures.

Avoid storing raw video by default. Store event metadata and explicitly selected test images only. Where people participate in self-collected data, obtain consent and document the collection purpose.

---

## 3. Intended System Architecture

```text
USB Webcam
    |
    v
Laptop Python Application
    |- OpenCV frame capture
    |- YOLOv8n inference
    |- Observation validation
    |- Temporal filtering
    |- Incident manager
    |- CSV/JSONL logger
    |- PySerial transport and heartbeat
    v
USB Serial: newline-delimited JSON
    |
    v
ESP32-S3 UNO
    |- JSON parser
    |- Communication watchdog
    |- Latched alarm state machine
    |- Red LED: GPIO12
    |- Green LED: GPIO11
    |- Passive buzzer: GPIO14
    |- Wi-Fi and Blynk client
    v
Blynk Mobile Dashboard
    |- Current system state
    |- Incident information
    |- Push/event notification
    |- Silence command
    |- Acknowledge command
    |- Conditional reset command
```

The laptop performs computer-vision inference. The ESP32-S3 independently owns the latched alarm state. Loss of Blynk must not clear or prevent the local physical alarm.

---

## 4. Required Repository Structure

Create this structure inside the new project folder:

```text
project-root/
├── README.md
├── plan.md
├── .gitignore
├── .env.example
├── requirements.txt
├── config/
│   ├── app.example.yaml
│   └── classes.yaml
├── data/
│   ├── README.md
│   ├── raw/
│   ├── reviewed/
│   ├── splits/
│   └── test_cases/
├── models/
│   ├── README.md
│   └── best.pt
├── src/
│   ├── main.py
│   ├── config.py
│   ├── camera.py
│   ├── detector.py
│   ├── observations.py
│   ├── temporal_filter.py
│   ├── incident_manager.py
│   ├── serial_protocol.py
│   ├── serial_transport.py
│   ├── event_logger.py
│   ├── overlay.py
│   └── health_monitor.py
├── firmware/
│   └── esp32_eye_shield_monitor/
│       ├── esp32_eye_shield_monitor.ino
│       ├── config.example.h
│       ├── protocol.h
│       ├── alarm_state.h
│       ├── alarm_state.cpp
│       └── README.md
├── training/
│   ├── train_yolov8n.py
│   ├── evaluate_model.py
│   ├── export_metrics.py
│   └── colab_training.ipynb
├── tests/
│   ├── test_temporal_filter.py
│   ├── test_incident_manager.py
│   ├── test_serial_protocol.py
│   ├── test_recorded_video.py
│   └── fixtures/
├── tools/
│   ├── serial_mock.py
│   ├── camera_check.py
│   ├── inspect_dataset.py
│   ├── measure_latency.py
│   └── export_demo_logs.py
├── experiments/
│   ├── ai_metrics.csv
│   ├── system_trials.csv
│   └── README.md
└── docs/
    ├── architecture.md
    ├── annotation_guideline.md
    ├── protocol.md
    ├── blynk_setup.md
    ├── test_plan.md
    ├── demo_runbook.md
    ├── troubleshooting.md
    ├── decision_log.md
    └── ai_usage_log.md
```

Do not commit private raw images, credentials, `.env`, `config.h`, generated training runs, or large model artifacts unless explicitly required.

---

## 5. Dataset and Annotation Plan

### 5.1 Starting source

Use the proposed Roboflow Universe dataset only as a starting source:

`https://universe.roboflow.com/workspace-3vzwb/safety-glasses-dataset`

The source currently contains PPE classes such as `glasses`, but the final project requires a direct and defensible compliance decision. Audit the images and annotations before using them. Do not treat absence of a `glasses` detection as a `no_goggles` label.

### 5.2 Final class definitions

Use exactly these primary classes unless an evidence-based revision is documented:

- `goggles`: A visible person is correctly wearing genuine laboratory or industrial safety goggles over the eyes.
- `no_goggles`: The relevant face or head region is sufficiently visible for assessment, but genuine safety goggles are absent or incorrectly positioned.

Ordinary prescription glasses and sunglasses must normally be labelled `no_goggles`, unless the project explicitly uses certified prescription safety eyewear and documents that distinction.

### 5.3 Annotation unit

Use the same semantic unit for both classes. Preferred approach: draw a consistent bounding box around the visible head/face region for both `goggles` and `no_goggles`.

Do not mix a tiny goggles-only box for one class with a whole-person box for the other. Document minimum visible face size, treatment of side views, occlusion rules, incorrect wearing, goggles on the forehead, face shields, masks, and ambiguous cases in `docs/annotation_guideline.md`.

### 5.4 Data quality targets

Initial practical target:

- 500 to 800 reviewed images.
- At least 250 usable instances per class where practical.
- Three to five participants in self-collected data.
- Two to three designs of real safety goggles.
- Bright, normal, and dim lighting.
- Front, left, right, and downward head poses.
- Hard negatives including ordinary glasses, sunglasses, masks, face shields, partial occlusion, empty scenes, posters, and small distant faces.

These figures are collection targets, not fabricated achieved values.

### 5.5 Split rules

Create train, validation, and independent test splits, initially 70/20/10. Split by source video, recording session, or participant where possible, not by randomly distributing adjacent video frames. Adjacent frames from the same clip must not appear across train and test sets.

Maintain a split manifest containing:

- Image ID
- Source
- Participant/session ID where applicable
- Class labels
- Split
- Lighting condition
- Pose
- Goggles type

### 5.6 Dataset acceptance gate

Do not begin final training until:

- The annotation guideline exists.
- All retained annotations have been reviewed.
- Duplicate and near-duplicate frames have been checked.
- Class counts are reported.
- The independent test set is locked.
- No test images are used to select training epochs, confidence thresholds, or augmentations.

---

## 6. AI Training and Evaluation Plan

### 6.1 Baseline

- Framework: Ultralytics YOLOv8.
- Base weights: `yolov8n.pt`.
- Image size: begin with 640 pixels.
- Epochs: begin with a maximum of approximately 100, with early stopping/patience.
- Batch size: select according to Google Colab GPU memory.
- Seed: fixed and recorded.
- Save `args.yaml`, training curves, confusion matrix, precision-recall curves, and `best.pt`.

All final hyperparameters must be copied from the actual training output into the experiment record.

### 6.2 Evaluation metrics

Report at minimum:

- Class-wise precision.
- Class-wise recall.
- Class-wise F1-score.
- mAP@0.5.
- mAP@0.5:0.95.
- Confusion matrix.
- False-positive and false-negative examples.
- Laptop inference FPS.
- Per-frame inference latency.

Because this is a safety-monitoring scenario, explicitly discuss false negatives for `no_goggles`. Do not optimise only for headline mAP.

### 6.3 Threshold selection

Select the confidence threshold using validation data, then freeze it before independent testing. Begin investigation around 0.50 to 0.60, but do not hard-code the final value without measurement.

Log threshold experiments in this form:

```text
threshold,precision_no_goggles,recall_no_goggles,f1_no_goggles,false_triggers
```

### 6.4 Performance goals

Treat these as goals, not claimed results:

- Precision: at least 0.90.
- Recall: at least 0.85.
- F1-score: at least 0.87.
- mAP@0.5: at least 0.90.
- Runtime: at least 15 FPS on the demonstration laptop.
- Post-filter false-trigger rate: below 5% in the defined system trials.

If a goal is missed, retain the measured result, analyse the cause, and propose a justified improvement. Never hide unfavourable cases.

### 6.5 AI acceptance gate

The model is ready for integration only when:

- `best.pt` loads locally without a cloud connection.
- Class IDs match `classes.yaml`.
- Independent test evaluation runs from one command.
- Test-set results are exported to CSV/JSON.
- Representative failure cases are saved.
- Live webcam inference meets the minimum usable FPS.

---

## 7. Laptop Application Design

### 7.1 Runtime pipeline

For every frame:

1. Capture frame and monotonic timestamp.
2. Run YOLO inference.
3. Convert detections into validated observations.
4. Reject observations that are too small or too ambiguous for a compliance decision.
5. Update temporal filtering using elapsed time, not assumed frame count.
6. Create one incident when a sustained violation becomes confirmed.
7. Send the incident once and retry only according to the serial acknowledgement policy.
8. Continue sending state/heartbeat messages.
9. Draw a clear local overlay.
10. Write structured event logs without blocking inference.

### 7.2 Temporal filtering

Default design target:

- Confirmation duration: 2.0 seconds.
- Rolling observation window: approximately 2.0 seconds.
- Required violation proportion: initially 80% of valid observations.
- Clearance duration before reset eligibility: 2.0 continuous seconds of safe observation.
- Use `time.monotonic()` for durations.

The filter must tolerate occasional dropped detections. It must distinguish:

- `UNKNOWN`: no reliable compliance observation.
- `SAFE`: a valid `goggles` observation.
- `VIOLATION_PENDING`: insufficient-duration `no_goggles` evidence.
- `VIOLATION_CONFIRMED`: sustained evidence sufficient to create an incident.

`UNKNOWN` must not automatically become `SAFE` or `NO_GOGGLES`.

### 7.3 Incident lifecycle

Each confirmed incident receives a monotonically increasing or UUID-based `incident_id`. Only one active incident may exist in the basic PoC.

Incident fields:

```json
{
  "incident_id": 12,
  "type": "no_goggles",
  "confidence": 0.91,
  "duration_ms": 2087,
  "created_at": "ISO-8601 timestamp",
  "source": "camera_0"
}
```

The laptop must not repeatedly create incidents for every frame. A new incident is permitted only after the previous incident has been safely reset and a new violation occurs.

### 7.4 Local overlay

Display at least:

- Bounding boxes and class names.
- Detection confidence.
- Current observation state.
- Pending violation duration/progress.
- Active incident ID.
- Serial connection status.
- ESP32-reported alarm state.
- Actual measured FPS.

Use green for confirmed compliance, amber for pending/unknown, and red for confirmed violation. Do not show green merely because no detection exists.

### 7.5 Logging

Write one JSONL event log and one CSV trial log. Include timestamps for:

- First valid violation observation.
- Violation confirmation.
- Alert sent.
- ESP32 acknowledgement received.
- Blynk command received where available.
- Silence, acknowledgement, reset request, reset acceptance, and reset rejection.
- Connection loss and recovery.

---

## 8. Serial Protocol

### 8.1 Transport

- USB serial through PySerial.
- Begin at 115200 baud.
- UTF-8 newline-delimited JSON, one complete object per line.
- Maximum message length must be defined and enforced.
- Ignore or reject malformed messages safely.
- Never use blocking infinite waits.

### 8.2 Required message types

Laptop to ESP32:

```json
{"type":"hello","protocol":1,"app":"eye_shield_monitor"}
{"type":"heartbeat","seq":101,"safe_observed":true}
{"type":"violation","incident_id":12,"class":"no_goggles","confidence":0.91,"duration_ms":2087}
{"type":"safety_state","state":"safe","clear_duration_ms":2150}
```

ESP32 to laptop:

```json
{"type":"hello_ack","protocol":1,"device":"esp32_s3"}
{"type":"ack","incident_id":12,"state":"alarm_latched"}
{"type":"state","incident_id":12,"state":"silenced"}
{"type":"reset_result","incident_id":12,"accepted":false,"reason":"unsafe_or_link_unhealthy"}
{"type":"heartbeat_ack","seq":101}
```

### 8.3 Reliability rules

- Reject duplicate incident IDs without retriggering duplicate Blynk events.
- Retry an unacknowledged violation message a limited number of times.
- Reconnect automatically after serial loss.
- On reconnection, perform a handshake and state synchronisation.
- The ESP32 must not clear a latched incident because the serial port disconnects.
- The laptop must not display hardware state as safe if it has not received a valid state report.

Document the complete schema and transition effects in `docs/protocol.md`.

---

## 9. ESP32-S3 Firmware Design

### 9.1 Pin configuration

- Red LED: GPIO12.
- Green LED: GPIO11.
- Passive buzzer: GPIO14.

Keep all pin assignments in one configuration section. Confirm whether the LED module is active-high or active-low during hardware bring-up and record the result.

### 9.2 Required state machine

Implement explicit states:

- `BOOT`
- `SAFE`
- `ALARM_LATCHED`
- `SILENCED`
- `ACKNOWLEDGED`
- `READY_TO_RESET`
- `FAULT`

Recommended output behaviour:

| State | Green LED | Red LED | Buzzer |
|---|---|---|---|
| BOOT | Blink | Off | Off |
| SAFE | Solid | Off | Off |
| ALARM_LATCHED | Off | Solid | Alarm pattern |
| SILENCED | Off | Solid | Off |
| ACKNOWLEDGED | Off | Slow blink | Off |
| READY_TO_RESET | Alternate/defined pattern | Slow blink | Off |
| FAULT | Off | Fast blink or distinct pattern | Optional short pattern |

Do not use green to mean merely acknowledged. Green must represent a genuinely safe state.

### 9.3 Command semantics

- **Silence:** Stop the buzzer only. Keep the red incident indication and incident record.
- **Acknowledge:** Record that a supervisor has accepted responsibility for checking the incident. Do not clear the incident.
- **Reset:** Accept only if the incident has been acknowledged, the laptop reports a sufficient continuous safe-clear interval, and communication health is acceptable.

### 9.4 Non-blocking firmware

Do not use long `delay()` calls for buzzer patterns, LED blinking, Wi-Fi retries, or Blynk processing. Use `millis()`-based scheduling so serial parsing, alarm outputs, and Blynk remain responsive.

### 9.5 Fault behaviour

- Serial/laptop connection lost while safe: enter a visible fault state rather than presenting confirmed safety.
- Serial lost while an alarm is latched: keep the alarm latched.
- Wi-Fi/Blynk lost: preserve local serial and physical alarm operation.
- ESP32 reboot with an unresolved incident: where practical, persist minimal incident state in Preferences/NVS; otherwise document this limitation and test reboot behaviour.

---

## 10. Blynk Design

Create a template and dashboard with clearly named datastreams. Exact virtual-pin allocation may be adjusted once, then documented.

Suggested mapping:

- V0: Current state text/value.
- V1: Incident ID.
- V2: Confidence.
- V3: Silence button.
- V4: Acknowledge button.
- V5: Reset button.
- V6: Serial/laptop link health.
- V7: Last event timestamp.

Required behaviour:

1. A newly latched incident triggers one Blynk event/notification.
2. Duplicate serial messages must not create duplicate mobile notifications.
3. Silence acts only on the buzzer.
4. Acknowledge changes the incident state but does not indicate safety.
5. Reset is conditionally accepted or rejected by the ESP32 state machine.
6. Dashboard status reflects the ESP32 state, not a locally optimistic button state.
7. Local alarm continues if Blynk or Wi-Fi is unavailable.

Store auth tokens and Wi-Fi credentials in an uncommitted `config.h` generated from `config.example.h`.

---

## 11. Development Phases and Acceptance Criteria

### Phase 0: Repository and reproducibility

Tasks:

- Create the required folder structure.
- Initialise Git.
- Add `.gitignore`, `requirements.txt`, `.env.example`, example YAML configuration, and README.
- Pin tested Python package versions after the environment works.
- Add a one-command environment check.

Acceptance:

- A clean clone can create the environment and run `python tools/camera_check.py`.
- No secrets are committed.
- Configuration errors produce readable messages.

### Phase 1: Dataset audit

Tasks:

- Download/export authorised source data.
- Inspect class names, box consistency, duplicates, image sizes, corrupt files, and image sources.
- Write the annotation guideline.
- Relabel selected images into the final two-class scheme.
- Add self-collected and hard-negative images.
- Create leakage-safe splits and manifest.

Acceptance:

- Class and split statistics are exported.
- Random visual samples have been manually reviewed.
- Test set is locked.
- No unsupported claim is made about the original dataset.

### Phase 2: YOLOv8n baseline

Tasks:

- Train the first model in Google Colab.
- Export all actual training artifacts.
- Run validation and independent testing.
- Inspect false positives and false negatives.

Acceptance:

- Training is reproducible from the notebook/script.
- `best.pt` runs locally.
- Metrics and failure examples are available.

### Phase 3: Laptop inference application

Tasks:

- Implement modular webcam inference.
- Add observation states, temporal filtering, overlays, and logs.
- Add recorded-video test mode for repeatability.
- Add unit tests for timing boundaries and unknown observations.

Acceptance:

- A violation shorter than the threshold does not create an incident.
- A sustained valid violation creates exactly one incident.
- One or two dropped frames do not necessarily reset the timer.
- No person/invalid observation does not automatically trigger or appear safe.

### Phase 4: Serial protocol and simulator

Tasks:

- Implement typed message encode/decode.
- Build `tools/serial_mock.py` before hardware integration.
- Add handshake, acknowledgement, heartbeat, timeout, retry, and reconnection.
- Test malformed and duplicate messages.

Acceptance:

- Protocol tests pass.
- Duplicate incident IDs do not duplicate actions.
- Laptop recovers from temporary serial loss.
- Malformed JSON does not crash either endpoint.

### Phase 5: ESP32 hardware node

Tasks:

- Verify LEDs and buzzer one component at a time.
- Implement the non-blocking alarm state machine.
- Connect serial protocol.
- Test silence, acknowledge, safe-clear eligibility, and reset rejection.

Acceptance:

- A violation latches red LED and buzzer.
- Removal of the violation does not auto-clear the incident.
- Silence stops only the buzzer.
- Unsafe reset is rejected.
- Safe, acknowledged, link-healthy reset succeeds.

### Phase 6: Blynk integration

Tasks:

- Create template, datastreams, event, and mobile dashboard.
- Implement notifications and commands.
- Prevent duplicate event notifications.
- Test Wi-Fi loss and recovery.

Acceptance:

- One incident creates one notification.
- Dashboard commands map to the correct state transitions.
- Wi-Fi loss does not disable local alarming.
- Reconnection does not clear an unresolved incident.

### Phase 7: Full integration

Tasks:

- Integrate camera, model, temporal filter, serial, ESP32, and Blynk.
- Add health status and useful local overlay.
- Run the complete scenario repeatedly.
- Freeze package/configuration versions for the demo laptop.

Acceptance:

- At least 10 consecutive rehearsal cycles complete without manual code restart.
- All event timestamps and states can be traced through logs.
- Recovery from unplugged/replugged serial is demonstrated.

### Phase 8: Formal experiments

Tasks:

- Execute the test matrix in Section 12.
- Repeat quantitative trials.
- Export raw results and compute summary statistics.
- Save representative evidence.

Acceptance:

- Every reported number is traceable to raw data.
- Mean, standard deviation, minimum, maximum, and success rate are reported where relevant.
- Limitations and failed cases are retained.

### Phase 9: Final demonstration and documentation

Tasks:

- Create `docs/demo_runbook.md`.
- Prepare offline model, local dependencies, spare cables, and fallback recorded video.
- Prepare architecture, state-machine, dataset, metric, and result diagrams for report/video.
- Document generative-AI assistance and human verification.

Acceptance:

- Six-minute live demo fits the course limit.
- Every team member can explain their own code and contributions.
- No live demo step requires retraining or cloud inference.

---

## 12. Formal Test Matrix

Use at least 20 repetitions for the main quantitative cases where time permits. Do not claim a sample size that was not completed.

| Test ID | Scenario | Measurement | Pass intention |
|---|---|---|---|
| AI-01 | Independent labelled test set | Precision, recall, F1, mAP | Report actual values |
| AI-02 | Bright/normal/dim lighting | Recall by condition | No hidden weak condition |
| AI-03 | Front/left/right/down pose | Recall by pose | Report actual values |
| AI-04 | Multiple people/goggles types | Success by subgroup | Generalisation evidence |
| AI-05 | Ordinary glasses/sunglasses | False-positive rate | Minimise false compliance |
| SYS-01 | Violation under 2 seconds | False trigger count | Target 0/20 |
| SYS-02 | Sustained violation | Trigger success rate | Target at least 19/20 |
| SYS-03 | End-to-end trigger latency | Mean, SD, min, max | Stable and explainable |
| SYS-04 | Violation disappears after alarm | Latch retention | 20/20 retained |
| SYS-05 | Silence command | Buzzer off, red retained | 20/20 correct |
| SYS-06 | Acknowledge command | State changes, no safe claim | 20/20 correct |
| SYS-07 | Reset while unsafe | Rejection rate | 20/20 rejected |
| SYS-08 | Reset after safe-clear | Acceptance rate | 20/20 accepted if conditions met |
| COM-01 | Malformed serial JSON | Crash/error behaviour | No crash; safe rejection |
| COM-02 | Duplicate incident packet | Duplicate alarm/event | No duplicate incident |
| COM-03 | Serial unplug/replug | Recovery time and state | Recover without clearing latch |
| IOT-01 | Blynk event delivery | Delivery success/latency | Report actual values |
| IOT-02 | Wi-Fi loss during alarm | Local alarm behaviour | Local alarm unaffected |
| ROB-01 | Empty scene/no face | State behaviour | UNKNOWN, not false safe/violation |
| ROB-02 | Camera unavailable | Fault indication | Visible fault; no false safe |

For latency, record at least:

```text
trial_id,violation_start_ts,confirmed_ts,serial_sent_ts,esp32_ack_ts,alarm_active_ts,total_latency_ms,result
```

---

## 13. Automated Tests Required

### `test_temporal_filter.py`

Cover:

- Exact threshold boundary.
- Just below threshold.
- Sustained violation.
- Intermittent dropped observations.
- Safe interruption.
- Unknown observations.
- Variable frame intervals.
- Clearance timer.

### `test_incident_manager.py`

Cover:

- One incident per violation episode.
- Duplicate suppression.
- No new incident before valid reset.
- Reset eligibility rules.
- Incident ID progression.

### `test_serial_protocol.py`

Cover:

- Valid message round trips.
- Missing required fields.
- Wrong field types.
- Oversized lines.
- Malformed JSON.
- Duplicate IDs.
- Protocol-version mismatch.

### Recorded-video integration test

Use consented, non-private test clips to verify deterministic behaviour without depending on a live person. Keep the clips outside Git if large or sensitive, but document where to place them.

---

## 14. Configuration Requirements

`config/app.example.yaml` should expose at least:

```yaml
camera:
  index: 0
  width: 1280
  height: 720

model:
  path: models/best.pt
  image_size: 640
  confidence_threshold: 0.55
  iou_threshold: 0.45

observation:
  minimum_box_area_ratio: 0.01

filter:
  confirmation_seconds: 2.0
  window_seconds: 2.0
  violation_ratio: 0.80
  clearance_seconds: 2.0

serial:
  port: auto
  baudrate: 115200
  heartbeat_seconds: 2.0
  ack_timeout_seconds: 1.0
  retry_count: 3

logging:
  directory: logs
  save_event_images: false
```

All defaults must be validated and bounded. Invalid values must cause a concise startup error rather than silent unsafe behaviour.

---

## 15. Definition of Done

The project is complete only when all of the following are true:

- The dataset provenance, licence, annotation rules, and split strategy are documented.
- A custom YOLOv8n model is trained and independently evaluated.
- The live application distinguishes safe, pending, violation, and unknown observations.
- The two-second rule is based on real elapsed time.
- An incident is transmitted using structured JSON and acknowledged by ESP32.
- The hardware alarm remains latched after the person becomes safe or leaves view.
- Silence, acknowledge, and reset produce distinct, correct effects.
- Unsafe or communication-unhealthy reset attempts are rejected.
- Blynk sends a notification and supports documented commands.
- Local alarms remain functional without Blynk.
- Faults such as camera loss, malformed JSON, serial loss, and Wi-Fi loss are tested.
- AI and system results are recorded from real experiments.
- The complete demonstration has passed at least 10 consecutive rehearsals.
- Setup, testing, troubleshooting, AI usage, and limitations are documented.
- No secret is stored in the submitted source code.
- No 7B model has been used or tested.

---

## 16. Recommended Six-Minute Demonstration Sequence

1. Show architecture briefly and start the system in `SAFE` with green LED.
2. Show correctly worn safety goggles and stable safe detection.
3. Remove goggles for less than two seconds; show `PENDING` and no alarm.
4. Sustain `no_goggles` for at least two seconds; show confirmed incident.
5. Show red LED, buzzer, serial acknowledgement, and Blynk notification.
6. Put goggles back on; show that the alarm remains latched.
7. Press Silence; confirm buzzer stops while red remains.
8. Press Acknowledge; confirm the incident remains unresolved.
9. Attempt reset before safe-clear or under an unsafe condition; show rejection.
10. Maintain safe observation for the clearance interval and reset successfully.
11. If time permits, disconnect Wi-Fi or serial briefly to demonstrate controlled fault behaviour.
12. End with measured AI and end-to-end results, not only a successful anecdote.

---

## 17. Questions the Agent Must Be Able to Answer from the Code and Records

- Why is `no detection` not treated as `no_goggles`?
- Why was YOLOv8n selected?
- How were the two classes defined consistently?
- How was train/test leakage prevented?
- How was the confidence threshold selected?
- Why is temporal filtering time-based instead of frame-count based?
- What happens if the model misses one frame?
- Why is the alarm owned and latched by the ESP32?
- What is the difference between Silence, Acknowledge, and Reset?
- What happens if Wi-Fi, Blynk, camera, or serial communication fails?
- How are duplicate incidents prevented?
- How was end-to-end latency measured?
- What are the model's most important false-negative cases?
- What limitations stop this PoC from being a certified production safety system?

---

## 18. Immediate First Execution Order

The AI development agent should begin in this exact order:

1. Create the repository structure and baseline documentation.
2. Inspect the available dataset without modifying the locked test data.
3. Draft `docs/annotation_guideline.md` and produce the class/split audit.
4. Implement dataset inspection and duplicate-check tooling.
5. Prepare the reproducible YOLOv8n training notebook and script.
6. Train and evaluate a baseline only after the dataset gate passes.
7. Build the laptop application with a mocked detector first.
8. Unit-test temporal filtering and incident logic.
9. Implement and test the serial protocol against a software mock.
10. Bring up LEDs and buzzer separately on the ESP32-S3.
11. Implement the ESP32 state machine and serial integration.
12. Add Blynk only after local alarm behaviour is reliable.
13. Run full integration, formal experiments, and repeated demo rehearsals.

At the end of every step, update:

- `README.md`
- `docs/decision_log.md`
- `docs/ai_usage_log.md`
- Relevant tests
- Relevant experiment records

Do not move to the next stage while a critical acceptance gate is failing. Record the failure, diagnose it, make the smallest justified correction, and rerun the affected tests.
